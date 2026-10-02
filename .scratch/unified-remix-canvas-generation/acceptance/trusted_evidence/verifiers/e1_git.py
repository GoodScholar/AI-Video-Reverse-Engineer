"""Read fixed Git objects and verify reviewed source and license facts."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Iterator

from ..models import AcceptanceProfile, EvidencePointer, VerificationContext, VerificationResult
from ..registry import E1GitDefinition, GitObjectSource


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class _GitObjectError(RuntimeError):
    """An object source was unavailable without falling back to a workspace."""


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _run_git(argv: list[str], *, cwd: Path | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *argv],
            cwd=cwd,
            check=False,
            capture_output=True,
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


@contextmanager
def _open_object_database(source: GitObjectSource) -> Iterator[Path]:
    path = _validate_source(source)
    if source.kind == "repository":
        _run_git(["-C", str(path), "rev-parse", "--git-dir"])
        yield path
        return

    try:
        bundle_bytes = path.read_bytes()
    except OSError as error:
        raise _GitObjectError("Git bundle is unavailable") from error
    if _digest(bundle_bytes) != source.digest:
        raise _GitObjectError("Git bundle digest mismatch")
    _run_git(["bundle", "verify", str(path)])
    with tempfile.TemporaryDirectory(prefix="trusted-e1-git-") as temporary_name:
        temporary = Path(temporary_name)
        _run_git(["init", "--bare", str(temporary)])
        yield temporary


def _ensure_commit(repository: Path, commit: str) -> None:
    if not _COMMIT.fullmatch(commit):
        raise _GitObjectError("invalid fixed commit")
    _run_git(["-C", str(repository), "cat-file", "-e", f"{commit}^{{commit}}"])


def _tree_blob_id(repository: Path, commit: str, path: str) -> str | None:
    output = _run_git(["-C", str(repository), "ls-tree", "-z", "--full-tree", commit, "--", path])
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
        with _open_object_database(source) as repository:
            if source.kind == "bundle":
                _run_git(["-C", str(repository), "fetch", "--no-tags", str(source.path), commit])
            _ensure_commit(repository, commit)
            object_id = _tree_blob_id(repository, commit, path)
            if object_id is None:
                raise _GitObjectError("fixed blob is unavailable")
            return _run_git(["-C", str(repository), "cat-file", "blob", object_id])


def _repository_remotes(repository: Path) -> frozenset[str]:
    names = _run_git(["-C", str(repository), "remote"]).decode("utf-8").splitlines()
    urls: set[str] = set()
    for name in names:
        output = _run_git(["-C", str(repository), "remote", "get-url", "--all", name])
        urls.update(value for value in output.decode("utf-8").splitlines() if value)
    return frozenset(urls)


def _license_matches_spdx(payload: bytes, spdx: str) -> bool:
    normalized = payload.decode("utf-8", "replace").lower()
    expected = spdx.lower()
    if f"spdx-license-identifier: {expected}" in normalized:
        return True
    return expected == "mit" and "mit license" in normalized


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
            input_digest=pointer.artifact_ref,
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
            "remoteUrls": self.definition.remote_urls,
            "blobDigests": {},
            "license": {"path": self.definition.license_fact.path, "spdx": self.definition.license_fact.spdx},
            "reuse": {"kind": self.definition.reuse.kind, "attribution": self.definition.reuse.attribution},
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
            with _open_object_database(self.definition.object_source) as repository:
                if self.definition.object_source.kind == "bundle":
                    _run_git(
                        [
                            "-C",
                            str(repository),
                            "fetch",
                            "--no-tags",
                            str(self.definition.object_source.path),
                            self.definition.commit,
                        ]
                    )
                _ensure_commit(repository, self.definition.commit)
                if self.definition.object_source.kind == "repository":
                    if _repository_remotes(repository) != frozenset(self.definition.remote_urls):
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)

                blob_digests: dict[str, str] = {}
                for path, expected in self.definition.blobs.items():
                    object_id = _tree_blob_id(repository, self.definition.commit, path)
                    if object_id is None:
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)
                    payload = _run_git(["-C", str(repository), "cat-file", "blob", object_id])
                    digest = _digest(payload)
                    blob_digests[path] = digest
                    if digest != expected.digest:
                        facts["blobDigests"] = blob_digests
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)

                license_id = _tree_blob_id(repository, self.definition.commit, self.definition.license_fact.path)
                if license_id is None:
                    return self._result(pointer, context, valid=False, error_code="LICENSE_MISMATCH", facts=facts)
                license_payload = _run_git(["-C", str(repository), "cat-file", "blob", license_id])
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
