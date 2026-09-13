import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

from fastapi.testclient import TestClient

from app import main
from app.local_preprocessing import (
    AnalysisProxySummary,
    ReproducibilityAssessment,
    STAGE_ORDER,
    new_local_preprocessing,
)
from app.local_preprocessing_runner import LocalPreprocessingFailure, PreprocessingRunResult
from app.main import create_app
from app.reference_video import ReferenceVideo


class ManualQueue:
    def __init__(self, handler):
        self.handler = handler
        self.pending = []
        self.closed = False

    def submit(self, project_id):
        self.pending.append(project_id)
        return True

    def run_next(self):
        self.handler(self.pending.pop(0))

    def shutdown(self):
        self.closed = True

    def is_active(self, project_id):
        return project_id in self.pending


def client_with_video_and_queue(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=queue_factory))
    project = client.post("/api/projects", json={"name": "预处理项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    project_path = tmp_path / "project-files" / project["id"] / "reference-videos"
    project_path.mkdir(parents=True)
    (project_path / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")
    return client, project, holder["queue"]


def write_completed_project_with_artifacts(tmp_path, *, first_invalid_stage):
    now = datetime.now(timezone.utc)
    preprocessing = new_local_preprocessing("preprocessing-001", "video-001", "video", now)
    for state in preprocessing.stages:
        state.status = "completed"
        state.startedAt = now.isoformat()
        state.completedAt = now.isoformat()
    preprocessing.status = "completed"
    preprocessing.completedAt = now.isoformat()
    preprocessing.proxySummary = AnalysisProxySummary(
        keyframeCount=4, sceneChangeCount=0, motionP50=None, motionP90=None,
        motionPeak=None, motionLevel="unavailable",
    )
    preprocessing.reproducibilityAssessment = ReproducibilityAssessment(
        status="out_of_scope", checks=[],
    )
    project = {
        "id": "project-001", "name": "完成项目", "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(), "referenceMedia": {"type": "video",
            "id": "video-001", "originalName": "clip.mp4", "format": "mp4", "sizeBytes": 1,
            "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24,
        }, "localPreprocessing": preprocessing.model_dump(),
    }
    (tmp_path / "projects.json").write_text(json.dumps([project]), encoding="utf-8")
    directory = (
        tmp_path / "project-files" / project["id"] / "local-preprocessing" / preprocessing.id
    )
    directory.mkdir(parents=True)
    for stage in STAGE_ORDER:
        if stage == first_invalid_stage:
            continue
        if stage == "decoding":
            (directory / "decode.json").write_text('{"done": true}', encoding="utf-8")
        elif stage == "sceneDetection":
            (directory / "scene-changes.json").write_text('{"done": true}', encoding="utf-8")
        elif stage == "keyframeExtraction":
            frames = directory / "keyframes"
            frames.mkdir()
            for index in range(1, 5):
                (frames / f"frame-{index:04d}.jpg").write_bytes(b"jpeg")
            (directory / "contact-sheet.jpg").write_bytes(b"sheet")
        elif stage == "motionAnalysis":
            (directory / "motion.json").write_text('{"done": true}', encoding="utf-8")
        elif stage == "reproducibilityAssessment":
            (directory / "analysis-proxy.json").write_text('{"done": true}', encoding="utf-8")
            (directory / "manifest.json").write_text(json.dumps({
                "schemaVersion": 1,
                "algorithmVersion": 1,
                "sourceReferenceVideoId": "video-001",
            }), encoding="utf-8")
    return project, (tmp_path / "projects.json").read_bytes()


def preprocessing_file_snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def assert_missing_completed_artifact_projection(task, *, first_invalid_stage):
    start = STAGE_ORDER.index(first_invalid_stage)
    code = {
        "decoding": "video_decode_failed",
        "sceneDetection": "scene_detection_failed",
        "keyframeExtraction": "keyframe_extraction_failed",
        "motionAnalysis": "motion_analysis_failed",
        "reproducibilityAssessment": "assessment_failed",
    }[first_invalid_stage]
    assert task["status"] == "failed"
    assert task["currentStage"] == first_invalid_stage
    assert [stage["status"] for stage in task["stages"]] == [
        "completed" if index < start else "pending" for index in range(len(STAGE_ORDER))
    ]
    assert task["completedAt"] is None
    assert task["proxySummary"] is None
    assert task["reproducibilityAssessment"] is None
    assert task["error"] == {
        "code": code,
        "message": "已完成结果缺少必需产物，请重新启动本地预处理。",
        "stage": first_invalid_stage,
        "retryable": True,
    }


def test_listing_projects_projects_invalid_completed_preprocessing_without_persisting_projection(tmp_path):
    for stage in STAGE_ORDER:
        case_directory = tmp_path / stage
        case_directory.mkdir()
        _, persisted = write_completed_project_with_artifacts(
            case_directory, first_invalid_stage=stage,
        )
        directory = (
            case_directory / "project-files/project-001/local-preprocessing/preprocessing-001"
        )
        artifacts_before = preprocessing_file_snapshot(directory)
        client = TestClient(create_app(
            data_dir=case_directory, preprocessing_queue_factory=ManualQueue,
        ))

        response = client.get("/api/projects")

        assert response.status_code == 200
        assert_missing_completed_artifact_projection(
            response.json()[0]["localPreprocessing"], first_invalid_stage=stage,
        )
        assert (case_directory / "projects.json").read_bytes() == persisted
        assert preprocessing_file_snapshot(directory) == artifacts_before


def test_opening_project_projects_invalid_completed_preprocessing_and_manual_restart_uses_a_new_id(tmp_path):
    project, persisted = write_completed_project_with_artifacts(
        tmp_path, first_invalid_stage="decoding",
    )
    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=ManualQueue))
    directory = tmp_path / "project-files/project-001/local-preprocessing/preprocessing-001"
    artifacts_before = preprocessing_file_snapshot(directory)

    opened = client.get(f"/api/projects/{project['id']}")

    assert opened.status_code == 200
    assert_missing_completed_artifact_projection(
        opened.json()["localPreprocessing"], first_invalid_stage="decoding",
    )
    assert (tmp_path / "projects.json").read_bytes() == persisted
    assert preprocessing_file_snapshot(directory) == artifacts_before

    restarted = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert restarted.status_code == 202
    assert restarted.json()["localPreprocessing"]["id"] != "preprocessing-001"


