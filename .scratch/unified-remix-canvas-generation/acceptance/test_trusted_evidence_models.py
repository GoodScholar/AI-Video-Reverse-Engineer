from dataclasses import FrozenInstanceError, fields

import pytest

from trusted_evidence.errors import classify_error
from trusted_evidence.models import (
    AcceptanceProfile,
    CanonicalTruthRecord,
    EvidencePointer,
    TruthQueryPolicy,
    TruthSourceAdapter,
    VerificationResult,
)


def _valid_pointer() -> dict[str, object]:
    return {
        "id": "ticket-23-e2-canvas-persistence-a1b2c3d4",
        "profileId": "ticket-23/v1",
        "verifierId": "e2.canvas-persistence",
        "subjectRef": "git://product@" + "a" * 40,
        "artifactRef": "sha256:" + "b" * 64,
        "registeredAt": "2026-10-02T08:30:00Z",
    }


def test_pointer_accepts_only_six_immutable_fields():
    pointer = EvidencePointer.from_dict(_valid_pointer())

    assert pointer.profile_id == "ticket-23/v1"
    assert pointer.verifier_id == "e2.canvas-persistence"
    assert pointer.subject_ref == "git://product@" + "a" * 40
    assert pointer.artifact_ref == "sha256:" + "b" * 64
    assert pointer.registered_at == "2026-10-02T08:30:00Z"
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
        "exitCode",
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


def test_runtime_facts_are_deeply_frozen_and_detached_from_source_values():
    result_facts = {"nested": {"ids": ["original"]}}
    record_facts = {"nested": {"ids": ["original"]}}
    result = VerificationResult(
        valid=True,
        error_code=None,
        facts=result_facts,
        observed_at="2026-10-02T08:30:00Z",
        input_digest="sha256:" + "c" * 64,
        zero_tolerance_violations=(),
    )
    record = CanonicalTruthRecord(
        ref="project://ticket-23/run-1",
        observed_at="2026-10-02T08:30:00Z",
        facts=record_facts,
    )

    result_facts["nested"]["ids"].append("mutated")
    record_facts["nested"]["ids"].append("mutated")

    assert result.facts["nested"]["ids"] == ("original",)
    assert record.facts["nested"]["ids"] == ("original",)
    with pytest.raises(TypeError):
        result.facts["nested"]["new"] = "value"


def _acceptance_profile(**overrides):
    values = {
        "profile_id": "ticket-23/v1",
        "version": "v1",
        "subject_ref": "git://product@" + "a" * 40,
        "owned_item_ids": {"C01"},
        "required_verifier_ids": {"C01": ("e2.canvas-persistence",)},
        "supporting_item_ids": set(),
    }
    values.update(overrides)
    return AcceptanceProfile(**values)


@pytest.mark.parametrize(
    "factory",
    (
        lambda value: VerificationResult(
            valid=False,
            error_code="ZERO_TOLERANCE_VIOLATION",
            facts={},
            observed_at="2026-10-02T08:30:00Z",
            input_digest="sha256:" + "c" * 64,
            zero_tolerance_violations=value,
        ),
        lambda value: _acceptance_profile(owned_item_ids=value),
        lambda value: _acceptance_profile(supporting_item_ids=value),
        lambda value: _acceptance_profile(required_verifier_ids={"C01": value}),
        lambda value: TruthQueryPolicy(allowed_schemes=value),
    ),
    ids=(
        "zero_tolerance_violations",
        "owned_item_ids",
        "supporting_item_ids",
        "required_verifier_ids",
        "allowed_schemes",
    ),
)
@pytest.mark.parametrize("invalid_collection", ("C01", b"C01"))
def test_collection_fields_reject_string_and_bytes_inputs(factory, invalid_collection):
    with pytest.raises(ValueError):
        factory(invalid_collection)
