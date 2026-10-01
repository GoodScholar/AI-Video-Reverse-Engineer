from pathlib import Path

import pytest


def _packaging(elements):
    return {
        "schemaVersion": 1,
        "templateId": "commercial-clean",
        "templateVersion": 1,
        "font": {
            "id": "noto-sans-cjk-sc-regular", "version": "Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603",
            "sha256": "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b",
            "source": "Noto Sans CJK SC", "license": "OFL-1.1",
        },
        "safeArea": {"left": .05, "right": .05, "top": .05, "bottom": .05},
        "elements": elements,
    }


def _element(kind, **overrides):
    value = {"id": kind + "-one", "kind": kind, "enabled": True, "start": 0, "end": 1,
             "text": "新品\\N{安全}\n第二行", "position": "bottom", "style": "plain"}
    value.update(overrides)
    return value


def test_compiler_uses_verified_private_font_and_literal_ass_escaping():
    from app.montage_packaging import compile_ass, private_font_path, validate_packaging

    packaging = _packaging([_element("subtitle")])
    checked = validate_packaging(packaging, [], [])
    result = compile_ass(checked, [], [], 720, 1280, private_font_path())

    assert "PlayResX: 720" in result
    assert "PlayResY: 1280" in result
    assert "G0,NotoSansCJKsc-Regular,38" in result
    assert "\\\u2060N" in result
    assert "\\{" in result and "\\}" in result
    assert "\\N第二行" in result


def test_rejects_tampered_font_unknown_glyph_and_bad_subtitle_cue_reference(monkeypatch):
    from app import montage_packaging
    from app.montage_packaging import PackagingError, validate_final_events, validate_packaging

    with pytest.raises(PackagingError, match="字体"):
        validate_final_events(_packaging([_element("title", text="𠀀")]), [], [], batch=False)
    with pytest.raises(PackagingError, match="cueId"):
        validate_packaging(_packaging([{
            "id": "subtitle-one", "kind": "subtitle", "enabled": True, "cueId": "missing",
            "position": "bottom", "style": "plain",
        }]), [{"id": "cue-1", "start": 0, "end": 1, "text": "字幕"}], [], batch=True)
    monkeypatch.setattr(montage_packaging, "FONT_SHA256", "00" * 32)
    with pytest.raises(PackagingError, match="字体"):
        validate_final_events(_packaging([_element("title")]), [], [], batch=False)


def test_structure_validation_never_opens_font_but_request_proof_binds_font_and_snapshot(monkeypatch):
    """State loading may validate shape under a lock; byte/font work belongs to a proof."""
    from app import montage_packaging
    from app.montage_packaging import (PackagingProofConflict, consume_packaging_proof,
                                       prepare_packaging_proof, validate_packaging)

    package = _packaging([_element("title", text="锁外证明")])
    def must_not_open_font():
        raise AssertionError("structure validation opened the controlled font")
    monkeypatch.setattr(montage_packaging, "private_font_path", must_not_open_font)
    assert validate_packaging(package, [], []) == package
    monkeypatch.undo()

    snapshot = {"settings": {"width": 720, "height": 1280}, "tracks": [], "packaging": package}
    proof = prepare_packaging_proof(snapshot, [], batch=False)
    assert proof is not None
    consume_packaging_proof(proof, snapshot, [], batch=False)
    with pytest.raises(PackagingProofConflict):
        consume_packaging_proof(proof, {**snapshot, "tracks": [{"id": "changed"}]}, [], batch=False)
    original = montage_packaging._font_identity
    monkeypatch.setattr(montage_packaging, "_font_identity", lambda path: (*original(path)[:-1], original(path)[-1] + 1))
    with pytest.raises(PackagingProofConflict):
        consume_packaging_proof(proof, snapshot, [], batch=False)


