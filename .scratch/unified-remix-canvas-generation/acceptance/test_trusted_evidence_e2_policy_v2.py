import hashlib
import json
import copy
import os
import pickle

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
    BrokerLifecycleProofV1,
    PolicyPublishTransactionJournalV1,
    RegistrationJournalEntryV1,
    RegistrationPendingTransitionV1,
    RegistrationRepositoryRecoveryBindingV1,
    RegistrationTransactionCapabilityV1,
    RegistrationTransactionIntentV1,
    RegistrationTransactionStateV1,
    Task26CompletionPayloadV1,
    Task26CompletionRecordV1,
    BrokerAuthLeaseIssueRequestV1,
    BrokerAuthLeaseIssueEventPayloadV1,
    BrokerAuthSequenceJournalEntryV1,
    BrokerAuthSequenceJournalV1,
    BrokerAuthSequenceStateV1,
    RegistrationBrokerAuthKeyRegistrationV1,
    RegistrationBrokerAuthRegistryV1,
    RegistrationBrokerAuthRevocationEntryV1,
    RegistrationBrokerAuthRevocationsV1,
    RegistrationRecoveryLeaseIdentityPreimageV1,
    RegistrationWorkerSessionFamilyAuthorizationV1,
)
from trusted_evidence.handoffs import PolicyActivationMarkerV1


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


# D.2 uses literal bytes, actual directory descriptors, and a side-effect
# recorder: contract failures must never reach an authorization write.
def _recovery_binding(**overrides):
    values = {
        "schema": "trusted-registration-repository-recovery-binding/v1",
        "repository_absolute_path": "/audit/repository", "repository_device": 11,
        "repository_inode": 12, "repository_mount_identity": "mount-1",
        "repository_boundary_digest": DIGEST_A, "base_commit": BASE,
        "fixed_ref_name": "refs/heads/fixed", "fixed_ref_commit": BASE,
        "trusted_git_toolchain_build_record_ref": DIGEST_B,
        "trusted_git_toolchain_build_record_digest": DIGEST_C,
        "trusted_git_executable_digest": DIGEST_A,
        "trusted_config_snapshot_digest": DIGEST_B,
        "trusted_attribute_snapshot_digest": DIGEST_C,
        "trusted_worktree_snapshot_digest": DIGEST_A,
        "trusted_git_view_digest": DIGEST_B, "task_output_policy_digest": DIGEST_C,
    }
    values.update(overrides)
    return RegistrationRepositoryRecoveryBindingV1(**values)


def _registration_intent(**overrides):
    staged = StagedCandidateSetV1("trusted-staged-candidate-set/v1", True, tuple(
        StagingCandidateProvenanceV1(ref, 10, index, 17, ref)
        for index, ref in enumerate(REFS, 100)
    ))
    values = {
        "schema": "trusted-registration-transaction-intent/v1", "operation_id": "operation-d2",
        "output_plan_digest": _task28_allowed().canonical_digest,
        "trusted_pre_state_digest": _prestate().canonical_digest,
        "staged_candidate_set_digest": staged.canonical_digest,
        "quarantine_object_set_digest": _quarantine().canonical_digest,
        "repository_recovery_binding_digest": _recovery_binding().canonical_digest,
        "registration_recovery_lease_identity_digest": DIGEST_A,
        "expected_cas_refs": REFS, "expected_audit_digest": DIGEST_B,
        "expected_catalog_digest": DIGEST_C,
    }
    values.update(overrides)
    return RegistrationTransactionIntentV1(**values)


