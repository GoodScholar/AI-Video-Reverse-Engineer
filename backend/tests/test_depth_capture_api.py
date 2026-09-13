import json
from datetime import datetime, timezone
from threading import Barrier, Event, Thread

import pytest
from fastapi.testclient import TestClient

from app import main
from app.depth_capture import DepthOutputSummary, DepthQualityAssessment, DepthQualityCheck, new_depth_capture
from app.depth_capture_runner import DepthCaptureFailure, DepthCaptureRunResult
from app.depth_capture_storage import DEPTH_ARTIFACTS
from app.local_preprocessing import (
    AnalysisProxySummary,
    ReproducibilityAssessment,
    new_local_preprocessing,
)
from app.main import create_app
from app.reference_video import ReferenceVideo
from app.reference_video import ProbedReferenceVideo
from app.reference_media_storage import managed_reference_media_is_safe


class ManualDepthQueue:
    def __init__(self, handler):
        self.handler = handler
        self.pending = []
        self.closed = False

    def submit(self, project_id):
        self.pending.append(project_id)
        return True

    def is_active(self, project_id):
        return project_id in self.pending

    def shutdown(self):
        self.closed = True


class ManualComputeQueue:
    def __init__(self):
        self.pending = []
        self.closed = False

    def submit(self, kind, project_id, handler):
        if self.closed or any(key == (kind, project_id) for key, _ in self.pending):
            return False
        self.pending.append(((kind, project_id), handler))
        return True

    def is_active(self, kind, project_id):
        return any(key == (kind, project_id) for key, _ in self.pending)

    def shutdown(self):
        self.closed = True

    def run_next(self):
        _, handler = self.pending.pop(0)
        handler("project-001")


def _completed_project(tmp_path, *, assessment="pending_semantic_confirmation"):
    now = datetime.now(timezone.utc).isoformat()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2, width=640, height=360, frameRate=24,
    )
    preprocessing = new_local_preprocessing("preprocess-001", reference.id, datetime.now(timezone.utc))
    for stage in preprocessing.stages:
        stage.status = "completed"
        stage.startedAt = now
        stage.completedAt = now
    preprocessing.status = "completed"
    preprocessing.completedAt = now
    preprocessing.proxySummary = AnalysisProxySummary(
        keyframeCount=4, sceneChangeCount=0, motionP50=None, motionP90=None,
        motionPeak=None, motionLevel="unavailable",
    )
    preprocessing.reproducibilityAssessment = ReproducibilityAssessment(status=assessment, checks=[])
    project = {
        "id": "project-001", "name": "深度项目", "createdAt": now, "updatedAt": now,
        "referenceMedia": reference.model_dump(), "localPreprocessing": preprocessing.model_dump(),
    }
    (tmp_path / "projects.json").write_text(json.dumps([project]), encoding="utf-8")
    video_dir = tmp_path / "project-files/project-001/reference-videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "video-001.mp4").write_bytes(b"video")
    directory = tmp_path / "project-files/project-001/local-preprocessing/preprocess-001"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("decode.json", "scene-changes.json", "motion.json", "analysis-proxy.json"):
        (directory / name).write_text('{"done":true}', encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps({
        "schemaVersion": 1, "algorithmVersion": 1, "sourceReferenceVideoId": "video-001",
    }), encoding="utf-8")
    (directory / "contact-sheet.jpg").write_bytes(b"sheet")
    frames = directory / "keyframes"
    frames.mkdir(exist_ok=True)
    for index in range(1, 5):
        (frames / f"frame-{index:04d}.jpg").write_bytes(b"frame")
    return project


def _write_depth_artifacts(tmp_path, capture):
    directory = tmp_path / "project-files/project-001/depth-captures" / capture.id
    directory.mkdir(parents=True)
    for name in DEPTH_ARTIFACTS:
        if name == "manifest.json":
            (directory / name).write_text(json.dumps({
                "schemaVersion": 1, "algorithmVersion": 1,
                "sourceReferenceVideoId": "video-001",
                "modelIdentity": capture.modelIdentity.model_dump(),
                "normalizationDirection": "near_white_far_black",
                "files": list(DEPTH_ARTIFACTS),
            }), encoding="utf-8")
        else:
            (directory / name).write_bytes(b"content")


