import io
from threading import Event, Thread
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.generation_service import (
    GenerationAdapter,
    GenerationAdapterRegistry,
    GenerationError,
    GenerationService,
)
from app.project_assets import ProjectAssets
from app.preproduction import PreproductionStore
from app.preproduction_api import create_preproduction_router


PROJECT_ID = "image-project"


def png_bytes(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), color).save(output, format="PNG")
    return output.getvalue()


def image_capabilities(**changes):
    capabilities = {
        "version": "fake-image-v1",
        "referenceTypes": ["reference", "edit-source", "upscale-source"],
        "minReferences": 0,
        "maxReferences": 3,
        "imageEditing": True,
        "superResolution": True,
        "aspectRatios": ["1:1", "16:9"],
        "resolutions": ["1024x1024", "1536x864"],
        "minResults": 1,
        "maxResults": 3,
        "cancel": True,
        "timeout": True,
    }
    capabilities.update(changes)
    return capabilities


def image_service(tmp_path, execute, *, capabilities=None, cancel=None, queue=None):
    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter(
        "fake-image", "protocol-v1", "image", execute,
        lambda provider, model: {
            "provider": provider,
            "model": model,
            "configurationVersion": "fake-config-v1",
            "capabilities": capabilities or image_capabilities(),
        },
        cancel=cancel,
    ))
    return GenerationService(tmp_path, registry=registry, queue=queue)


def submit(service, **image_params):
    idempotency_key = image_params.pop("idempotencyKey", "image-request-1")
    return service.submit_image(
        PROJECT_ID,
        disclosure={
            "provider": "fake-provider",
            "model": "fake-model",
            "prompt": "为商品制作干净主图",
            "accepted": True,
        },
        image_params={
            "operation": "generate",
            "references": [],
            "aspectRatio": "1:1",
            "resolution": "1024x1024",
            "resultCount": 1,
            **image_params,
        },
        idempotency_key=idempotency_key,
        caller="asset-library",
    )


def install_reference(tmp_path, *, name="reference.png"):
    source = tmp_path / name
    source.write_bytes(png_bytes("blue"))
    return ProjectAssets(tmp_path).install_run_result(
        PROJECT_ID,
        source=source,
        run_id="source-run",
        result_id=name.replace(".", "-"),
        kind="image",
        name=name,
        input_snapshot={"source": name},
        domain_kind="test",
        domain_run_id="source-run",
        media={"width": 12, "height": 8},
    )


def test_legacy_immutable_image_asset_has_stable_v1_reference(tmp_path):
    reference = install_reference(tmp_path)
    store = PreproductionStore(tmp_path)
    state = store.load(PROJECT_ID)
    state["assets"][0].pop("assetVersionId")
    store.save(PROJECT_ID, state)
    captured = []

    def execute(run):
        captured.append(run)
        yield {"type": "result", "index": 0, "content": png_bytes("green"), "name": "续创.png"}
        yield {"type": "done"}

    service = image_service(tmp_path, execute)
    run = submit(service, references=[{
        "assetId": reference["id"],
        "assetVersionId": reference["id"] + "-v1",
        "referenceType": "reference",
    }])

    assert run["status"] == "completed"
    assert captured[0]["input"]["imageParams"]["references"][0]["assetVersionId"] == reference["id"] + "-v1"


def test_image_capability_catalog_is_versioned_and_rejects_unknown_fields(tmp_path):
    service = image_service(tmp_path, lambda _run: [])

    catalog = service.capabilities("fake-provider", "fake-model", modality="image")

    assert catalog == {
        "adapterId": "fake-image",
        "adapterVersion": "protocol-v1",
        "provider": "fake-provider",
        "model": "fake-model",
        "configurationVersion": "fake-config-v1",
        "capabilities": image_capabilities(),
    }
    invalid = image_service(
        tmp_path / "invalid",
        lambda _run: [],
        capabilities={**image_capabilities(), "providerPayload": {"quality": "hd"}},
    )
    with pytest.raises(GenerationError, match="generation_capability_unavailable"):
        invalid.capabilities("fake-provider", "fake-model", modality="image")


