import hashlib
import shutil
import subprocess
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.douyin_import import (
    DouyinImportError,
    DouyinProbe,
    SystemDouyinCookieStore,
    _classify_douyin_ytdlp_error,
    _douyin_yt_dlp_options,
    extract_douyin_share_url,
    normalize_douyin_url,
    resolve_douyin_short_url,
)
from app.preproduction import PreproductionStore
from app.preproduction_api import create_preproduction_router


PROJECT_ID = "project-douyin-source"
BASE = f"/api/projects/{PROJECT_ID}/preproduction"
CANONICAL = "https://www.douyin.com/video/7420000000000000001"


class Queue:
    def submit(self, _kind, run_id, handler, **_kwargs):
        handler(run_id)
        return True


class ControlledDouyinAdapter:
    """E2 seam: it returns a local authorized fixture and never calls Douyin."""

    version = "2026.08.19"
    is_controlled = True

    def __init__(self, media: Path | None = None, error: Exception | None = None):
        self.media, self.error = media, error
        self.probe_calls = 0
        self.download_calls = 0
        self.cookies = []

    def probe(self, url: str, *, cookie: str | None = None, cancelled=None) -> DouyinProbe:
        self.probe_calls += 1
        if self.error:
            raise self.error
        if cancelled is not None and cancelled():
            from app.youtube_import import YoutubeImportCancelled
            raise YoutubeImportCancelled()
        self.cookies.append(cookie)
        return DouyinProbe(canonical_url=CANONICAL, video_id="7420000000000000001", title="授权抖音样片", duration=1.0, uploader="Golden owner")

    def download(self, probe: DouyinProbe, destination: Path, *, cookie: str | None = None, cancelled=None) -> Path:
        self.download_calls += 1
        if cancelled is not None and cancelled():
            from app.youtube_import import YoutubeImportCancelled
            raise YoutubeImportCancelled()
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


def client_for(tmp_path: Path, adapter: ControlledDouyinAdapter, **kwargs) -> TestClient:
    app = FastAPI()
    project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue(), douyin_adapter=adapter, **kwargs))
    return TestClient(app)


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="抖音受控导入合同需要 ffmpeg 和 ffprobe",
)


def test_douyin_share_text_requires_exactly_one_safe_douyin_url():
    """Treating arbitrary prose/foreign URLs or multiple URLs as a source must fail."""
    assert extract_douyin_share_url("3.21 复制打开抖音，看看【授权作者】 https://v.douyin.com/AbC123/ ") == "https://v.douyin.com/AbC123/"
    assert extract_douyin_share_url(f"看看 {CANONICAL}，谢谢！") == CANONICAL
    assert extract_douyin_share_url("https://evil.example/a") is None
    assert extract_douyin_share_url(f"{CANONICAL} https://v.douyin.com/AbC123/") is None
    assert normalize_douyin_url("https://www.douyin.com/video/7420000000000000001?previous_page=app_code_link") == CANONICAL
    assert normalize_douyin_url("http://www.douyin.com/video/7420000000000000001") is None


def test_douyin_short_link_resolver_rejects_private_or_foreign_hops_and_keeps_only_redacted_facts():
    """Following unpinned/private redirects or retaining the share token must fail."""
    def dns(host):
        return ["1.1.1.1"] if host == "v.douyin.com" else ["8.8.8.8"]

    facts = []
    resolved = resolve_douyin_short_url(
        "https://v.douyin.com/AbC123/", dns_resolver=dns,
        redirect_once=lambda url, addresses: (CANONICAL, {"host": "v.douyin.com", "addresses": addresses}),
        facts=facts,
    )
    assert resolved == CANONICAL
    assert facts == [{"hostHash": facts[0]["hostHash"], "tokenHash": facts[0]["tokenHash"], "hop": 1}]
    assert "AbC123" not in str(facts) and "1.1.1.1" not in str(facts)

    assert resolve_douyin_short_url(
        "https://v.douyin.com/AbC123/", dns_resolver=lambda _host: ["127.0.0.1"],
        redirect_once=lambda *_: pytest.fail("private DNS must not be requested"),
    ) is None
    assert resolve_douyin_short_url(
        "https://v.douyin.com/AbC123/", dns_resolver=dns,
        redirect_once=lambda *_: "https://evil.example/video/7420000000000000001",
    ) is None


@pytest.mark.parametrize("final_addresses", [["127.0.0.1"], ["8.8.8.8", "127.0.0.1"]])
def test_douyin_three_hop_redirect_rejects_an_unsafe_final_canonical_dns_answer(final_addresses):
    """Returning the post-third-hop canonical URL without checking its DNS boundary must fail."""
    redirects = iter(["https://v.douyin.com/Two/", "https://v.douyin.com/Three/", CANONICAL])
    facts = []
    def dns(host): return final_addresses if host == "www.douyin.com" else ["1.1.1.1"]
    assert resolve_douyin_short_url("https://v.douyin.com/One/", dns_resolver=dns,
                                    redirect_once=lambda *_: next(redirects), facts=facts) is None
    assert "One" not in str(facts) and "127.0.0.1" not in str(facts)