def _assessment(status):
    states = ["passed"] * 6
    if status == "review_required":
        states[2] = status
    elif status == "failed":
        states[0] = status
    return DepthQualityAssessment(status=status, checks=[
        DepthQualityCheck(
            criterion=criterion, status=check_status, message="检查", evidence="测试",
            metric=0, threshold=1, sampleTimestamps=[],
        )
        for criterion, check_status in zip(
            ("completeness", "dynamicRange", "temporalFlicker", "directionStability", "edgeContinuity", "timelineAlignment"),
            states,
        )
    ])


def _successful_runner(tmp_path, quality_status):
    def run(*, request, on_stage_started, on_stage_completed, **_):
        for stage in ("preparing", "estimatingDepth", "encoding", "qualityAssessment"):
            on_stage_started(stage)
            on_stage_completed(stage)
        capture = new_depth_capture(request.source_reference_video_id, "auto", datetime.now(timezone.utc).isoformat())
        capture.id = request.capture_id
        _write_depth_artifacts(tmp_path, capture)
        return DepthCaptureRunResult(
            directory=tmp_path, executionDevice=request.execution_device, frameCount=16,
            width=640, height=360, frameRate=8, qualityAssessment=_assessment(quality_status),
        )
    return run


def _probe_reference(path, original_name, size_bytes, **_):
    return ProbedReferenceVideo(
        format="mp4", duration_seconds=2, width=640, height=360, frame_rate=24,
    )


def _persist_capture(tmp_path, capture, *, active=None):
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    payload[0]["depthCaptures"] = [capture.model_dump()]
    if active is not None:
        payload[0]["activeDepthCaptureId"] = active
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")


def _completed_capture(tmp_path, *, quality="review_required", source="video-001"):
    project = _completed_project(tmp_path)
    capture = new_depth_capture(source, "auto", project["createdAt"])
    capture.status = "completed"
    capture.executionDevice = "cpu"
    capture.completedAt = project["createdAt"]
    capture.updatedAt = project["createdAt"]
    capture.qualityAssessment = _assessment(quality)
    capture.outputSummary = DepthOutputSummary(
        width=640, height=360, frameRate=8, frameCount=16, durationSeconds=2,
    )
    for stage in capture.stages:
        stage.status = "completed"
        stage.startedAt = project["createdAt"]
        stage.completedAt = project["createdAt"]
    _write_depth_artifacts(tmp_path, capture)
    _persist_capture(tmp_path, capture)
    return capture


def test_start_depth_capture_queues_after_preconditions(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=queue))

    response = client.post("/api/projects/project-001/depth-captures", json={"devicePreference": "auto"})

    assert response.status_code == 202
    assert response.json()["depthCaptures"][-1]["status"] == "queued"
    assert queue.pending[0][0] == ("depth_capture", "project-001")


def test_start_depth_capture_requires_completed_preprocessing(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))
    project = client.post("/api/projects", json={"name": "没有预处理"}).json()

    response = client.post(f"/api/projects/{project['id']}/depth-captures", json={"devicePreference": "auto"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_capture_preconditions_not_met"


@pytest.mark.parametrize("mutation", ["source", "out_of_scope", "assessment", "artifacts"])
def test_start_rejects_each_completed_preprocessing_precondition(tmp_path, mutation):
    _completed_project(tmp_path)
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    preprocessing = payload[0]["localPreprocessing"]
    if mutation == "source":
        preprocessing["sourceReferenceVideoId"] = "video-other"
        manifest = tmp_path / "project-files/project-001/local-preprocessing/preprocess-001/manifest.json"
        manifest.write_text(json.dumps({
            "schemaVersion": 1, "algorithmVersion": 1, "sourceReferenceVideoId": "video-other",
        }), encoding="utf-8")
    elif mutation == "out_of_scope":
        preprocessing["reproducibilityAssessment"]["status"] = "out_of_scope"
    elif mutation == "assessment":
        preprocessing["reproducibilityAssessment"] = None
    else:
        (tmp_path / "project-files/project-001/local-preprocessing/preprocess-001/decode.json").unlink()
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))

    response = client.post("/api/projects/project-001/depth-captures", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_capture_preconditions_not_met"


def test_start_requires_existing_non_symlink_managed_reference_file(tmp_path):
    _completed_project(tmp_path)
    (tmp_path / "project-files/project-001/reference-videos/video-001.mp4").unlink()
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))

    response = client.post("/api/projects/project-001/depth-captures", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_capture_preconditions_not_met"


def test_managed_reference_safety_rejects_a_symlinked_video(tmp_path):
    _completed_project(tmp_path)
    source = tmp_path / "project-files/project-001/reference-videos/video-001.mp4"
    source.unlink()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    source.symlink_to(outside)
    video = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2, width=640, height=360, frameRate=24,
    )

    assert managed_reference_media_is_safe(tmp_path, "project-001", video) is False


