"""Read fixed Git objects and verify reviewed source and license facts."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Iterator

from ..models import AcceptanceProfile, EvidencePointer, VerificationContext, VerificationResult
from ..registry import E1GitDefinition, GitObjectSource


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SPDX_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")
_SPDX_HEADER = re.compile(r"^\s*SPDX-License-Identifier:\s*([A-Za-z0-9][A-Za-z0-9.+-]*)\s*$", re.MULTILINE)


class _GitObjectError(RuntimeError):
    """An object source was unavailable without falling back to a workspace."""


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _git_environment() -> dict[str, str]:
    """Provide Git no inherited repository, object, index, or config state."""
    return {
        "HOME": tempfile.gettempdir(),
        "LC_ALL": "C",
        "PATH": os.defpath,
    }


def _run_git(argv: list[str], *, cwd: Path) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *argv],
            cwd=cwd,
            check=False,
            capture_output=True,
            env=_git_environment(),
            shell=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _GitObjectError("Git object source is unavailable") from error
    if completed.returncode != 0:
        raise _GitObjectError("Git object source is unavailable")
    return completed.stdout


def _validate_source(source: GitObjectSource) -> Path:
    if source.kind not in {"repository", "bundle"}:
        raise _GitObjectError("unsupported Git object source")
    path = Path(source.path)
    if source.kind == "repository":
        if source.digest is not None:
            raise _GitObjectError("repository source must not have a digest")
        return path
    if not isinstance(source.digest, str) or not _DIGEST.fullmatch(source.digest):
        raise _GitObjectError("bundle source has no valid digest")
    return path


def _repository_root(path: Path) -> Path:
    try:
        source_path = path.resolve(strict=True)
    except OSError as error:
        raise _GitObjectError("Git repository is unavailable") from error
    bare = _run_git(["-C", str(source_path), "rev-parse", "--is-bare-repository"], cwd=source_path)
    if bare.strip() == b"true":
        root = Path(
            _run_git(["-C", str(source_path), "rev-parse", "--absolute-git-dir"], cwd=source_path)
            .decode("utf-8")
            .strip()
        ).resolve()
    else:
        root = Path(
            _run_git(["-C", str(source_path), "rev-parse", "--show-toplevel"], cwd=source_path)
            .decode("utf-8")
            .strip()
        ).resolve()
    if root != source_path:
        raise _GitObjectError("Git source must be a repository root")
    return source_path


@contextmanager
def _open_object_database(source: GitObjectSource, commit: str) -> Iterator[Path]:
    path = _validate_source(source)
    if source.kind == "repository":
        repository = _repository_root(path)
        _ensure_commit(repository, commit)
        yield repository
        return

    try:
        bundle_bytes = path.read_bytes()
    except OSError as error:
        raise _GitObjectError("Git bundle is unavailable") from error
    if _digest(bundle_bytes) != source.digest:
        raise _GitObjectError("Git bundle digest mismatch")
    with tempfile.TemporaryDirectory(prefix="trusted-e1-git-") as temporary_name:
        temporary = Path(temporary_name)
        _run_git(["init", "--bare", str(temporary)], cwd=temporary.parent)
        _run_git(["-C", str(temporary), "bundle", "verify", str(path)], cwd=temporary)
        _run_git(["-C", str(temporary), "fetch", "--no-tags", str(path), commit], cwd=temporary)
        _ensure_commit(temporary, commit)
        yield temporary


def _ensure_commit(repository: Path, commit: str) -> None:
    if not _COMMIT.fullmatch(commit):
        raise _GitObjectError("invalid fixed commit")
    _run_git(["-C", str(repository), "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repository)


def _tree_blob_id(repository: Path, commit: str, path: str) -> str | None:
    output = _run_git(
        ["-C", str(repository), "ls-tree", "-z", "--full-tree", commit, "--", path],
        cwd=repository,
    )
    if not output:
        return None
    entry = output.rstrip(b"\0")
    try:
        metadata, entry_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split(b" ", 2)
    except ValueError as error:
        raise _GitObjectError("invalid Git tree entry") from error
    if mode == b"160000" or kind != b"blob" or entry_path.decode("utf-8", "surrogateescape") != path:
        return None
    return object_id.decode("ascii")


class GitObjectReader:
    """Read only exact blobs from a reviewed Git object database or bundle."""

    def read_blob(self, source: GitObjectSource, commit: str, path: str) -> bytes:
        with _open_object_database(source, commit) as repository:
            object_id = _tree_blob_id(repository, commit, path)
            if object_id is None:
                raise _GitObjectError("fixed blob is unavailable")
            return _run_git(["-C", str(repository), "cat-file", "blob", object_id], cwd=repository)


def _repository_remotes(repository: Path) -> frozenset[str]:
    names = _run_git(["-C", str(repository), "remote"], cwd=repository).decode("utf-8").splitlines()
    urls: set[str] = set()
    for name in names:
        output = _run_git(["-C", str(repository), "remote", "get-url", "--all", name], cwd=repository)
        urls.update(value for value in output.decode("utf-8").splitlines() if value)
    return frozenset(urls)


def _license_matches_spdx(payload: bytes, spdx: str) -> bool:
    if not _SPDX_IDENTIFIER.fullmatch(spdx):
        return False
    text = payload.decode("utf-8", "replace")
    expected = spdx.casefold()
    header = _SPDX_HEADER.search(text)
    if header is not None:
        return header.group(1).casefold() == expected
    if expected != "mit":
        return False
    normalized = text.casefold().lstrip()
    return (
        normalized.startswith("mit license\n")
        and "permission is hereby granted, free of charge, to any person obtaining a copy" in normalized
        and 'the software is provided "as is"' in normalized
    )


def _verification_input_digest(pointer: EvidencePointer, definition: E1GitDefinition) -> str:
    policy = {
        "artifactRef": pointer.artifact_ref,
        "repositoryId": definition.repository_id,
        "objectSource": {
            "kind": definition.object_source.kind,
            "path": str(definition.object_source.path),
            "sha256": definition.object_source.digest,
        },
        "remoteUrls": definition.remote_urls,
        "commit": definition.commit,
        "blobs": {
            path: {
                "sha256": fact.digest,
                "localPath": fact.local_path,
                "attribution": fact.attribution,
            }
            for path, fact in sorted(definition.blobs.items())
        },
        "licenseFact": {
            "path": definition.license_fact.path,
            "spdx": definition.license_fact.spdx,
            "sha256": definition.license_fact.digest,
        },
        "reuse": {"kind": definition.reuse.kind, "attribution": definition.reuse.attribution},
    }
    return _digest(json.dumps(policy, separators=(",", ":"), sort_keys=True).encode("utf-8"))


class GitLicenseVerifier:
    """Verify E1 static policy against immutable Git objects only."""

    level = "E1"

    def __init__(self, verifier_id: str, definition: E1GitDefinition) -> None:
        self.verifier_id = verifier_id
        self.definition = definition
        self.reader = GitObjectReader()

    def _result(
        self,
        pointer: EvidencePointer,
        context: VerificationContext,
        *,
        valid: bool,
        error_code: str | None,
        facts: dict[str, object],
    ) -> VerificationResult:
        return VerificationResult(
            valid=valid,
            error_code=error_code,
            facts=facts,
            observed_at=context.observed_at,
            input_digest=_verification_input_digest(pointer, self.definition),
            zero_tolerance_violations=(),
        )

    def verify(
        self,
        pointer: EvidencePointer,
        profile: AcceptanceProfile,
        context: VerificationContext,
    ) -> VerificationResult:
        facts: dict[str, object] = {
            "repositoryId": self.definition.repository_id,
            "commit": self.definition.commit,
            "policyAttributionRemoteUrls": self.definition.remote_urls,
            "blobDigests": {},
            "license": {"path": self.definition.license_fact.path, "spdx": self.definition.license_fact.spdx},
            "reuse": {"kind": self.definition.reuse.kind, "attribution": self.definition.reuse.attribution},
        }
        if self.definition.object_source.kind == "bundle":
            facts["observedSourceIdentity"] = {
                "kind": "bundle",
                "bundleDigest": self.definition.object_source.digest,
                "repositoryId": self.definition.repository_id,
                "commit": self.definition.commit,
            }
        if (
            pointer.verifier_id != self.verifier_id
            or pointer.profile_id != profile.profile_id
            or pointer.subject_ref != profile.subject_ref
            or pointer.subject_ref != context.subject_ref
        ):
            return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)
        if any(
            fact.local_path is not None and fact.attribution != self.definition.reuse.attribution
            for fact in self.definition.blobs.values()
        ):
            return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)

        try:
            with _open_object_database(self.definition.object_source, self.definition.commit) as repository:
                if self.definition.object_source.kind == "repository":
                    observed_remotes = _repository_remotes(repository)
                    facts["observedSourceIdentity"] = {
                        "kind": "repository",
                        "repositoryPath": str(repository),
                        "commit": self.definition.commit,
                        "remoteUrls": tuple(sorted(observed_remotes)),
                    }
                    if observed_remotes != frozenset(self.definition.remote_urls):
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)

                blob_digests: dict[str, str] = {}
                for path, expected in self.definition.blobs.items():
                    object_id = _tree_blob_id(repository, self.definition.commit, path)
                    if object_id is None:
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)
                    payload = _run_git(["-C", str(repository), "cat-file", "blob", object_id], cwd=repository)
                    digest = _digest(payload)
                    blob_digests[path] = digest
                    if digest != expected.digest:
                        facts["blobDigests"] = blob_digests
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)

                license_id = _tree_blob_id(repository, self.definition.commit, self.definition.license_fact.path)
                if license_id is None:
                    return self._result(pointer, context, valid=False, error_code="LICENSE_MISMATCH", facts=facts)
                license_payload = _run_git(["-C", str(repository), "cat-file", "blob", license_id], cwd=repository)
                facts["blobDigests"] = blob_digests
                facts["license"] = {
                    "path": self.definition.license_fact.path,
                    "spdx": self.definition.license_fact.spdx,
                    "digest": _digest(license_payload),
                }
                if (
                    _digest(license_payload) != self.definition.license_fact.digest
                    or not _license_matches_spdx(license_payload, self.definition.license_fact.spdx)
                ):
                    return self._result(pointer, context, valid=False, error_code="LICENSE_MISMATCH", facts=facts)
        except _GitObjectError:
            return self._result(pointer, context, valid=False, error_code="SOURCE_REF_UNREACHABLE", facts=facts)

        return self._result(pointer, context, valid=True, error_code=None, facts=facts)
