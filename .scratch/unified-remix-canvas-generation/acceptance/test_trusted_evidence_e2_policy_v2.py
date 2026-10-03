import hashlib
import hmac
import json
import copy
from dataclasses import replace
import os
import pickle
import socket
import fcntl
import array
import base64
import sys
from pathlib import Path

import pytest

from trusted_evidence.e2_policy import (
    AcceptanceOperationSnapshotV1,
    AllowedTaskOutputsV1,
    canonical_digest,
    canonical_json_bytes,
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
    BrokerAuthEnvelopeSignEventPayloadV1,
    BrokerAuthRotatePolicyEventPayloadV1,
    BrokerAuthRevokePolicyEventPayloadV1,
    BrokerAuthAuthoritativeIssuerV1,
    BrokerAuthSequenceJournalEntryV1,
    BrokerAuthSequenceJournalV1,
    BrokerAuthSequenceReplayBaselineV1,
    BrokerAuthSequenceStateV1,
    RegistrationBrokerAuthKeyRegistrationV1,
    RegistrationBrokerAuthRegistryV1,
    RegistrationBrokerAuthRevocationEntryV1,
    RegistrationBrokerAuthRevocationsV1,
    RegistrationBrokerAuthPrivateKeySlotV1,
    RegistrationBrokerAuthGovernanceV1,
    RegistrationBrokerStateMacCapabilityV1,
    RegistrationExpectedReceiveContextV1,
    RegistrationRecoveryLeaseV1,
    RegistrationRecoveryLeaseIdentityPreimageV1,
    RegistrationWorkerSessionFamilyAuthorizationV1,
    RegistrationWorkerInputObjectV1,
    RegistrationWorkerInputV1,
    FdOpenProvenanceV1,
    FdObservableIdentityV1,
    FdDescriptorV1,
    CapabilityEnvelopeUnsignedV1,
    CapabilityEnvelopeV1,
    RegistrationRecoveryCapabilityV1,
    RegistrationMutationChannelBindingV1,
    RegistrationMutationRequestV1,
    RegistrationMutationSenderEvidenceV1,
    RegistrationBrokerMutationVerifierV1,
    BrokerMutationReceiptV1,
    RegistrationForwardOnlyRecoveryAuthorityV1,
    HeldCandidateFdV1,
    PinnedTaskOutputPathV1,
    PinnedTaskOutputRepositoryObservationV1,
    verify_registration_forward_only_recovery,
    validate_task_output_repository_state,
    _read_bounded_broker_sequence_payload_fd,
    _ed_sign,
)
from trusted_evidence.handoffs import PolicyActivationMarkerV1


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _broker_governance(key_id: str, public: bytes):
    """Build the sole canonical governance closure used by issuer/lease tests."""
    public_b64 = base64.b64encode(public).decode("ascii")
    allowed = {"keys": [{
        "keyId": key_id, "purpose": "registration-broker-auth",
        "rawPublicKey": public_b64, "resolvedPublicKey": public_b64,
        "signatureDomain": "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "wrapperIdentity": "test-broker-wrapper-v1",
    }]}
    allowed_bytes = canonical_json_bytes(allowed)
    public_digest = "sha256:" + hashlib.sha256(public).hexdigest()
    key = RegistrationBrokerAuthKeyRegistrationV1(
        "trusted-registration-broker-auth-key-registration/v1", key_id,
        "registration-broker-auth", "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "ai-video-reverse-engineer/registration-broker-lease/v1",
        "ai-video-reverse-engineer/registration-broker-startup-challenge/v1", "raw-ed25519-32",
        public_digest, public_digest,
        "sha256:" + hashlib.sha256(b"test-broker-wrapper-v1").hexdigest(),
        public_digest, public_digest, "sha256:" + hashlib.sha256(canonical_json_bytes(allowed["keys"][0])).hexdigest(),
        "active", 1, None,
    )
    registry = RegistrationBrokerAuthRegistryV1(
        "trusted-registration-broker-auth-registry/v1", 1, None, key_id, (key,)
    )
    revocations = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", registry.canonical_digest, ()
    )
    policy_digest = _broker_policy_digest(registry, revocations)
    return (
        RegistrationBrokerAuthGovernanceV1.from_canonical_bytes(
            registry.canonical_bytes, revocations.canonical_bytes, allowed_bytes,
        ), key,
    )


def _state_mac_capability(tmp_path, key: bytes):
    path = tmp_path / "state-mac"
    path.write_bytes(key)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        return RegistrationBrokerStateMacCapabilityV1.load_from_readonly_fd(descriptor)
    finally:
        os.close(descriptor)


def _broker_policy_digest(registry, revocations):
    """Hand-derived v1 policy-state binding used to test broker replay."""
    return canonical_digest({
        "registryDigest": registry.canonical_digest,
        "revocationsDigest": revocations.canonical_digest,
        "schema": "trusted-registration-broker-auth-policy-state/v1",
    })


def test_authoritative_issuer_rejects_cross_generation_signer_replacement(tmp_path):
    """A signer replacement must close over the issuer's exact governance generation."""
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    governance, key = _broker_governance("broker-fixed", public)
    allowed = {"keys": [{
        "keyId": "broker-fixed", "purpose": "registration-broker-auth",
        "rawPublicKey": base64.b64encode(public).decode("ascii"),
        "resolvedPublicKey": base64.b64encode(public).decode("ascii"),
        "signatureDomain": "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "wrapperIdentity": "test-broker-wrapper-v1",
    }]}
    next_registry = RegistrationBrokerAuthRegistryV1(
        "trusted-registration-broker-auth-registry/v1", 2,
        governance.registry.canonical_digest, "broker-fixed", (key,),
    )
    next_revocations = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", next_registry.canonical_digest, (),
    )
    cross_generation = RegistrationBrokerAuthGovernanceV1.from_canonical_bytes(
        next_registry.canonical_bytes, next_revocations.canonical_bytes, canonical_json_bytes(allowed),
    )
    key_path = tmp_path / "broker-key"
    key_path.write_bytes(seed)
    descriptor = os.open(key_path, os.O_RDONLY)
    try:
        signer = RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, key, public)
    finally:
        os.close(descriptor)
    state_mac = _state_mac_capability(tmp_path, b"state-mac-key-is-independent-32!!!"[:32])
    try:
        issuer = BrokerAuthAuthoritativeIssuerV1.create(
            governance, DIGEST_A, _broker_policy_digest(governance.registry, governance.revocations),
            signer, state_mac, 2,
        )
        before = (issuer.state, issuer.registry, issuer.revocations)
        with pytest.raises(ValueError, match="governance"):
            issuer.replace_signer(signer, cross_generation)
        assert (issuer.state, issuer.registry, issuer.revocations) == before
    finally:
        state_mac.close()
        signer.close()


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
ACCEPTANCE_ROOT = ".scratch/unified-remix-canvas-generation/acceptance"
TASK28_SOURCE = f"{ACCEPTANCE_ROOT}/test_ticket23_collect_register.py"
TASK28_CATALOG = f"{ACCEPTANCE_ROOT}/evidence-catalog.json"
TASK28_AUDIT = f"{ACCEPTANCE_ROOT}/audit/ticket-23-trusted-evidence.json"
TASK28_CAS_ROOT = f"{ACCEPTANCE_ROOT}/evidence/sha256"
TASK29_CONTRACT = f"{ACCEPTANCE_ROOT}/matrix-contract.json"
TASK29_SOURCES = (
    f"{ACCEPTANCE_ROOT}/README.md",
    f"{ACCEPTANCE_ROOT}/test_ticket23_promotion.py",
    ".scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md",
)


