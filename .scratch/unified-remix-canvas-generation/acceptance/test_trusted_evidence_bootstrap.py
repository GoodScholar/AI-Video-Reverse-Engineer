import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ACCEPTANCE_ROOT = Path(__file__).parent
BOOTSTRAP = ACCEPTANCE_ROOT / "bootstrap"
VERIFIER = ACCEPTANCE_ROOT / "tools" / "verify_e2_bootstrap.py"
PYTHON = Path(sys.executable)


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
    platform = "darwin-arm64" if sys.platform == "darwin" else "linux-x86_64-gnu"
    return next(item for item in manifest["uvArchives"] if item["platform"] == platform)


def _current_wheel_artifact(manifest: dict[str, object]) -> dict[str, object]:
    platform = "darwin-arm64" if sys.platform == "darwin" else "linux-x86_64-gnu"
    return next(item for item in manifest["zstandardWheels"] if item["platform"] == platform)


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
