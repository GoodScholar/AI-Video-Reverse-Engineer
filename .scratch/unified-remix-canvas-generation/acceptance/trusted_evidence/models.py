"""Immutable core contracts for the trusted acceptance evidence bridge."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import math
import re
from types import MappingProxyType
from typing import Literal, Protocol


_SUBJECT_REF_PATTERN = re.compile(r"^git://product@[0-9a-f]{40}$")
_ARTIFACT_REF_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_CATALOG_POINTER_FIELDS = {
    "id": "id",
    "profileId": "profile_id",
    "verifierId": "verifier_id",
    "subjectRef": "subject_ref",
    "artifactRef": "artifact_ref",
    "registeredAt": "registered_at",
}


def _require_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_rfc3339(value: object, field_name: str) -> str:
    timestamp = _require_nonempty_string(value, field_name)
    if not _RFC3339_PATTERN.fullmatch(timestamp):
        raise ValueError(f"{field_name} must be RFC3339")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be RFC3339") from error
    return timestamp


def _freeze_json_value(value: object, field_name: str, ancestors: set[int] | None = None) -> object:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain finite JSON values")
        return value
    if isinstance(value, (Mapping, list, tuple)):
        ancestors = set() if ancestors is None else ancestors
        value_id = id(value)
        if value_id in ancestors:
            raise ValueError(f"{field_name} must not contain cycles")
        ancestors.add(value_id)
        try:
            if isinstance(value, Mapping):
                frozen = {}
                for key, nested_value in value.items():
                    if not isinstance(key, str):
                        raise ValueError(f"{field_name} mapping keys must be strings")
                    frozen[key] = _freeze_json_value(nested_value, field_name, ancestors)
                return MappingProxyType(frozen)
            return tuple(_freeze_json_value(item, field_name, ancestors) for item in value)
        finally:
            ancestors.remove(value_id)
    raise ValueError(f"{field_name} must contain JSON values")


def _freeze_mapping(value: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    frozen = _freeze_json_value(value, field_name)
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple of non-empty strings")
    return tuple(_require_nonempty_string(item, field_name) for item in value)


def _freeze_string_set(value: object, field_name: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"{field_name} must be a collection of non-empty strings")
    return frozenset(_require_nonempty_string(item, field_name) for item in value)


@dataclass(frozen=True, slots=True)
class EvidencePointer:
    """The only catalog payload: an immutable reference, never a result."""

    id: str
    profile_id: str
    verifier_id: str
    subject_ref: str
    artifact_ref: str
    registered_at: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EvidencePointer:
        if not isinstance(raw, Mapping):
            raise ValueError("pointer must be a mapping")

        keys = set(raw)
        expected_fields = set(_CATALOG_POINTER_FIELDS)
        unknown = keys - expected_fields
        if unknown:
            raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
        missing = expected_fields - keys
        if missing:
            raise ValueError(f"missing fields: {', '.join(sorted(missing))}")

        subject_ref = _require_nonempty_string(raw["subjectRef"], "subjectRef")
        if not _SUBJECT_REF_PATTERN.fullmatch(subject_ref):
            raise ValueError("subject_ref must be git://product@<40-hex>")
        artifact_ref = _require_nonempty_string(raw["artifactRef"], "artifactRef")
        if not _ARTIFACT_REF_PATTERN.fullmatch(artifact_ref):
            raise ValueError("artifact_ref must be sha256:<64-hex>")

        return cls(
            id=_require_nonempty_string(raw["id"], "id"),
            profile_id=_require_nonempty_string(raw["profileId"], "profileId"),
            verifier_id=_require_nonempty_string(raw["verifierId"], "verifierId"),
            subject_ref=subject_ref,
            artifact_ref=artifact_ref,
            registered_at=_require_rfc3339(raw["registeredAt"], "registeredAt"),
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Runtime-only verifier output; no catalog deserialization API is exposed."""

    valid: bool
    error_code: str | None
    facts: Mapping[str, object]
    observed_at: str
    input_digest: str
    zero_tolerance_violations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise ValueError("valid must be a boolean")
        if self.error_code is not None:
            _require_nonempty_string(self.error_code, "error_code")
        object.__setattr__(self, "facts", _freeze_mapping(self.facts, "facts"))
        object.__setattr__(
            self,
            "observed_at",
            _require_rfc3339(self.observed_at, "observed_at"),
        )
        input_digest = _require_nonempty_string(self.input_digest, "input_digest")
        if not _ARTIFACT_REF_PATTERN.fullmatch(input_digest):
            raise ValueError("input_digest must be sha256:<64-hex>")
        object.__setattr__(self, "input_digest", input_digest)
        object.__setattr__(
            self,
            "zero_tolerance_violations",
            _freeze_string_tuple(self.zero_tolerance_violations, "zero_tolerance_violations"),
        )