def test_start_rejects_symlinked_reference_videos_ancestor(tmp_path):
    _completed_project(tmp_path)
    directory = tmp_path / "project-files/project-001/reference-videos"
    source = directory / "video-001.mp4"
    source.unlink()
    directory.rmdir()
    outside = tmp_path / "outside-videos"
    outside.mkdir()
    (outside / "video-001.mp4").write_bytes(b"outside")
    directory.symlink_to(outside, target_is_directory=True)
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))

    response = client.post("/api/projects/project-001/depth-captures", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_capture_preconditions_not_met"


def test_start_maps_device_probe_failure_to_stable_service_error(tmp_path):
    _completed_project(tmp_path)

    def unavailable_probe():
        raise RuntimeError("private driver diagnostics")

    client = TestClient(create_app(
        data_dir=tmp_path, local_compute_queue=ManualComputeQueue(), depth_device_probe=unavailable_probe,
    ))
    response = client.post("/api/projects/project-001/depth-captures", json={})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "depth_device_probe_unavailable"


@pytest.mark.parametrize("preference", ["cuda", "mps"])
def test_start_rejects_explicit_unavailable_device(tmp_path, preference):
    _completed_project(tmp_path)
    client = TestClient(create_app(
        data_dir=tmp_path, local_compute_queue=ManualComputeQueue(), depth_device_probe=lambda: (False, False),
    ))

    response = client.post("/api/projects/project-001/depth-captures", json={"devicePreference": preference})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_device_unavailable"


def test_review_confirmation_only_accepts_review_required(tmp_path):
    project = _completed_project(tmp_path)
    capture = new_depth_capture("video-001", "auto", project["createdAt"])
    capture.status = "completed"
    capture.completedAt = project["createdAt"]
    capture.updatedAt = project["createdAt"]
    _write_depth_artifacts(tmp_path, capture)
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    payload[0]["depthCaptures"] = [capture.model_dump()]
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))

    response = client.post(f"/api/projects/project-001/depth-captures/{capture.id}/confirm-review")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_review_not_required"


def test_confirm_review_is_idempotent_for_a_valid_review_capture(tmp_path):
    capture = _completed_capture(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))

    first = client.post(f"/api/projects/project-001/depth-captures/{capture.id}/confirm-review")
    second = client.post(f"/api/projects/project-001/depth-captures/{capture.id}/confirm-review")

    assert first.status_code == second.status_code == 200
    assert first.json()["activeDepthCaptureId"] == second.json()["activeDepthCaptureId"] == capture.id
    assert first.json()["depthCaptures"][0]["reviewConfirmedAt"] == second.json()["depthCaptures"][0]["reviewConfirmedAt"]