def test_text_to_image_only_catalog_allows_zero_reference_types(tmp_path):
    capabilities = image_capabilities(
        referenceTypes=[], minReferences=0, maxReferences=0,
        imageEditing=False, superResolution=False,
    )

    service = image_service(
        tmp_path,
        lambda _run: iter([
            {"type": "result", "index": 0, "content": png_bytes("orange"), "name": "文生图.png"},
            {"type": "done"},
        ]),
        capabilities=capabilities,
    )

    assert service.capabilities("fake-provider", "fake-model", modality="image")["capabilities"] == capabilities
    assert submit(service)["status"] == "completed"


@pytest.mark.parametrize(
    ("capability_changes", "params", "code"),
    [
        ({"maxReferences": 1}, {"references": "two"}, "generation_reference_limit"),
        ({"referenceTypes": ["reference"], "imageEditing": False, "superResolution": False}, {"references": "edit"}, "generation_reference_type_unsupported"),
        ({"imageEditing": False}, {"operation": "edit", "references": "edit"}, "generation_image_edit_unsupported"),
        ({"superResolution": False}, {"operation": "super-resolution", "references": "upscale"}, "generation_super_resolution_unsupported"),
        ({}, {"aspectRatio": "4:3"}, "generation_aspect_ratio_unsupported"),
        ({}, {"resolution": "2048x2048"}, "generation_resolution_unsupported"),
        ({"maxResults": 2}, {"resultCount": 3}, "generation_result_count_unsupported"),
    ],
)
def test_image_submission_enforces_catalog_before_adapter_call(tmp_path, capability_changes, params, code):
    calls = []
    capabilities = image_capabilities(**capability_changes)
    service = image_service(tmp_path, lambda run: calls.append(run) or [], capabilities=capabilities)
    reference = install_reference(tmp_path)
    references = {
        "two": [
            {"assetId": reference["id"], "assetVersionId": reference["assetVersionId"], "referenceType": "reference"},
            {"assetId": reference["id"], "assetVersionId": reference["assetVersionId"], "referenceType": "reference"},
        ],
        "edit": [{"assetId": reference["id"], "assetVersionId": reference["assetVersionId"], "referenceType": "edit-source"}],
        "upscale": [{"assetId": reference["id"], "assetVersionId": reference["assetVersionId"], "referenceType": "upscale-source"}],
    }
    actual = {
        key: references[value] if key == "references" else value
        for key, value in params.items()
    }

    with pytest.raises(GenerationError, match=code):
        submit(service, **actual)

    assert calls == []


def test_multiple_image_results_persist_partial_success_and_install_real_assets(tmp_path):
    def execute(_run):
        yield {"type": "result", "index": 0, "content": png_bytes("red"), "name": "红色方案.png"}
        yield {"type": "error", "index": 1, "code": "provider_rejected", "billingUnknown": False}
        yield {"type": "result", "index": 2, "content": png_bytes("green"), "name": "绿色方案.png"}
        yield {"type": "done"}

    service = image_service(tmp_path, execute)
    run = submit(service, resultCount=3)

    assert run["status"] == "partial_success"
    assert run["outcome"] == "partial_success"
    assert [(item["index"], item["status"]) for item in run["outputItems"]] == [
        (0, "completed"), (1, "failed"), (2, "completed"),
    ]
    installed = [item for item in run["outputItems"] if item["status"] == "completed"]
    assert all(item["assetVersionId"] == item["assetId"] + "-v1" for item in installed)
    assert all(item["url"].endswith(f"/{item['assetId']}/file") for item in installed)
    assets = ProjectAssets(tmp_path)
    assert len(assets.records(PROJECT_ID)) == 2
    assert [Image.open(assets.file(PROJECT_ID, item["assetId"])).size for item in installed] == [(12, 8), (12, 8)]


class HeldQueue:
    def __init__(self):
        self.job = None

    def submit(self, _kind, _key, job, **_kwargs):
        self.job = job
        return True


