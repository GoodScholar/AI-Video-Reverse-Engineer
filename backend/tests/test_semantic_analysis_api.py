from datetime import datetime, timezone
from io import BytesIO
import json

from fastapi.testclient import TestClient
from PIL import Image

from app.analysis_settings import AnalysisSettings
from app.image_preprocessing_runner import run_image_preprocessing
from app.local_preprocessing import new_local_preprocessing
from app.local_preprocessing_storage import preprocessing_directory
from app.main import create_app
from app.reference_media import ReferenceImage
from app.reference_media_storage import managed_reference_media_path
from app.semantic_analysis import StructuredVisualAnalysis
from app.semantic_analysis_storage import new_semantic_analysis
from app.semantic_analysis_storage import semantic_analysis_checkpoint_path


class InMemoryCredentials:
    def __init__(self):
        self.values = {"bailian": "test-key"}

    def get(self, provider):
        return self.values.get(provider)

    def set(self, provider, secret):
        self.values[provider] = secret

    def delete(self, provider):
        self.values.pop(provider, None)


class ManualQueue:
    def __init__(self, handler):
        self.handler = handler
        self.pending = []

    def submit(self, project_id):
        self.pending.append(project_id)
        return True

    def shutdown(self):
        pass

    def is_active(self, project_id):
        return project_id in self.pending

    def run_next(self):
        self.handler(self.pending.pop(0))


class RejectingQueue(ManualQueue):
    def submit(self, project_id):
        return False


def ready_project(client, tmp_path):
    project = client.post("/api/projects", json={"name": "语义分析项目"}).json()
    now = datetime.now(timezone.utc)
    reference = ReferenceImage(
        id="image-001", originalName="private.png", format="png", sizeBytes=1,
        width=256, height=256, hasTransparency=False,
    )
    preprocessing = new_local_preprocessing("preprocessing-001", reference.id, "image", now)
    source = managed_reference_media_path(tmp_path, project["id"], reference)
    source.parent.mkdir(parents=True)
    image = Image.new("RGB", (256, 256), (12, 34, 56))
    image.save(source, format="PNG")
    result = run_image_preprocessing(
        source_path=source,
        reference=reference,
        preprocessing=preprocessing,
        output_directory=preprocessing_directory(tmp_path, project["id"], preprocessing.id),
        on_stage_started=lambda _: None,
        on_stage_completed=lambda _: None,
    )
    preprocessing.status = "completed"
    preprocessing.completedAt = now.isoformat()
    preprocessing.proxySummary = result.proxy_summary
    preprocessing.reproducibilityAssessment = result.assessment
    for stage in preprocessing.stages:
        stage.status = "completed"
        stage.startedAt = now.isoformat()
        stage.completedAt = now.isoformat()
    (tmp_path / "projects.json").write_text(json.dumps([{
        **project,
        "referenceMedia": reference.model_dump(),
        "localPreprocessing": preprocessing.model_dump(),
    }]), encoding="utf-8")
    return project["id"]


def test_analysis_requires_explicit_disclosure_confirmation(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    project = client.post("/api/projects", json={"name": "语义分析项目"}).json()

    response = client.post(f"/api/projects/{project['id']}/semantic-analysis", json={
        "provider": "bailian",
        "model": "qwen3.7-flash",
        "disclosureAccepted": False,
    })

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "analysis_disclosure_required"


def test_analysis_start_persists_queued_job_and_returns_accepted(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
    ))
    project_id = ready_project(client, tmp_path)
    configured = client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    })
    assert configured.status_code == 200

    response = client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "bailian",
        "model": "qwen3.7-flash",
        "disclosureAccepted": True,
    })

    assert response.status_code == 202
    assert response.json()["semanticAnalysis"]["status"] == "queued"
    assert holder["queue"].pending == [project_id]


