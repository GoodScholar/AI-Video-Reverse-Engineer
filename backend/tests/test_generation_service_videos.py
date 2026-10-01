import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.generation_service import (
    GenerationAdapterRegistry,
    GenerationError,
    GenerationService,
    VideoGenerationAdapter,
)
from app.project_assets import ProjectAssets
from app.preproduction_api import create_preproduction_router
from app.timeline import validate_workspace


PROJECT_ID = "video-project"
VIDEO_FIXTURE = Path(__file__).parents[2] / "frontend/src/contentPrototypeMedia/pour-demo.mp4"


def video_capabilities(**changes):
    value = {
        "version": "fake-video-v1",
        "profiles": [
            {
                "id": "t2v-main",
                "mode": "text-to-video",
                "referencePatterns": [[]],
                "combinations": [
                    {"durationSeconds": 5, "aspectRatio": "16:9", "resolution": "1280x720", "audio": False},
                ],
                "minResults": 1,
                "maxResults": 2,
            },
            {
                "id": "i2v-main",
                "mode": "image-to-video",
                "referencePatterns": [
                    ["first-frame"],
                    ["first-frame", "last-frame"],
                    ["reference", "reference"],
                ],
                "combinations": [
                    {"durationSeconds": 6, "aspectRatio": "9:16", "resolution": "720x1280", "audio": True},
                ],
                "minResults": 1,
                "maxResults": 1,
            },
        ],
        "cancel": True,
    }
    value.update(changes)
    return value


class FakeVideoProvider:
    def __init__(self, tmp_path, *, capabilities=None, cancel_result=None):
        self.tmp_path = tmp_path
        self.capabilities = capabilities or video_capabilities()
        self.cancel_result = cancel_result or {"status": "cancelled", "billingUnknown": False}
        self.submissions = []
        self.polls = []
        self.downloads = []
        self.download_committed_bytes = 0
        self.poll_responses = []
        self.download_sources = {}

    def catalog(self):
        return [{"provider": "fake-video", "model": "cinema-1", "label": "Fake Cinema 1"}]

    def capability_catalog(self, provider, model):
        if (provider, model) != ("fake-video", "cinema-1"):
            return None
        return {
            "provider": provider,
            "model": model,
            "configurationVersion": "fake-video-config-v1",
            "capabilities": self.capabilities,
        }

    def submit(self, run):
        self.submissions.append(run)
        return {
            "remoteTaskId": "remote-task-1",
            "status": "queued",
            "pollCursor": "poll-1",
            "callbackCursor": "callback-1",
        }

    def poll(self, run):
        self.polls.append(run)
        return self.poll_responses.pop(0)

    def download(self, run, result, output):
        self.downloads.append((run["id"], result["remoteResultId"]))
        source = self.download_sources[result["remoteResultId"]]
        if isinstance(source, Exception):
            raise source
        with Path(source).open("rb") as input_file:
            while chunk := input_file.read(64 * 1024):
                self.download_committed_bytes += output.write(chunk)

    def cancel(self, run):
        return dict(self.cancel_result)

    def adapter(self):
        return VideoGenerationAdapter(
            "fake-video-adapter",
            "async-video-v1",
            "video",
            self.capability_catalog,
            self.catalog,
            self.submit,
            self.poll,
            self.download,
            self.cancel,
        )


class HeldQueue:
    def submit(self, _kind, _key, _job, **_kwargs):
        return True


def service_for(tmp_path, fake, **kwargs):
    registry = GenerationAdapterRegistry()
    registry.register(fake.adapter())
    return GenerationService(tmp_path, registry=registry, **kwargs)


def install_image(tmp_path, name="frame.png"):
    source = tmp_path / name
    Image.new("RGB", (8, 8), "blue").save(source)
    return ProjectAssets(tmp_path).install_run_result(
        PROJECT_ID,
        source=source,
        run_id="frame-source-run",
        result_id=name.replace(".", "-"),
        kind="image",
        name=name,
        input_snapshot={"source": name},
        common_run_id="frame-common-run",
        domain_kind="test",
        domain_run_id="frame-source-run",
        media={"width": 8, "height": 8},
    )


