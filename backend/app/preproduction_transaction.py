"""Recoverable two-file transaction for workspace content and project layout."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .canvas_layout import CanvasLayoutStore
from .preproduction import PreproductionStore


class WorkspaceTransactionError(Exception):
    def __init__(self, *, recovery_pending: bool = False):
        super().__init__("workspace transaction failed")
        self.recovery_pending = recovery_pending


class WorkspaceTransaction:
    def __init__(self, content_store: PreproductionStore, layout_store: CanvasLayoutStore):
        self.content_store = content_store
        self.layout_store = layout_store

    def path(self, project_id: str) -> Path:
        return self.content_store.path(project_id, "workspace-transaction.json")

    def recover(self, project_id: str) -> None:
        path = self.path(project_id)
        if not path.exists():
            return
        if path.is_symlink() or not path.is_file():
            raise OSError("工作区事务记录无效")
        try:
            transaction = json.loads(path.read_text(encoding="utf-8"))
            if set(transaction) != {"version", "originalState", "originalLayout"} or transaction["version"] != 1:
                raise ValueError("工作区事务记录无效")
            self.content_store.save(project_id, transaction["originalState"])
            self.layout_store.save_snapshot(project_id, transaction["originalLayout"])
            path.unlink()
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OSError("工作区事务无法恢复") from error

    def commit(
        self,
        project_id: str,
        original_state: dict[str, Any],
        original_layout: dict[str, Any],
        next_state: dict[str, Any],
        next_layout: dict[str, Any],
    ) -> None:
        self.recover(project_id)
        self._write_marker(project_id, {
            "version": 1,
            "originalState": original_state,
            "originalLayout": original_layout,
        })
        try:
            self.content_store.save(project_id, next_state)
            self.layout_store.save_snapshot(project_id, next_layout)
            self.path(project_id).unlink()
        except (OSError, ValueError) as error:
            try:
                self.recover(project_id)
            except OSError as recovery_error:
                raise WorkspaceTransactionError(recovery_pending=True) from recovery_error
            raise WorkspaceTransactionError from error

    def _write_marker(self, project_id: str, transaction: dict[str, Any]) -> None:
        path = self.path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".workspace-transaction-", suffix=".part")
            temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(transaction, target, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
