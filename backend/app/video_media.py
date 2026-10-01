"""Validate downloaded video results with the project's existing media limits."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from .preproduction_nodes import (
    FFMPEG_TIMEOUT_SECONDS,
    MAX_MEDIA_DURATION_SECONDS,
    MAX_OUTPUT_DIMENSION,
    MAX_OUTPUT_PIXELS,
)


def probe_video(path: Path, *, ffprobe_path: str = "ffprobe", ffmpeg_path: str = "ffmpeg") -> dict[str, Any]:
    """Probe metadata and decode one real video frame before asset installation.

    This is the generation-service form of ``preproduction_api._probe_media``:
    it keeps the same project limits and decode check without depending on an API
    module or accepting audio-only media.
    """
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        media_format = payload["format"]
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        raise ValueError("generation_result_invalid") from error
    if result.returncode or not isinstance(streams, list) or not isinstance(media_format, dict):
        raise ValueError("generation_result_invalid")
    video = next((stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"
                  and (stream.get("disposition") or {}).get("attached_pic") != 1), None)
    try:
        duration = float(media_format["duration"])
        width, height = int(video["width"]), int(video["height"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("generation_result_invalid") from None
    if (
        video is None
        or not math.isfinite(duration)
        or not 0 < duration <= MAX_MEDIA_DURATION_SECONDS
        or width <= 0
        or height <= 0
        or width > MAX_OUTPUT_DIMENSION
        or height > MAX_OUTPUT_DIMENSION
        or width * height > MAX_OUTPUT_PIXELS
    ):
        raise ValueError("generation_result_invalid")
    try:
        decoded = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
             "-map", "0:v:0", "-frames:v", "1", "-an", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("generation_result_invalid") from error
    if decoded.returncode:
        raise ValueError("generation_result_invalid")
    return {"duration": duration, "width": width, "height": height}
