"""Deterministic, explainable montage planning over indexed segment candidates."""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any


PLANNER_VERSION = "montage-planner-v1"
BEAT_SNAP_TOLERANCE_SECONDS = 0.12


class MontagePlanningError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def split_script_segments(script: str) -> list[str]:
    return [value.strip() for value in re.split(r"[。！？；;!?\n]|\.(?!\d)", script) if value.strip()]


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _round(value: float) -> float:
    return round(float(value), 6)


def _validate_segment(segment: dict[str, Any]) -> None:
    if (
        not isinstance(segment, dict)
        or not isinstance(segment.get("id"), str)
        or not segment["id"]
        or not isinstance(segment.get("text"), str)
        or not segment["text"].strip()
        or not _number(segment.get("narrationDurationSeconds"))
        or segment["narrationDurationSeconds"] <= 0
        or not isinstance(segment.get("wordBoundariesSeconds"), list)
        or segment.get("narrationSource", "provided") not in (
            "provided", "measured_voiceover", "subtitle_cue", "estimated_text",
        )
        or not isinstance(segment.get("candidates"), list)
        or not isinstance(segment.get("searchId"), str)
        or not isinstance(segment.get("indexVersion"), str)
    ):
        raise MontagePlanningError("montage_segment_invalid", "脚本段落或旁白时长无效。")
    boundaries = segment["wordBoundariesSeconds"]
    if any(not _number(value) or not 0 < value <= segment["narrationDurationSeconds"] for value in boundaries):
        raise MontagePlanningError("montage_segment_invalid", "旁白词边界无效。")
    if boundaries != sorted(set(boundaries)):
        raise MontagePlanningError("montage_segment_invalid", "旁白词边界必须递增且不能重复。")


def _validate_candidate(candidate: dict[str, Any]) -> None:
    quality = candidate.get("quality")
    if (
        not isinstance(candidate, dict)
        or not isinstance(candidate.get("candidateId"), str)
        or not candidate["candidateId"]
        or not isinstance(candidate.get("assetId"), str)
        or not candidate["assetId"]
        or not _number(candidate.get("inPointSeconds"))
        or not _number(candidate.get("outPointSeconds"))
        or not 0 <= candidate["inPointSeconds"] < candidate["outPointSeconds"]
        or not _number(candidate.get("visualRelevance"))
        or not 0 <= candidate["visualRelevance"] <= 1
        or not isinstance(quality, dict)
        or not _number(quality.get("score"))
        or not 0 <= quality["score"] <= 1
    ):
        raise MontagePlanningError("montage_candidate_invalid", "素材片段候选的来源区间或评分无效。")


def _source_overlaps(candidate: dict[str, Any], used_ranges: dict[str, list[tuple[float, float]]]) -> bool:
    start = float(candidate["inPointSeconds"])
    end = float(candidate["outPointSeconds"])
    return any(start < used_end and end > used_start for used_start, used_end in used_ranges.get(candidate["assetId"], []))


def _rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    requested_duration: float,
    used_candidate_ids: set[str],
    used_ranges: dict[str, list[tuple[float, float]]],
    shared_usage: dict[str, int],
    variant_ordinal: int,
    previous_candidate: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool, bool]:
    def semantic_score(item: dict[str, Any]) -> float:
        rerank = item.get("rerank")
        return float(rerank["confidence"]) if isinstance(rerank, dict) else float(item["visualRelevance"])

    def covers_narration(item: dict[str, Any]) -> bool:
        return float(item["outPointSeconds"]) - float(item["inPointSeconds"]) >= requested_duration - 0.001

    def duplicate_penalty(item: dict[str, Any]) -> int:
        level = item.get("duplicateRisk", {}).get("level")
        return 2 if level == "high" else 1 if level == "medium" else 0

    def continuity_score(item: dict[str, Any]) -> int:
        if previous_candidate is None or item["assetId"] != previous_candidate["assetId"]:
            return 0
        gap = float(item["inPointSeconds"]) - float(previous_candidate["outPointSeconds"])
        return 2 if 0 <= gap <= 2 else 1

    def decision_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            0 if covers_narration(item) else 1,
            -semantic_score(item),
            duplicate_penalty(item),
            -continuity_score(item),
            -float(item["quality"]["score"]),
            shared_usage.get(item["candidateId"], 0),
        )

    available = [candidate for candidate in candidates if candidate["candidateId"] not in used_candidate_ids
                 and not _source_overlaps(candidate, used_ranges)]
    available.sort(key=decision_key)
    if not available:
        return [], False, False
    best = available[0]
    best_key = decision_key(best)
    tied = [item for item in available if decision_key(item) == best_key]
    rotated = len(tied) > 1 and variant_ordinal % len(tied) != 0
    if len(tied) > 1:
        chosen = tied[variant_ordinal % len(tied)]
        available.remove(chosen)
        available.insert(0, chosen)
    chosen = available[0]
    chosen_without_usage = decision_key(chosen)[:-1]
    avoided_shared = shared_usage.get(chosen["candidateId"], 0) == 0 and any(
        decision_key(item)[:-1] == chosen_without_usage
        and shared_usage.get(item["candidateId"], 0) > 0
        for item in available[1:]
    )
    return available, rotated, avoided_shared


