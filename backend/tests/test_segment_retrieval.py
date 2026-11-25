import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.preproduction import PreproductionStore
from app.main import create_app
from app.segment_retrieval import (
    AnalysisProviderSegmentReranker,
    DOUBAO_RERANK_MODEL,
    RerankProviderSnapshot,
    SegmentRerankRequest,
    SegmentRerankError,
    Siglip2VisualVectorizer,
    create_segment_retrieval_router,
    load_planning_search,
    PlanningSearchError,
)
from app.source_segment_index import KEYFRAME_MODEL_VERSION, SEGMENT_ALGORITHM_VERSION


PROJECT_ID = "project-1"
INTENT = {"x-aivre-intent": "semantic-analysis"}


class FixedVectorizer:
    version = "fixed-visual-v1"

    def embed_text(self, _query: str) -> list[float]:
        return [1.0, 0.0]

    def embed_image(self, path: Path) -> list[float]:
        return {
            "keyframe-1.jpg": [1.0, 0.0],
            "keyframe-2.jpg": [0.8, 0.2],
            "keyframe-3.jpg": [0.0, 1.0],
        }[path.name]


def test_siglip_vectorizer_truncates_long_queries_to_model_limit(tmp_path: Path):
    calls = []

    class Processor:
        def __call__(self, **kwargs):
            calls.append(kwargs)
            return {}

    class Model:
        config = SimpleNamespace(text_config=SimpleNamespace(max_position_embeddings=64))

        def get_text_features(self, **_inputs):
            return object()

    class Torch:
        @staticmethod
        def no_grad():
            return nullcontext()

    vectorizer = Siglip2VisualVectorizer(tmp_path)
    vectorizer._runtime = (Torch(), Model(), Processor())
    vectorizer._values = lambda _features: [1.0]

    assert vectorizer.embed_text("商品动作" * 125) == [1.0]
    assert calls == [{
        "text": ["商品动作" * 125],
        "return_tensors": "pt",
        "padding": "max_length",
        "truncation": True,
        "max_length": 64,
    }]


