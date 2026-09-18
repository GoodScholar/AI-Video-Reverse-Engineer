"""Durable state and validation helpers for the local editing timeline."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .preproduction import safe_child
from .reference_video import validate_storage_id


DEFAULT_SETTINGS = {"width": 1280, "height": 720, "fps": 30}
MAX_DURATION = 300.0
MAX_TRACKS = 8
MAX_CLIPS = 80


class TimelineStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)

    def path(self, project_id: str, *parts: str) -> Path:
        return safe_child(self.root, "project-files", project_id, "timeline", *parts)

    def load(self, project_id: str) -> Dict[str, Any]:
        path = self.path(project_id, "state.json")
        if not path.exists():
            return default_workspace()
        if path.is_symlink() or not path.is_file():
            raise OSError("时间线状态文件无效")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise OSError("时间线状态无法读取") from error
        validate_state(state)
        return state

    def save(self, project_id: str, state: Dict[str, Any]) -> None:
        validate_state(state)
        path = self.path(project_id, "state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            descriptor, raw = tempfile.mkstemp(dir=str(path.parent), prefix=".timeline-", suffix=".part")
            temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(path))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def default_workspace() -> Dict[str, Any]:
    return {"schemaVersion": 1, "revision": 0, "settings": dict(DEFAULT_SETTINGS), "tracks": [], "runs": []}


def validate_state(state: Any) -> None:
    if not isinstance(state, dict) or state.get("schemaVersion") != 1:
        raise ValueError("时间线状态无效")
    if not isinstance(state.get("revision"), int) or isinstance(state["revision"], bool) or state["revision"] < 0:
        raise ValueError("时间线版本无效")
    if not isinstance(state.get("settings"), dict) or not isinstance(state.get("tracks"), list) or not isinstance(state.get("runs"), list):
        raise ValueError("时间线状态无效")


def validate_workspace(value: Any, assets: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    _keys(value, ("revision", "settings", "tracks"))
    revision = value["revision"]
    if not _integer(revision) or revision < 0:
        raise ValueError("版本无效")
    settings = _settings(value["settings"])
    tracks_value = value["tracks"]
    if not isinstance(tracks_value, list) or len(tracks_value) > MAX_TRACKS:
        raise ValueError("轨道数量无效")
    seen_tracks = set()
    seen_clips = set()
    clips_total = 0
    tracks = []
    for raw_track in tracks_value:
        _keys(raw_track, ("id", "name", "kind", "muted", "hidden", "clips"))
        track_id = _identifier(raw_track["id"], "轨道标识符")
        if track_id in seen_tracks:
            raise ValueError("轨道标识符不能重复")
        seen_tracks.add(track_id)
        name = _name(raw_track["name"], "轨道名称")
        kind = raw_track["kind"]
        if kind not in ("video", "audio"):
            raise ValueError("轨道类型无效")
        if not isinstance(raw_track["muted"], bool) or not isinstance(raw_track["hidden"], bool):
            raise ValueError("轨道开关无效")
        raw_clips = raw_track["clips"]
        if not isinstance(raw_clips, list):
            raise ValueError("片段列表无效")
        clips = []
        for raw_clip in raw_clips:
            clips_total += 1
            if clips_total > MAX_CLIPS:
                raise ValueError("片段数量不能超过 80")
            clip = _clip(raw_clip, assets, kind)
            if clip["id"] in seen_clips:
                raise ValueError("片段标识符不能重复")
            seen_clips.add(clip["id"])
            clips.append(clip)
        if kind == "video":
            previous_end = -1.0
            for clip in sorted(clips, key=lambda item: item["start"]):
                if clip["start"] < previous_end - 1e-8:
                    raise ValueError("同一视频轨道的片段不能重叠")
                previous_end = clip["start"] + clip["duration"]
        tracks.append({"id": track_id, "name": name, "kind": kind, "muted": raw_track["muted"], "hidden": raw_track["hidden"], "clips": clips})
    return settings, tracks


def has_visible_video(tracks: Iterable[Dict[str, Any]]) -> bool:
    return any(track["kind"] == "video" and not track["hidden"] and track["clips"] for track in tracks)


def has_audible_audio(tracks: Iterable[Dict[str, Any]], assets: Dict[str, Dict[str, Any]]) -> bool:
    for track in tracks:
        if track["muted"]:
            continue
        for clip in track["clips"]:
            asset = assets.get(clip["assetId"], {})
            if clip["volume"] > 0 and asset.get("kind") in ("audio", "video"):
                return True
    return False


def _settings(value: Any) -> Dict[str, int]:
    _keys(value, ("width", "height", "fps"))
    width, height, fps = value["width"], value["height"], value["fps"]
    if not _integer(width) or width < 64 or width > 1920 or width % 2:
        raise ValueError("输出宽度必须是 64 到 1920 的偶数")
    if not _integer(height) or height < 64 or height > 1920 or height % 2:
        raise ValueError("输出高度必须是 64 到 1920 的偶数")
    if fps not in (24, 25, 30) or not _integer(fps):
        raise ValueError("帧率必须是 24、25 或 30")
    return {"width": width, "height": height, "fps": fps}


def _clip(value: Any, assets: Dict[str, Dict[str, Any]], track_kind: str) -> Dict[str, Any]:
    _keys(value, ("id", "assetId", "start", "inPoint", "duration", "speed", "volume", "fadeIn", "fadeOut"))
    clip_id = _identifier(value["id"], "片段标识符")
    asset_id = _identifier(value["assetId"], "素材标识符")
    asset = assets.get(asset_id)
    if asset is None:
        raise ValueError("片段引用的素材不存在")
    allowed = ("image", "video") if track_kind == "video" else ("audio", "video")
    if asset.get("kind") not in allowed:
        raise ValueError("素材不能放入当前轨道")
    start = _number(value["start"], "时间线起点")
    in_point = _number(value["inPoint"], "素材入点")
    duration = _number(value["duration"], "输出时长")
    speed = _number(value["speed"], "速度")
    volume = _number(value["volume"], "音量")
    fade_in = _number(value["fadeIn"], "淡入")
    fade_out = _number(value["fadeOut"], "淡出")
    if start < 0 or start > MAX_DURATION or in_point < 0 or duration <= 0 or start + duration > MAX_DURATION:
        raise ValueError("片段时间必须在 0 到 300 秒范围内")
    if speed < .25 or speed > 4:
        raise ValueError("速度必须在 0.25 到 4 范围内")
    if volume < 0 or volume > 2:
        raise ValueError("音量必须在 0 到 2 范围内")
    if fade_in < 0 or fade_out < 0 or fade_in > duration or fade_out > duration:
        raise ValueError("淡入淡出必须在片段时长范围内")
    source_duration = asset.get("duration")
    if asset.get("kind") in ("audio", "video"):
        source_duration = _number(source_duration, "素材时长")
        if in_point + duration * speed > source_duration + 1e-8:
            raise ValueError("片段超出素材可用时长")
    return {"id": clip_id, "assetId": asset_id, "start": start, "inPoint": in_point, "duration": duration, "speed": speed, "volume": volume, "fadeIn": fade_in, "fadeOut": fade_out}


def _keys(value: Any, expected: Tuple[str, ...]) -> None:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("请求字段无效")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError(label + "无效")
    try:
        return validate_storage_id(value)
    except ValueError as error:
        raise ValueError(label + "无效") from error


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError(label + "无效")
    return value


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + "必须是有限数值")
    return float(value)
