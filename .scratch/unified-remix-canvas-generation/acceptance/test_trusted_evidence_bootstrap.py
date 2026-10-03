import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest


ACCEPTANCE_ROOT = Path(__file__).parent
BOOTSTRAP = ACCEPTANCE_ROOT / "bootstrap"
VERIFIER = ACCEPTANCE_ROOT / "tools" / "verify_e2_bootstrap.py"
PYTHON = Path(sys.executable)


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_e2_bootstrap", VERIFIER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    safe_env = dict(env) if env is not None else {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    }
    return subprocess.run(
        [PYTHON, VERIFIER, "--manifest", BOOTSTRAP / "manifest.json", *args],
        cwd=ACCEPTANCE_ROOT.parent.parent.parent,
        env=safe_env,
        capture_output=True,
        text=True,
    )


def _copy_bootstrap(tmp_path: Path) -> Path:
    target = tmp_path / "bootstrap"
    shutil.copytree(BOOTSTRAP, target)
    return target


def _run_copy(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    safe_env = dict(env or os.environ)
    return subprocess.run(
        [PYTHON, VERIFIER, "--manifest", root / "manifest.json", *args],
        cwd=ACCEPTANCE_ROOT.parent.parent.parent,
        env=safe_env,
        capture_output=True,
        text=True,
    )


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, value: dict[str, object]) -> None:
    (root / "manifest.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _current_uv_artifact(manifest: dict[str, object]) -> dict[str, object]:
    return next(item for item in manifest["uvArchives"] if item["platform"] == _verifier()._host_platform())


def _current_wheel_artifact(manifest: dict[str, object]) -> dict[str, object]:
    return next(item for item in manifest["zstandardWheels"] if item["platform"] == _verifier()._host_platform())


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, contents in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))


def _rewrite_wheel_record(source: Path, destination: Path, transform) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as rewritten:
        for info in original.infolist():
            contents = original.read(info.filename)
            if info.filename.endswith(".dist-info/RECORD"):
                contents = transform(contents.decode("utf-8")).encode("utf-8")
            rewritten.writestr(info, contents)


def test_linux_x86_64_requires_proven_gnu_libc():
    """Catches musl or unknown Linux hosts selecting the GNU-only bootstrap artifact."""
    verifier = _verifier()

    assert verifier._classify_platform("linux", "x86_64", "glibc 2.39") == "linux-x86_64-gnu"
    for libc in (None, "", "musl 1.2.5", "uclibc 1.0"):
        with pytest.raises(verifier.BootstrapError, match="GNU libc"):
            verifier._classify_platform("linux", "x86_64", libc)


def test_archive_budget_rejects_compressed_and_expanded_inputs_before_extraction(tmp_path):
    """Catches archive bombs before digesting whole compressed inputs or extracting members."""
    verifier = _verifier()
    archive = tmp_path / "uv.tar.gz"
    _write_tar(archive, {"uv-root/uv": b"uv"})
    ratio_archive = tmp_path / "ratio.tar.gz"
    _write_tar(ratio_archive, {"uv-root/uv": b"a" * 4096})
    tiny_compressed = verifier.ArchiveLimits(compressed_bytes=1, members=100, member_bytes=1024, expanded_bytes=1024, ratio=100)
    tiny_expanded = verifier.ArchiveLimits(compressed_bytes=1024, members=100, member_bytes=1, expanded_bytes=1, ratio=100)
    expanded_total = verifier.ArchiveLimits(compressed_bytes=1024, members=100, member_bytes=1024, expanded_bytes=1, ratio=100)
    compressed_ratio = verifier.ArchiveLimits(compressed_bytes=1024, members=100, member_bytes=8192, expanded_bytes=8192, ratio=1)

    with pytest.raises(verifier.BootstrapError, match="compressed budget"):
        verifier._inspect_tar(archive, tiny_compressed)
    with pytest.raises(verifier.BootstrapError, match="member budget"):
        verifier._inspect_tar(archive, tiny_expanded)
    with pytest.raises(verifier.BootstrapError, match="expanded budget"):
        verifier._inspect_tar(archive, expanded_total)
    with pytest.raises(verifier.BootstrapError, match="compression ratio"):
        verifier._inspect_tar(ratio_archive, compressed_ratio)


