from app.montage_planner import MontagePlanningError, plan_montage


def candidate(candidate_id, asset_id, start, end, relevance, quality=0.8, **values):
    result = {
        "candidateId": candidate_id,
        "assetId": asset_id,
        "inPointSeconds": start,
        "outPointSeconds": end,
        "visualRelevance": relevance,
        "quality": {"score": quality, "evidence": {"width": 1280, "height": 720}},
        "duplicateRisk": {"level": "low", "reason": "不同源区间"},
        "evidence": {"kind": "keyframe_visual_vector", "basis": "关键帧视觉证据"},
    }
    result.update(values)
    return result


def segment(segment_id, text, duration, candidates, boundaries=(), source="provided"):
    return {
        "id": segment_id,
        "text": text,
        "narrationDurationSeconds": duration,
        "wordBoundariesSeconds": list(boundaries),
        "narrationSource": source,
        "searchId": f"search-{segment_id}",
        "indexVersion": f"index-{segment_id}",
        "candidates": candidates,
    }


def test_plan_covers_every_script_segment_and_keeps_semantics_ahead_of_beats():
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=2,
        variant_revision=3,
        segments=[
            segment("intro", "展示杯盖防泼溅", 2.4, [
                candidate("lid", "asset-a", 1, 5, 0.94),
                candidate("fallback", "asset-b", 0, 4, 0.72),
            ], boundaries=[1.2, 2.4]),
            segment("result", "展示通勤携带效果", 2.0, [
                candidate("commute", "asset-c", 3, 7, 0.91),
                candidate("fallback-2", "asset-d", 0, 4, 0.7),
            ], boundaries=[1.0, 2.0]),
        ],
        beat_times=[2.18, 4.4],
    )

    assert proposal["plannerVersion"] == "montage-planner-v2"
    assert [item["status"] for item in proposal["segments"]] == ["planned", "planned"]
    assert [item["suggestion"]["candidateId"] for item in proposal["segments"]] == ["lid", "commute"]
    assert [clip["start"] for clip in proposal["clips"]] == [0.0, 2.4]
    assert [clip["duration"] for clip in proposal["clips"]] == [2.4, 2.0]
    assert proposal["transitions"][0]["template"] == "cut"
    assert proposal["segments"][0]["timing"]["beatSnap"] is None
    assert "节拍 2.180 秒早于旁白结束，因此未吸附" in proposal["segments"][0]["reasons"]
    assert proposal["inputSnapshot"]["contentRevision"] == 2
    assert proposal["inputSnapshot"]["variantRevision"] == 3


def test_plan_never_reuses_or_overlaps_a_source_range_and_returns_an_explicit_gap():
    shared = candidate("shared", "asset-a", 0, 3, 0.98)
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[
            segment("one", "第一段", 2.0, [shared]),
            segment("two", "第二段", 2.0, [shared]),
        ],
        beat_times=[],
    )

    assert proposal["segments"][0]["status"] == "planned"
    assert proposal["segments"][1]["status"] == "gap"
    assert proposal["segments"][1]["suggestion"] is None
    assert [item["candidate"]["candidateId"] for item in proposal["segments"][1]["alternatives"]] == ["shared"]
    assert proposal["segments"][1]["unmet"] == ["没有不重复且不重叠的可用素材片段"]
    assert proposal["clips"] == [proposal["segments"][0]["selectedClip"]]


def test_timing_never_cuts_narration_at_an_earlier_word_boundary_or_beat():
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[segment("intro", "完整旁白", 2.0, [candidate("full", "asset-a", 0, 4, 0.9)], boundaries=[1.8])],
        beat_times=[1.95],
    )

    assert proposal["clips"][0]["duration"] == 2.0
    assert proposal["segments"][0]["timing"] == {
        "narrationDurationSeconds": 2.0,
        "wordBoundarySeconds": 1.8,
        "beatSnap": None,
    }
    assert "节拍 1.950 秒早于旁白结束，因此未吸附" in proposal["segments"][0]["reasons"]


def test_optional_beat_tail_never_turns_a_narration_complete_candidate_into_a_gap():
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[segment("intro", "完整旁白", 2.0, [candidate("enough", "asset-a", 0, 2.05, 0.9)])],
        beat_times=[2.1],
    )

    assert proposal["segments"][0]["status"] == "planned"
    assert proposal["segments"][0]["timing"]["beatSnap"] is None
    assert proposal["clips"][0]["duration"] == 2.0
    assert proposal["unmet"] == []
    assert "候选覆盖完整旁白但不足以延伸到节拍 2.100 秒，因此未吸附" in proposal["segments"][0]["reasons"]