def test_batch_subtitle_is_cue_style_overlay_not_a_second_transcript():
    from app.montage_packaging import validate_packaging

    checked = validate_packaging(
        _packaging([{"id": "subtitle-one", "kind": "subtitle", "enabled": True, "cueId": "cue-1",
                     "position": "bottom", "style": "plain"}]),
        [{"id": "cue-1", "start": 2, "end": 3, "text": "正本文字"}], [], batch=True,
    )

    assert checked["elements"][0] == {
        "id": "subtitle-one", "kind": "subtitle", "enabled": True, "cueId": "cue-1",
        "position": "bottom", "style": "plain",
    }


def _reframed_track(*, start=0, duration=1, box=None):
    box = box or {"x": .35, "y": .35, "width": .3, "height": .3, "label": "person", "confidence": .9}
    return [{"id": "video", "name": "画面", "kind": "video", "muted": False, "hidden": False, "clips": [{
        "id": "clip", "assetId": "asset", "start": start, "duration": duration, "inPoint": 0, "speed": 1,
        "reframe": {"sourceWidth": 1000, "sourceHeight": 1000, "samples": [
            {"timeSeconds": 0, "boxes": [box], "center": {"x": .5, "y": .5}, "confidence": .9},
            {"timeSeconds": 1, "boxes": [box], "center": {"x": .5, "y": .5}, "confidence": .9},
        ], "status": "ready", "uncertaintyReasons": [], "override": {"mode": "auto", "keyframes": []}},
    }]}]


def test_enabled_packaging_rejects_visible_clip_without_subject_decision_but_ignores_nonoverlap():
    """A graphic event may not silently assume a visible clip has no subject."""
    from app.montage_packaging import PackagingError, _subject_boxes

    track = _reframed_track()[0]
    track["clips"][0].pop("reframe")

    with pytest.raises(PackagingError, match="主体信息缺失需人工构图"):
        _subject_boxes([track], .2, .8, 720, 1280)
    assert _subject_boxes([track], 1.1, 1.2, 720, 1280) == []

    # Image clips share the visible-track contract; neither kind may become an
    # implicit empty subject set merely because it does not have video samples.
    track["clips"][0]["_stillImage"] = True
    with pytest.raises(PackagingError, match="主体信息缺失需人工构图"):
        _subject_boxes([track], .2, .8, 720, 1280)


def test_batch_preview_and_export_preflight_share_the_missing_subject_block():
    from app.montage_packaging import PackagingError, validate_final_events

    track = _reframed_track()[0]
    track["clips"][0].pop("reframe")
    cues = [{"id": "cue", "start": .2, "end": .8, "text": "批量包装"}]
    with pytest.raises(PackagingError, match="主体信息缺失需人工构图"):
        validate_final_events(_packaging([]), cues, [track], batch=True)

    cues[0].update(start=1.1, end=1.2)
    assert validate_final_events(_packaging([]), cues, [track], batch=True)["elements"] == []


def test_server_media_kind_context_distinguishes_a_still_from_a_video_and_binds_the_proof():
    """A client-side clip flag must not turn a video observation into an image fact."""
    from app.montage_packaging import (PackagingError, PackagingProofConflict, consume_packaging_proof,
                                       prepare_packaging_proof, validate_final_events)

    track = _reframed_track()[0]
    reframe = track["clips"][0]["reframe"]
    reframe.update(status="review_required", samples=reframe["samples"][:1],
                   frameDurationSeconds=.04, override={"mode": "center", "keyframes": []})
    # This legacy flag is deliberately ignored: only the server-owned context
    # determines whether the 0-second observation can cover the full event.
    track["clips"][0]["_stillImage"] = True
    package = _packaging([_element("title", start=.2, end=.8, text="服务端媒介类型")])
    with pytest.raises(PackagingError, match="覆盖"):
        validate_final_events(package, [], [track], batch=False, asset_media_kinds={"asset": "video"})
    assert validate_final_events(package, [], [track], batch=False,
                                 asset_media_kinds={"asset": "image"})["elements"]

    snapshot = {"settings": {"width": 720, "height": 1280}, "tracks": [track], "packaging": package}
    proof = prepare_packaging_proof(snapshot, [], batch=False, asset_media_kinds={"asset": "image"})
    with pytest.raises(PackagingProofConflict):
        consume_packaging_proof(proof, snapshot, [], batch=False, asset_media_kinds={"asset": "video"})


