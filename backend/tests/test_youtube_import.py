import hashlib
import shutil
import subprocess
from threading import Event, Thread
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.preproduction import PreproductionStore
from app.preproduction_api import create_preproduction_router
from app.youtube_import import YoutubeImportError, YoutubeProbe, _classify_ytdlp_error, _yt_dlp_options, normalize_youtube_url


PROJECT_ID = "project-youtube-source"
BASE = f"/api/projects/{PROJECT_ID}/preproduction"


class Queue:
    def submit(self, _kind, run_id, handler, **_kwargs):
        handler(run_id)
        return True


class ControlledYoutubeAdapter:
    """E2 seam: no request reaches YouTube, and no real cookie is ever used."""

    version = "2026.08.19"
    is_controlled = True

    def __init__(self, media: Path | None = None, error: Exception | None = None):
        self.media, self.error = media, error
        self.probe_calls = 0
        self.download_calls = 0
        self.cookies = []

    def probe(self, url: str, *, cookie: str | None = None) -> YoutubeProbe:
        self.probe_calls += 1
        if self.error:
            raise self.error
        self.cookies.append(cookie)
        return YoutubeProbe(
            canonical_url="https://www.youtube.com/watch?v=YE7VzlLtp-4",
            video_id="YE7VzlLtp-4", title="授权样片", duration=1.0,
            uploader="Golden owner", status="available",
        )

    def download(self, probe: YoutubeProbe, destination: Path, *, cookie: str | None = None) -> Path:
        self.download_calls += 1
        self.cookies.append(cookie)
        assert self.media is not None
        shutil.copyfile(self.media, destination)
        return destination


def make_video(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=6:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def client_for(tmp_path: Path, adapter: ControlledYoutubeAdapter) -> TestClient:
    app = FastAPI()
    project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(
        tmp_path, lambda _: project, Queue(), youtube_adapter=adapter,
    ))
    return TestClient(app)


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="YouTube 受控导入合同需要 ffmpeg 和 ffprobe",
)


def test_youtube_share_links_normalize_to_a_canonical_public_video_url():
    """Removing strict watch/short-link recognition must make this fail."""
    assert normalize_youtube_url("https://youtu.be/YE7VzlLtp-4?t=2") == "https://www.youtube.com/watch?v=YE7VzlLtp-4"
    assert normalize_youtube_url("https://www.youtube.com/watch?v=YE7VzlLtp-4&feature=share") == "https://www.youtube.com/watch?v=YE7VzlLtp-4"
    assert normalize_youtube_url("https://evil.example/?v=YE7VzlLtp-4") is None