def test_cancel_after_one_result_preserves_late_asset_as_late_success(tmp_path):
    first_written = Event()
    release = Event()
    cancelled = Event()

    def execute(_run):
        yield {"type": "result", "index": 0, "content": png_bytes("red"), "name": "保留.png"}
        first_written.set()
        release.wait(2)
        yield {"type": "result", "index": 1, "content": png_bytes("green"), "name": "迟到.png"}
        yield {"type": "done"}

    queue = HeldQueue()
    service = image_service(tmp_path, execute, cancel=lambda _run: (cancelled.set(), release.set()), queue=queue)
    queued = submit(service, resultCount=2)
    worker = Thread(target=lambda: queue.job(PROJECT_ID))
    worker.start()
    assert first_written.wait(2)

    cancelled_run = service.cancel(PROJECT_ID, queued["id"])
    worker.join(2)
    late_run = service.get_run(PROJECT_ID, queued["id"])

    assert cancelled.is_set()
    assert cancelled_run["status"] == "partial_success"
    assert cancelled_run["outcome"] == "cancelled"
    assert late_run["status"] == "completed"
    assert late_run["outcome"] == "late_success"
    assert [item["status"] for item in late_run["outputItems"]] == ["completed", "completed"]
    assert len(ProjectAssets(tmp_path).records(PROJECT_ID)) == 2


def test_timeout_after_one_result_preserves_late_asset_as_late_success(tmp_path):
    first_written = Event()
    release = Event()

    def execute(_run):
        yield {"type": "result", "index": 0, "content": png_bytes("red"), "name": "保留.png"}
        first_written.set()
        release.wait(3)
        yield {"type": "result", "index": 1, "content": png_bytes("green"), "name": "迟到.png"}
        yield {"type": "done"}

    queue = HeldQueue()
    service = image_service(tmp_path, execute, queue=queue)
    queued = submit(service, resultCount=2, timeoutSeconds=1)
    worker = Thread(target=lambda: queue.job(PROJECT_ID))
    worker.start()
    assert first_written.wait(2)
    worker.join(2)
    release.set()
    worker.join(2)

    timed_out = service.get_run(PROJECT_ID, queued["id"])
    assert timed_out["status"] == "completed"
    assert timed_out["outcome"] == "late_success"
    # A timeout was already indeterminate; later successful output does not
    # supply retrospective billing proof.
    assert timed_out["billingUnknown"] is True
    assert [item["status"] for item in timed_out["outputItems"]] == ["completed", "completed"]
    assert len(ProjectAssets(tmp_path).records(PROJECT_ID)) == 2


def test_cancelled_run_preserves_two_late_images_only_after_done(tmp_path):
    started, release = Event(), Event()
    def execute(_run):
        started.set(); release.wait(2)
        yield {"type": "result", "index": 0, "content": png_bytes("red"), "name": "一.png"}
        yield {"type": "result", "index": 1, "content": png_bytes("green"), "name": "二.png"}
        yield {"type": "done"}
    queue = HeldQueue()
    service = image_service(tmp_path, execute, cancel=lambda _run: release.set(), queue=queue)
    queued = submit(service, resultCount=2)
    worker = Thread(target=lambda: queue.job(PROJECT_ID)); worker.start(); assert started.wait(2)
    service.cancel(PROJECT_ID, queued["id"]); worker.join(2)
    run = service.get_run(PROJECT_ID, queued["id"])
    assert run["status"] == "completed" and run["outcome"] == "late_success"
    assert [item["status"] for item in run["outputItems"]] == ["completed", "completed"]
    assert run["routeAttempts"][-1]["status"] == "cancelled"
    assert run["routeAttempts"][-1]["failureCode"] == "generation_cancelled"


def test_cancelled_run_late_image_and_error_is_partial_and_late(tmp_path):
    started, release = Event(), Event()
    def execute(_run):
        started.set(); release.wait(2)
        yield {"type": "result", "index": 0, "content": png_bytes("red"), "name": "一.png"}
        yield {"type": "error", "index": 1, "code": "late_failed", "billingUnknown": False}
        yield {"type": "done"}
    queue = HeldQueue()
    service = image_service(tmp_path, execute, cancel=lambda _run: release.set(), queue=queue)
    queued = submit(service, resultCount=2)
    worker = Thread(target=lambda: queue.job(PROJECT_ID)); worker.start(); assert started.wait(2)
    service.cancel(PROJECT_ID, queued["id"]); worker.join(2)
    run = service.get_run(PROJECT_ID, queued["id"])
    assert run["status"] == "partial_success" and run["outputItems"][1]["reason"] == "late_failed"
    assert run["billingUnknown"] is True
    assert run["routeAttempts"][-1]["status"] == "cancelled"
    assert run["routeAttempts"][-1]["failureCode"] == "generation_cancelled"
    assert run["routeAttempts"][-1]["fallbackDecision"] == "fallback_not_requested_cancelled"
    assert service.public_run(run)["executionSemantics"]["late"] is True