def _cas_path(ref):
    return f"{TASK28_CAS_ROOT}/{ref[7:9]}/{ref[7:]}"


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
        TaskOutputTransitionV1(TASK28_AUDIT, "task28-register", "audit", "must-not-exist", None, "create-exact-bytes", _post(), None),
        TaskOutputTransitionV1(TASK28_CATALOG, "task28-register", "catalog", "equals-base-bytes", DIGEST_A, "replace-with-exact-bytes", _post("modified", DIGEST_B, REFS[1]), None),
        *tuple(
            TaskOutputTransitionV1(
                _cas_path(ref), "task28-register", "evidence-cas",
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
        "source_dirty_inputs": (SourceDirtyAllowanceV1(TASK28_SOURCE, "modified", DIGEST_C),),
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
        "source_dirty_inputs": tuple(SourceDirtyAllowanceV1(path, "modified", DIGEST_C) for path in TASK29_SOURCES),
        "staged_candidates": (),
        "transitions": (TaskOutputTransitionV1(TASK29_CONTRACT, "task29-promote", "contract", "equals-base-bytes", DIGEST_A, "replace-with-exact-bytes", _post("modified", DIGEST_B, REFS[0]), None),),
        "candidate_refs": REFS, "new_candidate_refs": REFS,
        "idempotent_candidate_refs": (), "expected_post_tree_digest": DIGEST_C,
    }
    values.update(overrides)
    return AllowedTaskOutputsV1(**values)


def _materialized_task28_plan(candidate_paths):
    """Build a literal plan whose candidate identities come from already openable files."""
    refs = tuple(sorted(candidate_paths))
    identities = tuple(
        StagedCandidateIdentityV1(ref, candidate_paths[ref].stat().st_dev, candidate_paths[ref].stat().st_ino,
                                  candidate_paths[ref].stat().st_size, _digest_bytes(candidate_paths[ref].read_bytes()))
        for ref in refs
    )
    transitions = (
        TaskOutputTransitionV1(TASK28_AUDIT, "task28-register", "audit", "must-not-exist", None, "create-exact-bytes", ExpectedTaskOutputV1("added", 5, _digest_bytes(b"audit"), refs[0]), None),
        TaskOutputTransitionV1(TASK28_CATALOG, "task28-register", "catalog", "equals-base-bytes", DIGEST_A, "replace-with-exact-bytes", ExpectedTaskOutputV1("modified", 7, _digest_bytes(b"catalog"), refs[0]), None),
        *tuple(TaskOutputTransitionV1(_cas_path(ref), "task28-register", "evidence-cas", "must-not-exist-or-same-intent-recovery", None, "create-or-resume-exact-cas", ExpectedTaskOutputV1("added", candidate_paths[ref].stat().st_size, ref, ref), ref) for ref in refs),
    )
    policy_bytes = (Path(__file__).parent / "trust" / "task-output-policy.json").read_bytes()
    policy = json.loads(policy_bytes)
    return AllowedTaskOutputsV1("trusted-allowed-task-outputs/v1", 28, "task28-register", BASE, _digest_bytes(policy_bytes), _digest_bytes(canonical_json_bytes(policy["protectedPaths"])), (SourceDirtyAllowanceV1(TASK28_SOURCE, "modified", DIGEST_C),), identities, transitions, refs, refs, (), DIGEST_C)


def _pinned_task28_paths(plan, candidate_paths):
    policy = json.loads((Path(__file__).parent / "trust" / "task-output-policy.json").read_bytes())
    paths = [PinnedTaskOutputPathV1(item.repository_relative_path, DIGEST_A, item.expected_status, item.expected_content_digest, 1, 1, 1, None) for item in plan.source_dirty_inputs]
    paths.extend(PinnedTaskOutputPathV1(item["repositoryRelativePath"], DIGEST_A, "unchanged", DIGEST_A, 1, 1, 1, None) for item in policy["protectedPaths"])
    for transition in plan.transitions:
        if transition.output_kind == "catalog":
            paths.append(PinnedTaskOutputPathV1(transition.repository_relative_path, transition.expected_pre_digest, "unchanged", transition.expected_pre_digest, 2, 2, 1, None))
        else:
            paths.append(PinnedTaskOutputPathV1(transition.repository_relative_path, None, "absent", None, None, None, None, None))
    return tuple(sorted(paths, key=lambda item: item.repository_relative_path.encode("utf-8")))


def test_allowed_outputs_accept_literal_task28_and_task29_manifests():
    """Both phases have closed, phase-specific plans made from literal identities."""
    register = _task28_allowed()
    promote = _task29_allowed()

    assert register.canonical_bytes == json.dumps(
        {**register.to_wire()}, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert AllowedTaskOutputsV1.from_canonical_bytes(register.canonical_bytes) == register
    assert AllowedTaskOutputsV1.from_canonical_bytes(promote.canonical_bytes) == promote


def test_allowed_outputs_reject_cross_phase_dirty_refresh_and_candidate_drift_without_writes():
    """A codec cannot widen a plan from current dirty bytes or an index-like change."""
    recorder = VisibleAuthorizationWriteRecorder()
    recorder.assert_rejected(lambda: _task28_allowed(phase="task29-promote"))
    recorder.assert_rejected(lambda: _task28_allowed(source_dirty_inputs=(SourceDirtyAllowanceV1(TASK28_SOURCE, "renamed", DIGEST_C),)))
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


def test_pinned_task_output_observation_rejects_ambient_git_and_path_refresh(monkeypatch, tmp_path):
    """The Task 2 verifier consumes only a sealed Task 7 view and held FDs."""
    policy_bytes = (Path(__file__).parent / "trust" / "task-output-policy.json").read_bytes()
    candidate_paths = {}
    held = []
    try:
        for number in range(1, 8):
            payload = f"candidate-{number}\n".encode("ascii")
            ref = _digest_bytes(payload)
            candidate = tmp_path / str(number)
            candidate.write_bytes(payload)
            candidate_paths[ref] = candidate
        plan = _materialized_task28_plan(candidate_paths)
        for ref, candidate in candidate_paths.items():
            held.append(HeldCandidateFdV1.from_fd(ref, os.open(candidate, os.O_RDONLY)))
        observation = PinnedTaskOutputRepositoryObservationV1(
            "trusted-pinned-task-output-observation/v1", plan.base_commit, DIGEST_A,
            plan.canonical_digest, True, (), _pinned_task28_paths(plan, candidate_paths), None,
        )
        monkeypatch.setattr("trusted_evidence.e2_policy.subprocess", object(), raising=False)
        monkeypatch.setattr("trusted_evidence.e2_policy.Path", lambda *args, **kwargs: pytest.fail("ambient repository path"), raising=False)
        for candidate in candidate_paths.values():
            candidate.unlink()
        validate_task_output_repository_state(
            plan, policy_bytes, observation, tuple(held),
            expected_allowed_outputs_digest=plan.canonical_digest,
            expected_trusted_git_view_digest=DIGEST_A,
        )
    finally:
        for item in held:
            item.close()


def test_pinned_task_output_observation_allows_only_same_intent_exact_cas_residual(tmp_path):
    """Equal residual bytes are insufficient unless both pinned facts name one intent."""
    policy_bytes = (Path(__file__).parent / "trust" / "task-output-policy.json").read_bytes()
    intent = DIGEST_B
    candidate_paths = {}
    held = []
    try:
        for number in range(1, 8):
            payload = f"candidate-{number}\n".encode("ascii")
            ref = _digest_bytes(payload)
            candidate = tmp_path / str(number)
            candidate.write_bytes(payload)
            candidate_paths[ref] = candidate
        plan = _materialized_task28_plan(candidate_paths)
        for ref, candidate in candidate_paths.items():
            held.append(HeldCandidateFdV1.from_fd(ref, os.open(candidate, os.O_RDONLY)))
        residual_ref = plan.candidate_refs[0]
        paths = list(_pinned_task28_paths(plan, candidate_paths))
        target = next(index for index, item in enumerate(paths) if item.repository_relative_path == plan.transitions[2].repository_relative_path)
        candidate = candidate_paths[residual_ref]
        metadata = candidate.stat()
        paths[target] = PinnedTaskOutputPathV1(
            paths[target].repository_relative_path, None, "added", residual_ref,
            metadata.st_dev, metadata.st_ino, metadata.st_size, intent,
        )
        observation = PinnedTaskOutputRepositoryObservationV1(
            "trusted-pinned-task-output-observation/v1", plan.base_commit, DIGEST_A,
            plan.canonical_digest, True, (), tuple(paths), intent,
        )
        validate_task_output_repository_state(
            plan, policy_bytes, observation, tuple(held),
            expected_allowed_outputs_digest=plan.canonical_digest,
            expected_trusted_git_view_digest=DIGEST_A,
            persistent_intent_digest=intent,
        )
        with pytest.raises(ValueError):
            validate_task_output_repository_state(
                plan, policy_bytes, observation, tuple(held),
                expected_allowed_outputs_digest=plan.canonical_digest,
                expected_trusted_git_view_digest=DIGEST_A,
                persistent_intent_digest=DIGEST_C,
            )
    finally:
        for item in held:
            item.close()


def test_pinned_task_output_observation_rejects_view_swap_and_task29_candidate_fds(tmp_path):
    """Task 29 consumes a closed no-FD view, and a replacement view is never accepted."""
    policy_bytes = (Path(__file__).parent / "trust" / "task-output-policy.json").read_bytes()
    policy = json.loads(policy_bytes)
    plan = AllowedTaskOutputsV1(
        "trusted-allowed-task-outputs/v1", 29, "task29-promote", BASE,
        _digest_bytes(policy_bytes), _digest_bytes(canonical_json_bytes(policy["protectedPaths"])),
        tuple(SourceDirtyAllowanceV1(path, "modified", DIGEST_C) for path in TASK29_SOURCES), (),
        (TaskOutputTransitionV1(TASK29_CONTRACT, "task29-promote", "contract", "equals-base-bytes", DIGEST_A, "replace-with-exact-bytes", ExpectedTaskOutputV1("modified", 17, DIGEST_B, REFS[0]), None),),
        REFS, REFS, (), DIGEST_C,
    )
    paths = [PinnedTaskOutputPathV1(path, DIGEST_A, "modified", DIGEST_C, 1, 1, 1, None) for path in TASK29_SOURCES]
    paths.extend(PinnedTaskOutputPathV1(item["repositoryRelativePath"], DIGEST_A, "unchanged", DIGEST_A, 1, 1, 1, None) for item in policy["protectedPaths"])
    paths.append(PinnedTaskOutputPathV1(TASK29_CONTRACT, DIGEST_A, "unchanged", DIGEST_A, 2, 2, 1, None))
    observation = PinnedTaskOutputRepositoryObservationV1(
        "trusted-pinned-task-output-observation/v1", BASE, DIGEST_A, plan.canonical_digest,
        True, (), tuple(sorted(paths, key=lambda item: item.repository_relative_path.encode("utf-8"))), None,
    )
    validate_task_output_repository_state(
        plan, policy_bytes, observation, (), expected_allowed_outputs_digest=plan.canonical_digest,
        expected_trusted_git_view_digest=DIGEST_A,
    )
    with pytest.raises(ValueError):
        validate_task_output_repository_state(
            plan, policy_bytes, observation, (), expected_allowed_outputs_digest=plan.canonical_digest,
            expected_trusted_git_view_digest=DIGEST_B,
        )
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"candidate\n")
    held = HeldCandidateFdV1.from_fd(_digest_bytes(b"candidate\n"), os.open(candidate, os.O_RDONLY))
    try:
        with pytest.raises(ValueError):
            validate_task_output_repository_state(
                plan, policy_bytes, observation, (held,), expected_allowed_outputs_digest=plan.canonical_digest,
                expected_trusted_git_view_digest=DIGEST_A,
            )
    finally:
        held.close()


def test_task_output_policy_uses_only_ticket23_paths_and_derived_cas_layout():
    """The frozen policy names real Ticket 23 inputs, outputs, and protected governance."""
    raw = json.loads((Path(__file__).parent / "trust" / "task-output-policy.json").read_bytes())
    protected = {item["repositoryRelativePath"] for item in raw["protectedPaths"]}
    assert {TASK28_SOURCE, TASK28_CATALOG, TASK28_AUDIT, TASK29_CONTRACT} <= {
        raw["tasks"][0]["sourceDirtyInputs"][0]["repositoryRelativePath"],
        raw["tasks"][0]["transitions"][0]["repositoryRelativePath"],
        raw["tasks"][0]["transitions"][1]["repositoryRelativePath"],
        raw["task29ContractPath"],
    }
    assert raw["tasks"][0]["candidateCasPathPrefix"] == TASK28_CAS_ROOT
    assert raw["tasks"][1]["sourceDirtyInputs"] == [
        {"repositoryRelativePath": path, "role": role}
        for path, role in ((TASK29_SOURCES[0], "readme"), (TASK29_SOURCES[1], "test-source"), (TASK29_SOURCES[2], "issue"))
    ]
    assert {
        f"{ACCEPTANCE_ROOT}/trust/handoff-transport-allowed-signers",
        f"{ACCEPTANCE_ROOT}/trust/policy-activation-allowed-signers",
        f"{ACCEPTANCE_ROOT}/trust/registration-broker-auth-allowed-signers",
        f"{ACCEPTANCE_ROOT}/tools/trusted_handoff_orchestrator.py",
    } <= protected


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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


def test_precreated_transaction_capability_owns_real_directory_fds_and_has_no_restoration_path(tmp_path, monkeypatch):
    parent, root, operation = tmp_path / "parent", tmp_path / "parent" / "root", tmp_path / "parent" / "root" / "operation-d2"
    repository = tmp_path / "repository-authority"
    operation.mkdir(parents=True)
    repository.mkdir()
    for path in (parent, root, operation, repository):
        path.chmod(0o700)
    descriptors = tuple(os.open(path, os.O_RDONLY | os.O_DIRECTORY) for path in (parent, root, operation, repository))
    capability = None
    try:
        monkeypatch.setattr(fcntl, "fcntl", lambda *_args, **_kwargs: pytest.fail("capability must not recover a directory path"))
        monkeypatch.setattr(os, "readlink", lambda *_args, **_kwargs: pytest.fail("capability must not recover a directory path"))
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


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="Task 14 Linux mount-ID gate is intentionally fail-closed")
def test_precreated_transaction_capability_revalidates_bound_directory_entries_after_root_swap(tmp_path):
    parent = tmp_path / "parent"
    root = parent / "transaction-root"
    operation = root / "operation-d2"
    repository = tmp_path / "repository-authority"
    operation.mkdir(parents=True)
    repository.mkdir()
    for path in (parent, root, operation, repository):
        path.chmod(0o700)
    descriptors = tuple(os.open(path, os.O_RDONLY | os.O_DIRECTORY) for path in (parent, root, operation, repository))
    capability = None
    try:
        capability = RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
        capability.validate_open_identities()

        root.rename(parent / "replaced-root")
        (parent / "transaction-root" / "operation-d2").mkdir(parents=True)

        with pytest.raises(ValueError, match="directory.*identity|directory.*relationship"):
            capability.validate_open_identities()
    finally:
        if capability is not None:
            capability.close()
        for descriptor in descriptors:
            os.close(descriptor)


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="Task 14 Linux mount-ID gate is intentionally fail-closed")
def test_precreated_transaction_capability_rejects_unsafe_modes_and_descriptor_replacements_without_writes(tmp_path, monkeypatch):
    writes = fsyncs = 0

    def no_write(*_args, **_kwargs):
        nonlocal writes
        writes += 1
        raise AssertionError("capability validation must not write")

    def no_fsync(*_args, **_kwargs):
        nonlocal fsyncs
        fsyncs += 1
        raise AssertionError("capability validation must not fsync")

    monkeypatch.setattr(os, "write", no_write)
    monkeypatch.setattr(os, "fsync", no_fsync)

    def open_layout(base, *, root_name="transaction-root"):
        parent = base / "parent"
        root = parent / root_name
        operation = root / "operation-d2"
        repository = base / "repository-authority"
        operation.mkdir(parents=True)
        repository.mkdir(parents=True)
        for path in (parent, root, operation, repository):
            path.chmod(0o700)
        return parent, root, operation, repository, tuple(os.open(path, os.O_RDONLY | os.O_DIRECTORY) for path in (parent, root, operation, repository))

    parent, root, operation, repository, descriptors = open_layout(tmp_path / "good")
    capability = None
    try:
        capability = RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
        operation.rename(root / "replaced-operation")
        (root / "operation-d2").mkdir(parents=True)
        with pytest.raises(ValueError, match="directory.*identity|directory.*relationship"):
            capability.validate_open_identities()
    finally:
        if capability is not None:
            capability.close()
            with pytest.raises(ValueError):
                capability.validate_open_identities()
        for descriptor in descriptors:
            os.close(descriptor)

    parent, root, operation, repository, descriptors = open_layout(tmp_path / "symlink")
    capability = None
    try:
        capability = RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
        operation.rename(root / "real-operation")
        os.symlink("real-operation", root / "operation-d2")
        with pytest.raises(ValueError, match="directory.*identity|directory.*relationship"):
            capability.validate_open_identities()
    finally:
        if capability is not None:
            capability.close()
        for descriptor in descriptors:
            os.close(descriptor)

    parent, root, operation, repository, descriptors = open_layout(tmp_path / "independent-repository")
    try:
        capability = RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
        capability.validate_open_identities()
        capability.close()
        with pytest.raises(ValueError):
            RegistrationTransactionCapabilityV1.open_precreated(descriptors[0], descriptors[0], descriptors[2], descriptors[3])
    finally:
        for descriptor in descriptors:
            os.close(descriptor)

    parent, root, operation, repository, descriptors = open_layout(tmp_path / "unsafe-mode")
    try:
        for unsafe_mode in (0o755, 0o750, 0o711):
            parent.chmod(unsafe_mode)
            with pytest.raises(ValueError):
                RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
    finally:
        parent.chmod(0o700)
        for descriptor in descriptors:
            os.close(descriptor)

    parent, root, operation, repository, descriptors = open_layout(tmp_path / "repository-swap")
    capability = None
    try:
        capability = RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
        repository.rename(tmp_path / "repository-replaced")
        replacement = tmp_path / "repository-authority"
        replacement.mkdir()
        replacement.chmod(0o700)
        capability_fd = capability._fds[3]
        os.close(capability_fd)
        assert os.open(replacement, os.O_RDONLY | os.O_DIRECTORY) == capability_fd
        with pytest.raises(ValueError, match="directory.*identity"):
            capability.validate_open_identities()
    finally:
        if capability is not None:
            capability.close()
        for descriptor in descriptors:
            os.close(descriptor)

    assert writes == fsyncs == 0


def test_precreated_transaction_capability_labels_portable_filesystem_identity_and_fails_closed_for_linux_mount_identity(tmp_path):
    parent, root, operation = tmp_path / "parent", tmp_path / "parent" / "root", tmp_path / "parent" / "root" / "operation-d2"
    repository = tmp_path / "repository-authority"
    operation.mkdir(parents=True)
    repository.mkdir()
    for path in (parent, root, operation, repository):
        path.chmod(0o700)
    descriptors = tuple(os.open(path, os.O_RDONLY | os.O_DIRECTORY) for path in (parent, root, operation, repository))
    capability = None
    try:
        if sys.platform.startswith("linux"):
            with pytest.raises(ValueError, match="Task 14.*mount identity"):
                RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
        else:
            capability = RegistrationTransactionCapabilityV1.open_precreated(*descriptors)
            assert len(capability.filesystem_identities) == 4
            assert not hasattr(capability, "mount_identities")
    finally:
        if capability is not None:
            capability.close()
        for descriptor in descriptors:
            os.close(descriptor)


def test_lifecycle_completion_and_policy_journal_bind_full_forward_chain_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    proof = BrokerLifecycleProofV1("trusted-broker-lifecycle-proof/v1", "broker-key", "operation-1", "lifecycle-1", "lease-1", ProductGitObjectIdV1("sha1", "1" * 40), (NamedDigest("record-payload", DIGEST_A), NamedDigest("staged/report", DIGEST_B)), DIGEST_C, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, "a" * 128, DIGEST_A, 1, DIGEST_B, DIGEST_C, DIGEST_A, 1)
    proof.validate_staged_artifacts((NamedDigest("record-payload", DIGEST_A), NamedDigest("staged/report", DIGEST_B)))
    payload = Task26CompletionPayloadV1("ai-video-reverse-engineer", 26, BASE, BASE, BASE, "refs/heads/main", "pass", NamedDigest("review", DIGEST_A), NamedDigest("plan", DIGEST_B), NamedDigest("spec", DIGEST_C), "task26-review-controller/v1", "executor-1", DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A)
    record = Task26CompletionRecordV1("trusted-task-completion-record/v1", payload, "sha256:" + hashlib.sha256(payload.canonical_bytes).hexdigest(), "broker-key", "operation-1", "lifecycle-1", "b" * 128, DIGEST_A, 1, DIGEST_B, DIGEST_C, DIGEST_A, 1)
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


