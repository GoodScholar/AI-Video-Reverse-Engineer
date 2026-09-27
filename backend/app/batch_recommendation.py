"""Explainable script-to-asset matching from user-supplied asset metadata."""
from __future__ import annotations

import math
import re
from uuid import uuid4

from .timeline import validate_workspace


class RecommendationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _terms(value: str) -> set[str]:
    terms = set()
    for run in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value.lower()):
        if re.fullmatch(r"[a-z0-9]+", run):
            if len(run) > 1:
                terms.add(run)
        else:
            terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def recommend(task: dict, assets: dict[str, dict], asset_ids: list[str]) -> dict:
    visuals = []
    for asset_id in asset_ids:
        asset = assets.get(asset_id)
        if asset is None:
            raise RecommendationError("batch_asset_missing", "所选素材已不存在，请重新选择。")
        if asset.get("kind") in ("video", "image"):
            visuals.append(asset)
    if len(visuals) < 2:
        raise RecommendationError("batch_assets_insufficient", "至少选择两段画面素材，再按脚本推荐。")
    segments = [segment.strip() for segment in re.split(r"[。！？；;!?\n]|\.(?!\d)", task["script"]) if segment.strip()]
    if not segments:
        raise RecommendationError("batch_script_empty", "脚本缺少可匹配的内容段落，请补充描述。")
    if len(segments) > 80:
        raise RecommendationError("batch_script_too_long", "脚本段落超过 80 段，请拆分任务。")
    point_terms = _terms(task["sellingPoint"])
    preferences = []
    for index, segment in enumerate(segments, start=1):
        segment_terms = _terms(segment)
        candidates = []
        for rank, asset in enumerate(visuals):
            description = f"{asset.get('name', '')} {asset.get('notes', '')}"
            metadata_terms = _terms(description)
            shared = segment_terms & metadata_terms
            if shared:
                point_shared = point_terms & metadata_terms
                candidates.append((asset, sorted(shared), sorted(point_shared), len(shared), len(point_shared), rank))
        if not candidates:
            raise RecommendationError("batch_match_unavailable", f"脚本第 {index} 段「{segment[:40]}」没有匹配的素材名称或备注；请补充相关描述。")
        preferences.append(sorted(candidates, key=lambda item: (-item[3], -item[4], item[5])))

    owner: dict[str, int] = {}
    chosen: list[tuple | None] = [None] * len(segments)

    def assign(index: int, visited: set[str]) -> bool:
        for candidate in preferences[index]:
            asset_id = candidate[0]["id"]
            if asset_id in visited:
                continue
            visited.add(asset_id)
            previous = owner.get(asset_id)
            if previous is None or assign(previous, visited):
                owner[asset_id] = index
                chosen[index] = candidate
                return True
        return False

    for index in range(len(segments)):
        if not assign(index, set()):
            raise RecommendationError("batch_assets_insufficient", f"脚本第 {index + 1} 段缺少另一段匹配画面素材；请增加或补充素材描述。")

    clips = []
    matches = []
    position = 0.0
    for segment, candidate in zip(segments, chosen):
        assert candidate is not None
        asset, shared, point_shared, _, _, _ = candidate
        source_duration = asset.get("duration") if asset["kind"] == "video" else 3.0
        if type(source_duration) not in (int, float) or not math.isfinite(source_duration) or source_duration <= 0:
            raise RecommendationError("batch_asset_unavailable", f"素材「{asset['name']}」时长无效，请检查素材。")
        duration = min(3.0, source_duration)
        clip = {"id": uuid4().hex, "assetId": asset["id"], "start": position, "inPoint": 0, "duration": duration,
                "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}
        clips.append(clip)
        matches.append({"clipId": clip["id"], "scriptSegment": segment, "assetId": asset["id"],
                        "assetName": asset["name"], "matchedTerms": shared, "matchedSellingPointTerms": point_shared})
        position += duration
    variant = task["variant"]
    first_video = next((track["id"] for track in variant["tracks"] if track["kind"] == "video"), None)
    tracks = [{**track, "clips": clips} if track["id"] == first_video else track for track in variant["tracks"]]
    try:
        validate_workspace({"revision": variant["revision"], "settings": variant["settings"], "tracks": tracks}, assets)
    except ValueError as error:
        raise RecommendationError("batch_recommendation_invalid", f"推荐超出时间线限制：{error}") from None
    return {"baseRevision": variant["revision"], "method": "asset-metadata-keywords", "assetIds": asset_ids,
            "clips": clips, "matches": matches}
