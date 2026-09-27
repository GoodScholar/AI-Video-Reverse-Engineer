"""Shared aspect-ratio presets and product smart-resolution rules."""
from __future__ import annotations

from math import gcd
from typing import Iterable, Mapping, Any


PRODUCT_PRESETS = {
    "21:9": (1680, 720),
    "16:9": (1280, 720),
    "4:3": (960, 720),
    "1:1": (720, 720),
    "3:4": (720, 960),
    "9:16": (720, 1280),
}
ASPECT_MODES = (*PRODUCT_PRESETS, "smart")


def valid_aspect_mode(value: Any) -> bool:
    return isinstance(value, str) and value in ASPECT_MODES


def ratio_label(width: int, height: int) -> str:
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def resolve_product_aspect(mode: str, assets: Iterable[Mapping[str, Any]]) -> dict:
    if not valid_aspect_mode(mode):
        raise ValueError("视频比例无效")
    if mode != "smart":
        width, height = PRODUCT_PRESETS[mode]
        return {"mode": mode, "resolvedAspect": mode, "width": width, "height": height,
                "reason": f"使用项目选择的 {mode} 比例。"}

    dimensions = []
    ordered_assets = sorted(assets, key=lambda asset: (
        str(asset.get("id", "")), asset.get("width", 0), asset.get("height", 0)))
    for asset in ordered_assets:
        width, height = asset.get("width"), asset.get("height")
        if type(width) is int and type(height) is int and width > 0 and height > 0:
            dimensions.append((width, height))
    if not dimensions:
        return _fallback("所选素材没有可读尺寸，智能比例回退到 9:16。")
    ratios = [width / height for width, height in dimensions]
    if max(ratios) / min(ratios) > 1.03:
        return _fallback("所选素材比例不一致，智能比例回退到 9:16。")

    ratio = ratios[0]
    closest = min(PRODUCT_PRESETS, key=lambda key: abs(PRODUCT_PRESETS[key][0] / PRODUCT_PRESETS[key][1] - ratio))
    preset_ratio = PRODUCT_PRESETS[closest][0] / PRODUCT_PRESETS[closest][1]
    if abs(preset_ratio - ratio) / ratio <= 0.02:
        width, height = PRODUCT_PRESETS[closest]
        resolved = closest
    else:
        width, height = _canvas_for_ratio(ratio)
        resolved = ratio_label(width, height)
    return {"mode": "smart", "resolvedAspect": resolved, "width": width, "height": height,
            "reason": "智能比例沿用所选主视觉素材的可读比例。"}


def _fallback(reason: str) -> dict:
    width, height = PRODUCT_PRESETS["9:16"]
    return {"mode": "smart", "resolvedAspect": "9:16", "width": width, "height": height, "reason": reason}


def _canvas_for_ratio(ratio: float) -> tuple[int, int]:
    if ratio >= 1:
        width, height = _even(round(720 * ratio)), 720
    else:
        width, height = 720, _even(round(720 / ratio))
    longest = max(width, height)
    if longest > 1920:
        scale = 1920 / longest
        width, height = _even(round(width * scale)), _even(round(height * scale))
    return width, height


def _even(value: int) -> int:
    return max(64, value - value % 2)