def test_lifecycle_and_completion_signatures_close_governance_before_durable_writes():
    """Tampering a broker-signed lifecycle or completion record never reaches a write."""
    recorder = VisibleAuthorizationWriteRecorder()
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    public_digest = "sha256:" + hashlib.sha256(public).hexdigest()
    allowed_signers_digest = "sha256:" + "d" * 64
    key = RegistrationBrokerAuthKeyRegistrationV1(
        "trusted-registration-broker-auth-key-registration/v1", "broker-fixed",
        "registration-broker-auth", "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "ai-video-reverse-engineer/registration-broker-lease/v1",
        "ai-video-reverse-engineer/registration-broker-startup-challenge/v1", "raw-ed25519-32",
        public_digest, public_digest, None, public_digest, public_digest, DIGEST_B, "active", 1, None,
    )
    registry = RegistrationBrokerAuthRegistryV1(
        "trusted-registration-broker-auth-registry/v1", 1, None, "broker-fixed", (key,)
    )
    revocations = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", registry.canonical_digest, ()
    )
    proof = BrokerLifecycleProofV1(
        "trusted-broker-lifecycle-proof/v1", "broker-fixed", "operation-1", "lifecycle-1", "lease-1",
        ProductGitObjectIdV1("sha1", "1" * 40),
        (NamedDigest("record-payload", DIGEST_A), NamedDigest("staged/report", DIGEST_B)),
        DIGEST_C, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, "0" * 128,
        broker_auth_public_key_digest=public_digest,
        broker_auth_registry_generation=registry.generation,
        broker_auth_registry_digest=registry.canonical_digest,
        broker_auth_revocation_digest=revocations.canonical_digest,
        broker_auth_allowed_signers_digest=allowed_signers_digest,
        broker_auth_sign_sequence=1,
    )
    proof = replace(
        proof,
        broker_signature=_ed_sign(
            seed,
            b"ai-video-reverse-engineer/broker-lifecycle-proof/v1\0" + proof.unsigned_bytes,
        ).hex(),
    )
    payload = Task26CompletionPayloadV1(
        "ai-video-reverse-engineer", 26, BASE, BASE, BASE, "refs/heads/main", "pass",
        NamedDigest("review", DIGEST_A), NamedDigest("plan", DIGEST_B), NamedDigest("spec", DIGEST_C),
        "task26-review-controller/v1", "executor-1", DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A,
    )
    record = Task26CompletionRecordV1(
        "trusted-task-completion-record/v1", payload,
        "sha256:" + hashlib.sha256(payload.canonical_bytes).hexdigest(), "broker-fixed", "operation-1", "lifecycle-1", "0" * 128,
        broker_auth_public_key_digest=public_digest,
        broker_auth_registry_generation=registry.generation,
        broker_auth_registry_digest=registry.canonical_digest,
        broker_auth_revocation_digest=revocations.canonical_digest,
        broker_auth_allowed_signers_digest=allowed_signers_digest,
        broker_auth_sign_sequence=1,
    )
    record = replace(
        record,
        broker_signature=_ed_sign(
            seed,
            b"ai-video-reverse-engineer/task26-completion-record/v1\0" + record.unsigned_bytes,
        ).hex(),
    )
    proof.verify_signature(registry, revocations, {"broker-fixed": public}, allowed_signers_digest, 1)
    record.verify_signature(registry, revocations, {"broker-fixed": public}, allowed_signers_digest, 1)
    recorder.assert_rejected(lambda: replace(proof, subject_commit=ProductGitObjectIdV1("sha1", "2" * 40)).verify_signature(
        registry, revocations, {"broker-fixed": public}, allowed_signers_digest, 1
    ))
    recorder.assert_rejected(lambda: replace(proof, staged_artifact_digests=(NamedDigest("record-payload", DIGEST_A), NamedDigest("staged/report", DIGEST_C))).verify_signature(
        registry, revocations, {"broker-fixed": public}, allowed_signers_digest, 1
    ))
    recorder.assert_rejected(lambda: replace(record, payload=replace(payload, reviewed_commit=GitObjectIdV1("sha1", "2" * 40)), payload_digest="sha256:" + hashlib.sha256(replace(payload, reviewed_commit=GitObjectIdV1("sha1", "2" * 40)).canonical_bytes).hexdigest()).verify_signature(
        registry, revocations, {"broker-fixed": public}, allowed_signers_digest, 1
    ))
    recorder.assert_rejected(lambda: proof.verify_signature(registry, revocations, {"broker-fixed": bytes(32)}, allowed_signers_digest, 1))
    recorder.assert_rejected(lambda: proof.verify_signature(registry, revocations, {"broker-fixed": public}, DIGEST_A, 1))
    revoked = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", registry.canonical_digest,
        (RegistrationBrokerAuthRevocationEntryV1("broker-fixed", 1, DIGEST_A),),
    )
    recorder.assert_rejected(lambda: proof.verify_signature(registry, revoked, {"broker-fixed": public}, allowed_signers_digest, 1))


# E.1 uses literals deliberately.  A production change that accepts an
# unlinked head, non-contiguous sequence, or a post-state field in an issue
# request must make this test fail before it can reach authorization writes.
def test_broker_auth_issue_chain_is_prestate_only_and_contiguous_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    governance, key = _broker_governance("broker-fixed", bytes(32))
    registry, revocations = governance.registry, governance.revocations
    policy_digest = _broker_policy_digest(registry, revocations)
    family = RegistrationWorkerSessionFamilyAuthorizationV1(
        "trusted-registration-worker-session-family-authorization/v1", "op-1", DIGEST_A,
        DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, "broker-fixed", key.canonical_digest,
        ("register", "recover"), "register-to-recover", DIGEST_C, DIGEST_A, DIGEST_B,
        DIGEST_C, DIGEST_A,
    )
    request = BrokerAuthLeaseIssueRequestV1(
        "trusted-registration-broker-auth-lease-issue-request/v1", "op-1", "lifecycle-1",
        family.canonical_digest, "broker-fixed", 0, DIGEST_A, registry.canonical_digest,
        revocations.canonical_digest, policy_digest, governance.allowed_signers_digest, 2,
    )
    payload = BrokerAuthLeaseIssueEventPayloadV1(
        "trusted-registration-broker-auth-lease-issue-payload/v1", "issue-lease", DIGEST_A,
        request, request.request_digest, "op-1", "broker-fixed", 1, 2, 3, policy_digest, policy_digest,
    )
    entry = BrokerAuthSequenceJournalEntryV1(
        "trusted-registration-broker-auth-sequence-entry/v1", 1, None, DIGEST_A, "issue-lease",
        payload.full_bytes_digest, len(payload.canonical_bytes), payload.full_bytes_digest, policy_digest, policy_digest,
        registry.canonical_digest, revocations.canonical_digest, "broker-fixed", "a" * 64,
    )
    journal = BrokerAuthSequenceJournalV1(
        "trusted-registration-broker-auth-sequence-journal/v1", DIGEST_A, 1, (entry,), "b" * 64
    )
    state = BrokerAuthSequenceStateV1(
        "trusted-registration-broker-auth-sequence-state/v1", DIGEST_A, 1, entry.canonical_digest,
        payload.full_bytes_digest, payload.full_bytes_digest, policy_digest, registry.canonical_digest,
        revocations.canonical_digest, "c" * 64,
    )
    mac_key = b"0123456789abcdef0123456789abcdef"
    authenticated_entry = replace(
        entry,
        broker_state_mac_hex=hmac.new(mac_key, json.dumps(entry._unsigned_wire(), separators=(",", ":"), sort_keys=True).encode(), hashlib.sha256).hexdigest(),
    )
    authenticated_entry.verify_state_mac(mac_key)
    with pytest.raises(ValueError):
        replace(authenticated_entry, broker_state_mac_hex="0" * 64).verify_state_mac(mac_key)
    journal.validate_state(
        state, {payload.full_bytes_digest: payload},
        BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
    )
    # These records cross the broker boundary as canonical bytes.  Removing a
    # strict decoder would make a tampered chain look like an in-memory value.
    for record_type, record in (
        (RegistrationWorkerSessionFamilyAuthorizationV1, family),
        (BrokerAuthLeaseIssueRequestV1, request),
        (BrokerAuthLeaseIssueEventPayloadV1, payload),
        (BrokerAuthSequenceJournalEntryV1, entry),
        (BrokerAuthSequenceJournalV1, journal),
        (BrokerAuthSequenceStateV1, state),
    ):
        assert record_type.from_canonical_bytes(record.canonical_bytes) == record
        with pytest.raises(ValueError):
            record_type.from_canonical_bytes(record.canonical_bytes[:-1] + b',"unexpected":null}')
        with pytest.raises(ValueError):
            record_type.from_canonical_bytes(record.canonical_bytes.replace(b'{', b'{"schema":"duplicate",', 1))
        with pytest.raises(ValueError):
            record_type.from_canonical_bytes(record.canonical_bytes + b"\n")
    recorder.assert_rejected(lambda: BrokerAuthLeaseIssueRequestV1(
        "trusted-registration-broker-auth-lease-issue-request/v1", "op-1", "lifecycle-1",
        family.canonical_digest, "broker-fixed", 1, entry.canonical_digest, registry.canonical_digest,
        revocations.canonical_digest, policy_digest, DIGEST_C, 0,
    ))
    recorder.assert_rejected(lambda: BrokerAuthSequenceJournalV1(
        "trusted-registration-broker-auth-sequence-journal/v1", DIGEST_A, 1,
        (BrokerAuthSequenceJournalEntryV1(
            "trusted-registration-broker-auth-sequence-entry/v1", 2, None, DIGEST_A, "issue-lease",
            payload.full_bytes_digest, len(payload.canonical_bytes), payload.full_bytes_digest, policy_digest, policy_digest,
            registry.canonical_digest, revocations.canonical_digest, "broker-fixed", "a" * 64,
        ),), "b" * 64,
    ))


def test_broker_sequence_payload_and_entry_reject_the_fixed_256_kib_budget_before_decode():
    """Every typed payload constructor and decoder enforces the same 256 KiB cap."""
    boundary = BrokerAuthEnvelopeSignEventPayloadV1(
        "trusted-registration-broker-auth-envelope-sign-payload/v1", "sign-envelope",
        DIGEST_A, "x", DIGEST_B, DIGEST_C, DIGEST_A, "broker-fixed", 1, DIGEST_B, DIGEST_B,
    )
    boundary = BrokerAuthEnvelopeSignEventPayloadV1(
        "trusted-registration-broker-auth-envelope-sign-payload/v1", "sign-envelope",
        DIGEST_A, "x" * (256 * 1024 - len(boundary.canonical_bytes) + 1),
        DIGEST_B, DIGEST_C, DIGEST_A, "broker-fixed", 1, DIGEST_B, DIGEST_B,
    )
    assert len(boundary.canonical_bytes) == 256 * 1024
    oversized_text = "o" * (256 * 1024)
    governance, key = _broker_governance("broker-fixed", bytes(32))
    registry, revocations = governance.registry, governance.revocations
    policy_digest = canonical_digest({
        "registryDigest": registry.canonical_digest,
        "revocationsDigest": revocations.canonical_digest,
        "schema": "trusted-registration-broker-auth-policy-state/v1",
    })
    family = RegistrationWorkerSessionFamilyAuthorizationV1(
        "trusted-registration-worker-session-family-authorization/v1", oversized_text,
        DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, "broker-fixed",
        key.canonical_digest, ("register", "recover"), "register-to-recover", DIGEST_C,
        DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A,
    )
    request = BrokerAuthLeaseIssueRequestV1(
        "trusted-registration-broker-auth-lease-issue-request/v1", oversized_text,
        "lifecycle-1", family.canonical_digest, "broker-fixed", 0, DIGEST_A,
        registry.canonical_digest, revocations.canonical_digest, policy_digest,
        governance.allowed_signers_digest, 2,
    )
    old_governance, old_key = _broker_governance("old", b"1" * 32)
    large_governance, large_key = _broker_governance(oversized_text, b"2" * 32)
    old_registry, old_revocations = old_governance.registry, old_governance.revocations
    next_registry = RegistrationBrokerAuthRegistryV1(
        "trusted-registration-broker-auth-registry/v1", 2, old_registry.canonical_digest,
        oversized_text,
        tuple(sorted((
            replace(large_key, effective_lease_sequence=2),
            replace(old_key, state="retiring", retire_after_lease_sequence=2),
        ), key=lambda item: item.key_id.encode("utf-8"))),
    )
    next_revocations = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", next_registry.canonical_digest, (),
    )
    rotate_pre = canonical_digest({
        "registryDigest": old_registry.canonical_digest,
        "revocationsDigest": old_revocations.canonical_digest,
        "schema": "trusted-registration-broker-auth-policy-state/v1",
    })
    rotate_post = canonical_digest({
        "registryDigest": next_registry.canonical_digest,
        "revocationsDigest": next_revocations.canonical_digest,
        "schema": "trusted-registration-broker-auth-policy-state/v1",
    })
    revoked_governance, _ = _broker_governance(oversized_text, b"3" * 32)
    revoked_registry, revoked_before = revoked_governance.registry, revoked_governance.revocations
    revoked_after = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", revoked_registry.canonical_digest,
        (RegistrationBrokerAuthRevocationEntryV1(oversized_text, 1, DIGEST_A),),
    )
    revoke_pre = canonical_digest({
        "registryDigest": revoked_registry.canonical_digest,
        "revocationsDigest": revoked_before.canonical_digest,
        "schema": "trusted-registration-broker-auth-policy-state/v1",
    })
    revoke_post = canonical_digest({
        "registryDigest": revoked_registry.canonical_digest,
        "revocationsDigest": revoked_after.canonical_digest,
        "schema": "trusted-registration-broker-auth-policy-state/v1",
    })
    constructors = {
        "issue": lambda: BrokerAuthLeaseIssueEventPayloadV1(
            "trusted-registration-broker-auth-lease-issue-payload/v1", "issue-lease", DIGEST_A,
            request, request.request_digest, oversized_text, "broker-fixed", 1, 2, 3,
            policy_digest, policy_digest,
        ),
        "sign": lambda: BrokerAuthEnvelopeSignEventPayloadV1(
            "trusted-registration-broker-auth-envelope-sign-payload/v1", "sign-envelope",
            DIGEST_A, oversized_text, DIGEST_B, DIGEST_C, DIGEST_A, "broker-fixed", 1,
            DIGEST_B, DIGEST_B,
        ),
            "rotate": lambda: BrokerAuthRotatePolicyEventPayloadV1(
                "trusted-registration-broker-auth-rotate-payload/v1", "rotate-registry", DIGEST_A,
                old_registry.canonical_digest, old_revocations.canonical_digest, large_governance,
                oversized_text, "old", 2, rotate_pre, rotate_post,
        ),
        "revoke": lambda: BrokerAuthRevokePolicyEventPayloadV1(
            "trusted-registration-broker-auth-revoke-payload/v1", "revoke-key", DIGEST_A,
            revoked_registry.canonical_digest, revoked_before.canonical_digest, revoked_after,
            oversized_text, 1, revoke_pre, revoke_post,
        ),
    }
    for constructor in constructors.values():
        with pytest.raises(ValueError):
            constructor()
    oversized_wire = b"{" + b" " * (256 * 1024) + b"}"
    for payload_type in (
        BrokerAuthLeaseIssueEventPayloadV1,
        BrokerAuthEnvelopeSignEventPayloadV1,
        BrokerAuthRotatePolicyEventPayloadV1,
        BrokerAuthRevokePolicyEventPayloadV1,
    ):
        with pytest.raises(ValueError, match="too large"):
            payload_type.from_canonical_bytes(oversized_wire)
    with pytest.raises(ValueError, match="256 KiB"):
        BrokerAuthSequenceJournalEntryV1(
            "trusted-registration-broker-auth-sequence-entry/v1", 1, None, DIGEST_A,
            "sign-envelope", DIGEST_B, 256 * 1024 + 1, DIGEST_C, DIGEST_A,
            DIGEST_A, DIGEST_B, DIGEST_C, "broker-fixed", "a" * 64,
        )


