"""Versioned commercial graphics packaging rendered through one ASS compiler.

Adapted from OpenShorts ``subtitles.py`` (89f030934388d5167b3b0ede164ef893dad09ae9,
MIT): ASS v4+ header/style/event layout.  This module deliberately does not
reuse its Verdana fallback or text rewriting; product text is retained and
encoded literally for libass.
"""
from __future__ import annotations

import hashlib
import math
from copy import deepcopy
from pathlib import Path
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


class PackagingError(ValueError):
    pass


def private_font_path() -> Path:
    """Return the only allowed font after hash and metadata verification."""
    if not _FONT.is_file() or hashlib.sha256(_FONT.read_bytes()).hexdigest() != FONT_SHA256:
        raise PackagingError("受控字体不可用或哈希不匹配。")
    try:
        from fontTools.ttLib import TTFont
        face = TTFont(_FONT, lazy=True)
        names = {entry.nameID: entry.toUnicode() for entry in face["name"].names if entry.nameID in (1, 4, 6)}
        if names.get(1) != FONT_FAMILY or names.get(4) != FONT_FAMILY or names.get(6) != FONT_POSTSCRIPT:
            raise PackagingError("受控字体身份无效。")
    except PackagingError:
        raise
    except Exception as error:
        raise PackagingError("无法验证受控字体。") from error
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
    private_font_path()


def _validate_text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 120 or not value.strip() or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise PackagingError("包装文字无效。")
    return value


def _glyphs(text: str) -> None:
    """Reject unsupported user glyphs before libass can silently fall back."""
    try:
        from fontTools.ttLib import TTFont
        cmap = TTFont(private_font_path(), lazy=True).getBestCmap()
    except PackagingError:
        raise
    except Exception as error:
        raise PackagingError("无法读取受控字体字符表。") from error
    missing = [char for char in text if char not in "\n\t" and ord(char) not in cmap]
    if missing:
        raise PackagingError("受控字体缺少文字字符。")


def validate_packaging(value: Any, cues: list[dict[str, Any]], tracks: list[dict[str, Any]], *, batch: bool = False) -> dict[str, Any]:
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
                _glyphs(_validate_text(cue_by_id[cue_id].get("text")))
            elements.append({"id": element_id, "kind": kind, "enabled": raw["enabled"], "cueId": cue_id,
                             "position": raw["position"], "style": raw["style"]})
            continue
        if set(raw) != {"id", "kind", "enabled", "start", "end", "text", "position", "style"}:
            raise PackagingError("包装元素字段无效。")
        start, end = _number(raw["start"], "包装起点"), _number(raw["end"], "包装终点")
        if end <= start:
            raise PackagingError("包装元素时序无效。")
        text = _validate_text(raw["text"])
        if raw["enabled"]:
            _glyphs(text)
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


