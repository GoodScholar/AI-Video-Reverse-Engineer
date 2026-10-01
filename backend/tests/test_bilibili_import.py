import hashlib
import shutil
import subprocess
from threading import Event, Thread
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bilibili_import import BilibiliImportError, BilibiliPart, BilibiliProbe, SystemBilibiliCookieStore, _b23_redirect_once, _bilibili_yt_dlp_options, _classify_bilibili_ytdlp_error, normalize_bilibili_url
from app.preproduction import PreproductionStore
from app.preproduction_api import create_preproduction_router


PROJECT_ID = "project-bilibili-source"
BASE = f"/api/projects/{PROJECT_ID}/preproduction"


class Queue:
    def submit(self, _kind, run_id, handler, **_kwargs):
        handler(run_id)
        return True


class ControlledBilibiliAdapter:
    """E2 seam: no request reaches Bilibili and no actual session is used."""

    version = "2026.08.19"
    is_controlled = True

    def __init__(self, media: Path | None = None, error: Exception | None = None):
        self.media, self.error = media, error
        self.probe_calls = 0
        self.download_calls = 0
        self.cookies = []

    def probe(self, url: str, *, cookie: str | None = None) -> BilibiliProbe:
        self.probe_calls += 1
        if self.error:
            raise self.error
        self.cookies.append(cookie)
        return BilibiliProbe(
            canonical_url="https://www.bilibili.com/video/BV1xx411c7mD",
            video_id="BV1xx411c7mD",
            title="授权多分P样片",
            uploader="Golden owner",
            parts=(
                BilibiliPart(number=1, cid="111", title="第一集", duration=1.0),
                BilibiliPart(number=2, cid="222", title="第二集", duration=1.0),
            ),
        )

    def download(self, probe: BilibiliProbe, part: BilibiliPart, destination: Path, *, cookie: str | None = None) -> Path:
        self.download_calls += 1
        self.cookies.append(cookie)
        assert self.media is not None
        shutil.copyfile(self.media, destination)
        return destination


def make_video(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=6:duration=1",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True)


def client_for(tmp_path: Path, adapter: ControlledBilibiliAdapter) -> TestClient:
    app = FastAPI()
    project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(
        tmp_path, lambda _: project, Queue(), bilibili_adapter=adapter,
    ))
    return TestClient(app)


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="B站受控导入合同需要 ffmpeg 和 ffprobe",
)


def test_bilibili_standard_page_and_share_link_normalize_but_b23_is_not_followed_implicitly():
    """Removing standard page recognition or treating a shortener as a trusted video identity must fail."""
    canonical = "https://www.bilibili.com/video/BV1xx411c7mD"
    assert normalize_bilibili_url("https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.999.0.0") == canonical
    assert normalize_bilibili_url("https://www.bilibili.com/video/BV1xx411c7mD?p=2") == canonical + "?p=2"
    assert normalize_bilibili_url("https://b23.tv/abc123") is None
    assert normalize_bilibili_url("https://evil.example/video/BV1xx411c7mD") is None


def test_bilibili_b23_share_is_resolved_only_by_the_limited_redirect_boundary(tmp_path):
    """Accepting a b23 link without a restricted resolver, or allowing it into yt-dlp unchanged, must fail."""
    canonical = "https://www.bilibili.com/video/BV1xx411c7mD"
    source = tmp_path / "authorized.mp4"; make_video(source)
    adapter = ControlledBilibiliAdapter(source)
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(
        tmp_path, lambda _: project, Queue(), bilibili_adapter=adapter,
        bilibili_short_link_resolver=lambda value: canonical if value == "https://b23.tv/abc123" else None,
    ))
    response = TestClient(app).post(BASE + "/bilibili/probe", json={"url": "https://b23.tv/abc123", "externalRequestAccepted": True})
    assert response.status_code == 200, response.text
    assert adapter.probe_calls == 1