def test_broker_sequence_sparse_oversize_carrier_is_rejected_before_content_read(tmp_path, monkeypatch):
    """The codec's future durable reader receives no bytes from an oversize carrier."""
    carrier = tmp_path / "oversize-sequence-payload"
    carrier.write_bytes(b"x")
    os.truncate(carrier, 256 * 1024 + 1)
    fd = os.open(carrier, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        monkeypatch.setattr(os, "pread", lambda *_args: pytest.fail("oversize payload was read"))
        with pytest.raises(ValueError, match="too large"):
            _read_bounded_broker_sequence_payload_fd(fd)
    finally:
        os.close(fd)


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


def test_signed_lease_wire_is_canonical_and_respects_expiry_and_global_revocation():
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    governance, key = _broker_governance("broker-fixed", public)
    registry, revocations = governance.registry, governance.revocations
    template = RegistrationRecoveryLeaseV1(
        "trusted-registration-recovery-lease/v1", "ai-video-reverse-engineer", "op-lease", "life",
        DIGEST_A, 1, DIGEST_A, "transaction-root", DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B,
        DIGEST_C, DIGEST_A, DIGEST_B, DIGEST_C, 1, registry.canonical_digest, revocations.canonical_digest, DIGEST_B, governance.allowed_signers_digest, "broker-fixed",
        DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, 1, 3, DIGEST_C, "OPEN", "a" * 128,
        "b" * 64,
    )
    lease = replace(template, lease_identity_digest=template.identity_preimage().lease_identity_digest)
    # Private test seed enters only a temporary read-only FD, mirroring the
    # root-broker slot boundary rather than a serializable signer object.
    import tempfile
    with tempfile.NamedTemporaryFile() as stream:
        stream.write(seed); stream.flush()
        descriptor = os.open(stream.name, os.O_RDONLY)
        try:
            signer = RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, key, public)
        finally:
            os.close(descriptor)
        try:
            lease = replace(lease, broker_signature_hex=signer.sign("ai-video-reverse-engineer/registration-broker-lease/v1", json.dumps(lease._unsigned_wire(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")))
        finally:
            signer.close()
    assert RegistrationRecoveryLeaseV1.from_canonical_bytes(lease.canonical_bytes) == lease
    lease.verify_signature(governance, 3)
    with pytest.raises(ValueError):
        RegistrationRecoveryLeaseV1.from_canonical_bytes(lease.canonical_bytes + b"\n")
    with pytest.raises(ValueError):
        lease.verify_signature(governance, 4)
    with pytest.raises(ValueError):
        replace(lease, broker_signature_hex="0" * 128).verify_signature(governance, 3)


def test_authoritative_broker_issuer_commits_a_verified_issue_chain_and_preserves_state_on_rejection(tmp_path):
    """Issuer derives event IDs and rejects mixed key domains, governance, family and TTL."""
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    governance, key = _broker_governance("broker-fixed", public)
    registry, revocations = governance.registry, governance.revocations
    family = RegistrationWorkerSessionFamilyAuthorizationV1(
        "trusted-registration-worker-session-family-authorization/v1", "op-issuer", DIGEST_A,
        DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, "broker-fixed", key.canonical_digest,
        ("register", "recover"), "register-to-recover", DIGEST_C, DIGEST_A, DIGEST_B,
        DIGEST_C, DIGEST_A,
    )
    policy_digest = _broker_policy_digest(registry, revocations)
    request = BrokerAuthLeaseIssueRequestV1(
        "trusted-registration-broker-auth-lease-issue-request/v1", "op-issuer", "lifecycle-issuer",
        family.canonical_digest, "broker-fixed", 0, DIGEST_A, registry.canonical_digest,
        revocations.canonical_digest, policy_digest, governance.allowed_signers_digest, 2,
    )
    template = RegistrationRecoveryLeaseV1(
        "trusted-registration-recovery-lease/v1", "ai-video-reverse-engineer", "op-issuer", "lifecycle-issuer",
        family.canonical_digest, 1, DIGEST_A, "transaction-root", DIGEST_B, DIGEST_C, DIGEST_A,
        DIGEST_B, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, 1, registry.canonical_digest,
        revocations.canonical_digest, policy_digest, governance.allowed_signers_digest, "broker-fixed", request.request_digest,
        DIGEST_A, DIGEST_B, DIGEST_C, 1, 3, DIGEST_A, "OPEN", "a" * 128, "b" * 64,
    )
    key_file = tmp_path / "broker-key"
    key_file.write_bytes(seed)
    descriptor = os.open(key_file, os.O_RDONLY)
    try:
        signer = RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, key, public)
    finally:
        os.close(descriptor)
    try:
        state_mac = _state_mac_capability(tmp_path, b"state-mac-key-is-independent-32!!!"[:32])
        with pytest.raises(ValueError):
            BrokerAuthAuthoritativeIssuerV1.create(
                governance, DIGEST_A, DIGEST_A, signer, state_mac, 2,
            )
        issuer = BrokerAuthAuthoritativeIssuerV1.create(
            governance, DIGEST_A, policy_digest, signer, state_mac, 2
        )
        before = issuer.state
        with pytest.raises(ValueError):
            issuer.issue_lease(replace(request, expected_current_head_digest=DIGEST_B), family, template)
        assert issuer.state == before and issuer.entries == () and issuer.payloads == {}

        with pytest.raises(ValueError):
            issuer.issue_lease(replace(request, lease_ttl_sequences=3), family, template)
        with pytest.raises(ValueError):
            issuer.issue_lease(request, family, replace(template, state="RECLAIMED", next_recovery_sequence=999))
        assert issuer.state == before and issuer.entries == () and issuer.payloads == {}
        with pytest.raises(ValueError):
            issuer.issue_lease(request, family, replace(template, executor_identity_digest=DIGEST_A))
        issued = issuer.issue_lease(request, family, template)
        assert issued.entry.sequence == issued.payload.issued_sequence == issued.state.current_sequence == 1
        assert issued.payload.event_id_digest == "sha256:" + hashlib.sha256(
            b"ai-video-reverse-engineer/registration-broker-lease-issue-event/v1\0" + request.request_digest.encode("ascii")
        ).hexdigest()
        assert issued.entry.event_payload_ref == "sha256:" + hashlib.sha256(issued.payload.canonical_bytes).hexdigest()
        issued.entry.verify_state_mac(b"state-mac-key-is-independent-32!!!"[:32])
        issued.state.verify_state_mac(b"state-mac-key-is-independent-32!!!"[:32])
        issued.journal.verify_state_mac(b"state-mac-key-is-independent-32!!!"[:32])
        with pytest.raises(ValueError):
            issued.entry.verify_state_mac(seed)
        issued.journal.validate_state(
            issued.state, issued.payloads,
            BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
        )
        # Payload storage is the exact replay closure, not a bag of otherwise
        # well-formed content-addressed values.  The second payload belongs to
        # a different event and is therefore an orphan for this one-entry
        # journal.
        orphan_payload = replace(issued.payload, event_id_digest=DIGEST_B)
        with pytest.raises(ValueError, match="payload"):
            issued.journal.validate_state(
                issued.state,
                {**issued.payloads, orphan_payload.full_bytes_digest: orphan_payload},
                BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
            )
        duplicate_ref_entry = replace(
            issued.entry, sequence=2, previous_entry_digest=issued.entry.canonical_digest,
            broker_state_mac_hex="0" * 64,
        )
        duplicate_ref_entry = replace(
            duplicate_ref_entry,
            broker_state_mac_hex=state_mac.mac(canonical_json_bytes(duplicate_ref_entry._unsigned_wire())),
        )
        duplicate_ref_journal = replace(
            issued.journal, entries=(issued.entry, duplicate_ref_entry), broker_state_mac_hex="0" * 64,
        )
        duplicate_ref_journal = replace(
            duplicate_ref_journal,
            broker_state_mac_hex=state_mac.mac(canonical_json_bytes(duplicate_ref_journal._unsigned_wire())),
        )
        duplicate_ref_state = replace(
            issued.state, current_sequence=2, head_entry_digest=duplicate_ref_entry.canonical_digest,
            broker_state_mac_hex="0" * 64,
        )
        duplicate_ref_state = replace(
            duplicate_ref_state,
            broker_state_mac_hex=state_mac.mac(canonical_json_bytes(duplicate_ref_state._unsigned_wire())),
        )
        with pytest.raises(ValueError, match="exactly close"):
            duplicate_ref_journal.validate_state(
                duplicate_ref_state, issued.payloads,
                BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
            )
        issued.lease.verify_signature(governance, 1)
        assert issued.lease.broker_auth_lease_request_digest == request.request_digest
        assert issued.lease.lease_identity_digest == issued.lease.identity_preimage().lease_identity_digest

        signed = issuer.sign_envelope(
            "op-issuer", issued.lease.lease_identity_digest, request.request_digest, DIGEST_B, DIGEST_C
        )
        assert signed.entry.sequence == signed.payload.sign_sequence == signed.state.current_sequence == 2
        signed.journal.validate_state(
            signed.state, signed.payloads,
            BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
        )

        stable = issuer.state
        with pytest.raises(ValueError):
            issuer.issue_lease(request, family, template)
        assert issuer.state == stable and len(issuer.entries) == 2
        with pytest.raises(ValueError):
            issuer.issue_lease(replace(request, expected_current_sequence=1, expected_current_head_digest=stable.head_entry_digest), family, template)
        assert issuer.state == stable and len(issuer.entries) == 2
    finally:
        state_mac.close()
        signer.close()


def test_authoritative_broker_issuer_rotates_and_revokes_without_partial_state(tmp_path):
    """Rotation changes the sole issuer; revoke stops the named key at its event sequence."""
    seeds = (
        bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"),
        bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"),
    )
    publics = (
        bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"),
        bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"),
    )
    governance, old_key = _broker_governance("old", publics[0])
    registry, revocations = governance.registry, governance.revocations
    path = tmp_path / "old-key"; path.write_bytes(seeds[0])
    descriptor = os.open(path, os.O_RDONLY)
    try:
        old_signer = RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, old_key, publics[0])
    finally:
        os.close(descriptor)
    new_signer = None
    try:
        state_mac = _state_mac_capability(tmp_path, b"state-mac-key-is-independent-32!!!"[:32])
        issuer = BrokerAuthAuthoritativeIssuerV1.create(
            governance, DIGEST_A, _broker_policy_digest(registry, revocations), old_signer, state_mac, 2,
        )
        family = RegistrationWorkerSessionFamilyAuthorizationV1(
            "trusted-registration-worker-session-family-authorization/v1", "op-retiring", DIGEST_A,
            DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, "old", old_key.canonical_digest,
            ("register", "recover"), "register-to-recover", DIGEST_C, DIGEST_A, DIGEST_B,
            DIGEST_C, DIGEST_A,
        )
        policy_digest = _broker_policy_digest(registry, revocations)
        request = BrokerAuthLeaseIssueRequestV1(
            "trusted-registration-broker-auth-lease-issue-request/v1", "op-retiring", "lifecycle-retiring",
            family.canonical_digest, "old", 0, DIGEST_A, registry.canonical_digest,
            revocations.canonical_digest, policy_digest, governance.allowed_signers_digest, 2,
        )
        template = RegistrationRecoveryLeaseV1(
            "trusted-registration-recovery-lease/v1", "ai-video-reverse-engineer", "op-retiring", "lifecycle-retiring",
            family.canonical_digest, 1, DIGEST_A, "transaction-root", DIGEST_B, DIGEST_C, DIGEST_A,
            DIGEST_B, DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, registry.generation, registry.canonical_digest,
            revocations.canonical_digest, policy_digest, governance.allowed_signers_digest, "old", request.request_digest,
            DIGEST_A, DIGEST_B, DIGEST_C, 1, 3, DIGEST_A, "OPEN", "a" * 128, "b" * 64,
        )
        issued = issuer.issue_lease(request, family, template)
        def next_governance():
            entries = []
            for key_id, public in (("new", publics[1]), ("old", publics[0])):
                encoded = base64.b64encode(public).decode("ascii")
                entries.append({"keyId": key_id, "purpose": "registration-broker-auth", "rawPublicKey": encoded, "resolvedPublicKey": encoded, "signatureDomain": "ai-video-reverse-engineer/registration-broker-envelope/v1", "wrapperIdentity": "test-broker-wrapper-v1"})
            allowed = {"keys": entries}
            allowed_bytes = canonical_json_bytes(allowed)
            def registered(entry, state, effective_sequence, retire_after):
                digest = "sha256:" + hashlib.sha256(base64.b64decode(entry["rawPublicKey"])).hexdigest()
                return RegistrationBrokerAuthKeyRegistrationV1("trusted-registration-broker-auth-key-registration/v1", entry["keyId"], "registration-broker-auth", "ai-video-reverse-engineer/registration-broker-envelope/v1", "ai-video-reverse-engineer/registration-broker-lease/v1", "ai-video-reverse-engineer/registration-broker-startup-challenge/v1", "raw-ed25519-32", digest, digest, "sha256:" + hashlib.sha256(b"test-broker-wrapper-v1").hexdigest(), digest, digest, "sha256:" + hashlib.sha256(canonical_json_bytes(entry)).hexdigest(), state, effective_sequence, retire_after)
            new_key, retired_old = registered(entries[0], "active", 2, None), registered(entries[1], "retiring", 1, 2)
            next_registry = RegistrationBrokerAuthRegistryV1("trusted-registration-broker-auth-registry/v1", 2, registry.canonical_digest, "new", (new_key, retired_old))
            next_revocations = RegistrationBrokerAuthRevocationsV1("trusted-registration-broker-auth-revocations/v1", next_registry.canonical_digest, ())
            return RegistrationBrokerAuthGovernanceV1.from_canonical_bytes(next_registry.canonical_bytes, next_revocations.canonical_bytes, allowed_bytes), new_key, next_registry, next_revocations
        next_governance_value, new_key, next_registry, next_revocations = next_governance()
        before = issuer.state
        next_policy_digest = _broker_policy_digest(next_registry, next_revocations)
        bad_registry = replace(next_registry, previous_registry_digest=DIGEST_C)
        bad_revocations = RegistrationBrokerAuthRevocationsV1(
            "trusted-registration-broker-auth-revocations/v1", bad_registry.canonical_digest, (),
        )
        bad_governance = RegistrationBrokerAuthGovernanceV1.from_canonical_bytes(
            bad_registry.canonical_bytes, bad_revocations.canonical_bytes,
            next_governance_value.allowed_signers_canonical_bytes,
        )
        bad_payload = BrokerAuthRotatePolicyEventPayloadV1(
            "trusted-registration-broker-auth-rotate-payload/v1", "rotate-registry", DIGEST_C,
            DIGEST_C, revocations.canonical_digest, bad_governance, "new", "old", 2,
            issuer.state.current_policy_digest, _broker_policy_digest(bad_registry, bad_revocations),
        )
        bad_entry = BrokerAuthSequenceJournalEntryV1(
            "trusted-registration-broker-auth-sequence-entry/v1", 2, issued.entry.canonical_digest,
            bad_payload.event_id_digest, bad_payload.event_kind, bad_payload.full_bytes_digest,
            len(bad_payload.canonical_bytes), bad_payload.full_bytes_digest,
            bad_payload.pre_policy_digest, bad_payload.post_policy_digest,
            bad_registry.canonical_digest, bad_revocations.canonical_digest, "old", "0" * 64,
        )
        bad_entry = replace(
            bad_entry,
            broker_state_mac_hex=state_mac.mac(canonical_json_bytes(bad_entry._unsigned_wire())),
        )
        bad_state = BrokerAuthSequenceStateV1(
            "trusted-registration-broker-auth-sequence-state/v1", DIGEST_A, 2,
            bad_entry.canonical_digest, bad_payload.full_bytes_digest, bad_payload.full_bytes_digest,
            bad_payload.post_policy_digest, bad_registry.canonical_digest,
            bad_revocations.canonical_digest, "0" * 64,
        )
        bad_state = replace(
            bad_state,
            broker_state_mac_hex=state_mac.mac(canonical_json_bytes(bad_state._unsigned_wire())),
        )
        bad_journal = BrokerAuthSequenceJournalV1(
            "trusted-registration-broker-auth-sequence-journal/v1", DIGEST_A, 1,
            (issued.entry, bad_entry), "0" * 64,
        )
        bad_journal = replace(
            bad_journal,
            broker_state_mac_hex=state_mac.mac(canonical_json_bytes(bad_journal._unsigned_wire())),
        )
        with pytest.raises(ValueError, match="rotation"):
            bad_journal.validate_state(
                bad_state,
                {issued.payload.full_bytes_digest: issued.payload, bad_payload.full_bytes_digest: bad_payload},
                BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
            )
        with pytest.raises(ValueError):
            issuer.rotate_registry(governance, DIGEST_A)
        assert issuer.state == before and issuer.entries == (issued.entry,)
        rotated = issuer.rotate_registry(next_governance_value, DIGEST_A)
        assert rotated.entry.sequence == rotated.payload.effective_lease_sequence == 2
        assert issuer.registry == next_registry and issuer.revocations == next_revocations

        def assert_replay_rejects_forged_event_signer(result, forged_key_id):
            forged_entry = replace(result.entry, broker_auth_key_id=forged_key_id, broker_state_mac_hex="0" * 64)
            forged_entry = replace(
                forged_entry,
                broker_state_mac_hex=state_mac.mac(canonical_json_bytes(forged_entry._unsigned_wire())),
            )
            forged_journal = replace(
                result.journal, entries=(*result.journal.entries[:-1], forged_entry), broker_state_mac_hex="0" * 64,
            )
            forged_journal = replace(
                forged_journal,
                broker_state_mac_hex=state_mac.mac(canonical_json_bytes(forged_journal._unsigned_wire())),
            )
            forged_state = replace(result.state, head_entry_digest=forged_entry.canonical_digest, broker_state_mac_hex="0" * 64)
            forged_state = replace(
                forged_state,
                broker_state_mac_hex=state_mac.mac(canonical_json_bytes(forged_state._unsigned_wire())),
            )
            with pytest.raises(ValueError, match="broker auth"):
                forged_journal.validate_state(
                    forged_state, result.payloads,
                    BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
                )

        # These entries retain valid entry/state/journal MACs.  Replay must
        # bind the recorded signer to the active pre-state key, rather than
        # treating it as unauthenticated audit text.
        assert_replay_rejects_forged_event_signer(rotated, "unknown-key")
        assert_replay_rejects_forged_event_signer(rotated, "new")

        new_path = tmp_path / "new-key"; new_path.write_bytes(seeds[1])
        descriptor = os.open(new_path, os.O_RDONLY)
        try:
            new_signer = RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, new_key, publics[1])
        finally:
            os.close(descriptor)
        issuer.replace_signer(new_signer, next_governance_value)

        recovery_lease = replace(issued.lease, state="RECOVERY_RUNNING", next_recovery_sequence=2)
        recovery_lease = replace(
            recovery_lease,
            broker_signature_hex=old_signer.sign(
                "ai-video-reverse-engineer/registration-broker-lease/v1",
                canonical_json_bytes(recovery_lease._unsigned_wire()),
            ),
        )
        recovery_lease.verify_signature(next_governance_value, issuer.state.current_sequence)
        recovery_descriptors = (
            _unsigned_envelope("recover").fd_descriptors
            if sys.platform == "darwin" else _broker_produced_closure(tmp_path, "recover")
        )
        unsigned_recovery = _unsigned_envelope(
            "recover", broker_lease_identity_digest=recovery_lease.lease_identity_digest,
            operation_id=recovery_lease.operation_id, lifecycle_nonce=recovery_lease.lifecycle_nonce,
            worker_sequence=2, broker_auth_key_id="old", broker_auth_public_key_digest=old_signer.public_key_digest,
            broker_auth_registry_generation=next_registry.generation,
            broker_auth_registry_digest=next_registry.canonical_digest,
            broker_auth_revocation_digest=next_revocations.canonical_digest,
            broker_auth_allowed_signers_digest=next_governance_value.allowed_signers_digest,
            broker_auth_lease_issued_sequence=recovery_lease.broker_auth_lease_issued_sequence,
            broker_auth_lease_expiry_sequence=recovery_lease.broker_auth_lease_expiry_sequence,
            broker_auth_sign_sequence=3, fd_descriptors=recovery_descriptors,
        )
        if sys.platform == "darwin":
            with pytest.raises(ValueError, match="broker-produced"):
                issuer.sign_recovery_envelope(recovery_lease, unsigned_recovery, old_signer, DIGEST_C)
        else:
            signed_recovery = issuer.sign_recovery_envelope(
                recovery_lease, unsigned_recovery, old_signer, DIGEST_C,
            )
            assert signed_recovery.transition.payload.broker_auth_key_id == "old"
            assert signed_recovery.envelope.unsigned == unsigned_recovery
            RegistrationExpectedReceiveContextV1(
                "recover", DIGEST_C, DIGEST_B, DIGEST_A, _worker_input().canonical_digest,
                "session-e2", "op-retiring", "lifecycle-retiring", 2, "broker-session-e2",
                recovery_lease, 3, next_governance_value,
            ).verify_envelope(signed_recovery.envelope)
        stable = issuer.state
        with pytest.raises(ValueError):
            issuer.sign_recovery_envelope(recovery_lease, unsigned_recovery, new_signer, DIGEST_B)
        assert issuer.state == stable

        # This is the liveness half of rotation: the new active signer must
        # issue and then record a new envelope against the replayed closure.
        new_family = replace(family, operation_id="op-after-rotation", broker_auth_key_id="new", broker_auth_key_registration_digest=new_key.canonical_digest)
        new_request = BrokerAuthLeaseIssueRequestV1("trusted-registration-broker-auth-lease-issue-request/v1", "op-after-rotation", "lifecycle-after-rotation", new_family.canonical_digest, "new", issuer.state.current_sequence, issuer.state.head_entry_digest, next_registry.canonical_digest, next_revocations.canonical_digest, issuer.state.current_policy_digest, next_governance_value.allowed_signers_digest, 2)
        new_template = replace(template, operation_id="op-after-rotation", lifecycle_nonce="lifecycle-after-rotation", session_family_authorization_digest=new_family.canonical_digest, broker_auth_lease_request_digest=new_request.request_digest)
        post_rotation = issuer.issue_lease(new_request, new_family, new_template)
        assert post_rotation.lease.broker_auth_key_id == "new"
        signed_new = issuer.sign_envelope(post_rotation.lease.operation_id, post_rotation.lease.lease_identity_digest, new_request.request_digest, DIGEST_B, "sha256:" + "d" * 64)
        signed_new.journal.validate_state(signed_new.state, signed_new.payloads, BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest))

        stale = issuer.state
        with pytest.raises(ValueError):
            issuer.revoke_key("not-registered", next_revocations, DIGEST_B, DIGEST_A)
        assert issuer.state == stale and issuer.revocations == next_revocations
        revocation_sequence = issuer.state.current_sequence + 1
        revoked = RegistrationBrokerAuthRevocationsV1(
            "trusted-registration-broker-auth-revocations/v1", next_registry.canonical_digest,
            (RegistrationBrokerAuthRevocationEntryV1("old", revocation_sequence, DIGEST_A),),
        )
        with pytest.raises(ValueError):
            issuer.revoke_key("old", revoked, DIGEST_B, DIGEST_A)
        assert issuer.state == stale and issuer.revocations == next_revocations
        result = issuer.revoke_key("old", revoked, DIGEST_B, _broker_policy_digest(next_registry, revoked))
        assert result.entry.sequence == result.payload.revoked_at_broker_sequence == revocation_sequence
        assert issuer.revocations == revoked
        assert_replay_rejects_forged_event_signer(result, "old")
        revoked_active = RegistrationBrokerAuthRevocationsV1(
            "trusted-registration-broker-auth-revocations/v1", next_registry.canonical_digest,
            (*revoked.entries, RegistrationBrokerAuthRevocationEntryV1("new", revocation_sequence + 1, DIGEST_B)),
        )
        second_revoke = issuer.revoke_key(
            "new", revoked_active, DIGEST_C, _broker_policy_digest(next_registry, revoked_active),
        )
        assert_replay_rejects_forged_event_signer(second_revoke, "old")
    finally:
        state_mac.close()
        if new_signer is not None:
            new_signer.close()
        old_signer.close()


