"""Constrained Douyin source adapter; it never bypasses platform controls."""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .analysis_service_secrets import SecureStorageUnavailable
from .platform_video_import import PlatformVideoImportRuns, public_platform_video_import_run
from .preproduction_nodes import FFMPEG_TIMEOUT_SECONDS, MAX_OUTPUT_DIMENSION, MAX_OUTPUT_PIXELS
from .youtube_import import (
    MAX_YOUTUBE_SOURCE_BYTES,
    MAX_YOUTUBE_SOURCE_DURATION_SECONDS,
    YoutubeImportCancelled,
    _SAFE_KEYRING_MODULES,
    _SilentYtDlpLogger,
)


YT_DLP_VERSION = "2026.08.19"
DOUYIN_ADAPTER = {"id": "yt-dlp", "version": YT_DLP_VERSION}
MAX_DOUYIN_SOURCE_DURATION_SECONDS = MAX_YOUTUBE_SOURCE_DURATION_SECONDS
MAX_DOUYIN_SOURCE_BYTES = MAX_YOUTUBE_SOURCE_BYTES
os.environ["YTDLP_NO_PLUGINS"] = "1"
DouyinStatus = Literal["available", "requires_login", "temporarily_unavailable", "unsupported_link"]
_DIRECT_HOSTS = frozenset({"douyin.com", "www.douyin.com"})
_SHORT_HOSTS = frozenset({"v.douyin.com"})
_URL_TOKEN = re.compile(r"https?://[^\s<>\"'，。！？；：、）】》]+", re.IGNORECASE)


class DouyinImportError(RuntimeError):
    def __init__(self, status: DouyinStatus, message: str = ""):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class DouyinProbe:
    canonical_url: str
    video_id: str
    title: str
    duration: float | None
    uploader: str | None
    status: DouyinStatus = "available"


class DouyinImportRuns(PlatformVideoImportRuns):
    def __init__(self, root: Path):
        super().__init__(root, "douyin")


def public_douyin_import_run(run: dict[str, Any]) -> dict[str, Any]:
    return public_platform_video_import_run(run)