def test_queue_rejection_marks_the_persisted_analysis_as_retryable_failure(tmp_path):
    def queue_factory(handler):
        return RejectingQueue(handler)

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    }).status_code == 200

    response = client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "bailian", "model": "qwen3.7-flash", "disclosureAccepted": True,
    })

    assert response.status_code == 503
    task = client.get(f"/api/projects/{project_id}").json()["semanticAnalysis"]
    assert task["status"] == "failed"
    assert task["error"]["code"] == "semantic_analysis_queue_unavailable"
    assert task["error"]["retryable"] is True


def test_analysis_rejects_completed_preprocessing_without_validated_artifacts(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    }).status_code == 200
    (preprocessing_directory(tmp_path, project_id, "preprocessing-001") / "analysis-proxy.jpg").unlink()

    response = client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "bailian", "model": "qwen3.7-flash", "disclosureAccepted": True,
    })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "semantic_analysis_preprocessing_invalid"


def test_analysis_rejects_a_model_outside_the_provider_catalog(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "not-a-bailian-vision-model",
    }).status_code == 200

    response = client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "bailian", "model": "not-a-bailian-vision-model", "disclosureAccepted": True,
    })

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_analysis_model"


def test_local_compatible_analysis_allows_an_optional_credential(tmp_path):
    holder = {}
    credentials = InMemoryCredentials()
    credentials.values.clear()

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=credentials,
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/local_openai_compatible/configuration", json={
        "model": "local-vision", "baseUrl": "http://localhost:1234/v1",
    }).status_code == 200

    response = client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "local_openai_compatible", "model": "local-vision", "disclosureAccepted": True,
    })

    assert response.status_code == 202
    assert response.json()["semanticAnalysis"]["status"] == "queued"


def test_application_restart_marks_queued_analysis_as_retryable_failure(tmp_path):
    initial = TestClient(create_app(data_dir=tmp_path))
    project_id = ready_project(initial, tmp_path)
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    payload[0]["semanticAnalysis"] = new_semantic_analysis(
        reference_media_id="image-001",
        preprocessing_id="preprocessing-001",
        provider="bailian",
        model="qwen3.7-flash",
        now=datetime.now(timezone.utc),
    ).model_dump()
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")

    restarted = TestClient(create_app(data_dir=tmp_path))
    task = restarted.get(f"/api/projects/{project_id}").json()["semanticAnalysis"]

    assert task["status"] == "failed"
    assert task["error"] == {
        "code": "semantic_analysis_interrupted",
        "message": "本地服务曾退出，语义分析已中断，请重新开始。",
        "retryable": True,
    }


def test_analysis_failure_logs_and_response_do_not_leak_sensitive_provider_data(tmp_path, caplog):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    def runner(**kwargs):
        raise RuntimeError(
            f"Authorization: Bearer top-secret data:image/jpeg;base64,LEAK {tmp_path} supplier-full-response"
        )

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
        semantic_analysis_runner=runner,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    }).status_code == 200
    assert client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "bailian", "model": "qwen3.7-flash", "disclosureAccepted": True,
    }).status_code == 202

    holder["queue"].run_next()

    response = client.get(f"/api/projects/{project_id}")
    combined = caplog.text + response.text
    for forbidden in ("top-secret", "Authorization", "data:image/jpeg;base64,LEAK", str(tmp_path), "supplier-full-response"):
        assert forbidden not in combined
    assert response.json()["semanticAnalysis"]["error"]["code"] == "semantic_analysis_unexpected_error"


def test_connection_test_uses_configured_provider_without_reading_project_media(tmp_path):
    calls = []

    class ProbeProvider:
        def test_connection(self, model):
            calls.append(model)

    def registry(**kwargs):
        assert kwargs["credential"] == "test-key"
        assert kwargs["base_url"] is None
        return ProbeProvider()

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        provider_registry=registry,
    ))
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    }).status_code == 200

    response = client.post("/api/analysis-providers/bailian/test-connection")

    assert response.status_code == 200
    assert response.json() == {"provider": "bailian", "model": "qwen3.7-flash", "status": "connected"}
    assert calls == ["qwen3.7-flash"]