def submit(service, *, idempotency_key="video-request-1", **video_params):
    return service.submit_video(
        PROJECT_ID,
        disclosure={
            "provider": "fake-video",
            "model": "cinema-1",
            "prompt": "商品从暗处旋转进入棚拍光线",
            "accepted": True,
        },
        video_params={
            "profileId": "t2v-main",
            "mode": "text-to-video",
            "references": [],
            "durationSeconds": 5,
            "aspectRatio": "16:9",
            "resolution": "1280x720",
            "audio": False,
            "resultCount": 1,
            **video_params,
        },
        idempotency_key=idempotency_key,
        caller="asset-library",
    )


def success(remote_result_id="remote-result-1", *, name="生成视频.mp4", cursor="poll-2"):
    return {
        "status": "succeeded",
        "pollCursor": cursor,
        "callbackCursor": "callback-2",
        "billingUnknown": False,
        "results": [{"index": 0, "remoteResultId": remote_result_id, "name": name}],
        "failedItems": [],
    }


def test_video_catalog_exposes_only_verified_exact_combinations_and_rejects_unknowns(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    service = service_for(tmp_path, fake)

    assert service.list_video_models() == [{
        "provider": "fake-video",
        "model": "cinema-1",
        "label": "Fake Cinema 1",
        "adapterId": "fake-video-adapter",
        "adapterVersion": "async-video-v1",
        "configurationVersion": "fake-video-config-v1",
        "capabilities": video_capabilities(),
    }]
    assert service.capabilities("fake-video", "cinema-1", modality="video")["capabilities"] == video_capabilities()

    frame = install_image(tmp_path)
    valid_references = [{
        "assetId": frame["id"], "assetVersionId": frame["assetVersionId"], "referenceType": "first-frame",
    }]
    valid = submit(
        service,
        idempotency_key="valid-i2v",
        profileId="i2v-main",
        mode="image-to-video",
        references=valid_references,
        durationSeconds=6,
        aspectRatio="9:16",
        resolution="720x1280",
        audio=True,
    )
    assert valid["remoteTaskId"] == "remote-task-1"

    invalid_inputs = [
        {"profileId": "unknown"},
        {"durationSeconds": 10},
        {"aspectRatio": "1:1"},
        {"resolution": "1920x1080"},
        {"audio": True},
        {"mode": "image-to-video"},
        {"references": valid_references},
    ]
    for index, changes in enumerate(invalid_inputs):
        with pytest.raises(GenerationError, match="generation_(profile|combination|reference_pattern)_unsupported"):
            submit(service, idempotency_key=f"invalid-{index}", **changes)
    assert len(fake.submissions) == 1


@pytest.mark.parametrize("capabilities", [
    video_capabilities(profiles=[{
        "id": "i2v-too-many-references", "mode": "image-to-video",
        "referencePatterns": [["reference"] * 33],
        "combinations": [{"durationSeconds": 5, "aspectRatio": "16:9", "resolution": "1280x720", "audio": False}],
        "minResults": 1, "maxResults": 1,
    }]),
    video_capabilities(profiles=[{
        "id": "too-many-results", "mode": "text-to-video", "referencePatterns": [[]],
        "combinations": [{"durationSeconds": 5, "aspectRatio": "16:9", "resolution": "1280x720", "audio": False}],
        "minResults": 1, "maxResults": 65,
    }]),
    video_capabilities(profiles=[{
        "id": "oversized-combination", "mode": "text-to-video", "referencePatterns": [[]],
        "combinations": [{"durationSeconds": 5, "aspectRatio": "x" * 33, "resolution": "1280x720", "audio": False}],
        "minResults": 1, "maxResults": 1,
    }]),
])
def test_video_catalog_hides_capabilities_that_submit_api_cannot_accept(tmp_path, capabilities):
    fake = FakeVideoProvider(tmp_path, capabilities=capabilities)
    service = service_for(tmp_path, fake)

    assert service.list_video_models() == []
    with pytest.raises(GenerationError, match="generation_capability_unavailable"):
        service.capabilities("fake-video", "cinema-1", modality="video")


def test_video_restart_refreshes_remote_task_without_resubmitting(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    first = service_for(tmp_path, fake)
    run = submit(first)
    fake.poll_responses.append({
        "status": "running",
        "pollCursor": "poll-2",
        "callbackCursor": "callback-2",
        "billingUnknown": True,
    })

    restarted = service_for(tmp_path, fake)
    recovered = restarted.get_run(PROJECT_ID, run["id"])
    refreshed = restarted.refresh_video(PROJECT_ID, run["id"])

    assert recovered["status"] == "running"
    assert recovered["remoteTaskId"] == "remote-task-1"
    assert refreshed["lastConfirmedRemoteStatus"] == "running"
    assert refreshed["pollCursor"] == "poll-2"
    assert refreshed["callbackCursor"] == "callback-2"
    assert refreshed["billingUnknown"] is True
    assert len(fake.submissions) == 1
    assert len(fake.polls) == 1


def test_video_restart_resumes_confirmed_success_download_without_poll_or_resubmit(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    first = service_for(tmp_path, fake)
    run = submit(first)
    first._record_video_success(PROJECT_ID, run["id"], success())
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE

    restarted = service_for(tmp_path, fake)
    completed = restarted.refresh_video(PROJECT_ID, run["id"])

    assert completed["status"] == "completed"
    assert completed["outputItems"][0]["status"] == "completed"
    assert fake.polls == []
    assert len(fake.submissions) == 1
    assert fake.downloads == [(run["id"], "remote-result-1")]


def test_video_concurrent_refreshes_single_flight_the_remote_poll(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    entered = threading.Event()
    release = threading.Event()

    def blocking_poll(run):
        fake.polls.append(run)
        entered.set()
        assert release.wait(2)
        return success()

    fake.poll = blocking_poll
    service = service_for(tmp_path, fake)
    run = submit(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.refresh_video, PROJECT_ID, run["id"])
        assert entered.wait(1)
        second = pool.submit(service.refresh_video, PROJECT_ID, run["id"])
        time.sleep(0.05)
        release.set()
        assert first.result()["status"] == "completed"
        assert second.result()["status"] == "completed"

    assert len(fake.polls) == 1
    assert len(fake.downloads) == 1


def test_video_refresh_rejects_adapter_version_drift_without_polling(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    first = service_for(tmp_path, fake)
    run = submit(first)

    changed = FakeVideoProvider(tmp_path)
    changed_adapter = changed.adapter()
    registry = GenerationAdapterRegistry()
    registry.register(VideoGenerationAdapter(
        changed_adapter.adapter_id,
        "async-video-v2",
        changed_adapter.modality,
        changed_adapter.capability_resolver,
        changed_adapter.catalog,
        changed_adapter.submit,
        changed_adapter.poll,
        changed_adapter.download,
        changed_adapter.cancel,
    ))
    restarted = GenerationService(tmp_path, registry=registry)

    with pytest.raises(GenerationError, match="generation_adapter_version_changed"):
        restarted.refresh_video(PROJECT_ID, run["id"])
    assert changed.polls == []


def test_video_malformed_poll_response_becomes_explicit_remote_unknown(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    fake.poll_responses.append({
        "status": "succeeded",
        "pollCursor": "poll-2",
        "callbackCursor": "callback-2",
        "billingUnknown": False,
        "results": [{
            "index": 0,
            "remoteResultId": "remote-result-1",
            "name": "result.mp4",
            "privateUrl": "https://provider.invalid/result",
        }],
        "failedItems": [],
    })
    service = service_for(tmp_path, fake)
    run = submit(service)

    refreshed = service.refresh_video(PROJECT_ID, run["id"])

    assert refreshed["status"] == "running"
    assert refreshed["reason"] == "generation_protocol_invalid"
    assert refreshed["billingUnknown"] is True
    assert refreshed["outputItems"][0]["status"] == "pending"
    assert "privateUrl" not in (tmp_path / "project-files" / PROJECT_ID / "generation-runs.json").read_text()


def test_video_submit_response_loss_recovers_unknown_and_same_key_never_resubmits(tmp_path):
    fake = FakeVideoProvider(tmp_path)

    def accepted_then_connection_lost(run):
        fake.submissions.append(run)
        raise SystemExit("process stopped after the provider accepted the request")

    fake.submit = accepted_then_connection_lost
    service = service_for(tmp_path, fake)
    with pytest.raises(SystemExit):
        submit(service)

    restarted = service_for(tmp_path, fake)
    recovered = restarted.list_runs(PROJECT_ID)[0]
    repeated = submit(restarted)

    assert recovered["status"] == "unknown"
    assert recovered["outcome"] == "unknown"
    assert recovered["reason"] == "generation_submit_outcome_unknown"
    assert recovered["billingUnknown"] is True
    assert repeated["id"] == recovered["id"]
    assert len(fake.submissions) == 1


def test_video_submit_transport_error_is_unknown_and_never_auto_retried(tmp_path):
    fake = FakeVideoProvider(tmp_path)

    def uncertain_submit(run):
        fake.submissions.append(run)
        raise ConnectionError("private provider detail must not escape")

    fake.submit = uncertain_submit
    service = service_for(tmp_path, fake)
    run = submit(service)
    repeated = submit(service)

    assert run["status"] == "unknown"
    assert run["outcome"] == "unknown"
    assert run["reason"] == "generation_submit_unknown"
    assert run["billingUnknown"] is True
    assert run["outputItems"][0]["status"] == "unknown"
    assert repeated["id"] == run["id"]
    assert len(fake.submissions) == 1


@pytest.mark.parametrize("remote_identity,marker", [
    ("https://provider.invalid/private-task", "provider.invalid"),
    (" https://provider.invalid/private-task", "provider.invalid"),
    ("../private/task", "private/task"),
    (r"\\server\private-task", "private-task"),
])
def test_video_submit_rejects_private_remote_identity_without_persisting_it(tmp_path, remote_identity, marker):
    fake = FakeVideoProvider(tmp_path)

    def private_identity(run):
        fake.submissions.append(run)
        return {
            "remoteTaskId": remote_identity, "status": "queued",
            "pollCursor": "poll-1", "callbackCursor": "callback-1",
        }

    fake.submit = private_identity
    service = service_for(tmp_path, fake)
    run = submit(service)

    assert run["status"] == "unknown"
    persisted = (tmp_path / "project-files" / PROJECT_ID / "generation-runs.json").read_text()
    assert marker not in persisted


@pytest.mark.parametrize("event,marker", [
    ({
        "status": "running", "pollCursor": "https://provider.invalid/private-cursor",
        "callbackCursor": "callback-2", "billingUnknown": False,
    }, "provider.invalid"),
    ({
        "status": "running", "pollCursor": "/Users/provider/private-cursor",
        "callbackCursor": "callback-2", "billingUnknown": False,
    }, "/Users/provider"),
    ({
        "status": "failed", "pollCursor": "poll-2", "callbackCursor": "callback-2",
        "billingUnknown": False, "reason": "https://provider.invalid/private-error",
    }, "provider.invalid"),
    ({
        "status": "succeeded", "pollCursor": "poll-2", "callbackCursor": "callback-2",
        "billingUnknown": False,
        "results": [{"index": 0, "remoteResultId": "https://provider.invalid/private-result", "name": "result.mp4"}],
        "failedItems": [],
    }, "provider.invalid"),
    ({
        "status": "succeeded", "pollCursor": "poll-2", "callbackCursor": "callback-2",
        "billingUnknown": False,
        "results": [{"index": 0, "remoteResultId": "../private/result", "name": "result.mp4"}],
        "failedItems": [],
    }, "private/result"),
    ({
        "status": "succeeded", "pollCursor": "poll-2", "callbackCursor": "callback-2",
        "billingUnknown": False,
        "results": [{"index": 0, "remoteResultId": r"\\server\private-result", "name": "result.mp4"}],
        "failedItems": [],
    }, "private-result"),
    ({
        "status": "succeeded", "pollCursor": "poll-2", "callbackCursor": "callback-2",
        "billingUnknown": False,
        "results": [{"index": 0, "remoteResultId": "remote-result", "name": "../private-result.mp4"}],
        "failedItems": [],
    }, "private-result.mp4"),
])
def test_video_poll_rejects_private_protocol_fields_without_persisting_them(tmp_path, event, marker):
    fake = FakeVideoProvider(tmp_path)
    fake.poll_responses.append(event)
    service = service_for(tmp_path, fake)
    run = submit(service)

    refreshed = service.refresh_video(PROJECT_ID, run["id"])

    assert refreshed["status"] == "running"
    assert refreshed["reason"] == "generation_protocol_invalid"
    assert refreshed["billingUnknown"] is True
    persisted = (tmp_path / "project-files" / PROJECT_ID / "generation-runs.json").read_text()
    assert marker not in persisted


def test_video_local_stop_cancel_unknown_and_late_success_remain_distinct(tmp_path):
    fake = FakeVideoProvider(tmp_path, cancel_result={"status": "unknown", "billingUnknown": True, "pollCursor": "cancel-poll"})
    service = service_for(tmp_path, fake)
    run = submit(service)

    stopped = service.stop_video_local(PROJECT_ID, run["id"])
    assert stopped["status"] == "running"
    assert stopped["localDisposition"] == "stopped"
    assert stopped["remoteCancelResult"] is None

    unknown = service.cancel_video_remote(PROJECT_ID, run["id"])
    assert unknown["status"] == "running"
    assert unknown["remoteCancelResult"] == "unknown"
    assert unknown["lastConfirmedRemoteStatus"] == "unknown"
    assert unknown["billingUnknown"] is True

    fake.download_sources["late-result"] = VIDEO_FIXTURE
    fake.poll_responses.append(success("late-result", name="迟到结果.mp4"))
    late = service.refresh_video(PROJECT_ID, run["id"])
    assert late["status"] == "completed"
    assert late["outcome"] == "late_success"
    assert late["localDisposition"] == "stopped"
    assert late["remoteCancelResult"] == "unknown"
    assert late["outputItems"][0]["status"] == "completed"


def test_video_cancel_unsupported_and_remote_cancelled_are_not_local_stop(tmp_path):
    unsupported_capabilities = video_capabilities(cancel=False)
    unsupported_fake = FakeVideoProvider(tmp_path / "unsupported", capabilities=unsupported_capabilities)
    unsupported = service_for(tmp_path / "unsupported", unsupported_fake)
    unsupported_run = submit(unsupported)
    result = unsupported.cancel_video_remote(PROJECT_ID, unsupported_run["id"])
    assert result["status"] == "running"
    assert result["remoteCancelResult"] == "unsupported"
    assert result["localDisposition"] == "active"

    cancelled_fake = FakeVideoProvider(tmp_path / "cancelled")
    cancelled = service_for(tmp_path / "cancelled", cancelled_fake)
    cancelled_run = submit(cancelled)
    result = cancelled.cancel_video_remote(PROJECT_ID, cancelled_run["id"])
    assert result["status"] == "cancelled"
    assert result["outcome"] == "cancelled"
    assert result["remoteCancelResult"] == "cancelled"
    assert result["localDisposition"] == "active"
    assert result["routeAttempts"][-1]["status"] == "cancelled"
    assert result["routeAttempts"][-1]["failureCode"] == "generation_remote_cancelled"
    assert result["routeAttempts"][-1]["fallbackDecision"] == "fallback_not_requested_cancelled"


def test_video_success_downloads_probes_installs_and_is_timeline_referenceable(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    fake.poll_responses.append(success())
    service = service_for(tmp_path, fake)
    run = submit(service)

    completed = service.refresh_video(PROJECT_ID, run["id"])

    item = completed["outputItems"][0]
    assert completed["status"] == "completed"
    assert item["status"] == "completed"
    assert item["url"] == f"/api/projects/{PROJECT_ID}/preproduction/assets/{item['assetId']}/file"
    asset = ProjectAssets(tmp_path).get(PROJECT_ID, item["assetId"])
    assert asset["assetVersionId"] == item["assetVersionId"]
    assert asset["kind"] == "video"
    assert asset["duration"] == pytest.approx(6.0)
    assert (asset["width"], asset["height"]) == (720, 1280)
    settings, tracks = validate_workspace({
        "revision": 0,
        "settings": {"width": 720, "height": 1280, "fps": 30},
        "tracks": [{
            "id": "video-track", "name": "生成视频", "kind": "video", "muted": False, "hidden": False,
            "clips": [{
                "id": "generated-clip", "assetId": asset["id"], "start": 0, "inPoint": 0,
                "duration": 5, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0,
            }],
        }],
    }, {asset["id"]: asset})
    assert settings["height"] == 1280
    assert tracks[0]["clips"][0]["assetId"] == asset["id"]


def test_video_download_writer_does_not_expose_raw_file_or_descriptor(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    original_download = fake.download

    def inspect_writer(run, result, output):
        assert not hasattr(output, "fileno")
        assert not hasattr(output, "output")
        original_download(run, result, output)

    fake.download = inspect_writer
    fake.poll_responses.append(success())
    service = service_for(tmp_path, fake)
    run = submit(service)

    assert service.refresh_video(PROJECT_ID, run["id"])["status"] == "completed"


def test_video_multiple_results_keep_item_failure_and_partial_success(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    fake.poll_responses.append({
        "status": "succeeded",
        "pollCursor": "poll-2",
        "callbackCursor": "callback-2",
        "billingUnknown": False,
        "results": [{"index": 0, "remoteResultId": "remote-result-1", "name": "成功.mp4"}],
        "failedItems": [{"index": 1, "code": "provider_item_rejected", "billingUnknown": True}],
    })
    service = service_for(tmp_path, fake)
    run = submit(service, resultCount=2)

    partial = service.refresh_video(PROJECT_ID, run["id"])

    assert partial["status"] == "partial_success"
    assert partial["outcome"] == "partial_success"
    assert [(item["index"], item["status"]) for item in partial["outputItems"]] == [(0, "completed"), (1, "failed")]
    assert partial["outputItems"][1]["reason"] == "provider_item_rejected"
    assert partial["billingUnknown"] is True


def test_video_corruption_and_download_retry_preserve_history_without_new_remote_submission(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")
    fake.download_sources["remote-result-1"] = corrupt
    fake.poll_responses.append(success())
    service = service_for(tmp_path, fake)
    first = submit(service)
    failed = service.refresh_video(PROJECT_ID, first["id"])

    assert failed["status"] == "failed"
    assert failed["outputItems"][0]["reason"] == "generation_result_invalid"
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    retried = service.retry_video(PROJECT_ID, first["id"], idempotency_key="local-download-retry")

    assert retried["status"] == "completed"
    assert retried["retryOf"] == first["id"]
    assert retried["remoteTaskId"] == first["remoteTaskId"]
    assert len(fake.submissions) == 1
    assert len(fake.downloads) == 2
    runs = service.list_runs(PROJECT_ID)
    assert next(run for run in runs if run["id"] == first["id"])["status"] == "failed"


def test_video_download_rejects_result_over_configured_size_limit_before_probe(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    fake.poll_responses.append(success())
    service = service_for(tmp_path, fake, max_video_result_bytes=128)
    run = submit(service)

    failed = service.refresh_video(PROJECT_ID, run["id"])

    assert failed["status"] == "failed"
    assert failed["outputItems"][0]["reason"] == "generation_result_too_large"
    assert fake.download_committed_bytes == 0
    assert ProjectAssets(tmp_path).records(PROJECT_ID) == []


def test_video_retry_rejects_adapter_version_drift_before_persisting_new_run(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")
    fake.download_sources["remote-result-1"] = corrupt
    fake.poll_responses.append(success())
    first = service_for(tmp_path, fake)
    run = submit(first)
    failed = first.refresh_video(PROJECT_ID, run["id"])

    changed = FakeVideoProvider(tmp_path)
    changed_adapter = changed.adapter()
    registry = GenerationAdapterRegistry()
    registry.register(VideoGenerationAdapter(
        changed_adapter.adapter_id, "async-video-v2", changed_adapter.modality,
        changed_adapter.capability_resolver, changed_adapter.catalog, changed_adapter.submit,
        changed_adapter.poll, changed_adapter.download, changed_adapter.cancel,
    ))
    restarted = GenerationService(tmp_path, registry=registry)

    with pytest.raises(GenerationError, match="generation_adapter_version_changed"):
        restarted.retry_video(PROJECT_ID, failed["id"], idempotency_key="must-not-persist")
    assert [item["id"] for item in restarted.list_runs(PROJECT_ID)] == [failed["id"]]


def test_video_completed_retry_replays_same_key_after_adapter_version_drift(tmp_path):
    fake = FakeVideoProvider(tmp_path)
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")
    fake.download_sources["remote-result-1"] = corrupt
    fake.poll_responses.append(success())
    first = service_for(tmp_path, fake)
    original = submit(first)
    failed = first.refresh_video(PROJECT_ID, original["id"])
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    completed_retry = first.retry_video(PROJECT_ID, failed["id"], idempotency_key="completed-retry")

    changed = FakeVideoProvider(tmp_path)
    changed_adapter = changed.adapter()
    registry = GenerationAdapterRegistry()
    registry.register(VideoGenerationAdapter(
        changed_adapter.adapter_id, "async-video-v2", changed_adapter.modality,
        changed_adapter.capability_resolver, changed_adapter.catalog, changed_adapter.submit,
        changed_adapter.poll, changed_adapter.download, changed_adapter.cancel,
    ))
    restarted = GenerationService(tmp_path, registry=registry)
    replayed = restarted.retry_video(PROJECT_ID, failed["id"], idempotency_key="completed-retry")

    assert replayed["id"] == completed_retry["id"]
    assert len(restarted.list_runs(PROJECT_ID)) == 2
    assert changed.downloads == []


def test_video_api_hides_unknown_models_and_never_persists_private_result_location(tmp_path):
    from app.video_generation_run_api import create_video_generation_run_router

    fake = FakeVideoProvider(tmp_path)
    fake.download_sources["remote-result-1"] = VIDEO_FIXTURE
    fake.poll_responses.append(success())
    service = service_for(tmp_path, fake)
    app = FastAPI()
    app.include_router(create_video_generation_run_router(service, lambda project_id: {"id": project_id}))
    app.include_router(create_preproduction_router(tmp_path, lambda project_id: {"id": project_id}, HeldQueue()))
    client = TestClient(app)

    models = client.get(f"/api/projects/{PROJECT_ID}/video-generation/models")
    assert models.status_code == 200
    assert models.json()["models"][0]["provider"] == "fake-video"
    missing = client.get(f"/api/projects/{PROJECT_ID}/video-generation/capabilities?provider=fake-video&model=unknown")
    assert missing.status_code == 422
    response = client.post(f"/api/projects/{PROJECT_ID}/video-generation/runs", json={
        "disclosure": {"provider": "fake-video", "model": "cinema-1", "prompt": "商品旋转", "accepted": True},
        "videoParams": {
            "profileId": "t2v-main", "mode": "text-to-video", "references": [], "durationSeconds": 5,
            "aspectRatio": "16:9", "resolution": "1280x720", "audio": False, "resultCount": 1,
        },
        "idempotencyKey": "api-video-request", "caller": "asset-library",
    }, headers={"x-aivre-intent": "semantic-analysis"})
    assert response.status_code == 202
    run_id = response.json()["run"]["id"]
    refreshed = client.post(f"/api/projects/{PROJECT_ID}/video-generation/runs/{run_id}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["run"]["status"] == "completed"
    preview = client.get(refreshed.json()["run"]["outputItems"][0]["url"])
    assert preview.status_code == 200
    assert preview.content == VIDEO_FIXTURE.read_bytes()
    assert preview.headers["content-type"] == "video/mp4"
    public_run = refreshed.json()["run"]
    assert set(public_run).isdisjoint({"remoteTaskId", "pollCursor", "callbackCursor", "logs", "input", "disclosure"})
    assert "remoteResultId" not in public_run["outputItems"][0]
    assert public_run["outputItems"][0]["retryable"] is False
    persisted = (tmp_path / "project-files" / PROJECT_ID / "generation-runs.json").read_text()
    assert "private" not in persisted
    assert str(VIDEO_FIXTURE) not in persisted


def test_video_submit_does_not_block_other_api_requests(tmp_path):
    from app.video_generation_run_api import create_video_generation_run_router

    fake = FakeVideoProvider(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_submit(run):
        fake.submissions.append(run)
        entered.set()
        assert release.wait(2)
        return {
            "remoteTaskId": "remote-task-1", "status": "queued",
            "pollCursor": "poll-1", "callbackCursor": "callback-1",
        }

    fake.submit = blocking_submit
    service = service_for(tmp_path, fake)
    app = FastAPI()
    app.include_router(create_video_generation_run_router(service, lambda project_id: {"id": project_id}))
    payload = {
        "disclosure": {"provider": "fake-video", "model": "cinema-1", "prompt": "并发提交", "accepted": True},
        "videoParams": {
            "profileId": "t2v-main", "mode": "text-to-video", "references": [], "durationSeconds": 5,
            "aspectRatio": "16:9", "resolution": "1280x720", "audio": False, "resultCount": 1,
        },
        "idempotencyKey": "blocking-submit", "caller": "asset-library",
    }
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        submitted = pool.submit(client.post, f"/api/projects/{PROJECT_ID}/video-generation/runs", json=payload)
        assert entered.wait(1)
        catalog = pool.submit(client.get, f"/api/projects/{PROJECT_ID}/video-generation/models")
        try:
            assert catalog.result(timeout=0.5).status_code == 200
        finally:
            release.set()
        assert submitted.result(timeout=2).status_code == 202


def test_main_registers_video_slice_but_keeps_production_catalog_empty_without_real_adapter(tmp_path):
    from app.main import create_app

    with TestClient(create_app(tmp_path)) as client:
        project_id = client.post("/api/projects", json={"name": "视频生成边界"}).json()["id"]
        base = f"/api/projects/{project_id}/video-generation"

        assert client.get(base + "/models").json() == {"models": []}
        response = client.post(base + "/runs", json={
            "disclosure": {"provider": "not-registered", "model": "unknown", "prompt": "不得猜测能力", "accepted": True},
            "videoParams": {
                "profileId": "guessed", "mode": "text-to-video", "references": [], "durationSeconds": 5,
                "aspectRatio": "16:9", "resolution": "1280x720", "audio": False, "resultCount": 1,
            },
            "idempotencyKey": "unknown-model", "caller": "asset-library",
        }, headers={"x-aivre-intent": "semantic-analysis"})

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "generation_adapter_missing"
