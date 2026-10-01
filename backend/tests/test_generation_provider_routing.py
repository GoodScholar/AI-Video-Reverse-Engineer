import json
import io
from threading import Event, Thread

import pytest
from PIL import Image

from app.generation_routing import GenerationRoutingConflict, GenerationRoutingPolicyStore
from app.generation_service import GenerationAdapter, GenerationAdapterRegistry, GenerationError, GenerationService, VideoGenerationAdapter
from app.image_generation_adapter import ConfiguredImageGenerationAdapter


def _caps():
    return {
        "version": "routing-test-v1", "structuredSchema": True, "streaming": False,
        "stableReferences": False, "outputBudget": False, "timeout": False, "cancel": False,
    }


def _image_caps():
    return {
        "version": "image-test-v1", "referenceTypes": [], "minReferences": 0, "maxReferences": 0,
        "imageEditing": False, "superResolution": False, "aspectRatios": ["1:1"], "resolutions": ["1024x1024"],
        "minResults": 1, "maxResults": 1, "cancel": False, "timeout": False,
    }


def _png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (1, 1), "green").save(output, format="PNG")
    return output.getvalue()


class _HeldQueue:
    def __init__(self):
        self.job = None

    def submit(self, _kind, _key, job, **_kwargs):
        self.job = job
        return True


def test_policy_store_uses_cas_and_never_persists_provider_secrets(tmp_path):
    store = GenerationRoutingPolicyStore(tmp_path / "generation-routing.json")

    created = store.save({
        "id": "text-safe", "name": "文本备用", "modality": "text",
        "routes": [{"provider": "primary", "model": "model-a"}, {"provider": "backup", "model": "model-b"}],
    }, expected_revision=0)

    assert created["revision"] == 1
    assert store.get("text-safe", revision=1)["routes"][1] == {"provider": "backup", "model": "model-b"}
    assert store.list(modality="text")["policies"] == [{
        "id": "text-safe", "name": "文本备用", "modality": "text",
        "routes": [{"provider": "primary", "model": "model-a"}, {"provider": "backup", "model": "model-b"}], "revision": 1,
    }]
    with pytest.raises(GenerationRoutingConflict):
        store.save({key: value for key, value in created.items() if key != "revision"} | {"name": "过期写入"}, expected_revision=0)
    raw = (tmp_path / "generation-routing.json").read_text(encoding="utf-8")
    assert "apiKey" not in raw and "https://" not in raw and "credential" not in raw


def test_explicit_policy_freezes_candidates_and_only_falls_back_after_definitive_non_billable_failure(tmp_path):
    registry = GenerationAdapterRegistry()

    def capabilities(provider, model):
        if (provider, model) not in {("primary", "model-a"), ("backup", "model-b")}:
            return None
        return {"provider": provider, "model": model, "configurationVersion": f"cfg-{provider}", "capabilities": _caps()}

    def execute(run):
        if run["provider"] == "primary":
            raise GenerationError("generation_request_rejected", billing_unknown=False, safe_to_fallback=True)
        return "fallback result"

    registry.register(GenerationAdapter("routing-text", "adapter-v1", "text", execute, capabilities))
    policies = GenerationRoutingPolicyStore(tmp_path / "generation-routing.json")
    policy = policies.save({
        "id": "text-safe", "name": "文本备用", "modality": "text",
        "routes": [{"provider": "primary", "model": "model-a"}, {"provider": "backup", "model": "model-b"}],
    }, expected_revision=0)
    service = GenerationService(tmp_path, registry=registry, routing_policies=policies)

    run = service.submit(
        "project", disclosure={"provider": "primary", "model": "model-a", "prompt": "x", "accepted": True},
        text_params={}, idempotency_key="policy-fallback", caller="test",
        routing_policy_ref={"id": "text-safe", "revision": policy["revision"]},
    )

    assert run["status"] == "completed"
    assert run["actualRoute"] == {"provider": "backup", "model": "model-b"}
    assert run["routingPolicySnapshot"] == {
        "id": "text-safe", "revision": 1, "modality": "text",
        "candidates": [{"provider": "primary", "model": "model-a"}, {"provider": "backup", "model": "model-b"}],
    }
    assert [item["fallbackDecision"] for item in run["routeAttempts"]] == ["fallback", "completed"]
    assert run["routeAttempts"][0]["failureCode"] == "generation_request_rejected"
    assert run["routeAttempts"][1]["adapterVersion"] == "adapter-v1"