# E.2: these fixtures deliberately do not consult the codec under test to
# derive names, ordering, or expected wire fields.  A production change which
# accepts an altered closure must fail these boundary assertions.
_REGISTER_CLOSURE = (
    ("worker-input", "registration-worker-input", "worker-input-inbox"),
    ("input-snapshot", "product-handoff-cache", "worker-input-inbox"),
    ("input-snapshot", "policy-handoff-cache", "worker-input-inbox"),
    ("input-snapshot", "task-output-policy", "worker-input-inbox"),
    ("input-snapshot", "e1.ticket23.toonflow-source.v1", "worker-input-inbox"),
    ("input-snapshot", "e1.ticket23.product-adaptation.v1", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.backend-persistence.v2", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.frontend-persistence.v2", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.api.v2", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.canvas-structure.v2", "worker-input-inbox"),
    ("input-snapshot", "e4.ticket23.canvas-review.v1", "worker-input-inbox"),
    ("toolchain-build-record", "registration-toolchain-build-record", "trusted-toolchain-root"),
    ("toolchain-artifact", "registration-launcher-runtime-closure", "trusted-toolchain-root"),
    ("broker-control", "registration-mutation-control", None),
)

_RECOVER_CLOSURE = (
    ("worker-input", "registration-worker-input", "recovery-inbox"),
    ("transaction-record", "trusted-registration-pre-state", "transaction-operation-root"),
    ("transaction-record", "staged-candidate-set", "transaction-operation-root"),
    ("transaction-record", "quarantine-object-set", "transaction-operation-root"),
    ("transaction-record", "repository-recovery-binding", "transaction-operation-root"),
    ("transaction-record", "registration-transaction-intent", "transaction-operation-root"),
    ("transaction-record", "registration-plan", "transaction-operation-root"),
    ("transaction-record", "registration-journal-head", "transaction-operation-root"),
    ("quarantine-object", "planned-catalog", "transaction-quarantine-root"),
    ("quarantine-object", "registration-audit", "transaction-quarantine-root"),
    ("quarantine-object", "e1.ticket23.toonflow-source.v1", "transaction-quarantine-root"),
    ("quarantine-object", "e1.ticket23.product-adaptation.v1", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.backend-persistence.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.frontend-persistence.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.api.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.canvas-structure.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e4.ticket23.canvas-review.v1", "transaction-quarantine-root"),
    ("toolchain-build-record", "registration-toolchain-build-record", "trusted-toolchain-root"),
    ("toolchain-artifact", "registration-launcher-runtime-closure", "trusted-toolchain-root"),
    ("broker-control", "registration-mutation-control", None),
)


def _fd_descriptor(ordinal, entry):
    role, logical_name, root_role = entry
    if role == "broker-control":
        provenance = FdOpenProvenanceV1("broker-created-seqpacket", None, None, None, ("SOCK_CLOEXEC", "SOCK_SEQPACKET"), (), None)
        observable_wire = {"accessMode": "bidirectional-control", "byteLength": None, "contentDigest": None, "device": None, "fdFlags": ["FD_CLOEXEC"], "fileType": "socket-seqpacket", "identityDigest": DIGEST_B, "inode": None, "mountIdentityDigest": None, "socketIdentityDigest": DIGEST_A, "socketOptions": ["SO_PASSCRED=1", "SO_TYPE=SOCK_SEQPACKET"], "statusFlags": []}
    else:
        provenance = FdOpenProvenanceV1("openat2-beneath-no-symlinks", root_role, DIGEST_A, DIGEST_B, ("O_CLOEXEC", "O_NOFOLLOW", "O_RDONLY"), ("RESOLVE_BENEATH", "RESOLVE_NO_MAGICLINKS", "RESOLVE_NO_SYMLINKS", "RESOLVE_NO_XDEV"), DIGEST_C)
        observable_wire = {"accessMode": "read-only", "byteLength": 7, "contentDigest": DIGEST_A, "device": 1, "fdFlags": ["FD_CLOEXEC"], "fileType": "regular", "identityDigest": DIGEST_C, "inode": ordinal + 100, "mountIdentityDigest": DIGEST_B, "socketIdentityDigest": None, "socketOptions": [], "statusFlags": ["O_RDONLY"]}
    observable_wire["canonicalDigest"] = canonical_digest(observable_wire)
    descriptor_wire = {"cardinality": "exactly-one", "logicalName": logical_name, "observableFields": observable_wire, "ordinal": ordinal, "provenanceFields": provenance.to_wire(), "role": role}
    descriptor_wire["canonicalDigest"] = canonical_digest(descriptor_wire)
    return FdDescriptorV1._from_wire(descriptor_wire)


def test_e2_fd_observable_and_descriptor_reject_public_manual_construction():
    """A claimed wire identity cannot become a broker-produced descriptor."""
    provenance = FdOpenProvenanceV1(
        "openat2-beneath-no-symlinks", "worker-input-inbox", DIGEST_A, DIGEST_B,
        ("O_CLOEXEC", "O_NOFOLLOW", "O_RDONLY"),
        ("RESOLVE_BENEATH", "RESOLVE_NO_MAGICLINKS", "RESOLVE_NO_SYMLINKS", "RESOLVE_NO_XDEV"),
        DIGEST_C,
    )
    with pytest.raises(TypeError, match="FD observable"):
        FdObservableIdentityV1(
            "regular", "read-only", ("O_RDONLY",), ("FD_CLOEXEC",), 1, 2, 7,
            DIGEST_A, DIGEST_B, (), None, DIGEST_C,
        )
    with pytest.raises(TypeError, match="FD descriptor"):
        FdDescriptorV1("worker-input", 0, "worker-input", "exactly-one", provenance, object())
    for record_type in (FdObservableIdentityV1, FdDescriptorV1):
        assert not hasattr(record_type, "_BROKER_TOKEN")
        assert not hasattr(record_type, "_DECODED_TOKEN")
        assert not hasattr(record_type, "_construct")
    forged = object.__new__(FdDescriptorV1)
    object.__setattr__(forged, "role", "worker-input")
    assert not forged._is_broker_produced


def _worker_input(**overrides):
    input_roles = (
        "product-handoff-cache", "policy-handoff-cache", "task-output-policy",
        "transaction-record", "quarantine-object", "toolchain-build-record", "toolchain-artifact",
        *("candidate",) * 7,
    )
    inputs = tuple(
        RegistrationWorkerInputObjectV1(role, 0 if role != "candidate" else ordinal - 7,
            f"audit/{ordinal}", "regular", 7, DIGEST_A,
            REFS[ordinal - 7] if role == "candidate" else None)
        for ordinal, role in enumerate(input_roles)
    )
    values = dict(
        schema="trusted-registration-worker-input/v1", project_id="ai-video-reverse-engineer",
        operation_id="operation-e2", worker_mode="register", expected_worker_sequence=1,
        registration_profile="ticket-23/v1", subject="git://product@" + "1" * 40,
        expected_policy_commit=BASE, fixed_ref_name="refs/tags/trusted-acceptance/ticket-23-policy-v1",
        expected_fixed_ref_commit=BASE, expected_repository_display_path="/audit/repository",
        expected_git_executable_digest=DIGEST_A, inputs=inputs, candidate_refs=REFS,
    )
    values.update(overrides)
    return RegistrationWorkerInputV1(**values)


def _unsigned_envelope(mode="register", **overrides):
    closure = _REGISTER_CLOSURE if mode == "register" else _RECOVER_CLOSURE
    values = dict(
        schema="trusted-registration-capability-envelope/v1", broker_lease_identity_digest=DIGEST_A,
        operation_id="operation-e2", lifecycle_nonce="lifecycle-e2", worker_mode=mode,
        worker_session_id="session-e2", worker_executable_digest=DIGEST_B,
        worker_process_identity_digest=DIGEST_C, worker_pidfd_identity_digest=DIGEST_A,
        worker_sequence=1, broker_session_nonce="broker-session-e2",
        worker_input_digest=_worker_input().canonical_digest,
        fd_descriptors=tuple(_fd_descriptor(index, item) for index, item in enumerate(closure)),
        broker_auth_key_id="broker-fixed", broker_auth_public_key_digest=DIGEST_B,
        broker_auth_registry_generation=1, broker_auth_registry_digest=DIGEST_C,
        broker_auth_revocation_digest=DIGEST_A, broker_auth_allowed_signers_digest=DIGEST_B,
        broker_auth_lease_issued_sequence=1, broker_auth_lease_expiry_sequence=3,
        broker_auth_sign_sequence=2,
    )
    values.update(overrides)
    return CapabilityEnvelopeUnsignedV1(**values)


def _broker_produced_closure(tmp_path, mode="register"):
    """Build a live broker closure from real FDs, not decoded wire fixtures."""
    closure = _unsigned_envelope(mode).fd_descriptors
    try:
        control, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    except OSError:
        pytest.skip("platform cannot create the seqpacket broker-control FD")
    opened = []
    try:
        control.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        for ordinal in range(len(closure) - 1):
            source = tmp_path / f"broker-produced-{mode}-{ordinal}"
            source.write_bytes(f"broker-produced-{mode}-{ordinal}".encode())
            opened.append(os.open(source, os.O_RDONLY | os.O_CLOEXEC))
        return tuple(
            FdDescriptorV1.from_open_fd(
                fd, role=item.role, ordinal=item.ordinal, logical_name=item.logical_name,
                provenance_fields=item.provenance_fields,
            )
            for item, fd in zip(closure[:-1], opened)
        ) + (FdDescriptorV1.from_open_fd(
            control.fileno(), role=closure[-1].role, ordinal=closure[-1].ordinal,
            logical_name=closure[-1].logical_name, provenance_fields=closure[-1].provenance_fields,
        ),)
    finally:
        for fd in opened:
            os.close(fd)
        control.close()
        peer.close()


def test_e2_worker_input_and_fixed_14_20_fd_closures_reject_any_shape_drift_without_writes():
    recorder = VisibleAuthorizationWriteRecorder()
    worker_input = _worker_input()
    register, recover = _unsigned_envelope(), _unsigned_envelope("recover")
    assert len(register.fd_descriptors) == 14
    assert len(recover.fd_descriptors) == 20
    assert [item.logical_name for item in register.fd_descriptors] == [item[1] for item in _REGISTER_CLOSURE]
    assert [item.logical_name for item in recover.fd_descriptors] == [item[1] for item in _RECOVER_CLOSURE]
    assert RegistrationWorkerInputV1.from_canonical_bytes(worker_input.canonical_bytes) == worker_input
    assert CapabilityEnvelopeUnsignedV1.from_canonical_bytes(register.canonical_bytes) == register
    for changed in (
        tuple(reversed(register.fd_descriptors)), register.fd_descriptors[:-1],
        register.fd_descriptors + (register.fd_descriptors[-1],),
        tuple((*register.fd_descriptors[:-1], _fd_descriptor(13, ("broker-control", "renamed-control", None)))),
    ):
        recorder.assert_rejected(lambda changed=changed: _unsigned_envelope(fd_descriptors=changed))
    recorder.assert_rejected(lambda: _worker_input(inputs=tuple(reversed(worker_input.inputs))))
    recorder.assert_rejected(lambda: RegistrationWorkerInputV1.from_canonical_bytes(worker_input.canonical_bytes + b"\n"))


def test_e2_signed_wire_has_one_lowercase_signature_and_no_unsigned_signature_field(tmp_path):
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    public_digest = "sha256:" + hashlib.sha256(public).hexdigest()
    unsigned = _unsigned_envelope(broker_auth_public_key_digest=public_digest)
    assert b"brokerSignatureHex" not in unsigned.canonical_bytes
    key = RegistrationBrokerAuthKeyRegistrationV1(
        "trusted-registration-broker-auth-key-registration/v1", "broker-fixed",
        "registration-broker-auth", "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "ai-video-reverse-engineer/registration-broker-lease/v1",
        "ai-video-reverse-engineer/registration-broker-startup-challenge/v1", "raw-ed25519-32",
        public_digest, public_digest, None, public_digest, public_digest, DIGEST_A, "active", 1, None,
    )
    key_file = tmp_path / "broker-key"; key_file.write_bytes(seed)
    descriptor = os.open(key_file, os.O_RDONLY)
    try:
        signer = RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, key, public)
    finally:
        os.close(descriptor)
    try:
        # Strict decoding is deliberately permitted for worker-side wire
        # verification, but decoded records must never re-enter a live broker
        # signing path.
        with pytest.raises(ValueError, match="broker-produced"):
            CapabilityEnvelopeV1.create(unsigned, signer)
        signed = _decoded_signed_envelope(unsigned, signer)
        assert CapabilityEnvelopeV1.from_canonical_bytes(signed.canonical_bytes) == signed
        signed.verify({"broker-fixed": public})
        wire = json.loads(signed.canonical_bytes)
        assert set(wire) == set(json.loads(unsigned.canonical_bytes)) | {"brokerSignatureHex"}
        assert wire["brokerSignatureHex"] == signed.broker_signature_hex
        assert len(signed.broker_signature_hex) == 128 and signed.broker_signature_hex == signed.broker_signature_hex.lower()
        with pytest.raises(ValueError):
            CapabilityEnvelopeV1.from_canonical_bytes(signed.canonical_bytes.replace(b'"brokerSignatureHex":"', b'"brokerSignatureHex":"0x', 1))
        with pytest.raises(ValueError):
            CapabilityEnvelopeV1.from_canonical_bytes(signed.canonical_bytes.replace(b'"brokerSignatureHex":', b'"unexpected":null,"brokerSignatureHex":', 1))
        for signature in (
            "0" * 127,
            "0" * 129,
            "0" * 127 + "g",
            "A" * 128,
            "  " + "0" * 124 + "  ",
            "c2lnbmF0dXJl",
        ):
            malformed = dict(wire)
            malformed["brokerSignatureHex"] = signature
            with pytest.raises(ValueError):
                CapabilityEnvelopeV1.from_canonical_bytes(canonical_json_bytes(malformed))
        with pytest.raises(ValueError):
            CapabilityEnvelopeV1.from_canonical_bytes(
                signed.canonical_bytes[:-1] + b',"brokerSignatureHex":"' + b"0" * 128 + b'"}'
            )
        with pytest.raises(ValueError):
            CapabilityEnvelopeV1.from_canonical_bytes(b" " + signed.canonical_bytes)
    finally:
        signer.close()
    # A signer is deliberately required; no caller supplied signature or public
    # key may turn unsigned bytes into an authority-bearing wire value.
    with pytest.raises((TypeError, ValueError)):
        CapabilityEnvelopeV1.create(unsigned, object())