def test_planner_keeps_ordinary_narrative_boundary_as_hard_cut():
    proposal = plan_montage(
        task_id="task-a", variant_id="variant-a", content_revision=0, variant_revision=0,
        segments=[
            segment("one", "先展示操作", 2, [candidate("one", "asset-a", 0, 3, .9, sceneId="same")]),
            segment("two", "再展示结果", 2, [candidate("two", "asset-a", 4, 7, .9, sceneId="same")]),
        ], beat_times=[], selling_point="省时",
    )

    assert proposal["transitions"] == [{
        "id": proposal["transitions"][0]["id"], "leftClipId": proposal["clips"][0]["id"],
        "rightClipId": proposal["clips"][1]["id"], "template": "cut", "enabled": True,
        "duration": 0.0, "overlap": 0.0, "audioCrossfade": False,
        "reason": "普通叙事边界保持硬切，避免无依据堆叠效果",
    }]


def test_planner_uses_evidence_for_scene_beat_and_selling_point_transitions():
    common = dict(task_id="task-a", variant_id="variant-a", content_revision=0, variant_revision=0)
    scene = plan_montage(**common, segments=[
        segment("one", "开场", 2, [candidate("one", "asset-a", 0, 4, .9, sceneId="wide")]),
        segment("two", "细节", 2, [candidate("two", "asset-b", 0, 4, .9, sceneId="macro")]),
    ], beat_times=[], selling_point="省时")
    beat = plan_montage(**common, segments=[
        segment("one", "开场", 2, [candidate("one", "asset-a", 0, 4, .9, sceneId="same")]),
        segment("two", "细节", 2, [candidate("two", "asset-b", 0, 4, .9, sceneId="same")]),
    ], beat_times=[2], selling_point="省时")
    emphasis = plan_montage(**common, segments=[
        segment("one", "开场", 2, [candidate("one", "asset-a", 0, 4, .9, sceneId="same")]),
        segment("two", "突出省时", 2, [candidate("two", "asset-b", 0, 4, .9, sceneId="same")]),
    ], beat_times=[], selling_point="省时")

    assert scene["transitions"][0]["template"] == "dissolve"
    assert beat["transitions"][0]["template"] == "push"
    assert emphasis["transitions"][0]["template"] == "flash-white"
    assert all(item["transitions"][0]["enabled"] for item in (scene, beat, emphasis))
    assert scene["clips"][0]["duration"] == 2.35
    assert scene["clips"][1]["start"] == 2.0
    assert scene["transitions"][0]["audioCrossfade"] is True


def test_transition_falls_back_for_short_neighbor_and_unmeasured_or_earlier_beats():
    common = dict(task_id="t", variant_id="v", content_revision=0, variant_revision=0)
    for duration, source, beats, scenes in [
        (.1, "provided", [], ("a", "b")),
        (2, "estimated_text", [2], (None, None)),
        (2, "subtitle_cue", [2], (None, None)),
        (2, "provided", [1.95], (None, None)),
    ]:
        proposal = plan_montage(**common, segments=[
            segment("one", "开场", duration, [candidate("one", "a", 0, 4, .9, sceneId=scenes[0])], source=source),
            segment("two", "后续", duration, [candidate("two", "b", 0, 4, .9, sceneId=scenes[1])], source=source),
        ], beat_times=beats)
        assert proposal["transitions"][0]["template"] == "cut"
        assert proposal["clips"][1]["start"] == duration


def test_estimated_narration_is_traceable_and_does_not_claim_word_or_beat_timing():
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[segment(
            "intro", "估时旁白", 2.0, [candidate("enough", "asset-a", 0, 3, 0.9)],
            source="estimated_text",
        )],
        beat_times=[2.05],
    )

    timing = proposal["segments"][0]["timing"]
    assert timing == {"narrationDurationSeconds": 2.0, "wordBoundarySeconds": 0.0, "beatSnap": None}
    assert "当前未提供真实词级时间戳，不伪造词边界" in proposal["segments"][0]["reasons"]
    assert "旁白总时长来自文本估算；生成实测配音前不执行节拍吸附" in proposal["segments"][0]["reasons"]