class FakeReranker:
    def __init__(self):
        self.configuration_revision = "doubao-config-1"
        self.model = DOUBAO_RERANK_MODEL
        self.requests = []
        self.error = None
        self.snapshot_error = None
        self.response = None
        self.change_revision_during_call = False

    def snapshot(self):
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return RerankProviderSnapshot(
            provider="doubao",
            model=self.model,
            configuration_revision=self.configuration_revision,
        )

    def rerank(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.change_revision_during_call:
            self.configuration_revision = "doubao-config-2"
        if self.response is not None:
            return self.response
        return json.dumps({"ranking": [
            {"candidateId": "segment-3", "reason": "蓝色收尾画面更符合查询重点", "confidence": 0.91},
            {"candidateId": "segment-1", "reason": "红色主体清晰可见", "confidence": 0.72},
        ]}, ensure_ascii=False)


def install_completed_index(tmp_path: Path) -> None:
    store = PreproductionStore(tmp_path)
    state = store.load(PROJECT_ID)
    state["assets"].append({
        "id": "asset-a", "name": "咖啡冲泡长视频", "kind": "video", "role": "motion",
        "file": "asset-a.mp4", "duration": 10,
    })
    store.save(PROJECT_ID, state)
    source = store.path(PROJECT_ID, "assets", "asset-a.mp4")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source-video")

    run_id = "segment-run-a"
    run = tmp_path / "project-files" / PROJECT_ID / "source-segments" / "asset-a" / "runs" / run_id
    run.mkdir(parents=True)
    for index, color in enumerate(((220, 30, 30), (190, 60, 40), (20, 30, 220)), start=1):
        Image.new("RGB", (160, 90), color).save(run / f"keyframe-{index}.jpg")
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    candidates = []
    for index, (start, end) in enumerate(((0.0, 2.0), (2.0, 7.0), (7.0, 10.0)), start=1):
        candidates.append({
            "id": f"segment-{index}", "assetId": "asset-a", "sourceFingerprint": fingerprint,
            "algorithmVersion": SEGMENT_ALGORITHM_VERSION, "keyframeModelVersion": KEYFRAME_MODEL_VERSION,
            "inPointSeconds": start, "outPointSeconds": end,
            "keyframe": {"timeSeconds": (start + end) / 2, "_filename": f"keyframe-{index}.jpg"},
        })
    index_path = run.parents[1] / "state.json"
    index_path.write_text(json.dumps({
        "schemaVersion": 1, "assetId": "asset-a", "runId": run_id, "attemptId": "attempt-a",
        "status": "completed", "sourceFingerprint": fingerprint,
        "algorithmVersion": SEGMENT_ALGORITHM_VERSION, "keyframeModelVersion": KEYFRAME_MODEL_VERSION,
        "queuedAt": "2026-09-30T00:00:00+00:00", "startedAt": "2026-09-30T00:00:01+00:00",
        "completedAt": "2026-09-30T00:00:02+00:00", "updatedAt": "2026-09-30T00:00:02+00:00",
        "error": None, "validity": {"status": "current", "reason": None}, "candidates": candidates,
    }), encoding="utf-8")


def client_for(tmp_path: Path, *, vectorizer=FixedVectorizer(), reranker=None) -> TestClient:
    app = FastAPI()
    app.include_router(create_segment_retrieval_router(
        tmp_path,
        lambda project_id: {"id": project_id} if project_id == PROJECT_ID else None,
        vectorizer=vectorizer,
        reranker=reranker,
    ))
    return TestClient(app)


def test_local_visual_search_keeps_same_source_segments_and_returns_evidence(tmp_path):
    install_completed_index(tmp_path)
    client = client_for(tmp_path)

    response = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches",
        json={"query": "红色咖啡冲泡动作", "limit": 3},
        headers=INTENT,
    )

    assert response.status_code == 201, response.text
    search = response.json()
    assert search["query"] == "红色咖啡冲泡动作"
    assert search["vectorizerVersion"] == "fixed-visual-v1"
    assert search["validity"] == {"status": "current", "reason": None}
    results = search["localCandidates"]
    assert [item["candidateId"] for item in results] == ["segment-1", "segment-2", "segment-3"]
    assert {item["assetId"] for item in results} == {"asset-a"}
    assert [(item["inPointSeconds"], item["outPointSeconds"]) for item in results] == [
        (0.0, 2.0), (2.0, 7.0), (7.0, 10.0),
    ]
    assert results[0]["visualRelevance"] > results[1]["visualRelevance"] > results[2]["visualRelevance"]
    assert results[0]["quality"]["evidence"]["width"] == 160
    assert results[1]["duplicateRisk"]["level"] == "high"
    assert results[0]["preview"]["url"].endswith("/preproduction/assets/asset-a/file#t=0.000,2.000")
    assert results[0]["keyframe"]["url"].endswith("/candidates/segment-1/keyframe")
    assert search["rerank"] is None
    assert "咖啡冲泡长视频" not in json.dumps(results, ensure_ascii=False)

    reopened = client_for(tmp_path).get(f"/api/projects/{PROJECT_ID}/segment-searches")
    assert reopened.status_code == 200
    assert [item["id"] for item in reopened.json()["searches"]] == [search["id"]]


