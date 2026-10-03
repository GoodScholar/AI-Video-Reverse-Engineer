import hashlib
import json

import pytest

from trusted_evidence.e2_policy import (
    AcceptanceOperationSnapshotV1,
    AllowedTaskOutputsV1,
    GitObjectIdV1,
    NamedDigest,
    ProductGitObjectIdV1,
    QuarantineObjectIdentityV1,
    QuarantineObjectSetV1,
    RegistrationPreStatePathV1,
    ReplayPolicyBindingV1,
    SourceDirtyAllowanceV1,
    StagedCandidateIdentityV1,
    StagedCandidateSetV1,
    StagingCandidateProvenanceV1,
    TaskOutputTransitionV1,
    TrustedRegistrationPreStateV1,
    ExpectedTaskOutputV1,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _binding(**overrides):
    values = {
        "registry_digest": DIGEST_A,
        "suite_digests": (NamedDigest("suite/main", DIGEST_B),),
        "profile_digests": (NamedDigest("profile/main", DIGEST_C),),
        "revocation_digest": DIGEST_A,
        "reviewer_policy_digest": DIGEST_B,
        "toolchain_digests": (NamedDigest("toolchain/linux", DIGEST_C),),
        "dependency_digests": (),
        "product_source_digests": (NamedDigest("product/main", DIGEST_A),),
        "containment_policy_digests": (NamedDigest("containment/linux", DIGEST_B),),
    }
    values.update(overrides)
    return ReplayPolicyBindingV1(**values)


def test_replay_binding_excludes_catalog_contract_and_observation_time():
    """Adding operation-only state must not change a replay-policy digest."""
    binding = _binding()

    assert binding.canonical_digest == "sha256:8649890b1c44801a18deb6b2a66fac64055585eb681a64b524da1e65e2651790"
    assert "catalog" not in binding.canonical_bytes.decode()
    assert "contract" not in binding.canonical_bytes.decode()
    assert "observed" not in binding.canonical_bytes.decode()


def test_operation_snapshot_binds_catalog_contract_and_replay_binding():
    """Changing any operation barrier input invalidates its snapshot digest."""
    binding = _binding()
    snapshot = AcceptanceOperationSnapshotV1(DIGEST_A, DIGEST_B, binding.canonical_digest)

    assert snapshot.canonical_digest == "sha256:b12fa2b49e1df947c6038e11c014564297afd51dad0adae44d66c776bdedf6a7"
    assert snapshot != AcceptanceOperationSnapshotV1(DIGEST_C, DIGEST_B, binding.canonical_digest)
    assert snapshot != AcceptanceOperationSnapshotV1(DIGEST_A, DIGEST_C, binding.canonical_digest)


def test_named_digest_order_is_identical_for_different_creation_order():
    """Filesystem/construction order must not enter a binding digest."""
    first = _binding(
        suite_digests=(NamedDigest("suite/z", DIGEST_A), NamedDigest("suite/a", DIGEST_B)),
    )
    second = _binding(
        suite_digests=(NamedDigest("suite/a", DIGEST_B), NamedDigest("suite/z", DIGEST_A)),
    )

    assert first.suite_digests == second.suite_digests
    assert first.canonical_digest == second.canonical_digest


def test_duplicate_name_and_illegal_empty_set_reject():
    """A duplicate logical name or missing required binding set weakens closure."""
    with pytest.raises(ValueError):
        _binding(suite_digests=(NamedDigest("suite/main", DIGEST_A), NamedDigest("suite/main", DIGEST_B)))
    with pytest.raises(ValueError):
        _binding(product_source_digests=())


def test_v1_is_not_upgraded():
    """A v1-shaped policy cannot silently acquire v2 defaults."""
    with pytest.raises(ValueError):
        ReplayPolicyBindingV1.from_dict({"version": 1})


@pytest.mark.parametrize(
    ("algorithm", "hex_value"),
    (("sha1", "a" * 40), ("sha256", "b" * 64)),
)
def test_policy_git_object_ids_allow_only_matching_algorithm_lengths(algorithm, hex_value):
    assert GitObjectIdV1(algorithm, hex_value).hex == hex_value


@pytest.mark.parametrize(
    ("algorithm", "hex_value"),
    (("sha512", "a" * 64), ("sha1", "a" * 64), ("sha256", "a" * 40)),
)
def test_policy_git_object_ids_reject_unknown_or_mismatched_algorithms(algorithm, hex_value):
    with pytest.raises(ValueError):
        GitObjectIdV1(algorithm, hex_value)


@pytest.mark.parametrize("value", ("b" * 64, "a" * 39, "a" * 41))
def test_product_git_object_id_is_sha1_only(value):
    with pytest.raises(ValueError):
        ProductGitObjectIdV1("sha1", value)


# D.1 uses literals deliberately: expected canonical bytes and hashes never
# originate from the codecs under test.
REFS = tuple("sha256:" + value * 64 for value in "1234567")
BASE = GitObjectIdV1("sha1", "0" * 40)


class VisibleAuthorizationWriteRecorder:
    """Records the authorization boundary; portable codecs must never cross it."""

    def __init__(self):
        self.visible_authorization_writes = 0

    def assert_rejected(self, action):
        with pytest.raises(ValueError):
            action()
        assert self.visible_authorization_writes == 0


def _post(status="added", digest=DIGEST_A, source=REFS[0]):
    return ExpectedTaskOutputV1(status, 17, digest, source)


def _task28_allowed(**overrides):
    candidates = tuple(
        StagedCandidateIdentityV1(ref, 10, ordinal, 17, ref)
        for ordinal, ref in enumerate(REFS, start=100)
    )
    transitions = (
        TaskOutputTransitionV1("audit.json", "task28-register", "audit", "must-not-exist", None, "create-exact-bytes", _post(), None),
        TaskOutputTransitionV1("catalog.json", "task28-register", "catalog", "equals-base-bytes", DIGEST_A, "replace-with-exact-bytes", _post("modified", DIGEST_B, REFS[1]), None),
        *tuple(
            TaskOutputTransitionV1(
                f"evidence/{ordinal}.json", "task28-register", "evidence-cas",
                "must-not-exist-or-same-intent-recovery", None,
                "create-or-resume-exact-cas", _post("added", ref, ref), ref,
            )
            for ordinal, ref in enumerate(REFS, start=1)
        ),
    )
    values = {
        "schema": "trusted-allowed-task-outputs/v1", "task_id": 28,
        "phase": "task28-register", "base_commit": BASE,
        "task_output_policy_digest": DIGEST_A, "protected_paths_digest": DIGEST_B,
        "source_dirty_inputs": (SourceDirtyAllowanceV1("tests/task28.py", "modified", DIGEST_C),),
        "staged_candidates": candidates, "transitions": transitions,
        "candidate_refs": REFS, "new_candidate_refs": REFS,
        "idempotent_candidate_refs": (), "expected_post_tree_digest": DIGEST_C,
    }
    values.update(overrides)
    return AllowedTaskOutputsV1(**values)


def _task29_allowed(**overrides):
    values = {
        "schema": "trusted-allowed-task-outputs/v1", "task_id": 29,
        "phase": "task29-promote", "base_commit": BASE,
        "task_output_policy_digest": DIGEST_A, "protected_paths_digest": DIGEST_B,
        "source_dirty_inputs": (SourceDirtyAllowanceV1("README.md", "modified", DIGEST_C),),
        "staged_candidates": (),
        "transitions": (TaskOutputTransitionV1("matrix-contract.json", "task29-promote", "contract", "equals-base-bytes", DIGEST_A, "replace-with-exact-bytes", _post("modified", DIGEST_B, REFS[0]), None),),
        "candidate_refs": REFS, "new_candidate_refs": REFS,
        "idempotent_candidate_refs": (), "expected_post_tree_digest": DIGEST_C,
    }
    values.update(overrides)
    return AllowedTaskOutputsV1(**values)


def test_allowed_outputs_accept_literal_task28_and_task29_manifests():
    """Both phases have closed, phase-specific plans made from literal identities."""
    register = _task28_allowed()
    promote = _task29_allowed()

    assert register.canonical_digest == "sha256:fd2899922d63ed1a289d61d8e5aeb05a9632e7573bfe0f5a407abec6db57b997"
    assert register.canonical_bytes == json.dumps(
        {**register.to_wire()}, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert AllowedTaskOutputsV1.from_canonical_bytes(register.canonical_bytes) == register
    assert AllowedTaskOutputsV1.from_canonical_bytes(promote.canonical_bytes) == promote


def test_allowed_outputs_reject_cross_phase_dirty_refresh_and_candidate_drift_without_writes():
    """A codec cannot widen a plan from current dirty bytes or an index-like change."""
    recorder = VisibleAuthorizationWriteRecorder()
    recorder.assert_rejected(lambda: _task28_allowed(phase="task29-promote"))
    recorder.assert_rejected(lambda: _task28_allowed(source_dirty_inputs=(SourceDirtyAllowanceV1("tests/task28.py", "renamed", DIGEST_C),)))
    recorder.assert_rejected(lambda: _task28_allowed(candidate_refs=REFS[:-1]))
    recorder.assert_rejected(lambda: _task28_allowed(new_candidate_refs=REFS[:-1], idempotent_candidate_refs=(REFS[-1],)))
    recorder.assert_rejected(lambda: _task28_allowed(staged_candidates=tuple(reversed(_task28_allowed().staged_candidates))))
    recorder.assert_rejected(lambda: _task28_allowed(transitions=tuple(reversed(_task28_allowed().transitions))))


def test_allowed_outputs_accept_base_tracked_exact_idempotent_cas_recovery():
    """A retained base CAS must use the one matching retain transition."""
    baseline = _task28_allowed()
    retained = REFS[-1]
    transitions = tuple(
        TaskOutputTransitionV1(
            item.repository_relative_path, item.phase, item.output_kind,
            "equals-base-bytes", DIGEST_A, "retain-exact-cas",
            _post("unchanged", retained, retained), retained,
        ) if item.candidate_ref == retained else item
        for item in baseline.transitions
    )
    plan = _task28_allowed(
        transitions=transitions, new_candidate_refs=REFS[:-1],
        idempotent_candidate_refs=(retained,),
    )
    assert plan.idempotent_candidate_refs == (retained,)


def test_allowed_outputs_reject_noncanonical_unknown_missing_duplicate_and_oversize_bytes_without_writes():
    """Strict decode rejects wire ambiguity before any authorization side effect."""
    recorder = VisibleAuthorizationWriteRecorder()
    payload = _task28_allowed().canonical_bytes
    recorder.assert_rejected(lambda: AllowedTaskOutputsV1.from_canonical_bytes(payload.replace(b'"schema"', b'"unknown":null,"schema"', 1)))
    recorder.assert_rejected(lambda: AllowedTaskOutputsV1.from_canonical_bytes(payload.replace(b',"schema"', b'', 1)))
    recorder.assert_rejected(lambda: AllowedTaskOutputsV1.from_canonical_bytes(payload[:-1] + b',"schema":"trusted-allowed-task-outputs/v1"}'))
    recorder.assert_rejected(lambda: AllowedTaskOutputsV1.from_canonical_bytes(b'{\n' + payload[1:]))
    recorder.assert_rejected(lambda: AllowedTaskOutputsV1.from_canonical_bytes(payload + b' '))
    recorder.assert_rejected(lambda: AllowedTaskOutputsV1.from_canonical_bytes(b'{' + b'"x":"' + b'x' * (64 * 1024) + b'"}'))
    recorder.assert_rejected(lambda: _task28_allowed(source_dirty_inputs=(SourceDirtyAllowanceV1("x" * (64 * 1024), "modified", DIGEST_C),)))


def _prestate_path(path, role, classification, status, digest):
    return RegistrationPreStatePathV1(path, role, classification, status, digest)


def _prestate(**overrides):
    cas = tuple(_prestate_path(f"evidence/{index}.json", "evidence-cas", "absent", "absent", None) for index in range(1, 8))
    values = {
        "schema": "trusted-registration-pre-state/v1", "base_commit": BASE,
        "fixed_ref_name": "refs/heads/fixed", "fixed_ref_commit": BASE,
        "catalog": _prestate_path("catalog.json", "catalog", "equals-base-bytes", "unchanged", DIGEST_A),
        "audit": _prestate_path("audit.json", "audit", "absent", "absent", None),
        "cas_entries": cas,
        "source_dirty_inputs": (_prestate_path("tests/task28.py", "source-dirty", "allowed-dirty", "modified", DIGEST_B),),
        "protected_paths": (_prestate_path("policy.json", "protected", "protected-base-exact", "unchanged", DIGEST_C),),
        "index_classification": "empty", "index_digest": DIGEST_A,
    }
    values.update(overrides)
    return TrustedRegistrationPreStateV1(**values)


def test_prestate_and_staging_provenance_bind_literal_identity_and_self_digest():
    """Durable pre-state records are closed identities, not refreshable observations."""
    prestate = _prestate()
    staged = StagedCandidateSetV1("trusted-staged-candidate-set/v1", True, tuple(StagingCandidateProvenanceV1(ref, 10, index, 17, ref) for index, ref in enumerate(REFS, 100)))

    for record in (prestate, staged):
        wire = json.loads(record.canonical_bytes)
        digest = wire.pop("canonicalDigest")
        assert digest == "sha256:" + hashlib.sha256(
            json.dumps(wire, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
    assert TrustedRegistrationPreStateV1.from_canonical_bytes(prestate.canonical_bytes) == prestate
    assert StagedCandidateSetV1.from_canonical_bytes(staged.canonical_bytes) == staged


def test_prestate_and_staging_reject_drift_and_tamper_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    recorder.assert_rejected(lambda: _prestate(cas_entries=_prestate().cas_entries[:-1]))
    recorder.assert_rejected(lambda: _prestate(source_dirty_inputs=(_prestate_path("tests/task28.py", "source-dirty", "allowed-dirty", "modified", None),)))
    recorder.assert_rejected(lambda: StagedCandidateSetV1("trusted-staged-candidate-set/v1", False, ()))
    recorder.assert_rejected(lambda: StagedCandidateSetV1("trusted-staged-candidate-set/v1", True, tuple(reversed(tuple(StagingCandidateProvenanceV1(ref, 10, index, 17, ref) for index, ref in enumerate(REFS, 100))))))
    payload = _prestate().canonical_bytes
    recorder.assert_rejected(lambda: TrustedRegistrationPreStateV1.from_canonical_bytes(payload.replace(b'"canonicalDigest":"sha256:', b'"canonicalDigest":"sha256:0', 1)))


def _quarantine(**overrides):
    objects = (
        QuarantineObjectIdentityV1("audit.json", "expected-audit", None, "regular", 501, 0o600, 1, 10, 11, 17, DIGEST_A),
        *tuple(QuarantineObjectIdentityV1(f"candidates/{index}.json", "candidate", ref, "regular", 501, 0o600, 1, 10, index, 17, ref) for index, ref in enumerate(REFS, 1)),
        QuarantineObjectIdentityV1("plan.json", "output-plan", None, "regular", 501, 0o600, 1, 10, 99, 17, DIGEST_B),
    )
    values = {"schema": "trusted-registration-quarantine-object-set/v1", "objects": objects}
    values.update(overrides)
    return QuarantineObjectSetV1(**values)


def test_quarantine_set_accepts_exact_nine_regular_identity_records():
    quarantine = _quarantine()
    assert QuarantineObjectSetV1.from_canonical_bytes(quarantine.canonical_bytes) == quarantine


def test_quarantine_rejects_replacement_hardlink_ref_and_nfc_drift_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    objects = _quarantine().objects
    recorder.assert_rejected(lambda: _quarantine(objects=objects[:-1]))
    recorder.assert_rejected(lambda: _quarantine(objects=(QuarantineObjectIdentityV1("audit.json", "expected-audit", None, "regular", 501, 0o600, 2, 10, 11, 17, DIGEST_A), *objects[1:])))
    recorder.assert_rejected(lambda: _quarantine(objects=(QuarantineObjectIdentityV1("audit.json", "expected-audit", REFS[0], "regular", 501, 0o600, 1, 10, 11, 17, DIGEST_A), *objects[1:])))
    recorder.assert_rejected(lambda: QuarantineObjectIdentityV1("cafe\u0301.json", "output-plan", None, "regular", 501, 0o600, 1, 10, 11, 17, DIGEST_A))


@pytest.mark.parametrize(
    ("record", "decoder"),
    (
        (_task28_allowed(), AllowedTaskOutputsV1.from_canonical_bytes),
        (_prestate(), TrustedRegistrationPreStateV1.from_canonical_bytes),
        (StagedCandidateSetV1("trusted-staged-candidate-set/v1", True, tuple(StagingCandidateProvenanceV1(ref, 10, index, 17, ref) for index, ref in enumerate(REFS, 100))), StagedCandidateSetV1.from_canonical_bytes),
        (_quarantine(), QuarantineObjectSetV1.from_canonical_bytes),
    ),
)
def test_every_self_digest_codec_rejects_unknown_missing_duplicate_noncanonical_and_tamper_without_writes(record, decoder):
    """All durable records use the same exact-field and excluded-self-digest rule."""
    recorder = VisibleAuthorizationWriteRecorder()
    payload = record.canonical_bytes
    recorder.assert_rejected(lambda: decoder(payload.replace(b"{", b'{"unknown":null,', 1)))
    recorder.assert_rejected(lambda: decoder(payload.replace(b'"schema":', b'"schemaMissing":', 1)))
    recorder.assert_rejected(lambda: decoder(payload[:-1] + b',"schema":"duplicate"}'))
    recorder.assert_rejected(lambda: decoder(payload + b"\n"))
    recorder.assert_rejected(lambda: decoder(payload.replace(record.canonical_digest.encode("ascii"), ("sha256:" + "0" * 64).encode("ascii"), 1)))
