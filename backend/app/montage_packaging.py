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
    from fontTools.ttLib import TTFont
    font = TTFont(private_font_path(), lazy=True)
    cmap, metrics, units = font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm
    return sum(metrics[cmap[ord(char)]][0] for char in text if char not in "\n\t" and ord(char) in cmap) * size / units


def _font_height(size: float) -> float:
    """Use the font's Win ascent/descent, matching libass metric intent."""
    from fontTools.ttLib import TTFont
    font = TTFont(private_font_path(), lazy=True)
    os2, units = font["OS/2"], font["head"].unitsPerEm
    return (os2.usWinAscent + os2.usWinDescent) * size / units


def _subject_boxes(tracks: list[dict[str, Any]], start: float, end: float, width: int, height: int) -> list[tuple[float, float, float, float]]:
    """Project all intersecting reframe boxes into target canvas coordinates.

    During an xfade both adjacent clips overlap in timeline time, so this
    naturally reserves both subjects. Every stored sample is used to avoid a
    moving person crossing an otherwise clear overlay between endpoints.
    """
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
            if not isinstance(target, dict) or target.get("width") != width or target.get("height") != height:
                raise PackagingError("主体构图目标与包装画布不一致，需人工确认。")
            crop_w = min(source_w, round(source_h * width / height))
            crop_h = min(source_h, round(source_w * height / width))
            if crop_w <= 0 or crop_h <= 0:
                raise PackagingError("主体构图窗口无效，需人工确认。")
            for sample in samples:
                center = sample.get("center") if isinstance(sample, dict) else None
                if not isinstance(center, dict) or type(center.get("x")) not in (int, float) or type(center.get("y")) not in (int, float):
                    continue
                crop_x = min(max(float(center["x"]) * source_w - crop_w / 2, 0), source_w - crop_w)
                crop_y = min(max(float(center["y"]) * source_h - crop_h / 2, 0), source_h - crop_h)
                for box in sample.get("boxes", []):
                    try:
                        x, y, box_w, box_h = (float(box[key]) for key in ("x", "y", "width", "height"))
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
                    left, top = (x * source_w - crop_x) * width / crop_w, (y * source_h - crop_y) * height / crop_h
                    right, bottom = ((x + box_w) * source_w - crop_x) * width / crop_w, ((y + box_h) * source_h - crop_y) * height / crop_h
                    if right > 0 and bottom > 0 and left < width and top < height:
                        result.append((max(0, left), max(0, top), min(width, right), min(height, bottom)))
    return result


def _overlaps(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def _element_events(packaging: dict[str, Any], cues: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    cue_by_id = {cue["id"]: cue for cue in cues if isinstance(cue, dict) and isinstance(cue.get("id"), str)}
    events = []
    for item in packaging["elements"]:
        if not item["enabled"]:
            continue
        if item["kind"] == "subtitle" and "cueId" in item:
            cue = cue_by_id[item["cueId"]]
            text, start, end = cue["text"], float(cue["start"]), float(cue["end"])
        else:
            text, start, end = item["text"], item["start"], item["end"]
        size = {"title": 56, "cta": 48, "selling-point": 42, "keyword": 44, "subtitle": 38}[item["kind"]]
        outline = {"plain": 3, "emphasis": 5, "banner": 14}[item["style"]]
        events.append({**item, "text": text, "start": start, "end": end, "size": size,
                       "width": _advance(max(text.split("\n"), key=len), size) + outline * 2,
                       "height": _font_height(size) + outline * 2})
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
             "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
             f"Style: Plain,{FONT_POSTSCRIPT},40,&H00FFFFFF,&H00FFFFFF,&H90000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,0,1",
             f"Style: Emphasis,{FONT_POSTSCRIPT},44,&H0000D7FF,&H0000D7FF,&H90000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,2,0,0,0,1",
             f"Style: Banner,{FONT_POSTSCRIPT},42,&H00FFFFFF,&H00FFFFFF,&H001F2937,&H001F2937,1,0,0,0,100,100,0,0,3,12,0,2,0,0,0,1",
             "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    occupied: list[tuple[float, float, float, float, float, float]] = []
    for index, item in enumerate(_element_events(checked, cues, width, height)):
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
        style = {"plain": "Plain", "emphasis": "Emphasis", "banner": "Banner"}[item["style"]]
        # \\an5 with explicit centre is invariant for preview and export dimensions.
        lines.append("Dialogue: %d,%s,%s,%s,,0,0,0,,{\\an5\\pos(%d,%d)}%s" % (
            index, _ass_time(item["start"]), _ass_time(item["end"]), style, round(x), round(y), escape_ass_text(item["text"])))
    return "\n".join(lines) + "\n"
