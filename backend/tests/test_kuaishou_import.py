import pytest


def test_kuaishou_adapter_accepts_only_platform_pages_share_text_and_pinned_short_links():
    """Removing the dedicated host/path/DNS guard must break this contract."""
    from app.kuaishou_import import (
        KUAISHOU_ADAPTER,
        extract_kuaishou_share_url,
        normalize_kuaishou_url,
        resolve_kuaishou_short_url,
    )

    canonical = "https://www.kuaishou.com/short-video/3x9abcDEF"
    assert KUAISHOU_ADAPTER == {"id": "kuaishou-public-page", "version": "1.0.0"}
    assert extract_kuaishou_share_url(f"复制此作品打开快手 {canonical}") == canonical
    assert extract_kuaishou_share_url(f"{canonical} https://v.kuaishou.com/abc") is None
    assert extract_kuaishou_share_url("https://evil.example/video") is None
    assert normalize_kuaishou_url(canonical + "?shareToken=secret") == canonical
    assert normalize_kuaishou_url("http://www.kuaishou.com/short-video/3x9abcDEF") is None
    assert resolve_kuaishou_short_url(
        "https://v.kuaishou.com/abc", dns_resolver=lambda host: ["1.1.1.1"] if host == "v.kuaishou.com" else ["8.8.8.8"],
        redirect_once=lambda *_: canonical,
    ) == canonical
    assert resolve_kuaishou_short_url(
        "https://v.kuaishou.com/abc", dns_resolver=lambda _: ["127.0.0.1"],
        redirect_once=lambda *_: pytest.fail("private DNS must be rejected before request"),
    ) is None
    redirects = iter([
        "https://v.kuaishou.com/step-two",
        "https://v.kuaishou.com/step-three",
        canonical,
    ])
    facts = []
    assert resolve_kuaishou_short_url(
        "https://v.kuaishou.com/abc", dns_resolver=lambda _: ["1.1.1.1"],
        redirect_once=lambda *_: next(redirects), facts=facts,
    ) == canonical
    assert [fact["hop"] for fact in facts] == [1, 2, 3]


def test_kuaishou_session_store_is_explicit_and_never_echoes_cookie():
    """Changing the secure-storage-only session boundary must break this test."""
    from app.kuaishou_import import SystemKuaishouCookieStore

    class Backend:
        values = {}
        def get_password(self, service, account): return self.values.get((service, account))
        def set_password(self, service, account, value): self.values[(service, account)] = value
        def delete_password(self, service, account): self.values.pop((service, account), None)
    Backend.__module__ = "keyring.backends.macOS"
    store = SystemKuaishouCookieStore(Backend())
    store.set("session-secret")
    assert store.get() == "session-secret"
    store.delete()
    assert store.get() is None


def test_kuaishou_public_page_parser_and_default_redirect_path_are_dedicated():
    """Replacing the public JSON parser or the Kuaishou-pinned redirect path breaks this."""
    from app.kuaishou_import import _kuaishou_redirect_once, parse_kuaishou_public_page
    probe = parse_kuaishou_public_page("https://www.kuaishou.com/short-video/3x9abcDEF", '{"photoId":"3x9abcDEF","caption":"公开作品","duration":2000,"userName":"作者","photoUrl":"https://js2.a.yximgs.com/media.mp4"}')
    assert probe.video_id == "3x9abcDEF" and probe.title == "公开作品" and probe.duration == 2.0
    class Response:
        status = 302
        headers = {"Location": "https://www.kuaishou.com/short-video/3x9abcDEF"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
    class Opener:
        def open(self, *_args, **_kwargs): return Response()
    assert _kuaishou_redirect_once("https://v.kuaishou.com/abc", ("1.1.1.1",), opener_factory=lambda *_: Opener()).startswith("https://www.kuaishou.com/")


def test_kuaishou_public_page_parser_reads_embedded_json_before_login_navigation_and_classifies_http_failures():
    """A public page may contain a login navigation label; only the frozen data object decides availability."""
    from urllib.error import HTTPError

    from app.kuaishou_import import _classify_kuaishou_http_error, parse_kuaishou_public_page

    canonical = "https://www.kuaishou.com/short-video/3x9abcDEF"
    page = '登录 <script>window.__APOLLO_STATE__ = {"entry":{"photoId":"3x9abcDEF","caption":"含\\\"引号\\\"的作品","duration":2000,"userName":"作者","photoUrl":"https://js2.a.yximgs.com/media.mp4"}};</script>'
    assert parse_kuaishou_public_page(canonical, page).title == '含"引号"的作品'
    assert _classify_kuaishou_http_error(HTTPError(canonical, 401, "unauthorized", {}, None)).status == "requires_login"
    assert _classify_kuaishou_http_error(HTTPError(canonical, 404, "missing", {}, None)).status == "unsupported_link"
    assert _classify_kuaishou_http_error(HTTPError(canonical, 429, "rate limit", {}, None)).status == "temporarily_unavailable"