@pytest.mark.parametrize(("case", "expected_status", "expected_code"), [
    ("missing", 404, "depth_capture_not_found"),
    ("technical", 409, "depth_capture_not_completed"),
    ("quality_failed", 409, "depth_review_not_required"),
    ("passed", 409, "depth_review_not_required"),
    ("stale", 409, "depth_capture_stale_reference"),
    ("corrupt", 409, "depth_capture_artifacts_invalid"),
])
def test_confirm_review_rejects_invalid_capture_states(tmp_path, case, expected_status, expected_code):
    capture = _completed_capture(tmp_path, quality="review_required")
    if case == "technical":
        payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
        payload[0]["depthCaptures"][0]["status"] = "failed"
        (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    elif case == "quality_failed":
        payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
        payload[0]["depthCaptures"][0]["qualityAssessment"] = _assessment("failed").model_dump()
        (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    elif case == "passed":
        payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
        payload[0]["depthCaptures"][0]["qualityAssessment"] = _assessment("passed").model_dump()
        (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    elif case == "stale":
        payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
        payload[0]["referenceMedia"]["id"] = "video-current"
        (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))
    if case == "corrupt":
        (tmp_path / "project-files/project-001/depth-captures" / capture.id / "depth-control.mp4").unlink()
    capture_id = "unknown-capture" if case == "missing" else capture.id

    response = client.post(f"/api/projects/project-001/depth-captures/{capture_id}/confirm-review")

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code


def test_completed_passed_capture_is_activated_after_runner_finishes(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()
    client = TestClient(create_app(
        data_dir=tmp_path, local_compute_queue=queue,
        depth_capture_runner=_successful_runner(tmp_path, "passed"),
    ))
    queued = client.post("/api/projects/project-001/depth-captures", json={}).json()

    queue.run_next()
    completed = client.get("/api/projects/project-001").json()

    assert completed["depthCaptures"][-1]["status"] == "completed"
    assert completed["activeDepthCaptureId"] == queued["depthCaptures"][-1]["id"]


def test_review_required_capture_needs_confirmation_before_activation(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()
    client = TestClient(create_app(
        data_dir=tmp_path, local_compute_queue=queue,
        depth_capture_runner=_successful_runner(tmp_path, "review_required"),
    ))
    queued = client.post("/api/projects/project-001/depth-captures", json={}).json()
    capture_id = queued["depthCaptures"][-1]["id"]

    queue.run_next()
    before = client.get("/api/projects/project-001").json()
    confirmed = client.post(f"/api/projects/project-001/depth-captures/{capture_id}/confirm-review")

    assert before["activeDepthCaptureId"] is None
    assert confirmed.status_code == 200
    assert confirmed.json()["activeDepthCaptureId"] == capture_id


def test_failed_quality_capture_is_not_reused_and_can_be_queued_again(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()
    client = TestClient(create_app(
        data_dir=tmp_path, local_compute_queue=queue,
        depth_capture_runner=_successful_runner(tmp_path, "failed"),
    ))
    client.post("/api/projects/project-001/depth-captures", json={})
    queue.run_next()

    retried = client.post("/api/projects/project-001/depth-captures", json={})

    assert retried.status_code == 202
    assert len(retried.json()["depthCaptures"]) == 2
    first = client.get("/api/projects/project-001").json()["depthCaptures"][0]
    assert first["status"] == "completed"
    assert client.get("/api/projects/project-001").json()["activeDepthCaptureId"] is None


def test_runner_result_without_valid_committed_artifacts_is_failed_not_activated(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()

    def runner(*, request, on_stage_started, on_stage_completed, **_):
        for stage in ("preparing", "estimatingDepth", "encoding", "qualityAssessment"):
            on_stage_started(stage)
            on_stage_completed(stage)
        return DepthCaptureRunResult(
            directory=tmp_path, executionDevice="cpu", frameCount=16, width=640, height=360,
            frameRate=8, qualityAssessment=_assessment("passed"),
        )

    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=queue, depth_capture_runner=runner))
    client.post("/api/projects/project-001/depth-captures", json={})
    queue.run_next()

    capture = client.get("/api/projects/project-001").json()["depthCaptures"][-1]
    assert capture["status"] == "failed"
    assert capture["error"]["code"] == "depth_capture_artifacts_invalid"


def test_runner_failure_records_the_actual_callback_stage(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()

    def runner(*, on_stage_started, on_stage_completed, **_):
        on_stage_started("preparing")
        on_stage_completed("preparing")
        on_stage_started("estimatingDepth")
        on_stage_completed("estimatingDepth")
        on_stage_started("encoding")
        raise DepthCaptureFailure("depth_encoding_failed", "编码失败")

    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=queue, depth_capture_runner=runner))
    client.post("/api/projects/project-001/depth-captures", json={})
    queue.run_next()

    capture = client.get("/api/projects/project-001").json()["depthCaptures"][-1]
    assert capture["status"] == "failed"
    assert capture["currentStage"] == "encoding"
    assert capture["error"]["stage"] == "encoding"


def test_startup_marks_queued_capture_interrupted(tmp_path):
    project = _completed_project(tmp_path)
    capture = new_depth_capture("video-001", "auto", project["createdAt"])
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    payload[0]["depthCaptures"] = [capture.model_dump()]
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")

    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))
    reconciled = client.get("/api/projects/project-001").json()["depthCaptures"][0]

    assert reconciled["status"] == "failed"
    assert reconciled["error"]["code"] == "depth_capture_interrupted"
    persisted = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))[0]
    assert persisted["updatedAt"] == persisted["depthCaptures"][0]["updatedAt"]