def test_probe_discloses_external_request_and_never_claims_a_controlled_test_is_real(tmp_path):
    """Removing disclosure or turning E2 into a real verification claim must fail."""
    adapter = ControlledYoutubeAdapter()
    response = client_for(tmp_path, adapter).post(BASE + "/youtube/probe", json={"url": "https://youtu.be/YE7VzlLtp-4", "externalRequestAccepted": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "available"
    assert body["adapter"] == {"id": "yt-dlp", "version": "2026.08.19"}
    assert body["externalRequest"]["probeHosts"] == ["www.youtube.com", "YouTube player metadata API"]
    assert body["externalRequest"]["downloadHosts"] == ["*.googlevideo.com"]
    assert body["lastRealVerificationAt"] is None
    assert adapter.probe_calls == 1


def test_youtube_import_requires_rights_then_installs_a_hashed_stable_project_asset_once(tmp_path):
    """Removing the rights gate, probe/hash/install chain, or idempotency must fail."""
    source = tmp_path / "authorized.mp4"
    make_video(source)
    adapter = ControlledYoutubeAdapter(source)
    client = client_for(tmp_path, adapter)
    request = {"url": "https://youtu.be/YE7VzlLtp-4", "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True}

    rejected = client.post(BASE + "/youtube/import", json={**request, "rightsConfirmed": False})
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "youtube_video_rights_required"
    assert adapter.download_calls == 0

    first = client.post(BASE + "/youtube/import", json=request)
    assert first.status_code == 202, first.text
    first_run = first.json()["run"]
    first_run = client.get(BASE + f"/youtube/imports/{first_run['id']}").json()["run"]
    assert first_run["status"] == "completed"
    workspace = client.get(BASE).json()
    asset = next(asset for asset in workspace["assets"] if asset["id"] == first_run["uploadedAssetId"])
    assert asset["videoSource"]["sourceType"] == "youtube"
    assert asset["videoSource"]["originalReference"] == "https://www.youtube.com/watch?v=YE7VzlLtp-4"
    assert asset["videoSource"]["adapter"] == {"id": "yt-dlp", "version": "2026.08.19"}
    assert asset["videoSource"]["status"] == "available"
    assert asset["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert client.get(BASE + f"/assets/{asset['id']}/file").status_code == 200

    duplicate = client.post(BASE + "/youtube/import", json=request)
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["run"]["id"] == first_run["id"]
    assert adapter.download_calls == 1
    assert len(PreproductionStore(tmp_path).load(PROJECT_ID)["assets"]) == 1
    assert list(tmp_path.rglob(".youtube-*")) == []


def test_youtube_probe_classifies_login_and_redacts_secure_session_contents(tmp_path):
    """Returning a raw extractor error or a cookie value must make this fail."""
    secret = "session-secret"
    adapter = ControlledYoutubeAdapter(error=YoutubeImportError("requires_login", f"Sign in required: {secret}"))
    response = client_for(tmp_path, adapter).post(BASE + "/youtube/probe", json={"url": "https://youtu.be/YE7VzlLtp-4", "externalRequestAccepted": True})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "youtube_requires_login"
    assert secret not in response.text


@pytest.mark.parametrize("failure,status,code", [
    (YoutubeImportError("temporarily_unavailable", "connection timed out"), 503, "youtube_temporarily_unavailable"),
    (YoutubeImportError("unsupported_link", "DRM protected"), 422, "youtube_unsupported_link"),
])
def test_youtube_probe_exposes_only_the_four_contract_states(tmp_path, failure, status, code):
    """Changing network/site/DRM diagnostics into an unclassified raw error must fail."""
    response = client_for(tmp_path, ControlledYoutubeAdapter(error=failure)).post(
        BASE + "/youtube/probe", json={"url": "https://youtu.be/YE7VzlLtp-4", "externalRequestAccepted": True},
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "timed out" not in response.text
    assert "DRM protected" not in response.text


def test_youtube_import_requires_explicit_external_request_acceptance_before_download(tmp_path):
    """Dropping the pre-download external-call consent must make this fail."""
    source = tmp_path / "authorized.mp4"
    make_video(source)
    adapter = ControlledYoutubeAdapter(source)
    response = client_for(tmp_path, adapter).post(BASE + "/youtube/import", json={
        "url": "https://youtu.be/YE7VzlLtp-4", "role": "reference", "rightsConfirmed": True,
        "externalRequestAccepted": False,
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "youtube_external_request_confirmation_required"
    assert adapter.download_calls == 0


def test_youtube_download_failure_cleans_its_managed_partial_staging_file(tmp_path):
    """Removing the import finally-cleanup must make this cancellation/network test fail."""
    source = tmp_path / "authorized.mp4"
    make_video(source)

    class PartialDownload(ControlledYoutubeAdapter):
        def download(self, probe, destination, *, cookie=None):
            self.download_calls += 1
            shutil.copyfile(self.media, destination)
            raise YoutubeImportError("temporarily_unavailable", "cancelled after partial write")

    adapter = PartialDownload(source)
    response = client_for(tmp_path, adapter).post(BASE + "/youtube/import", json={
        "url": "https://youtu.be/YE7VzlLtp-4", "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True, "useSession": True,
    })
    assert response.status_code == 202
    run = client_for(tmp_path, adapter).get(BASE + f"/youtube/imports/{response.json()['run']['id']}").json()["run"]
    assert run["status"] == "failed"
    assert run["error"] == "youtube_temporarily_unavailable"
    assert list(tmp_path.glob(".youtube-*")) == []
    assert PreproductionStore(tmp_path).load(PROJECT_ID)["assets"] == []


def test_youtube_restart_recovery_discards_an_unregistered_install(tmp_path):
    """Dropping platform-install recovery must make this restart test fail."""
    from app.project_assets import ProjectAssets

    directory = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets"
    directory.mkdir(parents=True)
    asset_id, filename = "youtube-video-interrupted", "youtube-video-interrupted.mp4"
    (directory / filename).write_bytes(b"orphan")
    (directory / f"youtube-video-{asset_id}.pending.json").write_text(
        '{"version":1,"assetId":"youtube-video-interrupted","file":"youtube-video-interrupted.mp4"}', encoding="utf-8",
    )
    ProjectAssets(tmp_path).recover_youtube_video_installations(PROJECT_ID)
    assert not (directory / filename).exists()
    assert not list(directory.glob("youtube-video-*.pending.json"))


def test_youtube_install_rejects_a_staging_symlink_before_hashing_it(tmp_path, monkeypatch):
    """Hashing before the shared installer validates a staging file follows an attacker-controlled symlink."""
    from app.project_assets import ProjectAssets
    import app.project_assets as project_assets

    outside = tmp_path / "outside.mp4"; outside.write_bytes(b"not-a-video")
    staging = tmp_path / "staging"; staging.mkdir()
    source = staging / ".youtube-link.mp4"; source.symlink_to(outside)
    monkeypatch.setattr(project_assets, "_sha256", lambda _path: pytest.fail("must validate before hashing"))
    with pytest.raises(OSError):
        ProjectAssets(tmp_path).install_youtube_video(
            PROJECT_ID, source=source, staging_root=staging,
            probe=YoutubeProbe("https://www.youtube.com/watch?v=YE7VzlLtp-4", "YE7VzlLtp-4", "样片", 1, "owner"),
            role="reference", metadata={"duration": 1, "width": 1, "height": 1}, confirmed_at="2026-10-02T00:00:00Z",
        )


def test_youtube_session_is_read_only_from_the_injected_system_storage_seam_and_never_returned(tmp_path):
    """Reading a browser profile or exposing the session in API data must make this fail."""
    class SessionStore:
        def get(self): return "session-secret"

    source = tmp_path / "authorized.mp4"
    make_video(source)
    adapter = ControlledYoutubeAdapter(source)
    app = FastAPI()
    project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue(), youtube_adapter=adapter, youtube_session_store=SessionStore()))
    response = TestClient(app).post(BASE + "/youtube/import", json={
        "url": "https://youtu.be/YE7VzlLtp-4", "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True, "useSession": True,
    })
    assert response.status_code == 202, response.text
    run = TestClient(app).get(BASE + f"/youtube/imports/{response.json()['run']['id']}").json()["run"]
    assert run["status"] == "completed"
    assert adapter.cookies and set(adapter.cookies) == {"session-secret"}
    assert "session-secret" not in response.text
    assert "session-secret" not in str(PreproductionStore(tmp_path).load(PROJECT_ID))


def test_youtube_probe_requires_disclosure_and_public_requests_never_read_a_session(tmp_path):
    class SessionStore:
        def get(self): return "session-secret"

    adapter = ControlledYoutubeAdapter()
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue(), youtube_adapter=adapter, youtube_session_store=SessionStore()))
    client = TestClient(app)
    rejected = client.post(BASE + "/youtube/probe", json={"url": "https://youtu.be/YE7VzlLtp-4"})
    assert rejected.status_code == 422 and adapter.probe_calls == 0
    assert client.get(BASE + "/youtube/disclosure").json()["sessionPolicy"].startswith("默认不读取")
    accepted = client.post(BASE + "/youtube/probe", json={"url": "https://youtu.be/YE7VzlLtp-4", "externalRequestAccepted": True})
    assert accepted.status_code == 200 and adapter.cookies == [None]


def test_ytdlp_policy_disables_plugins_geo_bypass_and_oversize_live_or_restricted_content():
    options = _yt_dlp_options(download=True)
    assert options["geo_bypass"] is False and options["plugin_dirs"] == [] and options["remote_components"] == set()
    assert options["max_filesize"] == 200_000_000 and options["allowed_extractors"] == ["youtube"]
    assert _classify_ytdlp_error("DRM protected") .status == "unsupported_link"
    assert _classify_ytdlp_error("Video is not available in your country") .status == "unsupported_link"


def test_youtube_import_run_is_durable_idempotent_cancellable_retryable_and_recovers_without_redownload(tmp_path):
    """Removing durable run identity/cancel/retry/restart recovery must make this E2 fail."""
    source = tmp_path / "authorized.mp4"
    make_video(source)

    class HeldQueue:
        def __init__(self): self.pending = {}
        def submit(self, _kind, run_id, handler, **_kwargs): self.pending[run_id] = handler; return True
        def run(self, run_id): self.pending.pop(run_id)(run_id)

    queue = HeldQueue()
    adapter = ControlledYoutubeAdapter(source)
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, queue, youtube_adapter=adapter))
    client = TestClient(app)
    request = {"url": "https://youtu.be/YE7VzlLtp-4", "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True, "idempotencyKey": "same-authorized-input"}

    created = client.post(BASE + "/youtube/import", json=request)
    assert created.status_code == 202, created.text
    run = created.json()["run"]
    assert run["status"] == "queued"
    duplicate = client.post(BASE + "/youtube/import", json=request)
    assert duplicate.json()["run"]["id"] == run["id"]
    assert adapter.download_calls == 0
    assert client.post(BASE + f"/youtube/imports/{run['id']}/cancel").json()["run"]["status"] == "cancelled"
    queue.run(run["id"])
    assert adapter.download_calls == 0

    interrupted = client.post(BASE + "/youtube/import", json={**request, "idempotencyKey": "restart-me"}).json()["run"]
    recovered_app = FastAPI(); recovered_app.include_router(create_preproduction_router(tmp_path, lambda _: project, HeldQueue(), youtube_adapter=adapter))
    recovered = TestClient(recovered_app).get(BASE + f"/youtube/imports/{interrupted['id']}").json()["run"]
    assert recovered["status"] == "failed"
    assert recovered["error"] == "interrupted"
    assert adapter.download_calls == 0

    retried = client.post(BASE + f"/youtube/imports/{run['id']}/retry")
    assert retried.status_code == 202, retried.text
    retry = retried.json()["run"]
    assert retry["retryOf"] == run["id"] and retry["id"] != run["id"]
    queue.run(retry["id"])
    completed = client.get(BASE + f"/youtube/imports/{retry['id']}").json()["run"]
    assert completed["status"] == "completed"
    assert completed["uploadedAssetId"]
    assert adapter.download_calls == 1


def test_youtube_running_cancel_reaches_the_adapter_cancel_seam_and_cleans_staging(tmp_path):
    """Dropping progress-hook cancellation propagation must make this E2 fail."""
    entered, stopped = Event(), Event()

    class ThreadQueue:
        def submit(self, _kind, run_id, handler, **_kwargs):
            Thread(target=lambda: handler(run_id), daemon=True).start(); return True

    class CancelAwareAdapter(ControlledYoutubeAdapter):
        def download(self, probe, destination, *, cookie=None, cancelled=None):
            self.download_calls += 1; entered.set()
            while not cancelled(): pass
            stopped.set()
            from app.youtube_import import YoutubeImportCancelled
            raise YoutubeImportCancelled()

    adapter = CancelAwareAdapter()
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, ThreadQueue(), youtube_adapter=adapter))
    client = TestClient(app)
    response = client.post(BASE + "/youtube/import", json={"url": "https://youtu.be/YE7VzlLtp-4", "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True, "idempotencyKey": "cancel-running"})
    run = response.json()["run"]
    assert entered.wait(2)
    assert client.post(BASE + f"/youtube/imports/{run['id']}/cancel").status_code == 202
    assert stopped.wait(2)
    for _ in range(20):
        current = client.get(BASE + f"/youtube/imports/{run['id']}").json()["run"]
        if current["status"] == "cancelled": break
    assert current["status"] == "cancelled"
    assert list(tmp_path.rglob(".youtube-*")) == []


def test_youtube_restart_converges_an_asset_installed_before_run_record_commit_without_redownload(tmp_path, monkeypatch):
    """A crash after install but before the run transition must recover the same run."""
    source = tmp_path / "authorized.mp4"; make_video(source)
    adapter = ControlledYoutubeAdapter(source)
    original = __import__("app.preproduction_api", fromlist=["YoutubeImportRuns"]).YoutubeImportRuns.transition
    failed_once = {"value": False}

    def crash_after_install(self, project_id, run_id, **changes):
        if changes.get("status") == "completed" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated process crash")
        return original(self, project_id, run_id, **changes)

    monkeypatch.setattr("app.preproduction_api.YoutubeImportRuns.transition", crash_after_install)
    first = client_for(tmp_path, adapter).post(BASE + "/youtube/import", json={
        "url": "https://youtu.be/YE7VzlLtp-4", "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True,
    }).json()["run"]
    monkeypatch.setattr("app.preproduction_api.YoutubeImportRuns.transition", original)
    recovered_app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    recovered_app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue(), youtube_adapter=adapter))
    recovered = TestClient(recovered_app).get(BASE + f"/youtube/imports/{first['id']}").json()["run"]
    assert recovered["status"] == "completed" and recovered["uploadedAssetId"]
    assert adapter.download_calls == 1