def test_opening_project_returns_a_valid_completed_preprocessing_unchanged(tmp_path):
    project, persisted = write_completed_project_with_artifacts(tmp_path, first_invalid_stage=None)
    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=ManualQueue))
    directory = tmp_path / "project-files/project-001/local-preprocessing/preprocessing-001"
    artifacts_before = preprocessing_file_snapshot(directory)

    opened = client.get(f"/api/projects/{project['id']}")

    assert opened.status_code == 200
    assert opened.json() == project
    assert (tmp_path / "projects.json").read_bytes() == persisted
    assert preprocessing_file_snapshot(directory) == artifacts_before


def test_reading_invalid_completed_preprocessing_hides_preprocessing_directory_os_error(tmp_path, monkeypatch):
    project, _ = write_completed_project_with_artifacts(tmp_path, first_invalid_stage="decoding")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)

    def inaccessible_directory(*args):
        raise OSError("/private/customer/project-files")

    monkeypatch.setattr(main, "preprocessing_directory", inaccessible_directory)
    response = client.get(f"/api/projects/{project['id']}")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "本地项目存储不可用，请检查数据目录的访问权限后重试。"
    }


def test_start_persists_queued_state_before_dispatch(tmp_path):
    client, project, queue = client_with_video_and_queue(tmp_path)

    response = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert response.status_code == 202
    assert response.json()["localPreprocessing"]["status"] == "queued"
    assert queue.pending == [project["id"]]


