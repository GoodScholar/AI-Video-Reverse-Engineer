"""Fail-closed E2 replay in an isolated Git object closure."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Final, Iterator

from ..models import AcceptanceProfile, EvidencePointer, VerificationContext, VerificationResult
from ..redaction import RedactionError, redact_output
from ..registry import E2SuiteDefinition, PolicyError
from ..storage import ContentAddressedStore, canonical_json_bytes


_SUBJECT_REF: Final = re.compile(r"^git://product@([0-9a-f]{40})$")
_MAX_CAPTURE: Final = 32 * 1024
_DEFAULT_REDACTION: Final = (
    re.compile(r"(?i)\b(?:proxy-)?authorization\s*:\s*(?:basic|bearer|token)\s+\S+"),
    re.compile(r"(?i)\b(?:set-)?cookie\s*:\s*[^\r\n]+"),
    re.compile(r"(?i)\bssh_auth_sock\s*[:=]\s*\S+"),
    re.compile(r"(?i)\baws_(?:access_key_id|secret_access_key|session_token)\s*[:=]\s*\S+"),
    re.compile(r'(?i)\\?"(?:api[_-]?key|secret|token|aws_(?:access_key_id|secret_access_key|session_token))\\?"\s*:\s*\\?"(?:[^"\\]|\\.)*\\?"'),
    re.compile(r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\b\s*[:=]\s*[^\s]+"),
    re.compile(r"(?i)(?:file://)?[^\s]*\.ssh/(?:id_[^\s/]+|[^\s/]*private[^\s/]*)"),
    re.compile(r"(?i)\b(?:cookie|set_cookie)\s*[:=]\s*[^\s]+"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s/]+"),
)
_DANGEROUS_CONFIG: Final = re.compile(
    r"^(?:alias\.|core\.(?:hookspath|sshcommand)|credential\.|diff\..*\.textconv|"
    r"filter\..*\.(?:clean|smudge|process)|gpg\..*\.program|include(?:if)?\.|"
    r"merge\..*\.driver|remote\..*\.(?:uploadpack|receivepack|promisor|partialclonefilter)|"
    r"extensions\.partialclone)$",
    re.IGNORECASE,
)


class _ReplayBlocked(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    truncated: bool


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _summary(data: bytes, *, truncated: bool, patterns: tuple[re.Pattern[str], ...]) -> str:
    try:
        return redact_output(data + (b"\n...[truncated]" if truncated else b""), patterns).decode("utf-8")
    except RedactionError as error:
        raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "output cannot be safely redacted") from error


class SuiteRunner:
    """Execute immutable command sequences; nothing supplied by a pointer is executable."""

    def __init__(self, *, redaction_patterns: tuple[re.Pattern[str], ...] = ()) -> None:
        self.redaction_patterns = _DEFAULT_REDACTION + redaction_patterns

    def _result(self, suite: E2SuiteDefinition, ref: str, context: VerificationContext, *, valid: bool, code: str | None, facts: dict[str, object]) -> VerificationResult:
        return VerificationResult(
            valid=valid, error_code=code, facts=facts, observed_at=context.observed_at,
            input_digest=_digest({"suiteId": suite.id, "subjectRef": ref, "setupArgv": suite.setup_argv,
                "argv": suite.argv, "expectedTestIds": suite.expected_test_ids, "parser": suite.parser,
                "workingDirectory": suite.working_directory, "subjectRepository": suite.subject_repository,
                "allowedEnvironment": dict(suite.allowed_environment), "timeoutSeconds": suite.timeout_seconds,
                "lockfileDigests": dict(suite.lockfile_digests), "toolVersions": dict(suite.tool_versions)}),
            zero_tolerance_violations=(),
        )

    def _environment(self, suite: E2SuiteDefinition) -> dict[str, str]:
        """Expose only the static allowlist; executor-owned PATH is added later."""
        return dict(suite.allowed_environment)

    def _relative(self, checkout: Path, candidate: Path) -> str:
        try:
            root = Path(os.path.realpath(checkout))
            return str(Path(os.path.realpath(candidate)).relative_to(root))
        except ValueError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path escapes checkout") from error

    def _git_environment(self, root: Path) -> dict[str, str]:
        for item in ("home", "xdg", "empty-template"):
            (root / item).mkdir(exist_ok=True)
        return {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1", "LC_ALL": "C", "PATH": os.defpath}

    def _git(self, argv: list[str], *, env: dict[str, str], input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(["git", "--no-replace-objects", *argv], input=input, check=False,
                capture_output=True, env=env, shell=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git source is unavailable") from error

    def _source_repository(self, root: Path) -> Path:
        result = self._git(["rev-parse", "--show-toplevel"], env=self._git_environment(root))
        if result.returncode:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product repository is unavailable")
        try:
            return Path(result.stdout.decode().strip()).resolve(strict=True)
        except (OSError, UnicodeDecodeError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product repository is unavailable") from error

    def _no_symlink_ancestors(self, path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git path contains a symbolic link")

    def _source_safety(self, source: Path, commit: str, env: dict[str, str]) -> None:
        for argument in ("--absolute-git-dir", "--git-common-dir"):
            result = self._git(["-C", str(source), "rev-parse", argument], env=env)
            if result.returncode:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git metadata is unavailable")
            raw = Path(result.stdout.decode().strip())
            path = raw if raw.is_absolute() else source / raw
            path = path.resolve(strict=True)
            self._no_symlink_ancestors(path)
            for objects in (path / "objects",):
                self._no_symlink_ancestors(objects)
                if os.path.lexists(objects / "info" / "alternates"):
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git alternates are not trusted")
        names = self._git(["-C", str(source), "config", "--local", "--list", "--name-only"], env=env)
        config_names = names.stdout.decode("utf-8", "replace").splitlines() if names.returncode == 0 else ()
        if any(
            _DANGEROUS_CONFIG.fullmatch(name)
            or name.startswith("remote.") and name.endswith((".promisor", ".partialclonefilter", ".uploadpack", ".receivepack"))
            for name in config_names
        ):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git local configuration is not trusted")
        kind = self._git(["-C", str(source), "cat-file", "-t", commit], env=env)
        if kind.returncode or kind.stdout.strip() != b"commit":
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "subject is not an exact commit object")

    def _isolated_repository(self, source: Path, commit: str, root: Path, env: dict[str, str]) -> Path:
        self._source_safety(source, commit, env)
        isolated = root / "isolated.git"
        if self._git(["init", "--bare", f"--template={root / 'empty-template'}", str(isolated)], env=env).returncode:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "cannot initialize isolated Git repository")
        self._stream_object_closure(source, isolated, commit, env)
        if self._git(["-C", str(isolated), "fsck", "--connectivity-only", commit], env=env).returncode:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git object closure is incomplete")
        kind = self._git(["-C", str(isolated), "cat-file", "-t", commit], env=env)
        if kind.returncode or kind.stdout.strip() != b"commit":
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "isolated closure lacks the fixed commit")
        return isolated

    def _stream_object_closure(self, source: Path, isolated: Path, commit: str, env: dict[str, str]) -> None:
        """Pipe the fixed object closure directly into index-pack; never buffer it."""
        producer: subprocess.Popen[bytes] | None = None
        consumer: subprocess.Popen[bytes] | None = None
        try:
            producer = subprocess.Popen(
                ["git", "--no-replace-objects", "-C", str(source), "pack-objects", "--stdout", "--revs"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, shell=False,
            )
            assert producer.stdin and producer.stdout
            consumer = subprocess.Popen(
                ["git", "--no-replace-objects", "-C", str(isolated), "index-pack", "--stdin"],
                stdin=producer.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, shell=False,
            )
            producer.stdout.close()
            producer.stdin.write(f"{commit}\n".encode())
            producer.stdin.close()
            producer.wait(timeout=30)
            consumer.wait(timeout=30)
            if producer.returncode or consumer.returncode:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git object closure is incomplete")
        except (OSError, BrokenPipeError, subprocess.TimeoutExpired) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git object closure is unavailable") from error
        finally:
            for process in (producer, consumer):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()

    def _cleanup(self, repository: Path, checkout: Path, env: dict[str, str]) -> None:
        try:
            removed = self._git(["-C", str(repository), "worktree", "remove", "--force", str(checkout)], env=env)
        except _ReplayBlocked as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "temporary checkout cleanup failed") from error
        if removed.returncode:
            try:
                self._git(["-C", str(repository), "worktree", "prune", "--expire=now"], env=env)
            finally:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "temporary checkout cleanup failed")

    @contextmanager
    def _checkout(self, source: Path, commit: str) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="trusted-e2-") as name:
            root = Path(name); env = self._git_environment(root); isolated = self._isolated_repository(source, commit, root, env)
            checkout = root / "checkout"
            added = self._git(["-C", str(isolated), "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false",
                "worktree", "add", "--detach", str(checkout), commit], env=env)
            if added.returncode:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed checkout is unavailable")
            try:
                head = self._git(["-C", str(checkout), "rev-parse", "HEAD"], env=env)
                if head.returncode or head.stdout.decode("ascii", "strict").strip() != commit:
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "checkout HEAD differs from subject")
                yield checkout
            finally:
                self._cleanup(isolated, checkout, env)

    def _path(self, checkout: Path, relative: str, *, file: bool, may_absent: bool = False) -> Path:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path escapes checkout")
        candidate = checkout / relative; current = checkout
        for part in Path(relative).parts:
            if part not in ("", "."):
                current /= part
                if current.is_symlink():
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path contains a symbolic link")
        if may_absent and not candidate.exists():
            self._relative(checkout, candidate.parent.resolve(strict=True))
            return candidate
        try:
            resolved = candidate.resolve(strict=True); self._relative(checkout, resolved)
        except (OSError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path is unavailable") from error
        if (file and not resolved.is_file()) or (not file and not resolved.is_dir()):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path has wrong type")
        return resolved

    def _command_path(self, command: tuple[str, ...], cwd: Path, checkout: Path, *, trusted_host: bool = False) -> None:
        if "/" not in command[0]:
            return
        if Path(command[0]).is_absolute():
            if trusted_host:
                return
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "absolute commands are not allowed")
        try:
            self._path(checkout, self._relative(checkout, (cwd / command[0]).resolve(strict=True)), file=True)
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command is unavailable") from error

    def _run(self, command: tuple[str, ...], *, cwd: Path, checkout: Path, env: dict[str, str], timeout: int, limit: int, trusted_host: bool = False) -> _CommandResult:
        self._command_path(command, cwd, checkout, trusted_host=trusted_host)
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False, start_new_session=True)
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command is unavailable") from error
        assert process.stdout and process.stderr
        selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); selector.register(process.stderr, selectors.EVENT_READ)
        output = {process.stdout: bytearray(), process.stderr: bytearray()}; truncated = False; deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except OSError: process.kill()
                    process.wait()
                    raise _ReplayBlocked("TEST_REPLAY_TIMEOUT", "registered process group timed out")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    room = max(0, limit - len(output[key.fileobj]))
                    output[key.fileobj].extend(chunk[:room]); truncated |= len(chunk) > room
            try:
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                try: os.killpg(process.pid, signal.SIGKILL)
                except OSError: process.kill()
                process.wait()
                raise _ReplayBlocked("TEST_REPLAY_TIMEOUT", "registered process group timed out") from error
            return _CommandResult(code, bytes(output[process.stdout]), bytes(output[process.stderr]), truncated)
        finally:
            selector.close()

    def _host_tools(self, suite: E2SuiteDefinition) -> tuple[dict[str, str], dict[str, str]]:
        host_path = os.environ.get("PATH", ""); resolved: dict[str, str] = {}
        for tool in suite.tool_versions:
            if any(Path(command[0]).name == tool and "/" in command[0] for command in (*suite.setup_argv, *suite.argv)):
                continue
            path = shutil.which(tool, path=host_path)
            if path is None:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"declared tool is unavailable: {tool}")
            resolved[tool] = str(Path(path).resolve(strict=True))
        paths = list(dict.fromkeys(str(Path(path).parent) for path in resolved.values()))
        return resolved, {"PATH": os.pathsep.join([*paths, *os.defpath.split(os.pathsep)]), **suite.allowed_environment}

    @staticmethod
    def _resolve_host_command(command: tuple[str, ...], hosts: dict[str, str]) -> tuple[str, ...]:
        return (hosts.get(command[0], command[0]), *command[1:])

    def _lockfiles(self, suite: E2SuiteDefinition, checkout: Path) -> dict[str, str]:
        result = {}
        for path, expected in suite.lockfile_digests.items():
            actual = "sha256:" + hashlib.sha256(self._path(checkout, path, file=True).read_bytes()).hexdigest()
            result[path] = actual
            if actual != expected: raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "lockfile digest changed")
        return result

    def _tools(self, suite: E2SuiteDefinition, cwd: Path, checkout: Path, env: dict[str, str], hosts: dict[str, str]) -> dict[str, str]:
        observed = {}
        all_commands = (*suite.setup_argv, *suite.argv)
        for tool, expected in suite.tool_versions.items():
            command = hosts.get(tool) or next((item[0] for item in all_commands if Path(item[0]).name == tool), tool)
            result = self._run((command, "--version"), cwd=cwd, checkout=checkout, env=env, timeout=suite.timeout_seconds, limit=4096, trusted_host=command in hosts.values())
            version = _summary(result.stdout + result.stderr, truncated=result.truncated, patterns=self.redaction_patterns).strip()
            observed[tool] = version
            if result.returncode or version != expected: raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"tool version differs: {tool}")
        return observed

    def _verify_host_tools_before_setup(self, suite: E2SuiteDefinition, cwd: Path, checkout: Path, env: dict[str, str], hosts: dict[str, str]) -> None:
        for command in (*suite.setup_argv, *suite.argv):
            if "/" not in command[0] and command[0] not in hosts:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "bare command is not declared in toolVersions")
        for tool, path in hosts.items():
            result = self._run((path, "--version"), cwd=cwd, checkout=checkout, env=env, timeout=suite.timeout_seconds, limit=4096, trusted_host=True)
            version = _summary(result.stdout + result.stderr, truncated=result.truncated, patterns=self.redaction_patterns).strip()
            if result.returncode or version != suite.tool_versions[tool]:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"host tool version differs: {tool}")

    def _pytest_path(self, command: tuple[str, ...], cwd: Path, checkout: Path, *, prepare: bool) -> Path:
        if "--json-report" not in command: raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest reporter is disabled")
        report = ".report.json"
        for index, value in enumerate(command):
            if value.startswith("--json-report-file="): report = value.removeprefix("--json-report-file=")
            elif value == "--json-report-file":
                if index + 1 == len(command): raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report path missing")
                report = command[index + 1]
        path = self._path(checkout, self._relative(checkout, (cwd / report).resolve(strict=False)), file=True, may_absent=True)
        if prepare and path.exists():
            if path.is_symlink() or not path.is_file(): raise _ReplayBlocked("TEST_SELECTION_MISSING", "stale report path unsafe")
            path.unlink()
        return path

    def _parse(self, suite: E2SuiteDefinition, command: tuple[str, ...], result: _CommandResult, cwd: Path, checkout: Path) -> tuple[str, ...]:
        try:
            if suite.parser == "pytest-json-v1":
                path = self._pytest_path(command, cwd, checkout, prepare=False)
                report = json.loads(self._path(checkout, self._relative(checkout, path), file=True).read_text())
                rows = report.get("tests") if isinstance(report, dict) else None
                summary = report.get("summary") if isinstance(report, dict) else None
                if not isinstance(summary, dict) or any(not isinstance(summary.get(key), int) or isinstance(summary[key], bool) for key in ("total", "passed")):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report summary is invalid")
                if report.get("exitcode") != 0 or summary["total"] != len(rows) or summary["passed"] != len(rows):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report summary is inconsistent")
                outcomes = {row.get("nodeid"): row.get("outcome") for row in rows if isinstance(row, dict) and isinstance(row.get("nodeid"), str) and isinstance(row.get("outcome"), str)} if isinstance(rows, list) else {}
            else:
                if "--reporter=json" not in command and not any(command[i:i+2] == ("--reporter", "json") for i in range(len(command)-1)): raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest reporter is disabled")
                report = json.loads(result.stdout.decode("utf-8")); rows = report.get("testResults") if isinstance(report, dict) else None; outcomes = {}
                if isinstance(rows, list):
                    for row in rows:
                        assertions = row.get("assertionResults") if isinstance(row, dict) else None
                        if isinstance(assertions, list):
                            for item in assertions:
                                if isinstance(item, dict) and isinstance(item.get("fullName"), str) and isinstance(item.get("status"), str): outcomes[item["fullName"]] = item["status"]
            if not outcomes: raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report has no executed tests")
            expected = set(suite.expected_test_ids)
            if any(test_id not in expected for test_id in outcomes):
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report contains an unallowlisted test ID")
            if any(value != "passed" for value in outcomes.values()): raise _ReplayBlocked("TEST_REPLAY_FAILED", "machine report contains a failed test")
            return tuple(test_id for test_id in suite.expected_test_ids if test_id in outcomes)
        except _ReplayBlocked as error:
            if error.code == "TEST_REPLAY_FAILED":
                raise
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report is missing or unsafe") from error
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report is invalid") from error

    def _receipt_pointer(self, pointer: EvidencePointer, suite: E2SuiteDefinition, context: VerificationContext) -> None:
        try:
            raw = ContentAddressedStore(Path(context.trust_root)).read(pointer.artifact_ref)
            receipt = json.loads(raw)
            expected_fields = {"schema", "suiteId", "subjectRef", "parser", "inputDigest", "selectedTestIds", "invocations", "toolVersions", "lockfileDigests", "durationMilliseconds"}
            expected_digest = self._result(suite, pointer.subject_ref, context, valid=True, code=None, facts={}).input_digest
            if canonical_json_bytes(receipt) != raw or not isinstance(receipt, dict) or set(receipt) != expected_fields:
                raise ValueError
            if (receipt["schema"] != "trusted-e2-receipt/v1" or receipt["suiteId"] != suite.id or receipt["subjectRef"] != pointer.subject_ref
                    or receipt["parser"] != suite.parser or receipt["inputDigest"] != expected_digest
                    or not isinstance(receipt["selectedTestIds"], list) or tuple(receipt["selectedTestIds"]) != suite.expected_test_ids
                    or not isinstance(receipt["invocations"], list) or len(receipt["invocations"]) != len(suite.argv)
                    or receipt["toolVersions"] != dict(suite.tool_versions) or receipt["lockfileDigests"] != dict(suite.lockfile_digests)
                    or isinstance(receipt["durationMilliseconds"], bool) or not isinstance(receipt["durationMilliseconds"], int) or receipt["durationMilliseconds"] < 0):
                raise ValueError
            for invocation in receipt["invocations"]:
                if not isinstance(invocation, dict) or set(invocation) != {"exitCode", "selectedTestIds", "stdout", "stderr"} or invocation["exitCode"] != 0 or not isinstance(invocation["selectedTestIds"], list) or any(not isinstance(value, str) for value in invocation["selectedTestIds"]) or not isinstance(invocation["stdout"], str) or not isinstance(invocation["stderr"], str):
                    raise ValueError
        except (OSError, PolicyError, ValueError, TypeError, json.JSONDecodeError) as error:
            code = error.code if isinstance(error, PolicyError) else "POINTER_INVALID"
            raise _ReplayBlocked(code, "receipt is not a trusted E2 receipt") from error

    def run(self, suite: E2SuiteDefinition, subject_ref: str, context: VerificationContext) -> VerificationResult:
        match = _SUBJECT_REF.fullmatch(subject_ref); facts: dict[str, object] = {"suiteId": suite.id, "subjectRef": subject_ref}; started = time.monotonic()
        if match is None or subject_ref != context.subject_ref: return self._result(suite, subject_ref, context, valid=False, code="SUITE_NOT_ALLOWLISTED", facts=facts)
        try:
            if not all(any(test in command for command in suite.argv) for test in suite.expected_test_ids): raise _ReplayBlocked("TEST_SELECTION_MISSING", "expected IDs not selected by registry argv")
            with tempfile.TemporaryDirectory(prefix="trusted-e2-source-") as root: source = self._source_repository(Path(root))
            hosts, env = self._host_tools(suite)
            receipt: dict[str, object]
            with self._checkout(source, match.group(1)) as checkout:
                cwd = checkout if suite.working_directory == "." else self._path(checkout, suite.working_directory, file=False)
                locks = self._lockfiles(suite, checkout)
                self._verify_host_tools_before_setup(suite, cwd, checkout, env, hosts)
                for setup in suite.setup_argv:
                    result = self._run(self._resolve_host_command(setup, hosts), cwd=cwd, checkout=checkout, env=env, timeout=suite.timeout_seconds, limit=_MAX_CAPTURE, trusted_host=setup[0] in hosts)
                    if result.returncode: raise _ReplayBlocked("TEST_REPLAY_FAILED", "setup command failed")
                tools = self._tools(suite, cwd, checkout, env, hosts)
                selected: list[str] = []; invocations = []; replay_failed = False
                for command in suite.argv:
                    if suite.parser == "pytest-json-v1": self._pytest_path(command, cwd, checkout, prepare=True)
                    resolved = self._resolve_host_command(command, hosts)
                    result = self._run(resolved, cwd=cwd, checkout=checkout, env=env, timeout=suite.timeout_seconds, limit=max(1, _MAX_CAPTURE // len(suite.argv)), trusted_host=command[0] in hosts)
                    if result.returncode:
                        raise _ReplayBlocked("TEST_REPLAY_FAILED", "test command failed")
                    actual = self._parse(suite, command, result, cwd, checkout)
                    selected.extend(actual); invocations.append({"exitCode": result.returncode, "selectedTestIds": actual,
                        "stdout": _summary(result.stdout, truncated=result.truncated, patterns=self.redaction_patterns),
                        "stderr": _summary(result.stderr, truncated=result.truncated, patterns=self.redaction_patterns)})
                if not replay_failed and (set(selected) != set(suite.expected_test_ids) or len(selected) != len(set(selected))):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine reports do not exactly cover expected tests")
                receipt = {"schema": "trusted-e2-receipt/v1", "suiteId": suite.id, "subjectRef": subject_ref, "parser": suite.parser,
                    "inputDigest": self._result(suite, subject_ref, context, valid=True, code=None, facts={}).input_digest,
                    "selectedTestIds": tuple(selected), "invocations": invocations, "toolVersions": tools, "lockfileDigests": locks,
                    "durationMilliseconds": int((time.monotonic()-started)*1000)}
            receipt_ref = ContentAddressedStore(Path(context.trust_root)).put(canonical_json_bytes(receipt))
            facts.update(receipt | {"receiptRef": receipt_ref})
            return self._result(suite, subject_ref, context, valid=not replay_failed, code=None if not replay_failed else "TEST_REPLAY_FAILED", facts=facts)
        except (_ReplayBlocked, PolicyError) as error:
            facts["durationMilliseconds"] = int((time.monotonic()-started)*1000)
            return self._result(suite, subject_ref, context, valid=False, code=error.code if isinstance(error, _ReplayBlocked) else error.code, facts=facts)


class E2SuiteVerifier:
    level = "E2"
    def __init__(self, verifier_id: str, definition: E2SuiteDefinition, *, runner: SuiteRunner | None = None) -> None:
        self.verifier_id, self.definition, self.runner = verifier_id, definition, runner or SuiteRunner()
    def verify(self, pointer: EvidencePointer, profile: AcceptanceProfile, context: VerificationContext) -> VerificationResult:
        if pointer.verifier_id != self.verifier_id or pointer.profile_id != profile.profile_id or pointer.subject_ref != profile.subject_ref or pointer.subject_ref != context.subject_ref:
            return self.runner._result(self.definition, context.subject_ref, context, valid=False, code="SUITE_NOT_ALLOWLISTED", facts={"suiteId": self.definition.id})
        try: self.runner._receipt_pointer(pointer, self.definition, context)
        except _ReplayBlocked as error:
            return self.runner._result(self.definition, pointer.subject_ref, context, valid=False, code=error.code, facts={"suiteId": self.definition.id, "receiptRef": pointer.artifact_ref})
        return self.runner.run(self.definition, pointer.subject_ref, context)
