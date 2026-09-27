"""Durable state and conservative validation for the pre-production workspace."""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
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
        if not isinstance(value, dict) or value.get("schemaVersion") not in (1, 2):
            raise OSError("前置工作台状态无效")
        _validate_state_shape(value)
        return migrate_workspace(value)

    def save(self, project_id: str, state: dict[str, Any]) -> None:
        state = prepare_workspace_for_save(state)
        _validate_state_shape(state)
        path = self.path(project_id, "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_v1(project_id, path)
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

    def _backup_v1(self, project_id: str, path: Path) -> None:
        backup = self.path(project_id, "state.v1.json")
        if backup.exists() or not path.exists():
            return
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise OSError("前置工作台状态无法备份") from error
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            return
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(dir=path.parent, prefix=".state-v1-", suffix=".part")
            temporary = Path(raw_path)
            with os.fdopen(descriptor, "wb") as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, backup)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def default_workspace() -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "revision": 0,
        "brief": {"theme": "", "purpose": "", "style": "", "duration": 0, "aspect": "", "mustPreserve": ""},
        "assets": [],
        "scenes": [default_scene()],
        "shots": [],
    }


def default_scene() -> dict[str, str]:
    return {"id": "scene-default", "title": "未分场", "rank": "00000001", "description": ""}


def migrate_workspace(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schemaVersion") == 2:
        return deepcopy(state)
    migrated = deepcopy(state)
    migrated["schemaVersion"] = 2
    migrated["scenes"] = [default_scene()]
    for index, shot in enumerate(migrated["shots"], start=1):
        shot["sceneId"] = "scene-default"
        shot["rank"] = f"{index:08d}"
    return migrated


def prepare_workspace_for_save(state: dict[str, Any]) -> dict[str, Any]:
    prepared = state if state.get("schemaVersion") == 2 else migrate_workspace(state)
    if prepared.get("schemaVersion") != 2 or not prepared.get("scenes"):
        return prepared
    default_scene_id = prepared["scenes"][0].get("id")
    for index, shot in enumerate(prepared.get("shots", []), start=1):
        shot.setdefault("sceneId", default_scene_id)
        shot.setdefault("rank", f"{index:08d}")
    return prepared


def _validate_state_shape(state: dict[str, Any]) -> None:
    if state.get("schemaVersion") not in (1, 2):
        raise ValueError("前置工作台模式版本无效")
    if not isinstance(state.get("revision"), int) or state["revision"] < 0:
        raise ValueError("前置工作台版本无效")
    if not isinstance(state.get("brief"), dict) or not isinstance(state.get("assets"), list) or not isinstance(state.get("shots"), list):
        raise ValueError("前置工作台状态无效")
    if state["schemaVersion"] != 2:
        return
    scenes = state.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("前置工作台场景无效")
    scene_ids: set[str] = set()
    scene_ranks: set[str] = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ValueError("前置工作台场景无效")
        scene_id = scene.get("id")
        rank = scene.get("rank")
        if not isinstance(scene_id, str) or not isinstance(rank, str) or not rank:
            raise ValueError("前置工作台场景无效")
        validate_storage_id(scene_id)
        if scene_id in scene_ids or rank in scene_ranks:
            raise ValueError("前置工作台场景顺序无效")
        if not isinstance(scene.get("title"), str) or not isinstance(scene.get("description"), str):
            raise ValueError("前置工作台场景无效")
        scene_ids.add(scene_id)
        scene_ranks.add(rank)
    shot_ranks: set[str] = set()
    for shot in state["shots"]:
        if not isinstance(shot, dict) or shot.get("sceneId") not in scene_ids:
            raise ValueError("前置工作台镜头场景无效")
        rank = shot.get("rank")
        if not isinstance(rank, str) or not rank or rank in shot_ranks:
            raise ValueError("前置工作台镜头顺序无效")
        shot_ranks.add(rank)