def extract_douyin_share_url(value: str) -> str | None:
    """Return exactly one URL from normal share text; ambiguity is rejected."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        return None
    values = [match.group(0).rstrip(".,!?;:)]}〉》") for match in _URL_TOKEN.finditer(value)]
    if any((urlparse(item).hostname or "").lower() not in _DIRECT_HOSTS | _SHORT_HOSTS for item in values):
        return None
    return values[0] if len(values) == 1 else None


def normalize_douyin_url(value: str) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value):
        return None
    parsed = urlparse(value.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port or (parsed.hostname or "").lower() not in _DIRECT_HOSTS:
        return None
    match = re.fullmatch(r"/video/([0-9]+)/?", parsed.path)
    if match is None:
        return None
    return "https://www.douyin.com/video/" + match.group(1)


def resolve_douyin_short_url(value: str, *, redirect_once=None, dns_resolver=None, facts: list[dict[str, Any]] | None = None) -> str | None:
    """Resolve only pinned, HTTPS v.douyin hops and return a canonical video page."""
    current = value.strip() if isinstance(value, str) else ""
    if not current or len(current) > 4096 or any(ord(char) < 32 for char in current):
        return None
    resolver = dns_resolver or _public_addresses
    for hop in range(1, 4):
        direct = normalize_douyin_url(current)
        if direct is not None:
            return _validated_canonical(direct, resolver)
        host = _safe_short_host(current)
        if host is None:
            return None
        try:
            addresses = tuple(resolver(host))
        except OSError:
            raise DouyinImportError("temporarily_unavailable") from None
        if not addresses or any(not _is_public_address(address) for address in addresses):
            return None
        if facts is not None:
            facts.append({"hostHash": hashlib.sha256(host.encode("utf-8")).hexdigest(),
                          "tokenHash": _short_token_hash(current), "hop": hop})
        try:
            result = (redirect_once or _douyin_redirect_once)(current, addresses)
        except OSError:
            raise DouyinImportError("temporarily_unavailable") from None
        location = result[0] if isinstance(result, tuple) else result
        if not isinstance(location, str) or not location:
            return None
        current = urljoin(current, location)
    canonical = normalize_douyin_url(current)
    return _validated_canonical(canonical, resolver) if canonical is not None else None


def _validated_canonical(canonical: str, resolver) -> str | None:
    host = (urlparse(canonical).hostname or "").lower()
    try:
        addresses = tuple(resolver(host))
    except OSError:
        raise DouyinImportError("temporarily_unavailable") from None
    return canonical if addresses and all(_is_public_address(address) for address in addresses) else None


def _short_token_hash(value: str) -> str:
    parsed = urlparse(value)
    token = parsed.path.strip("/")
    return hashlib.sha256(("aivre:douyin-short-token:v1:" + token).encode("utf-8")).hexdigest()


def _safe_short_host(value: str) -> str | None:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or port or host not in _SHORT_HOSTS:
        return None
    return host


def _public_addresses(host: str) -> tuple[str, ...]:
    return tuple(sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}))


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global and not any((address.is_loopback, address.is_private, address.is_link_local,
                                          address.is_reserved, address.is_multicast, address.is_unspecified))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, addresses: tuple[str, ...], **kwargs):
        super().__init__(host, **kwargs)
        self._addresses = addresses

    def connect(self):
        last_error = None
        for address in self._addresses:
            try:
                raw = socket.create_connection((address, self.port), self.timeout, self.source_address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as error:
                last_error = error
        raise last_error or OSError("pinned DNS connection failed")


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, host: str, addresses: tuple[str, ...]):
        super().__init__(context=ssl.create_default_context())
        self._host, self._addresses = host, addresses

    def https_open(self, req):
        if (urlparse(req.full_url).hostname or "").lower() != self._host:
            raise OSError("redirect host changed before validation")
        return self.do_open(lambda target, **kwargs: _PinnedHTTPSConnection(target, self._addresses, **kwargs), req)


def _douyin_redirect_once(value: str, addresses: tuple[str, ...], *, opener_factory=build_opener) -> str | None:
    host = _safe_short_host(value)
    if host is None or not addresses or any(not _is_public_address(item) for item in addresses):
        return None

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None

    request = Request(value, headers={"User-Agent": "AI-Video-Reverse-Engineer/1.0", "Range": "bytes=0-0"})
    try:
        with opener_factory(ProxyHandler({}), _NoRedirect(), _PinnedHTTPSHandler(host, addresses)).open(request, timeout=15) as response:
            return response.headers.get("Location") if 300 <= response.status < 400 else None
    except HTTPError as error:
        return error.headers.get("Location") if 300 <= error.code < 400 else None


def douyin_video_source(*, probe: DouyinProbe, metadata: dict[str, Any], content_hash: str, confirmed_at: str, redirect_facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("抖音视频内容哈希无效")
    if not confirmed_at.endswith("Z"):
        raise ValueError("抖音视频权利确认时间无效")
    duration, width, height = metadata.get("duration"), metadata.get("width"), metadata.get("height")
    if not (isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0
            and isinstance(width, int) and not isinstance(width, bool) and width > 0
            and isinstance(height, int) and not isinstance(height, bool) and height > 0):
        raise ValueError("抖音视频探测元数据无效")
    from .video_sources import utc_timestamp
    return {
        "schemaVersion": 1, "sourceId": "source-douyin-" + probe.video_id, "sourceType": "douyin",
        "originalReference": probe.canonical_url, "adapter": dict(DOUYIN_ADAPTER),
        "probe": {"durationSeconds": float(duration), "width": width, "height": height},
        "rightsConfirmation": {"confirmedAt": confirmed_at}, "contentHash": content_hash,
        "verifiedAt": utc_timestamp(), "status": "available",
        "metadata": {"title": _display(probe.title, "抖音视频"), "uploader": _display(probe.uploader, "")},
        "redirectFacts": list(redirect_facts or []),
    }


def probe_downloaded_douyin_video(path: Path, *, ffprobe_path: str = "ffprobe", ffmpeg_path: str = "ffmpeg") -> dict[str, Any]:
    try:
        result = subprocess.run([ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=False)
        payload = json.loads(result.stdout)
        video = next(stream for stream in payload["streams"] if isinstance(stream, dict) and stream.get("codec_type") == "video" and (stream.get("disposition") or {}).get("attached_pic") != 1)
        next(stream for stream in payload["streams"] if isinstance(stream, dict) and stream.get("codec_type") == "audio")
        duration, width, height = float(payload["format"]["duration"]), int(video["width"]), int(video["height"])
    except (OSError, StopIteration, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        raise ValueError("douyin_download_invalid") from error
    if (result.returncode or not math.isfinite(duration) or not 0 < duration <= MAX_DOUYIN_SOURCE_DURATION_SECONDS
            or width <= 0 or height <= 0 or width > MAX_OUTPUT_DIMENSION or height > MAX_OUTPUT_DIMENSION or width * height > MAX_OUTPUT_PIXELS):
        raise ValueError("douyin_download_invalid")
    try:
        decoded = subprocess.run([ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-frames:v", "1", "-frames:a", "1", "-f", "null", "-"], capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("douyin_download_invalid") from error
    if decoded.returncode: raise ValueError("douyin_download_invalid")
    return {"duration": duration, "width": width, "height": height}


class SystemDouyinCookieStore:
    def __init__(self, backend=None):
        if backend is None:
            try:
                import keyring
                backend = keyring.get_keyring()
            except Exception:
                raise SecureStorageUnavailable("系统安全存储不可用。") from None
        if type(backend).__module__ not in _SAFE_KEYRING_MODULES:
            raise SecureStorageUnavailable("系统安全存储不可用。")
        self._backend = backend

    def get(self) -> str | None:
        try: return self._backend.get_password("ai-video-reverse-engineer.douyin-import", "douyin-cookiejar")
        except Exception: raise SecureStorageUnavailable("系统安全存储不可用。") from None

    def set(self, cookie: str) -> None:
        if not isinstance(cookie, str) or not cookie.strip() or len(cookie) > 1_000_000 or "\x00" in cookie:
            raise ValueError("抖音会话格式无效")
        try: self._backend.set_password("ai-video-reverse-engineer.douyin-import", "douyin-cookiejar", cookie)
        except Exception: raise SecureStorageUnavailable("系统安全存储不可用。") from None

    def delete(self) -> None:
        try: self._backend.delete_password("ai-video-reverse-engineer.douyin-import", "douyin-cookiejar")
        except Exception: raise SecureStorageUnavailable("系统安全存储不可用。") from None


class YtDlpDouyinAdapter:
    version = YT_DLP_VERSION
    is_controlled = False

    def __init__(self, *, ffmpeg_path: str = "ffmpeg"):
        self._ffmpeg_path = ffmpeg_path

    def probe(self, url: str, *, cookie: str | None = None, cancelled=None) -> DouyinProbe:
        canonical = normalize_douyin_url(url)
        if canonical is None: raise DouyinImportError("unsupported_link")
        if cancelled is not None and cancelled(): raise YoutubeImportCancelled()
        info = self._extract(canonical, download=False, cookie=cookie, cancelled=cancelled)
        if cancelled is not None and cancelled(): raise YoutubeImportCancelled()
        return self._probe_from_info(canonical, info)

    def download(self, probe: DouyinProbe, destination: Path, *, cookie: str | None = None, cancelled=None) -> Path:
        info = self._extract(probe.canonical_url, download=True, cookie=cookie, destination=destination, cancelled=cancelled)
        requested = Path(str(info.get("_filename") or destination))
        if requested.is_file(): return requested
        candidates = sorted(destination.parent.glob(destination.name + "*"), key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates and candidates[0].is_file(): return candidates[0]
        raise DouyinImportError("temporarily_unavailable")

    def _extract(self, url: str, *, download: bool, cookie: str | None, destination: Path | None = None, cancelled=None) -> dict[str, Any]:
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as error:
            raise DouyinImportError("temporarily_unavailable") from error
        options = _douyin_yt_dlp_options(download, ffmpeg_path=self._ffmpeg_path)
        if destination is not None: options["outtmpl"] = {"default": str(destination) + ".%(ext)s"}
        if cancelled is not None:
            def cancel_hook(progress):
                if cancelled(): raise YoutubeImportCancelled()
                if isinstance(progress.get("downloaded_bytes"), int) and progress["downloaded_bytes"] > MAX_DOUYIN_SOURCE_BYTES:
                    raise DouyinImportError("unsupported_link")
            options["progress_hooks"] = [cancel_hook]
        if cookie: options["cookiefile"] = io.StringIO(cookie)
        try:
            with YoutubeDL(options) as ydl:
                result = ydl.extract_info(url, download=download)
                if not isinstance(result, dict): raise DouyinImportError("temporarily_unavailable")
                if download: result["_filename"] = ydl.prepare_filename(result)
                return result
        except (DouyinImportError, YoutubeImportCancelled): raise
        except DownloadError as error: raise _classify_douyin_ytdlp_error(str(error)) from None
        except Exception: raise DouyinImportError("temporarily_unavailable") from None

    @staticmethod
    def _probe_from_info(canonical: str, info: dict[str, Any]) -> DouyinProbe:
        video_id, title, duration = info.get("id"), info.get("title"), info.get("duration")
        if (not isinstance(video_id, str) or not video_id.isdigit() or not isinstance(title, str)
                or not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0):
            raise DouyinImportError("temporarily_unavailable")
        if video_id != canonical.rsplit("/", 1)[-1] or info.get("is_live") is True or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise DouyinImportError("unsupported_link")
        return DouyinProbe(canonical, video_id, title, float(duration), info.get("uploader") if isinstance(info.get("uploader"), str) else None)


def _classify_douyin_ytdlp_error(message: str) -> DouyinImportError:
    value = message.lower()
    if any(token in value for token in ("fresh cookies", "login", "sign in", "需要登录", "验证码", "verify")):
        return DouyinImportError("requires_login")
    if any(token in value for token in ("unsupported url", "no suitable extractor", "drm", "geo restricted", "geo-restricted", "地区", "private", "not available", "restricted")):
        return DouyinImportError("unsupported_link")
    return DouyinImportError("temporarily_unavailable")


def _douyin_yt_dlp_options(download: bool, *, ffmpeg_path: str = "ffmpeg") -> dict[str, Any]:
    return {
        "quiet": True, "no_warnings": True, "noplaylist": True, "extract_flat": False, "skip_download": not download,
        "overwrites": False, "logger": _SilentYtDlpLogger(), "format": "bv*+ba/b", "merge_output_format": "mp4", "ffmpeg_location": ffmpeg_path,
        "geo_bypass": False, "geo_bypass_country": None, "geo_bypass_ip_block": None, "proxy": "", "geo_verification_proxy": None,
        "enable_file_urls": False, "plugin_dirs": [], "remote_components": set(), "js_runtimes": {}, "cachedir": False,
        "username": None, "password": None, "usenetrc": False, "videopassword": None, "allow_unplayable_formats": False,
        "writeinfojson": False, "writethumbnail": False, "writesubtitles": False, "writeautomaticsub": False,
        "allowed_extractors": ["douyin"], "socket_timeout": 15, "retries": 1, "fragment_retries": 1, "extractor_retries": 1,
        "max_filesize": MAX_DOUYIN_SOURCE_BYTES,
    }


def _display(value: str | None, fallback: str) -> str:
    return value.strip()[:255] if isinstance(value, str) and value.strip() else fallback