def test_e2_receive_once_fails_closed_when_msg_cmsg_cloexec_is_unavailable():
    if hasattr(socket, "MSG_CMSG_CLOEXEC"):
        pytest.skip("platform can exercise the SCM_RIGHTS green path")
    with pytest.raises(ValueError, match="MSG_CMSG_CLOEXEC unavailable"):
        RegistrationRecoveryCapabilityV1.receive_once(object(), object())


def _fd_digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _expected_regular_observable(fd, content):
    info = os.fstat(fd)
    mount_digest = _fd_digest({"filesystemId": os.fstatvfs(fd).f_fsid, "kind": "fstatvfs-fsid/v1"})
    wire = {"accessMode": "read-only", "byteLength": len(content), "contentDigest": "sha256:" + hashlib.sha256(content).hexdigest(), "device": info.st_dev, "fdFlags": ["FD_CLOEXEC"], "fileType": "regular", "inode": info.st_ino, "mountIdentityDigest": mount_digest, "socketIdentityDigest": None, "socketOptions": [], "statusFlags": ["O_RDONLY"]}
    wire["identityDigest"] = _fd_digest(wire)
    wire["canonicalDigest"] = canonical_digest(wire)
    return FdObservableIdentityV1._from_wire(wire)


def _decoded_observable_with_length(observable, length):
    wire = observable.to_wire()
    wire["byteLength"] = length
    wire["identityDigest"] = canonical_digest({key: value for key, value in wire.items() if key not in {"canonicalDigest", "identityDigest"}})
    wire["canonicalDigest"] = canonical_digest({key: value for key, value in wire.items() if key != "canonicalDigest"})
    return FdObservableIdentityV1._from_wire(wire)


def _regular_provenance(root_role="worker-input-inbox"):
    return FdOpenProvenanceV1(
        "openat2-beneath-no-symlinks", root_role, DIGEST_A, DIGEST_B,
        ("O_CLOEXEC", "O_NOFOLLOW", "O_RDONLY"),
        ("RESOLVE_BENEATH", "RESOLVE_NO_MAGICLINKS", "RESOLVE_NO_SYMLINKS", "RESOLVE_NO_XDEV"),
        DIGEST_C,
    )


def _open_regular_descriptor(fd, *, role="worker-input", ordinal=0, logical_name="worker-input"):
    return FdDescriptorV1.from_open_fd(
        fd, role=role, ordinal=ordinal, logical_name=logical_name,
        provenance_fields=_regular_provenance(),
    )


def test_e2_rebuilds_regular_fd_identity_from_kernel_facts(tmp_path):
    """Dropping mount/content/identity reconstruction must reject a real input FD."""
    source = tmp_path / "worker-input"
    content = b"verified regular worker input\n"
    source.write_bytes(content)
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        expected = _expected_regular_observable(fd, content)
        assert _open_regular_descriptor(fd).observable_fields == expected
        RegistrationRecoveryCapabilityV1._verify_observable_fd(fd, expected, role="worker-input")
        source.write_bytes(b"tampered regular worker input\n")
        with pytest.raises(ValueError, match="identity changed"):
            RegistrationRecoveryCapabilityV1._verify_observable_fd(fd, expected, role="worker-input")
        fcntl.fcntl(fd, fcntl.F_SETFD, fcntl.fcntl(fd, fcntl.F_GETFD) & ~fcntl.FD_CLOEXEC)
        with pytest.raises(ValueError, match="FD_CLOEXEC"):
            RegistrationRecoveryCapabilityV1._verify_observable_fd(fd, expected, role="worker-input")
    finally:
        os.close(fd)


def test_e2_regular_fd_rejects_size_or_role_budget_before_any_content_read(tmp_path, monkeypatch):
    """A huge carrier is rejected from fstat facts, before its first content read."""
    source = tmp_path / "oversize-worker-input"
    source.write_bytes(b"x")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        expected = _expected_regular_observable(fd, b"x")
        os.truncate(source, 256 * 1024 * 1024 + 1)
        expected = _decoded_observable_with_length(expected, 256 * 1024 * 1024 + 1)
        monkeypatch.setattr(os, "pread", lambda *_args: pytest.fail("regular content was read"))
        with pytest.raises(ValueError, match="budget"):
            RegistrationRecoveryCapabilityV1._verify_observable_fd(
                fd, expected, role="worker-input",
            )
    finally:
        os.close(fd)


def test_e2_broker_producer_rejects_role_oversize_sparse_regular_fd_before_any_content_read(tmp_path, monkeypatch):
    """Changing producer construction to hash before its role budget must fail this test."""
    source = tmp_path / "oversize-broker-producer"
    source.write_bytes(b"x")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.truncate(source, 1024 * 1024 + 1)
        reads = 0

        def counted_pread(*_args):
            nonlocal reads
            reads += 1
            pytest.fail("producer read an over-budget regular FD")

        monkeypatch.setattr(os, "pread", counted_pread)
        with pytest.raises(ValueError, match="budget"):
            _open_regular_descriptor(fd)
        with pytest.raises(ValueError, match="role"):
            _open_regular_descriptor(fd, role="unrecognized-role")
        assert reads == 0
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "writes",
    (
        (b"B" * 64,),
        (b"B" * 64, b"A" * 64),
    ),
    ids=("same-length-overwrite", "same-length-overwrite-then-restore"),
)
def test_e2_regular_fd_rejects_same_length_mutation_during_hash_even_if_bytes_are_restored(tmp_path, monkeypatch, writes):
    """A read-window mutation must reject even when the final bytes equal the signed bytes."""
    source = tmp_path / "mutable-worker-input"
    original = b"A" * 64
    source.write_bytes(original)
    read_fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    write_fd = os.open(source, os.O_WRONLY)
    original_pread = os.pread
    try:
        first_read = True

        def mutate_after_first_read(descriptor, size, offset):
            nonlocal first_read
            result = original_pread(descriptor, size, offset)
            if first_read and descriptor == read_fd and result:
                first_read = False
                for replacement in writes:
                    assert os.pwrite(write_fd, replacement, 0) == len(replacement)
            return result

        monkeypatch.setattr(os, "pread", mutate_after_first_read)
        with pytest.raises(ValueError, match="changed while hashing"):
            RegistrationRecoveryCapabilityV1._verify_observable_fd(
                read_fd, _expected_regular_observable(read_fd, original), role="worker-input",
            )
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_e2_regular_fd_hashes_only_the_exact_length_then_proves_eof(tmp_path, monkeypatch):
    """The worker must hash the signed size, then use a one-byte EOF probe."""
    source = tmp_path / "exact-worker-input"
    content = b"exact worker input"
    source.write_bytes(content)
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    original_pread = os.pread
    calls = []

    def counted_pread(descriptor, size, offset):
        calls.append((size, offset))
        return original_pread(descriptor, size, offset)

    monkeypatch.setattr(os, "pread", counted_pread)
    try:
        RegistrationRecoveryCapabilityV1._verify_observable_fd(
            fd, _expected_regular_observable(fd, content), role="worker-input",
        )
        assert calls[-1] == (1, len(content))
        assert all(offset + size <= len(content) for size, offset in calls[:-1])
    finally:
        os.close(fd)