def test_bootstrap_consumes_private_snapshots_after_an_audited_source_path_is_replaced(tmp_path):
    """Catches a verified artifact path being atomically replaced before wheel parsing or uv execution."""
    verifier = _verifier()
    root = _copy_bootstrap(tmp_path)
    manifest = _manifest(root)
    uv = _current_uv_artifact(manifest)
    source = root / "artifacts" / uv["filename"]

    original_entries = verifier._all_entries
    calls = 0

    def replace_source_path(*args, **kwargs):
        nonlocal calls
        result = original_entries(*args, **kwargs)
        calls += 1
        if calls != 2:
            return result
        forged = tmp_path / "forged.tar.gz"
        _write_tar(forged, {"forged/uv": b"#!/bin/sh\necho forged\n"})
        os.replace(forged, source)
        return result

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(verifier, "_all_entries", replace_source_path)
    try:
        executable, _ = verifier.verify(root / "manifest.json")
    finally:
        monkeypatch.undo()

    assert executable.is_file()
    assert source.read_bytes() != (BOOTSTRAP / "artifacts" / uv["filename"]).read_bytes()


def test_snapshot_copy_stops_an_append_before_exceeding_the_compressed_budget(tmp_path, monkeypatch):
    """Catches a source growing after fstat and being copied past the compressed-byte limit."""
    verifier = _verifier()
    source = tmp_path / "source.bin"
    source.write_bytes(b"a")
    snapshot_dir = tmp_path / "snapshot"
    real_fdopen = verifier.os.fdopen
    appended = False

    def controlled_fdopen(descriptor, mode, *args, **kwargs):
        handle = real_fdopen(descriptor, mode, *args, **kwargs)
        if mode != "rb":
            return handle

        class AppendingReader:
            def __enter__(self):
                handle.__enter__()
                return self

            def __exit__(self, *exc):
                return handle.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(handle, name)

            def read(self, size=-1):
                nonlocal appended
                chunk = handle.read(size)
                if not appended:
                    source.write_bytes(source.read_bytes() + b"bc")
                    appended = True
                return chunk

        return AppendingReader()

    def controlled_mkdtemp(**_kwargs):
        snapshot_dir.mkdir()
        return str(snapshot_dir)

    monkeypatch.setattr(verifier.os, "fdopen", controlled_fdopen)
    monkeypatch.setattr(verifier.tempfile, "mkdtemp", controlled_mkdtemp)
    with pytest.raises(verifier.BootstrapError, match="compressed budget"):
        verifier._snapshot_file(source, label="artifact", maximum=2)
    assert not snapshot_dir.exists()


def test_verify_cleans_executable_on_version_failure(tmp_path, monkeypatch):
    """Catches a failed version check leaking the private executable directory."""
    verifier = _verifier()
    root = _copy_bootstrap(tmp_path)
    manifest = _manifest(root)
    _current_uv_artifact(manifest)["versionOutput"] = "forged uv version"
    _write_manifest(root, manifest)
    created: list[Path] = []
    real_mkdtemp = verifier.tempfile.mkdtemp

    def record_mkdtemp(**kwargs):
        path = Path(real_mkdtemp(**kwargs))
        created.append(path)
        return str(path)

    monkeypatch.setattr(verifier.tempfile, "mkdtemp", record_mkdtemp)
    with pytest.raises(verifier.BootstrapError, match="version/build identity"):
        verifier.verify(root / "manifest.json")
    assert created
    assert all(not path.exists() for path in created)


def test_verify_does_not_expose_a_public_race_test_hook(tmp_path):
    """Catches test-only source replacement callbacks becoming a production verifier interface."""
    verifier = _verifier()
    root = _copy_bootstrap(tmp_path)

    with pytest.raises(TypeError):
        verifier.verify(root / "manifest.json", after_snapshots=lambda: None)