def test_retry_creates_new_run_reuses_successful_asset_and_is_idempotent(tmp_path):
    attempts = 0

    def execute(run):
        nonlocal attempts
        attempts += 1
        pending = [item["index"] for item in run["outputItems"] if item["status"] == "pending"]
        for index in pending:
            if attempts == 1 and index == 1:
                yield {"type": "error", "index": index, "code": "provider_failed", "billingUnknown": False}
            else:
                yield {"type": "result", "index": index, "content": png_bytes("red" if index == 0 else "green"), "name": f"方案 {index + 1}.png"}
        yield {"type": "done"}

    service = image_service(tmp_path, execute)
    first = submit(service, resultCount=2)
    repeated = service.retry_image(PROJECT_ID, first["id"], idempotency_key="retry-1")
    same_retry = service.retry_image(PROJECT_ID, first["id"], idempotency_key="retry-1")

    assert repeated["id"] != first["id"]
    assert repeated["retryOf"] == first["id"]
    assert same_retry["id"] == repeated["id"]
    assert repeated["status"] == "completed"
    assert repeated["outputItems"][0]["status"] == "reused"
    assert repeated["outputItems"][0]["assetId"] == first["outputItems"][0]["assetId"]
    assert repeated["outputItems"][1]["status"] == "completed"
    assert len(ProjectAssets(tmp_path).records(PROJECT_ID)) == 2
    assert attempts == 2


def test_retry_rejects_a_fully_successful_run(tmp_path):
    service = image_service(tmp_path, lambda _run: iter([
        {"type": "result", "index": 0, "content": png_bytes("green"), "name": "完成.png"},
        {"type": "done"},
    ]))
    completed = submit(service)

    with pytest.raises(GenerationError, match="generation_retry_invalid"):
        service.retry_image(PROJECT_ID, completed["id"], idempotency_key="unneeded-retry")


def test_image_generation_api_exposes_capabilities_runs_asset_download_and_stable_reuse(tmp_path):
    from app.image_generation_run_api import create_image_generation_run_router

    def execute(run):
        for item in run["outputItems"]:
            if item["status"] == "pending":
                yield {"type": "result", "index": item["index"], "content": png_bytes("purple"), "name": "生成主图.png"}
        yield {"type": "done"}

    service = image_service(tmp_path, execute)
    app = FastAPI()
    app.include_router(create_image_generation_run_router(service, lambda _project_id: object()))
    app.include_router(create_preproduction_router(tmp_path, lambda _project_id: object(), HeldQueue()))
    client = TestClient(app)
    base = f"/api/projects/{PROJECT_ID}/image-generation"

    capabilities = client.get(base + "/capabilities", params={"provider": "fake-provider", "model": "fake-model"})
    assert capabilities.status_code == 200
    assert capabilities.json()["capabilities"]["maxResults"] == 3
    first = client.post(base + "/runs", json={
        "disclosure": {"provider": "fake-provider", "model": "fake-model", "prompt": "商品主图", "accepted": True},
        "imageParams": {"operation": "generate", "references": [], "aspectRatio": "1:1", "resolution": "1024x1024", "resultCount": 1},
        "idempotencyKey": "api-image-1", "caller": "asset-library",
    })
    assert first.status_code == 202, first.text
    run = first.json()["run"]
    output = run["outputItems"][0]
    assert output["status"] == "completed"
    downloaded = client.get(output["url"])
    assert downloaded.status_code == 200
    assert Image.open(io.BytesIO(downloaded.content)).size == (12, 8)
    assert "生成主图.png" in unquote(downloaded.headers["content-disposition"])

    downstream = client.post(base + "/runs", json={
        "disclosure": {"provider": "fake-provider", "model": "fake-model", "prompt": "继续创作", "accepted": True},
        "imageParams": {
            "operation": "generate",
            "references": [{"assetId": output["assetId"], "assetVersionId": output["assetVersionId"], "referenceType": "reference"}],
            "aspectRatio": "1:1", "resolution": "1024x1024", "resultCount": 1,
        },
        "idempotencyKey": "api-image-2", "caller": "asset-library",
    })
    assert downstream.status_code == 202, downstream.text
    assert downstream.json()["run"]["actualRoute"] == {"provider": "fake-provider", "model": "fake-model"}
    assert "input" not in downstream.json()["run"]
    assert client.get(base + f"/runs/{run['id']}").json()["run"]["id"] == run["id"]


