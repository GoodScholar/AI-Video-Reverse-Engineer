"""Replay one reviewed E2 suite in a detached checkout of its fixed subject."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Final

from ..models import AcceptanceProfile, EvidencePointer, VerificationContext, VerificationResult
from ..redaction import redact_output
from ..registry import E2SuiteDefinition, PolicyError
from ..storage import ContentAddressedStore, canonical_json_bytes


_SUBJECT_REF: Final = re.compile(r"^git://product@([0-9a-f]{40})$")
_DEFAULT_REDACTION_PATTERNS: Final = (
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s]+"),
)
_MAX_OUTPUT_BYTES: Final = 32 * 1024


class _ReplayBlocked(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _safe_summary(data: bytes, patterns: tuple[re.Pattern[str], ...]) -> str:
    suffix = b"\n...[truncated]" if len(data) > _MAX_OUTPUT_BYTES else b""
    return redact_output(data[:_MAX_OUTPUT_BYTES] + suffix, patterns).decode("utf-8", "replace")


class SuiteRunner:
    """Execute only the argv and environment frozen in ``E2SuiteDefinition``."""

    def __init__(self, *, redaction_patterns: tuple[re.Pattern[str], ...] = _DEFAULT_REDACTION_PATTERNS) -> None:
        self.redaction_patterns = redaction_patterns

    def _environment(self, suite: E2SuiteDefinition) -> dict[str, str]:
        return dict(suite.allowed_environment)

    def _result(
        self,
        suite: E2SuiteDefinition,
        subject_ref: str,
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
            input_digest=_digest(
                {
                    "suiteId": suite.id,
                    "subjectRef": subject_ref,
                    "workingDirectory": suite.working_directory,
                    "setupArgv": suite.setup_argv,
                    "argv": suite.argv,
                    "expectedTestIds": suite.expected_test_ids,
                    "parser": suite.parser,
                    "allowedEnvironment": dict(suite.allowed_environment),
                    "lockfileDigests": dict(suite.lockfile_digests),
                    "toolVersions": dict(suite.tool_versions),
                }
            ),
            zero_tolerance_violations=(),
        )

    def _git_environment(self, temporary_root: Path) -> dict[str, str]:
        home = temporary_root / "home"
        xdg = temporary_root / "xdg"
        home.mkdir(exist_ok=True)
        xdg.mkdir(exist_ok=True)
        return {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C",
            "PATH": os.defpath,
        }

    def _source_repository(self, temporary_root: Path) -> Path:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                check=False,
                capture_output=True,
                env=self._git_environment(temporary_root),
                shell=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git repository is unavailable") from error
        if completed.returncode != 0:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git repository is unavailable")
        try:
            repository = Path(completed.stdout.decode("utf-8").strip()).resolve(strict=True)
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git repository is unavailable") from error
        if not repository.is_dir():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git repository is unavailable")
        return repository

    def _worktree(self, repository: Path, checkout: Path, commit: str, environment: dict[str, str]) -> None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository), "worktree", "add", "--detach", str(checkout), commit],
                check=False,
                capture_output=True,
                env=environment,
                shell=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git checkout is unavailable") from error
        if completed.returncode != 0:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git checkout is unavailable")

    def _remove_worktree(self, repository: Path, checkout: Path, environment: dict[str, str]) -> None:
        subprocess.run(
            ["git", "-C", str(repository), "worktree", "remove", "--force", str(checkout)],
            check=False,
            capture_output=True,
            env=environment,
            shell=False,
            timeout=30,
        )

    def _checked_file(self, checkout: Path, relative_path: str) -> Path:
        candidate = checkout / relative_path
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(checkout.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "checked file is unavailable") from error
        if candidate.is_symlink() or not resolved.is_file():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "checked file is unsafe")
        return resolved

    def _check_lockfiles(self, suite: E2SuiteDefinition, checkout: Path) -> dict[str, str]:
        observed: dict[str, str] = {}
        for relative_path, expected in suite.lockfile_digests.items():
            digest = "sha256:" + hashlib.sha256(self._checked_file(checkout, relative_path).read_bytes()).hexdigest()
            observed[relative_path] = digest
            if digest != expected:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "lockfile digest does not match policy")
        return observed

    def _check_tools(self, suite: E2SuiteDefinition, cwd: Path, environment: dict[str, str]) -> dict[str, str]:
        observed: dict[str, str] = {}
        for tool, expected in suite.tool_versions.items():
            command = suite.argv[0] if Path(suite.argv[0]).name == tool else tool
            try:
                completed = subprocess.run(
                    [command, "--version"],
                    cwd=cwd,
                    check=False,
                    capture_output=True,
                    env=environment,
                    shell=False,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"tool is unavailable: {tool}") from error
            version = _safe_summary(completed.stdout + completed.stderr, self.redaction_patterns).strip()
            observed[tool] = version
            if completed.returncode != 0 or expected not in version:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"tool version does not match policy: {tool}")
        return observed

    def _selection_is_complete(self, suite: E2SuiteDefinition) -> bool:
        return all(test_id in suite.argv for test_id in suite.expected_test_ids)

    def _store_receipt(self, context: VerificationContext, receipt: dict[str, object]) -> str:
        return ContentAddressedStore(Path(context.trust_root)).put(canonical_json_bytes(receipt))

    def run(
        self,
        suite: E2SuiteDefinition,
        subject_ref: str,
        context: VerificationContext,
    ) -> VerificationResult:
        match = _SUBJECT_REF.fullmatch(subject_ref)
        facts: dict[str, object] = {"suiteId": suite.id, "subjectRef": subject_ref}
        if match is None or subject_ref != context.subject_ref:
            return self._result(suite, subject_ref, context, valid=False, error_code="SUITE_NOT_ALLOWLISTED", facts=facts)
        if not self._selection_is_complete(suite):
            facts["expectedTestIds"] = suite.expected_test_ids
            return self._result(suite, subject_ref, context, valid=False, error_code="TEST_SELECTION_MISSING", facts=facts)

        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="trusted-e2-") as temporary_name:
                temporary_root = Path(temporary_name)
                git_environment = self._git_environment(temporary_root)
                repository = self._source_repository(temporary_root)
                checkout = temporary_root / "checkout"
                added = False
                try:
                    self._worktree(repository, checkout, match.group(1), git_environment)
                    added = True
                    working_directory = self._checked_file(checkout, suite.working_directory) if suite.working_directory != "." else checkout
                    if not working_directory.is_dir():
                        raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "suite working directory is unavailable")
                    lockfile_digests = self._check_lockfiles(suite, checkout)
                    environment = self._environment(suite)
                    tool_versions = self._check_tools(suite, working_directory, environment)
                    for setup_argv in (suite.setup_argv, suite.argv):
                        if not setup_argv:
                            continue
                        try:
                            completed = subprocess.run(
                                setup_argv,
                                cwd=working_directory,
                                check=False,
                                capture_output=True,
                                env=environment,
                                shell=False,
                                timeout=suite.timeout_seconds,
                            )
                        except subprocess.TimeoutExpired:
                            facts.update({"durationMilliseconds": int((time.monotonic() - started) * 1000)})
                            return self._result(suite, subject_ref, context, valid=False, error_code="TEST_REPLAY_TIMEOUT", facts=facts)
                        except OSError as error:
                            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command is unavailable") from error
                        if setup_argv == suite.setup_argv and completed.returncode != 0:
                            facts.update({"setupExitCode": completed.returncode})
                            return self._result(suite, subject_ref, context, valid=False, error_code="TEST_REPLAY_FAILED", facts=facts)
                        if setup_argv == suite.argv:
                            receipt = {
                                "schema": "trusted-e2-receipt/v1",
                                "suiteId": suite.id,
                                "subjectRef": subject_ref,
                                "exitCode": completed.returncode,
                                "selectedTestIds": suite.expected_test_ids,
                                "parser": suite.parser,
                                "stdout": _safe_summary(completed.stdout, self.redaction_patterns),
                                "stderr": _safe_summary(completed.stderr, self.redaction_patterns),
                                "toolVersions": tool_versions,
                                "lockfileDigests": lockfile_digests,
                                "durationMilliseconds": int((time.monotonic() - started) * 1000),
                            }
                            receipt_ref = self._store_receipt(context, receipt)
                            facts.update(receipt | {"receiptRef": receipt_ref})
                            return self._result(
                                suite,
                                subject_ref,
                                context,
                                valid=completed.returncode == 0,
                                error_code=None if completed.returncode == 0 else "TEST_REPLAY_FAILED",
                                facts=facts,
                            )
                finally:
                    if added:
                        self._remove_worktree(repository, checkout, git_environment)
        except _ReplayBlocked as error:
            facts["durationMilliseconds"] = int((time.monotonic() - started) * 1000)
            return self._result(suite, subject_ref, context, valid=False, error_code=error.code, facts=facts)

        return self._result(suite, subject_ref, context, valid=False, error_code="SOURCE_REF_UNREACHABLE", facts=facts)


class E2SuiteVerifier:
    """Verifier boundary that rejects mismatched pointers before replaying a suite."""

    level = "E2"

    def __init__(self, verifier_id: str, definition: E2SuiteDefinition, *, runner: SuiteRunner | None = None) -> None:
        self.verifier_id = verifier_id
        self.definition = definition
        self.runner = runner or SuiteRunner()

    def verify(
        self,
        pointer: EvidencePointer,
        profile: AcceptanceProfile,
        context: VerificationContext,
    ) -> VerificationResult:
        if (
            pointer.verifier_id != self.verifier_id
            or pointer.profile_id != profile.profile_id
            or pointer.subject_ref != profile.subject_ref
            or pointer.subject_ref != context.subject_ref
        ):
            return self.runner._result(
                self.definition,
                context.subject_ref,
                context,
                valid=False,
                error_code="SUITE_NOT_ALLOWLISTED",
                facts={"suiteId": self.definition.id},
            )
        try:
            ContentAddressedStore(Path(context.trust_root)).read(pointer.artifact_ref)
        except PolicyError as error:
            return self.runner._result(
                self.definition,
                pointer.subject_ref,
                context,
                valid=False,
                error_code=error.code,
                facts={"suiteId": self.definition.id, "receiptRef": pointer.artifact_ref},
            )
        return self.runner.run(self.definition, pointer.subject_ref, context)
