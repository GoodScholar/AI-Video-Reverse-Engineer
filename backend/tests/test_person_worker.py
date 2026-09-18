import importlib.util
import json
from pathlib import Path

import pytest


WORKER = Path(__file__).parents[1] / "person_worker" / "run_person.py"


def _worker_module():
    spec = importlib.util.spec_from_file_location("person_worker", WORKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quality_requires_review_for_missing_or_multiple_people():
    worker = _worker_module()

    quality = worker.assess_quality(
        frame_count=4,
        detected_frame_count=3,
        missing_times_seconds=[0.125],
        multiple_person_times_seconds=[0.25],
    )

    assert quality["status"] == "review_required"
    assert quality["frameCount"] == 4
    assert quality["detectedFrameCount"] == 3
    assert quality["missingTimesSeconds"] == [0.125]
    assert quality["multiplePersonTimesSeconds"] == [0.25]


def test_quality_fails_when_no_person_is_detected():
    worker = _worker_module()

    quality = worker.assess_quality(
        frame_count=2,
        detected_frame_count=0,
        missing_times_seconds=[0.0, 0.125],
        multiple_person_times_seconds=[],
    )

    assert quality["status"] == "failed"
    assert "未检测到人物" in quality["message"]


def test_quality_requires_review_for_low_confidence_invalid_landmarks_or_empty_masks():
    worker = _worker_module()

    quality = worker.assess_quality(
        frame_count=3,
        detected_frame_count=3,
        missing_times_seconds=[],
        multiple_person_times_seconds=[],
        low_confidence_times_seconds=[0.125],
        invalid_landmark_times_seconds=[0.25],
        unusable_mask_times_seconds=[0.0],
    )

    assert quality["status"] == "review_required"
    assert quality["lowConfidenceTimesSeconds"] == [0.125]
    assert quality["invalidLandmarkTimesSeconds"] == [0.25]
    assert quality["unusableMaskTimesSeconds"] == [0.0]


def test_render_helpers_draw_a_black_pose_frame_and_binary_mask():
    np = pytest.importorskip("numpy", reason="渲染测试只在独立人物 worker 运行时执行")
    pytest.importorskip("cv2", reason="渲染测试只在独立人物 worker 运行时执行")

    worker = _worker_module()
    frame = np.full((24, 32, 3), 80, dtype=np.uint8)
    landmarks = [
        {"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.9, "presence": 0.9}
        for _ in range(33)
    ]
    mask = np.array([[0.49, 0.5], [0.9, 0.0]], dtype=np.float32)

    pose = worker.render_pose(frame.shape, landmarks)
    binary = worker.threshold_mask(mask, width=32, height=24)
    overlay = worker.render_overlay(frame, landmarks, binary)

    assert pose.shape == frame.shape and pose.max() > 0
    assert set(np.unique(binary)) == {0, 255}
    assert overlay.shape == frame.shape and not np.array_equal(overlay, frame)


def test_landmark_record_has_stable_empty_frame_shape():
    worker = _worker_module()

    record = worker.landmark_record(0.125, None, 0)

    assert json.loads(json.dumps(record)) == {
        "timeSeconds": 0.125,
        "landmarks33": None,
        "presence": None,
        "visibility": None,
        "personCount": 0,
    }