def test_subtitle_timing_does_not_snap_video_away_from_saved_cues():
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[segment(
            "intro", "字幕旁白", 2.0, [candidate("enough", "asset-a", 0, 3, 0.9)],
            source="subtitle_cue",
        )],
        beat_times=[2.05],
    )

    timing = proposal["segments"][0]["timing"]
    assert timing == {"narrationDurationSeconds": 2.0, "wordBoundarySeconds": 0.0, "beatSnap": None}
    assert proposal["clips"][0]["duration"] == 2.0
    assert "旁白时长来自字幕区间；没有独立配音轨时不执行节拍吸附" in proposal["segments"][0]["reasons"]


def test_candidate_that_covers_narration_outranks_a_shorter_higher_score_candidate():
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[segment("intro", "完整旁白", 2.0, [
            candidate("short", "asset-a", 0, 1, 0.99),
            candidate("full", "asset-b", 0, 4, 0.7),
        ])],
        beat_times=[],
    )

    assert proposal["segments"][0]["suggestion"]["candidateId"] == "full"
    assert proposal["segments"][0]["unmet"] == []
    assert proposal["unmet"] == []


def test_short_candidate_gap_is_rolled_up_to_the_proposal_gate():
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[segment("intro", "完整旁白", 2.0, [candidate("short", "asset-a", 0, 1, 0.99)])],
        beat_times=[],
    )

    assert proposal["segments"][0]["unmet"] == ["候选仅有 1.000 秒，短于旁白所需 2.000 秒"]
    assert proposal["unmet"] == [{
        "segmentId": "intro",
        "message": "候选仅有 1.000 秒，短于旁白所需 2.000 秒",
    }]


def test_duplicate_risk_and_adjacent_source_continuity_break_semantic_ties():
    first = candidate("first", "asset-a", 0, 2, 0.9)
    risky = candidate("risky", "asset-b", 0, 3, 0.9)
    risky["duplicateRisk"] = {"level": "high", "reason": "相似构图"}
    continuous = candidate("continuous", "asset-a", 2, 5, 0.9)
    different = candidate("different", "asset-c", 0, 3, 0.9)
    proposal = plan_montage(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[
            segment("one", "第一段", 2.0, [first]),
            segment("two", "第二段", 2.0, [risky, different, continuous]),
        ],
        beat_times=[],
    )

    assert proposal["segments"][1]["suggestion"]["candidateId"] == "continuous"
    assert "与上一镜头来自同一素材的相邻区间，保持动作与构图连续" in proposal["segments"][1]["reasons"]


def test_equal_candidates_rotate_between_variants_but_remain_deterministic():
    candidates = [
        candidate("one", "asset-a", 0, 4, 0.9),
        candidate("two", "asset-b", 0, 4, 0.9),
        candidate("three", "asset-c", 0, 4, 0.9),
    ]
    kwargs = dict(
        task_id="task-a",
        variant_id="variant-a",
        content_revision=0,
        variant_revision=0,
        segments=[segment("intro", "开场", 2, candidates)],
        beat_times=[],
    )

    first = plan_montage(**kwargs, variant_ordinal=0)
    again = plan_montage(**kwargs, variant_ordinal=0)
    second = plan_montage(**kwargs, variant_ordinal=1)

    assert first == again
    assert first["segments"][0]["suggestion"]["candidateId"] == "one"
    assert second["segments"][0]["suggestion"]["candidateId"] == "two"
    assert second["segments"][0]["reasons"][-1] == "同批第 2 个变体轮换同分候选，减少成片重复"


def test_shared_usage_changes_only_equivalent_candidates_and_explains_the_difference():
    candidates = [
        candidate("used", "asset-a", 0, 4, 0.9),
        candidate("unused", "asset-b", 0, 4, 0.9),
        candidate("weak", "asset-c", 0, 4, 0.4),
    ]

    proposal = plan_montage(
        task_id="task-b", variant_id="variant-b", content_revision=0, variant_revision=0,
        segments=[segment("intro", "开场", 2, candidates)], beat_times=[],
        shared_usage={"used": 1}, variant_ordinal=1,
    )

    assert proposal["segments"][0]["suggestion"]["candidateId"] == "unused"
    assert proposal["segments"][0]["reasons"][-1] == "优先选择同批其他变体尚未使用的候选，减少成片重复"


def test_invalid_candidate_ranges_are_rejected_instead_of_clamped_or_fabricated():
    broken = candidate("broken", "asset-a", 4, 3, 0.9)

    try:
        plan_montage(
            task_id="task-a",
            variant_id="variant-a",
            content_revision=0,
            variant_revision=0,
            segments=[segment("intro", "开场", 2, [broken])],
            beat_times=[],
        )
    except MontagePlanningError as error:
        assert error.code == "montage_candidate_invalid"
    else:
        raise AssertionError("倒置的源区间必须被拒绝")
