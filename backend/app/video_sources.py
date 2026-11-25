"""Shared, durable provenance contract for videos imported into a project."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping


VideoSourceStatus = Literal[
    "available", "requires_login", "temporarily_unavailable", "unsupported_link",
]
VIDEO_SOURCE_STATUSES = frozenset({
    "available", "requires_login", "temporarily_unavailable", "unsupported_link",
})
LOCAL_VIDEO_ADAPTER = {"id": "managed-local-upload", "version": "1"}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_video_source(*, original_name: str, metadata: Mapping[str, Any], content_hash: str, confirmed_at: str) -> dict[str, Any]:
    """Build the portable source record without retaining the user's local path."""
    if not isinstance(content_hash, str) or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
        raise ValueError("视频来源内容哈希无效")
    if not isinstance(confirmed_at, str) or not confirmed_at.endswith("Z"):
        raise ValueError("视频来源权利确认时间无效")
    name = _display_name(original_name)
    duration, width, height = metadata.get("duration"), metadata.get("width"), metadata.get("height")
    if (not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0
            or not isinstance(width, int) or isinstance(width, bool) or width <= 0
            or not isinstance(height, int) or isinstance(height, bool) or height <= 0):
        raise ValueError("视频来源探测元数据无效")
    verified_at = utc_timestamp()
    return {
        "schemaVersion": 1,
        "sourceId": "source-local-" + content_hash[:24],
        "sourceType": "local",
        "originalReference": name,
        "adapter": dict(LOCAL_VIDEO_ADAPTER),
        "probe": {"durationSeconds": float(duration), "width": width, "height": height},
        "rightsConfirmation": {"confirmedAt": confirmed_at},
        "contentHash": content_hash,
        "verifiedAt": verified_at,
        "status": "available",
    }


def validate_source_status(status: str) -> VideoSourceStatus:
    if status not in VIDEO_SOURCE_STATUSES:
        raise ValueError("视频来源状态无效")
    return status  # type: ignore[return-value]


def _display_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("视频来源原始引用无效")
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255]
    if not name or any(ord(character) < 32 for character in name):
        raise ValueError("视频来源原始引用无效")
    return name