def _ass_time(seconds: float) -> str:
    centiseconds = int(round(seconds * 100))
    return "%d:%02d:%02d.%02d" % (centiseconds // 360000, centiseconds // 6000 % 60, centiseconds // 100 % 60, centiseconds % 100)


def _advance(text: str, size: float) -> float:
    """libass/FreeType REAL_DIM advance in ASS script pixels."""
    from fontTools.ttLib import TTFont
    font = TTFont(private_font_path(), lazy=True)
    cmap, metrics, os2 = font.getBestCmap(), font["hmtx"].metrics, font["OS/2"]
    real_dim = os2.usWinAscent + os2.usWinDescent
    if real_dim <= 0:
        raise PackagingError("受控字体真实尺寸无效。")
    return sum(metrics[cmap[ord(char)]][0] for char in text if char not in "\n\t" and ord(char) in cmap) * size / real_dim


def _font_height(size: float) -> float:
    """FT_SIZE_REQUEST_TYPE_REAL_DIM makes Win ascent+descent equal Fontsize."""
    from fontTools.ttLib import TTFont
    font = TTFont(private_font_path(), lazy=True)
    os2 = font["OS/2"]
    if os2.usWinAscent + os2.usWinDescent <= 0:
        raise PackagingError("受控字体真实尺寸无效。")
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


def _subject_boxes(tracks: list[dict[str, Any]], start: float, end: float, width: int, height: int) -> list[tuple[float, float, float, float]]:
    """Project only subjects active during an event onto its output canvas."""
    from .montage_reframe import _center_at, _crop_position, crop_geometry
    result = []
    for track in tracks:
        if not isinstance(track, dict) or track.get("kind") != "video" or track.get("hidden"):
            continue
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or start >= clip.get("start", 0) + clip.get("duration", 0) or end <= clip.get("start", 0):
                continue
            reframe = clip.get("reframe")
            if not isinstance(reframe, dict):
                continue
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
            times = [source_start, source_end]
            times.extend(float(sample["timeSeconds"]) for sample in samples if isinstance(sample, dict)
                         and isinstance(sample.get("timeSeconds"), (int, float)) and source_start < sample["timeSeconds"] < source_end)
            def project(source_time: float) -> list[tuple[float, float, float, float]]:
                projected = []
                try:
                    crop_x, crop_y = _crop_position(_center_at(reframe, source_time), source_w, source_h, crop_w, crop_h)
                except (KeyError, TypeError, ValueError):
                    raise PackagingError("主体构图轨迹无效，需人工确认。") from None
                for box in _interpolated_boxes(samples, source_time):
                    try:
                        x, y, box_w, box_h = (float(box[key]) for key in ("x", "y", "width", "height"))
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
                    left, top = (x * source_w - crop_x) * width / crop_w, (y * source_h - crop_y) * height / crop_h
                    right, bottom = ((x + box_w) * source_w - crop_x) * width / crop_w, ((y + box_h) * source_h - crop_y) * height / crop_h
                    if right > 0 and bottom > 0 and left < width and top < height:
                        projected.append((max(0, left), max(0, top), min(width, right), min(height, bottom)))
                return projected

            # Between detector samples both the box and Task1 crop centre are
            # linear/piecewise-linear.  The endpoint union is therefore a
            # conservative swept envelope; with no stable detector identity we
            # intentionally retain every endpoint box instead of zip-dropping.
            sampled = [(source_time, project(source_time)) for source_time in sorted(set(times))]
            for _source_time, boxes in sampled:
                result.extend(boxes)
            for (_left_time, left_boxes), (_right_time, right_boxes) in zip(sampled, sampled[1:]):
                swept = [*left_boxes, *right_boxes]
                if swept:
                    result.append((min(box[0] for box in swept), min(box[1] for box in swept),
                                   max(box[2] for box in swept), max(box[3] for box in swept)))
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
        # This includes implicit/default batch cues, before any libass call.
        _glyphs(item["text"])
        size = {"title": 56, "cta": 48, "selling-point": 42, "keyword": 44, "subtitle": 38}[item["kind"]]
        outline = {"plain": 3, "emphasis": 5, "banner": 14}[item["style"]]
        line_widths = [_advance(line, size) for line in item["text"].split("\n")]
        # ASS uses the selected face's Win ascender/descender per line; the
        # compiler has no unbounded animation, so this is its maximum frame.
        item.update(size=size, outline=outline, width=max(line_widths, default=0) + outline * 2,
                    height=_font_height(size) * len(line_widths) + outline * 2)
    return events


def compile_ass(packaging: dict[str, Any], cues: list[dict[str, Any]], tracks: list[dict[str, Any]], width: int, height: int, font_path: Path | str | None = None) -> str:
    """Compile all graphics into a single normalized PlayRes ASS document."""
    if type(width) is not int or type(height) is not int or width < 2 or height < 2:
        raise PackagingError("包装画布无效。")
    if font_path is not None and Path(font_path).resolve() != private_font_path().resolve():
        raise PackagingError("只能使用受控字体。")
    checked = validate_packaging(packaging, cues, tracks, batch=any("cueId" in item for item in packaging.get("elements", []) if isinstance(item, dict)))
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
        subjects = _subject_boxes(tracks, item["start"], item["end"], width, height)
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
        lines.append("Dialogue: %d,%s,%s,%s,,0,0,0,,{\\an5\\pos(%d,%d)}%s" % (
            index, _ass_time(item["start"]), _ass_time(item["end"]), "G" + str(index), round(x), round(y), escape_ass_text(item["text"])))
    return "\n".join(lines) + "\n"