def test_unknown_or_billable_failure_never_uses_the_next_saved_route(tmp_path):
    registry = GenerationAdapterRegistry()
    calls = []

    def capabilities(provider, model):
        return {"provider": provider, "model": model, "configurationVersion": "cfg", "capabilities": _caps()}

    def execute(run):
        calls.append(run["provider"])
        raise GenerationError("generation_submit_unknown", outcome="unknown", billing_unknown=True)

    registry.register(GenerationAdapter("routing-text", "adapter-v1", "text", execute, capabilities))
    policies = GenerationRoutingPolicyStore(tmp_path / "generation-routing.json")
    policy = policies.save({"id": "p", "name": "p", "modality": "text", "routes": [
        {"provider": "primary", "model": "a"}, {"provider": "backup", "model": "b"},
    ]}, expected_revision=0)
    run = GenerationService(tmp_path, registry=registry, routing_policies=policies).submit(
        "project", disclosure={"provider": "primary", "model": "a", "prompt": "x", "accepted": True},
        text_params={}, idempotency_key="unknown", caller="test", routing_policy_ref={"id": "p", "revision": policy["revision"]},
    )

    assert calls == ["primary"]
    assert run["billingUnknown"] is True
    assert run["routeAttempts"][-1]["fallbackDecision"] == "fallback_blocked_unknown_outcome"


def test_text_partial_output_never_calls_backup_even_when_error_is_marked_safe(tmp_path):
    registry = GenerationAdapterRegistry()
    calls = []

    def capabilities(provider, model):
        return {"provider": provider, "model": model, "configurationVersion": "cfg", "capabilities": _caps()}

    def execute(run):
        calls.append(run["provider"])
        if run["provider"] == "primary":
            def chunks():
                yield {"sequence": 0, "text": "已产生的文本"}
                raise GenerationError("generation_request_rejected", safe_to_fallback=True)
            return chunks()
        return "不应执行备用模型"

    registry.register(GenerationAdapter("routing-text", "adapter-v1", "text", execute, capabilities))
    policies = GenerationRoutingPolicyStore(tmp_path / "generation-routing.json")
    policy = policies.save({"id": "p", "name": "p", "modality": "text", "routes": [
        {"provider": "primary", "model": "a"}, {"provider": "backup", "model": "b"},
    ]}, expected_revision=0)

    run = GenerationService(tmp_path, registry=registry, routing_policies=policies).submit(
        "project", disclosure={"provider": "primary", "model": "a", "prompt": "x", "accepted": True},
        text_params={}, idempotency_key="partial", caller="test", routing_policy_ref={"id": "p", "revision": policy["revision"]},
    )

    assert calls == ["primary"]
    assert run["outputItems"][0]["text"] == "已产生的文本"
    assert run["routeAttempts"][-1]["fallbackDecision"] == "fallback_blocked_partial_result"