def test_start_requires_a_reference_video(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    project = client.post("/api/projects", json={"name": "无视频项目"}).json()

    response = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "reference_video_required", "message": "请先上传参考视频后再开始本地预处理。",
    }


def test_restart_marks_queued_preprocessing_as_retryable_failure(tmp_path):
    now = datetime.now(timezone.utc)
    preprocessing = new_local_preprocessing("preprocessing-001", "video-001", "video", now)
    (tmp_path / "projects.json").write_text(__import__("json").dumps([{
        "id": "project-001", "name": "旧项目", "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(), "referenceMedia": {"type": "video",
            "id": "video-001", "originalName": "clip.mp4", "format": "mp4", "sizeBytes": 1,
            "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24,
        }, "localPreprocessing": preprocessing.model_dump(),
    }]), encoding="utf-8")

    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=ManualQueue))

    task = client.get("/api/projects/project-001").json()["localPreprocessing"]
    assert task["status"] == "failed"
    assert task["error"] == {
        "code": "preprocessing_interrupted",
        "message": "本地服务曾退出，已保留完成阶段，可从解码继续重试。",
        "stage": "decoding",
        "retryable": True,
    }


def test_second_start_while_queued_is_rejected_without_another_dispatch(tmp_path):
    client, project, queue = client_with_video_and_queue(tmp_path)

    assert client.post(f"/api/projects/{project['id']}/local-preprocessing").status_code == 202
    duplicate = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "preprocessing_in_progress"
    assert queue.pending == [project["id"]]