@pytest.mark.parametrize("status", ["queued", "running"])
def test_interrupted_capture_statuses_become_retryable_and_can_restart(tmp_path, status):
    project = _completed_project(tmp_path)
    capture = new_depth_capture("video-001", "auto", project["createdAt"])
    capture.status = status
    if status == "running":
        capture.startedAt = project["createdAt"]
        capture.currentStage = "preparing"
        capture.stages[0].status = "running"
        capture.stages[0].startedAt = project["createdAt"]
    _persist_capture(tmp_path, capture)
    queue = ManualComputeQueue()
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=queue))

    reconciled = client.get("/api/projects/project-001").json()["depthCaptures"][0]
    retry = client.post("/api/projects/project-001/depth-captures", json={})

    assert reconciled["status"] == "failed"
    assert reconciled["error"]["code"] == "depth_capture_interrupted"
    assert retry.status_code == 202


def test_technical_failure_can_be_retried(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()

    def broken(**_):
        raise DepthCaptureFailure("depth_worker_failed", "失败")

    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=queue, depth_capture_runner=broken))
    client.post("/api/projects/project-001/depth-captures", json={})
    queue.run_next()

    retry = client.post("/api/projects/project-001/depth-captures", json={})
    assert retry.status_code == 202


def test_no_callback_runner_cannot_complete_capture(tmp_path):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()

    def runner(*, request, **_):
        capture = new_depth_capture(request.source_reference_video_id, "auto", datetime.now(timezone.utc).isoformat())
        capture.id = request.capture_id
        _write_depth_artifacts(tmp_path, capture)
        return DepthCaptureRunResult(
            directory=tmp_path, executionDevice="cpu", frameCount=16, width=640, height=360,
            frameRate=8, qualityAssessment=_assessment("passed"),
        )

    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=queue, depth_capture_runner=runner))
    client.post("/api/projects/project-001/depth-captures", json={})
    queue.run_next()
    capture = client.get("/api/projects/project-001").json()["depthCaptures"][-1]

    assert capture["status"] == "failed"
    assert capture["error"]["code"] == "depth_capture_unexpected_error"


def test_reference_replacement_blocks_active_depth_compute(tmp_path, monkeypatch):
    _completed_project(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))
    client.post("/api/projects/project-001/depth-captures", json={})
    monkeypatch.setattr(main, "probe_reference_video", _probe_reference)

    response = client.put(
        "/api/projects/project-001/reference-video",
        files={"file": ("replacement.mp4", b"replacement", "video/mp4")},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_capture_in_progress"


def test_reference_replacement_blocks_running_depth_compute(tmp_path, monkeypatch):
    project = _completed_project(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))
    capture = new_depth_capture("video-001", "auto", project["createdAt"])
    capture.status = "running"
    _persist_capture(tmp_path, capture)
    monkeypatch.setattr(main, "probe_reference_video", _probe_reference)

    response = client.put(
        "/api/projects/project-001/reference-video",
        files={"file": ("replacement.mp4", b"replacement", "video/mp4")},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_capture_in_progress"


def test_reference_replacement_keeps_history_and_clears_active_capture(tmp_path, monkeypatch):
    project = _completed_project(tmp_path)
    capture = new_depth_capture("video-001", "auto", project["createdAt"])
    capture.status = "completed"
    capture.completedAt = project["createdAt"]
    capture.updatedAt = project["createdAt"]
    _write_depth_artifacts(tmp_path, capture)
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    payload[0]["depthCaptures"] = [capture.model_dump()]
    payload[0]["activeDepthCaptureId"] = capture.id
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(main, "probe_reference_video", _probe_reference)
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))

    response = client.put(
        "/api/projects/project-001/reference-video",
        files={"file": ("replacement.mp4", b"replacement", "video/mp4")},
    )

    assert response.status_code == 200
    assert response.json()["activeDepthCaptureId"] is None
    assert [item["id"] for item in response.json()["depthCaptures"]] == [capture.id]