def test_image_generation_api_maps_capability_rejection_without_echoing_secrets(tmp_path):
    from app.image_generation_run_api import create_image_generation_run_router

    service = image_service(tmp_path, lambda _run: pytest.fail("不得调用适配器"))
    app = FastAPI()
    app.include_router(create_image_generation_run_router(service, lambda _project_id: object()))
    response = TestClient(app).post(f"/api/projects/{PROJECT_ID}/image-generation/runs", json={
        "disclosure": {"provider": "fake-provider", "model": "fake-model", "prompt": "secret-canary", "accepted": True},
        "imageParams": {"operation": "crop", "references": [], "aspectRatio": "1:1", "resolution": "1024x1024", "resultCount": 1},
        "idempotencyKey": "api-image-invalid", "caller": "asset-library",
    })

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "generation_request_invalid"
    assert "secret-canary" not in response.text


def test_configured_image_adapter_only_resolves_verified_capabilities_and_maps_stable_references(tmp_path):
    from app.image_generation_adapter import ConfiguredImageGenerationAdapter

    reference = install_reference(tmp_path)
    calls = []

    class Configurations:
        def __init__(self, source):
            self.source = source

        def snapshot(self):
            return {
                "revision": "cfg-v1",
                "providers": [{
                    "providerId": "verified-provider", "protocol": "volcengine-ark",
                    "baseUrl": "https://ark.example.test/api/v3", "defaultModelId": "verified-model",
                    "credentialState": "configured",
                }],
                "modelCatalog": [{
                    "providerId": "verified-provider", "modelId": "verified-model", "mediaType": "image",
                    "capabilitySource": self.source, "capabilityVersion": "probe-v1",
                    "capabilities": image_capabilities(maxResults=1),
                }],
            }

    class ProviderService:
        def generate(self, snapshot, input_images=None):
            calls.append((snapshot, input_images))
            return png_bytes("purple")

    unverified = ConfiguredImageGenerationAdapter(tmp_path, Configurations("user-configured"), ProviderService())
    assert unverified.capability_catalog("verified-provider", "verified-model") is None

    configured = ConfiguredImageGenerationAdapter(tmp_path, Configurations("verified"), ProviderService())
    catalog = configured.capability_catalog("verified-provider", "verified-model")
    assert catalog["configurationVersion"] == "cfg-v1:probe-v1"
    run = {
        "_projectId": PROJECT_ID,
        "provider": "verified-provider", "model": "verified-model",
        "configurationVersion": "cfg-v1:probe-v1",
        "input": {"prompt": "继续创作", "imageParams": {
            "operation": "generate", "aspectRatio": "1:1", "resolution": "1024x1024", "resultCount": 1,
            "references": [{"assetId": reference["id"], "assetVersionId": reference["assetVersionId"], "referenceType": "reference"}],
        }},
        "outputItems": [{"index": 0, "status": "pending"}],
    }
    events = list(configured.execute(run))

    assert [event["type"] for event in events] == ["result", "done"]
    assert calls[0][0] == {
        "baseUrl": "https://ark.example.test/api/v3", "provider": "volcengine-ark",
        "providerId": "verified-provider", "model": "verified-model", "revision": "cfg-v1",
        "prompt": "继续创作", "size": "1024x1024",
    }
    assert calls[0][1] == [ProjectAssets(tmp_path).file(PROJECT_ID, reference)]
