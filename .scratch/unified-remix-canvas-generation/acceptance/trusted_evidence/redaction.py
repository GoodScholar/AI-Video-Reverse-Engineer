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
            re.compile(r"(?i)\b(?:proxy-)?authorization\s*:\s*(?:basic|bearer|token)\s+\S+"),
            re.compile(r"(?i)\b(?:set-)?cookie\s*:\s*[^\r\n]+"),
            re.compile(r"(?i)\bssh_auth_sock\s*[:=]\s*\S+"),
            re.compile(r"(?i)\baws_secret_access_key\s*[:=]\s*\S+"),
            re.compile(r'(?i)"(?:api[_-]?key|secret|token|aws_secret_access_key)"\s*:\s*"[^"\\]*"'),
            re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        )
    ):
        raise RedactionError("receipt output still contains a credential marker")
    return text.encode("utf-8")