@pytest.mark.parametrize("modality", ["text", "image", "video"])
def test_each_modality_falls_back_only_from_a_frozen_definitive_non_billable_submit_stage(tmp_path, modality):
    registry = GenerationAdapterRegistry()
    if modality == "video":
        registry.register(VideoGenerationAdapter(
            "primary", "v1", "video", lambda *_: None, lambda: [], lambda *_: {}, lambda *_: {}, lambda *_: None,
        ))
        registry.register(VideoGenerationAdapter(
            "backup", "v1", "video", lambda *_: None, lambda: [], lambda *_: {}, lambda *_: {}, lambda *_: None,
        ))
    else:
        registry.register(GenerationAdapter("primary", "v1", modality, lambda _: None, lambda *_: None))
        registry.register(GenerationAdapter("backup", "v1", modality, lambda _: None, lambda *_: None))
    service = GenerationService(tmp_path, registry=registry)
    if modality == "video":
        service._submit_video_remote = lambda *_: None  # type: ignore[method-assign]
    else:
        service._execute = lambda *_: None  # type: ignore[method-assign]
    capabilities = {"version": "frozen-v1", "cancel": False}
    run = {
        "id": "run", "modality": modality, "status": "submitting" if modality == "video" else "running",
        "provider": "primary-provider", "model": "primary-model", "adapterId": "primary", "adapterVersion": "v1",
        "configurationVersion": "cfg-primary", "capabilitySnapshot": capabilities, "actualRoute": {"provider": "primary-provider", "model": "primary-model"},
        "routingPolicySnapshot": {"id": "p", "revision": 1, "modality": modality, "candidates": [{"provider": "primary-provider", "model": "primary-model"}, {"provider": "backup-provider", "model": "backup-model"}]},
        "routingCandidateSnapshots": [
            {"provider": "primary-provider", "model": "primary-model", "adapterId": "primary", "adapterVersion": "v1", "configurationVersion": "cfg-primary", "capabilitySnapshot": capabilities},
            {"provider": "backup-provider", "model": "backup-model", "adapterId": "backup", "adapterVersion": "v1", "configurationVersion": "cfg-backup", "capabilitySnapshot": capabilities},
        ],
        "routeAttempts": [{"provider": "primary-provider", "model": "primary-model", "adapterId": "primary", "adapterVersion": "v1", "configurationVersion": "cfg-primary", "capabilityVersion": "frozen-v1", "status": "selected", "failureCode": None, "fallbackDecision": "selected"}],
        "logs": [], "outputItems": [{"status": "pending"}], "remoteTaskId": None,
    }
    service.store.save("project", [run])
    service.recovered_projects.add("project")

    assert service._start_safe_fallback("project", "run", GenerationError("rejected", billing_unknown=False, safe_to_fallback=True))
    changed = service.get_run("project", "run")
    assert changed["actualRoute"] == {"provider": "backup-provider", "model": "backup-model"}
    assert changed["configurationVersion"] == "cfg-backup"
    assert changed["routeAttempts"][-1]["adapterVersion"] == "v1"


@pytest.mark.parametrize("modality", ["text", "image", "video"])
def test_public_lifecycle_projection_keeps_route_audit_but_hides_disclosure_and_private_provider_fields(tmp_path, modality):
    service = GenerationService(tmp_path)
    public = service.public_run({
        "id": "run", "modality": modality, "status": "running", "outcome": None,
        "provider": "safe-provider", "model": "safe-model", "billingUnknown": True,
        "actualRoute": {"provider": "safe-provider", "model": "safe-model"},
        "routeAttempts": [{"provider": "safe-provider", "model": "safe-model", "failureCode": None}],
        "disclosure": {"prompt": "canary-secret"}, "remoteTaskId": "private-task", "pollCursor": "private-cursor",
        "input": {"prompt": "canary-secret"}, "logs": [{"providerError": "canary-secret"}],
        "outputItems": [{"resultId": "result", "status": "pending", "remoteResultId": "private-result"}],
    })

    serialized = json.dumps(public, ensure_ascii=False)
    assert "canary-secret" not in serialized and "private-task" not in serialized and "private-result" not in serialized
    assert public["executionSemantics"] == {
        "cancel": "unsupported", "cancelSupport": "unsupported", "cancelResult": "unsupported",
        "stateConfidence": "known", "late": False, "partial": False,
        "billing": "unknown", "actualRoute": {"provider": "safe-provider", "model": "safe-model"},
    }


