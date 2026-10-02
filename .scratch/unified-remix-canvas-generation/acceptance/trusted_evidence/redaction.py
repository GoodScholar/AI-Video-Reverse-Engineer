"""Secret-safe byte redaction for persisted verification receipts."""

from __future__ import annotations

from re import Pattern


def redact_output(data: bytes, patterns: tuple[Pattern[str], ...]) -> bytes:
    """Replace every configured textual secret before any receipt is stored."""
    text = data.decode("utf-8", "replace")
    for pattern in patterns:
        text = pattern.sub("[REDACTED]", text)
    return text.encode("utf-8")
