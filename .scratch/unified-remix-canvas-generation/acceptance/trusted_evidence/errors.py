"""Stable error semantics for trusted acceptance evidence."""

from typing import Literal


ErrorDisposition = Literal["失败", "阻塞"]


_FAILURE_CODES = frozenset(
    {
        "GIT_FACT_MISMATCH",
        "LICENSE_MISMATCH",
        "TEST_REPLAY_FAILED",
        "TRUTH_RELATION_MISMATCH",
        "ZERO_TOLERANCE_VIOLATION",
    }
)

_BLOCKED_CODES = frozenset(
    {
        "POINTER_INVALID",
        "ARTIFACT_MISSING",
        "DIGEST_MISMATCH",
        "PROFILE_NOT_FOUND",
        "VERIFIER_NOT_REGISTERED",
        "PROFILE_SCOPE_MISMATCH",
        "SOURCE_REF_UNREACHABLE",
        "SUITE_NOT_ALLOWLISTED",
        "TEST_SELECTION_MISSING",
        "TEST_REPLAY_TIMEOUT",
        "TRUTH_SOURCE_UNAVAILABLE",
        "TRUTH_SOURCE_STALE",
        "SIGNER_UNTRUSTED",
        "SIGNATURE_INVALID",
        "ATTESTATION_SCOPE_MISMATCH",
        "EVIDENCE_EXPIRED",
        "EVIDENCE_REVOKED",
        "ACCEPTANCE_STATE_CHANGED",
    }
)

# The set is deliberately explicit: it is the complete Section 9 error-code
# vocabulary, while unknown codes remain fail-closed as "阻塞".
KNOWN_ERROR_CODES = _FAILURE_CODES | _BLOCKED_CODES


def classify_error(code: str) -> ErrorDisposition:
    """Classify a stable error code without allowing unknown codes to pass."""
    if code in _FAILURE_CODES:
        return "失败"
    return "阻塞"