def test_douyin_short_link_dns_outage_is_retryable_without_leaking_link_text():
    """Collapsing a resolver outage into unsupported or returning the raw share link must fail."""
    with pytest.raises(DouyinImportError) as error:
        resolve_douyin_short_url("https://v.douyin.com/OutageToken/", dns_resolver=lambda _host: (_ for _ in ()).throw(OSError("offline")))
    assert error.value.status == "temporarily_unavailable"
    assert "OutageToken" not in str(error.value)


def test_douyin_probe_requires_disclosure_and_returns_real_adapter_not_e3_claim(tmp_path):
    """Skipping the pre-request disclosure or claiming controlled E2 as E3 must fail."""
    adapter = ControlledDouyinAdapter()
    client = client_for(tmp_path, adapter, douyin_short_link_resolver=lambda _value, *, facts=None: CANONICAL)
    rejected = client.post(BASE + "/douyin/probe", json={"url": CANONICAL})
    assert rejected.status_code == 422 and adapter.probe_calls == 0
    response = client.post(BASE + "/douyin/probe", json={"url": "分享 https://v.douyin.com/AbC123/", "externalRequestAccepted": True})
    assert response.status_code == 200, response.text
    assert response.json()["adapter"] == {"id": "yt-dlp", "version": "2026.08.19"}
    assert response.json()["lastRealVerificationAt"] is None
    assert response.json()["externalRequest"]["shortLinkPolicy"].startswith("v.douyin.com")