def test_wheel_budget_and_record_rows_fail_closed(tmp_path):
    """Catches zip bombs and malformed, duplicate, missing, or unchecked RECORD rows."""
    verifier = _verifier()
    manifest = _manifest(BOOTSTRAP)
    entry = _current_wheel_artifact(manifest)
    source = BOOTSTRAP / "artifacts" / entry["filename"]
    tiny = verifier.ArchiveLimits(compressed_bytes=1024**3, members=1, member_bytes=256 * 1024**2, expanded_bytes=4 * 1024**3, ratio=100)
    with pytest.raises(verifier.BootstrapError, match="member budget"):
        verifier._inspect_zip(source, tiny)

    def bad_field(record: str) -> str:
        row, *rest = record.splitlines()
        return "broken,row\n" + "\n".join(rest) + "\n"

    def duplicate(record: str) -> str:
        row, *rest = record.splitlines()
        return row + "\n" + row + "\n" + "\n".join(rest) + "\n"

    def missing(record: str) -> str:
        return "\n".join(record.splitlines()[1:]) + "\n"

    def wrong_hash(record: str) -> str:
        row, *rest = record.splitlines()
        filename, _, size = row.split(",")
        return f"{filename},sha256={'A' * 43},{size}\n" + "\n".join(rest) + "\n"

    for name, transform in (("field", bad_field), ("duplicate", duplicate), ("missing", missing), ("hash", wrong_hash)):
        rewritten = tmp_path / f"{name}.whl"
        _rewrite_wheel_record(source, rewritten, transform)
        with pytest.raises(verifier.BootstrapError, match="wheel RECORD"):
            verifier._wheel_is_valid(rewritten, entry, verifier.DEFAULT_LIMITS)


def test_uv_version_executes_with_only_the_explicit_controlled_environment():
    """Catches host PATH, dynamic-loader, or arbitrary variables influencing uv identity checks."""
    verifier = _verifier()
    environment = verifier._uv_environment()

    assert environment == {
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "UV_OFFLINE": "1",
        "UV_CONFIG_FILE": os.devnull,
    }


def test_bootstrap_verifier_requires_an_offline_manifest_and_is_not_a_missing_script():
    """Catches a release where verification is absent or silently accepts a network-capable run."""
    result = _run()

    assert result.returncode != 0
    assert "--offline is required" in result.stderr


def test_bootstrap_verifies_real_archives_wheel_metadata_and_empty_configuration():
    """Catches swapped bytes, wrong wheel tags, and a non-empty uv configuration."""
    result = _run("--offline")

    assert result.returncode == 0, result.stderr
    assert "BOOTSTRAP_UV=" in result.stdout
    assert "MANIFEST_DIGEST=sha256:" in result.stdout


def test_bootstrap_rejects_missing_non_regular_or_digest_mismatched_artifacts(tmp_path):
    """Catches a deleted, symlinked, or tampered audited input being trusted."""
    root = _copy_bootstrap(tmp_path)
    manifest = _manifest(root)
    uv = _current_uv_artifact(manifest)
    artifact = root / "artifacts" / uv["filename"]

    artifact.unlink()
    missing = _run_copy(root, "--offline")
    assert missing.returncode != 0
    assert "missing regular artifact" in missing.stderr

    artifact.symlink_to(root / "manifest.json")
    symlink = _run_copy(root, "--offline")
    assert symlink.returncode != 0
    assert "artifact" in symlink.stderr

    artifact.unlink()
    artifact.write_bytes(b"tampered archive")
    tampered = _run_copy(root, "--offline")
    assert tampered.returncode != 0
    assert "digest mismatch" in tampered.stderr


def test_bootstrap_checks_every_audited_platform_artifact_before_selecting_the_host(tmp_path):
    """Catches a corrupted non-host artifact being carried into a later platform's bootstrap."""
    root = _copy_bootstrap(tmp_path)
    manifest = _manifest(root)
    current = _current_uv_artifact(manifest)["filename"]
    other = next(item for item in manifest["uvArchives"] if item["filename"] != current)
    (root / "artifacts" / other["filename"]).write_bytes(b"cross-platform tampering")

    result = _run_copy(root, "--offline")

    assert result.returncode != 0
    assert "uv digest mismatch" in result.stderr


