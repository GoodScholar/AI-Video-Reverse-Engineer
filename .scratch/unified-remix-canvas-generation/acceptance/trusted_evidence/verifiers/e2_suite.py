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
import stat
import subprocess
import tempfile
import time
from typing import Final, Iterator

from ..models import AcceptanceProfile, EvidencePointer, VerificationContext, VerificationResult
from ..redaction import DEFAULT_SECRET_PATTERNS, RedactionError, ensure_redacted, redact_output
from ..registry import E2SuiteDefinition, PolicyError
from ..storage import ContentAddressedStore, canonical_json_bytes


_SUBJECT_REF: Final = re.compile(r"^git://product@([0-9a-f]{40})$")
_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_CAPTURE: Final = 32 * 1024
_DANGEROUS_CONFIG: Final = re.compile(
    r"^(?:alias\.|core\.(?:attributesfile|editor|excludesfile|fsmonitor|hookspath|pager|sshcommand)|"
    r"credential\.|diff\..*\.(?:command|textconv)|filter\..*\.(?:clean|smudge|process)|"
    r"gpg\..*\.program|include(?:if)?\.|interactive\.difffilter|merge\..*\.driver|pager\.|"
    r"protocol\..*\.allow|remote\..*\.(?:uploadpack|receivepack|promisor|partialclonefilter)|"
    r"sequence\.editor|extensions\.partialclone)$",
    re.IGNORECASE,
)


class _ReplayBlocked(RuntimeError):
    def __init__(self, code: str, detail: str, observed_ids: tuple[str, ...] = ()) -> None:
        super().__init__(detail)
        self.code = code
        self.observed_ids = observed_ids


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    truncated: bool
    duration_milliseconds: int


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    digest: str

    def receipt(self) -> dict[str, object]:
        return {"sha256": self.digest, "size": self.size}


@dataclass(frozen=True, slots=True)
class _PinnedTool:
    name: str
    path: Path
    identity: _FileIdentity
    version: str


@dataclass(frozen=True, slots=True)
class _GitLayout:
    source: Path
    marker: Path
    git_dir: Path
    common_dir: Path
    objects: Path


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _summary(data: bytes, *, truncated: bool, patterns: tuple[re.Pattern[str], ...]) -> str:
    try:
        payload = data + (b"\n...[truncated]" if truncated else b"")
        return redact_output(payload, patterns).decode("utf-8")
    except RedactionError as error:
        raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "output cannot be safely redacted") from error