def test_douyin_import_requires_rights_and_installs_one_hashed_project_asset(tmp_path):
    """Removing rights confirmation, controlled staging, or stable asset installation must fail."""
    source = tmp_path / "authorized.mp4"; make_video(source)
    adapter = ControlledDouyinAdapter(source)
    client = client_for(tmp_path, adapter)
    request = {"url": CANONICAL, "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True}
    denied = client.post(BASE + "/douyin/import", json={**request, "rightsConfirmed": False})
    assert denied.status_code == 422 and denied.json()["detail"]["code"] == "douyin_video_rights_required"
    first = client.post(BASE + "/douyin/import", json=request)
    assert first.status_code == 202, first.text
    run = client.get(BASE + f"/douyin/imports/{first.json()['run']['id']}").json()["run"]
    assert run["status"] == "completed" and run["uploadedAssetId"]
    asset = next(item for item in client.get(BASE).json()["assets"] if item["id"] == run["uploadedAssetId"])
    assert asset["videoSource"]["sourceType"] == "douyin"
    assert asset["videoSource"]["originalReference"] == CANONICAL
    assert asset["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert client.post(BASE + "/douyin/import", json=request).json()["run"]["id"] == run["id"]
    assert adapter.download_calls == 1 and list(tmp_path.rglob(".douyin-*")) == []


@pytest.mark.parametrize("failure,status,code", [
    (DouyinImportError("requires_login", "fresh cookie: secret"), 409, "douyin_requires_login"),
    (DouyinImportError("temporarily_unavailable", "timeout secret"), 503, "douyin_temporarily_unavailable"),
    (DouyinImportError("unsupported_link", "DRM secret"), 422, "douyin_unsupported_link"),
])
def test_douyin_errors_are_four_state_and_redacted(tmp_path, failure, status, code):
    """Leaking extractor/session text or changing a four-state error must fail."""
    response = client_for(tmp_path, ControlledDouyinAdapter(error=failure)).post(
        BASE + "/douyin/probe", json={"url": CANONICAL, "externalRequestAccepted": True},
    )
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert "secret" not in response.text


def test_douyin_explicit_secure_session_is_never_persisted_or_echoed(tmp_path):
    """Reading a browser profile or returning a selected secure session must fail."""
    class Store:
        def get(self): return "session-secret"

    source = tmp_path / "authorized.mp4"; make_video(source)
    adapter = ControlledDouyinAdapter(source)
    response = client_for(tmp_path, adapter, douyin_session_store=Store()).post(BASE + "/douyin/import", json={
        "url": CANONICAL, "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True, "useSession": True,
    })
    assert response.status_code == 202 and set(adapter.cookies) == {"session-secret"}
    assert "session-secret" not in response.text
    assert "session-secret" not in str(PreproductionStore(tmp_path).load(PROJECT_ID))


def test_douyin_run_cancels_retries_and_recovers_without_a_second_download(tmp_path, monkeypatch):
    """Dropping durable cancellation/retry or post-install crash reconciliation must fail."""
    source = tmp_path / "authorized.mp4"; make_video(source)

    class HeldQueue:
        def __init__(self): self.pending = {}
        def submit(self, _kind, run_id, handler, **_kwargs): self.pending[run_id] = handler; return True
        def run(self, run_id): self.pending.pop(run_id)(run_id)

    queue, adapter = HeldQueue(), ControlledDouyinAdapter(source)
    app = FastAPI(); project = SimpleNamespace(id=PROJECT_ID, referenceMedia=None, localPreprocessing=None)
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, queue, douyin_adapter=adapter))
    client = TestClient(app)
    request = {"url": CANONICAL, "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True, "idempotencyKey": "same"}
    run = client.post(BASE + "/douyin/import", json=request).json()["run"]
    assert client.post(BASE + f"/douyin/imports/{run['id']}/cancel").json()["run"]["status"] == "cancelled"
    queue.run(run["id"]); assert adapter.download_calls == 0
    retry = client.post(BASE + f"/douyin/imports/{run['id']}/retry").json()["run"]
    queue.run(retry["id"])
    assert client.get(BASE + f"/douyin/imports/{retry['id']}").json()["run"]["status"] == "completed"


def test_douyin_fixed_dependency_policy_disables_js_ejs_plugins_proxy_and_bypass():
    """Allowing ambient JS/EJS/plugins/proxy or a generic extractor must fail."""
    options = _douyin_yt_dlp_options(download=True)
    assert options["allowed_extractors"] == ["douyin"]
    assert options["js_runtimes"] == {} and options["remote_components"] == set() and options["plugin_dirs"] == []
    assert options["proxy"] == "" and options["geo_bypass"] is False and options["enable_file_urls"] is False
    assert _classify_douyin_ytdlp_error("Fresh cookies are needed").status == "requires_login"
    assert _classify_douyin_ytdlp_error("geo-restricted").status == "unsupported_link"
    import yt_dlp
    assert yt_dlp.version.__version__ == "2026.08.19"


def test_douyin_cookie_store_never_uses_browser_profiles():
    """A system store must only hold an explicit Netscape payload in its dedicated keyring entry."""
    class Backend:
        values = {}
        def get_password(self, service, account): return self.values.get((service, account))
        def set_password(self, service, account, value): self.values[(service, account)] = value
        def delete_password(self, service, account): self.values.pop((service, account), None)
    Backend.__module__ = "keyring.backends.macOS"
    store = SystemDouyinCookieStore(Backend())
    store.set("# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t0\ts_v_web_id\tsecret")
    assert store.get().endswith("s_v_web_id\tsecret")
    store.delete(); assert store.get() is None


def test_douyin_session_settings_only_reports_configuration_and_never_echoes_cookie():
    """A production settings API must not rely solely on router test injection or return the cookie."""
    from app.douyin_session_api import create_douyin_session_router
    class Store:
        value = None
        def get(self): return self.value
        def set(self, value): self.value = value
        def delete(self): self.value = None
    app = FastAPI(); app.include_router(create_douyin_session_router(Store()))
    client = TestClient(app)
    assert client.get("/api/source-sessions/douyin").json() == {"configured": False}
    saved = client.put("/api/source-sessions/douyin", json={"cookie": "session-secret"})
    assert saved.status_code == 204 and "session-secret" not in saved.text
    assert client.get("/api/source-sessions/douyin").json() == {"configured": True}
    assert client.delete("/api/source-sessions/douyin").status_code == 204


def test_douyin_restart_reconciles_an_asset_installed_before_run_transition(tmp_path, monkeypatch):
    """A crash after asset installation must reconcile the durable run rather than redownload."""
    source = tmp_path / "authorized.mp4"; make_video(source)
    adapter = ControlledDouyinAdapter(source)
    from app.platform_video_import import PlatformVideoImportRuns
    original, failed_once = PlatformVideoImportRuns.transition, {"value": False}
    def crash_after_install(self, project_id, run_id, **changes):
        if self._platform == "douyin" and changes.get("status") == "completed" and not failed_once["value"]:
            failed_once["value"] = True; raise OSError("simulated crash")
        return original(self, project_id, run_id, **changes)
    monkeypatch.setattr(PlatformVideoImportRuns, "transition", crash_after_install)
    run = client_for(tmp_path, adapter).post(BASE + "/douyin/import", json={
        "url": CANONICAL, "role": "reference", "rightsConfirmed": True, "externalRequestAccepted": True,
    }).json()["run"]
    monkeypatch.setattr(PlatformVideoImportRuns, "transition", original)
    recovered = client_for(tmp_path, adapter).get(BASE + f"/douyin/imports/{run['id']}").json()["run"]
    assert recovered["status"] == "completed" and recovered["uploadedAssetId"] and adapter.download_calls == 1