def test_review_required_video_requires_real_frame_coverage_without_gaps_or_tail_extrapolation():
    """Removing a real frame interval, or using the sampling cadence as a hold, must block graphics."""
    from app.montage_packaging import PackagingError, _subject_boxes

    track = _reframed_track()[0]
    reframe = track["clips"][0]["reframe"]
    reframe.update(status="review_required", override={"mode": "manual", "keyframes": [{"timeSeconds": 0, "x": .5, "y": .5}]},
                   frameDurationSeconds=.04, samplingIntervalSeconds=.5,
                   samples=[{**sample, "timeSeconds": point} for sample, point in zip(reframe["samples"], (0, .5))])
    context = {"asset": "video"}
    with pytest.raises(PackagingError, match="覆盖"):
        _subject_boxes([track], .04, .08, 720, 1280, asset_media_kinds=context)
    with pytest.raises(PackagingError, match="覆盖"):
        _subject_boxes([track], .54, .58, 720, 1280, asset_media_kinds=context)

    # Consecutive real display-frame samples cover the half-open event exactly.
    reframe["samples"] = [{**reframe["samples"][0], "timeSeconds": point} for point in (0, .04, .08, .12)]
    assert _subject_boxes([track], .04, .12, 720, 1280, asset_media_kinds=context)


def test_review_required_mid_frame_event_projects_the_intersecting_frame_subject():
    """Coverage and occupancy must use the same half-open real-frame interval."""
    from app.montage_packaging import PackagingError, _subject_boxes, compile_ass, private_font_path

    track = _reframed_track()[0]
    clip = track["clips"][0]
    clip.update(inPoint=2, speed=.5)
    reframe = clip["reframe"]
    sample = reframe["samples"][0]
    sample.update(timeSeconds=2, boxes=[{
        "x": .2, "y": 0, "width": .6, "height": .25,
        "label": "person", "confidence": .99,
    }])
    reframe.update(status="review_required", samples=[sample], frameDurationSeconds=.04,
                   override={"mode": "center", "keyframes": []})
    context = {"asset": "video"}

    # Timeline .05-.075 maps to source 2.025-2.0375: wholly inside the
    # decoded frame [2.0, 2.04), while its PTS itself is outside the event.
    subjects = _subject_boxes([track], .05, .075, 720, 1280, asset_media_kinds=context)
    assert subjects and subjects[0][1] == 0
    package = _packaging([_element("keyword", start=.05, end=.075, position="top", text="帧内主体")])
    with pytest.raises(PackagingError, match="无可用安全区"):
        compile_ass(package, [], [track], 720, 1280, private_font_path(), asset_media_kinds=context)


def test_only_titles_receive_the_fixed_template_fade():
    from app.montage_packaging import compile_ass, private_font_path

    package = _packaging([_element("title", text="标题", position="top"), _element("keyword", text="关键词", position="bottom")])
    ass = compile_ass(package, [], [], 720, 1280, private_font_path(), asset_media_kinds={})
    title_line = next(line for line in ass.splitlines() if line.endswith("标题"))
    keyword_line = next(line for line in ass.splitlines() if line.endswith("关键词"))
    assert "\\fad(180,180)" in title_line
    assert "\\fad(" not in keyword_line


def test_short_title_scales_fade_to_leave_a_visible_middle_and_rejects_sub_precision_events():
    from app.montage_packaging import PackagingError, compile_ass, private_font_path, validate_packaging

    short = _packaging([_element("title", start=.2, end=.32, text="短标题")])
    ass = compile_ass(short, [], [], 720, 1280, private_font_path())
    assert "\\fad(40,40)" in ass

    too_short = _packaging([_element("title", start=.2, end=.28, text="不可见标题")])
    with pytest.raises(PackagingError, match="动态标题时长"):
        validate_packaging(too_short, [], [])

    sub_precision = _packaging([_element("keyword", start=.2, end=.204, text="零时长")])
    with pytest.raises(PackagingError, match="ASS 时间精度"):
        validate_packaging(sub_precision, [], [])