def test_completed_artifact_corruption_is_read_projection_without_rewrite(tmp_path):
    capture = _completed_capture(tmp_path, quality="passed")
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ManualComputeQueue()))
    (tmp_path / "project-files/project-001/depth-captures" / capture.id / "depth-control.mp4").unlink()
    persisted = (tmp_path / "projects.json").read_bytes()

    response = client.get("/api/projects/project-001")

    assert response.status_code == 200
    assert response.json()["depthCaptures"][0]["status"] == "failed"
    assert response.json()["depthCaptures"][0]["error"]["code"] == "depth_capture_artifacts_invalid"
    assert (tmp_path / "projects.json").read_bytes() == persisted


def test_real_concurrent_start_submits_one_depth_job(tmp_path):
    _completed_project(tmp_path)
    entered = Event()
    release = Event()

    def blocking_runner(*, request, on_stage_started, on_stage_completed, **_):
        on_stage_started("preparing")
        entered.set()
        release.wait(timeout=2)
        for stage in ("preparing", "estimatingDepth", "encoding", "qualityAssessment"):
            if stage != "preparing":
                on_stage_started(stage)
            on_stage_completed(stage)
        capture = new_depth_capture(request.source_reference_video_id, "auto", datetime.now(timezone.utc).isoformat())
        capture.id = request.capture_id
        _write_depth_artifacts(tmp_path, capture)
        return DepthCaptureRunResult(
            directory=tmp_path, executionDevice="cpu", frameCount=16, width=640, height=360,
            frameRate=8, qualityAssessment=_assessment("passed"),
        )

    client = TestClient(create_app(data_dir=tmp_path, depth_capture_runner=blocking_runner))
    barrier = Barrier(3)
    responses = []

    def submit():
        barrier.wait()
        responses.append(client.post("/api/projects/project-001/depth-captures", json={}))

    first = Thread(target=submit)
    second = Thread(target=submit)
    first.start()
    second.start()
    barrier.wait()
    first.join(timeout=2)
    second.join(timeout=2)
    try:
        assert sorted(response.status_code for response in responses) == [202, 409]
        assert entered.wait(timeout=2)
        project = client.get("/api/projects/project-001").json()
        assert len(project["depthCaptures"]) == 1
    finally:
        release.set()


