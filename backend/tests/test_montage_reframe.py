"""Reframe analysis contracts and FFmpeg crop trajectory behavior."""
from __future__ import annotations

import pytest
import sys
import time

from app.montage_reframe import MODEL_SHA256, ReframeError, normalize_reframe, pending_reframe, reframe_filter, run_local_detector


def _analysis(*, override=None, samples=None, status="ready"):
    return {
        "schemaVersion": 1,
        "sourceFingerprint": "a" * 64,
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "targetAspect": {"width": 9, "height": 16},
        "detectorVersion": "mediapipe-object-detector-0.10.21",
        "modelSha256": MODEL_SHA256,
        "samples": samples if samples is not None else [
            {"timeSeconds": 2.0, "boxes": [{"x": .1, "y": .1, "width": .2, "height": .5,
                                                "label": "person", "confidence": .9}],
             "center": {"x": .2, "y": .5}, "confidence": .9},
            {"timeSeconds": 4.0, "boxes": [{"x": .6, "y": .1, "width": .2, "height": .5,
                                                "label": "person", "confidence": .9}],
             "center": {"x": .7, "y": .5}, "confidence": .9},
        ],
        "uncertaintyReasons": [] if status == "ready" else ["尚未完成主体检测"], "status": status,
        "override": override or {"mode": "auto", "keyframes": []},
    }


def test_normalize_reframe_preserves_detection_facts_when_manual_override_is_selected():
    """Removing source boxes on a manual crop would hide the detector evidence."""
    reframe = normalize_reframe(_analysis(override={
        "mode": "manual", "keyframes": [{"timeSeconds": 2.0, "x": .8, "y": .4}],
    }), source_fingerprint="a" * 64, source_width=1920, source_height=1080)

    assert reframe["override"] == {"mode": "manual", "keyframes": [{"timeSeconds": 2.0, "x": .8, "y": .4}]}
    assert reframe["samples"][0]["boxes"][0]["label"] == "person"
    assert reframe["samples"][1]["center"] == {"x": .7, "y": .5}


@pytest.mark.parametrize("field, value", [
    ("sourceFingerprint", "different"),
    ("sourceWidth", 0),
])
def test_normalize_reframe_rejects_analysis_for_another_or_invalid_source(field, value):
    """A stale analysis must never be rendered against a different source."""
    analysis = _analysis()
    analysis[field] = value

    with pytest.raises(ReframeError):
        normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080)


def test_normalize_reframe_accepts_a_real_half_open_final_frame_only_when_sampling_cadence_covers_it():
    analysis = _analysis(samples=[
        {"timeSeconds": 0.0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.966667, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ])

    stored = normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                               required_start=0, required_end=2)

    assert stored["samples"][-1]["timeSeconds"] == 1.966667
    analysis["samples"][1]["timeSeconds"] = .1
    analysis["samples"][-1]["timeSeconds"] = .9
    with pytest.raises(ReframeError, match="未覆盖"):
        normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                          required_start=0, required_end=2)


def test_normalize_reframe_rejects_worker_timing_holes_and_long_tail_but_accepts_real_frame_tolerance():
    analysis = _analysis(samples=[
        {"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 10, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 19.958333, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ])
    analysis.update(frameDurationSeconds=1 / 24, samplingIntervalSeconds=.5)
    with pytest.raises(ReframeError, match="时间空洞"):
        normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                          required_start=0, required_end=20)

    analysis["samples"] = [
        {"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": .5, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.958333, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ]
    with pytest.raises(ReframeError, match="时间空洞"):
        normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                          required_start=0, required_end=2)

    analysis["samples"] = [
        {"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": .5, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.5, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.958333, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ]
    normalized = normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                                   required_start=0, required_end=2)
    assert normalized["samples"][-1]["timeSeconds"] == 1.958333


def test_reframe_filter_maps_output_time_to_source_time_and_crops_before_scaling():
    """Applying crop after speed conversion would track the wrong source instant."""
    graph = reframe_filter(
        _analysis(), source_width=1920, source_height=1080, output_width=720, output_height=1280,
        in_point=2, speed=2, duration=1,
    )

    assert "crop@reframe=" in graph
    assert "sendcmd=" in graph
    assert "crop@reframe=" in graph and ",scale=720:1280" in graph
    assert "0.0000 crop@reframe x " in graph
    assert "1.0000 crop@reframe x " in graph


def test_reframe_filter_rejects_review_required_auto_analysis_but_accepts_center_risk_override():
    """Unknown automatic framing must block render unless the user explicitly accepts center framing."""
    pending = _analysis(samples=[], status="review_required")
    with pytest.raises(ReframeError, match="待人工"):
        reframe_filter(pending, source_width=1920, source_height=1080, output_width=720, output_height=1280,
                       in_point=0, speed=1, duration=1)

    pending["override"] = {"mode": "center", "keyframes": []}
    graph = reframe_filter(pending, source_width=1920, source_height=1080, output_width=720, output_height=1280,
                           in_point=0, speed=1, duration=1)
    assert "crop@reframe" in graph


def test_pending_reframe_binds_center_override_to_the_current_real_source_without_inventing_detection_facts():
    """A deliberately accepted risk must not be rejected just because no detector ran yet."""
    pending = pending_reframe(target_width=9, target_height=16, reason="检测尚未完成")
    pending["override"] = {"mode": "center", "keyframes": []}

    graph = reframe_filter(pending, source_width=1920, source_height=1080, output_width=720, output_height=1280,
                           in_point=0, speed=1, duration=1, source_hash="c" * 64)

    assert "crop@reframe" in graph
    assert pending["samples"] == []
    assert pending["sourceFingerprint"] == ""


def test_local_detector_spools_large_child_output_and_reaps_cancelled_child(tmp_path):
    source, model = tmp_path / "source.mp4", tmp_path / "model.tflite"
    source.write_bytes(b"source")
    model.write_bytes(b"model")
    writer = tmp_path / "writer.py"
    writer.write_text("import json; print(json.dumps({'payload': 'x' * 200000}))", encoding="utf-8")
    result = run_local_detector(source, start=0, end=1, target_width=9, target_height=16,
                                worker_python=sys.executable, worker_path=writer, model_path=model)
    assert len(result["payload"]) == 200000

    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time; time.sleep(30)", encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(ReframeError, match="取消"):
        run_local_detector(source, start=0, end=1, target_width=9, target_height=16,
                           worker_python=sys.executable, worker_path=sleeper, model_path=model, cancelled=lambda: True)
    assert time.monotonic() - started < 6