def test_application_policy_is_not_written_into_project_backup(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    canary = "routing-policy-canary-secret"
    policy = GenerationRoutingPolicyStore(tmp_path / "generation-routing.json")
    policy.save({
        "id": "safe", "name": "safe", "modality": "text",
        "routes": [{"provider": "provider-without-secret", "model": "model-without-secret"}],
    }, expected_revision=0)
    (tmp_path / "generation-routing.json").write_text(
        (tmp_path / "generation-routing.json").read_text(encoding="utf-8") + canary,
        encoding="utf-8",
    )
    with TestClient(create_app(data_dir=tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "backup policy boundary"}).json()["id"]
        archive = client.post(f"/api/projects/{project_id}/backup", json={}, headers={"x-aivre-intent": "semantic-analysis"})
    assert archive.status_code == 200
    assert canary.encode() not in archive.content


def test_aigc_workspace_exposes_only_public_text_runs_without_canary(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.aigc_content_api import create_aigc_content_router

    service = GenerationService(tmp_path)
    canary = "workspace-routing-canary"
    service.store.save("project", [{
        "id": "text-run", "modality": "text", "status": "completed", "outcome": "completed",
        "provider": "provider", "model": "model", "billingUnknown": False,
        "input": {"prompt": canary}, "disclosure": {"prompt": canary}, "logs": [{"canary": canary}],
        "routingCandidateSnapshots": [{"remoteTaskId": canary}], "remoteTaskId": canary,
        "outputItems": [{"resultId": "result", "status": "completed", "text": "安全输出"}],
    }, {
        "id": "image-run", "modality": "image", "status": "completed", "outputItems": [],
    }])
    app = FastAPI()
    app.include_router(create_aigc_content_router(tmp_path, lambda _: True, generation_service=service))

    with TestClient(app) as client:
        payload = client.get("/api/projects/project/aigc-content").json()

    assert [run["id"] for run in payload["generationRuns"]] == ["text-run"]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert canary not in encoded
    assert all(key not in encoded for key in ("input", "disclosure", "logs", "routingCandidateSnapshots", "remoteTaskId"))


def test_queued_run_never_executes_newer_adapter_after_catalog_upgrade(tmp_path):
    registry = GenerationAdapterRegistry()
    executed = []
    registry.register(GenerationAdapter("adapter", "v2", "text", lambda run: executed.append(run) or "new", lambda *_: {
        "configurationVersion": "cfg", "capabilities": _caps(),
    }))
    service = GenerationService(tmp_path, registry=registry)
    service.store.save("project", [{
        "id": "old", "modality": "text", "status": "queued", "outcome": None, "provider": "p", "model": "m",
        "adapterId": "adapter", "adapterVersion": "v1", "configurationVersion": "cfg", "capabilitySnapshot": _caps(),
        "input": {"textParams": {}}, "outputItems": [{"resultId": "r", "status": "pending", "chunks": [], "text": ""}],
        "routeAttempts": [{"provider": "p", "model": "m", "adapterId": "adapter", "adapterVersion": "v1", "configurationVersion": "cfg", "capabilityVersion": "routing-test-v1", "status": "selected", "failureCode": None, "fallbackDecision": "selected"}],
        "routingPolicySnapshot": {"mode": "explicit"}, "logs": [], "billingUnknown": False,
    }])
    service.recovered_projects.add("project")

    service._execute("project", "old")

    run = service.get_run("project", "old")
    assert executed == []
    assert run["reason"] == "generation_adapter_version_unavailable"
    assert run["routeAttempts"][-1]["failureCode"] == "generation_adapter_version_unavailable"


def test_text_cancel_then_late_provider_result_is_preserved_as_late_success(tmp_path):
    started = Event()
    release = Event()
    queue = _HeldQueue()
    registry = GenerationAdapterRegistry()
    caps = _caps() | {"cancel": True}
    registry.register(GenerationAdapter(
        "text", "v1", "text",
        lambda _run: (started.set(), release.wait(2), "迟到文本")[2],
        lambda provider, model: {"provider": provider, "model": model, "configurationVersion": "cfg", "capabilities": caps},
        cancel=lambda _run: release.set(),
    ))
    service = GenerationService(tmp_path, registry=registry, queue=queue)
    queued = service.submit(
        "project", disclosure={"provider": "p", "model": "m", "prompt": "x", "accepted": True},
        text_params={}, idempotency_key="late-text", caller="test",
    )
    worker = Thread(target=lambda: queue.job("job"))
    worker.start()
    assert started.wait(2)

    cancelled = service.cancel("project", queued["id"])
    worker.join(2)
    late = service.get_run("project", queued["id"])

    assert cancelled["status"] == "cancelled"
    assert late["status"] == "completed" and late["outcome"] == "late_success"
    assert late["outputItems"][0]["text"] == "迟到文本"
    assert service.public_run(late)["executionSemantics"]["late"] is True


def test_configured_image_adapter_configuration_change_falls_back_before_provider_call(tmp_path):
    class Configurations:
        def __init__(self):
            self.revision = "cfg-1"

        def snapshot(self):
            return {
                "revision": self.revision,
                "providers": [{"providerId": "primary", "credentialState": "configured", "protocol": "fake", "baseUrl": None}],
                "modelCatalog": [{"providerId": "primary", "modelId": "primary-model", "mediaType": "image",
                    "capabilitySource": "verified", "capabilityVersion": "cap-1", "capabilities": _image_caps()}],
            }

    class ImageService:
        def generate(self, *_args, **_kwargs):
            pytest.fail("配置变化后不得发送 primary Provider 请求")

    configurations = Configurations()
    registry = GenerationAdapterRegistry()
    registry.register(ConfiguredImageGenerationAdapter(tmp_path, configurations, ImageService()).as_generation_adapter())
    registry.register(GenerationAdapter(
        "backup-image", "v1", "image",
        lambda _run: iter([{"type": "result", "index": 0, "content": _png_bytes(), "name": "备用.png"}, {"type": "done"}]),
        lambda provider, model: {"provider": provider, "model": model, "configurationVersion": "backup-cfg", "capabilities": _image_caps()},
    ))
    policies = GenerationRoutingPolicyStore(tmp_path / "generation-routing.json")
    policy = policies.save({"id": "image", "name": "图片备用", "modality": "image", "routes": [
        {"provider": "primary", "model": "primary-model"}, {"provider": "backup", "model": "backup-model"},
    ]}, expected_revision=0)
    queue = _HeldQueue()
    service = GenerationService(tmp_path, registry=registry, routing_policies=policies, queue=queue)
    queued = service.submit_image(
        "project", disclosure={"provider": "primary", "model": "primary-model", "prompt": "x", "accepted": True},
        image_params={"operation": "generate", "references": [], "aspectRatio": "1:1", "resolution": "1024x1024", "resultCount": 1},
        idempotency_key="configured-adapter-fallback", caller="test", routing_policy_ref={"id": "image", "revision": policy["revision"]},
    )
    configurations.revision = "cfg-2"
    queue.job("job")

    run = service.get_run("project", queued["id"])
    assert run["status"] == "completed" and run["actualRoute"] == {"provider": "backup", "model": "backup-model"}
    assert [attempt["fallbackDecision"] for attempt in run["routeAttempts"]] == ["fallback", "completed"]


@pytest.mark.parametrize("modality", ["text", "image"])
def test_queued_local_cancel_is_non_billable_and_publicly_recorded(tmp_path, modality):
    service = GenerationService(tmp_path)
    service.store.save("project", [{
        "id": modality, "modality": modality, "status": "queued", "outcome": None, "provider": "p", "model": "m",
        "capabilitySnapshot": {"cancel": False}, "routeAttempts": [], "logs": [], "billingUnknown": False,
        "outputItems": [{"resultId": "r", "status": "pending", **({"chunks": [], "text": ""} if modality == "text" else {})}],
    }])
    service.recovered_projects.add("project")

    run = service.cancel("project", modality)
    public = service.public_run(run)

    assert run["billingUnknown"] is False and run["localCancelResult"] == "cancelled"
    assert public["executionSemantics"]["cancelSupport"] == "supported"
    assert public["executionSemantics"]["cancelResult"] == "cancelled"


def test_retry_from_second_frozen_candidate_advances_to_third_without_overwriting_audit(tmp_path):
    registry = GenerationAdapterRegistry()
    for adapter_id in ("two", "three"):
        registry.register(GenerationAdapter(adapter_id, "v1", "text", lambda _run: "ok", lambda *_: None))
    service = GenerationService(tmp_path, registry=registry)
    service._execute = lambda *_: None  # type: ignore[method-assign]
    caps = _caps()
    run = {"id": "r", "modality": "text", "status": "running", "provider": "p2", "model": "m2", "actualRoute": {"provider": "p2", "model": "m2"},
      "routingPolicySnapshot": {"id": "p"}, "routingCandidateSnapshots": [
        {"provider": "p1", "model": "m1", "adapterId": "two", "adapterVersion": "v1", "configurationVersion": "c1", "capabilitySnapshot": caps},
        {"provider": "p2", "model": "m2", "adapterId": "two", "adapterVersion": "v1", "configurationVersion": "c2", "capabilitySnapshot": caps},
        {"provider": "p3", "model": "m3", "adapterId": "three", "adapterVersion": "v1", "configurationVersion": "c3", "capabilitySnapshot": caps}],
      "routeAttempts": [{"provider": "p2", "model": "m2", "adapterId": "two", "adapterVersion": "v1", "configurationVersion": "c2", "capabilityVersion": caps["version"], "status": "selected", "failureCode": None, "fallbackDecision": "selected"}], "outputItems": [{"chunks": [], "status": "pending"}], "logs": []}
    service.store.save("p", [run]); service.recovered_projects.add("p")
    assert service._start_safe_fallback("p", "r", GenerationError("local", safe_to_fallback=True))
    changed = service.get_run("p", "r")
    assert changed["actualRoute"] == {"provider": "p3", "model": "m3"}


def test_image_terminal_preserves_existing_fallback_audit_decision(tmp_path):
    service = GenerationService(tmp_path)
    run = {"id": "image", "modality": "image", "status": "running", "outputItems": [{"status": "pending"}],
      "routeAttempts": [{"status": "selected", "failureCode": None, "fallbackDecision": "selected"}], "logs": []}
    service.store.save("project", [run]); service.recovered_projects.add("project")
    service._mark_route_failure("project", "image", "generation_safe_failed", "fallback_exhausted")
    service._finish_image_terminal("project", "image", outcome="failed", reason="generation_safe_failed", billing_unknown=False)
    assert service.get_run("project", "image")["routeAttempts"][-1]["fallbackDecision"] == "fallback_exhausted"


def test_execute_preflight_preserves_saved_policy_exhaustion_decision(tmp_path):
    service = GenerationService(tmp_path, registry=GenerationAdapterRegistry())
    caps = _caps()
    run = {"id": "text", "modality": "text", "status": "queued", "provider": "p", "model": "m", "actualRoute": {"provider": "p", "model": "m"},
      "adapterId": "missing", "adapterVersion": "v1", "configurationVersion": "c", "capabilitySnapshot": caps,
      "routingPolicySnapshot": {"id": "saved"}, "routingCandidateSnapshots": [{"provider": "p", "model": "m", "adapterId": "missing", "adapterVersion": "v1", "configurationVersion": "c", "capabilitySnapshot": caps}],
      "routeAttempts": [{"provider": "p", "model": "m", "adapterId": "missing", "adapterVersion": "v1", "configurationVersion": "c", "capabilityVersion": caps["version"], "status": "selected", "failureCode": None, "fallbackDecision": "selected"}],
      "input": {"textParams": {}}, "outputItems": [{"status": "pending", "chunks": [], "text": ""}], "logs": []}
    service.store.save("project", [run]); service.recovered_projects.add("project")
    service._execute("project", "text")
    assert service.get_run("project", "text")["routeAttempts"][-1]["fallbackDecision"] == "fallback_exhausted"


def test_video_recovery_without_remote_identity_closes_route_audit(tmp_path):
    service = GenerationService(tmp_path)
    run = {"id": "video", "modality": "video", "status": "submitting", "outputItems": [{"status": "pending"}],
      "routeAttempts": [{"status": "selected", "failureCode": None, "fallbackDecision": "selected"}], "logs": []}
    service.store.save("project", [run])
    recovered = service.get_run("project", "video")
    assert recovered["status"] == "unknown"
    assert recovered["routeAttempts"][-1]["status"] == "failed"
    assert recovered["routeAttempts"][-1]["failureCode"] == "generation_recovery_unknown"
    assert recovered["routeAttempts"][-1]["fallbackDecision"] == "fallback_blocked_unknown_outcome"


def test_routing_policy_put_rejects_untrusted_origin(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.put("/api/generation-routing-policies/text-safe", headers={
            "origin": "https://evil.invalid", "content-type": "application/json", "x-aivre-intent": "semantic-analysis",
        }, json={"expectedRevision": 0, "policy": {
            "id": "text-safe", "name": "text", "modality": "text", "routes": [{"provider": "p", "model": "m"}],
        }})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "request_origin_rejected"