def test_bilibili_probe_lists_all_parts_and_import_requires_explicit_part_selection(tmp_path):
    """Dropping multi-part disclosure or silently choosing P1 must make this fail."""
    source = tmp_path / "authorized.mp4"
    make_video(source)
    adapter = ControlledBilibiliAdapter(source)
    client = client_for(tmp_path, adapter)
    request = {
        "url": "https://www.bilibili.com/video/BV1xx411c7mD", "role": "reference",
        "rightsConfirmed": True, "externalRequestAccepted": True,
    }

    probe = client.post(BASE + "/bilibili/probe", json={"url": request["url"], "externalRequestAccepted": True})
    assert probe.status_code == 200, probe.text
    assert probe.json()["parts"] == [
        {"number": 1, "cid": "111", "title": "第一集", "durationSeconds": 1.0},
        {"number": 2, "cid": "222", "title": "第二集", "durationSeconds": 1.0},
    ]

    rejected = client.post(BASE + "/bilibili/import", json=request)
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "bilibili_part_selection_required"
    assert adapter.download_calls == 0

    imported = client.post(BASE + "/bilibili/import", json={**request, "part": 2})
    assert imported.status_code == 202, imported.text
    run = client.get(BASE + f"/bilibili/imports/{imported.json()['run']['id']}").json()["run"]
    assert run["status"] == "completed"
    asset = next(asset for asset in client.get(BASE).json()["assets"] if asset["id"] == run["uploadedAssetId"])
    assert asset["videoSource"]["sourceType"] == "bilibili"
    assert asset["videoSource"]["originalReference"] == "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
    assert asset["videoSource"]["adapter"] == {"id": "yt-dlp", "version": "2026.08.19"}
    assert asset["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert client.get(BASE + f"/assets/{asset['id']}/file").status_code == 200
    assert adapter.download_calls == 1
    assert list(tmp_path.rglob(".bilibili-*")) == []
    assert len(PreproductionStore(tmp_path).load(PROJECT_ID)["assets"]) == 1


def test_bilibili_login_and_unavailable_errors_are_redacted_and_mapped_to_four_states(tmp_path):
    """Exposing raw extractor/session errors or losing a contract state must make this fail."""
    secret = "session-secret"
    client = client_for(tmp_path, ControlledBilibiliAdapter(error=BilibiliImportError("requires_login", secret)))
    login = client.post(BASE + "/bilibili/probe", json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "externalRequestAccepted": True})
    assert login.status_code == 409
    assert login.json()["detail"]["code"] == "bilibili_requires_login"
    assert secret not in login.text

    unavailable = client_for(tmp_path, ControlledBilibiliAdapter(error=BilibiliImportError("temporarily_unavailable", "timeout"))).post(
        BASE + "/bilibili/probe", json={"url": "https://www.bilibili.com/video/BV1xx411c7mD", "externalRequestAccepted": True},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "bilibili_temporarily_unavailable"
    assert "timeout" not in unavailable.text


def test_bilibili_explicit_session_is_read_only_and_never_returned(tmp_path):
    """Silently falling back without an explicitly selected session must make this fail."""
    class SessionStore:
        def get(self): return "session-secret"

    source = tmp_path / "authorized.mp4"; make_video(source)
    adapter = ControlledBilibiliAdapter(source)
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue(), bilibili_adapter=adapter, bilibili_session_store=SessionStore()))
    response = TestClient(app).post(BASE + "/bilibili/import", json={
        "url": "https://www.bilibili.com/video/BV1xx411c7mD", "part": 1, "role": "reference",
        "rightsConfirmed": True, "externalRequestAccepted": True, "useSession": True,
    })
    assert response.status_code == 202, response.text
    assert set(adapter.cookies) == {"session-secret"}
    assert "session-secret" not in response.text
    assert "session-secret" not in str(PreproductionStore(tmp_path).load(PROJECT_ID))


