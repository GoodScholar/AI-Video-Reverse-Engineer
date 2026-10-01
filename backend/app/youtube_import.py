"""Constrained YouTube source adapter; it never reads browser profiles or logs sessions."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import subprocess
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .analysis_service_secrets import SecureStorageUnavailable
from .preproduction_nodes import FFMPEG_TIMEOUT_SECONDS, MAX_OUTPUT_DIMENSION, MAX_OUTPUT_PIXELS
from .preproduction import safe_child
from .durable_runs import freeze_run_input


YT_DLP_VERSION = "2026.08.19"
YOUTUBE_ADAPTER = {"id": "yt-dlp", "version": YT_DLP_VERSION}
MAX_YOUTUBE_SOURCE_DURATION_SECONDS = 12 * 60 * 60
MAX_YOUTUBE_SOURCE_BYTES = 200_000_000
# This happens before the deferred yt-dlp import below.  Do not allow ambient
# user/system/PYTHONPATH plugins to run in a process that may receive a session.
os.environ["YTDLP_NO_PLUGINS"] = "1"
YoutubeStatus = Literal["available", "requires_login", "temporarily_unavailable", "unsupported_link"]
_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
_SAFE_KEYRING_MODULES = frozenset({
    "keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService", "keyring.backends.libsecret",
})


class YoutubeImportError(RuntimeError):
    def __init__(self, status: YoutubeStatus, message: str = ""):
        super().__init__(message)
        self.status = status


class YoutubeImportCancelled(RuntimeError):
    """Raised by a progress hook or controlled adapter after an acknowledged cancellation."""


@dataclass(frozen=True)
class YoutubeProbe:
    canonical_url: str
    video_id: str
    title: str
    duration: float | None
    uploader: str | None
    status: YoutubeStatus = "available"


class YoutubeImportRuns:
    """Atomic, project-scoped records for local YouTube import jobs."""

    _ACTIVE = frozenset({"queued", "running", "cancel_requested"})

    def __init__(self, root: Path):
        self._root = Path(root)
        self._lock = RLock()

    def create_or_reuse(self, project_id: str, snapshot: dict[str, Any], idempotency_key: str) -> tuple[dict[str, Any], bool]:
        frozen = freeze_run_input(snapshot)
        with self._lock:
            state = self._load(project_id)
            existing = next((run for run in state["runs"] if run["idempotencyKey"] == idempotency_key
                             and run["inputFingerprint"] == frozen.fingerprint), None)
            if existing is None:
                existing = next((run for run in state["runs"] if run["inputFingerprint"] == frozen.fingerprint
                                 and run["status"] in self._ACTIVE | {"completed"}), None)
            if existing is not None:
                return dict(existing), False
            run = self._new(snapshot, frozen.fingerprint, idempotency_key, retry_of=None)
            state["runs"].append(run); self._save(project_id, state)
            return dict(run), True

    def retry(self, project_id: str, run_id: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            state = self._load(project_id); previous = self._find(state, run_id)
            if previous["status"] not in {"failed", "cancelled"}:
                raise ValueError("该导入运行当前不能重试")
            # Retrying is itself idempotent: a second request for the same
            # failed parent must attach to the active/completed child rather
            # than queueing a second network download.
            existing = next((run for run in state["runs"] if run.get("retryOf") == run_id
                             and run.get("status") in self._ACTIVE | {"completed"}), None)
            if existing is not None:
                return dict(existing), False
            frozen = freeze_run_input(previous["input"])
            run = self._new(previous["input"], frozen.fingerprint, uuid4().hex, retry_of=run_id)
            state["runs"].append(run); self._save(project_id, state)
            return dict(run), True

    def get(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._find(self._load(project_id), run_id))

    def list(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(run) for run in reversed(self._load(project_id)["runs"])]

    def transition(self, project_id: str, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = self._load(project_id); run = self._find(state, run_id); run.update(changes); self._save(project_id, state)
            return dict(run)

    def request_cancel(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load(project_id); run = self._find(state, run_id)
            if run["status"] == "queued":
                run.update(status="cancelled", error="cancelled")
            elif run["status"] == "running":
                run.update(status="cancel_requested", error=None)
            elif run["status"] not in {"cancel_requested", "cancelled"}:
                raise ValueError("该导入运行当前不能取消")
            self._save(project_id, state); return dict(run)

    def recover(self, project_id: str) -> None:
        with self._lock:
            state = self._load(project_id); changed = False
            for run in state["runs"]:
                if run["status"] in {"queued", "running"}:
                    run.update(status="failed", error="interrupted"); changed = True
                elif run["status"] == "cancel_requested":
                    run.update(status="cancelled", error="cancelled"); changed = True
            if changed: self._save(project_id, state)
        for run in state["runs"]:
            if run["status"] in {"failed", "cancelled"}:
                shutil.rmtree(self.staging_dir(project_id, run["id"]), ignore_errors=True)

    def staging_dir(self, project_id: str, run_id: str) -> Path:
        return safe_child(self._root, "project-files", project_id, "preproduction", "youtube-import", "staging", run_id)

    def _path(self, project_id: str) -> Path:
        return safe_child(self._root, "project-files", project_id, "preproduction", "youtube-import", "runs.json")

    def _load(self, project_id: str) -> dict[str, Any]:
        path = self._path(project_id)
        if not path.exists(): return {"version": 1, "runs": []}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("version") != 1 or not isinstance(value.get("runs"), list): raise ValueError
            for run in value["runs"]:
                if not isinstance(run, dict) or not isinstance(run.get("id"), str) or not isinstance(run.get("status"), str): raise ValueError
            return value
        except (OSError, ValueError, json.JSONDecodeError, AttributeError) as error:
            raise OSError("YouTube 导入运行记录无效") from error

    def _save(self, project_id: str, state: dict[str, Any]) -> None:
        path = self._path(project_id); path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".youtube-runs-", suffix=".part"); temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(state, target, ensure_ascii=False, separators=(",", ":"), allow_nan=False); target.flush(); os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    @staticmethod
    def _find(state: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = next((item for item in state["runs"] if item.get("id") == run_id), None)
        if run is None: raise KeyError("YouTube 导入运行不存在")
        return run

    @staticmethod
    def _new(snapshot: dict[str, Any], fingerprint: str, idempotency_key: str, retry_of: str | None) -> dict[str, Any]:
        from .video_sources import utc_timestamp
        return {"id": "youtube-import-" + uuid4().hex, "status": "queued", "error": None,
                "createdAt": utc_timestamp(), "input": snapshot, "inputFingerprint": fingerprint,
                "idempotencyKey": idempotency_key, "retryOf": retry_of, "uploadedAssetId": None}


def public_youtube_import_run(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key != "input"}


def normalize_youtube_url(value: str) -> str | None:
    """Accept normal YouTube pages/shares only; never follow arbitrary redirectors."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value):
        return None
    parsed = urlparse(value.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    # A normal share is canonicalized to HTTPS.  Do not allow an HTTP URL to
    # trigger even the first metadata request (or any redirect handling).
    if parsed.scheme != "https" or parsed.username or parsed.password or port:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None
    video_id = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path == "/watch":
        video_id = (parse_qs(parsed.query).get("v") or [None])[0]
    elif parsed.path.startswith("/shorts/"):
        video_id = parsed.path.split("/", 3)[2]
    if not isinstance(video_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is None:
        return None
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_video_source(*, probe: YoutubeProbe, metadata: dict[str, Any], content_hash: str, confirmed_at: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("YouTube 视频内容哈希无效")
    if not confirmed_at.endswith("Z"):
        raise ValueError("YouTube 视频权利确认时间无效")
    duration, width, height = metadata.get("duration"), metadata.get("width"), metadata.get("height")
    if not (isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0
            and isinstance(width, int) and not isinstance(width, bool) and width > 0
            and isinstance(height, int) and not isinstance(height, bool) and height > 0):
        raise ValueError("YouTube 视频探测元数据无效")
    from .video_sources import utc_timestamp
    return {
        "schemaVersion": 1,
        "sourceId": "source-youtube-" + probe.video_id,
        "sourceType": "youtube",
        "originalReference": probe.canonical_url,
        "adapter": dict(YOUTUBE_ADAPTER),
        "probe": {"durationSeconds": float(duration), "width": width, "height": height},
        "rightsConfirmation": {"confirmedAt": confirmed_at},
        "contentHash": content_hash,
        "verifiedAt": utc_timestamp(),
        "status": "available",
        "metadata": {"title": _display(probe.title, "YouTube 视频"), "uploader": _display(probe.uploader, "")},
    }


def probe_downloaded_youtube_video(path: Path, *, ffprobe_path: str = "ffprobe", ffmpeg_path: str = "ffmpeg") -> dict[str, Any]:
    """Probe/decode an imported source without applying the 300-second generation limit."""
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        payload = json.loads(result.stdout)
        video = next(stream for stream in payload["streams"] if isinstance(stream, dict)
                     and stream.get("codec_type") == "video" and (stream.get("disposition") or {}).get("attached_pic") != 1)
        duration, width, height = float(payload["format"]["duration"]), int(video["width"]), int(video["height"])
    except (OSError, StopIteration, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        raise ValueError("youtube_download_invalid") from error
    if (result.returncode or not math.isfinite(duration) or not 0 < duration <= MAX_YOUTUBE_SOURCE_DURATION_SECONDS
            or width <= 0 or height <= 0 or width > MAX_OUTPUT_DIMENSION or height > MAX_OUTPUT_DIMENSION
            or width * height > MAX_OUTPUT_PIXELS):
        raise ValueError("youtube_download_invalid")
    try:
        decoded = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("youtube_download_invalid") from error
    if decoded.returncode:
        raise ValueError("youtube_download_invalid")
    return {"duration": duration, "width": width, "height": height}


class SystemYoutubeCookieStore:
    """Read a Netscape-cookie payload from OS storage only; it is never written by this service."""

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
            return self._backend.get_password("ai-video-reverse-engineer.youtube-import", "youtube-cookiejar")
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用。") from None


class YtDlpYoutubeAdapter:
    """Pinned embedded yt-dlp adapter, deliberately without browser-cookie extraction."""

    version = YT_DLP_VERSION
    is_controlled = False

    def probe(self, url: str, *, cookie: str | None = None, cancelled=None) -> YoutubeProbe:
        if cancelled is not None and cancelled():
            raise YoutubeImportCancelled()
        info = self._extract(url, download=False, cookie=cookie)
        if cancelled is not None and cancelled():
            raise YoutubeImportCancelled()
        return self._probe_from_info(url, info)

    def download(self, probe: YoutubeProbe, destination: Path, *, cookie: str | None = None, cancelled=None) -> Path:
        info = self._extract(probe.canonical_url, download=True, cookie=cookie, destination=destination, cancelled=cancelled)
        requested = Path(str(info.get("_filename") or destination))
        if requested.is_file():
            return requested
        candidates = sorted(destination.parent.glob(destination.name + "*"), key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates and candidates[0].is_file():
            return candidates[0]
        raise YoutubeImportError("temporarily_unavailable")

    def _extract(self, url: str, *, download: bool, cookie: str | None, destination: Path | None = None, cancelled=None) -> dict[str, Any]:
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as error:
            raise YoutubeImportError("temporarily_unavailable") from error
        options = _yt_dlp_options(download)
        if destination is not None:
            options["outtmpl"] = {"default": str(destination) + ".%(ext)s"}
        if cancelled is not None:
            def cancel_hook(_progress):
                if cancelled(): raise YoutubeImportCancelled()
                downloaded = _progress.get("downloaded_bytes")
                if isinstance(downloaded, int) and downloaded > MAX_YOUTUBE_SOURCE_BYTES:
                    raise YoutubeImportError("unsupported_link")
            options["progress_hooks"] = [cancel_hook]
        # The fixed source supports in-memory text streams as cookiefile.  Do not
        # pass cookiesfrombrowser or credentials, and never persist the stream.
        if cookie:
            options["cookiefile"] = io.StringIO(cookie)
        try:
            with YoutubeDL(options) as ydl:
                result = ydl.extract_info(url, download=download)
                if not isinstance(result, dict):
                    raise YoutubeImportError("temporarily_unavailable")
                if download:
                    result["_filename"] = ydl.prepare_filename(result)
                return result
        except YoutubeImportError:
            raise
        except YoutubeImportCancelled:
            raise
        except DownloadError as error:
            raise _classify_ytdlp_error(str(error)) from None
        except Exception:
            raise YoutubeImportError("temporarily_unavailable") from None

    @staticmethod
    def _probe_from_info(url: str, info: dict[str, Any]) -> YoutubeProbe:
        video_id, title = info.get("id"), info.get("title")
        if not isinstance(video_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is None or not isinstance(title, str):
            raise YoutubeImportError("temporarily_unavailable")
        canonical = normalize_youtube_url(url)
        expected_id = (parse_qs(urlparse(canonical).query).get("v") or [None])[0] if canonical else None
        # yt-dlp may follow a redirect internally.  Its discovered identity must
        # remain the exact public video the user reviewed before this adapter can
        # continue to download it.
        if video_id != expected_id:
            raise YoutubeImportError("unsupported_link")
        if info.get("is_live") is True or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise YoutubeImportError("unsupported_link")
        duration = info.get("duration")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
            raise YoutubeImportError("temporarily_unavailable")
        return YoutubeProbe(
            canonical_url=canonical or url, video_id=video_id, title=title,
            duration=float(duration),
            uploader=info.get("uploader") if isinstance(info.get("uploader"), str) else None,
        )


class _SilentYtDlpLogger:
    def debug(self, _message: str) -> None: pass
    def warning(self, _message: str) -> None: pass
    def error(self, _message: str) -> None: pass


def _classify_ytdlp_error(message: str) -> YoutubeImportError:
    value = message.lower()
    if any(token in value for token in ("sign in", "login", "private video", "members-only", "age-restricted")):
        return YoutubeImportError("requires_login")
    if any(token in value for token in (
        "unsupported url", "no suitable extractor", "drm", "not available in your country",
        "geo restricted", "geographic restriction", "this video is unavailable",
    )):
        return YoutubeImportError("unsupported_link")
    return YoutubeImportError("temporarily_unavailable")


def _yt_dlp_options(download: bool) -> dict[str, Any]:
    """Options for the embedded, public-YouTube-only adapter.

    Keep this as data rather than inheriting ambient command-line/config state:
    there is no browser-cookie extraction, account login, proxy/geo bypass,
    plugin loading, file URL access, or remote component download path here.
    """
    return {
        "quiet": True, "no_warnings": True, "noplaylist": True, "extract_flat": False,
        "skip_download": not download, "overwrites": False, "logger": _SilentYtDlpLogger(),
        "format": "bv*+ba/b", "merge_output_format": "mp4",
        "geo_bypass": False, "geo_bypass_country": None, "geo_bypass_ip_block": None,
        "proxy": "", "geo_verification_proxy": None, "enable_file_urls": False,
        "plugin_dirs": [], "remote_components": set(), "cachedir": False,
        "username": None, "password": None, "usenetrc": False, "videopassword": None,
        "allow_unplayable_formats": False, "writeinfojson": False, "writethumbnail": False,
        "writesubtitles": False, "writeautomaticsub": False,
        "allowed_extractors": ["youtube"], "socket_timeout": 15,
        "retries": 1, "fragment_retries": 1, "extractor_retries": 1,
        "max_filesize": MAX_YOUTUBE_SOURCE_BYTES,
    }


def _display(value: str | None, fallback: str) -> str:
    return value.strip()[:255] if isinstance(value, str) and value.strip() else fallback