@pytest.mark.parametrize("mode", ["center", "manual", "replace"])
def test_explicit_reframe_risk_without_observed_subject_facts_blocks_packaging(mode):
    """A crop decision alone cannot prove a packaging event avoids subjects."""
    from app.montage_packaging import PackagingError, _subject_boxes

    track = _reframed_track()[0]
    reframe = track["clips"][0]["reframe"]
    reframe["samples"] = []
    reframe["status"] = "review_required"
    reframe["override"] = {"mode": mode, "keyframes": ([] if mode == "center" else [{"timeSeconds": 0, "x": .5, "y": .5}])}

    with pytest.raises(PackagingError, match="包装避让证据缺失"):
        _subject_boxes([track], .2, .8, 720, 1280)


@pytest.mark.parametrize(("sample_confidence", "boxes"), [
    (.9, []),
    (.9, [{"x": .2, "y": .2, "width": .2, "height": .2, "label": "person", "confidence": 0}]),
    (0, [{"x": .2, "y": .2, "width": .2, "height": .2, "label": "person", "confidence": .9}]),
])
def test_review_observation_without_a_positive_subject_box_cannot_authorize_packaging(sample_confidence, boxes):
    from app.montage_packaging import PackagingError, _subject_boxes

    track = _reframed_track()[0]
    reframe = track["clips"][0]["reframe"]
    reframe.update(status="review_required", samples=[{
        "timeSeconds": .5, "boxes": boxes, "center": {"x": .5, "y": .5}, "confidence": sample_confidence,
    }], override={"mode": "center", "keyframes": []})

    with pytest.raises(PackagingError, match="包装避让证据缺失"):
        _subject_boxes([track], .2, .8, 720, 1280)


@pytest.mark.parametrize("mode", ["center", "manual", "replace"])
def test_explicit_reframe_risk_decision_cannot_authorize_partial_frame_facts(mode):
    """A crop override does not convert one observation into an event interval."""
    from app.montage_packaging import PackagingError, _subject_boxes, compile_ass, private_font_path, validate_final_events

    track = _reframed_track()[0]
    reframe = track["clips"][0]["reframe"]
    reframe.update(status="review_required", uncertaintyReasons=["历史分析中断"], samples=reframe["samples"][:1])
    reframe["samples"][0]["timeSeconds"] = .5
    reframe["override"] = {"mode": mode, "keyframes": ([] if mode == "center" else [{"timeSeconds": 0, "x": .5, "y": .5}])}
    packaging = _packaging([_element("title", start=.2, end=.8, position="auto", text="人工决定")])

    # Ordinary compilation and batch preflight/export share the same hard
    # block; neither may interpolate or hold a sparse observation.
    with pytest.raises(PackagingError, match="完整覆盖"):
        _subject_boxes([track], .2, .8, 720, 1280)
    with pytest.raises(PackagingError, match="完整覆盖"):
        compile_ass(packaging, [], [track], 720, 1280, private_font_path())
    cues = [{"id": "cue", "start": .2, "end": .8, "text": "人工决定"}]
    with pytest.raises(PackagingError, match="完整覆盖"):
        validate_final_events(_packaging([]), cues, [track], batch=True)


@pytest.mark.parametrize("mode", ["center", "manual", "replace"])
def test_explicit_reframe_risk_blocks_partial_subject_facts_outside_packaging_event(mode):
    """Partial review facts are usable only when the event contains an observation."""
    from app.montage_packaging import PackagingError, _subject_boxes

    track = _reframed_track()[0]
    reframe = track["clips"][0]["reframe"]
    reframe.update(status="review_required", uncertaintyReasons=["历史分析中断"], samples=reframe["samples"][:1])
    reframe["samples"][0]["timeSeconds"] = .1
    reframe["override"] = {"mode": mode, "keyframes": ([] if mode == "center" else [{"timeSeconds": 0, "x": .5, "y": .5}])}

    with pytest.raises(PackagingError, match="包装避让证据缺失"):
        _subject_boxes([track], .2, .8, 720, 1280)


