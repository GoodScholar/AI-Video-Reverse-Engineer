from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import create_app


def test_user_can_create_a_named_reproduction_project(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/api/projects", json={"name": "雨夜人像复刻"})

    assert response.status_code == 201
    project = response.json()
    assert project["name"] == "雨夜人像复刻"
    assert project["id"]
    assert project["createdAt"]
    assert project["updatedAt"] == project["createdAt"]


def test_new_project_has_no_reference_media(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/api/projects", json={"name": "雨夜人像复刻"})

    assert response.status_code == 201
    assert response.json()["referenceMedia"] is None
    assert "referenceVideo" not in response.json()


def test_project_without_reference_video_field_remains_readable(tmp_path):
    (tmp_path / "projects.json").write_text(
        '[{"id":"project-001","name":"旧项目","createdAt":"2026-09-10T10:00:00+00:00","updatedAt":"2026-09-10T10:00:00+00:00"}]',
        encoding="utf-8",
    )
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/projects/project-001")

    assert response.status_code == 200
    assert response.json()["referenceMedia"] is None
    assert "referenceVideo" not in response.json()


def test_legacy_video_and_completed_preprocessing_are_migrated_before_project_response(tmp_path):
    (tmp_path / "projects.json").write_text(json.dumps([{
        "id": "project-001", "name": "旧项目",
        "createdAt": "2026-09-10T10:00:00+00:00",
        "updatedAt": "2026-09-10T10:00:00+00:00",
        "referenceVideo": {
            "id": "video-001", "originalName": "clip.mp4", "format": "mp4", "sizeBytes": 11,
            "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24,
        },
        "localPreprocessing": {
            "id": "preprocessing-001", "sourceReferenceVideoId": "video-001",
            "algorithmVersion": 1, "status": "completed", "currentStage": None, "stages": [],
            "queuedAt": "2026-09-10T10:00:00+00:00", "startedAt": None,
            "updatedAt": "2026-09-10T10:00:00+00:00", "completedAt": None,
            "proxySummary": None, "reproducibilityAssessment": None, "error": None,
        },
    }]), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/projects/project-001")

    assert response.status_code == 200
    project = response.json()
    assert project["referenceMedia"]["type"] == "video"
    assert project["localPreprocessing"]["sourceReferenceMediaId"] == "video-001"
    assert project["localPreprocessing"]["mediaType"] == "video"
    assert "referenceVideo" not in project


@pytest.mark.parametrize("legacy_type", ["image", "invalid", None])
def test_persisted_legacy_video_with_an_existing_invalid_type_is_corrupt_data(tmp_path, legacy_type):
    (tmp_path / "projects.json").write_text(json.dumps([{
        "id": "project-001", "name": "旧项目",
        "createdAt": "2026-09-10T10:00:00+00:00",
        "updatedAt": "2026-09-10T10:00:00+00:00",
        "referenceVideo": {
            "type": legacy_type, "id": "video-001", "originalName": "clip.mp4", "format": "mp4",
            "sizeBytes": 11, "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24,
        },
    }]), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)

    response = client.get("/api/projects/project-001")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "本地项目数据已损坏，请从备份恢复 projects.json，或将损坏文件移到其他位置后重新读取。"
    )


def _legacy_video_payload():
    return {
        "id": "video-001", "originalName": "clip.mp4", "format": "mp4", "sizeBytes": 11,
        "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24,
    }


def _legacy_preprocessing_payload(status):
    stages_by_status = {
        "running": [
            {"name": "decoding", "status": "completed", "startedAt": "2026-09-10T10:01:00+00:00", "completedAt": "2026-09-10T10:01:03+00:00"},
            {"name": "sceneDetection", "status": "running", "startedAt": "2026-09-10T10:01:03+00:00", "completedAt": None},
            {"name": "keyframeExtraction", "status": "pending", "startedAt": None, "completedAt": None},
            {"name": "motionAnalysis", "status": "pending", "startedAt": None, "completedAt": None},
            {"name": "reproducibilityAssessment", "status": "pending", "startedAt": None, "completedAt": None},
        ],
        "failed": [
            {"name": "decoding", "status": "completed", "startedAt": "2026-09-10T10:01:00+00:00", "completedAt": "2026-09-10T10:01:03+00:00"},
            {"name": "sceneDetection", "status": "failed", "startedAt": "2026-09-10T10:01:03+00:00", "completedAt": "2026-09-10T10:01:04+00:00"},
            {"name": "keyframeExtraction", "status": "pending", "startedAt": None, "completedAt": None},
            {"name": "motionAnalysis", "status": "pending", "startedAt": None, "completedAt": None},
            {"name": "reproducibilityAssessment", "status": "pending", "startedAt": None, "completedAt": None},
        ],
        "completed": [
            {"name": name, "status": "completed", "startedAt": "2026-09-10T10:01:00+00:00", "completedAt": "2026-09-10T10:01:01+00:00"}
            for name in ["decoding", "sceneDetection", "keyframeExtraction", "motionAnalysis", "reproducibilityAssessment"]
        ],
    }
    payload = {
        "id": f"preprocessing-{status}", "sourceReferenceVideoId": "video-001",
        "algorithmVersion": 1, "status": status,
        "currentStage": "sceneDetection" if status in {"running", "failed"} else None,
        "stages": stages_by_status[status],
        "queuedAt": "2026-09-10T10:00:00+00:00",
        "startedAt": "2026-09-10T10:01:00+00:00",
        "updatedAt": "2026-09-10T10:01:04+00:00",
        "completedAt": "2026-09-10T10:01:04+00:00" if status in {"failed", "completed"} else None,
        "proxySummary": None,
        "reproducibilityAssessment": None,
        "error": None,
    }
    if status == "failed":
        payload["error"] = {
            "code": "scene_detection_failed", "message": "镜头检测失败。",
            "stage": "sceneDetection", "retryable": True,
        }
    if status == "completed":
        payload["proxySummary"] = {
            "keyframeCount": 4, "contactSheetCount": 1, "sceneChangeCount": 2,
            "motionP50": 1.0, "motionP90": 2.0, "motionPeak": 3.0, "motionLevel": "light",
        }
        payload["reproducibilityAssessment"] = {
            "status": "pending_semantic_confirmation",
            "checks": [{
                "criterion": "single_shot", "status": "passed", "message": "单镜头。", "evidence": "镜头检测。",
            }],
        }
    return payload


def _legacy_project_payload(kind):
    payload = {
        "id": f"project-{kind}", "name": f"旧项目 {kind}",
        "createdAt": "2026-09-10T10:00:00+00:00",
        "updatedAt": "2026-09-10T10:00:00+00:00",
    }
    if kind != "no-reference":
        payload["referenceVideo"] = _legacy_video_payload()
    if kind in {"running", "failed", "completed"}:
        payload["localPreprocessing"] = _legacy_preprocessing_payload(kind)
    return payload


@pytest.mark.parametrize("kind", ["no-reference", "video-only", "running", "failed", "completed"])
def test_five_legacy_project_json_fixtures_migrate_and_next_write_uses_only_new_fields(tmp_path, kind):
    raw_project = _legacy_project_payload(kind)
    (tmp_path / "projects.json").write_text(json.dumps([raw_project]), encoding="utf-8")

    project = main._read_projects(tmp_path)[0].model_dump()
    if kind == "no-reference":
        assert project["referenceMedia"] is None
        assert project["localPreprocessing"] is None
    else:
        assert project["referenceMedia"] == {**_legacy_video_payload(), "type": "video"}
    if kind in {"running", "failed", "completed"}:
        expected = _legacy_preprocessing_payload(kind)
        preprocessing = project["localPreprocessing"]
        assert preprocessing["id"] == expected["id"]
        assert preprocessing["algorithmVersion"] == expected["algorithmVersion"]
        assert preprocessing["status"] == expected["status"]
        assert preprocessing["currentStage"] == expected["currentStage"]
        assert preprocessing["stages"] == expected["stages"]
        assert preprocessing["error"] == expected["error"]
        expected_proxy_summary = expected["proxySummary"]
        if expected_proxy_summary is not None:
            expected_proxy_summary = {**expected_proxy_summary, "mediaType": "video"}
        assert preprocessing["proxySummary"] == expected_proxy_summary
        assert preprocessing["reproducibilityAssessment"] == expected["reproducibilityAssessment"]
        assert preprocessing["sourceReferenceMediaId"] == "video-001"
        assert preprocessing["mediaType"] == "video"

    main._write_projects(tmp_path, main._read_projects(tmp_path))

    persisted = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))[0]
    assert "referenceVideo" not in persisted
    assert "sourceReferenceVideoId" not in (persisted["localPreprocessing"] or {})


