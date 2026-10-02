"""Secret-safe byte redaction for persisted verification receipts."""

from __future__ import annotations

import re
from re import Pattern
from typing import Final


class RedactionError(ValueError):
    """Output cannot be rendered safely enough to persist."""


_VALUE: Final = r'''(?:"(?:[^"\\\r\n]|\\.)*(?:"|$)|'(?:[^'\\\r\n]|\\.)*(?:'|$)|[^\s\r\n]+)'''

# Replacement and residual detection intentionally share this exact vocabulary.
DEFAULT_SECRET_PATTERNS: Final[tuple[Pattern[str], ...]] = (
    re.compile(r"(?im)^.*\b(?:proxy-)?authorization\s*:\s*[^\r\n]*"),
    re.compile(r"(?im)^.*\b(?:set-)?cookie\s*:\s*[^\r\n]*"),
    re.compile(
        rf"(?i)\b(?:OPENAI_API_KEY|GITHUB_TOKEN|AZURE_CLIENT_SECRET|"
        rf"AWS_[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)[A-Z0-9_]*)\b\s*[:=]\s*{_VALUE}"
    ),
    re.compile(
        rf"(?i)\b[A-Z0-9_]*(?:COOKIE|AUTH(?:ORIZATION)?|SSH_AUTH_SOCK)\b\s*[:=]\s*{_VALUE}"
    ),
    re.compile(
        rf'''(?i)(?:\\?["'])?(?:api[_ -]?key|client[_ -]?secret|password|access[_ -]?token|'''
        rf'''refresh[_ -]?token|token|secret)(?:\\?["'])?\s*[:=]\s*{_VALUE}'''
    ),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+(?:@[^\s]+|$)"),
    re.compile(r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)"),
    re.compile(r"(?i)(?:file://)?[^\s]*\.ssh/(?:id_[^\s/]+|[^\s/]*private[^\s/]*)"),
)


def ensure_redacted(text: str, patterns: tuple[Pattern[str], ...]) -> None:
    """Reject text still matching the same secret vocabulary used to redact it."""
    if any(pattern.search(text) for pattern in patterns):
        raise RedactionError("receipt output still contains a credential marker")


def redact_output(data: bytes, patterns: tuple[Pattern[str], ...]) -> bytes:
    """Replace every configured textual secret before any receipt is stored."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RedactionError("receipt output is not UTF-8") from error
    for pattern in patterns:
        text = pattern.sub("[REDACTED]", text)
    ensure_redacted(text, patterns)
    return text.encode("utf-8")
