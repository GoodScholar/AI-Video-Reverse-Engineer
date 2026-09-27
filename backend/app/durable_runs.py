"""Shared lifecycle contract for durable local and external runs.

Business modules keep ownership of their inputs and decisions.  This module
only owns execution states, immutable input snapshots, safe transitions and
the local executor seam.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Literal, Mapping, Optional, cast


RunExecution = Literal["local", "external"]
RunStatus = Literal[
    "submitting", "queued", "running", "completed", "failed", "cancelled", "unknown"
]

SHARED_RUN_STATUSES = frozenset({"queued", "running", "completed", "failed", "cancelled"})
LOCAL_RUN_STATUSES = SHARED_RUN_STATUSES
EXTERNAL_RUN_STATUSES = SHARED_RUN_STATUSES | {"submitting", "unknown"}
BUSINESS_DECISION_FIELDS = frozenset({"approved", "adopted", "delivered", "reviewDecision"})


class RunConflict(ValueError):
    """The requested transition would violate the durable run contract."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise RunConflict("运行输入必须是可持久化的 JSON 数据。") from error


@dataclass(frozen=True)
class FrozenRunInput:
    _canonical: str
    fingerprint: str

    @property
    def snapshot(self) -> Any:
        return json.loads(self._canonical)

    def matches(self, current_input: Any) -> bool:
        return self.fingerprint == _fingerprint(current_input)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def freeze_run_input(snapshot: Any) -> FrozenRunInput:
    canonical = _canonical_json(snapshot)
    return FrozenRunInput(
        _canonical=canonical,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


@dataclass(frozen=True)
class RunRecovery:
    status: RunStatus
    reason: Optional[Literal["interrupted", "outcome_unknown"]] = None


@dataclass(frozen=True)
class RunCompletion:
    status: Literal["completed"]
    result: dict[str, Any]


def _contains_business_decision(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in BUSINESS_DECISION_FIELDS or _contains_business_decision(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_business_decision(item) for item in value)
    return False


@dataclass(frozen=True)
class RunPolicy:
    execution: RunExecution
    initial_status: RunStatus
    allowed_statuses: frozenset[str]
    active_statuses: frozenset[str]
    cancellable: bool

    def validate(self, status: str) -> RunStatus:
        if status == "unknown" and self.execution == "local":
            raise RunConflict("本地运行不能进入未知状态。")
        if status not in self.allowed_statuses:
            raise RunConflict("运行状态不受支持。")
        return cast(RunStatus, status)

    def active(self, status: str) -> bool:
        self.validate(status)
        return status in self.active_statuses

    def start(self, status: str) -> Literal["running"]:
        self.validate(status)
        expected = "queued" if self.execution == "local" else "submitting"
        if status != expected:
            raise RunConflict("运行状态不允许开始。")
        return "running"

    def complete(
        self,
        status: str,
        *,
        frozen_input: FrozenRunInput,
        current_input: Any,
        result: Mapping[str, Any],
    ) -> RunCompletion:
        self.validate(status)
        if status not in {"queued", "running"}:
            raise RunConflict("运行状态不允许完成。")
        if not frozen_input.matches(current_input):
            raise RunConflict("运行输入已更新，迟到结果不能覆盖当前版本。")
        if _contains_business_decision(result):
            raise RunConflict("运行结果不能包含采用、审核或交付等业务决定。")
        canonical = _canonical_json(dict(result))
        return RunCompletion(status="completed", result=json.loads(canonical))

    def fail(self, status: str) -> Literal["failed"]:
        self.validate(status)
        if status not in self.active_statuses:
            raise RunConflict("运行状态不允许失败。")
        return "failed"

    def cancel(self, status: str) -> Literal["cancelled"]:
        self.validate(status)
        if not self.cancellable:
            raise RunConflict("此执行适配器不支持取消。")
        if status not in self.active_statuses:
            raise RunConflict("运行状态不允许取消。")
        return "cancelled"

    def mark_outcome_unknown(self, status: str) -> Literal["unknown"]:
        self.validate(status)
        if self.execution != "external" or status not in self.active_statuses:
            raise RunConflict("只有可能已提交的外部运行才能进入未知状态。")
        return "unknown"

    def recover_after_restart(self, status: str) -> RunRecovery:
        value = self.validate(status)
        if self.execution == "local" and value in {"queued", "running"}:
            return RunRecovery(status="failed", reason="interrupted")
        if self.execution == "external" and value == "submitting":
            return RunRecovery(status=self.mark_outcome_unknown(value), reason="outcome_unknown")
        return RunRecovery(status=value)


LOCAL_RUN_POLICY = RunPolicy(
    execution="local",
    initial_status="queued",
    allowed_statuses=LOCAL_RUN_STATUSES,
    active_statuses=frozenset({"queued", "running"}),
    cancellable=True,
)
EXTERNAL_RUN_POLICY = RunPolicy(
    execution="external",
    initial_status="submitting",
    allowed_statuses=EXTERNAL_RUN_STATUSES,
    active_statuses=frozenset({"submitting", "queued", "running", "unknown"}),
    cancellable=False,
)


class LocalComputeJobQueue:
    """Single local executor shared by resource-intensive business modules."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-compute")
        self._active: set[tuple[str, str]] = set()
        self._futures: dict[tuple[str, str], Future[None]] = {}
        self._lock = Lock()
        self._closed = False

    def submit(self, run_kind: str, owner_id: str, handler: Callable[[str], None]) -> bool:
        key = (run_kind, owner_id)
        with self._lock:
            if self._closed or key in self._active:
                return False
            self._active.add(key)
        try:
            future = self._executor.submit(handler, owner_id)
        except RuntimeError:
            with self._lock:
                self._active.discard(key)
            return False
        with self._lock:
            self._futures[key] = future
        future.add_done_callback(lambda _: self._release(key))
        return True

    def _release(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._active.discard(key)
            self._futures.pop(key, None)

    def is_active(self, run_kind: str, owner_id: str) -> bool:
        with self._lock:
            return (run_kind, owner_id) in self._active

    def is_project_active(self, owner_id: str) -> bool:
        """Includes cancelled work until its handler and cleanup have exited."""
        with self._lock:
            return any(project_id == owner_id for _, project_id in self._active)

    def wait(self, run_kind: str, owner_id: str) -> None:
        with self._lock:
            future = self._futures.get((run_kind, owner_id))
        if future is not None:
            future.result()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)


class RunQueueAdapter:
    """Narrow submit/read interface bound to one business run kind."""

    def __init__(
        self,
        queue: LocalComputeJobQueue,
        run_kind: str,
        handler: Callable[[str], None],
    ) -> None:
        self._queue = queue
        self._run_kind = run_kind
        self._handler = handler

    def submit(self, owner_id: str) -> bool:
        return self._queue.submit(self._run_kind, owner_id, self._handler)

    def is_active(self, owner_id: str) -> bool:
        return self._queue.is_active(self._run_kind, owner_id)

    def wait(self, owner_id: str) -> None:
        self._queue.wait(self._run_kind, owner_id)