def test_disclosed_rerank_sends_only_selected_keyframes_and_binds_provider_revision(tmp_path):
    install_completed_index(tmp_path)
    reranker = FakeReranker()
    client = client_for(tmp_path, reranker=reranker)
    search = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches",
        json={"query": "展示咖啡产品并以蓝色画面收尾", "limit": 3},
        headers=INTENT,
    ).json()
    selected = ["segment-1", "segment-3"]

    disclosure = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank-disclosure",
        json={"candidateIds": selected},
        headers=INTENT,
    )

    assert disclosure.status_code == 200, disclosure.text
    preview = disclosure.json()
    assert preview["provider"] == "doubao"
    assert preview["model"] == DOUBAO_RERANK_MODEL
    assert preview["configurationRevision"] == "doubao-config-1"
    assert preview["query"] == "展示咖啡产品并以蓝色画面收尾"
    assert [item["candidateId"] for item in preview["candidates"]] == selected
    assert [(item["inPointSeconds"], item["outPointSeconds"]) for item in preview["candidates"]] == [
        (0.0, 2.0), (7.0, 10.0),
    ]
    assert preview["notSent"] == ["完整视频", "音频", "未选择的项目素材与候选"]
    assert preview["sentContent"]["text"] == (
        preview["sentContent"]["prompt"] + "\n" + preview["sentContent"]["analysisContext"]
    )
    assert "展示咖啡产品并以蓝色画面收尾" in preview["sentContent"]["text"]
    assert "segment-1" in preview["sentContent"]["text"]
    assert "segment-2" not in preview["sentContent"]["text"]
    assert '"width":640' in preview["sentContent"]["analysisContext"]

    unconfirmed = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank",
        json={"candidateIds": selected, "digest": preview["digest"], "disclosureAccepted": False},
        headers=INTENT,
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["detail"]["code"] == "rerank_disclosure_required"
    assert reranker.requests == []

    completed = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank",
        json={"candidateIds": selected, "digest": preview["digest"], "disclosureAccepted": True},
        headers=INTENT,
    )

    assert completed.status_code == 200, completed.text
    result = completed.json()["rerank"]
    assert result["status"] == "completed"
    assert result["configurationRevision"] == "doubao-config-1"
    assert result["model"] == DOUBAO_RERANK_MODEL
    assert [item["candidateId"] for item in result["ranking"]] == ["segment-3", "segment-1"]
    assert result["ranking"][0]["reason"] == "蓝色收尾画面更符合查询重点"
    assert result["ranking"][0]["confidence"] == 0.91
    assert result["validity"] == {"status": "current", "reason": None}
    assert len(completed.json()["rerankHistory"]) == 1

    sent = reranker.requests[0]
    assert sent.query == "展示咖啡产品并以蓝色画面收尾"
    assert [item["candidateId"] for item in sent.candidates] == selected
    assert sent.prompt == preview["sentContent"]["prompt"]
    assert sent.analysis_context == preview["sentContent"]["analysisContext"]
    assert sent.configuration_revision == "doubao-config-1"
    assert sent.contact_sheet_bytes.startswith(b"\xff\xd8")
    assert sent.contact_sheet_bytes.endswith(b"\xff\xd9")
    assert b"source-video" not in sent.contact_sheet_bytes


def test_planning_search_expected_no_rerank_becomes_stale_when_rerank_is_added(tmp_path):
    install_completed_index(tmp_path)
    reranker = FakeReranker()
    client = client_for(tmp_path, reranker=reranker)
    search = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches",
        json={"query": "咖啡产品动作", "limit": 3}, headers=INTENT,
    ).json()

    assert load_planning_search(
        tmp_path, PROJECT_ID, search["id"], reranker=reranker,
        vectorizer_version=FixedVectorizer.version, expected_rerank_id=None,
    )["rerankId"] is None
    selected = ["segment-1", "segment-3"]
    disclosure = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank-disclosure",
        json={"candidateIds": selected}, headers=INTENT,
    ).json()
    completed = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank",
        json={"candidateIds": selected, "digest": disclosure["digest"], "disclosureAccepted": True},
        headers=INTENT,
    )
    assert completed.status_code == 200, completed.text

    with pytest.raises(PlanningSearchError) as caught:
        load_planning_search(
            tmp_path, PROJECT_ID, search["id"], reranker=reranker,
            vectorizer_version=FixedVectorizer.version, expected_rerank_id=None,
        )
    assert caught.value.code == "segment_rerank_stale"


def start_search_and_disclose(client: TestClient):
    search = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches",
        json={"query": "咖啡产品动作", "limit": 3},
        headers=INTENT,
    ).json()
    selected = ["segment-1", "segment-3"]
    disclosure = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank-disclosure",
        json={"candidateIds": selected}, headers=INTENT,
    ).json()
    return search, selected, disclosure


