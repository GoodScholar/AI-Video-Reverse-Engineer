"""Versioned commercial graphics packaging rendered through one ASS compiler.

Adapted from OpenShorts ``subtitles.py`` (89f030934388d5167b3b0ede164ef893dad09ae9,
MIT): ASS v4+ header/style/event layout.  This module deliberately does not
reuse its Verdana fallback or text rewriting; product text is retained and
encoded literally for libass.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from bisect import bisect_left, bisect_right
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


FONT_ID = "noto-sans-cjk-sc-regular"
FONT_VERSION = "Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603"
FONT_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
FONT_POSTSCRIPT = "NotoSansCJKsc-Regular"
FONT_FAMILY = "Noto Sans CJK SC"
TEMPLATE_ID = "commercial-clean"
TEMPLATE_VERSION = 1
KINDS = {"subtitle", "keyword", "title", "selling-point", "cta"}
POSITIONS = {"auto", "top", "middle", "bottom"}
STYLES = {"plain", "emphasis", "banner"}
_FONT = Path(__file__).with_name("fonts") / "controlled" / "NotoSansCJKsc-Regular.otf"
_FONT_INFO: tuple[str, str, dict[int, str], dict[int, str], dict[str, tuple[int, int]], int] | None = None
_FONT_LOCK = RLock()
_PROOF_FONT: ContextVar["_FontFact | None"] = ContextVar("packaging_proof_font", default=None)


class PackagingError(ValueError):
    pass


class PackagingProofConflict(PackagingError):
    """A lock-free packaging proof no longer names the bytes it checked."""


@dataclass(frozen=True)
class _FontFact:
    path: str
    identity: tuple[int, int, int, int, int]
    digest: str
    cmap: dict[int, str]
    metrics: dict[str, tuple[int, int]]
    real_dim: int


@dataclass(frozen=True)
class PackagingProof:
    digest: str
    batch: bool
    font: _FontFact


def _font_identity(path: Path) -> tuple[int, int, int, int, int]:
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode):
        raise PackagingError("受控字体不可用或哈希不匹配。")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def current_font_identity() -> tuple[int, int, int, int, int]:
    """Cheap identity used to stale rendered lifecycle records; final gates still hash bytes."""
    return _font_identity(_FONT.resolve(strict=True))


def proof_font_identity(proof: PackagingProof | None) -> tuple[int, int, int, int, int] | None:
    return None if proof is None else proof.font.identity


def _hash_font(path: Path) -> tuple[str, tuple[int, int, int, int, int]]:
    """Hash the opened controlled file and reject path replacement while reading."""
    before = _font_identity(path)
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if opened_identity != before or not stat.S_ISREG(opened.st_mode):
            raise PackagingProofConflict("受控字体在验证期间发生变化。")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after_fd = os.fstat(descriptor)
        after_identity = (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_ctime_ns)
    finally:
        os.close(descriptor)
    after = _font_identity(path)
    if after_identity != before or after != before:
        raise PackagingProofConflict("受控字体在验证期间发生变化。")
    return digest.hexdigest(), before


def _read_font_fact() -> _FontFact:
    """Do the expensive font/content proof once, outside application locks."""
    path = _FONT.resolve(strict=True)
    digest, identity = _hash_font(path)
    if digest != FONT_SHA256:
        raise PackagingError("受控字体不可用或哈希不匹配。")
    face = None
    try:
        from fontTools.ttLib import TTFont
        face = TTFont(path, lazy=True)
        names = {entry.nameID: entry.toUnicode() for entry in face["name"].names if entry.nameID in (1, 4, 6)}
        if names.get(1) != FONT_FAMILY or names.get(4) != FONT_FAMILY or names.get(6) != FONT_POSTSCRIPT:
            raise PackagingError("受控字体身份无效。")
        real_dim = face["OS/2"].usWinAscent + face["OS/2"].usWinDescent
        if real_dim <= 0:
            raise PackagingError("受控字体真实尺寸无效。")
        fact = _FontFact(str(path), identity, digest, face.getBestCmap(), dict(face["hmtx"].metrics), real_dim)
    except PackagingError:
        raise
    except Exception as error:
        raise PackagingError("无法验证受控字体。") from error
    finally:
        if face is not None:
            face.close()
    try:
        if _font_identity(path) != identity:
            raise PackagingProofConflict("受控字体在验证期间发生变化。")
    except OSError as error:
        raise PackagingProofConflict("受控字体在验证期间不可读取。") from error
    return fact


def _normal_media_kinds(asset_media_kinds: dict[str, str] | None) -> dict[str, str]:
    """Canonical server-owned asset kind facts used by packaging decisions.

    Callers that own project assets must pass this mapping.  The conservative
    video default is retained only for isolated legacy/template callers: it
    can never accidentally grant an image's single-frame exemption.
    """
    if asset_media_kinds is None:
        return {}
    if not isinstance(asset_media_kinds, dict) or any(
            not isinstance(asset_id, str) or kind not in {"image", "video"}
            for asset_id, kind in asset_media_kinds.items()):
        raise PackagingError("服务端媒体类型上下文无效。")
    return dict(asset_media_kinds)


def _input_digest(snapshot: dict[str, Any], cues: list[dict[str, Any]], *, batch: bool,
                  asset_media_kinds: dict[str, str] | None = None) -> str:
    try:
        value = {"snapshot": snapshot, "cues": cues, "batch": batch,
                 "assetMediaKinds": _normal_media_kinds(asset_media_kinds)}
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError) as error:
        raise PackagingError("包装证明输入无效。") from error


def packaging_proof_key(snapshot: dict[str, Any], cues: list[dict[str, Any]], *, batch: bool,
                        asset_media_kinds: dict[str, str] | None = None) -> str:
    """Canonical request-local memoization key; never a persistent authorization."""
    return _input_digest(snapshot, cues, batch=batch, asset_media_kinds=asset_media_kinds)


def private_font_path() -> Path:
    """Return the only allowed font after hash and metadata verification."""
    prepared = _PROOF_FONT.get()
    if prepared is not None:
        return Path(prepared.path)
    global _FONT_INFO
    with _FONT_LOCK:
        actual_hash = hashlib.sha256(_FONT.read_bytes()).hexdigest() if _FONT.is_file() else None
        if actual_hash != FONT_SHA256:
            raise PackagingError("受控字体不可用或哈希不匹配。")
        identity = str(_FONT.resolve())
        if _FONT_INFO is not None and _FONT_INFO[:2] == (identity, actual_hash):
            return _FONT
        face = None
        try:
            from fontTools.ttLib import TTFont
            face = TTFont(_FONT, lazy=True)
            names = {entry.nameID: entry.toUnicode() for entry in face["name"].names if entry.nameID in (1, 4, 6)}
            if names.get(1) != FONT_FAMILY or names.get(4) != FONT_FAMILY or names.get(6) != FONT_POSTSCRIPT:
                raise PackagingError("受控字体身份无效。")
            os2 = face["OS/2"]
            real_dim = os2.usWinAscent + os2.usWinDescent
            if real_dim <= 0:
                raise PackagingError("受控字体真实尺寸无效。")
            if hashlib.sha256(_FONT.read_bytes()).hexdigest() != actual_hash:
                raise PackagingError("受控字体在验证期间发生变化。")
            _FONT_INFO = (identity, actual_hash, names, face.getBestCmap(), dict(face["hmtx"].metrics), real_dim)
        except PackagingError:
            raise
        except Exception as error:
            raise PackagingError("无法验证受控字体。") from error
        finally:
            if face is not None:
                face.close()
    return _FONT


def default_packaging() -> dict[str, Any]:
    return {
        "schemaVersion": 1, "templateId": TEMPLATE_ID, "templateVersion": TEMPLATE_VERSION,
        "font": {"id": FONT_ID, "version": FONT_VERSION, "sha256": FONT_SHA256,
                 "source": FONT_FAMILY, "license": "OFL-1.1"},
        "safeArea": {"left": .05, "right": .05, "top": .05, "bottom": .05}, "elements": [],
    }


def _number(value: Any, field: str, *, minimum: float = 0, maximum: float = math.inf) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise PackagingError(field + "无效。")
    return float(value)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 80 or any(not (char.isalnum() or char in "_-") for char in value):
        raise PackagingError(field + "无效。")
    return value


def _verify_font(value: Any) -> None:
    expected = default_packaging()["font"]
    if not isinstance(value, dict) or value != expected:
        raise PackagingError("字体引用无效。")


def _validate_text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 120 or not value.strip() or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise PackagingError("包装文字无效。")
    return value


def _glyphs(text: str) -> None:
    """Reject unsupported user glyphs before libass can silently fall back."""
    try:
        prepared = _PROOF_FONT.get()
        if prepared is not None:
            cmap = prepared.cmap
        else:
            private_font_path()
            assert _FONT_INFO is not None
            cmap = _FONT_INFO[3]
    except PackagingError:
        raise
    except Exception as error:
        raise PackagingError("无法读取受控字体字符表。") from error
    missing = [char for char in text if char not in "\n\t" and ord(char) not in cmap]
    if missing:
        raise PackagingError("受控字体缺少文字字符。")


def validate_packaging(value: Any, cues: list[dict[str, Any]], tracks: list[dict[str, Any]], *, batch: bool = False,
                       asset_media_kinds: dict[str, str] | None = None) -> dict[str, Any]:
    _normal_media_kinds(asset_media_kinds)
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "templateId", "templateVersion", "font", "safeArea", "elements"}:
        raise PackagingError("包装结构无效。")
    if value["schemaVersion"] != 1 or value["templateId"] != TEMPLATE_ID or value["templateVersion"] != TEMPLATE_VERSION:
        raise PackagingError("包装模板无效。")
    _verify_font(value["font"])
    area = value["safeArea"]
    if not isinstance(area, dict) or set(area) != {"left", "right", "top", "bottom"}:
        raise PackagingError("安全区无效。")
    safe = {key: _number(area[key], "安全区", maximum=.45) for key in area}
    if safe["left"] + safe["right"] >= .9 or safe["top"] + safe["bottom"] >= .9:
        raise PackagingError("安全区没有可用空间。")
    if not isinstance(value["elements"], list) or len(value["elements"]) > 40:
        raise PackagingError("包装元素无效。")
    cue_by_id = {cue.get("id"): cue for cue in cues if isinstance(cue, dict)}
    seen, elements = set(), []
    for raw in value["elements"]:
        if not isinstance(raw, dict):
            raise PackagingError("包装元素无效。")
        element_id = _id(raw.get("id"), "包装元素标识符")
        if element_id in seen:
            raise PackagingError("包装元素标识符不能重复。")
        seen.add(element_id)
        kind = raw.get("kind")
        if kind not in KINDS or type(raw.get("enabled")) is not bool or raw.get("position") not in POSITIONS or raw.get("style") not in STYLES:
            raise PackagingError("包装元素无效。")
        if batch and kind == "subtitle":
            if set(raw) != {"id", "kind", "enabled", "cueId", "position", "style"}:
                raise PackagingError("批量字幕只能保存 cueId 和样式覆盖。")
            cue_id = _id(raw.get("cueId"), "cueId")
            if cue_id not in cue_by_id:
                raise PackagingError("字幕 cueId 不存在。")
            if raw["enabled"]:
                _validate_text(cue_by_id[cue_id].get("text"))
            elements.append({"id": element_id, "kind": kind, "enabled": raw["enabled"], "cueId": cue_id,
                             "position": raw["position"], "style": raw["style"]})
            continue
        if set(raw) != {"id", "kind", "enabled", "start", "end", "text", "position", "style"}:
            raise PackagingError("包装元素字段无效。")
        start, end = _number(raw["start"], "包装起点"), _number(raw["end"], "包装终点")
        if end <= start:
            raise PackagingError("包装元素时序无效。")
        duration_centiseconds = _ass_centiseconds(end) - _ass_centiseconds(start)
        if duration_centiseconds <= 0:
            raise PackagingError("包装元素短于 ASS 时间精度。")
        if kind == "title" and duration_centiseconds < 10:
            raise PackagingError("动态标题时长至少需要 0.10 秒。")
        text = _validate_text(raw["text"])
        elements.append({"id": element_id, "kind": kind, "enabled": raw["enabled"], "start": start, "end": end,
                         "text": text, "position": raw["position"], "style": raw["style"]})
    return {"schemaVersion": 1, "templateId": TEMPLATE_ID, "templateVersion": TEMPLATE_VERSION,
            "font": deepcopy(value["font"]), "safeArea": safe, "elements": elements}


def escape_ass_text(text: str) -> str:
    """Literal encoding matching libass ass_get_next_char control parsing."""
    chunks = []
    for char in text:
        if char == "\\":
            chunks.append("\\\u2060")
        elif char == "{":
            chunks.append("\\{")
        elif char == "}":
            chunks.append("\\}")
        elif char == "\n":
            chunks.append("\\N")
        elif char == "\t":
            chunks.append(" ")
        else:
            chunks.append(char)
    return "".join(chunks)


def _ass_centiseconds(seconds: float) -> int:
    return int(round(seconds * 100))


def _ass_time(seconds: float) -> str:
    centiseconds = _ass_centiseconds(seconds)
    return "%d:%02d:%02d.%02d" % (centiseconds // 360000, centiseconds // 6000 % 60, centiseconds // 100 % 60, centiseconds % 100)


def _advance(text: str, size: float) -> float:
    """libass/FreeType REAL_DIM advance in ASS script pixels."""
    prepared = _PROOF_FONT.get()
    if prepared is not None:
        cmap, metrics, real_dim = prepared.cmap, prepared.metrics, prepared.real_dim
    else:
        private_font_path()
        assert _FONT_INFO is not None
        cmap, metrics, real_dim = _FONT_INFO[3], _FONT_INFO[4], _FONT_INFO[5]
    return sum(metrics[cmap[ord(char)]][0] for char in text if char not in "\n\t" and ord(char) in cmap) * size / real_dim


def _font_height(size: float) -> float:
    """FT_SIZE_REQUEST_TYPE_REAL_DIM makes Win ascent+descent equal Fontsize."""
    if _PROOF_FONT.get() is not None:
        return size
    private_font_path()
    assert _FONT_INFO is not None
    return size


def _interpolated_boxes(samples: list[dict[str, Any]], source_time: float) -> list[dict[str, float]]:
    """Return detector boxes at one source PTS, interpolating stable labels."""
    usable = [sample for sample in samples if isinstance(sample, dict) and isinstance(sample.get("timeSeconds"), (int, float))]
    if not usable:
        return []
    left = next((sample for sample in reversed(usable) if sample["timeSeconds"] <= source_time), usable[0])
    right = next((sample for sample in usable if sample["timeSeconds"] >= source_time), usable[-1])
    if left is right or right["timeSeconds"] == left["timeSeconds"]:
        return [box for box in left.get("boxes", []) if isinstance(box, dict)]
    fraction = (source_time - left["timeSeconds"]) / (right["timeSeconds"] - left["timeSeconds"])
    result = []
    for first, second in zip(left.get("boxes", []), right.get("boxes", [])):
        if not isinstance(first, dict) or not isinstance(second, dict) or first.get("label") != second.get("label"):
            continue
        try:
            result.append({key: float(first[key]) + (float(second[key]) - float(first[key])) * fraction
                           for key in ("x", "y", "width", "height")})
        except (KeyError, TypeError, ValueError):
            continue
    # A detector identity change is not safe to interpolate: reserve both ends.
    return result or [box for sample in (left, right) for box in sample.get("boxes", []) if isinstance(box, dict)]


_MISSING_PACKAGING_AVOIDANCE_FACT = "包装避让证据缺失，未完整覆盖实际帧区间；请先分析主体，或禁用/调整包装元素后再试。"


def _asset_media_kind(clip: dict[str, Any], asset_media_kinds: dict[str, str] | None) -> str:
    asset_id = clip.get("assetId")
    kinds = _normal_media_kinds(asset_media_kinds)
    if not isinstance(asset_id, str):
        raise PackagingError("服务端媒体类型上下文缺少素材。")
    # Fail closed for old callers.  In particular, never infer image status
    # from a mutable timeline field or file extension.
    return kinds.get(asset_id, "video")


def _review_observes_event(clip: dict[str, Any], reframe: dict[str, Any], start: float, end: float,
                           *, asset_media_kinds: dict[str, str] | None) -> bool:
    """Require contiguous real-frame subject coverage for a review event."""
    samples = reframe.get("samples")
    if not isinstance(samples, list):
        return False

    def has_subject(sample: dict[str, Any]) -> bool:
        sample_confidence = sample.get("confidence")
        if type(sample_confidence) not in (int, float) or not math.isfinite(sample_confidence) or sample_confidence <= 0:
            return False
        boxes = sample.get("boxes")
        if not isinstance(boxes, list):
            return False
        for box in boxes:
            if not isinstance(box, dict) or not isinstance(box.get("label"), str) or not box["label"].strip():
                continue
            values = [box.get(key) for key in ("x", "y", "width", "height", "confidence")]
            if (all(type(value) in (int, float) and math.isfinite(value) for value in values)
                    and box["width"] > 0 and box["height"] > 0 and box["confidence"] > 0):
                return True
        return False

    observed = [sample for sample in samples if isinstance(sample, dict)
                and type(sample.get("timeSeconds")) in (int, float)
                and math.isfinite(sample["timeSeconds"]) and has_subject(sample)]
    if _asset_media_kind(clip, asset_media_kinds) == "image":
        return any(float(sample["timeSeconds"]) == 0 for sample in observed)
    speed, in_point = clip.get("speed", 1), clip.get("inPoint", 0)
    if type(speed) not in (int, float) or speed <= 0 or type(in_point) not in (int, float):
        return False
    clip_start = clip.get("start", 0)
    duration = clip.get("duration", 0)
    if type(clip_start) not in (int, float) or type(duration) not in (int, float) or duration <= 0:
        return False
    overlap_start, overlap_end = max(start, clip_start), min(end, clip_start + duration)
    source_start = float(in_point) + (overlap_start - float(clip_start)) * float(speed)
    source_end = float(in_point) + (overlap_end - float(clip_start)) * float(speed)
    frame_duration = reframe.get("frameDurationSeconds")
    if (type(frame_duration) not in (int, float) or not math.isfinite(frame_duration)
            or frame_duration <= 0):
        return False
    # Samples name decoded display frames.  They cover only their own
    # half-open frame interval; samplingIntervalSeconds is an analysis cadence,
    # never a visual hold/interpolation authorization.
    intervals = sorted((float(sample["timeSeconds"]), float(sample["timeSeconds"]) + float(frame_duration))
                       for sample in observed)
    covered = source_start
    for left, right in intervals:
        if right <= covered + 1e-9:
            continue
        if left > covered + 1e-9:
            return False
        covered = max(covered, right)
        if covered >= source_end - 1e-9:
            return True
    return covered >= source_end - 1e-9


def _visible_event_reframes(tracks: list[dict[str, Any]], start: float, end: float, *,
                            asset_media_kinds: dict[str, str] | None = None):
    """Yield visible clip facts only after enforcing the packaging safety decision."""
    for track in tracks:
        if not isinstance(track, dict) or track.get("kind") != "video" or track.get("hidden"):
            continue
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or start >= clip.get("start", 0) + clip.get("duration", 0) or end <= clip.get("start", 0):
                continue
            reframe = clip.get("reframe")
            if not isinstance(reframe, dict):
                raise PackagingError("主体信息缺失需人工构图；请先分析主体，或选择中心裁剪/人工关键点。")
            override = reframe.get("override")
            mode = override.get("mode") if isinstance(override, dict) else None
            if reframe.get("status") == "review_required":
                if mode not in ("center", "manual", "replace"):
                    raise PackagingError("主体信息缺失需人工构图；请先分析主体，或选择中心裁剪/人工关键点。")
                # A crop decision permits rendering, but cannot prove that a
                # graphic event avoids subjects.  Partial facts are valid only
                # at their actual observed PTS; never extend or interpolate
                # them across an unobserved event interval.
                if not _review_observes_event(clip, reframe, start, end,
                                              asset_media_kinds=asset_media_kinds):
                    raise PackagingError(_MISSING_PACKAGING_AVOIDANCE_FACT)
            elif reframe.get("status") != "ready":
                raise PackagingError("主体信息缺失需人工构图；请先分析主体，或选择中心裁剪/人工关键点。")
            yield clip, reframe


def _subject_boxes(tracks: list[dict[str, Any]], start: float, end: float, width: int, height: int, *,
                   asset_media_kinds: dict[str, str] | None = None) -> list[tuple[float, float, float, float]]:
    """Project only subjects active during an event onto its output canvas."""
    from .montage_reframe import _center_at_trajectory, _crop_position, _trajectory, crop_geometry
    result = []
    for clip, reframe in _visible_event_reframes(tracks, start, end,
                                                  asset_media_kinds=asset_media_kinds):
        source_w, source_h, samples = reframe.get("sourceWidth"), reframe.get("sourceHeight"), reframe.get("samples")
        if type(source_w) is not int or type(source_h) is not int or source_w <= 0 or source_h <= 0 or not isinstance(samples, list):
            continue
        target = reframe.get("targetAspect") or {"width": width, "height": height}
        if (not isinstance(target, dict) or type(target.get("width")) is not int or type(target.get("height")) is not int
                or target["width"] <= 0 or target["height"] <= 0
                or target["width"] * height != target["height"] * width):
            raise PackagingError("主体构图目标与包装画布不一致，需人工确认。")
        try:
            crop_w, crop_h = crop_geometry(source_w, source_h, width, height)
        except ValueError as error:
            raise PackagingError("主体构图窗口无效，需人工确认。") from error
        clip_start, clip_end = float(clip.get("start", 0)), float(clip.get("start", 0)) + float(clip.get("duration", 0))
        overlap_start, overlap_end = max(start, clip_start), min(end, clip_end)
        speed = clip.get("speed", 1)
        in_point = clip.get("inPoint", 0)
        if type(speed) not in (int, float) or speed <= 0 or type(in_point) not in (int, float):
            raise PackagingError("主体构图窗口无效，需人工确认。")
        source_start = float(in_point) + (overlap_start - clip_start) * float(speed)
        source_end = float(in_point) + (overlap_end - clip_start) * float(speed)
        clip_source_end = float(in_point) + float(clip.get("duration", 0)) * float(speed)
        usable = [sample for sample in samples if isinstance(sample, dict) and isinstance(sample.get("timeSeconds"), (int, float))]
        usable.sort(key=lambda sample: float(sample["timeSeconds"]))
        override = reframe.get("override")
        mode = override.get("mode") if isinstance(override, dict) else None
        trajectory = _trajectory(reframe)
        def project(source_time: float, box: dict[str, Any]) -> tuple[float, float, float, float] | None:
            try:
                center = (.5, .5) if mode == "center" else _center_at_trajectory(trajectory, source_time)
                crop_x, crop_y = _crop_position(center, source_w, source_h, crop_w, crop_h)
            except (KeyError, TypeError, ValueError):
                raise PackagingError("主体构图轨迹无效，需人工确认。") from None
            try:
                x, y, box_w, box_h = (float(box[key]) for key in ("x", "y", "width", "height"))
            except (AttributeError, KeyError, TypeError, ValueError):
                return None
            return ((x * source_w - crop_x) * width / crop_w, (y * source_h - crop_y) * height / crop_h,
                    ((x + box_w) * source_w - crop_x) * width / crop_w,
                    ((y + box_h) * source_h - crop_y) * height / crop_h)

        def boxes(sample: dict[str, Any]) -> list[dict[str, Any]]:
            return [box for box in sample.get("boxes", []) if isinstance(box, dict)]

        def paired_boxes(left_boxes: list[dict[str, Any]], right_boxes: list[dict[str, Any]]):
            """Pair only explicit identities (or the sole unambiguous subject)."""
            def same_label(left: dict[str, Any], right: dict[str, Any]) -> bool:
                label = left.get("label")
                return isinstance(label, str) and bool(label) and label == right.get("label")

            def unique_by_identity(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
                identities: dict[str, list[dict[str, Any]]] = {}
                for item in items:
                    identity = item.get("id")
                    if isinstance(identity, str) and identity:
                        identities.setdefault(identity, []).append(item)
                return {identity: items[0] for identity, items in identities.items() if len(items) == 1}

            if len(left_boxes) == len(right_boxes) == 1 and same_label(left_boxes[0], right_boxes[0]):
                return [(left_boxes[0], right_boxes[0])], [], []
            left_ids, right_ids = unique_by_identity(left_boxes), unique_by_identity(right_boxes)
            shared = {identity for identity in set(left_ids) & set(right_ids)
                      if same_label(left_ids[identity], right_ids[identity])}
            paired_left, paired_right = {id(left_ids[label]) for label in shared}, {id(right_ids[label]) for label in shared}
            return ([(left_ids[label], right_ids[label]) for label in shared],
                    [box for box in left_boxes if id(box) not in paired_left],
                    [box for box in right_boxes if id(box) not in paired_right])

        def interpolate(left_box: dict[str, Any], right_box: dict[str, Any], fraction: float) -> dict[str, float] | None:
            try:
                return {key: float(left_box[key]) + (float(right_box[key]) - float(left_box[key])) * fraction
                        for key in ("x", "y", "width", "height")}
            except (KeyError, TypeError, ValueError):
                return None

        def append_sweep(left_sample: dict[str, Any], right_sample: dict[str, Any], segment_start: float,
                         segment_end: float) -> None:
            left_time, right_time = float(left_sample["timeSeconds"]), float(right_sample["timeSeconds"])
            left_boxes, right_boxes = boxes(left_sample), boxes(right_sample)
            pairs, left_only, right_only = paired_boxes(left_boxes, right_boxes)
            swept = []
            if left_time == right_time:
                pairs, left_only, right_only = [], left_boxes, []
            for first, second in pairs:
                for point in (segment_start, segment_end):
                    box = interpolate(first, second, (point - left_time) / (right_time - left_time))
                    if box is not None:
                        swept.append(project(point, box))
            # When identity is ambiguous (A+B→A, changed detector labels,
            # or duplicate labels), reserve every endpoint box.  The crop
            # centre is still evaluated at the event/sample intersection,
            # never at the unrelated whole-segment endpoint.
            for box in left_only + right_only:
                swept.extend((project(segment_start, box), project(segment_end, box)))
            swept = [box for box in swept if box is not None]
            if not swept:
                return
            left, top = min(box[0] for box in swept), min(box[1] for box in swept)
            right, bottom = max(box[2] for box in swept), max(box[3] for box in swept)
            if right > 0 and bottom > 0 and left < width and top < height:
                result.append((max(0, left), max(0, top), min(width, right), min(height, bottom)))

        if not usable:
            raise PackagingError("主体检测时间覆盖未知，需人工确认。")
        explicit_risk = (reframe.get("status") == "review_required" and isinstance(override, dict)
                         and override.get("mode") in ("center", "manual", "replace"))
        if explicit_risk:
            # A manual decision makes the crop renderable, but it does not
            # turn a partially completed detector run into continuous facts.
            # Reserve only observations that really occurred in this event;
            # do not interpolate, extend holds, or demand auto PTS coverage.
            if _asset_media_kind(clip, asset_media_kinds) == "image":
                for sample in usable:
                    if float(sample["timeSeconds"]) == 0:
                        append_sweep(sample, sample, 0, 0)
            else:
                frame_duration = float(reframe["frameDurationSeconds"])
                for sample in usable:
                    sample_time = float(sample["timeSeconds"])
                    segment_start = max(source_start, sample_time)
                    segment_end = min(source_end, sample_time + frame_duration)
                    if segment_start < segment_end:
                        append_sweep(sample, sample, segment_start, segment_end)
            continue
        if _asset_media_kind(clip, asset_media_kinds) == "image":
            if len(usable) != 1 or float(usable[0]["timeSeconds"]) != 0:
                raise PackagingError("静态图片主体检测必须有唯一的 0 秒样本，需人工确认。")
            append_sweep(usable[0], usable[0], 0, 0)
            continue
        first_time, last_time = float(usable[0]["timeSeconds"]), float(usable[-1]["timeSeconds"])
        if source_start < first_time - 1e-9:
            raise PackagingError("主体检测时间覆盖未知，需人工确认。")

        # Each interval is clipped before interpolation, so an event at
        # .9-.1 of a 0-1 movement only reserves that final tenth.
        covered_until = last_time
        sample_times = [float(sample["timeSeconds"]) for sample in usable]
        first_index = max(0, bisect_right(sample_times, source_start) - 1)
        last_index = min(len(usable), bisect_left(sample_times, source_end) + 1)
        event_samples = usable[first_index:last_index]
        for left_sample, right_sample in zip(event_samples, event_samples[1:]):
            left_time, right_time = float(left_sample["timeSeconds"]), float(right_sample["timeSeconds"])
            segment_start, segment_end = max(source_start, left_time), min(source_end, right_time)
            if segment_start < segment_end:
                append_sweep(left_sample, right_sample, segment_start, segment_end)

        # Task1 samples are real display-frame PTS.  Its last selected
        # frame is a finite half-open hold, never an unbounded terminal
        # keyframe.  Persisted duration wins; old records may use their
        # measured sampling cadence or prior real sample interval.  With
        # no such evidence, refuse automatic layout rather than returning
        # an empty occupancy set after the last sample.
        hold = reframe.get("frameDurationSeconds")
        if not isinstance(hold, (int, float)) or not math.isfinite(hold) or hold <= 0:
            hold = reframe.get("samplingIntervalSeconds")
        if (not isinstance(hold, (int, float)) or not math.isfinite(hold) or hold <= 0) and len(usable) > 1:
            hold = last_time - float(usable[-2]["timeSeconds"])
        if isinstance(hold, (int, float)) and math.isfinite(hold) and hold > 0:
            covered_until = min(last_time + float(hold), clip_source_end)
            tail_start, tail_end = max(source_start, last_time), min(source_end, covered_until)
            if tail_start < tail_end:
                append_sweep(usable[-1], usable[-1], tail_start, tail_end)
        if source_end > covered_until + 1e-9:
            raise PackagingError("主体检测时间覆盖未知，需人工确认。")
    return result


def _overlaps(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def _element_events(packaging: dict[str, Any], cues: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    cue_by_id = {cue["id"]: cue for cue in cues if isinstance(cue, dict) and isinstance(cue.get("id"), str)}
    events = []
    overrides = {item["cueId"]: item for item in packaging["elements"]
                 if item["kind"] == "subtitle" and "cueId" in item}
    # Packaging is an explicit rendering mode.  Its empty template still
    # renders the canonical cues once, with the controlled Noto ASS style.
    for cue in cues:
        if not isinstance(cue, dict) or not isinstance(cue.get("id"), str):
            continue
        override = overrides.get(cue["id"])
        if override is not None and not override["enabled"]:
            continue
        item = {"id": "cue-" + cue["id"], "kind": "subtitle", "enabled": True,
                "position": "bottom", "style": "plain"}
        if override is not None:
            item.update(override)
        text, start, end = cue.get("text"), cue.get("start"), cue.get("end")
        if isinstance(text, str) and isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            events.append({**item, "text": text, "start": float(start), "end": float(end)})
    for item in packaging["elements"]:
        if not item["enabled"]:
            continue
        if item["kind"] == "subtitle" and "cueId" in item:
            continue
        text, start, end = item["text"], item["start"], item["end"]
        events.append({**item, "text": text, "start": start, "end": end})
    for item in events:
        duration_centiseconds = _ass_centiseconds(item["end"]) - _ass_centiseconds(item["start"])
        if duration_centiseconds <= 0:
            raise PackagingError("包装元素短于 ASS 时间精度。")
        if item["kind"] == "title":
            if duration_centiseconds < 10:
                raise PackagingError("动态标题时长至少需要 0.10 秒。")
            item["fadeMilliseconds"] = min(180, duration_centiseconds * 10 // 3)
        # This includes implicit/default batch cues, before any libass call.
        _glyphs(item["text"])
        size = {"title": 56, "cta": 48, "selling-point": 42, "keyword": 44, "subtitle": 38}[item["kind"]]
        outline = {"plain": 3, "emphasis": 5, "banner": 14}[item["style"]]
        line_widths = [_advance(line, size) for line in item["text"].split("\n")]
        # ASS uses the selected face's Win ascender/descender per line; the
        # compiler has no unbounded animation, so this is its maximum frame.
        # libass paints on pixels while advance metrics are fractional.  Round
        # outward so collision checks and the compiler's reported bounds never
        # understate an edge pixel occupied by the outline.
        # Centre-aligned boxes expand by half their width on both sides, so a
        # conservative whole-pixel bound must also be even.
        item.update(size=size, outline=outline, width=math.ceil((max(line_widths, default=0) + outline * 2) / 2) * 2,
                    height=math.ceil(_font_height(size) * len(line_widths) + outline * 2))
    return events


def validate_final_events(packaging: dict[str, Any], cues: list[dict[str, Any]], tracks: list[dict[str, Any]], *, batch: bool,
                          asset_media_kinds: dict[str, str] | None = None) -> dict[str, Any]:
    """Validate every final ASS event, including default canonical cues."""
    # Rendering and lock-free proof generation keep the terminal font check;
    # state-store shape validation intentionally does not read this 16 MB file.
    if _PROOF_FONT.get() is None:
        private_font_path()
    checked = validate_packaging(packaging, cues, tracks, batch=batch, asset_media_kinds=asset_media_kinds)
    for event in _element_events(checked, cues, 0, 0):
        # Preflight has no output dimensions, but it can still prove that each
        # visible event interval has a deliberate subject decision.  Geometry
        # and avoidance are checked by _subject_boxes during ASS compilation.
        list(_visible_event_reframes(tracks, event["start"], event["end"],
                                     asset_media_kinds=asset_media_kinds))
    return checked


def prepare_packaging_proof(snapshot: dict[str, Any], cues: list[dict[str, Any]], *, batch: bool,
                            asset_media_kinds: dict[str, str] | None = None) -> PackagingProof | None:
    """Prepare one request-local terminal packaging proof outside project locks."""
    packaging = snapshot.get("packaging") if isinstance(snapshot, dict) else None
    if packaging is None:
        return None
    digest = _input_digest(snapshot, cues, batch=batch, asset_media_kinds=asset_media_kinds)
    try:
        fact = _read_font_fact()
    except FileNotFoundError as error:
        raise PackagingError("受控字体不可用或哈希不匹配。") from error
    except OSError as error:
        raise PackagingProofConflict("受控字体在验证期间不可读取。") from error
    token = _PROOF_FONT.set(fact)
    try:
        validate_final_events(packaging, cues, snapshot.get("tracks", []), batch=batch,
                              asset_media_kinds=asset_media_kinds)
    finally:
        _PROOF_FONT.reset(token)
    return PackagingProof(digest, batch, fact)


def consume_packaging_proof(proof: PackagingProof | None, snapshot: dict[str, Any], cues: list[dict[str, Any]], *, batch: bool,
                            asset_media_kinds: dict[str, str] | None = None) -> None:
    """Consume a request-local proof with digest/stat checks only (never font IO)."""
    packaging = snapshot.get("packaging") if isinstance(snapshot, dict) else None
    if packaging is None:
        if proof is not None:
            raise PackagingProofConflict("包装证明已过期，请刷新后重试。")
        return
    if proof is None or proof.batch != batch or proof.digest != _input_digest(snapshot, cues, batch=batch,
                                                                               asset_media_kinds=asset_media_kinds):
        raise PackagingProofConflict("包装证明已过期，请刷新后重试。")
    try:
        path = _FONT.resolve(strict=True)
        if str(path) != proof.font.path or _font_identity(path) != proof.font.identity:
            raise PackagingProofConflict("受控字体在验证期间发生变化。")
    except PackagingProofConflict:
        raise
    except OSError as error:
        raise PackagingProofConflict("受控字体在验证期间不可读取。") from error


def compile_ass(packaging: dict[str, Any], cues: list[dict[str, Any]], tracks: list[dict[str, Any]], width: int, height: int,
                font_path: Path | str | None = None, *, asset_media_kinds: dict[str, str] | None = None) -> str:
    """Compile all graphics into a single normalized PlayRes ASS document."""
    if type(width) is not int or type(height) is not int or width < 2 or height < 2:
        raise PackagingError("包装画布无效。")
    if font_path is not None and Path(font_path).resolve() != private_font_path().resolve():
        raise PackagingError("只能使用受控字体。")
    checked = validate_final_events(packaging, cues, tracks,
                                    batch=any("cueId" in item for item in packaging.get("elements", []) if isinstance(item, dict)),
                                    asset_media_kinds=asset_media_kinds)
    safe = checked["safeArea"]
    left, right = width * safe["left"], width * (1 - safe["right"])
    top, bottom = height * safe["top"], height * (1 - safe["bottom"])
    lines = ["[Script Info]", "ScriptType: v4.00+", "WrapStyle: 2", "ScaledBorderAndShadow: yes",
             f"PlayResX: {width}", f"PlayResY: {height}", "", "[V4+ Styles]",
             "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding"]
    events = _element_events(checked, cues, width, height)
    for index, item in enumerate(events):
        primary, outline_colour, back, bold, border = (("&H00FFFFFF", "&H90000000", "&H00000000", 0, 1) if item["style"] == "plain" else
                                                        ("&H0000D7FF", "&H90000000", "&H00000000", 1, 1) if item["style"] == "emphasis" else
                                                        ("&H00FFFFFF", "&H001F2937", "&H001F2937", 1, 3))
        lines.append(f"Style: G{index},{FONT_POSTSCRIPT},{item['size']},{primary},{primary},{outline_colour},{back},{bold},0,0,0,100,100,0,0,{border},{item['outline']},0,2,0,0,0,1")
    lines.extend(["", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"])
    occupied: list[tuple[float, float, float, float, float, float]] = []
    for index, item in enumerate(events):
        if item["width"] > right - left:
            raise PackagingError("文字超出安全区，需人工调整。")
        x = width / 2
        candidates = (["bottom", "top", "middle"] if item["kind"] == "subtitle" else ["top", "bottom", "middle"])
        if item["position"] != "auto":
            candidates = [item["position"]]
        subjects = _subject_boxes(tracks, item["start"], item["end"], width, height,
                                  asset_media_kinds=asset_media_kinds)
        selected = None
        for position in candidates:
            y = {"top": top + item["height"] / 2, "middle": (top + bottom) / 2,
                 "bottom": bottom - item["height"] / 2}[position]
            box = (x - item["width"] / 2, y - item["height"] / 2, x + item["width"] / 2, y + item["height"] / 2)
            collides_with_element = any(item["start"] < until and item["end"] > since and _overlaps(box, (a, b, c, d))
                                        for since, until, a, b, c, d in occupied)
            if box[0] >= left and box[2] <= right and box[1] >= top and box[3] <= bottom and not collides_with_element and not any(_overlaps(box, subject) for subject in subjects):
                selected = (y, box)
                break
        if selected is None:
            raise PackagingError("包装布局无可用安全区，需人工确认。")
        y, box = selected
        occupied.append((item["start"], item["end"], *box))
        # \\an5 with explicit centre is invariant for preview and export dimensions.
        fade = "\\fad(%d,%d)" % ((item["fadeMilliseconds"],) * 2) if item["kind"] == "title" else ""
        lines.append("Dialogue: %d,%s,%s,%s,,0,0,0,,{\\an5\\pos(%d,%d)%s}%s" % (
            index, _ass_time(item["start"]), _ass_time(item["end"]), "G" + str(index), round(x), round(y), fade,
            escape_ass_text(item["text"])))
    return "\n".join(lines) + "\n"