def test_auto_review_with_partial_samples_still_requires_pts_coverage():
    from app.montage_packaging import PackagingError, _subject_boxes

    track = _reframed_track()[0]
    reframe = track["clips"][0]["reframe"]
    reframe.update(status="review_required", uncertaintyReasons=["历史分析中断"], samples=reframe["samples"][:1])
    with pytest.raises(PackagingError, match="主体信息缺失需人工构图"):
        _subject_boxes([track], .2, .8, 720, 1280)


def test_auto_layout_avoids_a_moving_reframe_subject_inside_safe_area():
    from app.montage_packaging import compile_ass, private_font_path

    tracks = _reframed_track(box={"x": .35, "y": .65, "width": .3, "height": .3, "label": "person", "confidence": .9})
    packaging = _packaging([_element("subtitle", position="auto", text="底部主体")])

    ass = compile_ass(packaging, [], tracks, 720, 1280, private_font_path())

    assert "\\pos(360,86)" in ass


def test_transition_and_full_subject_occupancy_require_manual_review():
    from app.montage_packaging import PackagingError, compile_ass, private_font_path

    full = {"x": 0, "y": 0, "width": 1, "height": 1, "label": "person", "confidence": .9}
    tracks = _reframed_track(box=full)
    tracks[0]["clips"].append({**tracks[0]["clips"][0], "id": "next", "start": .5})
    packaging = _packaging([_element("cta", position="auto", text="立即行动")])

    with pytest.raises(PackagingError, match="人工"):
        compile_ass(packaging, [], tracks, 720, 1280, private_font_path())


def test_batch_cues_are_emitted_once_with_an_empty_packaging_template():
    from app.montage_packaging import compile_ass, private_font_path

    ass = compile_ass(_packaging([]), [{"id": "cue-one", "start": .2, "end": .8, "text": "唯一正本字幕"}], [], 720, 1280,
                      private_font_path())

    assert ass.count("唯一正本字幕") == 1
    assert "Dialogue: 0,0:00:00.20,0:00:00.80,G0" in ass


def test_default_batch_cue_is_cmap_checked_before_libass():
    from app.montage_packaging import PackagingError, compile_ass, private_font_path

    with pytest.raises(PackagingError, match="缺少"):
        compile_ass(_packaging([]), [{"id": "cue-one", "start": .2, "end": .8, "text": "\U00020000"}], [], 720, 1280,
                    private_font_path())


def test_subject_projection_uses_aspect_ratio_clip_time_manual_center_and_even_crop():
    from app.montage_packaging import _subject_boxes

    tracks = _reframed_track(start=5, duration=2, box={"x": .75, "y": .4, "width": .15, "height": .2,
                                                        "label": "person", "confidence": .9})
    clip = tracks[0]["clips"][0]
    clip["inPoint"], clip["speed"] = 2, 2
    clip["reframe"]["targetAspect"] = {"width": 9, "height": 16}  # 9:16, not 720:1280 exact dimensions.
    clip["reframe"]["samples"] = [
        {"timeSeconds": 2, "boxes": [{"x": .8, "y": .4, "width": .1, "height": .2, "label": "person", "confidence": .9}], "center": {"x": .1, "y": .5}, "confidence": .9},
        {"timeSeconds": 6, "boxes": [{"x": .8, "y": .4, "width": .1, "height": .2, "label": "person", "confidence": .9}], "center": {"x": .8, "y": .5}, "confidence": .9},
    ]
    clip["reframe"]["override"] = {"mode": "manual", "keyframes": [{"timeSeconds": 2, "x": .9, "y": .5}]}

    # Output 5.0 maps to source 2.0; later detection samples cannot block it.
    early = _subject_boxes(tracks, 5, 5.1, 720, 1280)
    assert early and min(box[0] for box in early) > 300
    # Manual centre, rather than the detector's .1 centre, keeps the .8 box in crop.