def test_provider_timeout_keeps_local_candidates_and_records_failed_rerank(tmp_path):
    install_completed_index(tmp_path)
    reranker = FakeReranker()
    reranker.error = SegmentRerankError("timeout", "豆包视觉复排响应超时，请稍后重试。", True)
    client = client_for(tmp_path, reranker=reranker)
    search, selected, disclosure = start_search_and_disclose(client)

    response = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank",
        json={"candidateIds": selected, "digest": disclosure["digest"], "disclosureAccepted": True},
        headers=INTENT,
    )

    assert response.status_code == 504
    reopened = client.get(f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}").json()
    assert len(reopened["localCandidates"]) == 3
    assert reopened["rerank"]["status"] == "failed"
    assert reopened["rerank"]["error"] == {
        "code": "timeout", "message": "豆包视觉复排响应超时，请稍后重试。", "retryable": True,
    }
    assert reopened["rerank"]["ranking"] == []


def test_unavailable_doubao_blocks_disclosure_but_keeps_local_search(tmp_path):
    install_completed_index(tmp_path)
    reranker = FakeReranker()
    reranker.snapshot_error = SegmentRerankError(
        "provider_unconfigured", "请先配置并验证豆包视觉复排服务。", False,
    )
    client = client_for(tmp_path, reranker=reranker)
    search = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches",
        json={"query": "咖啡动作", "limit": 3}, headers=INTENT,
    ).json()

    response = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank-disclosure",
        json={"candidateIds": ["segment-1"]}, headers=INTENT,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "provider_unconfigured"
    assert len(client.get(f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}").json()["localCandidates"]) == 3


def test_invalid_or_late_configuration_rerank_never_replaces_local_results(tmp_path):
    install_completed_index(tmp_path)
    reranker = FakeReranker()
    reranker.response = '{"ranking":[{"candidateId":"not-selected","reason":"越界","confidence":1.0}]}'
    client = client_for(tmp_path, reranker=reranker)
    search, selected, disclosure = start_search_and_disclose(client)

    invalid = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank",
        json={"candidateIds": selected, "digest": disclosure["digest"], "disclosureAccepted": True},
        headers=INTENT,
    )
    assert invalid.status_code == 502
    failed = client.get(f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}").json()
    assert failed["rerank"]["error"]["code"] == "invalid_rerank_response"
    assert len(failed["localCandidates"]) == 3

    reranker.response = None
    reranker.change_revision_during_call = True
    new_disclosure = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank-disclosure",
        json={"candidateIds": selected}, headers=INTENT,
    ).json()
    late = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank",
        json={"candidateIds": selected, "digest": new_disclosure["digest"], "disclosureAccepted": True},
        headers=INTENT,
    )
    assert late.status_code == 409
    reopened = client.get(f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}").json()
    assert reopened["rerank"]["status"] == "failed"
    assert reopened["rerank"]["error"]["code"] == "provider_configuration_changed"
    assert len(reopened["rerankHistory"]) == 2


def test_index_model_and_configuration_changes_mark_history_stale_without_deleting_it(tmp_path):
    install_completed_index(tmp_path)
    reranker = FakeReranker()
    client = client_for(tmp_path, reranker=reranker)
    search, selected, disclosure = start_search_and_disclose(client)
    completed = client.post(
        f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}/rerank",
        json={"candidateIds": selected, "digest": disclosure["digest"], "disclosureAccepted": True},
        headers=INTENT,
    ).json()
    assert completed["rerank"]["validity"] == {"status": "current", "reason": None}

    reranker.configuration_revision = "doubao-config-2"
    changed_config = client.get(f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}").json()
    assert changed_config["rerank"]["validity"] == {
        "status": "stale", "reason": "provider_configuration_changed",
    }

    reranker.model = "doubao-seed-next"
    changed_model = client.get(f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}").json()
    assert changed_model["rerank"]["validity"] == {"status": "stale", "reason": "model_changed"}

    source = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "asset-a.mp4"
    source.write_bytes(b"changed-source")
    changed_index = client.get(f"/api/projects/{PROJECT_ID}/segment-searches/{search['id']}").json()
    assert changed_index["validity"] == {"status": "stale", "reason": "segment_index_changed"}
    assert changed_index["rerank"]["validity"] == {"status": "stale", "reason": "segment_index_changed"}
    assert len(changed_index["rerankHistory"]) == 1
    assert [item["candidateId"] for item in changed_index["rerank"]["ranking"]] == ["segment-3", "segment-1"]


