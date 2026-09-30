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
    assert "Plain,NotoSansCJKsc-Regular" in result
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

    assert "\\pos(360,95)" in ass


def test_transition_and_full_subject_occupancy_require_manual_review():
    from app.montage_packaging import PackagingError, compile_ass, private_font_path

    full = {"x": 0, "y": 0, "width": 1, "height": 1, "label": "person", "confidence": .9}
    tracks = _reframed_track(box=full)
    tracks[0]["clips"].append({**tracks[0]["clips"][0], "id": "next", "start": .5})
    packaging = _packaging([_element("cta", position="auto", text="立即行动")])

    with pytest.raises(PackagingError, match="人工"):
        compile_ass(packaging, [], tracks, 720, 1280, private_font_path())