def test_multiline_real_advances_are_not_character_count_estimates():
    from app.montage_packaging import PackagingError, compile_ass, private_font_path

    # Full-width glyphs have a wider true advance than narrow Latin glyphs.
    narrow = compile_ass(_packaging([_element("title", text="iiii\niiii", position="top")]), [], [], 720, 1280,
                         private_font_path())
    wide = compile_ass(_packaging([_element("title", text="全全\n全全", position="top")]), [], [], 720, 1280,
                       private_font_path())
    assert "\\pos(360," in narrow and "\\pos(360," in wide
    with pytest.raises(PackagingError, match="文字超出"):
        compile_ass(_packaging([_element("title", text="全" * 40, position="top")]), [], [], 720, 1280,
                    private_font_path())


def test_subject_sweep_blocks_middle_crossing_and_keeps_appearing_boxes():
    from app.montage_packaging import PackagingError, _subject_boxes, compile_ass, private_font_path

    tracks = _reframed_track()
    reframe = tracks[0]["clips"][0]["reframe"]
    reframe["samples"] = [
        {"timeSeconds": 0, "boxes": [{"x": 0, "y": .42, "width": .12, "height": .18, "label": "person", "confidence": .9}], "center": {"x": .1, "y": .5}, "confidence": .9},
        {"timeSeconds": 1, "boxes": [{"x": .88, "y": .42, "width": .12, "height": .18, "label": "new-person", "confidence": .9}], "center": {"x": .9, "y": .5}, "confidence": .9},
    ]
    swept = _subject_boxes(tracks, 0, 1, 720, 1280)
    assert any(box[0] < 360 < box[2] for box in swept)
    with pytest.raises(PackagingError, match="人工"):
        compile_ass(_packaging([_element("subtitle", position="middle", text="穿越主体")]), [], tracks, 720, 1280,
                    private_font_path())

    # At a transition both clips contribute independently; no endpoint list is dropped.
    other = {**tracks[0]["clips"][0], "id": "transition-right", "start": .5,
             "reframe": {**reframe, "samples": [{"timeSeconds": 0, "boxes": [{"x": .42, "y": .1, "width": .16, "height": .2,
                 "label": "right", "confidence": .9}], "center": {"x": .5, "y": .5}, "confidence": .9}],
                 "frameDurationSeconds": .5}}
    tracks[0]["clips"].append(other)
    during_transition = _subject_boxes(tracks, .5, .6, 720, 1280)
    assert any(box[1] < 300 for box in during_transition) and any(box[0] < 360 < box[2] for box in during_transition)