def test_doubao_reranker_protocol_sends_contact_sheet_query_and_strict_schema_only():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{"type": "message", "content": [{
                "type": "output_text",
                "text": '{"ranking":[{"candidateId":"segment-1","reason":"主体可见","confidence":0.8}]}',
            }]}],
        })

    class Configurations:
        def require_configured(self, provider, model, *, require_verified):
            assert (provider, model, require_verified) == ("doubao", DOUBAO_RERANK_MODEL, True)
            return SimpleNamespace(
                provider="doubao", model=DOUBAO_RERANK_MODEL,
                configurationRevision="config-revision", verificationState="available",
            )

        def use_provider(
            self, *, provider, model, operation, require_verified,
            expected_configuration_revision,
        ):
            from app.analysis_providers.doubao import DoubaoAnalysisProvider

            assert (provider, model, require_verified) == ("doubao", DOUBAO_RERANK_MODEL, True)
            assert expected_configuration_revision == "config-revision"
            client = httpx.Client(transport=httpx.MockTransport(handler))
            try:
                return operation(DoubaoAnalysisProvider(client, credential="test-key"), DOUBAO_RERANK_MODEL)
            finally:
                client.close()

    image = Image.new("RGB", (32, 24), "red")
    from io import BytesIO

    stream = BytesIO()
    image.save(stream, format="JPEG")
    gateway = AnalysisProviderSegmentReranker(Configurations())
    raw = gateway.rerank(SegmentRerankRequest(
        query="红色商品",
        candidates=[{
            "candidateId": "segment-1", "assetId": "asset-a",
            "inPointSeconds": 0.0, "outPointSeconds": 2.0, "keyframeTimeSeconds": 1.0,
        }],
        prompt="只按所选关键帧复排。查询：红色商品；候选：segment-1",
        analysis_context='{"mediaType":"image","width":32,"height":24,"aspectRatio":1.3333333333333333}',
        configuration_revision="config-revision",
        contact_sheet_bytes=stream.getvalue(),
        contact_sheet_width=32,
        contact_sheet_height=24,
        response_schema={
            "type": "object",
            "properties": {"ranking": {"type": "array", "items": {"type": "object", "properties": {
                "candidateId": {"type": "string"}, "reason": {"type": "string"}, "confidence": {"type": "number"},
            }, "additionalProperties": False}}},
            "additionalProperties": False,
        },
    ))

    assert json.loads(raw)["ranking"][0]["candidateId"] == "segment-1"
    body = json.loads(captured[0].content)
    assert body["model"] == DOUBAO_RERANK_MODEL and body["store"] is False
    assert body["text"]["format"]["strict"] is True
    content = body["input"][0]["content"]
    assert [item["type"] for item in content].count("input_image") == 1
    assert [item["text"] for item in content if item["type"] == "input_text"] == [
        "只按所选关键帧复排。查询：红色商品；候选：segment-1",
        '{"mediaType":"image","width":32,"height":24,"aspectRatio":1.3333333333333333}',
    ]
    assert all("segment-2" not in str(item) and "source-video" not in str(item) for item in content)


def test_main_app_mounts_segment_search_and_protects_mutations(tmp_path):
    (tmp_path / "projects.json").write_text(json.dumps([{
        "id": PROJECT_ID, "name": "商品项目",
        "createdAt": "2026-09-30T00:00:00+00:00", "updatedAt": "2026-09-30T00:00:00+00:00",
    }]), encoding="utf-8")
    install_completed_index(tmp_path)
    client = TestClient(create_app(
        tmp_path,
        segment_visual_vectorizer=FixedVectorizer(),
        segment_reranker=FakeReranker(),
    ))
    url = f"/api/projects/{PROJECT_ID}/segment-searches"

    denied = client.post(
        url, json={"query": "咖啡冲泡", "limit": 3},
        headers={"origin": "http://127.0.0.1:5173"},
    )
    allowed = client.post(
        url, json={"query": "咖啡冲泡", "limit": 3},
        headers={"origin": "http://127.0.0.1:5173", **INTENT},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 201
    assert len(allowed.json()["localCandidates"]) == 3
