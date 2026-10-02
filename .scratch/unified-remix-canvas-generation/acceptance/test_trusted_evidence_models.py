from dataclasses import FrozenInstanceError, fields

import pytest

from trusted_evidence.errors import classify_error
from trusted_evidence.models import (
    EvidencePointer,
    TruthSourceAdapter,
    VerificationResult,
)


def _valid_pointer() -> dict[str, object]:
    return {
        "id": "ticket-23-e2-canvas-persistence-a1b2c3d4",
        "profile_id": "ticket-23/v1",
        "verifier_id": "e2.canvas-persistence",
        "subject_ref": "git://product@" + "a" * 40,
        "artifact_ref": "sha256:" + "b" * 64,
        "registered_at": "2026-10-02T08:30:00Z",
    }


def test_pointer_accepts_only_six_immutable_fields():
    pointer = EvidencePointer.from_dict(_valid_pointer())

    assert [field.name for field in fields(pointer)] == [
        "id",
        "profile_id",
        "verifier_id",
        "subject_ref",
        "artifact_ref",
        "registered_at",
    ]
    with pytest.raises(FrozenInstanceError):
        pointer.id = "rewritten"  # type: ignore[misc]


def test_pointer_rejects_self_reported_level_supports_command_exit_decision_and_status():
    for self_reported_key in (
        "level",
        "supports",
        "command",
        "exit_code",
        "decision",
        "status",
    ):
        raw = _valid_pointer()
        raw[self_reported_key] = "self-reported"

        with pytest.raises(ValueError, match="unknown fields"):
            EvidencePointer.from_dict(raw)


def test_verification_result_cannot_be_loaded_from_catalog_json():
    assert not hasattr(VerificationResult, "from_dict")


def test_error_codes_map_capability_and_zero_tolerance_to_failure_and_unverifiable_input_to_blocked():
    assert classify_error("TEST_REPLAY_FAILED") == "失败"
    assert classify_error("ZERO_TOLERANCE_VIOLATION") == "失败"
    assert classify_error("TRUTH_SOURCE_UNAVAILABLE") == "阻塞"
    assert classify_error("UNRECOGNIZED_CODE") == "阻塞"


def test_truth_source_adapter_is_protocol_only_and_has_no_production_registration():
    assert getattr(TruthSourceAdapter, "_is_protocol", False)
    assert not hasattr(TruthSourceAdapter, "production_adapters")