def test_queued_analysis_runs_once_and_persists_a_completed_result(tmp_path):
    holder = {}
    calls = []

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    def runner(**kwargs):
        calls.append(kwargs)
        return StructuredVisualAnalysis.model_validate({
            "observedFacts": {
                "staticVisual": {
                    "subject": "人物", "scene": "室内", "composition": "中景",
                    "viewpoint": "平视", "lighting": "柔光", "color": "暖色",
                    "visualStyle": "写实",
                },
                "temporal": None,
            },
            "generationSuggestions": {
                "subjectMotion": "轻微动作", "environmentalMotion": "无",
                "cameraMotion": "固定", "rhythm": "平稳",
                "suggestedDuration": 3, "audio": "环境声",
            },
        })

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
        semantic_analysis_runner=runner,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    }).status_code == 200
    assert client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "bailian", "model": "qwen3.7-flash", "disclosureAccepted": True,
    }).status_code == 202

    holder["queue"].run_next()

    task = client.get(f"/api/projects/{project_id}").json()["semanticAnalysis"]
    assert task["status"] == "completed"
    assert task["result"]["observedFacts"]["staticVisual"]["subject"] == "人物"
    assert task["error"] is None
    assert len(calls) == 1
    assert calls[0]["model"] == "qwen3.7-flash"


def test_matching_completed_analysis_is_idempotent(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    def runner(**kwargs):
        return StructuredVisualAnalysis.model_validate({
            "observedFacts": {"staticVisual": {
                "subject": "人物", "scene": "室内", "composition": "中景", "viewpoint": "平视",
                "lighting": "柔光", "color": "暖色", "visualStyle": "写实",
            }, "temporal": None},
            "generationSuggestions": {
                "subjectMotion": "轻微动作", "environmentalMotion": "无", "cameraMotion": "固定",
                "rhythm": "平稳", "suggestedDuration": 3, "audio": "环境声",
            },
        })

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
        semantic_analysis_runner=runner,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    }).status_code == 200
    request = {"provider": "bailian", "model": "qwen3.7-flash", "disclosureAccepted": True}
    assert client.post(f"/api/projects/{project_id}/semantic-analysis", json=request).status_code == 202
    holder["queue"].run_next()

    response = client.post(f"/api/projects/{project_id}/semantic-analysis", json=request)

    assert response.status_code == 200
    assert response.json()["semanticAnalysis"]["status"] == "completed"
    assert holder["queue"].pending == []

    completed = response.json()["semanticAnalysis"]
    semantic_analysis_checkpoint_path(tmp_path, project_id, completed["id"]).write_text(
        '{"not":"a semantic checkpoint"}', encoding="utf-8",
    )
    retried = client.post(f"/api/projects/{project_id}/semantic-analysis", json=request)

    assert retried.status_code == 202
    assert retried.json()["semanticAnalysis"]["id"] != completed["id"]


def test_replacing_reference_media_clears_existing_semantic_analysis(tmp_path):
    holder = {}

    def queue_factory(handler):
        holder["queue"] = ManualQueue(handler)
        return holder["queue"]

    client = TestClient(create_app(
        data_dir=tmp_path,
        credential_store=InMemoryCredentials(),
        analysis_settings=AnalysisSettings(tmp_path / "analysis-providers.json"),
        semantic_analysis_queue_factory=queue_factory,
    ))
    project_id = ready_project(client, tmp_path)
    assert client.put("/api/analysis-providers/bailian/configuration", json={
        "model": "qwen3.7-flash",
    }).status_code == 200
    assert client.post(f"/api/projects/{project_id}/semantic-analysis", json={
        "provider": "bailian", "model": "qwen3.7-flash", "disclosureAccepted": True,
    }).status_code == 202
    image = Image.new("RGB", (256, 256), (1, 2, 3))
    payload = BytesIO()
    image.save(payload, format="PNG")

    response = client.put(
        f"/api/projects/{project_id}/reference-media",
        files={"file": ("replacement.png", payload.getvalue(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["semanticAnalysis"] is None