def test_e2_role_bound_fd_producer_and_worker_reject_wrong_type_access_and_length_before_reading(tmp_path, monkeypatch):
    """A role-bound FD path must reject nonconforming kernel facts before hashing content."""
    source = tmp_path / "worker-input-facts"
    content = b"verified regular worker input"
    source.write_bytes(content)
    read_fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    write_fd = os.open(source, os.O_WRONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        monkeypatch.setattr(os, "pread", lambda *_args: pytest.fail("invalid FD facts were read"))
        expected = _expected_regular_observable(read_fd, content)
        with pytest.raises(ValueError, match="length"):
            RegistrationRecoveryCapabilityV1._verify_observable_fd(
                read_fd, _decoded_observable_with_length(expected, len(content) + 1), role="worker-input",
            )
        with pytest.raises(ValueError, match="state"):
            _open_regular_descriptor(write_fd)
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_e2_regular_fd_masks_access_mode_without_rewriting_descriptor_status(tmp_path, monkeypatch):
    """A Linux status bit must not turn a read-only FD into a rejected access mode."""
    source = tmp_path / "worker-input-with-status"
    source.write_bytes(b"verified regular worker input\n")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    original_fcntl = fcntl.fcntl
    status_bit = getattr(os, "O_NONBLOCK", 0x800)

    def fcntl_with_status(descriptor, command, *args):
        result = original_fcntl(descriptor, command, *args)
        return result | status_bit if descriptor == fd and command == fcntl.F_GETFL else result

    monkeypatch.setattr(fcntl, "fcntl", fcntl_with_status)
    try:
        observed = _open_regular_descriptor(fd).observable_fields
        assert observed.access_mode == "read-only"
        assert observed.status_flags == ("O_RDONLY",)
    finally:
        os.close(fd)


def test_e2_rebuilds_socket_identity_and_rejects_non_seqpacket_control_fd():
    """A control descriptor must use kernel socket family/type/options, never envelope claims."""
    local, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        observed = FdObservableIdentityV1._from_open_fd(
            local.fileno(), expected_byte_length=None, maximum_byte_length=None,
        )
        assert observed.file_type == "socket-stream"
        assert observed.socket_options == ("SO_TYPE=SOCK_STREAM",)
        claimed = {"accessMode": "bidirectional-control", "byteLength": None, "contentDigest": None, "device": None, "fdFlags": ["FD_CLOEXEC"], "fileType": "socket-seqpacket", "inode": None, "mountIdentityDigest": None, "socketIdentityDigest": observed.socket_identity_digest, "socketOptions": ["SO_PASSCRED=1", "SO_TYPE=SOCK_SEQPACKET"], "statusFlags": []}
        claimed["identityDigest"] = observed.identity_digest
        claimed["canonicalDigest"] = canonical_digest(claimed)
        with pytest.raises(ValueError, match="control FD state"):
            RegistrationRecoveryCapabilityV1._verify_observable_fd(
                local.fileno(),
                FdObservableIdentityV1._from_wire(claimed),
            )
        local.setblocking(False)
        with pytest.raises(ValueError, match="control FD state"):
            RegistrationRecoveryCapabilityV1._verify_observable_fd(local.fileno(), observed)
    finally:
        local.close()
        peer.close()


@pytest.mark.skipif(
    sys.platform != "linux" or not hasattr(socket, "MSG_CMSG_CLOEXEC") or not hasattr(socket, "SO_PASSCRED"),
    reason="requires Linux SCM_RIGHTS, MSG_CMSG_CLOEXEC, and SO_PASSCRED",
)
def test_e2_linux_recvmsg_accepts_a_real_exact_register_closure(tmp_path):
    """Linux-only: the real recvmsg gate consumes exactly one signed 14-FD closure."""
    sender, receiver, opened, control, control_peer, signer, expected = _linux_delivery(tmp_path, "register")
    try:
        capability = RegistrationRecoveryCapabilityV1.receive_once(receiver, expected)
        received = capability.take_once()
        assert len(received) == 14
        with pytest.raises(ValueError, match="already consumed"):
            capability.take_once()
        for fd in received:
            os.close(fd)
    finally:
        if signer is not None:
            signer.close()
        for fd in opened:
            os.close(fd)
        sender.close()
        control.close()
        control_peer.close()


def _expected_receive_context(governance, lease, **overrides):
    values = dict(
        worker_mode="register", worker_process_identity_digest=DIGEST_C,
        worker_executable_digest=DIGEST_B, worker_pidfd_identity_digest=DIGEST_A,
        worker_input_digest=_worker_input().canonical_digest, worker_session_id="session-e2",
        operation_id="operation-e2", lifecycle_nonce="lifecycle-e2", worker_sequence=1,
        broker_session_nonce="broker-session-e2", lease=lease, broker_auth_sign_sequence=2,
        governance=governance,
    )
    values.update(overrides)
    return RegistrationExpectedReceiveContextV1(**values)


def _test_broker_signer(tmp_path):
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    governance, registration = _broker_governance("broker-fixed", public)
    key_path = tmp_path / "broker-seed"
    key_path.write_bytes(seed)
    descriptor = os.open(key_path, os.O_RDONLY)
    try:
        return governance, RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, registration, public)
    finally:
        os.close(descriptor)


def _test_receive_lease(governance, signer):
    template = RegistrationRecoveryLeaseV1(
        "trusted-registration-recovery-lease/v1", "ai-video-reverse-engineer", "operation-e2",
        "lifecycle-e2", DIGEST_A, 1, DIGEST_A, "transaction-root", DIGEST_B, DIGEST_C,
        DIGEST_A, DIGEST_B, DIGEST_C, DIGEST_A, DIGEST_B, DIGEST_C,
        governance.registry.generation, governance.registry.canonical_digest,
        governance.revocations.canonical_digest, DIGEST_B, governance.allowed_signers_digest,
        "broker-fixed", DIGEST_B, DIGEST_A, DIGEST_B, DIGEST_C, 1, 3, DIGEST_A,
        "OPEN", "a" * 128, "b" * 64,
    )
    lease = replace(template, lease_identity_digest=template.identity_preimage().lease_identity_digest)
    return replace(
        lease,
        broker_signature_hex=signer.sign(
            "ai-video-reverse-engineer/registration-broker-lease/v1", canonical_json_bytes(lease._unsigned_wire()),
        ),
    )


def _decoded_signed_envelope(unsigned, signer):
    """Exercise the worker decode path without treating decoded records as live input."""
    return CapabilityEnvelopeV1.from_canonical_bytes(canonical_json_bytes({
        **unsigned.to_wire(),
        "brokerSignatureHex": signer.sign(
            "ai-video-reverse-engineer/registration-broker-envelope/v1", unsigned.canonical_bytes,
        ),
    }))


def _signed_receive_envelope(governance, signer, lease, mode="register", **overrides):
    values = dict(
        broker_lease_identity_digest=lease.lease_identity_digest,
        broker_auth_public_key_digest=signer.public_key_digest,
        broker_auth_registry_generation=governance.registry.generation,
        broker_auth_registry_digest=governance.registry.canonical_digest,
        broker_auth_revocation_digest=governance.revocations.canonical_digest,
        broker_auth_allowed_signers_digest=governance.allowed_signers_digest,
    )
    values.update(overrides)
    if "fd_descriptors" not in values:
        return _decoded_signed_envelope(_unsigned_envelope(mode, **values), signer)
    return CapabilityEnvelopeV1.create(_unsigned_envelope(mode, **values), signer)


def test_e2_expected_receive_context_rejects_old_or_cross_bound_signed_envelopes(tmp_path):
    """Removing any expected-context comparison would accept a valid stale capability."""
    governance, signer = _test_broker_signer(tmp_path)
    try:
        lease = _test_receive_lease(governance, signer)
        expected = _expected_receive_context(governance, lease)
        signed = _signed_receive_envelope(governance, signer, lease)
        expected.verify_envelope(signed)
        for changed in (
            {"worker_mode": "recover"}, {"operation_id": "old-operation"},
            {"lifecycle_nonce": "old-lifecycle"}, {"worker_session_id": "old-session"},
            {"broker_session_nonce": "old-broker-session"}, {"worker_sequence": 2},
            {"broker_lease_identity_digest": DIGEST_B},
        ):
            with pytest.raises(ValueError, match="expected receive context"):
                mode = changed.get("worker_mode", "register")
                values = {key: value for key, value in changed.items() if key != "worker_mode"}
                candidate = _signed_receive_envelope(governance, signer, lease, mode, **values)
                expected.verify_envelope(candidate)
        with pytest.raises(ValueError, match="expected receive context"):
            _expected_receive_context(governance, lease, broker_auth_sign_sequence=3).verify_envelope(signed)
        terminal_lease = replace(lease, state="RECLAIMED", next_recovery_sequence=777)
        terminal_lease = replace(
            terminal_lease,
            broker_signature_hex=signer.sign(
                "ai-video-reverse-engineer/registration-broker-lease/v1",
                canonical_json_bytes(terminal_lease._unsigned_wire()),
            ),
        )
        terminal_envelope = _signed_receive_envelope(
            governance, signer, terminal_lease, worker_sequence=777,
        )
        with pytest.raises(ValueError, match="expected receive context"):
            _expected_receive_context(
                governance, terminal_lease, worker_sequence=777,
            ).verify_envelope(terminal_envelope)
        for unexpected_sequence in (2, 777):
            with pytest.raises(ValueError, match="expected receive context"):
                _expected_receive_context(
                    governance, lease, worker_sequence=unexpected_sequence,
                ).verify_envelope(
                    _signed_receive_envelope(
                        governance, signer, lease, worker_sequence=unexpected_sequence,
                    )
                )
        for changed_lease in (
            {"session_family_authorization_digest": DIGEST_B},
            {"broker_auth_lease_request_digest": DIGEST_C},
        ):
            other_lease = replace(lease, **changed_lease)
            other_lease = replace(
                other_lease, lease_identity_digest=other_lease.identity_preimage().lease_identity_digest,
            )
            other_lease = replace(
                other_lease,
                broker_signature_hex=signer.sign(
                    "ai-video-reverse-engineer/registration-broker-lease/v1",
                    canonical_json_bytes(other_lease._unsigned_wire()),
                ),
            )
            with pytest.raises(ValueError, match="expected receive context"):
                _expected_receive_context(governance, other_lease).verify_envelope(signed)
        other_governance, _ = _broker_governance("other-broker", signer._public)
        with pytest.raises(ValueError, match="expected receive context"):
            _expected_receive_context(other_governance, lease).verify_envelope(signed)
    finally:
        signer.close()


