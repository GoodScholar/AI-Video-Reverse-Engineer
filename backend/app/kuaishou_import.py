"""Constrained public Kuaishou adapter; never signs requests or bypasses access controls."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .analysis_service_secrets import SecureStorageUnavailable
from .platform_video_import import PlatformVideoImportRuns, public_platform_video_import_run
from .xiaohongshu_import import _PinnedHTTPSHandler, _is_public_address, _public_addresses
from .youtube_import import MAX_YOUTUBE_SOURCE_BYTES, MAX_YOUTUBE_SOURCE_DURATION_SECONDS, YoutubeImportCancelled, _SAFE_KEYRING_MODULES

KUAISHOU_ADAPTER = {"id": "kuaishou-public-page", "version": "1.0.0"}
MAX_KUAISHOU_SOURCE_BYTES = MAX_YOUTUBE_SOURCE_BYTES
MAX_KUAISHOU_SOURCE_DURATION_SECONDS = MAX_YOUTUBE_SOURCE_DURATION_SECONDS
KuaishouStatus = Literal["available", "requires_login", "temporarily_unavailable", "unsupported_link"]
_DIRECT_HOSTS = frozenset({"www.kuaishou.com"})
_SHORT_HOSTS = frozenset({"v.kuaishou.com"})
_URL = re.compile(r"https?://[^\s<>\"'，。！？；：、）】》]+", re.I)

class KuaishouImportError(RuntimeError):
    def __init__(self, status: KuaishouStatus): super().__init__(status); self.status = status

@dataclass(frozen=True)
class KuaishouProbe:
    canonical_url: str; video_id: str; title: str; duration: float | None; uploader: str | None
    status: KuaishouStatus = "available"; media_url: str | None = None

class KuaishouImportRuns(PlatformVideoImportRuns):
    def __init__(self, root: Path): super().__init__(root, "kuaishou")

def public_kuaishou_import_run(run: dict[str, Any]) -> dict[str, Any]: return public_platform_video_import_run(run)

def extract_kuaishou_share_url(value: str) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 and c not in "\n\r\t" for c in value): return None
    urls = [m.group(0).rstrip(".,!?;:)]}〉》") for m in _URL.finditer(value)]
    return urls[0] if len(urls) == 1 and (urlparse(urls[0]).hostname or "").lower() in _DIRECT_HOSTS | _SHORT_HOSTS else None

def normalize_kuaishou_url(value: str) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value): return None
    parsed = urlparse(value.strip())
    try: port = parsed.port
    except ValueError: return None
    match = re.fullmatch(r"/short-video/([A-Za-z0-9_-]{6,128})/?", parsed.path)
    if parsed.scheme != "https" or parsed.username or parsed.password or port or (parsed.hostname or "").lower() not in _DIRECT_HOSTS or not match: return None
    return "https://www.kuaishou.com/short-video/" + match.group(1)

def _short_host(value: str) -> str | None:
    parsed = urlparse(value)
    try: port = parsed.port
    except ValueError: return None
    return (parsed.hostname or "").lower() if parsed.scheme == "https" and not parsed.username and not parsed.password and not port and (parsed.hostname or "").lower() in _SHORT_HOSTS else None

def _validated(value: str, resolver) -> str | None:
    host = (urlparse(value).hostname or "").lower()
    try: addresses = tuple(resolver(host))
    except OSError: raise KuaishouImportError("temporarily_unavailable") from None
    return value if addresses and all(_is_public_address(a) for a in addresses) else None

def _kuaishou_redirect_once(value: str, addresses: tuple[str, ...], *, opener_factory=build_opener) -> str | None:
    """One no-proxy, DNS-pinned HTTPS hop; never follows a redirect implicitly."""
    host = _short_host(value)
    if host is None or not addresses or any(not _is_public_address(item) for item in addresses): return None
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    request = Request(value, headers={"User-Agent": "AI-Video-Reverse-Engineer/1.0", "Range": "bytes=0-0"})
    try:
        with opener_factory(ProxyHandler({}), NoRedirect(), _PinnedHTTPSHandler(host, addresses)).open(request, timeout=15) as response:
            return response.headers.get("Location") if 300 <= response.status < 400 else None
    except HTTPError as error: return error.headers.get("Location") if 300 <= error.code < 400 else None

def resolve_kuaishou_short_url(value: str, *, redirect_once=None, dns_resolver=None, facts: list[dict[str, Any]] | None = None) -> str | None:
    current, resolver = value.strip() if isinstance(value, str) else "", dns_resolver or _public_addresses
    for hop in range(1, 4):
        direct = normalize_kuaishou_url(current)
        if direct: return _validated(direct, resolver)
        host = _short_host(current)
        if not host: return None
        try: addresses = tuple(resolver(host))
        except OSError: raise KuaishouImportError("temporarily_unavailable") from None
        if not addresses or any(not _is_public_address(a) for a in addresses): return None
        if facts is not None: facts.append({"hostHash": hashlib.sha256(host.encode()).hexdigest(), "hop": hop})
        try: location = (redirect_once or _kuaishou_redirect_once)(current, addresses)
        except OSError: raise KuaishouImportError("temporarily_unavailable") from None
        location = location[0] if isinstance(location, tuple) else location
        if not isinstance(location, str) or not location: return None
        current = urljoin(current, location)
    return _validated(normalize_kuaishou_url(current), resolver) if normalize_kuaishou_url(current) else None

def kuaishou_source_reference(value: str) -> str: return (value or "").split("?", 1)[0]
def kuaishou_source_facts(original: str, final: str, redirect_facts=None) -> dict[str, Any]:
    fact = lambda v: {"hostHash": hashlib.sha256((urlparse(v).hostname or "").lower().encode()).hexdigest(), "pathHash": hashlib.sha256(urlparse(v).path.encode()).hexdigest()}
    return {"original": fact(original), "final": fact(final), "redirects": list(redirect_facts or [])}

def kuaishou_video_source(*, probe, metadata, content_hash, confirmed_at, source_facts=None):
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash) or not confirmed_at.endswith("Z"): raise ValueError("快手来源记录无效")
    if not all(isinstance(metadata.get(key), (int, float)) and metadata[key] > 0 for key in ("duration", "width", "height")): raise ValueError("快手视频探测元数据无效")
    from .video_sources import utc_timestamp
    return {"schemaVersion": 1, "sourceId": "source-kuaishou-" + probe.video_id, "sourceType": "kuaishou", "originalReference": kuaishou_source_reference(probe.canonical_url), "adapter": dict(KUAISHOU_ADAPTER), "probe": {"durationSeconds": float(metadata["duration"]), "width": metadata["width"], "height": metadata["height"]}, "rightsConfirmation": {"confirmedAt": confirmed_at}, "contentHash": content_hash, "verifiedAt": utc_timestamp(), "status": "available", "metadata": {"title": probe.title[:255] or "快手视频", "uploader": (probe.uploader or "")[:255]}, "sourceFacts": source_facts or kuaishou_source_facts(probe.canonical_url, probe.canonical_url)}


def _classify_kuaishou_http_error(error: HTTPError) -> KuaishouImportError:
    if error.code in {401}:
        return KuaishouImportError("requires_login")
    if error.code in {404, 410, 451}:
        return KuaishouImportError("unsupported_link")
    return KuaishouImportError("temporarily_unavailable")


def _find_public_photo(value: Any, video_id: str) -> dict[str, Any] | None:
    pending = [value]
    while pending:
        candidate = pending.pop()
        if isinstance(candidate, dict):
            if candidate.get("photoId") == video_id and isinstance(candidate.get("photoUrl"), str):
                return candidate
            pending.extend(candidate.values())
        elif isinstance(candidate, list):
            pending.extend(candidate)
    return None


def parse_kuaishou_public_page(canonical: str, page: str) -> KuaishouProbe:
    """Parse only public JSON fields; a login/captcha shell is an error, never a bypass trigger."""
    if not isinstance(page, str) or len(page) > 20_000_000: raise KuaishouImportError("temporarily_unavailable")
    marker = re.search(r"window\.__APOLLO_STATE__\s*=\s*", page)
    try:
        state = json.JSONDecoder().raw_decode(page, marker.end())[0] if marker else json.loads(page)
    except (json.JSONDecodeError, TypeError):
        lower = page.lower()
        if any(word in lower for word in ("captcha", "验证码", "risk control", "风控", "too many requests", " 429 ")): raise KuaishouImportError("temporarily_unavailable")
        if any(word in lower for word in ("private", "私密", "drm", "geo-restricted", "直播", "live", "deleted", "已删除")): raise KuaishouImportError("unsupported_link")
        if any(word in lower for word in ("login", "登录", "sign in")): raise KuaishouImportError("requires_login")
        raise KuaishouImportError("temporarily_unavailable")
    video_id = canonical.rsplit("/", 1)[-1]
    values = _find_public_photo(state, video_id)
    if values is None: raise KuaishouImportError("unsupported_link")
    media = values["photoUrl"]
    parsed = urlparse(media)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.lower().endswith((".yximgs.com", ".kwaicdn.com")): raise KuaishouImportError("unsupported_link")
    raw_duration = values.get("duration")
    duration = float(raw_duration) / 1000 if isinstance(raw_duration, (int, float)) and not isinstance(raw_duration, bool) else None
    return KuaishouProbe(canonical, video_id, str(values.get("caption") or "快手视频"), duration, values.get("userName") if isinstance(values.get("userName"), str) else None, media_url=media)

def _pinned_get(url: str, addresses: tuple[str, ...], *, cookie: str | None = None):
    parsed = urlparse(url); host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or any(not _is_public_address(item) for item in addresses): raise KuaishouImportError("unsupported_link")
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    headers = {"User-Agent": "AI-Video-Reverse-Engineer/1.0"}
    if cookie: headers["Cookie"] = cookie
    return build_opener(ProxyHandler({}), NoRedirect(), _PinnedHTTPSHandler(host, addresses)).open(Request(url, headers=headers), timeout=15)

class SystemKuaishouCookieStore:
    def __init__(self, backend=None):
        if backend is None:
            try: import keyring; backend = keyring.get_keyring()
            except Exception: raise SecureStorageUnavailable("系统安全存储不可用。") from None
        if type(backend).__module__ not in _SAFE_KEYRING_MODULES: raise SecureStorageUnavailable("系统安全存储不可用。")
        self._backend = backend
    def get(self):
        try: return self._backend.get_password("ai-video-reverse-engineer.kuaishou-import", "kuaishou-cookiejar")
        except Exception: raise SecureStorageUnavailable("系统安全存储不可用。") from None
    def set(self, cookie):
        if not isinstance(cookie, str) or not cookie.strip() or len(cookie) > 1_000_000 or "\0" in cookie: raise ValueError("快手会话格式无效")
        try: self._backend.set_password("ai-video-reverse-engineer.kuaishou-import", "kuaishou-cookiejar", cookie)
        except Exception: raise SecureStorageUnavailable("系统安全存储不可用。") from None
    def delete(self):
        try: self._backend.delete_password("ai-video-reverse-engineer.kuaishou-import", "kuaishou-cookiejar")
        except Exception: raise SecureStorageUnavailable("系统安全存储不可用。") from None

class KuaishouSourceAdapter:
    """Public-page boundary. No Generic extractor, browser login, signing, proxy, or CAPTCHA flow."""
    version = KUAISHOU_ADAPTER["version"]
    is_controlled = False
    def __init__(self, *, page_reader=None, opener=None): self._page_reader, self._opener = page_reader, opener or _pinned_get
    def probe(self, url: str, *, cookie: str | None = None, cancelled=None) -> KuaishouProbe:
        canonical = normalize_kuaishou_url(url)
        if not canonical: raise KuaishouImportError("unsupported_link")
        if cancelled and cancelled(): raise YoutubeImportCancelled()
        try:
            addresses = tuple(_public_addresses("www.kuaishou.com"))
            if not addresses or any(not _is_public_address(a) for a in addresses): raise KuaishouImportError("temporarily_unavailable")
            if self._page_reader: page = self._page_reader(canonical, addresses, cookie)
            else:
                with self._opener(canonical, addresses, cookie=cookie) as response: page = response.read(20_000_001).decode("utf-8", "replace")
            return parse_kuaishou_public_page(canonical, page)
        except (KuaishouImportError, YoutubeImportCancelled): raise
        except HTTPError as error: raise _classify_kuaishou_http_error(error) from None
        except Exception: raise KuaishouImportError("temporarily_unavailable") from None
    def download(self, probe: KuaishouProbe, destination: Path, *, cookie: str | None = None, cancelled=None) -> Path:
        if cancelled and cancelled(): raise YoutubeImportCancelled()
        if not probe.media_url: raise KuaishouImportError("unsupported_link")
        host = (urlparse(probe.media_url).hostname or "").lower()
        if not host.endswith((".yximgs.com", ".kwaicdn.com")): raise KuaishouImportError("unsupported_link")
        try:
            addresses = tuple(_public_addresses(host))
            if not addresses or any(not _is_public_address(a) for a in addresses): raise KuaishouImportError("temporarily_unavailable")
            written = 0
            with self._opener(probe.media_url, addresses, cookie=None) as response, destination.open("wb") as output:
                length = response.headers.get("Content-Length")
                if length and (not length.isdigit() or int(length) > MAX_KUAISHOU_SOURCE_BYTES): raise KuaishouImportError("unsupported_link")
                while chunk := response.read(65536):
                    if cancelled and cancelled(): raise YoutubeImportCancelled()
                    written += len(chunk)
                    if written > MAX_KUAISHOU_SOURCE_BYTES: raise KuaishouImportError("unsupported_link")
                    output.write(chunk)
            if not written: raise KuaishouImportError("temporarily_unavailable")
            return destination
        except (KuaishouImportError, YoutubeImportCancelled): destination.unlink(missing_ok=True); raise
        except HTTPError as error: destination.unlink(missing_ok=True); raise _classify_kuaishou_http_error(error) from None
        except Exception: destination.unlink(missing_ok=True); raise KuaishouImportError("temporarily_unavailable") from None
