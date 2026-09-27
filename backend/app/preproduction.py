"""Durable state and conservative validation for the pre-production workspace."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .reference_video import validate_storage_id


NODE_STATUSES = ("pending", "queued", "running", "completed", "failed", "stale")
ASSET_ROLES = ("character", "scene", "motion", "audio", "reference")


def safe_child(root: Path, *parts: str) -> Path:
    root = Path(os.path.abspath(root))
    if root.is_symlink():
        raise OSError("前置工作台存储目录无效")
    current = root
    for part in parts:
        validate_storage_id(part)
        current = current / part
        if current.exists() and current.is_symlink():
            raise OSError("前置工作台存储路径无效")
    current.resolve(strict=False).relative_to(root.resolve())
    return current


class PreproductionStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)

    def path(self, project_id: str, *parts: str) -> Path:
        return safe_child(self.root, "project-files", project_id, "preproduction", *parts)

    def load(self, project_id: str) -> dict[str, Any]:
        path = self.path(project_id, "state.json")
        if not path.exists():
            return default_workspace()
        if path.is_symlink() or not path.is_file():
            raise OSError("前置工作台状态文件无效")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise OSError("前置工作台状态无法读取") from error
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise OSError("前置工作台状态无效")
        _validate_state_shape(value)
        return value

    def save(self, project_id: str, state: dict[str, Any]) -> None:
        _validate_state_shape(state)
        path = self.path(project_id, "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".part")
            temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(state, target, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def default_workspace() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "revision": 0,
        "brief": {"theme": "", "purpose": "", "style": "", "duration": 0, "aspect": "", "mustPreserve": ""},
        "assets": [],
        "shots": [],
    }


def _validate_state_shape(state: dict[str, Any]) -> None:
    if not isinstance(state.get("revision"), int) or state["revision"] < 0:
        raise ValueError("前置工作台版本无效")
    if not isinstance(state.get("brief"), dict) or not isinstance(state.get("assets"), list) or not isinstance(state.get("shots"), list):
        raise ValueError("前置工作台状态无效")
