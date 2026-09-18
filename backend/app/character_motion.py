"""持久化的 Wan Animate 角色动作迁移方案。

方案不依赖语义分析；它只快照独立角色图、当前项目视频和固定官方模板。
"""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .reference_video import validate_storage_id


OFFICIAL_TEMPLATE = {
    "repository": "https://github.com/Comfy-Org/workflow_templates",
    "template": "templates/video_wan2_2_14B_animate.json",
    "revision": "90c71fb78b3726392d010ff62a8e79e92d7296ad",
    "sha256": "06ad8b95e64215328a2a3d2f90495b5bab3e251175e4258d01b977f6dcdecb69",
    "status": "candidate",
    "notice": "工作流来自 Comfy-Org 官方模板；尚未在本机 GPU/ComfyUI 队列验证。",
}


class CharacterImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    originalName: str
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    sizeBytes: int = Field(ge=1, le=20 * 1024 * 1024)


class MotionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(default=512, ge=256, le=1280)
    height: int = Field(default=512, ge=256, le=1280)
    frames: int = Field(default=81, ge=17, le=161)
    fps: int = Field(default=16, ge=8, le=24)
    seed: int = Field(default=42, ge=0, le=2**53 - 1)

    @field_validator("width", "height")
    @classmethod
    def aligned_dimensions(cls, value):
        if value % 16:
            raise ValueError("生成尺寸必须为 16 的倍数。")
        return value

    @field_validator("frames")
    @classmethod
    def aligned_frames(cls, value):
        if value % 4 != 1:
            raise ValueError("生成帧数必须符合 4n+1。")
        return value


class MotionRun(BaseModel):
    id: str
    promptId: Optional[str] = None
    status: Literal["submitting", "queued", "running", "completed", "failed", "unknown"]
    createdAt: str
    revision: int
    sourceHash: str
    comfyUrl: str
    workflow: dict
    error: Optional[str] = None
    outputs: list[dict] = Field(default_factory=list)


class CharacterMotionState(BaseModel):
    revision: int = 0
    prompt: str = Field(default="", max_length=12000)
    settings: MotionSettings = Field(default_factory=MotionSettings)
    comfyUrl: str = "http://127.0.0.1:8188"
    character: Optional[CharacterImage] = None
    sourceHash: Optional[str] = None
    runs: list[MotionRun] = Field(default_factory=list)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def driver_for(project):
    media = getattr(project, "referenceMedia", None)
    return media if media is not None and getattr(media, "type", None) == "video" else None


def source_hash(project, character: Optional[CharacterImage]) -> str:
    source = {"driver": getattr(driver_for(project), "id", None), "character": character.id if character else None}
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def default_settings(project):
    driver = driver_for(project)
    if not driver:
        return MotionSettings()
    side = min(getattr(driver, "width", 512), getattr(driver, "height", 512))
    scale = min(512 / max(side, 1), 1280 / max(getattr(driver, "width", 512), getattr(driver, "height", 512)))
    return MotionSettings(width=max(256, round(driver.width * scale / 16) * 16), height=max(256, round(driver.height * scale / 16) * 16))


class CharacterMotionStore:
    def __init__(self, data_dir: Path):
        self.root = Path(os.path.abspath(data_dir))

    def path(self, project_id: str, *parts: str) -> Path:
        for part in (project_id, *parts):
            validate_storage_id(part)
        path = self.root / "project-files" / project_id / "character-motion"
        for part in parts:
            path /= part
        current = self.root
        if current.is_symlink():
            raise OSError("角色动作素材路径无效。")
        for part in path.relative_to(self.root).parts:
            current /= part
            if current.is_symlink():
                raise OSError("角色动作素材路径无效。")
        return path

    def load(self, project):
        path = self.path(project.id, "state.json")
        if not path.exists():
            return CharacterMotionState(settings=default_settings(project))
        return CharacterMotionState.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project_id: str, state: CharacterMotionState):
        path = self.path(project_id, "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(state.model_dump_json())
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