def _timing(segment: dict[str, Any], timeline_start: float, beat_times: list[float]) -> tuple[float, dict[str, Any], list[str]]:
    narration = float(segment["narrationDurationSeconds"])
    boundaries = [float(value) for value in segment["wordBoundariesSeconds"]]
    last_word_boundary = max((value for value in boundaries if value <= narration), default=0.0)
    semantic_duration = narration
    semantic_end = timeline_start + semantic_duration
    nearest = min(beat_times, key=lambda beat: abs(beat - semantic_end), default=None)
    reasons = [f"按旁白总时长保留至少 {semantic_duration:.3f} 秒画面"]
    reasons.append(
        f"已核对最后词边界 {last_word_boundary:.3f} 秒，切点不得截断对白"
        if boundaries else "当前未提供真实词级时间戳，不伪造词边界"
    )
    beat_snap = None
    duration = semantic_duration
    narration_source = segment.get("narrationSource")
    if narration_source in ("estimated_text", "subtitle_cue"):
        reasons.append(
            "旁白总时长来自文本估算；生成实测配音前不执行节拍吸附"
            if narration_source == "estimated_text"
            else "旁白时长来自字幕区间；没有独立配音轨时不执行节拍吸附"
        )
        return _round(duration), {
            "narrationDurationSeconds": _round(narration),
            "wordBoundarySeconds": _round(last_word_boundary),
            "beatSnap": None,
        }, reasons
    forward = [beat for beat in beat_times if semantic_end <= beat <= semantic_end + BEAT_SNAP_TOLERANCE_SECONDS]
    if forward:
        beat_snap = min(forward)
        duration = beat_snap - timeline_start
        reasons.append(f"镜头结束向后吸附到音乐节拍 {beat_snap:.3f} 秒")
    elif nearest is not None and nearest < semantic_end:
        reasons.append(f"节拍 {nearest:.3f} 秒早于旁白结束，因此未吸附")
    elif nearest is not None and boundaries:
        reasons.append(f"节拍 {nearest:.3f} 秒超出安全吸附范围，因此未吸附")
    return _round(duration), {
        "narrationDurationSeconds": _round(narration),
        "wordBoundarySeconds": _round(last_word_boundary),
        "beatSnap": _round(beat_snap) if beat_snap is not None else None,
    }, reasons


def _clip(
    candidate: dict[str, Any],
    segment_id: str,
    timeline_start: float,
    requested_duration: float,
    minimum_duration: float,
) -> tuple[dict[str, Any], list[str]]:
    available = float(candidate["outPointSeconds"]) - float(candidate["inPointSeconds"])
    duration = min(requested_duration, available)
    unmet = [] if duration >= minimum_duration - 0.001 else [
        f"候选仅有 {available:.3f} 秒，短于旁白所需 {minimum_duration:.3f} 秒"
    ]
    clip_id = f"montage-clip-{hashlib.sha256((segment_id + ':' + candidate['candidateId']).encode()).hexdigest()[:20]}"
    return {
        "id": clip_id,
        "assetId": candidate["assetId"],
        "start": _round(timeline_start),
        "inPoint": _round(candidate["inPointSeconds"]),
        "duration": _round(duration),
        "speed": 1,
        "volume": 1,
        "fadeIn": 0,
        "fadeOut": 0,
    }, unmet


