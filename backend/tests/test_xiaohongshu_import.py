import pytest


def test_xiaohongshu_adapter_contract_requires_a_dedicated_extractor_and_safe_links():
    """The platform adapter must never fall back to Generic or accept unsafe share links."""
    from app.xiaohongshu_import import (
        _xiaohongshu_yt_dlp_options,
        extract_xiaohongshu_share_url,
        normalize_xiaohongshu_url,
        resolve_xiaohongshu_short_url,
        xiaohongshu_source_reference,
    )

    canonical = "https://www.xiaohongshu.com/explore/64aabbccddeeff0011223344"
    assert extract_xiaohongshu_share_url(f"复制后打开小红书 {canonical}") == canonical
    assert extract_xiaohongshu_share_url("https://evil.example/a") is None
    assert extract_xiaohongshu_share_url(f"{canonical} https://xhslink.com/m/abc") is None
    tokenized = canonical + "?xsec_token=ignored"
    assert normalize_xiaohongshu_url(tokenized) == tokenized
    assert xiaohongshu_source_reference(tokenized) == canonical
    assert normalize_xiaohongshu_url("http://www.xiaohongshu.com/explore/64aabbccddeeff0011223344") is None
    assert resolve_xiaohongshu_short_url(
        "https://xhslink.com/m/abc", dns_resolver=lambda host: ["1.1.1.1"] if host == "xhslink.com" else ["8.8.8.8"],
        redirect_once=lambda *_: canonical,
    ) == canonical
    assert resolve_xiaohongshu_short_url(
        "https://xhslink.com/m/abc", dns_resolver=lambda _host: ["127.0.0.1"],
        redirect_once=lambda *_: pytest.fail("private DNS must be rejected before request"),
    ) is None
    options = _xiaohongshu_yt_dlp_options(download=True)
    assert options["allowed_extractors"] == ["xiaohongshu"]
    assert options["proxy"] == "" and options["geo_bypass"] is False and options["enable_file_urls"] is False