def _linux_delivery(tmp_path, mode, fault=None, envelope_overrides=None):
    sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    control, control_peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    opened = []
    governance, signer = _test_broker_signer(tmp_path)
    lease = _test_receive_lease(governance, signer)
    worker_sequence = 1
    if mode == "recover":
        worker_sequence = 2
        lease = replace(lease, state="RECOVERY_RUNNING", next_recovery_sequence=worker_sequence)
        lease = replace(
            lease,
            broker_signature_hex=signer.sign(
                "ai-video-reverse-engineer/registration-broker-lease/v1",
                canonical_json_bytes(lease._unsigned_wire()),
            ),
        )
    try:
        control.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        closure = _unsigned_envelope(mode).fd_descriptors
        for ordinal in range(len(closure) - 1):
            source = tmp_path / f"{mode}-input-{ordinal}"
            source.write_bytes(f"{mode}-input-{ordinal}".encode())
            opened.append(os.open(source, os.O_RDONLY | os.O_CLOEXEC))
        descriptors = tuple(
            FdDescriptorV1.from_open_fd(
                fd, role=item.role, ordinal=item.ordinal, logical_name=item.logical_name,
                provenance_fields=item.provenance_fields,
            )
            for item, fd in zip(closure[:-1], opened)
        ) + (FdDescriptorV1.from_open_fd(
            control.fileno(), role=closure[-1].role, ordinal=closure[-1].ordinal,
            logical_name=closure[-1].logical_name, provenance_fields=closure[-1].provenance_fields,
        ),)
        sent = [*opened, control.fileno()]
        if fault == "missing":
            sent.pop()
        elif fault == "extra":
            sent.append(opened[0])
        elif fault == "reordered":
            sent.reverse()
        elif fault == "duplicate":
            duplicate = closure[1]
            descriptors = (
                descriptors[0],
                FdDescriptorV1.from_open_fd(
                    opened[0], role=duplicate.role, ordinal=duplicate.ordinal,
                    logical_name=duplicate.logical_name, provenance_fields=duplicate.provenance_fields,
                ),
                *descriptors[2:],
            )
            sent[1] = sent[0]
        envelope = _signed_receive_envelope(
            governance, signer, lease, mode, worker_sequence=worker_sequence, fd_descriptors=descriptors,
            **(envelope_overrides or {}),
        )
        sender.sendmsg([envelope.canonical_bytes], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", sent))])
        expected = _expected_receive_context(
            governance, lease, worker_mode=mode, worker_sequence=worker_sequence,
        )
        return sender, receiver, opened, control, control_peer, signer, expected
    except Exception:
        signer.close()
        for fd in opened:
            os.close(fd)
        sender.close()
        receiver.close()
        control.close()
        control_peer.close()
        raise


@pytest.mark.skipif(
    sys.platform != "linux" or not hasattr(socket, "MSG_CMSG_CLOEXEC") or not hasattr(socket, "SO_PASSCRED"),
    reason="requires Linux SCM_RIGHTS, MSG_CMSG_CLOEXEC, and SO_PASSCRED",
)
def test_e2_linux_recvmsg_accepts_a_real_exact_recover_closure(tmp_path):
    sender, receiver, opened, control, control_peer, signer, expected = _linux_delivery(tmp_path, "recover")
    try:
        capability = RegistrationRecoveryCapabilityV1.receive_once(receiver, expected)
        received = capability.take_once()
        assert len(received) == 20
        for fd in received:
            os.close(fd)
    finally:
        signer.close()
        for fd in opened:
            os.close(fd)
        sender.close()
        control.close()
        control_peer.close()


@pytest.mark.skipif(
    sys.platform != "linux" or not hasattr(socket, "MSG_CMSG_CLOEXEC") or not hasattr(socket, "SO_PASSCRED"),
    reason="requires Linux SCM_RIGHTS, MSG_CMSG_CLOEXEC, and SO_PASSCRED",
)
@pytest.mark.parametrize("mode", ("register", "recover"))
@pytest.mark.parametrize("fault", ("missing", "extra", "duplicate", "reordered", "wrong-context"))
def test_e2_linux_recvmsg_rejects_invalid_real_fd_closures(tmp_path, mode, fault):
    envelope_overrides = {"operation_id": "old-operation"} if fault == "wrong-context" else None
    sender, receiver, opened, control, control_peer, signer, expected = _linux_delivery(
        tmp_path, mode, None if fault == "wrong-context" else fault, envelope_overrides,
    )
    try:
        with pytest.raises(ValueError):
            RegistrationRecoveryCapabilityV1.receive_once(receiver, expected)
    finally:
        signer.close()
        for fd in opened:
            os.close(fd)
        sender.close()
        receiver.close()
        control.close()
        control_peer.close()


@pytest.mark.skipif(
    sys.platform != "linux" or not hasattr(socket, "MSG_CMSG_CLOEXEC") or not hasattr(socket, "SO_PASSCRED"),
    reason="requires Linux SCM_RIGHTS, MSG_CMSG_CLOEXEC, and SO_PASSCRED",
)
def test_e2_linux_recvmsg_closes_every_received_fd_after_validation_failure(tmp_path, monkeypatch):
    sender, receiver, opened, control, control_peer, signer, expected = _linux_delivery(tmp_path, "register")
    received = []

    def reject_after_receive(_descriptors, fds):
        received.extend(fds)
        raise ValueError("forced receive failure")

    monkeypatch.setattr(RegistrationRecoveryCapabilityV1, "_verify_received_closure", reject_after_receive)
    try:
        with pytest.raises(ValueError, match="forced receive failure"):
            RegistrationRecoveryCapabilityV1.receive_once(receiver, expected)
        assert len(received) == 14
        for fd in received:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        signer.close()
        for fd in opened:
            os.close(fd)
        sender.close()
        receiver.close()
        control.close()
        control_peer.close()


# E.3 deliberately exercises the portable contract only.  It must not grow a
# worker write path, a broker receipt key in the request, or any Linux runtime.
def test_e3_mutation_request_receipt_and_single_use_broker_verifier():
    binding = RegistrationMutationChannelBindingV1.create(_unsigned_envelope(), DIGEST_A)
    sender = RegistrationMutationSenderEvidenceV1.from_binding(binding)
    request = RegistrationMutationRequestV1.create(
        binding, DIGEST_A, DIGEST_B, "PREPARED", "persist-intent", DIGEST_C
    )
    assert RegistrationMutationRequestV1.from_canonical_bytes(request.canonical_bytes) == request
    assert "mac" not in request.canonical_bytes.decode("utf-8").lower()
    assert "secret" not in request.canonical_bytes.decode("utf-8").lower()

    calls = 0
    verifier = RegistrationBrokerMutationVerifierV1.create(
        binding, initial_state_digest=DIGEST_A, initial_transition_digest=DIGEST_B,
        initial_phase="PREPARED", audit_key=b"e3 broker audit key is root-only!!",
    )

    def execute():
        nonlocal calls
        calls += 1
        return "EXECUTED", DIGEST_C

    receipt = verifier.dispatch(request, sender, execute)
    assert calls == 1
    assert receipt.result == "EXECUTED"
    receipt.verify_audit_mac(b"e3 broker audit key is root-only!!")
    assert BrokerMutationReceiptV1.from_canonical_bytes(receipt.canonical_bytes) == receipt

    for replay in (request, request):
        with pytest.raises(ValueError):
            verifier.dispatch(replay, sender, execute)
        assert calls == 1

    forged = BrokerMutationReceiptV1(
        receipt.schema, receipt.operation_id, receipt.worker_session_id,
        receipt.worker_sequence, receipt.mutation_sequence, receipt.request_digest,
        receipt.result, receipt.resulting_registration_state_digest,
        "0" * 64,
    )
    with pytest.raises(ValueError):
        forged.verify_audit_mac(b"e3 broker audit key is root-only!!")


def test_e3_verifier_rejects_bound_identity_state_and_sequence_drift_before_action():
    binding = RegistrationMutationChannelBindingV1.create(_unsigned_envelope(), DIGEST_A)
    sender = RegistrationMutationSenderEvidenceV1.from_binding(binding)
    request = RegistrationMutationRequestV1.create(
        binding, DIGEST_A, DIGEST_B, "PREPARED", "persist-intent", DIGEST_C
    )

    def assert_rejected(candidate, evidence=sender):
        calls = 0
        verifier = RegistrationBrokerMutationVerifierV1.create(
            binding, initial_state_digest=DIGEST_A, initial_transition_digest=DIGEST_B,
            initial_phase="PREPARED", audit_key=b"e3 broker audit key is root-only!!",
        )

        def execute():
            nonlocal calls
            calls += 1
            return "EXECUTED", DIGEST_C

        with pytest.raises(ValueError):
            verifier.dispatch(candidate, evidence, execute)
        assert calls == 0

    assert_rejected(RegistrationMutationRequestV1.create(
        RegistrationMutationChannelBindingV1.create(_unsigned_envelope(worker_session_id="old-session"), DIGEST_A),
        DIGEST_A, DIGEST_B, "PREPARED", "persist-intent", DIGEST_C,
    ))
    assert_rejected(RegistrationMutationRequestV1.create(
        binding, DIGEST_A, DIGEST_B, "WRITING_CAS", "persist-intent", DIGEST_C,
    ))
    assert_rejected(RegistrationMutationRequestV1.create(
        binding, DIGEST_A, DIGEST_C, "PREPARED", "persist-intent", DIGEST_C,
    ))
    assert_rejected(RegistrationMutationRequestV1.create(
        binding, DIGEST_A, DIGEST_B, "PREPARED", "persist-intent", DIGEST_A,
    ))
    assert_rejected(request, RegistrationMutationSenderEvidenceV1(
        binding.channel_binding_digest, DIGEST_B, binding.operation_id,
        binding.worker_session_id, binding.worker_sequence,
    ))


def test_e3_forward_only_authority_is_root_only_and_exactly_two_actions(tmp_path):
    state_mac = _state_mac_capability(tmp_path, b"e3 broker audit key is root-only!!"[:32])
    authority = RegistrationForwardOnlyRecoveryAuthorityV1.create(
        state_mac, operation_id="operation-e2",
        lease_identity_digest=DIGEST_A, lease_request_digest=DIGEST_B,
        broker_auth_key_id="broker-fixed", broker_auth_key_registration_digest=DIGEST_C,
        rename_sequence=2, rename_head_digest=DIGEST_A, rename_policy_digest=DIGEST_B,
        registration_transaction_intent_digest=DIGEST_C, expected_catalog_digest=DIGEST_A,
        catalog_parent_identity_digest=DIGEST_B, registration_journal_identity_digest=DIGEST_C,
    )
    assert authority.allowed_actions == ("fsync-catalog-parent", "append-terminal")
    authority.verify_state_mac(state_mac)
    with pytest.raises(TypeError):
        copy.copy(authority)
    with pytest.raises(TypeError):
        pickle.dumps(authority)
    assert not hasattr(authority, "to_wire")
    with pytest.raises(ValueError):
        RegistrationForwardOnlyRecoveryAuthorityV1.create(
            state_mac, operation_id="operation-e2",
            lease_identity_digest=DIGEST_A, lease_request_digest=DIGEST_B,
            broker_auth_key_id="broker-fixed", broker_auth_key_registration_digest=DIGEST_C,
            rename_sequence=2, rename_head_digest=DIGEST_A, rename_policy_digest=DIGEST_B,
            registration_transaction_intent_digest=DIGEST_C, expected_catalog_digest=DIGEST_A,
            catalog_parent_identity_digest=DIGEST_B, registration_journal_identity_digest=DIGEST_C,
            allowed_actions=("fsync-catalog-parent", "append-terminal", "rename-catalog"),
        )
    state_mac.close()


def test_e3_forward_only_recovery_authenticates_every_broker_state_record_before_replay(tmp_path):
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
    governance, key = _broker_governance("broker-fixed", bytes(32))
    registry, revocations = governance.registry, governance.revocations
    policy_digest = _broker_policy_digest(registry, revocations)
    request = BrokerAuthLeaseIssueRequestV1(
        "trusted-registration-broker-auth-lease-issue-request/v1", "other-operation", "life",
        DIGEST_A, "broker-fixed", 0, DIGEST_A, registry.canonical_digest,
        revocations.canonical_digest, policy_digest, governance.allowed_signers_digest, 2,
    )
    payload = BrokerAuthLeaseIssueEventPayloadV1(
        "trusted-registration-broker-auth-lease-issue-payload/v1", "issue-lease", DIGEST_A,
        request, request.request_digest, "other-operation", "broker-fixed", 1, 2, 3,
        policy_digest, policy_digest,
    )
    entry = BrokerAuthSequenceJournalEntryV1(
        "trusted-registration-broker-auth-sequence-entry/v1", 1, None, DIGEST_A, "issue-lease",
        payload.full_bytes_digest, len(payload.canonical_bytes), payload.full_bytes_digest, policy_digest, policy_digest,
        registry.canonical_digest, revocations.canonical_digest, "broker-fixed", "a" * 64,
    )
    journal = BrokerAuthSequenceJournalV1(
        "trusted-registration-broker-auth-sequence-journal/v1", DIGEST_A, 1, (entry,), "b" * 64
    )
    state = BrokerAuthSequenceStateV1(
        "trusted-registration-broker-auth-sequence-state/v1", DIGEST_A, 1, entry.canonical_digest,
        payload.full_bytes_digest, payload.full_bytes_digest, policy_digest, registry.canonical_digest,
        revocations.canonical_digest, "c" * 64,
    )
    state_mac = _state_mac_capability(tmp_path, b"e3 broker audit key is root-only!!"[:32])
    authority = RegistrationForwardOnlyRecoveryAuthorityV1.create(
        state_mac, operation_id="operation-e2",
        lease_identity_digest=DIGEST_A, lease_request_digest=DIGEST_B,
        broker_auth_key_id="broker-fixed", broker_auth_key_registration_digest=key.canonical_digest,
        rename_sequence=1, rename_head_digest=entry.canonical_digest, rename_policy_digest=policy_digest,
        registration_transaction_intent_digest=DIGEST_C, expected_catalog_digest=DIGEST_A,
        catalog_parent_identity_digest=DIGEST_B, registration_journal_identity_digest=DIGEST_C,
    )
    def verify(candidate_authority, candidate_journal, candidate_state, candidate_state_mac=state_mac):
        verify_registration_forward_only_recovery(
            candidate_authority, candidate_state_mac, candidate_journal, candidate_state,
            {payload.full_bytes_digest: payload},
            BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
            registry=registry, revocations=revocations,
            operation_id="operation-e2", lease_identity_digest=DIGEST_A,
            lease_request_digest=DIGEST_B,
            registration_transaction_intent_digest=DIGEST_C, catalog_digest=DIGEST_A,
            catalog_parent_identity_digest=DIGEST_B,
            registration_journal_identity_digest=DIGEST_C, action="append-terminal",
        )

    # Shape-valid MACs are not authentication.  This reproduces the original
    # bypass: before the fix, this call returns despite all three chain MACs
    # being attacker-selected hex strings.
    with pytest.raises(ValueError):
        verify(authority, journal, state)

    authenticated_entry = replace(
        entry,
        broker_state_mac_hex=state_mac.mac(canonical_json_bytes(entry._unsigned_wire())),
    )
    unsigned_journal = replace(journal, entries=(authenticated_entry,))
    authenticated_journal = replace(
        unsigned_journal,
        broker_state_mac_hex=state_mac.mac(canonical_json_bytes(unsigned_journal._unsigned_wire())),
    )
    authenticated_state = replace(
        state,
        broker_state_mac_hex=state_mac.mac(canonical_json_bytes(state._unsigned_wire())),
    )
    verify(authority, authenticated_journal, authenticated_state)

    tampered_entry = replace(authenticated_entry, broker_state_mac_hex="0" * 64)
    journal_with_tampered_entry = replace(authenticated_journal, entries=(tampered_entry,))
    journal_with_tampered_entry = replace(
        journal_with_tampered_entry,
        broker_state_mac_hex=state_mac.mac(
            canonical_json_bytes(journal_with_tampered_entry._unsigned_wire())
        ),
    )
    with pytest.raises(ValueError):
        verify(authority, journal_with_tampered_entry, authenticated_state)
    with pytest.raises(ValueError):
        verify(authority, replace(authenticated_journal, broker_state_mac_hex="0" * 64), authenticated_state)
    with pytest.raises(ValueError):
        verify(authority, authenticated_journal, replace(authenticated_state, broker_state_mac_hex="0" * 64))

    other_state_mac = _state_mac_capability(tmp_path, b"other state MAC capability key!!!!"[:32])
    try:
        with pytest.raises(ValueError):
            verify(authority, authenticated_journal, authenticated_state, other_state_mac)
        with pytest.raises(ValueError):
            verify(authority, authenticated_journal, authenticated_state, b"0123456789abcdef0123456789abcdef")
    finally:
        other_state_mac.close()

    tampered_authority = RegistrationForwardOnlyRecoveryAuthorityV1.create(
        state_mac, operation_id="operation-e2",
        lease_identity_digest=DIGEST_A, lease_request_digest=DIGEST_B,
        broker_auth_key_id="broker-fixed", broker_auth_key_registration_digest=key.canonical_digest,
        rename_sequence=1, rename_head_digest=entry.canonical_digest, rename_policy_digest=policy_digest,
        registration_transaction_intent_digest=DIGEST_C, expected_catalog_digest=DIGEST_A,
        catalog_parent_identity_digest=DIGEST_B, registration_journal_identity_digest=DIGEST_C,
    )
    tampered_authority._broker_state_mac = "0" * 64
    with pytest.raises(ValueError):
        verify(tampered_authority, authenticated_journal, authenticated_state)

    with pytest.raises(ValueError):
        verify_registration_forward_only_recovery(
            authority, state_mac, authenticated_journal, authenticated_state, {payload.full_bytes_digest: payload},
            BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
            registry=registry, revocations=revocations, operation_id="operation-e2",
            lease_identity_digest=DIGEST_A, lease_request_digest=DIGEST_B,
            registration_transaction_intent_digest=DIGEST_C, catalog_digest=DIGEST_A,
            catalog_parent_identity_digest=DIGEST_B, registration_journal_identity_digest=DIGEST_C,
            action="rename-catalog",
        )
    with pytest.raises(ValueError):
        verify_registration_forward_only_recovery(
            authority, state_mac, authenticated_journal, authenticated_state, {payload.full_bytes_digest: payload},
            BrokerAuthSequenceReplayBaselineV1(DIGEST_A, governance, policy_digest),
            registry=registry, revocations=revocations, operation_id="operation-e2",
            lease_identity_digest=DIGEST_A, lease_request_digest=DIGEST_B,
            registration_transaction_intent_digest=DIGEST_C, catalog_digest=DIGEST_A,
            catalog_parent_identity_digest=DIGEST_B, registration_journal_identity_digest=DIGEST_A,
            action="append-terminal",
        )
    state_mac.close()


def test_e3_invalid_local_request_does_not_consume_the_worker_sequence():
    binding = RegistrationMutationChannelBindingV1.create(_unsigned_envelope(), DIGEST_A)
    with pytest.raises(ValueError):
        RegistrationMutationRequestV1.create(
            binding, DIGEST_A, DIGEST_B, "PREPARED", "unknown-action", DIGEST_C
        )
    request = RegistrationMutationRequestV1.create(
        binding, DIGEST_A, DIGEST_B, "PREPARED", "persist-intent", DIGEST_C
    )
    assert request.mutation_sequence == 0
