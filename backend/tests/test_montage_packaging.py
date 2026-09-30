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
    from app.montage_packaging import PackagingError, validate_packaging

    with pytest.raises(PackagingError, match="字体"):
        validate_packaging(_packaging([_element("title", text="𠀀")]), [], [])
    with pytest.raises(PackagingError, match="cueId"):
        validate_packaging(_packaging([{
            "id": "subtitle-one", "kind": "subtitle", "enabled": True, "cueId": "missing",
            "position": "bottom", "style": "plain",
        }]), [{"id": "cue-1", "start": 0, "end": 1, "text": "字幕"}], [], batch=True)
    monkeypatch.setattr(montage_packaging, "FONT_SHA256", "00" * 32)
    with pytest.raises(PackagingError, match="字体"):
        validate_packaging(_packaging([_element("title")]), [], [])


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
        ], "override": {"mode": "auto", "keyframes": []}},
    }]}]


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
                 "label": "right", "confidence": .9}], "center": {"x": .5, "y": .5}, "confidence": .9}]}}
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