@dataclass(frozen=True, slots=True)
class VerificationContext:
    """Immutable execution inputs supplied by promotion or reconciliation."""

    subject_ref: str
    observed_at: str
    trust_root: str

    def __post_init__(self) -> None:
        if not _SUBJECT_REF_PATTERN.fullmatch(self.subject_ref):
            raise ValueError("subject_ref must be git://product@<40-hex>")
        object.__setattr__(self, "observed_at", _require_rfc3339(self.observed_at, "observed_at"))
        _require_nonempty_string(self.trust_root, "trust_root")


@dataclass(frozen=True, slots=True)
class AcceptanceProfile:
    """Static, reviewable policy for one ticket profile."""

    profile_id: str
    version: str
    subject_ref: str
    owned_item_ids: frozenset[str]
    required_verifier_ids: Mapping[str, tuple[str, ...]]
    supporting_item_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _require_nonempty_string(self.profile_id, "profile_id")
        _require_nonempty_string(self.version, "version")
        if not _SUBJECT_REF_PATTERN.fullmatch(self.subject_ref):
            raise ValueError("subject_ref must be git://product@<40-hex>")
        object.__setattr__(self, "owned_item_ids", _freeze_string_set(self.owned_item_ids, "owned_item_ids"))
        object.__setattr__(
            self,
            "supporting_item_ids",
            _freeze_string_set(self.supporting_item_ids, "supporting_item_ids"),
        )
        if not isinstance(self.required_verifier_ids, Mapping):
            raise ValueError("required_verifier_ids must be a mapping")
        object.__setattr__(
            self,
            "required_verifier_ids",
            MappingProxyType(
                {
                    _require_nonempty_string(item_id, "required_verifier_ids key"): _freeze_string_tuple(
                        verifier_ids,
                        "required_verifier_ids values",
                    )
                    for item_id, verifier_ids in self.required_verifier_ids.items()
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class TruthQueryPolicy:
    """Per-profile constraints for an E3 truth-source lookup."""

    allowed_schemes: frozenset[str]
    max_age_seconds: int | None = None

    def __post_init__(self) -> None:
        schemes = _freeze_string_set(self.allowed_schemes, "allowed_schemes")
        if not schemes:
            raise ValueError("allowed_schemes must not be empty")
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        object.__setattr__(self, "allowed_schemes", schemes)


@dataclass(frozen=True, slots=True)
class CanonicalTruthRecord:
    """Minimal normalized E3 response; business state remains at its source."""

    ref: str
    observed_at: str
    facts: Mapping[str, object]

    def __post_init__(self) -> None:
        _require_nonempty_string(self.ref, "ref")
        object.__setattr__(self, "observed_at", _require_rfc3339(self.observed_at, "observed_at"))
        object.__setattr__(self, "facts", _freeze_mapping(self.facts, "facts"))


class Verifier(Protocol):
    verifier_id: str
    level: Literal["E1", "E2", "E3", "E4"]

    def verify(
        self,
        pointer: EvidencePointer,
        profile: AcceptanceProfile,
        context: VerificationContext,
    ) -> VerificationResult: ...


class TruthSourceAdapter(Protocol):
    """Future E3 boundary only; Task 1 registers no production adapter."""

    schemes: frozenset[str]

    def resolve(self, ref: str, policy: TruthQueryPolicy) -> CanonicalTruthRecord: ...