def test_new_and_legacy_projects_have_no_local_preprocessing(tmp_path):
    legacy = [{
        "id": "project-001", "name": "旧项目",
        "createdAt": "2026-09-10T10:00:00+00:00",
        "updatedAt": "2026-09-10T10:00:00+00:00",
    }]
    (tmp_path / "projects.json").write_text(json.dumps(legacy), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path))

    assert client.get("/api/projects/project-001").json()["localPreprocessing"] is None
    created = client.post("/api/projects", json={"name": "新项目"}).json()
    assert created["localPreprocessing"] is None


def test_user_cannot_create_a_project_with_a_blank_name(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/api/projects", json={"name": "   "})

    assert response.status_code == 422


def test_user_can_open_a_project_after_local_service_restarts(tmp_path):
    first_client = TestClient(create_app(data_dir=tmp_path))
    created = first_client.post("/api/projects", json={"name": "商品运动复刻"}).json()

    restarted_client = TestClient(create_app(data_dir=tmp_path))
    listed = restarted_client.get("/api/projects")
    opened = restarted_client.get(f"/api/projects/{created['id']}")

    assert listed.status_code == 200
    assert [project["id"] for project in listed.json()] == [created["id"]]
    assert opened.status_code == 200
    assert opened.json()["name"] == "商品运动复刻"


def test_user_can_see_independent_unconfigured_environment_statuses(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "analysisService": {"state": "unconfigured", "label": "未配置"},
        "localComfyui": {"state": "disconnected", "label": "未连接"},
    }


def test_user_receives_an_actionable_storage_failure_when_project_cannot_be_saved(tmp_path):
    unavailable_path = tmp_path / "not-a-directory"
    unavailable_path.write_text("occupied", encoding="utf-8")
    client = TestClient(create_app(data_dir=unavailable_path), raise_server_exceptions=False)

    response = client.post("/api/projects", json={"name": "无法保存的项目"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "本地项目存储不可用，请检查数据目录的访问权限后重试。"
    }


def test_user_receives_an_actionable_storage_failure_when_loading_projects(tmp_path):
    unavailable_path = tmp_path / "not-a-directory"
    unavailable_path.write_text("occupied", encoding="utf-8")
    client = TestClient(create_app(data_dir=unavailable_path), raise_server_exceptions=False)

    response = client.get("/api/projects")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "本地项目存储不可用，请检查数据目录的访问权限后重试。"
    }


def test_user_receives_the_same_actionable_storage_failure_for_corrupt_project_data(tmp_path):
    (tmp_path / "projects.json").write_text("{not-json", encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)

    response = client.get("/api/projects")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "本地项目数据已损坏，请从备份恢复 projects.json，或将损坏文件移到其他位置后重新读取。"
    }