def test_bootstrap_rejects_duplicate_platform_selection_and_manifest_identity_mismatches(tmp_path):
    """Catches a manifest selecting an ambiguous or mislabeled release artifact."""
    root = _copy_bootstrap(tmp_path)
    manifest = _manifest(root)
    manifest["uvArchives"].append(dict(_current_uv_artifact(manifest)))
    _write_manifest(root, manifest)
    duplicate = _run_copy(root, "--offline")
    assert duplicate.returncode != 0
    assert "exactly one uv artifact" in duplicate.stderr

    root = _copy_bootstrap(tmp_path / "second")
    manifest = _manifest(root)
    uv = _current_uv_artifact(manifest)
    uv["filename"] = "not-the-release-name.tar.gz"
    _write_manifest(root, manifest)
    bad_filename = _run_copy(root, "--offline")
    assert bad_filename.returncode != 0
    assert "filename does not match" in bad_filename.stderr

    root = _copy_bootstrap(tmp_path / "third")
    manifest = _manifest(root)
    _current_wheel_artifact(manifest)["version"] = "9.9.9"
    _write_manifest(root, manifest)
    bad_version = _run_copy(root, "--offline")
    assert bad_version.returncode != 0
    assert "zstandard version must be 0.23.0" in bad_version.stderr


def test_bootstrap_rejects_nonempty_config_proxy_and_network_enabled_execution(tmp_path):
    """Catches implicit uv configuration or execution environments that can reach a network."""
    root = _copy_bootstrap(tmp_path)
    (root / "uv-empty.toml").write_text("index-url = 'https://example.invalid'\n", encoding="utf-8")
    config = _run_copy(root, "--offline")
    assert config.returncode != 0
    assert "empty config must be zero bytes" in config.stderr

    proxy_env = dict(os.environ, HTTPS_PROXY="http://proxy.invalid:8080")
    proxy = _run("--offline", env=proxy_env)
    assert proxy.returncode != 0
    assert "proxy environment is forbidden" in proxy.stderr

    network = _run()
    assert network.returncode != 0
    assert "--offline is required" in network.stderr


def test_bootstrap_manifest_has_the_frozen_handwritten_digests_and_lock_hashes():
    """Catches accidental regeneration of the approved immutable release identities."""
    manifest = _manifest(BOOTSTRAP)
    expected = {
        "uv-aarch64-apple-darwin-0.11.15.tar.gz": "sha256:7e5b336108f8576eda1939920ca0a805b4a9a3c3d3eb2f6140e38b7092fbe4f3",
        "uv-x86_64-unknown-linux-gnu-0.11.15.tar.gz": "sha256:b03e572f010bea94a4a52d42671ba72981e12894f71576181a1d26ff68546da7",
        "zstandard-0.23.0-cp313-cp313-macosx_11_0_arm64.whl": "sha256:38302b78a850ff82656beaddeb0bb989a0322a8bbb1bf1ab10c17506681d772a",
        "zstandard-0.23.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": "sha256:6f77fa49079891a4aab203d0b1744acc85577ed16d767b52fc089d83faf8d8ed",
        "uv-empty.toml": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    }
    listed = {
        item["filename"]: item["sha256"]
        for section in ("uvArchives", "zstandardWheels")
        for item in manifest[section]
    }
    listed[manifest["emptyConfig"]["filename"]] = manifest["emptyConfig"]["sha256"]
    assert listed == expected

    lock = (BOOTSTRAP / "requirements.lock").read_text(encoding="utf-8")
    assert "zstandard==0.23.0" in lock
    assert "38302b78a850ff82656beaddeb0bb989a0322a8bbb1bf1ab10c17506681d772a" in lock
    assert "6f77fa49079891a4aab203d0b1744acc85577ed16d767b52fc089d83faf8d8ed" in lock
    assert hashlib.sha256((BOOTSTRAP / "uv-empty.toml").read_bytes()).hexdigest() == expected["uv-empty.toml"].split(":", 1)[1]