def test_bilibili_import_failure_and_restart_recovery_cleanup_without_asset(tmp_path):
    """Leaving a partial managed file or retrying an interrupted run automatically must make this fail."""
    source = tmp_path / "authorized.mp4"; make_video(source)

    class PartialDownload(ControlledBilibiliAdapter):
        def download(self, probe, part, destination, *, cookie=None):
            self.download_calls += 1; shutil.copyfile(self.media, destination)
            raise BilibiliImportError("temporarily_unavailable", "partial timeout")

    failed = client_for(tmp_path, PartialDownload(source)).post(BASE + "/bilibili/import", json={
        "url": "https://www.bilibili.com/video/BV1xx411c7mD", "part": 1, "role": "reference",
        "rightsConfirmed": True, "externalRequestAccepted": True,
    })
    run = client_for(tmp_path, ControlledBilibiliAdapter(source)).get(BASE + f"/bilibili/imports/{failed.json()['run']['id']}").json()["run"]
    assert run["status"] == "failed" and run["error"] == "bilibili_temporarily_unavailable"
    assert list(tmp_path.rglob(".bilibili-*")) == []
    assert PreproductionStore(tmp_path).load(PROJECT_ID)["assets"] == []

    class HeldQueue:
        def __init__(self): self.pending = {}
        def submit(self, _kind, run_id, handler, **_kwargs): self.pending[run_id] = handler; return True

    queue, adapter = HeldQueue(), ControlledBilibiliAdapter(source)
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, queue, bilibili_adapter=adapter))
    waiting = TestClient(app).post(BASE + "/bilibili/import", json={
        "url": "https://www.bilibili.com/video/BV1xx411c7mD", "part": 2, "role": "reference",
        "rightsConfirmed": True, "externalRequestAccepted": True, "idempotencyKey": "restart-me",
    }).json()["run"]
    recovered_app = FastAPI(); recovered_app.include_router(create_preproduction_router(tmp_path, lambda _: project, HeldQueue(), bilibili_adapter=adapter))
    recovered = TestClient(recovered_app).get(BASE + f"/bilibili/imports/{waiting['id']}").json()["run"]
    assert recovered["status"] == "failed" and recovered["error"] == "interrupted"
    assert adapter.download_calls == 0


def test_bilibili_yt_dlp_policy_forces_single_extractor_mp4_merge_and_no_bypass():
    """Allowing ambient plugins/proxy/geo bypass or a non-MP4 stream policy must make this fail."""
    options = _bilibili_yt_dlp_options(download=True, playlist=False)
    assert options["allowed_extractors"] == ["bilibili"]
    assert options["format"] == "bv*+ba/b" and options["merge_output_format"] == "mp4"
    assert options["plugin_dirs"] == [] and options["remote_components"] == set()
    assert options["proxy"] == "" and options["geo_bypass"] is False and options["enable_file_urls"] is False


def test_bilibili_yt_dlp_policy_uses_the_injected_ffmpeg_location_for_merging():
    """Ignoring a non-PATH ffmpeg deployment would make separate DASH streams unmergeable."""
    assert _bilibili_yt_dlp_options(download=True, playlist=False, ffmpeg_path="/opt/media/bin/ffmpeg")["ffmpeg_location"] == "/opt/media/bin/ffmpeg"


@pytest.mark.parametrize("message", ["geo-restricted", "premium members only", "supporter-only"])
def test_bilibili_classifier_marks_restricted_content_unsupported(message):
    """Treating platform restrictions as retryable would encourage bypass attempts."""
    assert _classify_bilibili_ytdlp_error(message).status == "unsupported_link"


def test_b23_redirect_opener_disables_ambient_proxies():
    """A b23 resolution must not inherit user proxy environment settings."""
    captured = []

    class Opener:
        def open(self, *_args, **_kwargs):
            raise OSError("offline")

    with pytest.raises(OSError):
        _b23_redirect_once("https://b23.tv/abc123", opener_factory=lambda *handlers: (captured.extend(handlers), Opener())[1])
    proxy = next(handler for handler in captured if handler.__class__.__name__ == "ProxyHandler")
    assert proxy.proxies == {}


def test_bilibili_cookie_store_writes_only_to_the_system_keyring_and_can_forget_it():
    """Saving a session anywhere other than the dedicated OS keyring account must fail this contract."""
    class Backend:
        values = {}
        def get_password(self, service, account): return self.values.get((service, account))
        def set_password(self, service, account, value): self.values[(service, account)] = value
        def delete_password(self, service, account): self.values.pop((service, account), None)
    Backend.__module__ = "keyring.backends.macOS"
    store = SystemBilibiliCookieStore(Backend())
    store.set("# Netscape HTTP Cookie File\n.example\tTRUE\t/\tFALSE\t0\tSESSDATA\tsecret")
    assert store.get().endswith("SESSDATA\tsecret")
    store.delete()
    assert store.get() is None