def plan_montage(
    *,
    task_id: str,
    variant_id: str,
    content_revision: int,
    variant_revision: int,
    segments: list[dict[str, Any]],
    beat_times: list[float],
    shared_usage: dict[str, int] | None = None,
    variant_ordinal: int = 0,
    proposal_generation: int = 0,
) -> dict[str, Any]:
    """Create a frozen proposal without mutating a saved edit sequence."""
    if not segments:
        raise MontagePlanningError("montage_script_empty", "已确认脚本没有可规划的段落。")
    if any(not _number(value) or value < 0 for value in beat_times) or beat_times != sorted(set(beat_times)):
        raise MontagePlanningError("montage_beats_invalid", "音乐节拍必须递增且不能重复。")
    if type(variant_ordinal) is not int or variant_ordinal < 0:
        raise MontagePlanningError("montage_variant_invalid", "批次变体序号无效。")
    if type(proposal_generation) is not int or proposal_generation < 0:
        raise MontagePlanningError("montage_generation_invalid", "混剪提案生成序号无效。")
    usage = shared_usage or {}
    for segment in segments:
        _validate_segment(segment)
        for item in segment["candidates"]:
            _validate_candidate(item)

    input_snapshot = {
        "taskId": task_id,
        "variantId": variant_id,
        "contentRevision": content_revision,
        "variantRevision": variant_revision,
        "segments": deepcopy(segments),
        "beatTimes": [_round(value) for value in beat_times],
        "sharedUsage": dict(sorted(usage.items())),
        "variantOrdinal": variant_ordinal,
        "proposalGeneration": proposal_generation,
    }
    digest = hashlib.sha256(json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    proposal_id = f"remix-plan-{digest[:24]}"
    planned_segments = []
    clips = []
    unmet = []
    used_candidate_ids: set[str] = set()
    used_ranges: dict[str, list[tuple[float, float]]] = {}
    previous_candidate: dict[str, Any] | None = None
    timeline_start = 0.0

    for segment in segments:
        requested_duration, timing, reasons = _timing(segment, timeline_start, beat_times)
        narration_duration = float(timing["narrationDurationSeconds"])
        ranked, rotated, avoided_shared = _rank_candidates(
            segment["candidates"], requested_duration=narration_duration, used_candidate_ids=used_candidate_ids,
            used_ranges=used_ranges, shared_usage=usage, variant_ordinal=variant_ordinal,
            previous_candidate=previous_candidate,
        )
        if not ranked:
            message = "没有不重复且不重叠的可用素材片段"
            fallback_ranked, _, _ = _rank_candidates(
                segment["candidates"], requested_duration=narration_duration, used_candidate_ids=set(),
                used_ranges={}, shared_usage=usage, variant_ordinal=variant_ordinal,
                previous_candidate=previous_candidate,
            )
            alternatives = []
            for alternative in fallback_ranked:
                alternative_clip, alternative_unmet = _clip(
                    alternative, segment["id"], timeline_start, requested_duration, narration_duration,
                )
                alternatives.append({
                    "candidate": deepcopy(alternative), "clip": alternative_clip, "unmet": alternative_unmet,
                })
            planned_segments.append({
                "id": segment["id"], "text": segment["text"], "status": "gap",
                "input": {
                    "searchId": segment["searchId"], "indexVersion": segment["indexVersion"],
                    "retrievalVersion": segment.get("retrievalVersion"), "vectorizerVersion": segment.get("vectorizerVersion"),
                    "rerankId": segment.get("rerankId"), "narrationSource": segment.get("narrationSource", "provided"),
                    "wordBoundariesSeconds": deepcopy(segment["wordBoundariesSeconds"]),
                    "candidateIds": [item["candidateId"] for item in segment["candidates"]],
                },
                "suggestion": None, "selectedClip": None, "alternatives": alternatives,
                "timing": timing, "reasons": reasons, "unmet": [message],
            })
            unmet.append({"segmentId": segment["id"], "message": message})
            timeline_start = _round(timeline_start + requested_duration)
            continue
        chosen = ranked[0]
        available_duration = float(chosen["outPointSeconds"]) - float(chosen["inPointSeconds"])
        if timing["beatSnap"] is not None and available_duration < requested_duration - 0.001:
            beat_snap = float(timing["beatSnap"])
            timing["beatSnap"] = None
            requested_duration = narration_duration
            reasons = [reason for reason in reasons if not reason.startswith("镜头结束")]
            reasons.append(f"候选覆盖完整旁白但不足以延伸到节拍 {beat_snap:.3f} 秒，因此未吸附")
        selected_clip, segment_unmet = _clip(
            chosen, segment["id"], timeline_start, requested_duration, narration_duration,
        )
        alternatives = []
        for alternative in ranked[1:]:
            alternative_clip, alternative_unmet = _clip(
                alternative, segment["id"], timeline_start, requested_duration, narration_duration,
            )
            alternatives.append({
                "candidate": deepcopy(alternative), "clip": alternative_clip, "unmet": alternative_unmet,
            })
        reasons.extend([
            f"本地视觉相关度 {chosen['visualRelevance']:.3f}，素材质量 {chosen['quality']['score']:.3f}",
            str(chosen.get("evidence", {}).get("basis") or "使用片段关键帧视觉证据"),
        ])
        duplicate_risk = chosen.get("duplicateRisk", {})
        reasons.append(f"相似构图风险：{duplicate_risk.get('level', 'unknown')}")
        if isinstance(chosen.get("rerank"), dict):
            reasons.append(
                f"豆包复排置信度 {chosen['rerank']['confidence']:.3f}：{chosen['rerank']['reason']}"
            )
        if usage.get(chosen["candidateId"], 0):
            reasons.append(f"该片段已被同批其他变体使用 {usage[chosen['candidateId']]} 次，仍因语义得分领先而保留")
        elif avoided_shared:
            reasons.append("优先选择同批其他变体尚未使用的候选，减少成片重复")
        if rotated:
            reasons.append(f"同批第 {variant_ordinal + 1} 个变体轮换同分候选，减少成片重复")
        if previous_candidate is not None and chosen["assetId"] == previous_candidate["assetId"]:
            gap = float(chosen["inPointSeconds"]) - float(previous_candidate["outPointSeconds"])
            if 0 <= gap <= 2:
                reasons.append("与上一镜头来自同一素材的相邻区间，保持动作与构图连续")
        suggestion = {
            "candidateId": chosen["candidateId"], "assetId": chosen["assetId"],
            "inPointSeconds": _round(chosen["inPointSeconds"]),
            "outPointSeconds": _round(chosen["outPointSeconds"]),
            "visualRelevance": _round(chosen["visualRelevance"]),
            "quality": deepcopy(chosen["quality"]),
            "evidence": deepcopy(chosen.get("evidence")),
        }
        planned_segments.append({
            "id": segment["id"], "text": segment["text"], "status": "gap" if segment_unmet else "planned",
            "input": {
                "searchId": segment["searchId"], "indexVersion": segment["indexVersion"],
                "retrievalVersion": segment.get("retrievalVersion"), "vectorizerVersion": segment.get("vectorizerVersion"),
                "rerankId": segment.get("rerankId"), "narrationSource": segment.get("narrationSource", "provided"),
                "wordBoundariesSeconds": deepcopy(segment["wordBoundariesSeconds"]),
                "candidateIds": [item["candidateId"] for item in segment["candidates"]],
            },
            "suggestion": suggestion, "suggestionClip": deepcopy(selected_clip), "selectedClip": selected_clip,
            "alternatives": alternatives, "timing": timing, "reasons": reasons,
            "unmet": segment_unmet,
        })
        clips.append(selected_clip)
        for message in segment_unmet:
            unmet.append({"segmentId": segment["id"], "message": message})
        used_candidate_ids.add(chosen["candidateId"])
        used_ranges.setdefault(chosen["assetId"], []).append((
            float(selected_clip["inPoint"]), float(selected_clip["inPoint"] + selected_clip["duration"]),
        ))
        previous_candidate = chosen
        timeline_start = _round(timeline_start + requested_duration)

    return {
        "schemaVersion": 2,
        "id": proposal_id,
        "engine": "RemixEngine",
        "entrypoint": "script",
        "plannerVersion": PLANNER_VERSION,
        "baseRevision": variant_revision,
        "inputSnapshot": input_snapshot,
        "segments": planned_segments,
        "clips": clips,
        "unmet": unmet,
    }


__all__ = ["MontagePlanningError", "PLANNER_VERSION", "plan_montage", "split_script_segments"]