def test_invalid_project_timestamp_in_json_is_reported_as_corrupt_data(tmp_path):
    (tmp_path / "projects.json").write_text(
        '[{"id":"project-001","name":"雨夜人像复刻","createdAt":"not-a-date","updatedAt":"2026-09-10T10:00:00+00:00"}]',
        encoding="utf-8",
    )
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)

    response = client.get("/api/projects")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "本地项目数据已损坏，请从备份恢复 projects.json，或将损坏文件移到其他位置后重新读取。"
    }


@pytest.mark.parametrize(
    "project_id,video_id",
    [
        ("..", "video-001"),
        ("project/001", "video-001"),
        ("project-001", ".."),
        ("project-001", "video\\001"),
    ],
)
def test_unsafe_persisted_project_or_reference_video_id_is_corrupt_data(tmp_path, project_id, video_id):
    (tmp_path / "projects.json").write_text(json.dumps([{
        "id": project_id,
        "name": "雨夜人像复刻",
        "createdAt": "2026-09-10T10:00:00+00:00",
        "updatedAt": "2026-09-10T10:00:00+00:00",
        "referenceVideo": {
            "id": video_id, "originalName": "clip.mp4", "format": "mp4", "sizeBytes": 11,
            "durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24,
        },
    }]), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)

    response = client.get("/api/projects")

    assert response.status_code == 503
    assert response.json()["detail"] == "本地项目数据已损坏，请从备份恢复 projects.json，或将损坏文件移到其他位置后重新读取。"


def test_concurrent_project_creations_keep_every_project(tmp_path, monkeypatch):
    original_read_projects = main._read_projects

    def delayed_read_projects(data_dir):
        time.sleep(0.02)
        return original_read_projects(data_dir)

    monkeypatch.setattr(main, "_read_projects", delayed_read_projects)
    app = create_app(data_dir=tmp_path)

    def create(index):
        client = TestClient(app)
        return client.post("/api/projects", json={"name": f"并发项目 {index}"})

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(create, range(8)))

    listed = TestClient(app).get("/api/projects")

    assert [response.status_code for response in responses] == [201] * 8
    assert {project["name"] for project in listed.json()} == {f"并发项目 {index}" for index in range(8)}


def test_resolve_default_binary_prioritizes_environment_variable(monkeypatch):
    monkeypatch.setenv("FFMPEG_PATH", "/custom/bin/ffmpeg")
    assert main.resolve_default_binary("ffmpeg") == "/custom/bin/ffmpeg"


def test_resolve_default_binary_detects_candidate_or_falls_back(monkeypatch):
    monkeypatch.delenv("CUSTOM_BINARY_PATH", raising=False)
    assert main.resolve_default_binary("custom_binary") == "custom_binary"
