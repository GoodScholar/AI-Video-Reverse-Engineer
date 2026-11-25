"""Constrained Bilibili source adapter; no browser profiles, proxy, or bypass paths."""
from __future__ import annotations

import io
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

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
BILIBILI_ADAPTER = {"id": "yt-dlp", "version": YT_DLP_VERSION}
MAX_BILIBILI_SOURCE_DURATION_SECONDS = MAX_YOUTUBE_SOURCE_DURATION_SECONDS
MAX_BILIBILI_SOURCE_BYTES = MAX_YOUTUBE_SOURCE_BYTES
os.environ["YTDLP_NO_PLUGINS"] = "1"
BilibiliStatus = Literal["available", "requires_login", "temporarily_unavailable", "unsupported_link"]
_BILIBILI_HOSTS = frozenset({"www.bilibili.com", "bilibili.com"})
_BVID = re.compile(r"BV[0-9A-Za-z]{10}", re.IGNORECASE)


class BilibiliImportError(RuntimeError):
    def __init__(self, status: BilibiliStatus, message: str = ""):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BilibiliPart:
    number: int
    cid: str
    title: str
    duration: float | None


@dataclass(frozen=True)
class BilibiliProbe:
    canonical_url: str
    video_id: str
    title: str
    uploader: str | None
    parts: tuple[BilibiliPart, ...]
    status: BilibiliStatus = "available"


class BilibiliImportRuns(PlatformVideoImportRuns):
    def __init__(self, root: Path):
        super().__init__(root, "bilibili")


def public_bilibili_import_run(run: dict[str, Any]) -> dict[str, Any]:
    return public_platform_video_import_run(run)


def normalize_bilibili_url(value: str) -> str | None:
    """Accept direct public Bilibili video pages only; never follow short-link redirects."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value):
        return None
    parsed = urlparse(value.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port or (parsed.hostname or "").lower() not in _BILIBILI_HOSTS:
        return None
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path, re.IGNORECASE)
    if match is None:
        return None
    part_values = parse_qs(parsed.query).get("p", [])
    if len(part_values) > 1 or (part_values and (not part_values[0].isdigit() or int(part_values[0]) < 1)):
        return None
    canonical = "https://www.bilibili.com/video/" + match.group(1)
    return canonical if not part_values else canonical + "?" + urlencode({"p": int(part_values[0])})


def bilibili_url_part(value: str) -> int | None:
    """Return an explicitly selected part from a direct canonical input only."""
    canonical = normalize_bilibili_url(value)
    if canonical is None:
        return None
    parts = parse_qs(urlparse(canonical).query).get("p", [])
    return int(parts[0]) if parts else None


def resolve_bilibili_short_url(value: str, *, redirect_once=None) -> str | None:
    """Resolve at most three HTTPS b23 hops and accept only the resulting BV page."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value): return None
    current = value.strip()
    for _ in range(3):
        direct = normalize_bilibili_url(current)
        if direct is not None: return direct
        parsed = urlparse(current)
        try: port = parsed.port
        except ValueError: return None
        if (parsed.scheme != "https" or parsed.username or parsed.password or port
                or (parsed.hostname or "").lower() not in {"b23.tv", "www.b23.tv"}): return None
        try: location = (redirect_once or _b23_redirect_once)(current)
        except OSError: raise BilibiliImportError("temporarily_unavailable") from None
        if not isinstance(location, str) or not location: return None
        current = urljoin(current, location)
    return normalize_bilibili_url(current)


def _b23_redirect_once(value: str, *, opener_factory=build_opener) -> str | None:
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    request = Request(value, headers={"User-Agent": "AI-Video-Reverse-Engineer/1.0", "Range": "bytes=0-0"})
    try:
        with opener_factory(ProxyHandler({}), _NoRedirect).open(request, timeout=15) as response:
            return response.headers.get("Location") if 300 <= response.status < 400 else None
    except HTTPError as error:
        return error.headers.get("Location") if 300 <= error.code < 400 else None


