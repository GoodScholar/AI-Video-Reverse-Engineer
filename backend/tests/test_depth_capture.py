import pytest
from pydantic import ValidationError

from app.depth_capture import (
    DEPTH_STAGE_ORDER,
    DepthCapture,
    DepthCaptureStage,
    DepthModelIdentity,
    DepthOutputSummary,
    new_depth_capture,
    select_execution_device,
)


def test_new_depth_capture_has_fixed_stage_order():
    capture = new_depth_capture("video-1", "auto", "2026-09-12T00:00:00+00:00")

    assert [stage.name for stage in capture.stages] == [
        "preparing", "estimatingDepth", "encoding", "qualityAssessment"
    ]
    assert capture.status == "queued"
    assert capture.outputResolution == "480p"


def test_new_depth_capture_records_requested_output_resolution_without_rewriting_legacy_captures():
    capture = new_depth_capture("video-1", "auto", "2026-09-12T00:00:00+00:00", "720p")

    assert capture.outputResolution == "720p"
    assert _depth_capture().outputResolution is None


def test_auto_device_prefers_cuda_then_mps_then_cpu():
    assert select_execution_device("auto", cuda=True, mps=True) == "cuda"
    assert select_execution_device("auto", cuda=False, mps=True) == "mps"
    assert select_execution_device("auto", cuda=False, mps=False) == "cpu"


def test_explicit_unavailable_device_is_rejected():
    with pytest.raises(ValueError, match="所选深度计算设备不可用"):
        select_execution_device("cuda", cuda=False, mps=True)


def test_explicit_unavailable_mps_device_is_rejected():
    with pytest.raises(ValueError, match="所选深度计算设备不可用"):
        select_execution_device("mps", cuda=True, mps=False)


def test_new_depth_capture_records_fixed_reproducibility_contract():
    capture = new_depth_capture("video-1", "auto", "2026-09-12T00:00:00+00:00")

    assert capture.algorithmVersion == 1
    assert capture.modelIdentity.modelId == "video-depth-anything-small-relative"
    assert capture.modelIdentity.upstreamCommit == "4f5ae23172ba60fd7bc11ef671cca678842c7072"
    assert capture.modelIdentity.checkpointSha256 == (
        "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"
    )
    assert capture.normalizationDirection == "near_white_far_black"
    assert capture.outputSummary is None


def test_depth_model_identity_is_immutable():
    identity = DepthModelIdentity()

    with pytest.raises(ValidationError, match="frozen_instance"):
        identity.modelId = "another-model"


@pytest.mark.parametrize(
    "stage_names",
    [
        [],
        ["preparing", "encoding", "estimatingDepth", "qualityAssessment"],
        ["preparing", "estimatingDepth", "encoding", "encoding"],
    ],
)
def test_depth_capture_rejects_missing_duplicate_or_unordered_stages(stage_names):
    with pytest.raises(ValidationError, match="深度捕捉阶段顺序无效"):
        _depth_capture(stages=[DepthCaptureStage(name=name) for name in stage_names])


def test_depth_capture_defaults_algorithm_version_and_validates_ids_and_timestamps():
    assert _depth_capture().algorithmVersion == 1

    with pytest.raises(ValidationError, match="存储标识符必须是安全的单段名称"):
        _depth_capture(id="../capture-1")
    with pytest.raises(ValidationError, match="项目时间戳必须包含时区"):
        _depth_capture(queuedAt="2026-09-12T00:00:00")


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 0),
        ("height", 0),
        ("frameRate", 0),
        ("frameCount", -1),
        ("durationSeconds", -0.1),
    ],
)
def test_depth_output_summary_rejects_invalid_dimensions_or_timing(field, value):
    payload = {
        "width": 1280,
        "height": 720,
        "frameRate": 24.0,
        "frameCount": 24,
        "durationSeconds": 1.0,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        DepthOutputSummary(**payload)


def _depth_capture(**overrides) -> DepthCapture:
    payload = {
        "id": "capture-1",
        "sourceReferenceVideoId": "video-1",
        "status": "queued",
        "devicePreference": "auto",
        "stages": [DepthCaptureStage(name=name) for name in DEPTH_STAGE_ORDER],
        "queuedAt": "2026-09-12T00:00:00+00:00",
        "updatedAt": "2026-09-12T00:00:00+00:00",
    }
    payload.update(overrides)
    return DepthCapture(**payload)