def test_registration_transaction_records_decode_literal_bytes_and_reject_chain_drift_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    literal = b'{"action":"install-cas","expectedContentDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","targetRepositoryRelativePath":"evidence/1.json"}'
    pending = RegistrationPendingTransitionV1.from_canonical_bytes(literal)
    assert pending.to_wire() == {"action": "install-cas", "expectedContentDigest": DIGEST_A, "targetRepositoryRelativePath": "evidence/1.json"}

    binding, intent = _recovery_binding(), _registration_intent()
    prepared = RegistrationJournalEntryV1("trusted-registration-journal-entry/v1", "operation-d2", intent.canonical_digest, "PREPARED", None, None, DIGEST_C)
    writing = RegistrationJournalEntryV1("trusted-registration-journal-entry/v1", "operation-d2", intent.canonical_digest, "WRITING_CAS", prepared.canonical_digest, pending, DIGEST_C)
    state = RegistrationTransactionStateV1("trusted-registration-transaction-state/v1", "operation-d2", intent.canonical_digest, writing.canonical_digest, "WRITING_CAS", prepared.canonical_digest, pending, DIGEST_C, DIGEST_A, False, False)

    assert RegistrationRepositoryRecoveryBindingV1.from_canonical_bytes(binding.canonical_bytes) == binding
    assert RegistrationTransactionIntentV1.from_canonical_bytes(intent.canonical_bytes) == intent
    assert RegistrationJournalEntryV1.from_canonical_bytes(writing.canonical_bytes) == writing
    assert RegistrationTransactionStateV1.from_canonical_bytes(state.canonical_bytes) == state
    RegistrationJournalEntryV1.validate_chain((prepared, writing))
    recorder.assert_rejected(lambda: _registration_intent(expected_cas_refs=REFS[:-1]))
    recorder.assert_rejected(lambda: RegistrationJournalEntryV1.validate_chain((prepared, RegistrationJournalEntryV1("trusted-registration-journal-entry/v1", "operation-d2", intent.canonical_digest, "WRITING_AUDIT", prepared.canonical_digest, pending, DIGEST_C))))
    recorder.assert_rejected(lambda: RegistrationTransactionStateV1("trusted-registration-transaction-state/v1", "operation-d2", intent.canonical_digest, writing.canonical_digest, "ACTIVATED", prepared.canonical_digest, None, DIGEST_C, DIGEST_A, False, True))
    recorder.assert_rejected(lambda: RegistrationTransactionIntentV1.from_canonical_bytes(intent.canonical_bytes.replace(b'"canonicalDigest"', b'"unexpected":null,"canonicalDigest"', 1)))


def test_precreated_transaction_capability_owns_real_directory_fds_and_has_no_restoration_path(tmp_path):
    parent, root, operation, repository = tmp_path / "parent", tmp_path / "parent" / "root", tmp_path / "parent" / "root" / "operation-d2", tmp_path / "repository"
    operation.mkdir(parents=True)
    repository.mkdir()
    descriptors = tuple(os.open(path, os.O_RDONLY | os.O_DIRECTORY) for path in (parent, root, operation, repository))
    capability = None
    try:
        capability = RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
        assert len(capability.directory_identities) == 4
        capability.validate_open_identities()
        with pytest.raises((TypeError, ValueError)):
            RegistrationTransactionCapabilityV1(str(parent))
        with pytest.raises(TypeError):
            copy.copy(capability)
        with pytest.raises(TypeError):
            pickle.dumps(capability)
        regular = tmp_path / "regular"
        regular.write_text("not a directory")
        regular_fd = os.open(regular, os.O_RDONLY)
        try:
            with pytest.raises(ValueError):
                RegistrationTransactionCapabilityV1.open_precreated(regular_fd, *descriptors[1:])
        finally:
            os.close(regular_fd)
    finally:
        if capability is not None:
            capability.close()
        for descriptor in descriptors:
            os.close(descriptor)


