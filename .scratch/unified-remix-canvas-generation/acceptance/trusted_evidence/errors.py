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
        "E2_ARTIFACT_TOO_LARGE",
        "E2_ARTIFACT_SCHEMA_INVALID",
        "E2_POINTER_MISSING",
        "E2_POINTER_AMBIGUOUS",
        "E2_TOOLCHAIN_UNAVAILABLE",
        "E2_TOOLCHAIN_PLATFORM_MISMATCH",
        "E2_TOOLCHAIN_MANIFEST_TOO_LARGE",
        "E2_TOOLCHAIN_CLOSURE_INVALID",
        "E2_TOOLCHAIN_PACKAGE_TOO_LARGE",
        "E2_TOOLCHAIN_ARCHIVE_INVALID",
        "E2_TOOLCHAIN_ENTRY_LIMIT",
        "E2_TOOLCHAIN_EXPANSION_LIMIT",
        "E2_DYNAMIC_DEPENDENCY_UNTRUSTED",
        "E2_DEPENDENCY_REPOSITORY_INVALID",
        "E2_DEPENDENCY_VIEW_INVALID",
        "E2_PYTHON_SDIST_FORBIDDEN",
        "E2_DEPENDENCY_MAPPING_INVALID",
        "E2_DERIVED_ENTRYPOINT_INVALID",
        "E2_LIVE_REGISTRY_ACCESS",
        "E2_HOST_TOOL_FALLBACK",
        "GIT_METADATA_TOO_LARGE",
        "GIT_METADATA_UNSAFE",
        "GIT_CONFIG_UNSAFE",
        "GIT_EXTERNAL_OBJECT_SOURCE",
        "GIT_REF_UNSAFE",
        "GIT_OBJECT_LAYOUT_UNSAFE",
        "GIT_OBJECT_BUDGET_EXCEEDED",
        "REPORT_TOO_LARGE",
        "REPORT_SCHEMA_INVALID",
        "E2_SETUP_FAILED",
        "E2_CONTAINMENT_UNAVAILABLE",
        "E2_CONTAINMENT_CLEANUP_FAILED",
        "OUTPUT_REDACTION_UNSAFE",
    }
)

# The set is deliberately explicit: it is the approved error-code vocabulary,
# while unknown codes remain fail-closed as "阻塞".
KNOWN_ERROR_CODES = _FAILURE_CODES | _BLOCKED_CODES


def classify_error(code: str) -> ErrorDisposition:
    """Classify a stable error code without allowing unknown codes to pass."""
    if code in _FAILURE_CODES:
        return "失败"
    return "阻塞"