def test_bilibili_session_settings_api_never_returns_the_cookie():
    """A settings endpoint may report configured state but must never echo a session."""
    from app.bilibili_session_api import create_bilibili_session_router

    class Store:
        value = None
        def get(self): return self.value
        def set(self, value): self.value = value
        def delete(self): self.value = None

    app = FastAPI(); app.include_router(create_bilibili_session_router(Store()))
    client = TestClient(app)
    assert client.get("/api/source-sessions/bilibili").json() == {"configured": False}
    saved = client.put("/api/source-sessions/bilibili", json={"cookie": "session-secret"})
    assert saved.status_code == 204 and "session-secret" not in saved.text
    assert client.get("/api/source-sessions/bilibili").json() == {"configured": True}
    assert client.delete("/api/source-sessions/bilibili").status_code == 204


def test_bilibili_retry_is_idempotent_after_a_transient_failure(tmp_path):
    """Dropping durable retry identity or retrying without a new successful installation must make this fail."""
    source = tmp_path / "authorized.mp4"; make_video(source)
    class TransientDownload(ControlledBilibiliAdapter):
        transient = True
        def download(self, probe, part, destination, *, cookie=None):
            if self.transient:
                self.download_calls += 1
                raise BilibiliImportError("temporarily_unavailable", "temporary")
            return super().download(probe, part, destination, cookie=cookie)

    adapter = TransientDownload(source)
    client = client_for(tmp_path, adapter)
    started = client.post(BASE + "/bilibili/import", json={
        "url": "https://www.bilibili.com/video/BV1xx411c7mD", "part": 1, "role": "reference",
        "rightsConfirmed": True, "externalRequestAccepted": True,
    }).json()["run"]
    assert client.get(BASE + f"/bilibili/imports/{started['id']}").json()["run"]["status"] == "failed"
    adapter.transient = False
    retry = client.post(BASE + f"/bilibili/imports/{started['id']}/retry").json()["run"]
    assert retry["retryOf"] == started["id"] and retry["id"] != started["id"]
    duplicate = client.post(BASE + f"/bilibili/imports/{started['id']}/retry").json()["run"]
    assert duplicate["id"] == retry["id"]
    assert client.get(BASE + f"/bilibili/imports/{retry['id']}").json()["run"]["status"] == "completed"


def test_bilibili_running_cancel_reaches_adapter_and_removes_staging(tmp_path):
    """Removing cancellation propagation must make this fail instead of leaving a running download."""
    entered, stopped = Event(), Event()

    class ThreadQueue:
        def submit(self, _kind, run_id, handler, **_kwargs):
            Thread(target=lambda: handler(run_id), daemon=True).start(); return True

    class CancelAwareAdapter(ControlledBilibiliAdapter):
        def download(self, probe, part, destination, *, cookie=None, cancelled=None):
            self.download_calls += 1; entered.set()
            while not cancelled(): pass
            stopped.set()
            from app.youtube_import import YoutubeImportCancelled
            raise YoutubeImportCancelled()

    adapter = CancelAwareAdapter()
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, ThreadQueue(), bilibili_adapter=adapter))
    client = TestClient(app)
    run = client.post(BASE + "/bilibili/import", json={
        "url": "https://www.bilibili.com/video/BV1xx411c7mD", "part": 1, "role": "reference",
        "rightsConfirmed": True, "externalRequestAccepted": True,
    }).json()["run"]
    assert entered.wait(2)
    assert client.post(BASE + f"/bilibili/imports/{run['id']}/cancel").status_code == 202
    assert stopped.wait(2)
    for _ in range(20):
        current = client.get(BASE + f"/bilibili/imports/{run['id']}").json()["run"]
        if current["status"] == "cancelled": break
    assert current["status"] == "cancelled"
    assert list(tmp_path.rglob(".bilibili-*")) == []