def test_lifecycle_completion_and_policy_journal_bind_full_forward_chain_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    proof = BrokerLifecycleProofV1("trusted-broker-lifecycle-proof/v1", "broker-key", "lifecycle-1", "lease-1", ProductGitObjectIdV1("sha1", "1" * 40), (NamedDigest("record-payload", DIGEST_A), NamedDigest("staged/report", DIGEST_B)), DIGEST_C, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, "a" * 128)
    proof.validate_staged_artifacts((NamedDigest("record-payload", DIGEST_A), NamedDigest("staged/report", DIGEST_B)))
    payload = Task26CompletionPayloadV1("ai-video-reverse-engineer", 26, BASE, BASE, BASE, "refs/heads/main", "pass", NamedDigest("review", DIGEST_A), NamedDigest("plan", DIGEST_B), NamedDigest("spec", DIGEST_C), "task26-review-controller/v1", "executor-1", DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A)
    record = Task26CompletionRecordV1("trusted-task-completion-record/v1", payload, "sha256:" + hashlib.sha256(payload.canonical_bytes).hexdigest(), "broker-key", "b" * 128)
    assert Task26CompletionRecordV1.from_canonical_bytes(record.canonical_bytes) == record
    entries, previous = [], None
    for state, transition in (("PREPARED", "QUARANTINE"), ("QUARANTINED", "PROMOTE_OBJECTS"), ("OBJECTS_PROMOTED", "INSTALL_REFS"), ("REFS_INSTALLED", "INSTALL_HANDOFF"), ("HANDOFF_INSTALLED", "ACTIVATE")):
        entry = PolicyPublishTransactionJournalV1("trusted-policy-publish-transaction/v1", "policy-op", record.canonical_digest, BASE, "refs/heads/main", "refs/tags/trusted-acceptance/ticket-23-policy-v1", True, BASE if state != "PREPARED" else None, DIGEST_A, DIGEST_A if state in {"HANDOFF_INSTALLED", "ACTIVATED"} else None, DIGEST_B if state in {"HANDOFF_INSTALLED", "ACTIVATED"} else None, DIGEST_C if state in {"HANDOFF_INSTALLED", "ACTIVATED"} else None, None, state, transition, previous)
        entries.append(entry)
        previous = entry.entry_digest
    marker = PolicyActivationMarkerV1("trusted-policy-activation/v1", "policy-op", entries[-1].entry_digest, BASE, "refs/tags/trusted-acceptance/ticket-23-policy-v1", DIGEST_A, DIGEST_B, DIGEST_C)
    entries.append(PolicyPublishTransactionJournalV1("trusted-policy-publish-transaction/v1", "policy-op", record.canonical_digest, BASE, "refs/heads/main", "refs/tags/trusted-acceptance/ticket-23-policy-v1", True, BASE, DIGEST_A, DIGEST_A, DIGEST_B, DIGEST_C, "sha256:" + hashlib.sha256(marker.canonical_bytes).hexdigest(), "ACTIVATED", None, previous))
    PolicyPublishTransactionJournalV1.validate_forward_sequence(tuple(entries), marker)
    recorder.assert_rejected(lambda: proof.validate_staged_artifacts((NamedDigest("record-payload", DIGEST_B), NamedDigest("staged/report", DIGEST_B))))
    recorder.assert_rejected(lambda: Task26CompletionPayloadV1("ai-video-reverse-engineer", 26, BASE, BASE, BASE, "refs/heads/main", "fail", NamedDigest("review", DIGEST_A), NamedDigest("plan", DIGEST_B), NamedDigest("spec", DIGEST_C), "task26-review-controller/v1", "executor-1", DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A))
    recorder.assert_rejected(lambda: PolicyPublishTransactionJournalV1.validate_forward_sequence(tuple(entries[:-1]), marker))


