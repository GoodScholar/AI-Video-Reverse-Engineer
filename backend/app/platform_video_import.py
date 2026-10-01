"""Durable lifecycle shared by constrained third-party video import adapters."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .durable_runs import freeze_run_input
from .preproduction import safe_child


class PlatformVideoImportRuns:
    """Atomic, project-scoped records and staging directories for one platform."""

    _ACTIVE = frozenset({"queued", "running", "cancel_requested"})

    def __init__(self, root: Path, platform: str):
        if platform not in {"youtube", "bilibili", "douyin", "xiaohongshu"}:
            raise ValueError("不支持的视频平台")
        self._root = Path(root)
        self._platform = platform
        self._lock = RLock()

    def create_or_reuse(self, project_id: str, snapshot: dict[str, Any], idempotency_key: str) -> tuple[dict[str, Any], bool]:
        # Rights confirmation is an auditable event timestamp, not a new media
        # request identity. Re-submission therefore remains idempotent.
        frozen = freeze_run_input({key: value for key, value in snapshot.items() if key != "rightsConfirmedAt"})
        with self._lock:
            state = self._load(project_id)
            existing = next((run for run in state["runs"] if run["idempotencyKey"] == idempotency_key
                             and run["inputFingerprint"] == frozen.fingerprint), None)
            if existing is None:
                existing = next((run for run in state["runs"] if run["inputFingerprint"] == frozen.fingerprint
                                 and run["status"] in self._ACTIVE | {"completed"}), None)
            if existing is not None:
                return dict(existing), False
            run = self._new(snapshot, frozen.fingerprint, idempotency_key, retry_of=None)
            state["runs"].append(run); self._save(project_id, state)
            return dict(run), True

    def retry(self, project_id: str, run_id: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            state = self._load(project_id); previous = self._find(state, run_id)
            if previous["status"] not in {"failed", "cancelled"}:
                raise ValueError("该导入运行当前不能重试")
            existing = next((run for run in state["runs"] if run.get("retryOf") == run_id
                             and run.get("status") in self._ACTIVE | {"completed"}), None)
            if existing is not None:
                return dict(existing), False
            frozen = freeze_run_input({key: value for key, value in previous["input"].items() if key != "rightsConfirmedAt"})
            run = self._new(previous["input"], frozen.fingerprint, uuid4().hex, retry_of=run_id)
            state["runs"].append(run); self._save(project_id, state)
            return dict(run), True

    def get(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._find(self._load(project_id), run_id))

    def list(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(run) for run in reversed(self._load(project_id)["runs"])]

    def transition(self, project_id: str, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = self._load(project_id); run = self._find(state, run_id); run.update(changes); self._save(project_id, state)
            return dict(run)

    def request_cancel(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load(project_id); run = self._find(state, run_id)
            if run["status"] == "queued":
                run.update(status="cancelled", error="cancelled")
            elif run["status"] == "running":
                run.update(status="cancel_requested", error=None)
            elif run["status"] not in {"cancel_requested", "cancelled"}:
                raise ValueError("该导入运行当前不能取消")
            self._save(project_id, state); return dict(run)

    def recover(self, project_id: str) -> None:
        with self._lock:
            state = self._load(project_id); changed = False
            for run in state["runs"]:
                if run["status"] in {"queued", "running"}:
                    run.update(status="failed", error="interrupted"); changed = True
                elif run["status"] == "cancel_requested":
                    run.update(status="cancelled", error="cancelled"); changed = True
            if changed: self._save(project_id, state)
        for run in state["runs"]:
            if run["status"] in {"failed", "cancelled"}:
                shutil.rmtree(self.staging_dir(project_id, run["id"]), ignore_errors=True)

    def staging_dir(self, project_id: str, run_id: str) -> Path:
        return safe_child(self._root, "project-files", project_id, "preproduction", f"{self._platform}-import", "staging", run_id)

    def _path(self, project_id: str) -> Path:
        return safe_child(self._root, "project-files", project_id, "preproduction", f"{self._platform}-import", "runs.json")

    def _load(self, project_id: str) -> dict[str, Any]:
        path = self._path(project_id)
        if not path.exists(): return {"version": 1, "runs": []}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("version") != 1 or not isinstance(value.get("runs"), list): raise ValueError
            for run in value["runs"]:
                if not isinstance(run, dict) or not isinstance(run.get("id"), str) or not isinstance(run.get("status"), str): raise ValueError
            return value
        except (OSError, ValueError, json.JSONDecodeError, AttributeError) as error:
            raise OSError(f"{self._platform} 导入运行记录无效") from error

    def _save(self, project_id: str, state: dict[str, Any]) -> None:
        path = self._path(project_id); path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=f".{self._platform}-runs-", suffix=".part"); temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(state, target, ensure_ascii=False, separators=(",", ":"), allow_nan=False); target.flush(); os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    def _new(self, snapshot: dict[str, Any], fingerprint: str, idempotency_key: str, retry_of: str | None) -> dict[str, Any]:
        from .video_sources import utc_timestamp
        return {"id": f"{self._platform}-import-" + uuid4().hex, "status": "queued", "error": None,
                "createdAt": utc_timestamp(), "input": snapshot, "inputFingerprint": fingerprint,
                "idempotencyKey": idempotency_key, "retryOf": retry_of, "uploadedAssetId": None}

    @staticmethod
    def _find(state: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = next((item for item in state["runs"] if item.get("id") == run_id), None)
        if run is None: raise KeyError("导入运行不存在")
        return run


def public_platform_video_import_run(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key != "input"}
