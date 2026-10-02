"""Secret-safe byte redaction for persisted verification receipts."""

from __future__ import annotations

import re
from re import Pattern


class RedactionError(ValueError):
    """Output cannot be rendered safely enough to persist."""


def redact_output(data: bytes, patterns: tuple[Pattern[str], ...]) -> bytes:
    """Replace every configured textual secret before any receipt is stored."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RedactionError("receipt output is not UTF-8") from error
    for pattern in patterns:
        text = pattern.sub("[REDACTED]", text)
    if any(
        marker.search(text)
        for marker in (
            re.compile(r"(?im)^\s*(?:authorization|cookie|set-cookie|ssh_auth_sock)\s*[:=]"),
            re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        )
    ):
        raise RedactionError("receipt output still contains a credential marker")
    return text.encode("utf-8")
