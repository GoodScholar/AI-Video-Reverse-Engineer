from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

from trusted_evidence.models import AcceptanceProfile, EvidencePointer, VerificationContext
from trusted_evidence.registry import (
    E1GitDefinition,
    GitBlobFact,
    GitObjectSource,
    LicenseFact,
    ReuseFact,
)
from trusted_evidence.verifiers.e1_git import GitLicenseVerifier, GitObjectReader


REMOTE = "https://github.com/example/upstream.git"
OBSERVED_AT = "2026-10-02T08:30:00Z"


def _git(path: Path, *argv: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *argv],
        check=True,
        capture_output=True,
        shell=False,
        text=True,
    )
    return completed.stdout.strip()


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _commit_repository(tmp_path: Path, name: str = "upstream") -> tuple[Path, str, bytes, bytes]:
    repository = tmp_path / name
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "acceptance@example.test")
    _git(repository, "config", "user.name", "Acceptance")
    _git(repository, "remote", "add", "origin", REMOTE)
    source = f"export const canvas = '{name}';\n".encode()
    license_text = b"MIT License\n\nCopyright (c) Example\n"
    (repository / "src").mkdir()
    (repository / "src" / "canvas.ts").write_bytes(source)
    (repository / "LICENSE").write_bytes(license_text)
    _git(repository, "add", "src/canvas.ts", "LICENSE")
    _git(repository, "commit", "-m", "trusted source")
    return repository, _git(repository, "rev-parse", "HEAD"), source, license_text


def _definition(
    repository: Path,
    commit: str,
    source: bytes,
    license_text: bytes,
    **overrides: object,
) -> E1GitDefinition:
    values: dict[str, object] = {
        "repository_id": "github.com/example/upstream",
        "object_source": GitObjectSource("repository", repository, None),
        "remote_urls": (REMOTE,),
        "commit": commit,
        "blobs": {"src/canvas.ts": GitBlobFact(_sha256(source), "src/canvas.ts", "Example upstream")},
        "license_fact": LicenseFact("LICENSE", "MIT", _sha256(license_text)),
        "reuse": ReuseFact("structural-adaptation", "Example upstream"),
    }
    values.update(overrides)
    return E1GitDefinition(**values)  # type: ignore[arg-type]


def _inputs(verifier_id: str = "e1.test") -> tuple[EvidencePointer, AcceptanceProfile, VerificationContext]:
    subject_ref = "git://product@" + "a" * 40
    pointer = EvidencePointer(
        id="ticket-23-e1-a1b2c3d4",
        profile_id="ticket-23/v1",
        verifier_id=verifier_id,
        subject_ref=subject_ref,
        artifact_ref="sha256:" + "b" * 64,
        registered_at=OBSERVED_AT,
    )
    profile = AcceptanceProfile(
        profile_id="ticket-23/v1",
        version="v1",
        subject_ref=subject_ref,
        owned_item_ids={"C01"},
        required_verifier_ids={"C01": (verifier_id,)},
    )
    return pointer, profile, VerificationContext(subject_ref, OBSERVED_AT, "test-root")


def _verify(definition: E1GitDefinition):
    pointer, profile, context = _inputs()
    return GitLicenseVerifier("e1.test", definition).verify(pointer, profile, context)


def test_e1_verifies_exact_commit_remote_source_blobs_and_license_digest(tmp_path):
    repository, commit, source, license_text = _commit_repository(tmp_path)

    result = _verify(_definition(repository, commit, source, license_text))

    assert result.valid is True
    assert result.error_code is None
    assert result.facts["commit"] == commit
    assert result.facts["repositoryId"] == "github.com/example/upstream"
    assert GitObjectReader().read_blob(GitObjectSource("repository", repository, None), commit, "src/canvas.ts") == source
    bundle = tmp_path / "upstream.bundle"
    _git(repository, "bundle", "create", str(bundle), "--all")
    bundle_source = GitObjectSource("bundle", bundle, _sha256(bundle.read_bytes()))
    assert GitObjectReader().read_blob(bundle_source, commit, "src/canvas.ts") == source
    assert _verify(_definition(repository, commit, source, license_text, object_source=bundle_source)).valid is True


def test_e1_ignores_untracked_and_uncommitted_workspace_changes(tmp_path):
    repository, commit, source, license_text = _commit_repository(tmp_path)
    (repository / "src" / "canvas.ts").write_bytes(b"export const canvas = 'dirty';\n")
    (repository / "LICENSE").write_bytes(b"Apache License\n")
    (repository / "untracked-proof.txt").write_text("workspace fallback must not run\n")

    result = _verify(_definition(repository, commit, source, license_text))

    assert result.valid is True
    assert result.facts["blobDigests"]["src/canvas.ts"] == _sha256(source)


def test_e1_blocks_when_commit_or_bundle_is_unreachable_without_workspace_fallback(tmp_path):
    repository, commit, source, license_text = _commit_repository(tmp_path)
    unreachable = tmp_path / "workspace-looking-directory"
    unreachable.mkdir()
    (unreachable / "src").mkdir()
    (unreachable / "src" / "canvas.ts").write_bytes(source)
    (unreachable / "LICENSE").write_bytes(license_text)

    repository_result = _verify(_definition(unreachable, commit, source, license_text))

    assert repository_result.valid is False
    assert repository_result.error_code == "SOURCE_REF_UNREACHABLE"

    bundle = tmp_path / "not-a-bundle"
    bundle.write_bytes(b"not a git bundle")
    bundle_result = _verify(
        _definition(
            repository,
            commit,
            source,
            license_text,
            object_source=GitObjectSource("bundle", bundle, _sha256(bundle.read_bytes())),
        )
    )

    assert bundle_result.valid is False
    assert bundle_result.error_code == "SOURCE_REF_UNREACHABLE"


@pytest.mark.parametrize(
    ("override", "expected_code"),
    (
        ({"remote_urls": ("https://github.com/example/other.git",)}, "GIT_FACT_MISMATCH"),
        ({"blobs": {"src/missing.ts": GitBlobFact("sha256:" + "0" * 64, None, None)}}, "GIT_FACT_MISMATCH"),
        ({"reuse": ReuseFact("structural-adaptation", "Different upstream")}, "GIT_FACT_MISMATCH"),
        ({"license_fact": LicenseFact("LICENSE", "Apache-2.0", "sha256:" + "0" * 64)}, "LICENSE_MISMATCH"),
    ),
)
def test_e1_fails_on_remote_blob_path_attribution_or_license_mismatch(tmp_path, override, expected_code):
    repository, commit, source, license_text = _commit_repository(tmp_path)

    result = _verify(_definition(repository, commit, source, license_text, **override))

    assert result.valid is False
    assert result.error_code == expected_code


def test_e1_rejects_host_repository_head_as_toonflow_source(tmp_path):
    upstream, upstream_commit, source, license_text = _commit_repository(tmp_path, "toonflow")
    host, host_commit, _, _ = _commit_repository(tmp_path, "host-product")
    (host / "host-only.txt").write_text("host repository is not Toonflow\n")
    _git(host, "add", "host-only.txt")
    _git(host, "commit", "-m", "host-only change")
    host_commit = _git(host, "rev-parse", "HEAD")
    assert host_commit != upstream_commit

    result = _verify(_definition(host, upstream_commit, source, license_text))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