def test_completed_task_is_idempotent_and_is_not_dispatched_again(tmp_path):
    def runner(**kwargs):
        for stage in STAGE_ORDER:
            kwargs["on_stage_started"](stage)
            kwargs["on_stage_completed"](stage)
        output = kwargs["output_directory"]
        output.mkdir(parents=True, exist_ok=True)
        (output / "decode.json").write_text('{"done": true}', encoding="utf-8")
        (output / "scene-changes.json").write_text('{"done": true}', encoding="utf-8")
        (output / "motion.json").write_text('{"done": true}', encoding="utf-8")
        frames = output / "keyframes"
        frames.mkdir()
        for index in range(1, 5):
            (frames / f"frame-{index:04d}.jpg").write_bytes(b"jpeg")
        (output / "contact-sheet.jpg").write_bytes(b"sheet")
        (output / "analysis-proxy.json").write_text('{"done": true}', encoding="utf-8")
        (output / "manifest.json").write_text(json.dumps({
            "schemaVersion": 1,
            "algorithmVersion": 1,
            "sourceReferenceVideoId": kwargs["reference"].id,
        }), encoding="utf-8")
        return PreprocessingRunResult(
            proxy_summary=AnalysisProxySummary(
                keyframeCount=4, sceneChangeCount=0, motionP50=None, motionP90=None,
                motionPeak=None, motionLevel="unavailable",
            ),
            assessment=ReproducibilityAssessment(status="out_of_scope", checks=[]),
        )

    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path, preprocessing_runner=runner, preprocessing_queue_factory=queue_factory,
    ))
    project = client.post("/api/projects", json={"name": "可完成项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    path = tmp_path / "project-files" / project["id"] / "reference-videos"
    path.mkdir(parents=True)
    (path / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")

    assert client.post(f"/api/projects/{project['id']}/local-preprocessing").status_code == 202
    holder["queue"].run_next()
    repeated = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert repeated.status_code == 200
    assert repeated.json()["localPreprocessing"]["status"] == "completed"
    assert holder["queue"].pending == []


def test_completed_task_with_missing_required_artifacts_starts_a_new_preprocessing_version(tmp_path):
    def runner(**kwargs):
        for stage in STAGE_ORDER:
            kwargs["on_stage_started"](stage)
            kwargs["on_stage_completed"](stage)
        return PreprocessingRunResult(
            proxy_summary=AnalysisProxySummary(
                keyframeCount=4, sceneChangeCount=0, motionP50=None, motionP90=None,
                motionPeak=None, motionLevel="unavailable",
            ),
            assessment=ReproducibilityAssessment(status="out_of_scope", checks=[]),
        )

    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path, preprocessing_runner=runner, preprocessing_queue_factory=queue_factory,
    ))
    project = client.post("/api/projects", json={"name": "损坏完成态项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    source_dir = tmp_path / "project-files" / project["id"] / "reference-videos"
    source_dir.mkdir(parents=True)
    (source_dir / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")

    assert client.post(f"/api/projects/{project['id']}/local-preprocessing").status_code == 202
    holder["queue"].run_next()
    completed = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))[0][
        "localPreprocessing"
    ]
    assert completed["status"] == "completed"

    repeated = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    task = repeated.json()["localPreprocessing"]
    assert repeated.status_code == 202
    assert task["id"] != completed["id"]
    assert task["status"] == "queued"
    assert [stage["status"] for stage in task["stages"]] == ["pending"] * 5
    assert holder["queue"].pending == [project["id"]]


def test_runner_failure_keeps_completed_stages_and_exposes_retryable_error(tmp_path):
    def failing_runner(**kwargs):
        kwargs["on_stage_started"]("decoding")
        kwargs["on_stage_completed"]("decoding")
        kwargs["on_stage_started"]("sceneDetection")
        raise LocalPreprocessingFailure("scene_detection_failed", "镜头检测失败，请重试。", "sceneDetection")

    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path, preprocessing_runner=failing_runner, preprocessing_queue_factory=queue_factory,
    ))
    project = client.post("/api/projects", json={"name": "失败项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    path = tmp_path / "project-files" / project["id"] / "reference-videos"
    path.mkdir(parents=True)
    (path / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")

    client.post(f"/api/projects/{project['id']}/local-preprocessing")
    holder["queue"].run_next()
    task = client.get(f"/api/projects/{project['id']}").json()["localPreprocessing"]

    assert task["status"] == "failed"
    assert task["stages"][0]["status"] == "completed"
    assert task["stages"][1]["status"] == "failed"
    assert task["error"]["code"] == "scene_detection_failed"

    retry = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert retry.status_code == 202
    assert retry.json()["localPreprocessing"]["id"] == task["id"]


def test_unexpected_storage_error_is_persisted_with_the_stable_storage_code(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path,
        preprocessing_runner=lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        preprocessing_queue_factory=queue_factory,
    ))
    project = client.post("/api/projects", json={"name": "存储失败项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    source_dir = tmp_path / "project-files" / project["id"] / "reference-videos"
    source_dir.mkdir(parents=True)
    (source_dir / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")

    assert client.post(f"/api/projects/{project['id']}/local-preprocessing").status_code == 202
    holder["queue"].run_next()

    task = client.get(f"/api/projects/{project['id']}").json()["localPreprocessing"]
    assert task["error"]["code"] == "preprocessing_storage_unavailable"
    assert task["error"]["stage"] == "decoding"


def test_running_preprocessing_rejects_upload_before_multipart_is_parsed(tmp_path):
    client, project, _ = client_with_video_and_queue(tmp_path)
    assert client.post(f"/api/projects/{project['id']}/local-preprocessing").status_code == 202

    response = client.put(
        f"/api/projects/{project['id']}/reference-video",
        content=b"not a valid multipart body",
        headers={"Content-Type": "multipart/form-data"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "preprocessing_in_progress"


def test_queue_rejection_marks_persisted_task_as_retryable_failure(tmp_path):
    class RejectingQueue(ManualQueue):
        def submit(self, project_id):
            return False

    holder = {}

    def queue_factory(handler):
        holder["queue"] = RejectingQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=queue_factory))
    project = client.post("/api/projects", json={"name": "队列故障项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    path = tmp_path / "project-files" / project["id"] / "reference-videos"
    path.mkdir(parents=True)
    (path / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")

    response = client.post(f"/api/projects/{project['id']}/local-preprocessing")
    task = client.get(f"/api/projects/{project['id']}").json()["localPreprocessing"]

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "preprocessing_queue_unavailable"
    assert task["status"] == "failed"
    assert task["error"]["code"] == "preprocessing_unexpected_error"


def test_restart_ignores_completed_current_stage_and_resumes_first_unfinished_stage(tmp_path):
    now = datetime.now(timezone.utc)
    preprocessing = new_local_preprocessing("preprocessing-001", "video-001", "video", now)
    for state in preprocessing.stages[:2]:
        state.status = "completed"
        state.startedAt = now.isoformat()
        state.completedAt = now.isoformat()
    preprocessing.status = "running"
    preprocessing.currentStage = "sceneDetection"
    (tmp_path / "projects.json").write_text(json.dumps([{
        "id": "project-001", "name": "旧项目", "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(), "referenceMedia": {"type": "video",
            "id": "video-001", "originalName": "clip.mp4", "format": "mp4", "sizeBytes": 1,
            "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24,
        }, "localPreprocessing": preprocessing.model_dump(),
    }]), encoding="utf-8")

    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=ManualQueue))

    task = client.get("/api/projects/project-001").json()["localPreprocessing"]
    assert [stage["status"] for stage in task["stages"][:2]] == ["completed", "completed"]
    assert task["currentStage"] == "keyframeExtraction"
    assert task["error"]["stage"] == "keyframeExtraction"


def test_stage_completion_advances_current_stage_before_an_unexpected_runner_failure(tmp_path):
    def runner(**kwargs):
        kwargs["on_stage_started"]("decoding")
        kwargs["on_stage_completed"]("decoding")
        raise RuntimeError("interrupted between stages")

    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    _, project, _ = client_with_video_and_queue(tmp_path)
    client = TestClient(create_app(
        data_dir=tmp_path, preprocessing_runner=runner, preprocessing_queue_factory=queue_factory,
    ))
    assert client.post(f"/api/projects/{project['id']}/local-preprocessing").status_code == 202
    holder["queue"].run_next()

    task = client.get(f"/api/projects/{project['id']}").json()["localPreprocessing"]
    assert task["stages"][0]["status"] == "completed"
    assert task["currentStage"] == "sceneDetection"
    assert task["error"]["stage"] == "sceneDetection"


def test_stale_queued_task_after_failed_dispatch_is_recovered_and_retried(tmp_path, monkeypatch):
    class FlakyQueue(ManualQueue):
        def __init__(self, handler):
            super().__init__(handler)
            self.attempts = 0

        def submit(self, project_id):
            self.attempts += 1
            if self.attempts == 1:
                return False
            self.pending.append(project_id)
            return True

    holder = {}

    def queue_factory(handler):
        holder["queue"] = FlakyQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=queue_factory))
    project = client.post("/api/projects", json={"name": "补偿写失败项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    path = tmp_path / "project-files" / project["id"] / "reference-videos"
    path.mkdir(parents=True)
    (path / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")
    original_write = __import__("app.main", fromlist=["_write_projects"])._write_projects
    writes = 0

    def fail_compensation(data_dir, projects):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("compensation unavailable")
        return original_write(data_dir, projects)

    monkeypatch.setattr("app.main._write_projects", fail_compensation)

    first = client.post(f"/api/projects/{project['id']}/local-preprocessing")
    second = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert first.status_code == 503
    assert second.status_code == 202
    assert holder["queue"].pending == [project["id"]]


def test_concurrent_starts_for_one_project_have_exactly_one_accepted_dispatch(tmp_path):
    client, project, queue = client_with_video_and_queue(tmp_path)
    app = client.app

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(
            lambda _: TestClient(app).post(f"/api/projects/{project['id']}/local-preprocessing"), range(2),
        ))

    assert sorted(response.status_code for response in responses) == [202, 409]
    assert queue.pending == [project["id"]]


def test_shutdown_lifecycle_closes_injected_queue(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    app = create_app(data_dir=tmp_path, preprocessing_queue_factory=queue_factory)
    with TestClient(app):
        assert holder["queue"].closed is False

    assert holder["queue"].closed is True


def test_dispatching_window_rejects_a_second_start_before_the_first_submit_returns(tmp_path):
    class BlockingQueue(ManualQueue):
        def __init__(self, handler):
            super().__init__(handler)
            self.submit_entered = Event()
            self.release_submit = Event()
            self.submit_count = 0

        def submit(self, project_id):
            self.submit_count += 1
            if self.submit_count == 1:
                self.submit_entered.set()
                assert self.release_submit.wait(timeout=2)
                self.pending.append(project_id)
                return True
            return False

    holder = {}

    def queue_factory(handler):
        holder["queue"] = BlockingQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=queue_factory))
    project = client.post("/api/projects", json={"name": "投递窗口项目"}).json()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=1,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )
    path = tmp_path / "project-files" / project["id"] / "reference-videos"
    path.mkdir(parents=True)
    (path / "video-001.mp4").write_bytes(b"video")
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project, "referenceMedia": reference.model_dump(),
    }]), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(TestClient(client.app).post, f"/api/projects/{project['id']}/local-preprocessing")
        assert holder["queue"].submit_entered.wait(timeout=2)
        second = client.post(f"/api/projects/{project['id']}/local-preprocessing")
        holder["queue"].release_submit.set()
        first_response = first.result(timeout=2)

    task = client.get(f"/api/projects/{project['id']}").json()["localPreprocessing"]
    assert second.status_code == 409
    assert first_response.status_code == 202
    assert holder["queue"].submit_count == 1
    assert task["status"] == "queued"