def _safe_expected_ids(ids: object, expected: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(ids, dict):
        values = ids.keys()
    elif isinstance(ids, (list, tuple, set, frozenset)):
        values = ids
    else:
        return ()
    observed = set(value for value in values if isinstance(value, str))
    return tuple(value for value in expected if value in observed)


class SuiteRunner:
    """Execute immutable command sequences; nothing supplied by a pointer is executable."""

    def __init__(self, *, redaction_patterns: tuple[re.Pattern[str], ...] = ()) -> None:
        self.redaction_patterns = DEFAULT_SECRET_PATTERNS + redaction_patterns

    def _result(
        self,
        suite: E2SuiteDefinition,
        ref: str,
        context: VerificationContext,
        *,
        valid: bool,
        code: str | None,
        facts: dict[str, object],
    ) -> VerificationResult:
        return VerificationResult(
            valid=valid,
            error_code=code,
            facts=facts,
            observed_at=context.observed_at,
            input_digest=_digest(
                {
                    "suiteId": suite.id,
                    "subjectRef": ref,
                    "setupArgv": suite.setup_argv,
                    "argv": suite.argv,
                    "expectedTestIds": suite.expected_test_ids,
                    "parser": suite.parser,
                    "workingDirectory": suite.working_directory,
                    "subjectRepository": suite.subject_repository,
                    "allowedEnvironment": dict(suite.allowed_environment),
                    "timeoutSeconds": suite.timeout_seconds,
                    "lockfileDigests": dict(suite.lockfile_digests),
                    "toolVersions": dict(suite.tool_versions),
                }
            ),
            zero_tolerance_violations=(),
        )

    def _environment(self, suite: E2SuiteDefinition) -> dict[str, str]:
        return dict(suite.allowed_environment)

    @staticmethod
    def _file_identity(path: Path) -> _FileIdentity:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
                    raise OSError("executable is not a regular executable file")
                digest = hashlib.sha256()
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
                after = os.fstat(handle.fileno())
                if (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise OSError("executable changed while being identified")
                return _FileIdentity(
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    "sha256:" + digest.hexdigest(),
                )
        except (OSError, TypeError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "tool identity is unavailable") from error

    def _assert_identity(self, tool: _PinnedTool) -> None:
        if self._file_identity(tool.path) != tool.identity:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"tool identity changed: {tool.name}")

    @staticmethod
    def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _execute(
        self,
        command: tuple[str, ...],
        *,
        tool: _PinnedTool,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
        limit: int,
    ) -> _CommandResult:
        if not command or Path(command[0]) != tool.path or not tool.path.is_absolute():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "command is not pinned to its executable")
        self._assert_identity(tool)
        process: subprocess.Popen[bytes] | None = None
        selector: selectors.BaseSelector | None = None
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
            if process.stdout is None or process.stderr is None:
                raise OSError("process pipes are unavailable")
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            output = {process.stdout: bytearray(), process.stderr: bytearray()}
            truncated = False
            deadline = started + timeout
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill_process_group(process)
                    raise _ReplayBlocked("TEST_REPLAY_TIMEOUT", "registered process group timed out")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    room = max(0, limit - len(output[key.fileobj]))
                    output[key.fileobj].extend(chunk[:room])
                    truncated |= len(chunk) > room
            try:
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                self._kill_process_group(process)
                raise _ReplayBlocked("TEST_REPLAY_TIMEOUT", "registered process group timed out") from error
            return _CommandResult(
                code,
                bytes(output[process.stdout]),
                bytes(output[process.stderr]),
                truncated,
                max(0, int((time.monotonic() - started) * 1000)),
            )
        except _ReplayBlocked:
            if process is not None:
                self._kill_process_group(process)
            raise
        except (OSError, TypeError, ValueError) as error:
            if process is not None:
                self._kill_process_group(process)
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered process is unavailable") from error
        finally:
            if selector is not None:
                try:
                    selector.close()
                except OSError:
                    pass
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass

    def _pin_host_tool(
        self,
        name: str,
        expected: str,
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> _PinnedTool:
        try:
            found = shutil.which(name, path=os.environ.get("PATH", ""))
            if found is None:
                raise OSError("tool not found")
            path = Path(found).resolve(strict=True)
        except (OSError, TypeError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"declared tool is unavailable: {name}") from error
        initial = _PinnedTool(name, path, self._file_identity(path), expected)
        probe_env = {**env, "PATH": os.environ.get("PATH", env.get("PATH", ""))}
        result = self._execute(
            (str(path), "--version"), tool=initial, cwd=cwd, env=probe_env, timeout=timeout, limit=4096
        )
        self._assert_identity(initial)
        version = _summary(result.stdout + result.stderr, truncated=result.truncated, patterns=self.redaction_patterns).strip()
        if result.returncode or version != expected:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"tool version differs: {name}")
        return initial

    def _pin_path_tool(
        self,
        name: str,
        path: Path,
        expected: str,
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> _PinnedTool:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, TypeError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"declared tool is unavailable: {name}") from error
        initial = _PinnedTool(name, resolved, self._file_identity(resolved), expected)
        result = self._execute((str(resolved), "--version"), tool=initial, cwd=cwd, env=env, timeout=timeout, limit=4096)
        self._assert_identity(initial)
        version = _summary(result.stdout + result.stderr, truncated=result.truncated, patterns=self.redaction_patterns).strip()
        if result.returncode or version != expected:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"tool version differs: {name}")
        return initial

    @staticmethod
    def _no_symlink_components(path: Path) -> None:
        absolute = Path(os.path.abspath(path))
        current = Path(absolute.anchor)
        try:
            for part in absolute.parts[1:]:
                current /= part
                if current.is_symlink():
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git path contains a symbolic link")
                os.lstat(current)
        except _ReplayBlocked:
            raise
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git metadata path is unavailable") from error

    @staticmethod
    def _read_git_pointer(path: Path, prefix: str) -> str:
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError("unsafe pointer")
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git pointer is unavailable") from error
        lines = text.splitlines()
        if len(lines) != 1 or not lines[0].startswith(prefix):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git pointer is malformed")
        value = lines[0][len(prefix):].strip()
        if not value or "\x00" in value:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git pointer is malformed")
        return value

    def _source_layout(self) -> _GitLayout:
        try:
            start = Path.cwd().absolute()
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product repository is unavailable") from error
        marker: Path | None = None
        source: Path | None = None
        for candidate in (start, *start.parents):
            possible = candidate / ".git"
            if os.path.lexists(possible):
                marker, source = possible, candidate
                break
        if marker is None or source is None:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "product repository is unavailable")
        if marker.is_symlink():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "raw .git may not be a symbolic link")
        self._no_symlink_components(source)
        if marker.is_dir():
            git_dir = marker
        elif marker.is_file():
            raw = Path(self._read_git_pointer(marker, "gitdir:"))
            git_dir = raw if raw.is_absolute() else marker.parent / raw
            git_dir = Path(os.path.abspath(git_dir))
        else:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "raw .git has an unsafe type")
        self._no_symlink_components(git_dir)
        common_pointer = git_dir / "commondir"
        if os.path.lexists(common_pointer):
            raw_common = Path(self._read_git_pointer(common_pointer, ""))
            common_dir = raw_common if raw_common.is_absolute() else git_dir / raw_common
            common_dir = Path(os.path.abspath(common_dir))
        else:
            common_dir = git_dir
        self._no_symlink_components(common_dir)
        objects = common_dir / "objects"
        self._no_symlink_components(objects)
        return _GitLayout(source, marker, git_dir, common_dir, objects)

    def _git_environment(self, root: Path, git: _PinnedTool) -> dict[str, str]:
        try:
            for item in ("home", "xdg", "empty-template"):
                (root / item).mkdir(exist_ok=True)
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git isolation directory is unavailable") from error
        return {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "xdg"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C",
            "PATH": str(git.path.parent),
        }

    def _git(
        self,
        argv: list[str],
        *,
        git: _PinnedTool,
        env: dict[str, str],
        input_data: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        self._assert_identity(git)
        command = [str(git.path), "--no-replace-objects", *argv]
        try:
            result = subprocess.run(
                command,
                input=input_data,
                check=False,
                capture_output=True,
                env=env,
                shell=False,
                timeout=30,
            )
            self._assert_identity(git)
            return result
        except _ReplayBlocked:
            raise
        except (OSError, TypeError, ValueError, subprocess.TimeoutExpired) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git source is unavailable") from error

    def _config_names(self, path: Path, *, git: _PinnedTool, env: dict[str, str]) -> tuple[str, ...]:
        if not os.path.lexists(path):
            return ()
        if path.is_symlink() or not path.is_file():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git configuration has an unsafe type")
        result = self._git(
            ["config", "--file", str(path), "--no-includes", "--name-only", "--list"],
            git=git,
            env=env,
        )
        if result.returncode:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git local configuration is unavailable")
        try:
            return tuple(result.stdout.decode("utf-8").splitlines())
        except UnicodeDecodeError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git local configuration is invalid") from error

    def _source_safety(
        self,
        layout: _GitLayout,
        commit: str,
        *,
        git: _PinnedTool,
        env: dict[str, str],
    ) -> None:
        forbidden = (
            layout.objects / "info" / "alternates",
            layout.objects / "info" / "http-alternates",
            layout.common_dir / "info" / "grafts",
            layout.git_dir / "info" / "grafts",
            layout.git_dir / "shallow",
            layout.common_dir / "shallow",
            layout.git_dir / "refs" / "replace",
            layout.common_dir / "refs" / "replace",
        )
        if any(os.path.lexists(path) for path in forbidden):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git metadata indirection is not trusted")
        for packed_refs in {layout.common_dir / "packed-refs", layout.git_dir / "packed-refs"}:
            if not os.path.lexists(packed_refs):
                continue
            try:
                if packed_refs.is_symlink() or not packed_refs.is_file():
                    raise OSError("unsafe packed refs")
                lines = packed_refs.read_text(encoding="ascii").splitlines()
            except (OSError, UnicodeDecodeError) as error:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git packed refs are unavailable") from error
            if any(
                len(parts := line.split(" ", 1)) == 2 and parts[1].startswith("refs/replace/")
                for line in lines
                if line and not line.startswith(("#", "^"))
            ):
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git replace refs are not trusted")
        names = (*self._config_names(layout.common_dir / "config", git=git, env=env),
                 *self._config_names(layout.git_dir / "config.worktree", git=git, env=env))
        if any(_DANGEROUS_CONFIG.fullmatch(name) for name in names):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git local configuration is not trusted")
        for argument, expected in (("--absolute-git-dir", layout.git_dir), ("--git-common-dir", layout.common_dir)):
            result = self._git(["-C", str(layout.source), "rev-parse", argument], git=git, env=env)
            if result.returncode:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git metadata is unavailable")
            try:
                raw = Path(result.stdout.decode("utf-8").strip())
                actual = raw if raw.is_absolute() else layout.source / raw
                if Path(os.path.abspath(actual)) != Path(os.path.abspath(expected)):
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git metadata path changed during resolution")
            except UnicodeDecodeError as error:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "Git metadata is unavailable") from error
        kind = self._git(["-C", str(layout.source), "cat-file", "-t", commit], git=git, env=env)
        if kind.returncode or kind.stdout.strip() != b"commit":
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "subject is not an exact commit object")
        connectivity = self._git(
            ["-C", str(layout.source), "fsck", "--connectivity-only", "--no-dangling", commit],
            git=git,
            env=env,
        )
        if connectivity.returncode:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "source Git object closure is incomplete")

    def _stream_object_closure(
        self,
        source: Path,
        isolated: Path,
        commit: str,
        *,
        git: _PinnedTool,
        env: dict[str, str],
    ) -> None:
        producer: subprocess.Popen[bytes] | None = None
        consumer: subprocess.Popen[bytes] | None = None
        try:
            self._assert_identity(git)
            producer = subprocess.Popen(
                [str(git.path), "--no-replace-objects", "-C", str(source), "pack-objects", "--stdout", "--revs"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                shell=False,
                start_new_session=True,
            )
            if producer.stdin is None or producer.stdout is None:
                raise OSError("Git pack pipes unavailable")
            self._assert_identity(git)
            consumer = subprocess.Popen(
                [str(git.path), "--no-replace-objects", "-C", str(isolated), "index-pack", "--stdin"],
                stdin=producer.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                shell=False,
                start_new_session=True,
            )
            producer.stdout.close()
            producer.stdin.write(f"{commit}\n".encode())
            producer.stdin.close()
            producer.wait(timeout=30)
            consumer.wait(timeout=30)
            if producer.returncode or consumer.returncode:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git object closure is incomplete")
        except _ReplayBlocked:
            raise
        except (OSError, BrokenPipeError, TypeError, ValueError, subprocess.TimeoutExpired) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git object closure is unavailable") from error
        finally:
            for process in (producer, consumer):
                if process is not None:
                    self._kill_process_group(process)

    def _isolated_repository(
        self,
        layout: _GitLayout,
        commit: str,
        root: Path,
        *,
        git: _PinnedTool,
        env: dict[str, str],
    ) -> Path:
        self._source_safety(layout, commit, git=git, env=env)
        isolated = root / "isolated.git"
        initialized = self._git(
            ["init", "--bare", f"--template={root / 'empty-template'}", str(isolated)], git=git, env=env
        )
        if initialized.returncode:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "cannot initialize isolated Git repository")
        self._stream_object_closure(layout.source, isolated, commit, git=git, env=env)
        if self._git(["-C", str(isolated), "fsck", "--connectivity-only", commit], git=git, env=env).returncode:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed Git object closure is incomplete")
        kind = self._git(["-C", str(isolated), "cat-file", "-t", commit], git=git, env=env)
        if kind.returncode or kind.stdout.strip() != b"commit":
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "isolated closure lacks the fixed commit")
        return isolated

    def _cleanup(self, repository: Path, checkout: Path, *, git: _PinnedTool, env: dict[str, str]) -> None:
        removed = self._git(
            ["-C", str(repository), "worktree", "remove", "--force", str(checkout)], git=git, env=env
        )
        if removed.returncode:
            try:
                self._git(["-C", str(repository), "worktree", "prune", "--expire=now"], git=git, env=env)
            finally:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "temporary checkout cleanup failed")

    @contextmanager
    def _checkout(self, layout: _GitLayout, commit: str, *, git: _PinnedTool) -> Iterator[Path]:
        try:
            with tempfile.TemporaryDirectory(prefix="trusted-e2-") as name:
                root = Path(name)
                env = self._git_environment(root, git)
                isolated = self._isolated_repository(layout, commit, root, git=git, env=env)
                checkout = root / "checkout"
                added = self._git(
                    ["-C", str(isolated), "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false",
                     "worktree", "add", "--detach", str(checkout), commit],
                    git=git,
                    env=env,
                )
                if added.returncode:
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "fixed checkout is unavailable")
                try:
                    head = self._git(["-C", str(checkout), "rev-parse", "HEAD"], git=git, env=env)
                    if head.returncode or head.stdout.decode("ascii", "strict").strip() != commit:
                        raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "checkout HEAD differs from subject")
                    yield checkout
                finally:
                    self._cleanup(isolated, checkout, git=git, env=env)
        except _ReplayBlocked:
            raise
        except (OSError, TypeError, ValueError, UnicodeDecodeError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "temporary checkout is unavailable") from error

    @staticmethod
    def _inside_checkout(checkout: Path, candidate: Path) -> Path:
        try:
            root = Path(os.path.realpath(checkout))
            absolute = Path(os.path.abspath(candidate))
            lexical = Path(os.path.realpath(absolute.parent)) / absolute.name
            lexical.relative_to(root)
            return lexical
        except (OSError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path escapes checkout") from error

    def _path(self, checkout: Path, relative: str, *, file: bool, may_absent: bool = False) -> Path:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path escapes checkout")
        candidate = self._inside_checkout(checkout, checkout / relative)
        current = checkout
        try:
            for part in Path(relative).parts:
                if part not in ("", "."):
                    current /= part
                    if current.is_symlink():
                        raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path contains a symbolic link")
            if may_absent and not candidate.exists():
                candidate.parent.resolve(strict=True)
                return candidate
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(checkout.resolve(strict=True))
        except _ReplayBlocked:
            raise
        except (OSError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path is unavailable") from error
        if (file and not resolved.is_file()) or (not file and not resolved.is_dir()):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "path has wrong type")
        return resolved

    def _relative_executable(self, raw: str, cwd: Path, checkout: Path) -> Path:
        candidate = self._inside_checkout(checkout, cwd / raw)
        root = Path(os.path.realpath(checkout))
        current = root
        relative = candidate.relative_to(root)
        try:
            for part in relative.parts[:-1]:
                current /= part
                if current.is_symlink():
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "command path contains a symbolic link")
            os.lstat(candidate)
            return candidate
        except _ReplayBlocked:
            raise
        except OSError as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command is unavailable") from error

    def _resolve_command(
        self,
        command: tuple[str, ...],
        *,
        suite: E2SuiteDefinition,
        cwd: Path,
        checkout: Path,
        env: dict[str, str],
        pins: dict[str, _PinnedTool],
    ) -> tuple[tuple[str, ...], _PinnedTool]:
        raw = command[0]
        name = Path(raw).name
        if "/" not in raw:
            if name not in suite.tool_versions:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "command executable is not declared in toolVersions")
            expected = suite.tool_versions[name]
            pin = pins.get(name)
            if pin is None:
                pin = self._pin_host_tool(name, expected, cwd=cwd, env=env, timeout=suite.timeout_seconds)
                pins[name] = pin
            return (str(pin.path), *command[1:]), pin
        if Path(raw).is_absolute():
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "absolute commands are not allowed")
        candidate = self._relative_executable(raw, cwd, checkout)
        if candidate.is_symlink():
            try:
                target = candidate.resolve(strict=True)
            except OSError as error:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command symlink is unavailable") from error
            pin = next((item for item in pins.values() if item.path == target), None)
            if pin is None:
                for declared, expected in suite.tool_versions.items():
                    if declared in pins:
                        continue
                    try:
                        found = shutil.which(declared, path=os.environ.get("PATH", ""))
                        matches = found is not None and Path(found).resolve(strict=True) == target
                    except (OSError, TypeError, ValueError):
                        matches = False
                    if matches:
                        pin = self._pin_host_tool(
                            declared, expected, cwd=cwd, env=env, timeout=suite.timeout_seconds
                        )
                        pins[declared] = pin
                        break
            if pin is None or target != pin.path:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "registered command symlink target is not trusted")
            return (str(pin.path), *command[1:]), pin
        if name not in suite.tool_versions:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "command executable is not declared in toolVersions")
        expected = suite.tool_versions[name]
        pin = pins.get(name)
        if pin is None:
            pin = self._pin_path_tool(
                name, candidate, expected, cwd=cwd, env=env, timeout=suite.timeout_seconds
            )
            pins[name] = pin
        elif pin.path != candidate.resolve(strict=True):
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "tool name resolves to multiple executables")
        return (str(pin.path), *command[1:]), pin

    def _lockfiles(self, suite: E2SuiteDefinition, checkout: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        try:
            for path, expected in suite.lockfile_digests.items():
                actual = "sha256:" + hashlib.sha256(self._path(checkout, path, file=True).read_bytes()).hexdigest()
                result[path] = actual
                if actual != expected:
                    raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "lockfile digest changed")
            return result
        except _ReplayBlocked:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "lockfile is unavailable") from error

    def _pytest_path(self, command: tuple[str, ...], cwd: Path, checkout: Path, *, prepare: bool) -> Path:
        if "--json-report" not in command:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest reporter is disabled")
        report = ".report.json"
        for index, value in enumerate(command):
            if value.startswith("--json-report-file="):
                report = value.removeprefix("--json-report-file=")
            elif value == "--json-report-file":
                if index + 1 == len(command):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report path missing")
                report = command[index + 1]
        try:
            root = Path(os.path.realpath(checkout))
            relative = str(self._inside_checkout(checkout, (cwd / report)).relative_to(root))
            path = self._path(checkout, relative, file=True, may_absent=True)
            if prepare and path.exists():
                if path.is_symlink() or not path.is_file():
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "stale report path unsafe")
                path.unlink()
            return path
        except _ReplayBlocked:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report path is unsafe") from error

    @staticmethod
    def _integer(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _pytest_outcomes(
        self,
        report: object,
        result: _CommandResult,
        expected: tuple[str, ...],
    ) -> dict[str, str]:
        if not isinstance(report, dict) or not isinstance(report.get("tests"), list) or not isinstance(report.get("summary"), dict):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report top-level schema is invalid")
        rows = report["tests"]
        summary = report["summary"]
        exitcode = report.get("exitcode")
        if not isinstance(exitcode, int) or isinstance(exitcode, bool) or exitcode != result.returncode:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest exit code is inconsistent")
        if not all(self._integer(summary.get(key)) for key in ("total", "passed")):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report summary is invalid")
        outcomes: dict[str, str] = {}
        allowed = {"passed", "failed", "skipped", "error", "xfailed", "xpassed"}
        for row in rows:
            if not isinstance(row, dict):
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report row is invalid")
            nodeid, outcome = row.get("nodeid"), row.get("outcome")
            if not isinstance(nodeid, str) or not nodeid or not isinstance(outcome, str) or outcome not in allowed:
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report row is invalid")
            if nodeid in outcomes:
                raise _ReplayBlocked(
                    "TEST_SELECTION_MISSING", "pytest report contains duplicate rows", _safe_expected_ids(outcomes, expected)
                )
            outcomes[nodeid] = outcome
        observed = _safe_expected_ids(outcomes, expected)
        counts = {name: sum(value == name for value in outcomes.values()) for name in allowed}
        if summary["total"] != len(rows) or summary["passed"] != counts["passed"]:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report summary is inconsistent", observed)
        for name, count in counts.items():
            if name in summary and (not self._integer(summary[name]) or summary[name] != count):
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "pytest report outcome count is inconsistent", observed)
        if any(test_id not in expected for test_id in outcomes):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report contains an unallowlisted test ID", observed)
        if result.returncode or exitcode or any(value != "passed" for value in outcomes.values()):
            raise _ReplayBlocked("TEST_REPLAY_FAILED", "pytest report contains a failed test", observed)
        return outcomes

    def _vitest_file(self, raw: str, cwd: Path, checkout: Path) -> str:
        try:
            path = Path(raw)
            candidate = path if path.is_absolute() else cwd / path
            lexical = self._inside_checkout(checkout, candidate)
            return lexical.relative_to(Path(os.path.realpath(checkout))).as_posix()
        except (OSError, TypeError, ValueError) as error:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest file identity is invalid") from error

    def _vitest_outcomes(
        self,
        report: object,
        result: _CommandResult,
        expected: tuple[str, ...],
        cwd: Path,
        checkout: Path,
    ) -> dict[str, str]:
        count_keys = (
            "numTotalTestSuites", "numPassedTestSuites", "numFailedTestSuites", "numPendingTestSuites",
            "numTotalTests", "numPassedTests", "numFailedTests", "numPendingTests", "numTodoTests",
        )
        if (
            not isinstance(report, dict)
            or not isinstance(report.get("success"), bool)
            or not isinstance(report.get("testResults"), list)
            or not all(self._integer(report.get(key)) for key in count_keys)
        ):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest report top-level schema is invalid")
        outcomes: dict[str, str] = {}
        suite_statuses: list[str] = []
        for row in report["testResults"]:
            if not isinstance(row, dict):
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest report row is invalid")
            name, status_value, assertions = row.get("name"), row.get("status"), row.get("assertionResults")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(status_value, str)
                or status_value not in {"passed", "failed", "pending"}
                or not isinstance(assertions, list)
            ):
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest report row is invalid")
            file_id = self._vitest_file(name, cwd, checkout)
            suite_statuses.append(status_value)
            for assertion in assertions:
                if not isinstance(assertion, dict):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest assertion is invalid")
                full_name, status_value = assertion.get("fullName"), assertion.get("status")
                if (
                    not isinstance(full_name, str)
                    or not full_name
                    or not isinstance(status_value, str)
                    or status_value not in {"passed", "failed", "pending", "todo"}
                ):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest assertion is invalid")
                test_id = f"{file_id}::{full_name}"
                if test_id in outcomes:
                    raise _ReplayBlocked(
                        "TEST_SELECTION_MISSING", "Vitest report contains duplicate assertions", _safe_expected_ids(outcomes, expected)
                    )
                outcomes[test_id] = status_value
        observed = _safe_expected_ids(outcomes, expected)
        test_counts = {
            "numTotalTests": len(outcomes),
            "numPassedTests": sum(value == "passed" for value in outcomes.values()),
            "numFailedTests": sum(value == "failed" for value in outcomes.values()),
            "numPendingTests": sum(value == "pending" for value in outcomes.values()),
            "numTodoTests": sum(value == "todo" for value in outcomes.values()),
        }
        suite_counts = {
            "numTotalTestSuites": len(suite_statuses),
            "numPassedTestSuites": sum(value == "passed" for value in suite_statuses),
            "numFailedTestSuites": sum(value == "failed" for value in suite_statuses),
            "numPendingTestSuites": sum(value == "pending" for value in suite_statuses),
        }
        if any(report[key] != value for key, value in {**test_counts, **suite_counts}.items()):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest report counts are inconsistent", observed)
        expected_success = result.returncode == 0 and not test_counts["numFailedTests"] and bool(outcomes)
        if report["success"] != expected_success:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest success flag is inconsistent", observed)
        if any(test_id not in expected for test_id in outcomes):
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report contains an unallowlisted test ID", observed)
        if result.returncode or any(value != "passed" for value in outcomes.values()):
            raise _ReplayBlocked("TEST_REPLAY_FAILED", "Vitest report contains a failed test", observed)
        return outcomes

    def _parse(
        self,
        suite: E2SuiteDefinition,
        command: tuple[str, ...],
        result: _CommandResult,
        cwd: Path,
        checkout: Path,
    ) -> tuple[str, ...]:
        try:
            if suite.parser == "pytest-json-v1":
                path = self._pytest_path(command, cwd, checkout, prepare=False)
                report = json.loads(path.read_text(encoding="utf-8"))
                outcomes = self._pytest_outcomes(report, result, suite.expected_test_ids)
            else:
                if "--reporter=json" not in command and not any(
                    command[index:index + 2] == ("--reporter", "json") for index in range(len(command) - 1)
                ):
                    raise _ReplayBlocked("TEST_SELECTION_MISSING", "Vitest reporter is disabled")
                report = json.loads(result.stdout.decode("utf-8"))
                outcomes = self._vitest_outcomes(
                    report, result, suite.expected_test_ids, cwd, checkout
                )
            if not outcomes:
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report has no executed tests")
            return tuple(test_id for test_id in suite.expected_test_ids if test_id in outcomes)
        except _ReplayBlocked:
            raise
        except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _ReplayBlocked("TEST_SELECTION_MISSING", "machine report is invalid") from error

    def _receipt_pointer(
        self, pointer: EvidencePointer, suite: E2SuiteDefinition, context: VerificationContext
    ) -> None:
        try:
            raw = ContentAddressedStore(Path(context.trust_root)).read(pointer.artifact_ref)
            receipt = json.loads(raw)
            expected_fields = {
                "schema", "suiteId", "subjectRef", "parser", "inputDigest", "selectedTestIds", "invocations",
                "toolVersions", "toolIdentities", "lockfileDigests", "durationMilliseconds",
            }
            expected_digest = self._result(
                suite, pointer.subject_ref, context, valid=True, code=None, facts={}
            ).input_digest
            if canonical_json_bytes(receipt) != raw or not isinstance(receipt, dict) or set(receipt) != expected_fields:
                raise ValueError
            if (
                receipt["schema"] != "trusted-e2-receipt/v1"
                or receipt["suiteId"] != suite.id
                or receipt["subjectRef"] != pointer.subject_ref
                or receipt["parser"] != suite.parser
                or receipt["inputDigest"] != expected_digest
                or not isinstance(receipt["selectedTestIds"], list)
                or tuple(receipt["selectedTestIds"]) != suite.expected_test_ids
                or not isinstance(receipt["invocations"], list)
                or len(receipt["invocations"]) != len(suite.argv)
                or receipt["toolVersions"] != dict(suite.tool_versions)
                or receipt["lockfileDigests"] != dict(suite.lockfile_digests)
                or not isinstance(receipt["toolIdentities"], dict)
                or set(receipt["toolIdentities"]) != set(suite.tool_versions)
                or not self._integer(receipt["durationMilliseconds"])
            ):
                raise ValueError
            for identity in receipt["toolIdentities"].values():
                if (
                    not isinstance(identity, dict)
                    or set(identity) != {"sha256", "size"}
                    or not isinstance(identity["sha256"], str)
                    or _DIGEST.fullmatch(identity["sha256"]) is None
                    or not self._integer(identity["size"])
                ):
                    raise ValueError
            selected_union: list[str] = []
            expected_set = set(suite.expected_test_ids)
            invocation_fields = {
                "argv", "durationMilliseconds", "exitCode", "selectedTestIds", "stdout", "stderr"
            }
            for index, invocation in enumerate(receipt["invocations"]):
                if not isinstance(invocation, dict) or set(invocation) != invocation_fields:
                    raise ValueError
                selected = invocation["selectedTestIds"]
                if (
                    invocation["argv"] != list(suite.argv[index])
                    or invocation["exitCode"] != 0
                    or isinstance(invocation["exitCode"], bool)
                    or not self._integer(invocation["durationMilliseconds"])
                    or not isinstance(selected, list)
                    or any(not isinstance(value, str) for value in selected)
                    or len(selected) != len(set(selected))
                    or any(value not in expected_set for value in selected)
                    or not isinstance(invocation["stdout"], str)
                    or not isinstance(invocation["stderr"], str)
                ):
                    raise ValueError
                ensure_redacted(invocation["stdout"], self.redaction_patterns)
                ensure_redacted(invocation["stderr"], self.redaction_patterns)
                selected_union.extend(selected)
            if len(selected_union) != len(set(selected_union)) or set(selected_union) != expected_set:
                raise ValueError
        except (OSError, PolicyError, RedactionError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            code = error.code if isinstance(error, PolicyError) else "POINTER_INVALID"
            raise _ReplayBlocked(code, "receipt is not a trusted E2 receipt") from error

    def run(
        self, suite: E2SuiteDefinition, subject_ref: str, context: VerificationContext
    ) -> VerificationResult:
        facts: dict[str, object] = {"suiteId": suite.id, "subjectRef": subject_ref}
        started = time.monotonic()
        match = _SUBJECT_REF.fullmatch(subject_ref)
        if match is None or subject_ref != context.subject_ref:
            return self._result(
                suite, subject_ref, context, valid=False, code="SUITE_NOT_ALLOWLISTED", facts=facts
            )
        try:
            if "git" not in suite.tool_versions:
                raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", "internal Git is not declared in toolVersions")
            if not all(any(test in command for command in suite.argv) for test in suite.expected_test_ids):
                raise _ReplayBlocked("TEST_SELECTION_MISSING", "expected IDs not selected by registry argv")
            base_env = {"LC_ALL": "C", **suite.allowed_environment}
            git = self._pin_host_tool(
                "git",
                suite.tool_versions["git"],
                cwd=Path.cwd(),
                env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
                timeout=suite.timeout_seconds,
            )
            layout = self._source_layout()
            pins: dict[str, _PinnedTool] = {"git": git}
            with self._checkout(layout, match.group(1), git=git) as checkout:
                cwd = checkout if suite.working_directory == "." else self._path(
                    checkout, suite.working_directory, file=False
                )
                locks = self._lockfiles(suite, checkout)
                env = {
                    "PATH": os.pathsep.join(
                        dict.fromkeys([str(git.path.parent), *os.defpath.split(os.pathsep)])
                    ),
                    **base_env,
                }
                setup_commands: list[tuple[tuple[str, ...], _PinnedTool]] = []
                for command in suite.setup_argv:
                    setup_commands.append(
                        self._resolve_command(
                            command, suite=suite, cwd=cwd, checkout=checkout, env=env, pins=pins
                        )
                    )
                for command, tool in setup_commands:
                    result = self._execute(
                        command,
                        tool=tool,
                        cwd=cwd,
                        env=env,
                        timeout=suite.timeout_seconds,
                        limit=_MAX_CAPTURE,
                    )
                    if result.returncode:
                        raise _ReplayBlocked("TEST_REPLAY_FAILED", "setup command failed")
                resolved_tests = [
                    self._resolve_command(
                        command, suite=suite, cwd=cwd, checkout=checkout, env=env, pins=pins
                    )
                    for command in suite.argv
                ]
                used_names = {"git", *(Path(command[0]).name for command in (*suite.setup_argv, *suite.argv))}
                for name, expected in suite.tool_versions.items():
                    if name not in pins:
                        if name in used_names:
                            raise _ReplayBlocked("SOURCE_REF_UNREACHABLE", f"declared tool is unavailable: {name}")
                        pins[name] = self._pin_host_tool(
                            name, expected, cwd=cwd, env=env, timeout=suite.timeout_seconds
                        )
                env["PATH"] = os.pathsep.join(
                    dict.fromkeys([*(str(pin.path.parent) for pin in pins.values()), *os.defpath.split(os.pathsep)])
                )
                selected: list[str] = []
                invocations: list[dict[str, object]] = []
                for policy_command, (resolved, tool) in zip(suite.argv, resolved_tests, strict=True):
                    if suite.parser == "pytest-json-v1":
                        self._pytest_path(policy_command, cwd, checkout, prepare=True)
                    result = self._execute(
                        resolved,
                        tool=tool,
                        cwd=cwd,
                        env=env,
                        timeout=suite.timeout_seconds,
                        limit=max(1, _MAX_CAPTURE // len(suite.argv)),
                    )
                    try:
                        actual = self._parse(suite, policy_command, result, cwd, checkout)
                    except _ReplayBlocked as error:
                        if error.observed_ids:
                            facts["selectedTestIds"] = error.observed_ids
                        if result.returncode and error.code != "TEST_REPLAY_FAILED":
                            raise _ReplayBlocked(
                                "TEST_REPLAY_FAILED", "test command failed", error.observed_ids
                            ) from error
                        raise
                    selected.extend(actual)
                    invocations.append(
                        {
                            "argv": policy_command,
                            "durationMilliseconds": result.duration_milliseconds,
                            "exitCode": result.returncode,
                            "selectedTestIds": actual,
                            "stdout": _summary(
                                result.stdout, truncated=result.truncated, patterns=self.redaction_patterns
                            ),
                            "stderr": _summary(
                                result.stderr, truncated=result.truncated, patterns=self.redaction_patterns
                            ),
                        }
                    )
                if set(selected) != set(suite.expected_test_ids) or len(selected) != len(set(selected)):
                    raise _ReplayBlocked(
                        "TEST_SELECTION_MISSING",
                        "machine reports do not exactly cover expected tests",
                        _safe_expected_ids(selected, suite.expected_test_ids),
                    )
                tool_identities = {name: pins[name].identity.receipt() for name in suite.tool_versions}
                receipt: dict[str, object] = {
                    "schema": "trusted-e2-receipt/v1",
                    "suiteId": suite.id,
                    "subjectRef": subject_ref,
                    "parser": suite.parser,
                    "inputDigest": self._result(
                        suite, subject_ref, context, valid=True, code=None, facts={}
                    ).input_digest,
                    "selectedTestIds": tuple(selected),
                    "invocations": invocations,
                    "toolVersions": dict(suite.tool_versions),
                    "toolIdentities": tool_identities,
                    "lockfileDigests": locks,
                    "durationMilliseconds": max(0, int((time.monotonic() - started) * 1000)),
                }
            receipt_ref = ContentAddressedStore(Path(context.trust_root)).put(canonical_json_bytes(receipt))
            facts.update(receipt | {"receiptRef": receipt_ref})
            return self._result(suite, subject_ref, context, valid=True, code=None, facts=facts)
        except (_ReplayBlocked, PolicyError) as error:
            if isinstance(error, _ReplayBlocked) and error.observed_ids:
                facts["selectedTestIds"] = error.observed_ids
            facts["durationMilliseconds"] = max(0, int((time.monotonic() - started) * 1000))
            return self._result(suite, subject_ref, context, valid=False, code=error.code, facts=facts)
        except (OSError, TypeError, ValueError, UnicodeError, subprocess.SubprocessError):
            facts["durationMilliseconds"] = max(0, int((time.monotonic() - started) * 1000))
            return self._result(
                suite, subject_ref, context, valid=False, code="SOURCE_REF_UNREACHABLE", facts=facts
            )


class E2SuiteVerifier:
    level = "E2"

    def __init__(
        self, verifier_id: str, definition: E2SuiteDefinition, *, runner: SuiteRunner | None = None
    ) -> None:
        self.verifier_id = verifier_id
        self.definition = definition
        self.runner = runner or SuiteRunner()

    def verify(
        self, pointer: EvidencePointer, profile: AcceptanceProfile, context: VerificationContext
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
                code="SUITE_NOT_ALLOWLISTED",
                facts={"suiteId": self.definition.id},
            )
        try:
            self.runner._receipt_pointer(pointer, self.definition, context)
        except _ReplayBlocked as error:
            return self.runner._result(
                self.definition,
                pointer.subject_ref,
                context,
                valid=False,
                code=error.code,
                facts={"suiteId": self.definition.id, "receiptRef": pointer.artifact_ref},
            )
        return self.runner.run(self.definition, pointer.subject_ref, context)
