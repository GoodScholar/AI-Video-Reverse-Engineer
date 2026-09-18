"""Durable, source-bound drafts for video shot preparation."""
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .reference_video import validate_storage_id


class ShotPrompts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positiveZh: str = Field(max_length=12_000)
    negativeZh: str = Field(max_length=12_000)
    positiveEn: str = Field(max_length=12_000)
    negativeEn: str = Field(max_length=12_000)


class ShotDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(default="", max_length=12_000)
    prompts: ShotPrompts = Field(default_factory=lambda: ShotPrompts(
        positiveZh="", negativeZh="", positiveEn="", negativeEn="",
    ))


class TimelineOverride(BaseModel):
    """A verified Toolkit cut snapshot applied only by shot preparation."""
    model_config = ConfigDict(extra="forbid")

    toolkitRunId: str = Field(min_length=1, max_length=100)
    cutRevision: int = Field(ge=0)
    cuts: list[float] = Field(default_factory=list, max_length=3_000)


class ShotPreparationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: int = 1
    sourceId: str
    preprocessingId: str
    revision: int = Field(default=0, ge=0)
    timelineEpoch: int = Field(default=0, ge=0)
    timelineOverride: Optional[TimelineOverride] = None
    drafts: dict[str, ShotDraft] = Field(default_factory=dict)


def empty_draft() -> ShotDraft:
    return ShotDraft()


def scene_boundaries(duration_seconds: float, scene_changes: object) -> list[float]:
    """Return all valid scene segments, including the leading and trailing spans."""
    if not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("参考视频时长无效")
    if not isinstance(scene_changes, list):
        raise ValueError("镜头检测结果无效")
    boundaries = [0.0]
    for item in scene_changes:
        if not isinstance(item, dict):
            raise ValueError("镜头检测结果无效")
        value = item.get("timeSeconds")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("镜头检测结果无效")
        if 0 < value < duration_seconds:
            boundaries.append(float(value))
    boundaries.append(float(duration_seconds))
    return sorted(set(boundaries))


class ShotPreparationStore:
    def __init__(self, data_dir: Path):
        self.root = Path(os.path.abspath(data_dir))

    def state_path(self, project_id: str) -> Path:
        validate_storage_id(project_id)
        if self.root.is_symlink():
            raise OSError("镜头准备存储路径无效")
        path = self.root / "project-files" / project_id / "shot-preparation" / "state.json"
        self._check_ancestors(path)
        return path

    def load(self, project_id: str, source_id: str, preprocessing_id: str) -> ShotPreparationState:
        path = self.state_path(project_id)
        if not path.exists():
            return ShotPreparationState(sourceId=source_id, preprocessingId=preprocessing_id)
        if path.is_symlink() or not path.is_file():
            raise OSError("镜头准备存储文件无效")
        try:
            state = ShotPreparationState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as error:
            raise OSError("镜头准备草稿无法读取") from error
        if state.sourceId != source_id or state.preprocessingId != preprocessing_id:
            # Do not let an old source's drafts reappear if a caller later observes
            # the former media id again. A replacement starts a distinct draft epoch.
            replacement = ShotPreparationState(sourceId=source_id, preprocessingId=preprocessing_id)
            self.save(project_id, replacement)
            return replacement
        return state

    def save(self, project_id: str, state: ShotPreparationState) -> None:
        path = self.state_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._check_ancestors(path)
        descriptor, raw_path = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".part")
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(state.model_dump_json())
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _check_ancestors(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise OSError("镜头准备存储路径超出数据目录") from error
        current = self.root
        for part in path.relative_to(self.root).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise OSError("镜头准备存储路径无效")


def load_scene_changes(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise OSError("镜头检测产物不可用")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["sceneChanges"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("镜头检测产物无效") from error
    if not isinstance(values, list):
        raise ValueError("镜头检测产物无效")
    return values
