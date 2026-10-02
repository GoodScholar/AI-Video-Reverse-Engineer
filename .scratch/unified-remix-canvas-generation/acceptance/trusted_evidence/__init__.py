"""Trusted acceptance evidence contracts."""

from .errors import ErrorDisposition, KNOWN_ERROR_CODES, classify_error
from .models import (
    AcceptanceProfile,
    CanonicalTruthRecord,
    EvidencePointer,
    TruthQueryPolicy,
    TruthSourceAdapter,
    VerificationContext,
    VerificationResult,
    Verifier,
)

__all__ = [
    "AcceptanceProfile",
    "CanonicalTruthRecord",
    "ErrorDisposition",
    "EvidencePointer",
    "KNOWN_ERROR_CODES",
    "TruthQueryPolicy",
    "TruthSourceAdapter",
    "VerificationContext",
    "VerificationResult",
    "Verifier",
    "classify_error",
]