# E.1 uses literals deliberately.  A production change that accepts an
# unlinked head, non-contiguous sequence, or a post-state field in an issue
# request must make this test fail before it can reach authorization writes.
def test_broker_auth_issue_chain_is_prestate_only_and_contiguous_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    key = RegistrationBrokerAuthKeyRegistrationV1(
        "trusted-registration-broker-auth-key-registration/v1", "broker-fixed",
        "registration-broker-auth", "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "ai-video-reverse-engineer/registration-broker-lease/v1",
        "ai-video-reverse-engineer/registration-broker-startup-challenge/v1",
        "raw-ed25519-32", DIGEST_A, DIGEST_A, None, DIGEST_A, DIGEST_A, DIGEST_B,
        "active", 1, None,
    )
    registry = RegistrationBrokerAuthRegistryV1(
        "trusted-registration-broker-auth-registry/v1", 1, None, "broker-fixed", (key,)
    )
    revocations = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", registry.canonical_digest, ()
    )
    family = RegistrationWorkerSessionFamilyAuthorizationV1(
        "trusted-registration-worker-session-family-authorization/v1", "op-1", DIGEST_A,
        DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, "broker-fixed", key.canonical_digest,
        ("register", "recover"), "register-to-recover", DIGEST_C, DIGEST_A, DIGEST_B,
        DIGEST_C, DIGEST_A,
    )
    request = BrokerAuthLeaseIssueRequestV1(
        "trusted-registration-broker-auth-lease-issue-request/v1", "op-1", "lifecycle-1",
        family.canonical_digest, "broker-fixed", 0, DIGEST_A, registry.canonical_digest,
        revocations.canonical_digest, DIGEST_B, DIGEST_C, 2,
    )
    payload = BrokerAuthLeaseIssueEventPayloadV1(
        "trusted-registration-broker-auth-lease-issue-payload/v1", "issue-lease", DIGEST_A,
        request, request.request_digest, "op-1", "broker-fixed", 1, 2, 3, DIGEST_B, DIGEST_B,
    )
    entry = BrokerAuthSequenceJournalEntryV1(
        "trusted-registration-broker-auth-sequence-entry/v1", 1, None, DIGEST_A, "issue-lease",
        DIGEST_C, len(payload.canonical_bytes), payload.canonical_digest, DIGEST_B, DIGEST_B,
        registry.canonical_digest, revocations.canonical_digest, "broker-fixed", "a" * 64,
    )
    journal = BrokerAuthSequenceJournalV1(
        "trusted-registration-broker-auth-sequence-journal/v1", DIGEST_A, 1, (entry,), "b" * 64
    )
    state = BrokerAuthSequenceStateV1(
        "trusted-registration-broker-auth-sequence-state/v1", DIGEST_A, 1, entry.canonical_digest,
        DIGEST_C, payload.canonical_digest, DIGEST_B, registry.canonical_digest,
        revocations.canonical_digest, "c" * 64,
    )
    journal.validate_state(state, {DIGEST_C: payload})
    recorder.assert_rejected(lambda: BrokerAuthLeaseIssueRequestV1(
        "trusted-registration-broker-auth-lease-issue-request/v1", "op-1", "lifecycle-1",
        family.canonical_digest, "broker-fixed", 1, entry.canonical_digest, registry.canonical_digest,
        revocations.canonical_digest, DIGEST_B, DIGEST_C, 0,
    ))
    recorder.assert_rejected(lambda: BrokerAuthSequenceJournalV1(
        "trusted-registration-broker-auth-sequence-journal/v1", DIGEST_A, 1,
        (BrokerAuthSequenceJournalEntryV1(
            "trusted-registration-broker-auth-sequence-entry/v1", 2, None, DIGEST_A, "issue-lease",
            DIGEST_C, len(payload.canonical_bytes), payload.canonical_digest, DIGEST_B, DIGEST_B,
            registry.canonical_digest, revocations.canonical_digest, "broker-fixed", "a" * 64,
        ),), "b" * 64,
    ))


def test_lease_identity_preimage_rejects_session_fields_and_binds_family_authorization():
    recorder = VisibleAuthorizationWriteRecorder()
    family = RegistrationWorkerSessionFamilyAuthorizationV1(
        "trusted-registration-worker-session-family-authorization/v1", "op-identity", DIGEST_A,
        DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, "broker-fixed", DIGEST_C,
        ("register", "recover"), "register-to-recover", DIGEST_A, DIGEST_B, DIGEST_C,
        DIGEST_A, DIGEST_B,
    )
    preimage = RegistrationRecoveryLeaseIdentityPreimageV1(
        "trusted-registration-recovery-lease-identity-preimage/v1", "ai-video-reverse-engineer",
        "op-identity", "stable-lifecycle", family.canonical_digest, DIGEST_A, "broker-fixed", 7,
        DIGEST_B, DIGEST_C, 3, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B,
        9, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, DIGEST_C,
    )
    assert preimage.lease_identity_digest == "sha256:3b8f8ce786f10f13eb93aee652286cf8383b52a16005d49d6afda559f7f9808f"
    recorder.assert_rejected(lambda: RegistrationRecoveryLeaseIdentityPreimageV1.from_canonical_bytes(
        preimage.canonical_bytes.replace(b"}", b',\"workerSessionId\":\"forbidden\"}', 1)
    ))