def test_sweep_keeps_offscreen_endpoints_and_short_lifecycle_boxes_conservatively():
    from app.montage_packaging import _subject_boxes

    tracks = _reframed_track()
    reframe = tracks[0]["clips"][0]["reframe"]
    reframe["samples"] = [
        {"timeSeconds": 0, "boxes": [
            {"x": 0, "y": .45, "width": .05, "height": .1, "label": "A", "confidence": .9},
            {"x": .45, "y": .45, "width": .1, "height": .1, "label": "B", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1, "boxes": [{"x": .95, "y": .45, "width": .05, "height": .1,
                                          "label": "A", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
    ]
    # Both A endpoints are outside after target crop, but its sweep crosses;
    # B's disappearance remains occupied for this short interval too.
    occupied = _subject_boxes(tracks, .45, .55, 720, 1280)
    assert any(box[0] < 360 < box[2] for box in occupied)
    assert any(box[0] < 360 < box[2] and box[1] < 640 < box[3] for box in occupied)

    later = {**tracks[0]["clips"][0], "id": "later", "start": 2, "duration": 1}
    tracks[0]["clips"].append(later)
    assert _subject_boxes(tracks, 0, .2, 720, 1280)  # current clip only
    assert not _subject_boxes(tracks, 1.1, 1.2, 720, 1280)  # neither clip covers the gap


def test_subject_sweep_uses_only_the_event_subsegment_and_the_real_last_frame_hold():
    """A tail-frame subject and a late moving subject must not disappear or sweep all history."""
    from app.montage_packaging import PackagingError, _subject_boxes

    tracks = _reframed_track(duration=.51)
    reframe = tracks[0]["clips"][0]["reframe"]
    reframe["frameDurationSeconds"] = .05
    reframe["samples"] = [{
        "timeSeconds": .45,
        "boxes": [{"x": .45, "y": .42, "width": .1, "height": .18, "label": "person", "confidence": .9}],
        "center": {"x": .5, "y": .5}, "confidence": .9,
    }]

    # Task1's selected final frame owns [.45, .50), so a .48-.50 event
    # cannot silently place graphics on the detected subject.
    tail = _subject_boxes(tracks, .48, .50, 720, 1280)
    assert tail and any(box[0] < 360 < box[2] for box in tail)
    # The hold is finite; the clip's extra .01s has no detector authorization.
    with pytest.raises(PackagingError, match="时间覆盖未知"):
        _subject_boxes(tracks, .50, .51, 720, 1280)

    tracks = _reframed_track()
    reframe = tracks[0]["clips"][0]["reframe"]
    reframe["samples"] = [
        {"timeSeconds": 0, "boxes": [{"x": .05, "y": .42, "width": .1, "height": .18,
                                          "label": "person", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1, "boxes": [{"x": .85, "y": .42, "width": .1, "height": .18,
                                          "label": "person", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
    ]

    # The .9-1 event sees only the final tenth of the path; the old full
    # 0-1 union falsely occupied the centre.  A mid-path event remains blocked.
    late = _subject_boxes(tracks, .9, 1, 720, 1280)
    assert late and not any(box[0] < 360 < box[2] for box in late)
    middle = _subject_boxes(tracks, .45, .55, 720, 1280)
    assert any(box[0] < 360 < box[2] for box in middle)


def test_subject_sweep_never_pairs_unique_boxes_when_their_labels_change():
    """Changing the sole detector label leaves both identities occupied, not interpolated."""
    from app.montage_packaging import _subject_boxes

    tracks = _reframed_track()
    tracks[0]["clips"][0]["reframe"]["samples"] = [
        {"timeSeconds": 0, "boxes": [{"x": .28, "y": .42, "width": .1, "height": .18,
                                          "label": "A", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1, "boxes": [{"x": .62, "y": .42, "width": .1, "height": .18,
                                          "label": "B", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
    ]

    # Mutating production code back to unconditional one-box pairing must fail:
    # the short final event would otherwise contain only the interpolated B side.
    occupied = _subject_boxes(tracks, .9, 1, 720, 1280)
    assert any(box[0] < 120 < box[2] for box in occupied)  # A remains occupied.
    assert any(box[0] < 560 < box[2] for box in occupied)  # B remains occupied.


def test_subject_sweep_keeps_each_ambiguous_endpoint_and_both_transition_sides_in_a_short_event():
    """A+B→A must not zip-away B, and an overlapping clip is independently occupied."""
    from app.montage_packaging import _subject_boxes

    tracks = _reframed_track()
    reframe = tracks[0]["clips"][0]["reframe"]
    reframe["samples"] = [
        {"timeSeconds": 0, "boxes": [
            {"x": .05, "y": .42, "width": .1, "height": .18, "label": "A", "confidence": .9},
            {"x": .45, "y": .42, "width": .1, "height": .18, "label": "B", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1, "boxes": [
            {"x": .85, "y": .42, "width": .1, "height": .18, "label": "A", "confidence": .9}],
         "center": {"x": .5, "y": .5}, "confidence": .9},
    ]
    right = {**tracks[0]["clips"][0], "id": "right-clip", "start": .9, "duration": .1,
             "reframe": {**reframe, "frameDurationSeconds": .1, "samples": [{
                 "timeSeconds": 0, "boxes": [{"x": .42, "y": .1, "width": .16, "height": .2,
                                                  "label": "right", "confidence": .9}],
                 "center": {"x": .5, "y": .5}, "confidence": .9,
             }]}}
    tracks[0]["clips"].append(right)

    occupied = _subject_boxes(tracks, .92, .98, 720, 1280)
    assert any(box[0] < 360 < box[2] and box[1] < 640 < box[3] for box in occupied)  # disappearing B
    assert any(box[1] < 300 for box in occupied)  # incoming transition clip