def test_initial_queued_write_failure_does_not_submit_a_job(tmp_path, monkeypatch):
    client, project, queue = client_with_video_and_queue(tmp_path)

    def fail_write(data_dir, projects):
        raise OSError("projects unavailable")

    monkeypatch.setattr("app.main._write_projects", fail_write)
    response = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert queue.pending == []


def test_algorithm_version_change_creates_a_new_preprocessing_id(tmp_path, monkeypatch):
    client, project, queue = client_with_video_and_queue(tmp_path)
    current = client.get(f"/api/projects/{project['id']}").json()
    previous = new_local_preprocessing("preprocessing-001", current["referenceMedia"]["id"], "video", datetime.now(timezone.utc))
    previous.status = "failed"
    (tmp_path / "projects.json").write_text(json.dumps([{
        **current, "localPreprocessing": previous.model_dump(),
    }]), encoding="utf-8")
    monkeypatch.setattr(main, "ALGORITHM_VERSION", 2)

    response = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    assert response.status_code == 202
    assert response.json()["localPreprocessing"]["id"] != previous.id
    assert queue.pending == [project["id"]]


def test_retry_rewinds_to_the_first_missing_completed_stage_artifact(tmp_path):
    client, project, queue = client_with_video_and_queue(tmp_path)
    current = client.get(f"/api/projects/{project['id']}").json()
    preprocessing = new_local_preprocessing("preprocessing-001", current["referenceMedia"]["id"], "video", datetime.now(timezone.utc))
    preprocessing.status = "failed"
    preprocessing.currentStage = "sceneDetection"
    preprocessing.stages[0].status = "completed"
    (tmp_path / "projects.json").write_text(json.dumps([{
        **current, "localPreprocessing": preprocessing.model_dump(),
    }]), encoding="utf-8")

    response = client.post(f"/api/projects/{project['id']}/local-preprocessing")

    task = response.json()["localPreprocessing"]
    assert response.status_code == 202
    assert task["id"] == preprocessing.id
    assert task["stages"][0]["status"] == "pending"
    assert task["stages"][1]["status"] == "pending"
    assert queue.pending == [project["id"]]
