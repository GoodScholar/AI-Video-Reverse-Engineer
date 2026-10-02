"""Replay reviewed E2 suites from isolated Git objects and machine reports."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Final, Iterator

from ..models import AcceptanceProfile, EvidencePointer, VerificationContext, VerificationResult
from ..redaction import RedactionError, redact_output
from ..registry import E2SuiteDefinition, PolicyError
from ..storage import ContentAddressedStore, canonical_json_bytes


_SUBJECT_REF: Final = re.compile(r"^git://product@([0-9a-f]{40})$")
_DEFAULT_REDACTION_PATTERNS: Final = (
    re.compile(r"(?ims)^\s*authorization\s*:\s*(?:basic|bearer|token)\s+\S+\s*$"),
    re.compile(r"(?im)^\s*(?:set-)?cookie\s*:\s*.+$"),
    re.compile(r"(?im)^\s*ssh_auth_sock\s*[:=]\s*\S+\s*$"),
    re.compile(r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\b\s*[:=]\s*[^\s]+"),
    re.compile(r"(?i)(?:file://)?[^\s]*\.ssh/(?:id_[^\s/]+|[^\s/]*private[^\s/]*)"),
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
    try:
        return redact_output(data[:_MAX_OUTPUT_BYTES] + suffix, patterns).decode("utf-8")
    except RedactionError as error:
        raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "receipt output cannot be safely redacted") from error


class SuiteRunner:
    """Run registry argv only after rebuilding the exact commit in a private Git repo."""

    def __init__(self, *, redaction_patterns: tuple[re.Pattern[str], ...] = ()) -> None:
        self.redaction_patterns = _DEFAULT_REDACTION_PATTERNS + redaction_patterns

    def _environment(self, suite: E2SuiteDefinition) -> dict[str, str]:
        return {"PATH": os.defpath, **suite.allowed_environment}

    def _result(self, suite: E2SuiteDefinition, subject_ref: str, context: VerificationContext, *, valid: bool, error_code: str | None, facts: dict[str, object]) -> VerificationResult:
        return VerificationResult(
            valid=valid, error_code=error_code, facts=facts, observed_at=context.observed_at,
            input_digest=_digest({
                "suiteId": suite.id, "subjectRef": subject_ref, "workingDirectory": suite.working_directory,
                "setupArgv": suite.setup_argv, "argv": suite.argv, "expectedTestIds": suite.expected_test_ids,
                "parser": suite.parser, "allowedEnvironment": dict(suite.allowed_environment),
                "lockfileDigests": dict(suite.lockfile_digests), "toolVersions": dict(suite.tool_versions),
            }),
            zero_tolerance_violations=(),
        )

    def _git_environment(self, root: Path) -> dict[str, str]:
        for name in ("home", "xdg", "empty-template"):
            (root / name).mkdir(exist_ok=True)
        return {
            "HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1", "LC_ALL": "C", "PATH": os.defpath,
        }

    def _git(self, argv: list[str], *, environment: dict[str, str], input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["git", "--no-replace-objects", *argv], input=input, check=False, capture_output=True,
                env=environment, shell=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git object source is unavailable") from error

    def _source_repository(self, root: Path) -> Path:
        completed = self._git(["rev-parse", "--show-toplevel"], environment=self._git_environment(root))
        if completed.returncode != 0:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git repository is unavailable")
        try:
            repository = Path(completed.stdout.decode("utf-8").strip()).resolve(strict=True)
        except (OSError, UnicodeDecodeError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git repository is unavailable") from error
        if not repository.is_dir():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git repository is unavailable")
        return repository

    def _verify_commit(self, repository: Path, commit: str, environment: dict[str, str]) -> None:
        result = self._git(["-C", str(repository), "cat-file", "-t", commit], environment=environment)
        if result.returncode != 0 or result.stdout.strip() != b"commit":
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "subject is not an exact commit object")

    def _isolated_repository(self, source: Path, commit: str, root: Path, environment: dict[str, str]) -> Path:
        git_dir = self._git(["-C", str(source), "rev-parse", "--absolute-git-dir"], environment=environment)
        if git_dir.returncode != 0:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product Git directory is unavailable")
        if os.path.lexists(Path(git_dir.stdout.decode("utf-8").strip()) / "objects" / "info" / "alternates"):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "untrusted Git object alternates are configured")
        self._verify_commit(source, commit, environment)
        isolated = root / "isolated.git"
        if self._git(["init", "--bare", f"--template={root / 'empty-template'}", str(isolated)], environment=environment).returncode != 0:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "isolated Git repository cannot be created")
        packed = self._git(["-C", str(source), "pack-objects", "--stdout", "--revs"], environment=environment, input=f"{commit}\n".encode("ascii"))
        if packed.returncode != 0 or not packed.stdout:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git objects cannot be packed")
        if self._git(["-C", str(isolated), "index-pack", "--stdin"], environment=environment, input=packed.stdout).returncode != 0:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git objects cannot be imported")
        self._verify_commit(isolated, commit, environment)
        return isolated

    def _remove_worktree(self, repository: Path, checkout: Path, environment: dict[str, str]) -> None:
        try:
            removed = self._git(["-C", str(repository), "worktree", "remove", "--force", str(checkout)], environment=environment)
        except _ReplayBlocked as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "temporary checkout cleanup timed out") from error
        if removed.returncode == 0:
            return
        self._git(["-C", str(repository), "worktree", "prune", "--expire=now"], environment=environment)
        raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "temporary checkout cleanup failed")

    @contextmanager
    def _detached_checkout(self, source: Path, commit: str) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="trusted-e2-") as name:
            root = Path(name)
            environment = self._git_environment(root)
            isolated = self._isolated_repository(source, commit, root, environment)
            checkout = root / "checkout"
            added = self._git(
                ["-C", str(isolated), "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false",
                 "worktree", "add", "--detach", str(checkout), commit],
                environment=environment,
            )
            if added.returncode != 0:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed detached checkout is unavailable")
            try:
                head = self._git(["-C", str(checkout), "rev-parse", "HEAD"], environment=environment)
                if head.returncode != 0 or head.stdout.decode("ascii", "strict").strip() != commit:
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "detached checkout head does not match subject")
                yield checkout
            finally:
                self._remove_worktree(isolated, checkout, environment)

    def _checked_path(self, checkout: Path, relative: str, *, file: bool) -> Path:
        root = checkout.resolve(strict=True)
        current = checkout
        for part in Path(relative).parts:
            if part not in {"", "."}:
                current /= part
                if current.is_symlink():
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "checked path contains a symbolic link")
        try:
            result = (checkout / relative).resolve(strict=True)
            result.relative_to(root)
        except (OSError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "checked path is unavailable") from error
        if (file and not result.is_file()) or (not file and not result.is_dir()):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "checked path has the wrong type")
        return result

    def _check_lockfiles(self, suite: E2SuiteDefinition, checkout: Path) -> dict[str, str]:
        observed = {}
        for path, expected in suite.lockfile_digests.items():
            digest = "sha256:" + hashlib.sha256(self._checked_path(checkout, path, file=True).read_bytes()).hexdigest()
            observed[path] = digest
            if digest != expected:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "lockfile digest does not match policy")
        return observed

    def _validate_command(self, argv: tuple[str, ...], cwd: Path, checkout: Path) -> None:
        if "/" not in argv[0]:
            return
        path = Path(argv[0])
        if path.is_absolute():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command must not use an absolute path")
        try:
            relative = str((cwd / path).resolve(strict=True).relative_to(checkout.resolve(strict=True)))
        except (OSError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command escapes checkout") from error
        self._checked_path(checkout, relative, file=True)

    def _run_command(self, argv: tuple[str, ...], *, cwd: Path, checkout: Path, environment: dict[str, str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        self._validate_command(argv, cwd, checkout)
        try:
            return subprocess.run(argv, cwd=cwd, check=False, capture_output=True, env=environment, shell=False, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise _ReplayBlocked("TEST_REPLAY_TIMEOUT", "registered command timed out") from error
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command is unavailable") from error

    def _check_tools(self, suite: E2SuiteDefinition, cwd: Path, checkout: Path, environment: dict[str, str]) -> dict[str, str]:
        observed = {}
        for tool, expected in suite.tool_versions.items():
            command = suite.argv[0] if Path(suite.argv[0]).name == tool else tool
            completed = self._run_command((command, "--version"), cwd=cwd, checkout=checkout, environment=environment, timeout=suite.timeout_seconds)
            version = _safe_summary(completed.stdout + completed.stderr, self.redaction_patterns).strip()
            observed[tool] = version
            if completed.returncode != 0 or version != expected:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"tool version does not exactly match policy: {tool}")
        return observed

    def _outcomes(self, suite: E2SuiteDefinition, outcomes: dict[str, str]) -> tuple[str, ...]:
        if any(test_id not in outcomes for test_id in suite.expected_test_ids):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "expected test IDs were not executed")
        if any(outcomes[test_id] != "passed" for test_id in suite.expected_test_ids):
            raise _ReplayBlocked("TEST_REPLAY_FAILED", "expected test ID did not pass")
        return suite.expected_test_ids

    def _pytest_report(self, suite: E2SuiteDefinition, cwd: Path, checkout: Path) -> tuple[str, ...]:
        if "--json-report" not in suite.argv:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest JSON reporter is not enabled")
        report_path = ".report.json"
        for index, argument in enumerate(suite.argv):
            if argument.startswith("--json-report-file="):
                report_path = argument.removeprefix("--json-report-file=")
            elif argument == "--json-report-file":
                if index + 1 == len(suite.argv):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest JSON report path is missing")
                report_path = suite.argv[index + 1]
        if not report_path or Path(report_path).is_absolute() or ".." in Path(report_path).parts:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest JSON report path is unsafe")
        try:
            relative = str((cwd / report_path).resolve(strict=True).relative_to(checkout.resolve(strict=True)))
            report = json.loads(self._checked_path(checkout, relative, file=True).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest JSON report is invalid") from error
        if not isinstance(report, dict) or not isinstance(report.get("tests"), list):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest JSON report has no test list")
        outcomes = {
            item.get("nodeid"): item.get("outcome") for item in report["tests"]
            if isinstance(item, dict) and isinstance(item.get("nodeid"), str) and isinstance(item.get("outcome"), str)
        }
        return self._outcomes(suite, outcomes)

    def _vitest_report(self, suite: E2SuiteDefinition, stdout: bytes) -> tuple[str, ...]:
        if "--reporter=json" not in suite.argv and not any(suite.argv[index:index + 2] == ("--reporter", "json") for index in range(len(suite.argv) - 1)):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest JSON reporter is not enabled")
        try:
            report = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest JSON stdout is invalid") from error
        if not isinstance(report, dict) or not isinstance(report.get("testResults"), list):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest JSON report has no test results")
        outcomes = {}
        for result in report["testResults"]:
            for assertion in result.get("assertionResults", ()) if isinstance(result, dict) else ():
                if isinstance(assertion, dict) and isinstance(assertion.get("fullName"), str) and isinstance(assertion.get("status"), str):
                    outcomes[assertion["fullName"]] = assertion["status"]
        return self._outcomes(suite, outcomes)

    def _selected_tests(self, suite: E2SuiteDefinition, stdout: bytes, cwd: Path, checkout: Path) -> tuple[str, ...]:
        return self._pytest_report(suite, cwd, checkout) if suite.parser == "pytest-json-v1" else self._vitest_report(suite, stdout)

    def _store_receipt(self, context: VerificationContext, receipt: dict[str, object]) -> str:
        return ContentAddressedStore(Path(context.trust_root)).put(canonical_json_bytes(receipt))

    def run(self, suite: E2SuiteDefinition, subject_ref: str, context: VerificationContext) -> VerificationResult:
        match = _SUBJECT_REF.fullmatch(subject_ref)
        facts: dict[str, object] = {"suiteId": suite.id, "subjectRef": subject_ref}
        if match is None or subject_ref != context.subject_ref:
            return self._result(suite, subject_ref, context, valid=False, error_code="SUITE_NOT_ALLOWLISTED", facts=facts)
        if not all(test_id in suite.argv for test_id in suite.expected_test_ids):
            facts["expectedTestIds"] = suite.expected_test_ids
            return self._result(suite, subject_ref, context, valid=False, error_code="TEST_SELECTION_MISSING", facts=facts)
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="trusted-e2-source-") as name:
                source = self._source_repository(Path(name))
            with self._detached_checkout(source, match.group(1)) as checkout:
                cwd = checkout if suite.working_directory == "." else self._checked_path(checkout, suite.working_directory, file=False)
                lockfiles = self._check_lockfiles(suite, checkout)
                environment = self._environment(suite)
                if suite.setup_argv:
                    setup = self._run_command(suite.setup_argv, cwd=cwd, checkout=checkout, environment=environment, timeout=suite.timeout_seconds)
                    if setup.returncode != 0:
                        facts["setupExitCode"] = setup.returncode
                        return self._result(suite, subject_ref, context, valid=False, error_code="TEST_REPLAY_FAILED", facts=facts)
                tools = self._check_tools(suite, cwd, checkout, environment)
                completed = self._run_command(suite.argv, cwd=cwd, checkout=checkout, environment=environment, timeout=suite.timeout_seconds)
                selected = () if completed.returncode else self._selected_tests(suite, completed.stdout, cwd, checkout)
                receipt = {
                    "schema": "trusted-e2-receipt/v1", "suiteId": suite.id, "subjectRef": subject_ref,
                    "exitCode": completed.returncode, "selectedTestIds": selected, "parser": suite.parser,
                    "stdout": _safe_summary(completed.stdout, self.redaction_patterns),
                    "stderr": _safe_summary(completed.stderr, self.redaction_patterns),
                    "toolVersions": tools, "lockfileDigests": lockfiles,
                    "durationMilliseconds": int((time.monotonic() - started) * 1000),
                }
                receipt_ref = self._store_receipt(context, receipt)
                facts.update(receipt | {"receiptRef": receipt_ref})
                return self._result(suite, subject_ref, context, valid=completed.returncode == 0, error_code=None if completed.returncode == 0 else "TEST_REPLAY_FAILED", facts=facts)
        except _ReplayBlocked as error:
            facts["durationMilliseconds"] = int((time.monotonic() - started) * 1000)
            return self._result(suite, subject_ref, context, valid=False, error_code=error.code, facts=facts)


class E2SuiteVerifier:
    level = "E2"

    def __init__(self, verifier_id: str, definition: E2SuiteDefinition, *, runner: SuiteRunner | None = None) -> None:
        self.verifier_id = verifier_id
        self.definition = definition
        self.runner = runner or SuiteRunner()

    def verify(self, pointer: EvidencePointer, profile: AcceptanceProfile, context: VerificationContext) -> VerificationResult:
        if pointer.verifier_id != self.verifier_id or pointer.profile_id != profile.profile_id or pointer.subject_ref != profile.subject_ref or pointer.subject_ref != context.subject_ref:
            return self.runner._result(self.definition, context.subject_ref, context, valid=False, error_code="SUITE_NOT_ALLOWLISTED", facts={"suiteId": self.definition.id})
        try:
            ContentAddressedStore(Path(context.trust_root)).read(pointer.artifact_ref)
        except PolicyError as error:
            return self.runner._result(self.definition, pointer.subject_ref, context, valid=False, error_code=error.code, facts={"suiteId": self.definition.id, "receiptRef": pointer.artifact_ref})
        return self.runner.run(self.definition, pointer.subject_ref, context)