def bilibili_video_source(*, probe: BilibiliProbe, part: BilibiliPart, metadata: dict[str, Any], content_hash: str, confirmed_at: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("B站视频内容哈希无效")
    if not confirmed_at.endswith("Z"):
        raise ValueError("B站视频权利确认时间无效")
    duration, width, height = metadata.get("duration"), metadata.get("width"), metadata.get("height")
    if not (isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0
            and isinstance(width, int) and not isinstance(width, bool) and width > 0
            and isinstance(height, int) and not isinstance(height, bool) and height > 0):
        raise ValueError("B站视频探测元数据无效")
    from .video_sources import utc_timestamp
    return {
        "schemaVersion": 1,
        "sourceId": f"source-bilibili-{probe.video_id}-p{part.number}",
        "sourceType": "bilibili",
        "originalReference": f"https://www.bilibili.com/video/{probe.video_id}?p={part.number}",
        "adapter": dict(BILIBILI_ADAPTER),
        "probe": {"durationSeconds": float(duration), "width": width, "height": height},
        "rightsConfirmation": {"confirmedAt": confirmed_at},
        "contentHash": content_hash,
        "verifiedAt": utc_timestamp(),
        "status": "available",
        "metadata": {"title": _display(part.title or probe.title, "B站视频"), "uploader": _display(probe.uploader, "")},
    }


def probe_downloaded_bilibili_video(path: Path, *, ffprobe_path: str = "ffprobe", ffmpeg_path: str = "ffmpeg") -> dict[str, Any]:
    """Require the merged MP4 to contain decodable video and audio before installation."""
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        video = next(stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"
                     and (stream.get("disposition") or {}).get("attached_pic") != 1)
        next(stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio")
        duration, width, height = float(payload["format"]["duration"]), int(video["width"]), int(video["height"])
    except (OSError, StopIteration, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        raise ValueError("bilibili_download_invalid") from error
    if (result.returncode or not math.isfinite(duration) or not 0 < duration <= MAX_BILIBILI_SOURCE_DURATION_SECONDS
            or width <= 0 or height <= 0 or width > MAX_OUTPUT_DIMENSION or height > MAX_OUTPUT_DIMENSION
            or width * height > MAX_OUTPUT_PIXELS):
        raise ValueError("bilibili_download_invalid")
    try:
        decoded = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-frames:v", "1", "-frames:a", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("bilibili_download_invalid") from error
    if decoded.returncode: raise ValueError("bilibili_download_invalid")
    return {"duration": duration, "width": width, "height": height}


class SystemBilibiliCookieStore:
    """Read a Bilibili Netscape cookie payload only from the OS secure store."""

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
        try:
            return self._backend.get_password("ai-video-reverse-engineer.bilibili-import", "bilibili-cookiejar")
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用。") from None

    def set(self, cookie: str) -> None:
        if not isinstance(cookie, str) or not cookie.strip() or len(cookie) > 1_000_000 or "\x00" in cookie:
            raise ValueError("B站会话格式无效")
        try:
            self._backend.set_password("ai-video-reverse-engineer.bilibili-import", "bilibili-cookiejar", cookie)
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用。") from None

    def delete(self) -> None:
        try:
            self._backend.delete_password("ai-video-reverse-engineer.bilibili-import", "bilibili-cookiejar")
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用。") from None


class YtDlpBilibiliAdapter:
    version = YT_DLP_VERSION
    is_controlled = False

    def __init__(self, *, ffmpeg_path: str = "ffmpeg"):
        self._ffmpeg_path = ffmpeg_path

    def probe(self, url: str, *, cookie: str | None = None, cancelled=None) -> BilibiliProbe:
        canonical = normalize_bilibili_url(url)
        if canonical is None: raise BilibiliImportError("unsupported_link")
        if cancelled is not None and cancelled(): raise YoutubeImportCancelled()
        # BiliBiliIE deliberately returns a playlist for an anthology without p.
        info = self._extract(canonical.split("?", 1)[0], download=False, cookie=cookie, playlist=True, cancelled=cancelled)
        if cancelled is not None and cancelled(): raise YoutubeImportCancelled()
        return self._probe_from_info(canonical, info)

    def download(self, probe: BilibiliProbe, part: BilibiliPart, destination: Path, *, cookie: str | None = None, cancelled=None) -> Path:
        url = f"https://www.bilibili.com/video/{probe.video_id}?p={part.number}"
        info = self._extract(url, download=True, cookie=cookie, destination=destination, playlist=False, cancelled=cancelled)
        requested = Path(str(info.get("_filename") or destination))
        if requested.is_file(): return requested
        candidates = sorted(destination.parent.glob(destination.name + "*"), key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates and candidates[0].is_file(): return candidates[0]
        raise BilibiliImportError("temporarily_unavailable")

    def _extract(self, url: str, *, download: bool, cookie: str | None, playlist: bool, destination: Path | None = None, cancelled=None) -> dict[str, Any]:
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as error:
            raise BilibiliImportError("temporarily_unavailable") from error
        options = _bilibili_yt_dlp_options(download, playlist=playlist, ffmpeg_path=self._ffmpeg_path)
        if destination is not None: options["outtmpl"] = {"default": str(destination) + ".%(ext)s"}
        if cancelled is not None:
            def cancel_hook(progress):
                if cancelled(): raise YoutubeImportCancelled()
                if isinstance(progress.get("downloaded_bytes"), int) and progress["downloaded_bytes"] > MAX_BILIBILI_SOURCE_BYTES:
                    raise BilibiliImportError("unsupported_link")
            options["progress_hooks"] = [cancel_hook]
        if cookie: options["cookiefile"] = io.StringIO(cookie)
        try:
            with YoutubeDL(options) as ydl:
                result = ydl.extract_info(url, download=download)
                if not isinstance(result, dict): raise BilibiliImportError("temporarily_unavailable")
                if download: result["_filename"] = ydl.prepare_filename(result)
                return result
        except (BilibiliImportError, YoutubeImportCancelled):
            raise
        except DownloadError as error:
            raise _classify_bilibili_ytdlp_error(str(error)) from None
        except Exception:
            raise BilibiliImportError("temporarily_unavailable") from None

    @staticmethod
    def _probe_from_info(canonical: str, info: dict[str, Any]) -> BilibiliProbe:
        video_id = _bvid_from_url(canonical)
        if video_id is None: raise BilibiliImportError("unsupported_link")
        if info.get("is_live") is True or info.get("live_status") in {"is_live", "is_upcoming"} or info.get("_type") == "multi_video":
            raise BilibiliImportError("unsupported_link")
        title = info.get("title") if isinstance(info.get("title"), str) else None
        entries = info.get("entries")
        raw_parts = list(entries) if isinstance(entries, list) else [info]
        parts: list[BilibiliPart] = []
        for number, entry in enumerate(raw_parts, 1):
            if not isinstance(entry, dict): continue
            duration = entry.get("duration")
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
                raise BilibiliImportError("temporarily_unavailable")
            part_title = entry.get("title") if isinstance(entry.get("title"), str) else title
            part_id = entry.get("id")
            if not isinstance(part_id, str) or not isinstance(part_title, str): raise BilibiliImportError("temporarily_unavailable")
            # The audited extractor exposes interactive paths with numeric cid
            # entry IDs; they are branches, not normal anthology P items.
            if re.search(r"_\d+$", part_id): raise BilibiliImportError("unsupported_link")
            parts.append(BilibiliPart(number=number, cid=part_id, title=part_title, duration=float(duration)))
        if not parts: raise BilibiliImportError("temporarily_unavailable")
        selected = parse_qs(urlparse(canonical).query).get("p", [])
        if selected:
            index = int(selected[0]) - 1
            if index >= len(parts): raise BilibiliImportError("unsupported_link")
            parts = [parts[index]]
        return BilibiliProbe(canonical_url=canonical.split("?", 1)[0], video_id=video_id, title=title or parts[0].title,
                             uploader=info.get("uploader") if isinstance(info.get("uploader"), str) else None, parts=tuple(parts))


def _bvid_from_url(value: str) -> str | None:
    match = re.search(r"/video/(BV[0-9A-Za-z]{10})", value, re.IGNORECASE)
    return match.group(1) if match else None


def _classify_bilibili_ytdlp_error(message: str) -> BilibiliImportError:
    value = message.lower()
    if any(token in value for token in ("login", "sign in", "大会员", "会员", "private", "需要登录")):
        return BilibiliImportError("requires_login")
    if any(token in value for token in (
        "unsupported url", "no suitable extractor", "drm", "geo restricted", "geo-restricted", "地区", "not available",
        "premium members only", "supporter-only",
    )):
        return BilibiliImportError("unsupported_link")
    return BilibiliImportError("temporarily_unavailable")


def _bilibili_yt_dlp_options(download: bool, *, playlist: bool, ffmpeg_path: str = "ffmpeg") -> dict[str, Any]:
    return {
        "quiet": True, "no_warnings": True, "noplaylist": not playlist, "extract_flat": False,
        "skip_download": not download, "overwrites": False, "logger": _SilentYtDlpLogger(),
        "format": "bv*+ba/b", "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_path,
        "geo_bypass": False, "geo_bypass_country": None, "geo_bypass_ip_block": None,
        "proxy": "", "geo_verification_proxy": None, "enable_file_urls": False,
        "plugin_dirs": [], "remote_components": set(), "cachedir": False,
        "username": None, "password": None, "usenetrc": False, "videopassword": None,
        "allow_unplayable_formats": False, "writeinfojson": False, "writethumbnail": False,
        "writesubtitles": False, "writeautomaticsub": False, "allowed_extractors": ["bilibili"],
        "socket_timeout": 15, "retries": 1, "fragment_retries": 1, "extractor_retries": 1,
        "max_filesize": MAX_BILIBILI_SOURCE_BYTES,
    }


def _display(value: str | None, fallback: str) -> str:
    return value.strip()[:255] if isinstance(value, str) and value.strip() else fallback