def test_completion_and_activation_cannot_reactivate_old_source_during_replacement(tmp_path, monkeypatch):
    _completed_project(tmp_path)
    runner_ready = Event()
    return_result = Event()
    completion_write = Event()
    release_completion = Event()
    replacement_attempt = Event()
    replacement_prelock_read = Event()
    replacement_lock_acquired = Event()
    original_write = main._write_projects
    original_read = main._read_projects

    def runner(*, request, on_stage_started, on_stage_completed, **_):
        for stage in ("preparing", "estimatingDepth", "encoding", "qualityAssessment"):
            on_stage_started(stage)
            on_stage_completed(stage)
        capture = new_depth_capture(request.source_reference_video_id, "auto", datetime.now(timezone.utc).isoformat())
        capture.id = request.capture_id
        _write_depth_artifacts(tmp_path, capture)
        runner_ready.set()
        return_result.wait(timeout=2)
        return DepthCaptureRunResult(
            directory=tmp_path, executionDevice="cpu", frameCount=16, width=640, height=360,
            frameRate=8, qualityAssessment=_assessment("passed"),
        )

    client = TestClient(create_app(data_dir=tmp_path, depth_capture_runner=runner))
    monkeypatch.setattr(main, "probe_reference_video", _probe_reference)
    client.post("/api/projects/project-001/depth-captures", json={})
    assert runner_ready.wait(timeout=2)

    def block_completion(data_dir, projects):
        capture = projects[0].depthCaptures[-1]
        if capture.status == "completed" and not completion_write.is_set():
            assert projects[0].activeDepthCaptureId == capture.id
            original_write(data_dir, projects)
            completion_write.set()
            release_completion.wait(timeout=2)
            return None
        return original_write(data_dir, projects)

    monkeypatch.setattr(main, "_write_projects", block_completion)
    return_result.set()
    assert completion_write.wait(timeout=2)
    response_holder = []

    def trace_replacement_read(*args, **kwargs):
        if replacement_attempt.is_set():
            if not replacement_prelock_read.is_set():
                replacement_prelock_read.set()
            else:
                replacement_lock_acquired.set()
        return original_read(*args, **kwargs)

    monkeypatch.setattr(main, "_read_projects", trace_replacement_read)

    def replace_reference():
        replacement_attempt.set()
        response_holder.append(client.put(
            "/api/projects/project-001/reference-video",
            files={"file": ("replacement.mp4", b"replacement", "video/mp4")},
        ))

    replacer = Thread(target=replace_reference)
    replacer.start()
    assert replacement_attempt.wait(timeout=2)
    assert replacement_prelock_read.wait(timeout=2)
    assert replacement_lock_acquired.is_set() is False
    release_completion.set()
    replacer.join(timeout=2)

    project = client.get("/api/projects/project-001").json()
    assert response_holder[0].status_code == 200
    assert replacement_lock_acquired.is_set()
    assert project["referenceMedia"]["id"] != "video-001"
    assert project["activeDepthCaptureId"] is None
    assert project["depthCaptures"][-1]["sourceReferenceVideoId"] == "video-001"


def test_default_app_routes_preprocessing_and_depth_through_one_compute_queue(tmp_path, monkeypatch):
    _completed_project(tmp_path)
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    payload[0]["localPreprocessing"] = None
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")
    created = []

    class RecordingQueue:
        def __init__(self):
            self.calls = []
            created.append(self)

        def submit(self, kind, project_id, handler):
            self.calls.append((kind, project_id, handler))
            return True

        def is_active(self, kind, project_id):
            return False

        def shutdown(self):
            return None

    monkeypatch.setattr(main, "LocalComputeJobQueue", RecordingQueue)
    client = TestClient(create_app(data_dir=tmp_path))
    preprocessing = client.post("/api/projects/project-001/local-preprocessing")
    _completed_project(tmp_path)
    depth = client.post("/api/projects/project-001/depth-captures", json={})

    assert preprocessing.status_code == depth.status_code == 202
    assert len(created) == 1
    assert [call[:2] for call in created[0].calls] == [
        ("preprocessing", "project-001"), ("depth_capture", "project-001"),
    ]


def test_stage_persistence_failure_becomes_stable_storage_error(tmp_path, monkeypatch):
    _completed_project(tmp_path)
    queue = ManualComputeQueue()
    original_write = main._write_projects
    entered = Event()
    allow_callback = Event()
    callback_continued = Event()

    def runner(*, on_stage_started, **_):
        entered.set()
        allow_callback.wait(timeout=2)
        on_stage_started("preparing")
        callback_continued.set()

    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=queue, depth_capture_runner=runner))
    client.post("/api/projects/project-001/depth-captures", json={})

    def fail_once(*args, **kwargs):
        if not entered.is_set():
            return original_write(*args, **kwargs)
        monkeypatch.setattr(main, "_write_projects", original_write)
        raise OSError("private disk path")

    monkeypatch.setattr(main, "_write_projects", fail_once)
    worker = Thread(target=queue.run_next)
    worker.start()
    assert entered.wait(timeout=2)
    allow_callback.set()
    worker.join(timeout=2)
    capture = client.get("/api/projects/project-001").json()["depthCaptures"][-1]

    assert callback_continued.is_set() is False
    assert capture["status"] == "failed"
    assert capture["error"]["code"] == "depth_capture_storage_failed"
    assert "private disk path" not in capture["error"]["message"]
