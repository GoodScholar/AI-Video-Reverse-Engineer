"""Reframe analysis contracts and FFmpeg crop trajectory behavior."""
from __future__ import annotations

import pytest
import sys
import time
from types import SimpleNamespace

from app import montage_reframe
from app.montage_reframe import MODEL_SHA256, ReframeError, SourceTiming, normalize_reframe, pending_reframe, probe_source_timing, reframe_filter, retain_server_reframe_facts, run_local_detector


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


@pytest.mark.parametrize("field,value", [("assetId", "replacement"), ("inPoint", 1), ("duration", 3), ("speed", 1.25)])
def test_replacement_or_source_range_change_resets_server_reframe_to_pending(field, value):
    """A stable clip id must never carry detector facts to another source identity."""
    existing = [{"id": "v", "clips": [{"id": "clip", "assetId": "original", "inPoint": 0, "duration": 2, "speed": 1, "reframe": _analysis()}]}]
    submitted_clip = {"id": "clip", "assetId": "original", "inPoint": 0, "duration": 2, "speed": 1, "reframe": None}
    submitted_clip[field] = value
    result = retain_server_reframe_facts(existing, [{"id": "v", "clips": [submitted_clip]}])
    reframe = result[0]["clips"][0]["reframe"]
    assert reframe["status"] == "review_required"
    assert reframe["sourceFingerprint"] == ""
    assert reframe["samples"] == []
    assert reframe["override"] == {"mode": "auto", "keyframes": []}
    assert "素材或源时间已改变" in reframe["uncertaintyReasons"][0]


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

    assert stored["status"] == "review_required"
    analysis["samples"][1]["timeSeconds"] = .1
    analysis["samples"][-1]["timeSeconds"] = .9
    assert normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                             required_start=0, required_end=2)["status"] == "review_required"


def test_normalize_reframe_rejects_worker_timing_holes_and_long_tail_but_accepts_real_frame_tolerance():
    analysis = _analysis(samples=[
        {"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 10, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 19.958333, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ])
    analysis.update(frameDurationSeconds=1 / 24, samplingIntervalSeconds=.5)
    assert normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                             required_start=0, required_end=20)["status"] == "review_required"

    analysis["samples"] = [
        {"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": .5, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.958333, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ]
    assert normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                             required_start=0, required_end=2)["status"] == "review_required"

    analysis["samples"] = [
        {"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": .5, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.5, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 1.958333, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ]
    normalized = normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                                   required_start=0, required_end=2)
    assert normalized["status"] == "review_required"


def test_reframe_filter_maps_output_time_to_source_time_and_crops_before_scaling():
    """Applying crop after speed conversion would track the wrong source instant."""
    graph = reframe_filter(
        _analysis(override={"mode": "manual", "keyframes": [{"timeSeconds": 2, "x": .2, "y": .5}, {"timeSeconds": 4, "x": .8, "y": .5}]}), source_width=1920, source_height=1080, output_width=720, output_height=1280,
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


def test_normalize_reframe_requires_server_pts_evidence_for_auto_ready():
    """客户端声明的宽松 cadence 不能让 [0,10] 冒充 [0,20)。"""
    analysis = _analysis(samples=[
        {"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 10, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ])
    analysis.update(frameDurationSeconds=1, samplingIntervalSeconds=2)
    timing = SourceTiming((0.0, 10.0, 19.958333), (.041667, .041667, .041667), True, None)

    stored = normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                               source_timing=timing, required_start=0, required_end=20)

    assert stored["status"] == "review_required"
    assert any("需重新分析源时间" in reason for reason in stored["uncertaintyReasons"])


def test_normalize_reframe_uses_the_same_integer_half_open_boundary_as_the_worker():
    """A non-zero container PTS can encode 2.1 as 4.1 - 2.0 just below 2.1 in binary."""
    analysis = _analysis(samples=[
        {"timeSeconds": 2.033334, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
        {"timeSeconds": 2.066667, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
    ])
    timing = SourceTiming((0.0, 4.033334 - 2.0, 4.066667 - 2.0, 4.1 - 2.0),
                          (.033333, .033333, .033333, .033333), True, None)

    stored = normalize_reframe(analysis, source_fingerprint="a" * 64, source_width=1920, source_height=1080,
                               source_timing=timing, required_start=2.0, required_end=2.1)

    assert stored["status"] == "ready"


def test_probe_rejects_vfr_from_pts_even_when_nominal_rates_would_agree(monkeypatch, tmp_path):
    """avg/r 元数据并未参与授权；逐帧 PTS 不均必须转人工。"""
    payload = {"streams": [{"start_time": "2", "duration": "1"}], "frames": [
        {"best_effort_timestamp_time": "2.000", "pkt_duration_time": ".04"},
        {"best_effort_timestamp_time": "2.040", "pkt_duration_time": ".04"},
        {"best_effort_timestamp_time": "2.100", "pkt_duration_time": ".04"},
    ]}
    monkeypatch.setattr(montage_reframe.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=__import__("json").dumps(payload)))

    timing = probe_source_timing(tmp_path / "nominal-cfr.mp4", "ffprobe")

    assert not timing.cfr
    assert "可变帧率" in timing.reason
