"""Read fixed Git objects and verify reviewed source and license facts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _GitSandbox:
    root: Path
    template: Path
    environment: dict[str, str]


@contextmanager
def _git_sandbox() -> Iterator[_GitSandbox]:
    """Create per-verification Git configuration and template isolation."""
    with tempfile.TemporaryDirectory(prefix="trusted-e1-git-env-") as temporary_name:
        root = Path(temporary_name)
        home = root / "home"
        xdg = root / "xdg"
        template = root / "empty-template"
        global_config = root / "global-config"
        home.mkdir()
        xdg.mkdir()
        template.mkdir()
        global_config.write_text("# intentionally empty\n", encoding="utf-8")
        yield _GitSandbox(
            root=root,
            template=template,
            environment={
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(xdg),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(global_config),
                "LC_ALL": "C",
                "PATH": os.defpath,
            },
        )


def _run_git(argv: list[str], *, cwd: Path, sandbox: _GitSandbox) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *argv],
            cwd=cwd,
            check=False,
            capture_output=True,
            env=sandbox.environment,
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


def _repository_root(path: Path, sandbox: _GitSandbox) -> Path:
    try:
        source_path = path.absolute()
        if source_path.resolve(strict=True) != source_path:
            raise _GitObjectError("Git source path must not contain symbolic links")
    except OSError as error:
        raise _GitObjectError("Git repository is unavailable") from error
    bare = _run_git(
        ["-C", str(source_path), "rev-parse", "--is-bare-repository"],
        cwd=source_path,
        sandbox=sandbox,
    )
    if bare.strip() == b"true":
        root = Path(
            _run_git(
                ["-C", str(source_path), "rev-parse", "--absolute-git-dir"],
                cwd=source_path,
                sandbox=sandbox,
            )
            .decode("utf-8")
            .strip()
        ).resolve()
    else:
        root = Path(
            _run_git(
                ["-C", str(source_path), "rev-parse", "--show-toplevel"],
                cwd=source_path,
                sandbox=sandbox,
            )
            .decode("utf-8")
            .strip()
        ).resolve()
    if root != source_path:
        raise _GitObjectError("Git source must be a repository root")
    return source_path


def _reject_symbolic_path(path: Path) -> None:
    if not path.is_absolute():
        raise _GitObjectError("Git object path must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise _GitObjectError("Git object path must not contain symbolic links")


def _trusted_git_path(repository: Path, raw_path: str) -> Path:
    try:
        trusted_root = repository.resolve(strict=True)
    except OSError as error:
        raise _GitObjectError("Git repository is unavailable") from error
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = repository / candidate
    candidate = candidate.absolute()
    _reject_symbolic_path(candidate)
    try:
        relative = candidate.resolve(strict=True).relative_to(trusted_root)
    except (OSError, ValueError) as error:
        raise _GitObjectError("Git object path is outside its trusted source") from error
    canonical_candidate = trusted_root / relative
    _reject_symbolic_path(canonical_candidate)
    return canonical_candidate


def _git_path(repository: Path, arguments: tuple[str, ...], sandbox: _GitSandbox) -> Path:
    raw_path = _run_git(
        ["-C", str(repository), "rev-parse", *arguments],
        cwd=repository,
        sandbox=sandbox,
    ).decode("utf-8").strip()
    return _trusted_git_path(repository, raw_path)


def _reject_untrusted_object_paths(repository: Path, sandbox: _GitSandbox) -> None:
    _git_path(repository, ("--absolute-git-dir",), sandbox)
    _git_path(repository, ("--git-common-dir",), sandbox)
    objects = _git_path(repository, ("--git-path", "objects"), sandbox)
    info = _trusted_git_path(repository, str(objects / "info"))
    alternates = info / "alternates"
    if os.path.lexists(alternates):
        raise _GitObjectError("Git object alternates are not trusted")


@contextmanager
def _open_object_database(source: GitObjectSource, commit: str) -> Iterator[tuple[Path, _GitSandbox]]:
    path = _validate_source(source)
    with _git_sandbox() as sandbox:
        if source.kind == "repository":
            repository = _repository_root(path, sandbox)
            _reject_untrusted_object_paths(repository, sandbox)
            _ensure_commit(repository, commit, sandbox)
            yield repository, sandbox
            return

        try:
            bundle_bytes = path.read_bytes()
        except OSError as error:
            raise _GitObjectError("Git bundle is unavailable") from error
        if _digest(bundle_bytes) != source.digest:
            raise _GitObjectError("Git bundle digest mismatch")
        with tempfile.TemporaryDirectory(prefix="trusted-e1-git-", dir=sandbox.root) as temporary_name:
            temporary = Path(temporary_name).resolve()
            _run_git(
                ["init", "--bare", f"--template={sandbox.template}", str(temporary)],
                cwd=temporary.parent,
                sandbox=sandbox,
            )
            _reject_untrusted_object_paths(temporary, sandbox)
            _run_git(["-C", str(temporary), "bundle", "verify", str(path)], cwd=temporary, sandbox=sandbox)
            _run_git(
                ["-C", str(temporary), "fetch", "--no-tags", str(path), commit],
                cwd=temporary,
                sandbox=sandbox,
            )
            _reject_untrusted_object_paths(temporary, sandbox)
            _ensure_commit(temporary, commit, sandbox)
            yield temporary, sandbox


def _ensure_commit(repository: Path, commit: str, sandbox: _GitSandbox) -> None:
    if not _COMMIT.fullmatch(commit):
        raise _GitObjectError("invalid fixed commit")
    _run_git(
        ["-C", str(repository), "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repository,
        sandbox=sandbox,
    )


def _tree_blob_id(repository: Path, commit: str, path: str, sandbox: _GitSandbox) -> str | None:
    output = _run_git(
        ["-C", str(repository), "ls-tree", "-z", "--full-tree", commit, "--", path],
        cwd=repository,
        sandbox=sandbox,
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
        with _open_object_database(source, commit) as (repository, sandbox):
            object_id = _tree_blob_id(repository, commit, path, sandbox)
            if object_id is None:
                raise _GitObjectError("fixed blob is unavailable")
            return _run_git(
                ["-C", str(repository), "cat-file", "blob", object_id],
                cwd=repository,
                sandbox=sandbox,
            )


def _repository_remotes(repository: Path, sandbox: _GitSandbox) -> frozenset[str]:
    names = _run_git(["-C", str(repository), "remote"], cwd=repository, sandbox=sandbox).decode("utf-8").splitlines()
    urls: set[str] = set()
    for name in names:
        output = _run_git(
            ["-C", str(repository), "remote", "get-url", "--all", name],
            cwd=repository,
            sandbox=sandbox,
        )
        urls.update(value for value in output.decode("utf-8").splitlines() if value)
    return frozenset(urls)


def _license_matches_spdx(payload: bytes, spdx: str) -> bool:
    if not _SPDX_IDENTIFIER.fullmatch(spdx):
        return False
    text = payload.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
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
            with _open_object_database(self.definition.object_source, self.definition.commit) as (repository, sandbox):
                if self.definition.object_source.kind == "repository":
                    observed_remotes = _repository_remotes(repository, sandbox)
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
                    object_id = _tree_blob_id(repository, self.definition.commit, path, sandbox)
                    if object_id is None:
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)
                    payload = _run_git(
                        ["-C", str(repository), "cat-file", "blob", object_id],
                        cwd=repository,
                        sandbox=sandbox,
                    )
                    digest = _digest(payload)
                    blob_digests[path] = digest
                    if digest != expected.digest:
                        facts["blobDigests"] = blob_digests
                        return self._result(pointer, context, valid=False, error_code="GIT_FACT_MISMATCH", facts=facts)

                license_id = _tree_blob_id(
                    repository,
                    self.definition.commit,
                    self.definition.license_fact.path,
                    sandbox,
                )
                if license_id is None:
                    return self._result(pointer, context, valid=False, error_code="LICENSE_MISMATCH", facts=facts)
                license_payload = _run_git(
                    ["-C", str(repository), "cat-file", "blob", license_id],
                    cwd=repository,
                    sandbox=sandbox,
                )
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
