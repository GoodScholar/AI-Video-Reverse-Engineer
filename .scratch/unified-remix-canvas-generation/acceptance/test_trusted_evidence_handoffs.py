import hashlib
import base64
import copy
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trusted_evidence.e2_policy import (
    GitObjectIdV1, ProductGitObjectIdV1, canonical_json_bytes,
    CapabilityEnvelopeV1, CapabilityEnvelopeUnsignedV1,
    BrokerAuthLeaseIssueEventPayloadV1, BrokerAuthLeaseIssueRequestV1,
    BrokerAuthSequenceJournalEntryV1, BrokerAuthSequenceJournalV1,
    BrokerAuthSequenceStateV1, RegistrationRecoveryLeaseV1,
    RegistrationRecoveryLeaseIdentityPreimageV1,
    RegistrationBrokerAuthKeyRegistrationV1, RegistrationBrokerAuthRegistryV1,
    RegistrationBrokerAuthPrivateKeySlotV1, RegistrationBrokerAuthRevocationsV1,
    RegistrationWorkerSessionFamilyAuthorizationV1,
    verify_registration_broker_startup_challenge,
)
from trusted_evidence.handoffs import (
    ActivatedPolicyExportRequestV1,
    EmbeddedCanonicalBytesV1,
    HandoffTransferManifestV1,
    HandoffRegistryV1,
    HandoffPointerV1,
    HandoffExportRequestV1,
    PolicyHandoffPointerV1,
    PolicyHandoffRecordV1,
    PolicyActivationProofV1,
    ProductSourceBudgetV1,
    ProductRepositorySourceV1,
    PolicyActivationMarkerV1,
    Ticket23ProductSourceHandoffV1,
    TrustedHandoffRecordV1,
    TrustedHandoffStoreV1,
    TransportReadyMarkerV1,
    TransportSealV1,
    load_disjoint_signing_domains,
    verify_transport_closure,
    verify_policy_activation_proof,
)
from trusted_evidence import registry as registry_module
from trusted_evidence.registry import APPROVED_V2_SCHEMA_IDS


ACCEPTANCE_ROOT = Path(__file__).parent


def _cleanup_tool_module():
    """Load the CLI module so cleanup races can be exercised in-process."""
    spec = importlib.util.spec_from_file_location(
        "trusted_handoff_store_cleanup_test",
        ACCEPTANCE_ROOT / "tools" / "trusted_handoff_store.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracked_governance_inputs_are_canonical_and_keep_broker_auth_unprovisioned():
    """Removing a checked-in trust input must fail before any transport is usable."""
    trust = ACCEPTANCE_ROOT / "trust"
    inputs = (
        "handoff-transport-registry.json",
        "handoff-transport-policy.json",
        "handoff-transport-allowed-signers",
        "policy-activation-registry.json",
        "policy-activation-allowed-signers",
        "registration-broker-auth-registry.json",
        "registration-broker-auth-revocations.json",
        "registration-broker-auth-allowed-signers",
        "task-output-policy.json",
    )
    payloads = {name: (trust / name).read_bytes() for name in inputs}
    for payload in payloads.values():
        value = json.loads(payload.decode("utf-8"))
        assert json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") == payload

    domains = load_disjoint_signing_domains(
        payloads["handoff-transport-registry.json"],
        payloads["policy-activation-registry.json"],
        payloads["registration-broker-auth-registry.json"],
        payloads["registration-broker-auth-revocations.json"],
        payloads["registration-broker-auth-allowed-signers"],
    )
    registry = RegistrationBrokerAuthRegistryV1.from_canonical_bytes(
        payloads["registration-broker-auth-registry.json"]
    )
    revocations = RegistrationBrokerAuthRevocationsV1.from_canonical_bytes(
        payloads["registration-broker-auth-revocations.json"]
    )
    assert len(domains.transport_keys) == len(domains.activation_keys) == len(domains.broker_keys) == 1
    assert registry.active_key_id == "broker-unprovisioned-v1"
    assert revocations.registry_digest == registry.canonical_digest
    assert b"private" not in b"".join(payloads[name] for name in inputs[5:8])
    output_policy = json.loads(payloads["task-output-policy.json"])
    assert output_policy["schema"] == "trusted-task-output-policy/v1"
    assert [(item["task"], item["phase"]) for item in output_policy["tasks"]] == [
        (28, "task28-register"),
        (29, "task29-promote"),
    ]
    assert {item["role"] for item in output_policy["tasks"][0]["transitions"]} == {
        "audit", "catalog", "evidence-cas"
    }
    assert output_policy["tasks"][1]["transitions"] == [{
        "allowedTransition": "replace-with-exact-bytes",
        "precondition": "equals-base-bytes",
        "role": "contract",
    }]


def test_production_broker_governance_rejects_the_repository_rfc8032_test_seed(tmp_path):
    """A public production governance key must not be recoverable from a repository test seed."""
    registry = RegistrationBrokerAuthRegistryV1.from_canonical_bytes(
        (ACCEPTANCE_ROOT / "trust" / "registration-broker-auth-registry.json").read_bytes()
    )
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    key_file = tmp_path / "rfc8032-seed"
    key_file.write_bytes(seed)
    descriptor = os.open(key_file, os.O_RDONLY)
    try:
        with pytest.raises(ValueError):
            RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(
                descriptor, registry.key(registry.active_key_id), _test_transport_public_key(seed),
            )
    finally:
        os.close(descriptor)


def test_schema_governance_closes_every_task2_persistent_wire_schema():
    """Removing a persisted Task 2 wire schema must be rejected by the registry gate."""
    assert APPROVED_V2_SCHEMA_IDS == frozenset({
        "trusted-acceptance-handoff-pointer/v1",
        "trusted-acceptance-handoff/v1",
        "trusted-activated-policy-export-request/v1",
        "trusted-allowed-task-outputs/v1",
        "trusted-broker-lifecycle-proof/v1",
        "trusted-handoff-export-request/v1",
        "trusted-handoff-fetch/v1",
        "trusted-handoff-finalized/v1",
        "trusted-handoff-import/v1",
        "trusted-handoff-store-lease/v1",
        "trusted-handoff-transfer/v1",
        "trusted-handoff-transport-ready/v1",
        "trusted-handoff-transport-replay/v1",
        "trusted-handoff-transport-seal/v1",
        "trusted-policy-activation-proof/v1",
        "trusted-policy-activation/v1",
        "trusted-policy-publish-transaction/v1",
        "trusted-product-source-budget/v1",
        "trusted-registration-broker-auth-envelope-sign-payload/v1",
        "trusted-registration-broker-auth-key-registration/v1",
        "trusted-registration-broker-auth-lease-issue-payload/v1",
        "trusted-registration-broker-auth-lease-issue-request/v1",
        "trusted-registration-broker-auth-registry/v1",
        "trusted-registration-broker-auth-revocations/v1",
        "trusted-registration-broker-auth-revoke-payload/v1",
        "trusted-registration-broker-auth-rotate-payload/v1",
        "trusted-registration-broker-auth-sequence-entry/v1",
        "trusted-registration-broker-auth-sequence-journal/v1",
        "trusted-registration-broker-auth-sequence-state/v1",
        "trusted-registration-capability-envelope/v1",
        "trusted-registration-forward-only-recovery-authority/v1",
        "trusted-registration-journal-entry/v1",
        "trusted-registration-mutation-receipt/v1",
        "trusted-registration-mutation-request/v1",
        "trusted-registration-pre-state/v1",
        "trusted-registration-quarantine-object-set/v1",
        "trusted-registration-recovery-lease-identity-preimage/v1",
        "trusted-registration-recovery-lease/v1",
        "trusted-registration-repository-recovery-binding/v1",
        "trusted-registration-transaction-intent/v1",
        "trusted-registration-transaction-state/v1",
        "trusted-registration-worker-input/v1",
        "trusted-registration-worker-session-family-authorization/v1",
        "trusted-staged-candidate-set/v1",
        "trusted-task-completion-record/v1",
        "trusted-task-output-policy/v1",
        "trusted-ticket23-policy-handoff/v1",
    })


def test_schema_governance_rejects_unknown_v1_and_legacy_dual_protocol_ids():
    """Only the frozen Task 2 v2 schema set is accepted at the governance boundary."""
    assert registry_module.require_approved_v2_schema_id("trusted-registration-recovery-lease/v1") == "trusted-registration-recovery-lease/v1"
    for schema_id in (
        "trusted-registration-recovery-lease/v0",
        "trusted-policy-handoff/v1",
        "trusted-registration-capability-envelope/v2",
        "unknown/v1",
    ):
        with pytest.raises(ValueError):
            registry_module.require_approved_v2_schema_id(schema_id)


def test_tracked_broker_auth_goldens_have_fixed_unsigned_and_lease_identity_bytes():
    """Golden bytes are fixtures, never generated by the production codec in this test."""
    root = ACCEPTANCE_ROOT / "fixtures" / "e2-v2" / "registration-broker-auth"
    envelope = json.loads((root / "registration-capability-envelope-golden-v1.json").read_bytes())
    lease = json.loads((root / "registration-recovery-lease-identity-golden-v1.json").read_bytes())

    assert set(envelope) == {"preimageHex", "publicKeyDigest", "publicKeyHex", "signatureHex", "signedWireHex", "unsignedBytesHex"}
    assert len(envelope["signatureHex"]) == 128
    assert envelope["signatureHex"] == envelope["signatureHex"].lower()
    assert len(bytes.fromhex(envelope["publicKeyHex"])) == 32
    unsigned = bytes.fromhex(envelope["unsignedBytesHex"])
    signed = bytes.fromhex(envelope["signedWireHex"])
    assert CapabilityEnvelopeUnsignedV1.from_canonical_bytes(unsigned).canonical_bytes == unsigned
    assert b"brokerSignatureHex" not in unsigned
    assert signed.count(b"brokerSignatureHex") == 1
    assert bytes.fromhex(envelope["preimageHex"]) == b"ai-video-reverse-engineer/registration-broker-envelope/v1\0" + unsigned
    assert envelope["publicKeyDigest"] == "sha256:" + hashlib.sha256(bytes.fromhex(envelope["publicKeyHex"])).hexdigest()
    signed_envelope = CapabilityEnvelopeV1.from_canonical_bytes(signed)
    assert signed_envelope.unsigned.canonical_bytes == unsigned
    signed_envelope.verify({"broker-fixed": bytes.fromhex(envelope["publicKeyHex"])})
    assert set(lease) == {"leaseIdentityDigest", "preimageBytesHex"}
    assert lease["leaseIdentityDigest"].startswith("sha256:")
    preimage = RegistrationRecoveryLeaseIdentityPreimageV1.from_canonical_bytes(
        bytes.fromhex(lease["preimageBytesHex"])
    )
    assert preimage.lease_identity_digest == lease["leaseIdentityDigest"]


def test_tracked_broker_auth_issue_chain_golden_is_present_without_private_material():
    """The fixed Task 14 decoder contract must include the full public lease issue chain."""
    root = ACCEPTANCE_ROOT / "fixtures" / "e2-v2" / "registration-broker-auth"
    chain = json.loads((root / "registration-recovery-lease-issue-chain-golden-v1.json").read_bytes())

    assert set(chain) == {
        "entryBytesHex", "familyAuthorizationBytesHex", "issuePayloadBytesHex", "journalBytesHex",
        "leaseIdentityPreimageBytesHex", "publicKeyHex", "registryBytesHex", "requestBytesHex",
        "revocationsBytesHex", "signedLeaseBytesHex", "stateBytesHex",
    }
    assert not any("private" in key.lower() or "seed" in key.lower() for key in chain)

    decode = lambda name: bytes.fromhex(chain[name])
    registry = RegistrationBrokerAuthRegistryV1.from_canonical_bytes(decode("registryBytesHex"))
    revocations = RegistrationBrokerAuthRevocationsV1.from_canonical_bytes(decode("revocationsBytesHex"))
    family = RegistrationWorkerSessionFamilyAuthorizationV1.from_canonical_bytes(decode("familyAuthorizationBytesHex"))
    request = BrokerAuthLeaseIssueRequestV1.from_canonical_bytes(decode("requestBytesHex"))
    payload = BrokerAuthLeaseIssueEventPayloadV1.from_canonical_bytes(decode("issuePayloadBytesHex"))
    entry = BrokerAuthSequenceJournalEntryV1.from_canonical_bytes(decode("entryBytesHex"))
    state = BrokerAuthSequenceStateV1.from_canonical_bytes(decode("stateBytesHex"))
    journal = BrokerAuthSequenceJournalV1.from_canonical_bytes(decode("journalBytesHex"))
    lease_preimage = RegistrationRecoveryLeaseIdentityPreimageV1.from_canonical_bytes(decode("leaseIdentityPreimageBytesHex"))
    lease = RegistrationRecoveryLeaseV1.from_canonical_bytes(decode("signedLeaseBytesHex"))

    assert family.canonical_bytes == decode("familyAuthorizationBytesHex")
    assert request.canonical_bytes == decode("requestBytesHex")
    assert payload.canonical_bytes == decode("issuePayloadBytesHex")
    assert entry.canonical_bytes == decode("entryBytesHex")
    assert state.canonical_bytes == decode("stateBytesHex")
    assert journal.canonical_bytes == decode("journalBytesHex")
    assert lease_preimage.canonical_bytes == decode("leaseIdentityPreimageBytesHex")
    assert lease.canonical_bytes == decode("signedLeaseBytesHex")
    assert payload.lease_request == request
    assert payload.lease_request_digest == request.request_digest
    assert entry.event_payload_ref == payload.canonical_digest
    assert lease.identity_preimage() == lease_preimage
    assert lease.lease_identity_digest == lease_preimage.lease_identity_digest
    state_mac_key = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    entry.verify_state_mac(state_mac_key)
    state.verify_state_mac(state_mac_key)
    journal.verify_state_mac(state_mac_key)
    journal.validate_state(state, {payload.canonical_digest: payload})
    lease.verify_signature(registry, revocations, {"broker-fixed": bytes.fromhex(chain["publicKeyHex"])}, state.current_sequence)


DIGEST = "sha256:" + "a" * 64
COMMIT = ProductGitObjectIdV1("sha1", "b" * 40)


def _record(**overrides):
    values = {
        "schema": "trusted-acceptance-handoff/v1",
        "handoff_id": "ticket-23-product-v1",
        "kind": "product-source",
        "producer_task": 22,
        "producer_commit": COMMIT,
        "consumer_tasks": (23,),
        "bindings": (("product-source-container", DIGEST),),
    }
    values.update(overrides)
    return TrustedHandoffRecordV1(**values)


def _source(commit=COMMIT):
    return ProductRepositorySourceV1(
        subject_repository="product",
        subject_commit=commit,
        source_ref=DIGEST,
        source_digest=DIGEST,
        container_format="trusted-product-raw-git-tar-zstd/v1",
        object_format="sha1",
        budget=ProductSourceBudgetV1(),
        boundary_digest=DIGEST,
        tracked_checkout_digest=DIGEST,
        control_metadata_digest=DIGEST,
        raw_inspection_proof_ref=DIGEST,
    )


def test_product_source_handoff_requires_same_record_source_and_view_commit():
    """A producer commit mismatch must be rejected before handoff consumption."""
    record = _record()
    handoff = Ticket23ProductSourceHandoffV1(record, _source(), COMMIT)

    assert handoff.producer_commit == COMMIT
    with pytest.raises(ValueError):
        Ticket23ProductSourceHandoffV1(record, _source(), ProductGitObjectIdV1("sha1", "c" * 40))


def test_record_has_no_self_digest_and_pointer_binds_full_canonical_bytes():
    """Pointer authentication covers literal record bytes, not a self-reported digest."""
    record = _record()
    record_bytes = record.canonical_bytes
    record_digest = "sha256:" + hashlib.sha256(record_bytes).hexdigest()
    pointer = HandoffPointerV1(
        schema="trusted-acceptance-handoff-pointer/v1",
        handoff_id=record.handoff_id,
        record_ref=record_digest,
        record_digest=record_digest,
        producer_task=22,
        producer_commit=COMMIT,
    )

    assert b"recordDigest" not in record_bytes
    assert pointer.record_ref == record_digest
    assert pointer.record_digest == record_digest


def test_product_source_rejects_host_path():
    """A durable product source must not retain an ambient repository path."""
    with pytest.raises(ValueError):
        ProductRepositorySourceV1(
            subject_repository="/host/repository",
            subject_commit=COMMIT,
            source_ref=DIGEST,
            source_digest=DIGEST,
            container_format="trusted-product-raw-git-tar-zstd/v1",
            object_format="sha1",
            budget=ProductSourceBudgetV1(),
            boundary_digest=DIGEST,
            tracked_checkout_digest=DIGEST,
            control_metadata_digest=DIGEST,
            raw_inspection_proof_ref=DIGEST,
        )


def test_product_source_budget_and_handoff_wire_are_closed_by_kind(tmp_path):
    """Budget bytes and kind are authenticated; policy cannot use a second wire."""
    product = _record()
    policy_commit = GitObjectIdV1("sha256", "c" * 64)
    policy = TrustedHandoffRecordV1(
        "trusted-acceptance-handoff/v1", "ticket-23-policy-v1", "policy", 27,
        policy_commit, (28,), (("policy-payload", DIGEST),),
    )
    pointer = HandoffPointerV1(
        "trusted-acceptance-handoff-pointer/v1", policy.handoff_id,
        policy.record_digest, policy.record_digest, policy.producer_task,
        policy.producer_commit,
    )

    source = _source()
    assert source.to_wire()["budget"] == source.budget.to_wire()
    assert ProductRepositorySourceV1.from_canonical_bytes(source.canonical_bytes).budget == source.budget
    assert PolicyHandoffRecordV1 is TrustedHandoffRecordV1
    assert PolicyHandoffPointerV1 is HandoffPointerV1
    assert PolicyHandoffRecordV1.from_canonical_bytes(policy.canonical_bytes) == policy
    assert HandoffPointerV1.from_canonical_bytes(pointer.canonical_bytes) == pointer

    with pytest.raises(ValueError):
        TrustedHandoffRecordV1(
            "trusted-acceptance-handoff/v1", product.handoff_id, "policy", 22,
            COMMIT, (23,), product.bindings,
        )
    wrong_pointer = HandoffPointerV1(
        "trusted-acceptance-handoff-pointer/v1", product.handoff_id,
        product.record_digest, product.record_digest, 27, policy_commit,
    )
    root = (tmp_path / "kind-store").resolve()
    root.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        HandoffRegistryV1(TrustedHandoffStoreV1(root)).publish_local(product, wrong_pointer)
    tampered = json.loads(source.canonical_bytes)
    tampered["budget"]["maxPackBytes"] -= 1
    with pytest.raises(ValueError):
        ProductRepositorySourceV1.from_canonical_bytes(canonical_json_bytes(tampered))


def test_startup_challenge_has_one_public_owner():
    """A same-named handoff stub must never shadow the broker challenge verifier."""
    import trusted_evidence.handoffs as handoffs

    assert not hasattr(handoffs, "verify_registration_broker_startup_challenge")
    assert verify_registration_broker_startup_challenge.__module__ == "trusted_evidence.e2_policy"


def test_handoff_store_publishes_content_addressed_blob_and_no_replace_pointer(tmp_path, monkeypatch):
    """A record/pointer collision must never replace bytes selected by another producer."""
    root = (tmp_path / "external-store").resolve()
    root.mkdir(mode=0o700)
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_ROOT", str(root))
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_LEASE", _store_lease(root))
    store = TrustedHandoffStoreV1.open_from_environment()
    payload = b'{"record":"one"}'
    ref = store.put_blob(payload)

    assert ref == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert store.read_blob(ref) == payload
    pointer = b'{"pointer":"one"}'
    assert store.publish_pointer("ticket-23-product-v1", pointer) is False
    assert store.publish_pointer("ticket-23-product-v1", pointer) is True
    with pytest.raises(ValueError):
        store.publish_pointer("ticket-23-product-v1", b'{"pointer":"two"}')


@pytest.mark.parametrize("root_value", ("relative-store",))
def test_handoff_store_rejects_nonabsolute_or_symlink_root(tmp_path, monkeypatch, root_value):
    """An ambient or aliased store root cannot carry durable handoff authority."""
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_ROOT", root_value)
    with pytest.raises(ValueError):
        TrustedHandoffStoreV1.open_from_environment()
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_ROOT", str(link))
    with pytest.raises(ValueError):
        TrustedHandoffStoreV1.open_from_environment()


def _store_lease(root: Path, *, project_id: str = "ai-video-reverse-engineer") -> str:
    """Literal broker lease fixture bound to this owning directory identity."""
    identity = root.stat()
    return canonical_json_bytes({
        "projectId": project_id,
        "rootDevice": identity.st_dev,
        "rootInode": identity.st_ino,
        "schema": "trusted-handoff-store-lease/v1",
    }).decode("utf-8")


def test_handoff_store_environment_requires_a_matching_project_lease(tmp_path, monkeypatch):
    """Removing or rebinding the broker lease must reject before a store capability is issued."""
    root = (tmp_path / "leased-store").resolve()
    root.mkdir(mode=0o700)
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_ROOT", str(root))

    with pytest.raises(ValueError, match="lease"):
        TrustedHandoffStoreV1.open_from_environment()

    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_LEASE", _store_lease(root, project_id="other-project"))
    with pytest.raises(ValueError, match="lease"):
        TrustedHandoffStoreV1.open_from_environment()

    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_LEASE", _store_lease(root))
    assert TrustedHandoffStoreV1.open_from_environment().read_blob is not None


def test_handoff_store_rejects_ancestor_symlink_and_root_swap_before_visible_write(tmp_path, monkeypatch):
    """Replacing either path component after capability issuance must not redirect a pointer write."""
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    nested = alias / "store"
    (target / "store").mkdir(mode=0o700)
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_ROOT", str(nested))
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_LEASE", _store_lease(target / "store"))
    with pytest.raises(ValueError):
        TrustedHandoffStoreV1.open_from_environment()

    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    store = TrustedHandoffStoreV1(root)
    original = tmp_path / "original"
    root.rename(original)
    root.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="binding"):
        store.publish_pointer("ticket-23-product-v1", b'{"redirected":true}')
    assert list(root.iterdir()) == []


def test_handoff_store_rejects_swapped_entry_parent_without_writing_through_symlink(tmp_path):
    """A parent entry changed to a symlink after open must fail rather than create files in its target."""
    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    store = TrustedHandoffStoreV1(root)
    (root / "pointers").mkdir(mode=0o700)
    redirected = tmp_path / "redirected"
    redirected.mkdir(mode=0o700)
    (root / "pointers").rmdir()
    (root / "pointers").symlink_to(redirected, target_is_directory=True)

    with pytest.raises(ValueError):
        store.publish_pointer("ticket-23-product-v1", b'{"pointer":"one"}')
    assert list(redirected.iterdir()) == []


def test_handoff_store_rejects_device_rebind_before_write(tmp_path, monkeypatch):
    """A changed held-root device identity must stop a publish before it becomes visible."""
    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    store = TrustedHandoffStoreV1(root)
    real_fstat = os.fstat

    def rebound(descriptor):
        result = real_fstat(descriptor)
        if descriptor == store._root_fd:
            fields = list(result)
            fields[2] += 1
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(os, "fstat", rebound)
    with pytest.raises(ValueError, match="binding"):
        store.publish_pointer("ticket-23-product-v1", b'{"pointer":"one"}')
    assert not (root / "pointers").exists()


def test_handoff_store_rejects_ancestor_owner_or_mode_identity_drift_before_write(tmp_path):
    """A lease cannot outlive an ancestor permission identity change."""
    ancestor = (tmp_path / "ancestor").resolve()
    root = ancestor / "store"
    ancestor.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    store = TrustedHandoffStoreV1(root)
    ancestor.chmod(0o755)

    with pytest.raises(ValueError, match="binding"):
        store.publish_pointer("ticket-23-product-v1", b'{"pointer":"one"}')
    assert not (root / "pointers").exists()


def test_handoff_store_concurrent_publication_is_idempotent_only_for_identical_bytes(tmp_path):
    """Concurrent publishers may converge on identical bytes but never mix competing payloads."""
    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    same = (TrustedHandoffStoreV1(root), TrustedHandoffStoreV1(root))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda item: item.publish_pointer("ticket-23-product-v1", b'{"pointer":"same"}'), same))
    assert sorted(outcomes) == [False, True]
    assert TrustedHandoffStoreV1(root).read_pointer("ticket-23-product-v1") == b'{"pointer":"same"}'

    different_root = (tmp_path / "different-store").resolve()
    different_root.mkdir(mode=0o700)
    competing = (TrustedHandoffStoreV1(different_root), TrustedHandoffStoreV1(different_root))

    def publish(pair):
        try:
            return pair[0].publish_pointer("ticket-23-product-v1", pair[1])
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ((competing[0], b'{"pointer":"one"}'), (competing[1], b'{"pointer":"two"}'))))
    assert results.count(None) == 1
    assert sum(isinstance(result, bool) for result in results) == 1


def test_handoff_store_rejects_post_link_entry_swap_without_accepting_redirected_bytes(tmp_path, monkeypatch):
    """An attacker replacing the target after link must turn the publication into a failure."""
    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    store = TrustedHandoffStoreV1(root)
    real_link = os.link

    def link_then_swap(source, destination, *, src_dir_fd, dst_dir_fd, follow_symlinks):
        real_link(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks)
        os.unlink(destination, dir_fd=dst_dir_fd)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dst_dir_fd)
        try:
            os.write(descriptor, b'{"pointer":"evil"}')
        finally:
            os.close(descriptor)

    monkeypatch.setattr(os, "link", link_then_swap)
    with pytest.raises(ValueError, match="entry changed"):
        store.publish_pointer("ticket-23-product-v1", b'{"pointer":"good"}')
    assert store.read_pointer("ticket-23-product-v1") == b'{"pointer":"evil"}'


def test_handoff_store_removes_its_link_when_final_binding_check_fails(tmp_path, monkeypatch):
    """A late binding failure must not leave this publisher's newly linked file behind."""
    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    store = TrustedHandoffStoreV1(root)
    original = TrustedHandoffStoreV1._verify_binding
    calls = 0

    def fail_after_link(self):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError("handoff store root binding changed")
        original(self)

    monkeypatch.setattr(TrustedHandoffStoreV1, "_verify_binding", fail_after_link)
    with pytest.raises(ValueError, match="binding"):
        store.publish_pointer("ticket-23-product-v1", b'{"pointer":"good"}')
    assert not (root / "pointers" / "ticket-23-product-v1.json").exists()


def test_handoff_store_capability_cannot_be_copied_or_dataclass_serialized(tmp_path):
    """A directory FD capability must not be duplicated through generic object helpers."""
    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    store = TrustedHandoffStoreV1(root)

    with pytest.raises(TypeError):
        copy.copy(store)
    with pytest.raises(TypeError):
        copy.replace(store)
    with pytest.raises(TypeError):
        replace(store)
    with pytest.raises(TypeError):
        asdict(store)


def test_export_request_has_closed_sorted_consumers_and_full_record_binding():
    """Changing a consumer or a pointer byte changes the export authorization."""
    request = HandoffExportRequestV1(
        schema="trusted-handoff-export-request/v1",
        handoff_id="ticket-23-product-v1",
        record_ref=DIGEST,
        record_full_bytes_digest=DIGEST,
        pointer_bytes_digest="sha256:" + "b" * 64,
        consumer_tasks=(23, 28),
        binding_set_digest="sha256:" + "c" * 64,
    )
    assert request.canonical_bytes == b'{"bindingSetDigest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","consumerTasks":[23,28],"handoffId":"ticket-23-product-v1","pointerBytesDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","recordFullBytesDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","recordRef":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","schema":"trusted-handoff-export-request/v1"}'
    with pytest.raises(ValueError):
        HandoffExportRequestV1("trusted-handoff-export-request/v1", "ticket-23-product-v1", DIGEST, DIGEST, "sha256:" + "b" * 64, (28, 23), "sha256:" + "c" * 64)


def test_handoff_cli_fails_closed_before_finalize_and_for_reserved_producer(tmp_path):
    """Lifecycle read and privileged producer publication must not be ambient shortcuts."""
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    environment = {**os.environ, "TRUSTED_HANDOFF_STORE_ROOT": str(root), "TRUSTED_HANDOFF_STORE_LEASE": _store_lease(root), "PYTHONPATH": str(Path(__file__).parent)}
    cli = Path(__file__).parent / "tools" / "trusted_handoff_store.py"
    read = subprocess.run([sys.executable, str(cli), "read", "ticket-23-product-v1"], env=environment, capture_output=True)
    publish = subprocess.run([sys.executable, str(cli), "publish-local", "--producer-task", "22"], env=environment, capture_output=True)
    export = subprocess.run([sys.executable, str(cli), "publish-export-request", "--producer-task", "27", "--request", str(tmp_path / "missing-request")], env=environment, capture_output=True)
    assert read.returncode != 0
    assert publish.returncode != 0
    assert export.returncode != 0
    assert b"not finalized" in read.stderr
    assert b"reserved producer" in publish.stderr
    assert b"reserved producer" in export.stderr


def test_handoff_cli_allows_a_nonreserved_export_request_producer(tmp_path):
    """The explicit gate blocks privileged producers without disabling ordinary export."""
    root = (tmp_path / "store").resolve()
    root.mkdir(mode=0o700)
    source = TrustedHandoffStoreV1(root)
    document = _source()
    document_ref = source.put_blob(document.canonical_bytes)
    record = _record(bindings=(("product-source", document_ref),))
    pointer = HandoffPointerV1(
        "trusted-acceptance-handoff-pointer/v1", record.handoff_id, record.record_digest,
        record.record_digest, 22, COMMIT,
    )
    request = HandoffExportRequestV1(
        "trusted-handoff-export-request/v1", record.handoff_id, record.record_digest,
        record.record_digest, "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(),
        (23,), record.binding_set_digest,
    )
    HandoffRegistryV1(source).publish_local(record, pointer, (document.canonical_bytes,))
    request_path = tmp_path / "request.json"
    request_path.write_bytes(request.canonical_bytes)
    environment = {**os.environ, "TRUSTED_HANDOFF_STORE_ROOT": str(root), "TRUSTED_HANDOFF_STORE_LEASE": _store_lease(root), "PYTHONPATH": str(Path(__file__).parent)}
    cli = Path(__file__).parent / "tools" / "trusted_handoff_store.py"

    result = subprocess.run([sys.executable, str(cli), "publish-export-request", "--producer-task", "23", "--request", str(request_path)], env=environment, capture_output=True)

    assert result.returncode == 0
    assert result.stdout.strip().decode("ascii") == "sha256:" + hashlib.sha256(request.canonical_bytes).hexdigest()


def test_registry_refuses_unsealed_fetch_and_import_before_consumer_write(tmp_path):
    """A canonical export request alone never authorizes consumer-visible bytes."""
    source_root = (tmp_path / "source").resolve()
    cache_root = (tmp_path / "cache").resolve()
    source_root.mkdir(mode=0o700)
    cache_root.mkdir(mode=0o700)
    source = TrustedHandoffStoreV1(source_root)
    source_document = _source()
    source_ref = source.put_blob(source_document.canonical_bytes)
    record = _record(bindings=(("product-source", source_ref),))
    pointer = HandoffPointerV1(
        schema="trusted-acceptance-handoff-pointer/v1",
        handoff_id=record.handoff_id,
        record_ref=record.record_digest,
        record_digest=record.record_digest,
        producer_task=22,
        producer_commit=COMMIT,
    )
    request = HandoffExportRequestV1(
        schema="trusted-handoff-export-request/v1",
        handoff_id=record.handoff_id,
        record_ref=record.record_digest,
        record_full_bytes_digest=record.record_digest,
        pointer_bytes_digest="sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(),
        consumer_tasks=(23,),
        binding_set_digest=record.binding_set_digest,
    )
    producer = HandoffRegistryV1(source)
    producer.publish_local(record, pointer, (source_document.canonical_bytes,))
    request_ref = producer.publish_export_request(request)

    consumer = HandoffRegistryV1(TrustedHandoffStoreV1(cache_root))
    with pytest.raises(ValueError, match="authenticated transport"):
        consumer.fetch(source, request_ref, record.handoff_id, consumer_task=23)
    with pytest.raises(ValueError, match="fetched handoff is missing"):
        consumer.import_(record.handoff_id, consumer_task=23)
    assert list(cache_root.iterdir()) == []


def test_registry_requires_an_authenticated_transport_before_reading_request_bytes(tmp_path):
    """An unsealed request cannot reach the consumer cache, canonical or otherwise."""
    source_root = (tmp_path / "source").resolve()
    cache_root = (tmp_path / "cache").resolve()
    source_root.mkdir(mode=0o700)
    cache_root.mkdir(mode=0o700)
    source = TrustedHandoffStoreV1(source_root)
    raw = b'{"bindingSetDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "consumerTasks":[23],"handoffId":"x","pointerBytesDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","recordFullBytesDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","recordRef":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","schema":"trusted-handoff-export-request/v1"}'
    ref = source.put_blob(raw)

    with pytest.raises(ValueError, match="authenticated transport"):
        HandoffRegistryV1(TrustedHandoffStoreV1(cache_root)).fetch(source, ref, "x", consumer_task=23)
    assert list(cache_root.iterdir()) == []


def test_handoff_cli_verifies_cache_and_cleans_only_a_narrow_owned_root(tmp_path):
    """The cleanup command removes an explicit 0700 child but rejects its broad parent."""
    root = tmp_path / "store"
    cache = tmp_path / "cache"
    owned = tmp_path / "owned"
    root.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    owned.mkdir(mode=0o700)
    (owned / "readonly").write_text("x")
    (owned / "readonly").chmod(0o400)
    environment = {**os.environ, "TRUSTED_HANDOFF_STORE_ROOT": str(root), "PYTHONPATH": str(Path(__file__).parent)}
    cli = Path(__file__).parent / "tools" / "trusted_handoff_store.py"

    verify_cache = subprocess.run([sys.executable, str(cli), "verify-cache", "--cache-root", str(cache), "--require-empty", "--expected-mode", "0700"], env=environment, capture_output=True)
    cleanup = subprocess.run([sys.executable, str(cli), "cleanup-owned-tree", "--root", str(owned), "--expected-mode", "0700"], env=environment, capture_output=True)
    broad_cleanup = subprocess.run([sys.executable, str(cli), "cleanup-owned-tree", "--root", str(Path.cwd()), "--expected-mode", "0700"], env=environment, capture_output=True)

    assert verify_cache.returncode == 0
    assert cleanup.returncode == 0
    assert not owned.exists()
    assert broad_cleanup.returncode != 0


def test_cleanup_rejects_ancestor_symlink_and_hardlink_aliases(tmp_path):
    """A cleanup root and every regular entry must have one stable local identity."""
    cli = ACCEPTANCE_ROOT / "tools" / "trusted_handoff_store.py"
    environment = {**os.environ, "PYTHONPATH": str(ACCEPTANCE_ROOT)}
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    alias_parent = tmp_path / "parent-alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    aliased_root = real_parent / "owned"
    aliased_root.mkdir(mode=0o700)
    (aliased_root / "payload").write_text("safe")

    ancestor_symlink = subprocess.run(
        [sys.executable, str(cli), "cleanup-owned-tree", "--root", str(alias_parent / "owned"), "--expected-mode", "0700"],
        env=environment,
        capture_output=True,
    )
    assert ancestor_symlink.returncode != 0
    assert aliased_root.exists()

    owned = tmp_path / "hardlink-owned"
    owned.mkdir(mode=0o700)
    external = tmp_path / "external-payload"
    external.write_text("must-survive")
    os.link(external, owned / "payload")
    hardlink_alias = subprocess.run(
        [sys.executable, str(cli), "cleanup-owned-tree", "--root", str(owned), "--expected-mode", "0700"],
        env=environment,
        capture_output=True,
    )
    assert hardlink_alias.returncode != 0
    assert owned.exists()
    assert external.read_text() == "must-survive"


def test_cleanup_detects_entry_replacement_before_unlink(tmp_path, monkeypatch):
    """Replacing a file after its no-follow check must stop before unlinking either file."""
    tool = _cleanup_tool_module()
    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    (owned / "payload").write_text("original")
    original_chmod = tool.os.chmod
    swapped = False

    def swap_after_chmod(name, mode, *, dir_fd=None, follow_symlinks=True):
        nonlocal swapped
        original_chmod(name, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if name == "payload" and dir_fd is not None and not swapped:
            swapped = True
            tool.os.rename("payload", "escaped-original", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            replacement_fd = tool.os.open("payload", tool.os.O_WRONLY | tool.os.O_CREAT | tool.os.O_EXCL, 0o600, dir_fd=dir_fd)
            tool.os.close(replacement_fd)

    monkeypatch.setattr(tool.os, "chmod", swap_after_chmod)
    with pytest.raises(ValueError, match="cleanup tree entry identity changed"):
        tool._cleanup_owned_tree(str(owned), 0o700)
    assert (owned / "payload").exists()
    assert (owned / "escaped-original").read_text() == "original"


def test_cleanup_rejects_root_replacement_during_identity_validation(tmp_path, monkeypatch):
    """A root swapped into a symlink cannot redirect cleanup to the symlink target."""
    tool = _cleanup_tool_module()
    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    (owned / "payload").write_text("original")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    (target / "must-survive").write_text("safe")
    original_resolve = tool.Path.resolve
    swapped = False

    def swap_root_on_resolve(path, *args, **kwargs):
        nonlocal swapped
        if path == owned and not swapped:
            swapped = True
            owned.rename(tmp_path / "escaped-owned")
            owned.symlink_to(target, target_is_directory=True)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(tool.Path, "resolve", swap_root_on_resolve)
    with pytest.raises(ValueError, match="cleanup root"):
        tool._cleanup_owned_tree(str(owned), 0o700)
    assert (target / "must-survive").read_text() == "safe"


def test_cleanup_rechecks_the_root_identity_captured_before_opening_parent(tmp_path, monkeypatch):
    """A same-owner replacement between lexical validation and FD traversal is not approved."""
    tool = _cleanup_tool_module()
    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    (owned / "original").write_text("must-survive")
    original_open_directory_path = tool._open_directory_path
    swapped = False

    def swap_before_opening_parent(path):
        nonlocal swapped
        if path == owned.parent and not swapped:
            swapped = True
            owned.rename(tmp_path / "escaped-owned")
            owned.mkdir(mode=0o700)
            (owned / "replacement").write_text("unapproved")
        return original_open_directory_path(path)

    monkeypatch.setattr(tool, "_open_directory_path", swap_before_opening_parent)
    with pytest.raises(ValueError, match="cleanup root identity changed"):
        tool._cleanup_owned_tree(str(owned), 0o700)
    assert (tmp_path / "escaped-owned" / "original").read_text() == "must-survive"
    assert (owned / "replacement").read_text() == "unapproved"


def test_cleanup_detects_root_replacement_after_the_last_predelete_check(tmp_path, monkeypatch):
    """A swap in the final unlink window is reported after the FD proves the old root survived."""
    tool = _cleanup_tool_module()
    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    (owned / "original").write_text("must-survive")
    original_rmdir = tool.os.rmdir
    swapped = False

    def swap_before_rmdir(name, *, dir_fd=None):
        nonlocal swapped
        if name == "owned" and dir_fd is not None and not swapped:
            swapped = True
            owned.rename(tmp_path / "escaped-owned")
            owned.mkdir(mode=0o700)
        original_rmdir(name, dir_fd=dir_fd)

    monkeypatch.setattr(tool.os, "rmdir", swap_before_rmdir)
    with pytest.raises(ValueError, match="cleanup root identity changed"):
        tool._cleanup_owned_tree(str(owned), 0o700)
    assert (tmp_path / "escaped-owned").exists()


def test_cleanup_rejects_a_preexisting_child_mount_identity(tmp_path):
    """A tree cannot cross into a child mount even before deletion begins."""
    tool = _cleanup_tool_module()
    owned = tmp_path / "owned"
    child = owned / "child"
    owned.mkdir(mode=0o700)
    child.mkdir(mode=0o700)
    root_inode = owned.stat().st_ino
    child_inode = child.stat().st_ino

    def mount_identity(fd):
        inode = os.fstat(fd).st_ino
        if inode == root_inode:
            return 1
        if inode == child_inode:
            return 2
        return 1

    with pytest.raises(ValueError, match="mount identity changed"):
        tool._cleanup_owned_tree(str(owned), 0o700, mount_identity=mount_identity)
    assert child.exists()


def test_cleanup_rejects_mount_identity_change_before_deletion(tmp_path):
    """The injectable test seam proves the production path rechecks kernel mount identity."""
    tool = _cleanup_tool_module()
    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    root_inode = owned.stat().st_ino
    root_calls = 0

    def mount_identity(fd):
        nonlocal root_calls
        if os.fstat(fd).st_ino == root_inode:
            root_calls += 1
            return root_calls
        return 0

    with pytest.raises(ValueError, match="mount identity changed"):
        tool._cleanup_owned_tree(str(owned), 0o700, mount_identity=mount_identity)
    assert owned.exists()


def test_cleanup_rejects_wrong_mode_or_owner_without_deleting(tmp_path):
    """Mode is exercised against a real tree; owner comes from the injectable kernel-fact seam."""
    tool = _cleanup_tool_module()
    wrong_mode = tmp_path / "wrong-mode"
    wrong_mode.mkdir(mode=0o700)
    wrong_mode.chmod(0o755)
    with pytest.raises(ValueError, match="expected mode"):
        tool._cleanup_owned_tree(str(wrong_mode), 0o700)
    assert wrong_mode.exists()

    wrong_owner = tmp_path / "wrong-owner"
    wrong_owner.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="owned"):
        tool._cleanup_owned_tree(str(wrong_owner), 0o700, owner_id=lambda: os.getuid() + 1)
    assert wrong_owner.exists()


@pytest.mark.parametrize("first_invalid", (True, False))
def test_cleanup_tries_every_root_and_reports_any_failure(tmp_path, first_invalid):
    """One failing root cannot prevent or erase the other cleanup attempt."""
    cli = ACCEPTANCE_ROOT / "tools" / "trusted_handoff_store.py"
    environment = {**os.environ, "PYTHONPATH": str(ACCEPTANCE_ROOT)}
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir(mode=0o700)
    bad.mkdir(mode=0o700)
    (good / "readonly").write_text("x")
    external = tmp_path / "external"
    external.write_text("must-survive")
    os.link(external, bad / "payload")
    roots = (bad, good) if first_invalid else (good, bad)

    completed = subprocess.run(
        [
            sys.executable, str(cli), "cleanup-owned-tree",
            "--root", str(roots[0]), "--root", str(roots[1]),
            "--expected-mode", "0700",
        ],
        env=environment,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert not good.exists()
    assert bad.exists()
    assert external.read_text() == "must-survive"


def test_cleanup_rejects_overlapping_roots_before_deleting_either_one(tmp_path):
    """A parent root cannot consume a separately requested child root."""
    cli = ACCEPTANCE_ROOT / "tools" / "trusted_handoff_store.py"
    environment = {**os.environ, "PYTHONPATH": str(ACCEPTANCE_ROOT)}
    parent = tmp_path / "parent"
    child = parent / "child"
    parent.mkdir(mode=0o700)
    child.mkdir(mode=0o700)
    (child / "payload").write_text("must-survive")

    completed = subprocess.run(
        [
            sys.executable, str(cli), "cleanup-owned-tree",
            "--root", str(parent), "--root", str(child),
            "--expected-mode", "0700",
        ],
        env=environment,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert (child / "payload").read_text() == "must-survive"


# This vector is built only from literals with the standard library.  The
# signature was independently fixed with the RFC 8032 reference algorithm for
# seed 9d61…e7f60; the production module is never asked to generate expected
# bytes or signatures.
FIXED_ACTIVATION_PUBLIC_KEY = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
FIXED_ACTIVATION_SIGNATURE = "4464c8bc88f64aa79c46729e27362f3b56a7763a66fac851ec81bfe1f4bd252d2e43f7a19b8e357f092fec467b8189700aabaac679884ee2515b60e66e62a203"
FIXED_ACTIVATION_PROOF_REF = "sha256:efa99efdeafd72475650bad8158058aa9fb7119f367c1c2e7b9bdb13e0ccbd47"


def _fixed_digest(character):
    return "sha256:" + character * 64


def _fixed_canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fixed_embedded(value):
    wire = _fixed_canonical(value)
    return {"canonicalBytesB64": base64.b64encode(wire).decode("ascii"), "digest": "sha256:" + hashlib.sha256(wire).hexdigest(), "encoding": "base64"}


def _activation_registry(public_key=FIXED_ACTIVATION_PUBLIC_KEY, *, key_id="activation-fixed"):
    return {
        "keys": [{
            "keyId": key_id,
            "purpose": "policy-activation",
            "signatureDomain": "ai-video-reverse-engineer/policy-activation/v1",
            "rawPublicKey": base64.b64encode(public_key).decode("ascii"),
            "wrapperIdentity": "activation-wrapper-fixed",
            "resolvedPublicKey": base64.b64encode(public_key).decode("ascii"),
        }],
    }


def _fixed_activation_proof_bytes():
    """Reference-only fixed activation chain; no trusted_evidence helper participates."""
    commit = {"algorithm": "sha1", "hex": "3" * 40}
    intent = {
        "activationMarkerDigest": None, "completionRecordDigest": _fixed_digest("a"), "completionRecordRef": _fixed_digest("b"),
        "entryDigest": _fixed_digest("c"), "exportRequestRef": _fixed_digest("d"),
        "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1", "fixedRefMustBeAbsent": True,
        "operationId": "policy-operation-fixed", "pendingTransition": "ACTIVATE", "pointerDigest": _fixed_digest("e"),
        "policyCommit": commit, "previousEntryDigest": _fixed_digest("f"), "recordRef": _fixed_digest("0"),
        "schema": "trusted-policy-publish-transaction/v1", "stagedPayloadDigest": _fixed_digest("1"),
        "state": "HANDOFF_INSTALLED", "targetHeadRef": "refs/heads/main",
    }
    embedded_intent = _fixed_embedded(intent)
    marker = {
        "activationIntentDigest": embedded_intent["digest"], "exportRequestRef": _fixed_digest("d"),
        "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1", "operationId": "policy-operation-fixed",
        "pointerDigest": _fixed_digest("e"), "policyCommit": commit, "recordRef": _fixed_digest("0"),
        "schema": "trusted-policy-activation/v1",
    }
    embedded_marker = _fixed_embedded(marker)
    activated = {
        "activationMarkerDigest": embedded_marker["digest"], "completionRecordDigest": _fixed_digest("a"),
        "completionRecordRef": _fixed_digest("b"), "entryDigest": _fixed_digest("2"), "exportRequestRef": _fixed_digest("d"),
        "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1", "fixedRefMustBeAbsent": True,
        "operationId": "policy-operation-fixed", "pendingTransition": None, "pointerDigest": _fixed_digest("e"),
        "policyCommit": commit, "previousEntryDigest": embedded_intent["digest"], "recordRef": _fixed_digest("0"),
        "schema": "trusted-policy-publish-transaction/v1", "stagedPayloadDigest": _fixed_digest("1"),
        "state": "ACTIVATED", "targetHeadRef": "refs/heads/main",
    }
    transport = _transport_registry()["keys"][0]
    activation = _activation_registry()["keys"][0]
    broker = _broker_registry()["keys"][0]
    trust_root = "sha256:" + hashlib.sha256(_fixed_canonical({
        "activation": ["sha256:" + hashlib.sha256(_fixed_canonical(activation)).hexdigest()],
        "broker": ["sha256:" + hashlib.sha256(_fixed_canonical(broker)).hexdigest()],
        "transport": ["sha256:" + hashlib.sha256(_fixed_canonical(transport)).hexdigest()],
    })).hexdigest()
    unsigned = {
        "activateIntent": embedded_intent, "activatedEntry": _fixed_embedded(activated), "activationMarker": embedded_marker,
        "activationTrustRootDigest": trust_root, "brokerKeyId": "activation-fixed", "completionRecordDigest": _fixed_digest("a"),
        "completionRecordRef": _fixed_digest("b"), "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1",
        "fixedRefValue": commit, "operationId": "policy-operation-fixed", "policyCommit": commit,
        "policyCoreExportRequestFullBytesDigest": _fixed_digest("2"), "policyCoreExportRequestRef": _fixed_digest("d"),
        "policyPointerBytesDigest": _fixed_digest("e"), "policyPointerName": "ticket-23-policy-v1",
        "policyRecordFullBytesDigest": _fixed_digest("4"), "policyRecordRef": _fixed_digest("0"), "profileDigest": _fixed_digest("5"),
        "schema": "trusted-policy-activation-proof/v1", "signatureAlgorithm": "ssh-ed25519",
        "signatureDomain": "ai-video-reverse-engineer/policy-activation/v1", "signingKeyPurpose": "policy-activation",
        "state": "ACTIVATED", "targetHeadRef": "refs/heads/main", "targetHeadValue": commit,
        "toolchainBuildRecordRef": _fixed_digest("6"), "trustedGitExecutableDigest": _fixed_digest("7"),
        "trustedGitViewDigest": _fixed_digest("8"),
    }
    return _fixed_canonical({**unsigned, "brokerSignature": FIXED_ACTIVATION_SIGNATURE})


def _transport_registry(public_key=bytes.fromhex("11" * 32), *, key_id="transport-fixed", wrapper="transport-wrapper-fixed"):
    return {
        "keys": [{
            "keyId": key_id,
            "purpose": "transport-seal",
            "signatureDomain": "ai-video-reverse-engineer/transport-seal/v1",
            "rawPublicKey": base64.b64encode(public_key).decode("ascii"),
            "wrapperIdentity": wrapper,
            "resolvedPublicKey": base64.b64encode(public_key).decode("ascii"),
        }],
    }


def _broker_registry(public_key=bytes.fromhex("22" * 32), *, key_id="broker-fixed", wrapper="broker-wrapper-fixed", resolved_public_key=None):
    resolved_public_key = public_key if resolved_public_key is None else resolved_public_key
    return {
        "keys": [{
            "keyId": key_id,
            "purpose": "registration-broker-auth",
            "signatureDomain": "ai-video-reverse-engineer/registration-broker-envelope/v1",
            "rawPublicKey": base64.b64encode(public_key).decode("ascii"),
            "wrapperIdentity": wrapper,
            "resolvedPublicKey": base64.b64encode(resolved_public_key).decode("ascii"),
        }],
    }


def _strict_broker_inputs(public_key=bytes.fromhex("22" * 32), *, key_id="broker-fixed", wrapper="broker-wrapper-fixed", resolved_public_key=None):
    resolved_public_key = public_key if resolved_public_key is None else resolved_public_key
    allowed = _broker_registry(public_key, key_id=key_id, wrapper=wrapper, resolved_public_key=resolved_public_key)
    entry = allowed["keys"][0]
    public_digest = "sha256:" + hashlib.sha256(public_key).hexdigest()
    resolved_digest = "sha256:" + hashlib.sha256(resolved_public_key).hexdigest()
    key = RegistrationBrokerAuthKeyRegistrationV1(
        "trusted-registration-broker-auth-key-registration/v1", key_id,
        "registration-broker-auth", "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "ai-video-reverse-engineer/registration-broker-lease/v1",
        "ai-video-reverse-engineer/registration-broker-startup-challenge/v1", "raw-ed25519-32",
        public_digest, public_digest, "sha256:" + hashlib.sha256(wrapper.encode("utf-8")).hexdigest(),
        resolved_digest, resolved_digest,
        "sha256:" + hashlib.sha256(canonical_json_bytes(entry)).hexdigest(), "active", 1, None,
    )
    registry = RegistrationBrokerAuthRegistryV1(
        "trusted-registration-broker-auth-registry/v1", 1, None, key_id, (key,)
    )
    revocations = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", registry.canonical_digest, ()
    )
    return registry.canonical_bytes, revocations.canonical_bytes, canonical_json_bytes(allowed)


def _allowed(registry):
    return {"keys": registry["keys"]}


def _domains(transport=None, activation=None, broker=None):
    transport = transport or _transport_registry()
    activation = activation or _activation_registry()
    broker = broker or _broker_registry()
    broker_registry, broker_revocations, broker_allowed = _strict_broker_inputs(
        base64.b64decode(broker["keys"][0]["rawPublicKey"]),
        key_id=broker["keys"][0]["keyId"],
        wrapper=broker["keys"][0]["wrapperIdentity"],
    )
    return load_disjoint_signing_domains(
        transport,
        activation,
        broker_registry,
        broker_revocations,
        broker_allowed,
    )


def test_signing_domains_accept_only_the_strict_broker_governance_closure():
    """Accepting a simplified broker registry would fork startup and loader trust state."""
    registry, revocations, allowed = _strict_broker_inputs()
    domains = load_disjoint_signing_domains(
        _transport_registry(), _activation_registry(), registry, revocations, allowed,
    )
    assert len(domains.broker_keys) == 1
    assert domains.broker_keys[0].key_id == "broker-fixed"
    with pytest.raises(ValueError):
        load_disjoint_signing_domains(
            _transport_registry(), _activation_registry(), b'{"keys":[]}', revocations, allowed,
        )


@pytest.mark.parametrize(
    "dimension",
    (
        "key-id",
        "raw-public-key",
        "raw-public-key-digest",
        "raw-public-key-fingerprint",
        "wrapper-identity",
        "resolved-leaf-public-key",
        "resolved-leaf-public-key-digest",
        "resolved-leaf-public-key-fingerprint",
    ),
)
def test_signing_domains_reject_each_broker_identity_dimension_collision(dimension):
    """Each broker identity projection must independently block a cross-domain collision."""
    options = {}
    if dimension == "key-id":
        options["key_id"] = "transport-fixed"
    elif dimension.startswith("raw-public"):
        options["public_key"] = bytes.fromhex("11" * 32)
    elif dimension == "wrapper-identity":
        options["wrapper"] = "transport-wrapper-fixed"
    else:
        options["resolved_public_key"] = bytes.fromhex("11" * 32)
    registry, revocations, allowed = _strict_broker_inputs(**options)
    with pytest.raises(ValueError):
        load_disjoint_signing_domains(
            _transport_registry(), _activation_registry(), registry, revocations, allowed,
        )


def test_fixed_activation_signer_verifies_complete_canonical_chain():
    """Changing proof bytes, signature binding, or any chain field must break this verifier."""
    domains = _domains()

    verified = verify_policy_activation_proof(
        _fixed_activation_proof_bytes(),
        domains,
        expected_transport={"consumerTask": 28, "destinationExecutorId": "executor-28"},
    )

    assert verified.proof_ref == FIXED_ACTIVATION_PROOF_REF
    assert verified.policy_commit.hex == "3" * 40


@pytest.mark.parametrize(
    "field",
    (
        "activateIntent", "activationMarker", "activatedEntry", "policyCommit", "completionRecordRef",
        "brokerSignature", "operationId", "state", "targetHeadRef", "fixedRef", "profileDigest",
        "toolchainBuildRecordRef", "trustedGitExecutableDigest", "trustedGitViewDigest",
        "activationTrustRootDigest", "policyRecordRef", "policyPointerBytesDigest",
        "policyCoreExportRequestRef",
    ),
)
def test_activation_proof_tampering_fails_closed(field):
    """Every signed proof component is authenticated before a policy handoff can be trusted."""
    proof = json.loads(_fixed_activation_proof_bytes())
    if field == "brokerSignature":
        proof[field] = "0" * 128
    elif field == "policyCommit":
        proof[field]["hex"] = "4" * 40
    elif field == "completionRecordRef":
        proof[field] = _fixed_digest("c")
    elif field in {"activateIntent", "activationMarker", "activatedEntry"}:
        proof[field]["digest"] = _fixed_digest("f")
    else:
        proof[field] = "tampered"
    with pytest.raises(ValueError):
        verify_policy_activation_proof(
            _fixed_canonical(proof), _domains(),
            expected_transport={"consumerTask": 28, "destinationExecutorId": "executor-28"},
        )


@pytest.mark.parametrize(
    "transport,activation,broker",
    (
        # Re-identifying a raw/leaf key must not evade domain isolation.
        (_transport_registry(), _activation_registry(bytes.fromhex("11" * 32)), _broker_registry()),
        # Reusing an ID with a different wrapper is likewise forbidden.
        (_transport_registry(), _activation_registry(key_id="transport-fixed"), _broker_registry()),
        # A wrapper/certificate identity is independently part of the closure.
        (_transport_registry(wrapper="shared-wrapper"), _activation_registry(), _broker_registry(wrapper="shared-wrapper")),
    ),
)
def test_signing_domains_reject_cross_purpose_identity_collisions(transport, activation, broker):
    """A collision in every identity dimension blocks proof/seal authorization before writes."""
    with pytest.raises(ValueError):
        _domains(transport, activation, broker)


@pytest.mark.parametrize(
    "field,value",
    (
        ("purpose", "transport-seal"),
        ("signatureDomain", "ai-video-reverse-engineer/transport-seal/v1"),
        ("purpose", None),
        ("signatureDomain", None),
    ),
)
def test_activation_registry_rejects_missing_or_wrong_purpose_domain(field, value):
    """Only an immutable activation-purpose registration may verify an activation proof."""
    activation = _activation_registry()
    if value is None:
        del activation["keys"][0][field]
    else:
        activation["keys"][0][field] = value
    with pytest.raises(ValueError):
        _domains(activation=activation)


def test_broker_registry_requires_unrevoked_exact_allowed_signer_closure():
    """Unknown, revoked, duplicate, or substituted broker keys cannot start a signing domain."""
    registry, revocations, allowed = _strict_broker_inputs()
    with pytest.raises(ValueError):
        load_disjoint_signing_domains(_transport_registry(), _activation_registry(), b'{"keys":[]}', revocations, allowed)
    with pytest.raises(ValueError):
        load_disjoint_signing_domains(_transport_registry(), _activation_registry(), registry, revocations, b'{"keys":[]}')
    duplicate = json.loads(allowed)
    duplicate["keys"].append(dict(duplicate["keys"][0]))
    with pytest.raises(ValueError):
        load_disjoint_signing_domains(_transport_registry(), _activation_registry(), registry, revocations, canonical_json_bytes(duplicate))


def test_broker_startup_challenge_uses_fixed_ed25519_vector_and_rejects_without_effects(tmp_path):
    """Wrong nonce/key/slot/domain/signature must fail before any child or repository action."""
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    public = _test_transport_public_key(seed)
    public_digest = "sha256:" + hashlib.sha256(public).hexdigest()
    key = RegistrationBrokerAuthKeyRegistrationV1(
        "trusted-registration-broker-auth-key-registration/v1", "broker-startup-fixed",
        "registration-broker-auth", "ai-video-reverse-engineer/registration-broker-envelope/v1",
        "ai-video-reverse-engineer/registration-broker-lease/v1",
        "ai-video-reverse-engineer/registration-broker-startup-challenge/v1", "raw-ed25519-32",
        public_digest, public_digest, None, public_digest, public_digest,
        "sha256:" + "a" * 64, "active", 1, None,
    )
    registry = RegistrationBrokerAuthRegistryV1(
        "trusted-registration-broker-auth-registry/v1", 1, None, key.key_id, (key,)
    )
    revocations = RegistrationBrokerAuthRevocationsV1(
        "trusted-registration-broker-auth-revocations/v1", registry.canonical_digest, ()
    )
    key_file = tmp_path / "key"; key_file.write_bytes(seed)
    descriptor = os.open(key_file, os.O_RDONLY)
    try:
        slot = RegistrationBrokerAuthPrivateKeySlotV1.load_from_readonly_fd(descriptor, key, public)
    finally:
        os.close(descriptor)
    try:
        signature = verify_registration_broker_startup_challenge(
            registry, revocations, {"broker-startup-fixed": public}, slot, "nonce-fixed"
        )
        assert signature == "e7dbf403d3df0371851fa72eb5fd9e98db3c228c18c66c48f10656898890c033944b5af0d3612564ba26a3b24f1e5ed413cea1715db1e8aa901878b641cb8b07"
        effects = {"childExec": 0, "repositoryWrites": 0, "fsyncs": 0}
        slot.close()
        with pytest.raises(ValueError):
            verify_registration_broker_startup_challenge(
                registry, revocations, {"broker-startup-fixed": public}, slot, "nonce-fixed"
            )
        assert effects == {"childExec": 0, "repositoryWrites": 0, "fsyncs": 0}
    finally:
        slot.close()


def _test_transport_public_key(seed: bytes) -> bytes:
    """Independent RFC 8032 public-key derivation for the fixed transport signer."""
    q = 2**255 - 19
    d = (-121665 * pow(121666, q - 2, q)) % q
    base_y = (4 * pow(5, q - 2, q)) % q
    base_x = pow(((base_y * base_y - 1) * pow((d * base_y * base_y + 1) % q, q - 2, q)) % q, (q + 3) // 8, q)
    if base_x & 1:
        base_x = q - base_x

    def add(left, right):
        x1, y1 = left
        x2, y2 = right
        denominator = (1 + d * x1 * x2 * y1 * y2) % q
        x3 = ((x1 * y2 + x2 * y1) * pow(denominator, q - 2, q)) % q
        y3 = ((y1 * y2 + x1 * x2) * pow((1 - d * x1 * x2 * y1 * y2) % q, q - 2, q)) % q
        return x3, y3

    scalar = int.from_bytes(hashlib.sha512(seed).digest()[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    point, result = (base_x, base_y), (0, 1)
    while scalar:
        if scalar & 1:
            result = add(result, point)
        point = add(point, point)
        scalar >>= 1
    return (result[1] | ((result[0] & 1) << 255)).to_bytes(32, "little")


def _test_ed25519_signature(seed: bytes, message: bytes) -> str:
    """Independent RFC 8032 signer used only to construct the activation fixture."""
    q, d = 2**255 - 19, (-121665 * pow(121666, 2**255 - 21, 2**255 - 19)) % (2**255 - 19)
    base_y = (4 * pow(5, q - 2, q)) % q
    base_x = pow(((base_y * base_y - 1) * pow((d * base_y * base_y + 1) % q, q - 2, q)) % q, (q + 3) // 8, q)
    if base_x & 1:
        base_x = q - base_x

    def add(left, right):
        x1, y1 = left; x2, y2 = right
        return (((x1 * y2 + x2 * y1) * pow((1 + d * x1 * x2 * y1 * y2) % q, q - 2, q)) % q, ((y1 * y2 + x1 * x2) * pow((1 - d * x1 * x2 * y1 * y2) % q, q - 2, q)) % q)

    def mul(point, scalar):
        result = (0, 1)
        while scalar:
            if scalar & 1:
                result = add(result, point)
            point = add(point, point); scalar >>= 1
        return result

    hashed = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(hashed[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    public = _test_transport_public_key(seed)
    nonce = int.from_bytes(hashlib.sha512(hashed[32:] + message).digest(), "little") % (2**252 + 27742317777372353535851937790883648493)
    point = mul((base_x, base_y), nonce)
    encoded_r = (point[1] | ((point[0] & 1) << 255)).to_bytes(32, "little")
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), "little") % (2**252 + 27742317777372353535851937790883648493)
    return (encoded_r + ((nonce + challenge * scalar) % (2**252 + 27742317777372353535851937790883648493)).to_bytes(32, "little")).hex()


def _policy_transport_fixture(tmp_path):
    from trusted_evidence.handoffs import Ticket23PolicyHandoffV1

    source_root = (tmp_path / "policy-source").resolve(); source_root.mkdir(mode=0o700)
    source = TrustedHandoffStoreV1(source_root)
    commit = GitObjectIdV1("sha256", "c" * 64)
    payload_ref = source.put_blob(b"policy payload")
    record = PolicyHandoffRecordV1("trusted-acceptance-handoff/v1", "ticket-23-policy-v1", "policy", 27, commit, (28,), (("policy-payload", payload_ref),))
    pointer = PolicyHandoffPointerV1("trusted-acceptance-handoff-pointer/v1", record.handoff_id, record.record_digest, record.record_digest, 27, commit)
    core_request = HandoffExportRequestV1("trusted-handoff-export-request/v1", record.handoff_id, record.record_digest, record.record_digest, "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), (28,), record.binding_set_digest)
    core_request_ref = "sha256:" + hashlib.sha256(core_request.canonical_bytes).hexdigest()
    activation_seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    transport_seed = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
    domains = _domains(transport=_transport_registry(_test_transport_public_key(transport_seed)), activation=_activation_registry(_test_transport_public_key(activation_seed)))
    digest = lambda char: "sha256:" + char * 64
    def embedded(value):
        wire = canonical_json_bytes(value)
        return {"canonicalBytesB64": base64.b64encode(wire).decode("ascii"), "digest": "sha256:" + hashlib.sha256(wire).hexdigest(), "encoding": "base64"}
    intent = {"activationMarkerDigest": None, "completionRecordDigest": digest("a"), "completionRecordRef": digest("b"), "entryDigest": digest("c"), "exportRequestRef": core_request_ref, "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1", "fixedRefMustBeAbsent": True, "operationId": "policy-operation-28", "pendingTransition": "ACTIVATE", "pointerDigest": "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), "policyCommit": commit.to_wire(), "previousEntryDigest": digest("d"), "recordRef": record.record_digest, "schema": "trusted-policy-publish-transaction/v1", "stagedPayloadDigest": digest("e"), "state": "HANDOFF_INSTALLED", "targetHeadRef": "refs/heads/main"}
    encoded_intent = embedded(intent)
    marker = {"activationIntentDigest": encoded_intent["digest"], "exportRequestRef": core_request_ref, "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1", "operationId": "policy-operation-28", "pointerDigest": "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), "policyCommit": commit.to_wire(), "recordRef": record.record_digest, "schema": "trusted-policy-activation/v1"}
    encoded_marker = embedded(marker)
    activated = {"activationMarkerDigest": encoded_marker["digest"], "completionRecordDigest": digest("a"), "completionRecordRef": digest("b"), "entryDigest": digest("f"), "exportRequestRef": core_request_ref, "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1", "fixedRefMustBeAbsent": True, "operationId": "policy-operation-28", "pendingTransition": None, "pointerDigest": "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), "policyCommit": commit.to_wire(), "previousEntryDigest": encoded_intent["digest"], "recordRef": record.record_digest, "schema": "trusted-policy-publish-transaction/v1", "stagedPayloadDigest": digest("e"), "state": "ACTIVATED", "targetHeadRef": "refs/heads/main"}
    unsigned = {"activateIntent": encoded_intent, "activatedEntry": embedded(activated), "activationMarker": encoded_marker, "activationTrustRootDigest": domains.canonical_digest, "brokerKeyId": "activation-fixed", "completionRecordDigest": digest("a"), "completionRecordRef": digest("b"), "fixedRef": "refs/tags/trusted-acceptance/ticket-23-policy-v1", "fixedRefValue": commit.to_wire(), "operationId": "policy-operation-28", "policyCommit": commit.to_wire(), "policyCoreExportRequestFullBytesDigest": core_request_ref, "policyCoreExportRequestRef": core_request_ref, "policyPointerBytesDigest": "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), "policyPointerName": record.handoff_id, "policyRecordFullBytesDigest": record.record_digest, "policyRecordRef": record.record_digest, "profileDigest": digest("1"), "schema": "trusted-policy-activation-proof/v1", "signatureAlgorithm": "ssh-ed25519", "signatureDomain": "ai-video-reverse-engineer/policy-activation/v1", "signingKeyPurpose": "policy-activation", "state": "ACTIVATED", "targetHeadRef": "refs/heads/main", "targetHeadValue": commit.to_wire(), "toolchainBuildRecordRef": digest("2"), "trustedGitExecutableDigest": digest("3"), "trustedGitViewDigest": digest("4")}
    signature = _test_ed25519_signature(activation_seed, b"ai-video-reverse-engineer/policy-activation/v1\0" + canonical_json_bytes(unsigned))
    proof_bytes = canonical_json_bytes({**unsigned, "brokerSignature": signature})
    proof_ref = "sha256:" + hashlib.sha256(proof_bytes).hexdigest()
    handoff = Ticket23PolicyHandoffV1("trusted-ticket23-policy-handoff/v1", record.record_digest, record.record_digest, record.handoff_id, "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), core_request_ref, core_request_ref, proof_ref, proof_ref)
    handoff_ref = "sha256:" + hashlib.sha256(handoff.canonical_bytes).hexdigest()
    request = ActivatedPolicyExportRequestV1("trusted-activated-policy-export-request/v1", record.handoff_id, handoff_ref, handoff_ref, core_request_ref, core_request_ref, proof_ref, proof_ref, (28,))
    request_ref = HandoffRegistryV1(source).publish_policy_local(record, pointer, core_request, handoff, request, proof_bytes, (b"policy payload",))
    return source, record, request_ref, transport_seed, domains


def test_run_transport_creates_distinct_sealed_ready_closures_for_two_consumers(tmp_path, monkeypatch):
    """Dropping per-consumer seal/ready bindings must make this real transport test fail."""
    from tools import trusted_handoff_orchestrator as orchestrator

    observed_public_jobs = []
    for stage_name in ("export", "upload", "install_transport"):
        original_stage = getattr(orchestrator, stage_name)

        def observe_public_job(*args, _original=original_stage, _stage_name=stage_name, **kwargs):
            observed_public_jobs.append((_stage_name, args[0]))
            assert not hasattr(args[0], "signing_key_fd")
            return _original(*args, **kwargs)

        monkeypatch.setattr(orchestrator, stage_name, observe_public_job)

    original_sign_seal = orchestrator.sign_seal
    observed_signing_fds = []

    def observe_sign_seal(*args, **kwargs):
        observed_signing_fds.append(kwargs["signing_key_fd"])
        return original_sign_seal(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "sign_seal", observe_sign_seal)

    source_root = (tmp_path / "source").resolve()
    source_root.mkdir(mode=0o700)
    source = TrustedHandoffStoreV1(source_root)
    source_document = _source()
    source_ref = source.put_blob(source_document.canonical_bytes)
    record = _record(consumer_tasks=(23, 25), bindings=(("product-source", source_ref),))
    pointer = HandoffPointerV1(
        "trusted-acceptance-handoff-pointer/v1", record.handoff_id, record.record_digest,
        record.record_digest, 22, COMMIT,
    )
    request = HandoffExportRequestV1(
        "trusted-handoff-export-request/v1", record.handoff_id, record.record_digest,
        record.record_digest, "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(),
        (23, 25), record.binding_set_digest,
    )
    producer = HandoffRegistryV1(source)
    producer.publish_local(record, pointer, (source_document.canonical_bytes,))
    request_ref = producer.publish_export_request(request)
    seed = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
    key_path = tmp_path / "transport-private-key"
    key_path.write_bytes(seed)
    key_path.chmod(0o400)
    key_fd = os.open(key_path, os.O_RDONLY)
    domains = _domains(transport=_transport_registry(_test_transport_public_key(seed)))
    try:
        results = []
        for consumer, transport_id, destination, nonce in (
            (23, "ticket-23-product-v1-c23", "executor-23", "nonce-23"),
            (25, "ticket-23-product-v1-c25", "executor-25", "nonce-25"),
        ):
            target_root = (tmp_path / f"target-{consumer}").resolve()
            stage_root = (tmp_path / f"stage-{consumer}").resolve()
            target_root.mkdir(mode=0o700)
            stage_root.mkdir(mode=0o700)
            results.append(orchestrator.run_transport(
                source_store=source,
                target_store=TrustedHandoffStoreV1(target_root),
                stage_root=stage_root,
                export_request_ref=request_ref,
                handoff_id=record.handoff_id,
                transport_id=transport_id,
                consumer_task=consumer,
                store_namespace="ai-video-reverse-engineer/ticket-23",
                source_executor_id="source-executor",
                destination_executor_id=destination,
                nonce=nonce,
                sequence=1,
                expires_at=None,
                signing_key_fd=key_fd,
                key_id="transport-fixed",
                signing_key_purpose="transport-seal",
                signature_domain="ai-video-reverse-engineer/transport-seal/v1",
                signing_domains=domains,
            ))
        ready_a = (tmp_path / "target-23" / "transports" / record.handoff_id / "ticket-23-product-v1-c23" / "ready.json").read_bytes()
        ready_b = (tmp_path / "target-25" / "transports" / record.handoff_id / "ticket-23-product-v1-c25" / "ready.json").read_bytes()
        assert ready_a != ready_b
        assert results[0].transport_id != results[1].transport_id
        assert results[0].nonce != results[1].nonce
        assert orchestrator.run_transport(
            source_store=source, target_store=TrustedHandoffStoreV1((tmp_path / "target-23").resolve()),
            stage_root=(tmp_path / "stage-23").resolve(), export_request_ref=request_ref, handoff_id=record.handoff_id,
            transport_id="ticket-23-product-v1-c23", consumer_task=23,
            store_namespace="ai-video-reverse-engineer/ticket-23", source_executor_id="source-executor",
            destination_executor_id="executor-23", nonce="nonce-23", sequence=1, expires_at=None,
            signing_key_fd=key_fd, key_id="transport-fixed", signing_key_purpose="transport-seal", signature_domain="ai-video-reverse-engineer/transport-seal/v1", signing_domains=domains,
        ).canonical_bytes == results[0].canonical_bytes
        replay_stage = (tmp_path / "stage-replay").resolve()
        replay_stage.mkdir(mode=0o700)
        with pytest.raises(ValueError):
            orchestrator.run_transport(
                source_store=source, target_store=TrustedHandoffStoreV1((tmp_path / "target-23").resolve()),
                stage_root=replay_stage, export_request_ref=request_ref, handoff_id=record.handoff_id,
                transport_id="ticket-23-product-v1-c23-replay", consumer_task=23,
                store_namespace="ai-video-reverse-engineer/ticket-23", source_executor_id="source-executor",
                destination_executor_id="executor-23", nonce="nonce-replay", sequence=1, expires_at=None,
                signing_key_fd=key_fd, key_id="transport-fixed", signing_key_purpose="transport-seal", signature_domain="ai-video-reverse-engineer/transport-seal/v1", signing_domains=domains,
            )
        assert (tmp_path / "target-23" / "transports" / record.handoff_id / "ticket-23-product-v1-c23" / "ready.json").read_bytes() == ready_a
        assert {name for name, _job in observed_public_jobs} == {"export", "upload", "install_transport"}
        assert observed_signing_fds == [key_fd] * 4
        with pytest.raises(ValueError):
            HandoffRegistryV1(TrustedHandoffStoreV1((tmp_path / "target-23").resolve())).fetch(
                TrustedHandoffStoreV1((tmp_path / "target-25").resolve()), request_ref,
                record.handoff_id, consumer_task=23, transport_id="ticket-23-product-v1-c25",
                expected_destination_executor_id="executor-23", signing_domains=domains,
            )
        cache_root = (tmp_path / "consumer-cache").resolve()
        cache_root.mkdir(mode=0o700)
        consumer = HandoffRegistryV1(TrustedHandoffStoreV1(cache_root))
        consumer.fetch(
            TrustedHandoffStoreV1((tmp_path / "target-23").resolve()), request_ref, record.handoff_id,
            consumer_task=23, transport_id="ticket-23-product-v1-c23",
            expected_destination_executor_id="executor-23", signing_domains=domains,
        )
        cached_seal = cache_root / "transports" / record.handoff_id / "ticket-23-product-v1-c23" / "seal.json"
        original_cached_seal = cached_seal.read_bytes()
        tampered_cached_seal = json.loads(original_cached_seal)
        tampered_cached_seal["nonce"] = "tampered-after-fetch"
        cached_seal.write_bytes(canonical_json_bytes(tampered_cached_seal))
        with pytest.raises(ValueError):
            consumer.import_(record.handoff_id, consumer_task=23, expected_destination_executor_id="executor-23", signing_domains=domains)
        assert not (cache_root / "imports").exists()
        cached_seal.write_bytes(original_cached_seal)
        consumer.import_(record.handoff_id, consumer_task=23, expected_destination_executor_id="executor-23", signing_domains=domains)
        with pytest.raises(ValueError, match="not finalized"):
            consumer.read_product_subject(record.handoff_id, consumer_task=23)
        consumer.finalize(
            record.handoff_id, expected_kind="product-source", expected_producer_task=22, consumer_task=23,
            expected_destination_executor_id="executor-23", signing_domains=domains,
        )
        assert consumer.read_product_subject(
            record.handoff_id, consumer_task=23, expected_destination_executor_id="executor-23", signing_domains=domains,
        ).hex == COMMIT.hex
        transport_root = tmp_path / "target-23" / "transports" / record.handoff_id / "ticket-23-product-v1-c23"
        original = {name: (transport_root / name).read_bytes() for name in ("manifest.json", "seal.json", "ready.json")}
        mutations = (
            ("ready.json", "storeNamespace", "other-namespace"),
            ("ready.json", "consumerTask", 25),
            ("ready.json", "destinationExecutorId", "executor-other"),
            ("ready.json", "sourceExecutorId", "source-other"),
            ("ready.json", "handoffId", "other-handoff"),
            ("ready.json", "transportId", "other-transport"),
            ("ready.json", "nonce", "other-nonce"),
            ("ready.json", "sequence", 2),
            ("seal.json", "signingKeyPurpose", "policy-activation"),
            ("seal.json", "signatureDomain", "ai-video-reverse-engineer/policy-activation/v1"),
            ("seal.json", "keyId", "activation-fixed"),
            ("seal.json", "nonce", "other-nonce"),
            ("seal.json", "sequence", 2),
            ("seal.json", "sourceExecutorId", "source-other"),
            ("seal.json", "recordRef", "sha256:" + "0" * 64),
            ("seal.json", "pointerBytesDigest", "sha256:" + "1" * 64),
            ("seal.json", "signature", "0" * 128),
            ("manifest.json", "storeNamespace", "other-namespace"),
            ("manifest.json", "handoffId", "other-handoff"),
            ("manifest.json", "transportId", "other-transport"),
            ("manifest.json", "sourceExecutorId", "source-other"),
            ("manifest.json", "recordRef", "sha256:" + "0" * 64),
            ("manifest.json", "recordFullBytesDigest", "sha256:" + "1" * 64),
            ("manifest.json", "pointerBytesDigest", "sha256:" + "2" * 64),
            ("manifest.json", "blobRefs", ["sha256:" + "3" * 64]),
            ("manifest.json", "expiresAt", "2020-01-01T00:00:00Z"),
        )
        for index, (name, field, value) in enumerate(mutations):
            payload = json.loads(original[name])
            payload[field] = value
            (transport_root / name).write_bytes(canonical_json_bytes(payload))
            rejected_cache = (tmp_path / f"rejected-{index}-{field}").resolve()
            rejected_cache.mkdir(mode=0o700)
            with pytest.raises(ValueError):
                HandoffRegistryV1(TrustedHandoffStoreV1(rejected_cache)).fetch(
                    TrustedHandoffStoreV1((tmp_path / "target-23").resolve()), request_ref,
                    record.handoff_id, consumer_task=23, transport_id="ticket-23-product-v1-c23",
                    expected_destination_executor_id="executor-23", signing_domains=domains,
                )
            assert list(rejected_cache.iterdir()) == []
            (transport_root / name).write_bytes(original[name])
        (transport_root / "ready.json").unlink()
        missing_ready_cache = (tmp_path / "rejected-missing-ready").resolve()
        missing_ready_cache.mkdir(mode=0o700)
        with pytest.raises(ValueError):
            HandoffRegistryV1(TrustedHandoffStoreV1(missing_ready_cache)).fetch(
                TrustedHandoffStoreV1((tmp_path / "target-23").resolve()), request_ref,
                record.handoff_id, consumer_task=23, transport_id="ticket-23-product-v1-c23",
                expected_destination_executor_id="executor-23", signing_domains=domains,
            )
        assert list(missing_ready_cache.iterdir()) == []
        (transport_root / "ready.json").write_bytes(original["ready.json"])
        (transport_root / "seal.json").unlink()
        missing_seal_cache = (tmp_path / "rejected-missing-seal").resolve()
        missing_seal_cache.mkdir(mode=0o700)
        with pytest.raises(ValueError):
            HandoffRegistryV1(TrustedHandoffStoreV1(missing_seal_cache)).fetch(
                TrustedHandoffStoreV1((tmp_path / "target-23").resolve()), request_ref,
                record.handoff_id, consumer_task=23, transport_id="ticket-23-product-v1-c23",
                expected_destination_executor_id="executor-23", signing_domains=domains,
            )
        assert list(missing_seal_cache.iterdir()) == []
    finally:
        os.close(key_fd)


def test_transport_cli_exposes_only_fd_keyed_public_stages():
    """Giving a public transport stage the seal FD must fail this command-boundary contract."""
    cli = Path(__file__).parent / "tools" / "trusted_handoff_orchestrator.py"
    result = subprocess.run([sys.executable, str(cli), "--help"], env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)}, capture_output=True, check=False)

    assert result.returncode == 0
    for command in (b"verify-signing-domains", b"verify-export-request", b"export", b"upload", b"sign-seal", b"install-transport", b"run-transport"):
        assert command in result.stdout
    assert b"signing-key-path" not in result.stdout
    assert b"signing-key-value" not in result.stdout
    for command in ("export", "upload", "sign-seal", "install-transport", "run-transport"):
        command_help = subprocess.run([sys.executable, str(cli), command, "--help"], env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)}, capture_output=True, check=False)
        assert command_help.returncode == 0
        assert (b"--signing-key-fd" in command_help.stdout) is (command in {"sign-seal", "run-transport"})


def test_transport_runner_requires_an_explicit_signing_identity():
    """Adding a purpose/domain fallback would let callers silently change the signing contract."""
    from tools.trusted_handoff_orchestrator import run_policy_transport, run_transport

    for runner in (run_transport, run_policy_transport):
        signature = inspect.signature(runner)
        assert signature.parameters["signing_key_purpose"].default is inspect.Parameter.empty
        assert signature.parameters["signature_domain"].default is inspect.Parameter.empty


def test_policy_record_pointer_uses_general_git_oid_and_full_bytes_digest():
    """Replacing the policy's SHA-256 commit with a product SHA-1-only type must fail."""
    from trusted_evidence.handoffs import PolicyHandoffPointerV1, PolicyHandoffRecordV1
    from trusted_evidence.e2_policy import GitObjectIdV1

    commit = GitObjectIdV1("sha256", "c" * 64)
    record = PolicyHandoffRecordV1(
        "trusted-acceptance-handoff/v1", "ticket-23-policy-v1", "policy", 27, commit,
        (28,), (("policy-payload", "sha256:" + "d" * 64),),
    )
    pointer = PolicyHandoffPointerV1(
        "trusted-acceptance-handoff-pointer/v1", record.handoff_id, record.record_digest,
        record.record_digest, 27, commit,
    )

    assert pointer.record_ref == "sha256:" + hashlib.sha256(record.canonical_bytes).hexdigest()
    with pytest.raises(ValueError):
        PolicyHandoffRecordV1(
            "trusted-acceptance-handoff/v1", "ticket-23-policy-v1", "policy", 27,
            ProductGitObjectIdV1("sha1", "e" * 40), (28,), (("policy-payload", "sha256:" + "d" * 64),),
        )


def test_policy_transport_requires_a_complete_activated_closure_before_ready(tmp_path):
    """Removing any durable policy proof binding must prevent a ready policy transport."""
    from tools.trusted_handoff_orchestrator import run_policy_transport

    source_root = (tmp_path / "policy-source").resolve()
    target_root = (tmp_path / "policy-target").resolve()
    stage_root = (tmp_path / "policy-stage").resolve()
    for root in (source_root, target_root, stage_root):
        root.mkdir(mode=0o700)
    source = TrustedHandoffStoreV1(source_root)
    commit = GitObjectIdV1("sha256", "c" * 64)
    payload_ref = source.put_blob(b"policy payload")
    record = PolicyHandoffRecordV1(
        "trusted-acceptance-handoff/v1", "ticket-23-policy-v1", "policy", 27,
        commit, (28,), (("policy-payload", payload_ref),),
    )
    pointer = PolicyHandoffPointerV1(
        "trusted-acceptance-handoff-pointer/v1", record.handoff_id, record.record_digest,
        record.record_digest, 27, commit,
    )

    # The runner must refuse this real store before it can publish a ready
    # marker: an activated policy request without a durable proof is not a
    # transportable policy closure.
    source.put_blob(record.canonical_bytes)
    source.publish_pointer(record.handoff_id, pointer.canonical_bytes)
    with pytest.raises(ValueError, match="policy"):
        run_policy_transport(
            source_store=source,
            target_store=TrustedHandoffStoreV1(target_root),
            stage_root=stage_root,
            export_request_ref=record.record_digest,
            handoff_id=record.handoff_id,
            transport_id="ticket-23-policy-v1-c28",
            consumer_task=28,
            store_namespace="ai-video-reverse-engineer/ticket-23",
            source_executor_id="policy-source",
            destination_executor_id="executor-28",
            nonce="policy-nonce-28",
            sequence=1,
            expires_at=None,
            signing_key_fd=-1,
            key_id="transport-fixed",
            signing_key_purpose="transport-seal",
            signature_domain="ai-video-reverse-engineer/transport-seal/v1",
            signing_domains=_domains(),
        )
    assert not (target_root / "transports" / record.handoff_id).exists()


def test_policy_transport_runs_the_product_stages_then_finalizes_and_reads(tmp_path):
    """Bypassing the shared seal/ready stages or proof verification breaks this policy handoff."""
    from tools.trusted_handoff_orchestrator import run_policy_transport

    source, record, request_ref, transport_seed, domains = _policy_transport_fixture(tmp_path)
    target_root = (tmp_path / "policy-target").resolve(); target_root.mkdir(mode=0o700)
    stage_root = (tmp_path / "policy-stage").resolve(); stage_root.mkdir(mode=0o700)
    key_path = tmp_path / "policy-transport-key"; key_path.write_bytes(transport_seed); key_path.chmod(0o400)
    key_fd = os.open(key_path, os.O_RDONLY)
    try:
        ready = run_policy_transport(
            source_store=source, target_store=TrustedHandoffStoreV1(target_root), stage_root=stage_root,
            export_request_ref=request_ref, handoff_id=record.handoff_id,
            transport_id="ticket-23-policy-v1-c28", consumer_task=28,
            store_namespace="ai-video-reverse-engineer/ticket-23", source_executor_id="policy-source",
            destination_executor_id="executor-28", nonce="policy-nonce-28", sequence=1,
            expires_at=None, signing_key_fd=key_fd, key_id="transport-fixed", signing_key_purpose="transport-seal", signature_domain="ai-video-reverse-engineer/transport-seal/v1", signing_domains=domains,
        )
    finally:
        os.close(key_fd)
    assert ready.policy_activation_proof_ref is not None
    assert (target_root / "transports" / record.handoff_id / "ticket-23-policy-v1-c28" / "ready.json").is_file()
    cache_root = (tmp_path / "policy-cache").resolve(); cache_root.mkdir(mode=0o700)
    consumer = HandoffRegistryV1(TrustedHandoffStoreV1(cache_root))
    consumer.fetch(TrustedHandoffStoreV1(target_root), request_ref, record.handoff_id, consumer_task=28, transport_id="ticket-23-policy-v1-c28", expected_destination_executor_id="executor-28", signing_domains=domains)
    consumer.import_(record.handoff_id, consumer_task=28, expected_destination_executor_id="executor-28", signing_domains=domains)
    consumer.finalize(record.handoff_id, expected_kind="policy", expected_producer_task=27, consumer_task=28, expected_destination_executor_id="executor-28", signing_domains=domains)
    assert consumer.read(record.handoff_id, consumer_task=28, field="producerCommit.hex", expected_destination_executor_id="executor-28", signing_domains=domains) == record.producer_commit.hex
    wrong_cache = (tmp_path / "wrong-policy-cache").resolve(); wrong_cache.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        HandoffRegistryV1(TrustedHandoffStoreV1(wrong_cache)).fetch(TrustedHandoffStoreV1(target_root), request_ref, record.handoff_id, consumer_task=28, transport_id="ticket-23-policy-v1-c28", expected_destination_executor_id="executor-other", signing_domains=domains)
    changed_stage = (tmp_path / "changed-policy-stage").resolve(); changed_stage.mkdir(mode=0o700)
    key_fd = os.open(key_path, os.O_RDONLY)
    try:
        with pytest.raises(ValueError):
            run_policy_transport(
                source_store=source, target_store=TrustedHandoffStoreV1(target_root), stage_root=changed_stage,
                export_request_ref=request_ref, handoff_id=record.handoff_id,
                transport_id="ticket-23-policy-v1-c28", consumer_task=28,
                store_namespace="ai-video-reverse-engineer/ticket-23", source_executor_id="policy-source",
                destination_executor_id="executor-28", nonce="changed-policy-nonce", sequence=1,
                expires_at=None, signing_key_fd=key_fd, key_id="transport-fixed", signing_key_purpose="transport-seal", signature_domain="ai-video-reverse-engineer/transport-seal/v1", signing_domains=domains,
            )
    finally:
        os.close(key_fd)


def test_policy_activation_cli_verifies_the_cached_proof_and_source_closure(tmp_path):
    """The policy CLI must report production-verifier output, never a fixed stub."""
    from tools.trusted_handoff_orchestrator import run_policy_transport

    source, record, request_ref, transport_seed, domains = _policy_transport_fixture(tmp_path)
    target_root = (tmp_path / "policy-target").resolve(); target_root.mkdir(mode=0o700)
    stage_root = (tmp_path / "policy-stage").resolve(); stage_root.mkdir(mode=0o700)
    key_path = tmp_path / "policy-transport-key"; key_path.write_bytes(transport_seed); key_path.chmod(0o400)
    key_fd = os.open(key_path, os.O_RDONLY)
    try:
        run_policy_transport(
            source_store=source, target_store=TrustedHandoffStoreV1(target_root), stage_root=stage_root,
            export_request_ref=request_ref, handoff_id=record.handoff_id,
            transport_id="ticket-23-policy-v1-c28", consumer_task=28,
            store_namespace="ai-video-reverse-engineer/ticket-23", source_executor_id="policy-source",
            destination_executor_id="executor-28", nonce="policy-nonce-28", sequence=1,
            expires_at=None, signing_key_fd=key_fd, key_id="transport-fixed", signing_key_purpose="transport-seal", signature_domain="ai-video-reverse-engineer/transport-seal/v1", signing_domains=domains,
        )
    finally:
        os.close(key_fd)
    cache_root = (tmp_path / "policy-cache").resolve(); cache_root.mkdir(mode=0o700)
    consumer = HandoffRegistryV1(TrustedHandoffStoreV1(cache_root))
    consumer.fetch(TrustedHandoffStoreV1(target_root), request_ref, record.handoff_id, consumer_task=28, transport_id="ticket-23-policy-v1-c28", expected_destination_executor_id="executor-28", signing_domains=domains)
    consumer.import_(record.handoff_id, consumer_task=28, expected_destination_executor_id="executor-28", signing_domains=domains)

    transport = _transport_registry(_test_transport_public_key(transport_seed))
    activation = _activation_registry(_test_transport_public_key(bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")))
    broker_registry, broker_revocations, broker_allowed = _strict_broker_inputs()
    files = {
        "transport": transport,
        "activation": activation,
        "broker": broker_registry,
        "revocations": broker_revocations,
        "allowed": broker_allowed,
    }
    paths = {}
    for name, payload in files.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(payload if isinstance(payload, bytes) else canonical_json_bytes(payload))
        paths[name] = path
    cli = Path(__file__).parent / "tools" / "trusted_handoff_store.py"
    command = [
        sys.executable, str(cli), "verify-policy-activation", "--cache-root", str(cache_root),
        "--handoff-id", record.handoff_id, "--consumer-task", "28",
        "--expected-destination-executor-id", "executor-28",
        "--transport-registry", str(paths["transport"]), "--activation-registry", str(paths["activation"]),
        "--broker-auth-registry", str(paths["broker"]), "--broker-auth-revocations", str(paths["revocations"]),
        "--broker-auth-allowed-signers", str(paths["allowed"]),
    ]
    result = subprocess.run(command, env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)}, capture_output=True)
    wrong_command = command.copy()
    wrong_command[wrong_command.index("--expected-destination-executor-id") + 1] = "executor-other"
    wrong_destination = subprocess.run(wrong_command, env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)}, capture_output=True)

    assert result.returncode == 0
    assert json.loads(result.stdout)["operationId"] == "policy-operation-28"
    assert wrong_destination.returncode != 0


@pytest.mark.parametrize("location", ("activated-request", "policy-handoff", "activation-proof", "manifest", "seal", "ready"))
def test_policy_transport_rejects_proof_binding_tamper_at_every_durable_location(tmp_path, location):
    """Changing a proof ref/digest carrier must fail before policy use at every closure location."""
    from tools.trusted_handoff_orchestrator import run_policy_transport

    source, record, request_ref, transport_seed, domains = _policy_transport_fixture(tmp_path)
    target_root = (tmp_path / "target").resolve(); target_root.mkdir(mode=0o700)
    stage_root = (tmp_path / "stage").resolve(); stage_root.mkdir(mode=0o700)
    key_path = tmp_path / "key"; key_path.write_bytes(transport_seed); key_path.chmod(0o400)
    key_fd = os.open(key_path, os.O_RDONLY)
    try:
        run_policy_transport(source_store=source, target_store=TrustedHandoffStoreV1(target_root), stage_root=stage_root, export_request_ref=request_ref, handoff_id=record.handoff_id, transport_id="ticket-23-policy-v1-c28", consumer_task=28, store_namespace="ai-video-reverse-engineer/ticket-23", source_executor_id="policy-source", destination_executor_id="executor-28", nonce="policy-nonce-28", sequence=1, expires_at=None, signing_key_fd=key_fd, key_id="transport-fixed", signing_key_purpose="transport-seal", signature_domain="ai-video-reverse-engineer/transport-seal/v1", signing_domains=domains)
    finally:
        os.close(key_fd)
    target = TrustedHandoffStoreV1(target_root)
    if location in {"activated-request", "policy-handoff", "activation-proof"}:
        activated = ActivatedPolicyExportRequestV1.from_canonical_bytes(target.read_blob(request_ref))
        ref = {"activated-request": request_ref, "policy-handoff": activated.policy_handoff_ref, "activation-proof": activated.policy_activation_proof_ref}[location]
        (target.root / "sha256" / ref[7:9] / ref[9:]).write_bytes(b"tampered policy closure")
    else:
        path = target_root / "transports" / record.handoff_id / "ticket-23-policy-v1-c28" / f"{location}.json"
        if location == "manifest":
            path = path.with_name("manifest.json")
        path.write_bytes(b"tampered policy closure")
    with pytest.raises(ValueError):
        verify_transport_closure(target, record.handoff_id, "ticket-23-policy-v1-c28", 28, "executor-28", domains)


def test_transport_and_policy_wires_reject_oversize_canonical_bytes_before_use():
    """An oversized canonical manifest or activation marker must not reach closure processing."""
    digest = "sha256:" + "a" * 64
    blob_refs = tuple(sorted(f"sha256:{index:064x}" for index in range(15_000)))
    manifest = HandoffTransferManifestV1(
        "trusted-handoff-transfer/v1", digest, digest, "ticket-23", "handoff", "transport",
        23, digest, digest, "handoff", digest, "source", "destination", "nonce", 0,
        None, None, None, blob_refs,
    )
    marker = PolicyActivationMarkerV1(
        "trusted-policy-activation/v1", "operation-" + "x" * (1024 * 1024), digest,
        GitObjectIdV1("sha256", "b" * 64), "refs/tags/trusted-acceptance/ticket-23-policy-v1",
        digest, digest, digest,
    )

    assert len(manifest.canonical_bytes) > 1024 * 1024
    assert len(marker.canonical_bytes) > 1024 * 1024
    with pytest.raises(ValueError, match="too large"):
        HandoffTransferManifestV1.from_canonical_bytes(manifest.canonical_bytes)
    with pytest.raises(ValueError, match="too large"):
        PolicyActivationMarkerV1.from_canonical_bytes(marker.canonical_bytes)


def test_transport_manifest_rejects_an_oversize_handoff_identity():
    """A transport ID binding longer than the wire identity budget must fail before sealing."""
    digest = "sha256:" + "a" * 64
    with pytest.raises(ValueError, match="invalid"):
        HandoffTransferManifestV1(
            "trusted-handoff-transfer/v1", digest, digest, "ticket-23", "h" * 257, "transport",
            23, digest, digest, "h" * 257, digest, "source", "destination", "nonce", 0,
            None, None, None, (digest,),
        )
    with pytest.raises(ValueError, match="invalid"):
        HandoffTransferManifestV1(
            "trusted-handoff-transfer/v1", digest, digest, "ticket-23", "你" * 86, "transport",
            23, digest, digest, "你" * 86, digest, "source", "destination", "nonce", 0,
            None, None, None, (digest,),
        )


@pytest.mark.parametrize(
    ("decoder", "payload"),
    (
        (HandoffTransferManifestV1.from_canonical_bytes, None),
        (PolicyActivationMarkerV1.from_canonical_bytes, "not-bytes"),
    ),
)
def test_handoff_and_policy_decoders_reject_nonbytes_inputs(decoder, payload):
    """Untrusted non-byte decoder inputs must fail through the stable ValueError boundary."""
    with pytest.raises(ValueError):
        decoder(payload)


def test_handoff_transport_and_policy_decoders_reject_closed_wire_mutations(tmp_path):
    """Record, transport, and activation decoders reject unknown, missing, duplicate, and noncanonical JSON."""
    from trusted_evidence.handoffs import Ticket23PolicyHandoffV1

    digest = "sha256:" + "a" * 64
    record = TrustedHandoffRecordV1(
        "trusted-acceptance-handoff/v1", "handoff", "product-source", 22,
        ProductGitObjectIdV1("sha1", "b" * 40), (23,), (("source", digest),),
    )
    pointer = HandoffPointerV1(
        "trusted-acceptance-handoff-pointer/v1", record.handoff_id, record.record_digest,
        record.record_digest, 22, record.producer_commit,
    )
    request = HandoffExportRequestV1(
        "trusted-handoff-export-request/v1", record.handoff_id, record.record_digest,
        record.record_digest, "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(),
        (23,), record.binding_set_digest,
    )
    manifest = HandoffTransferManifestV1(
        "trusted-handoff-transfer/v1", digest, digest, "ticket-23", "handoff", "transport",
        23, record.record_digest, record.record_digest, "handoff",
        "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), "source", "destination",
        "nonce", 0, None, None, None, (digest,),
    )
    seal = TransportSealV1(
        "trusted-handoff-transport-seal/v1", "ticket-23", "handoff", "transport", 23,
        record.record_digest, record.record_digest, "handoff",
        "sha256:" + hashlib.sha256(pointer.canonical_bytes).hexdigest(), manifest.digest,
        "source", "destination", "nonce", 0, None, None, None, "transport-seal",
        "ai-video-reverse-engineer/transport-seal/v1", "ssh-ed25519", "transport-key", "c" * 128,
    )
    ready = TransportReadyMarkerV1(
        "trusted-handoff-transport-ready/v1", "ticket-23", "handoff", "transport", 23,
        "source", "destination", "nonce", 0, manifest.digest,
        "sha256:" + hashlib.sha256(seal.canonical_bytes).hexdigest(), None, None,
    )
    marker = PolicyActivationMarkerV1(
        "trusted-policy-activation/v1", "operation", digest,
        GitObjectIdV1("sha256", "d" * 64), "refs/tags/trusted-acceptance/ticket-23-policy-v1",
        digest, digest, digest,
    )
    source, _policy_record, request_ref, _seed, _domains_value = _policy_transport_fixture(tmp_path)
    activated = ActivatedPolicyExportRequestV1.from_canonical_bytes(source.read_blob(request_ref))
    policy_handoff = Ticket23PolicyHandoffV1.from_canonical_bytes(source.read_blob(activated.policy_handoff_ref))
    proof = PolicyActivationProofV1.from_canonical_bytes(source.read_blob(activated.policy_activation_proof_ref))

    for decoder, payload in (
        (TrustedHandoffRecordV1.from_canonical_bytes, record.canonical_bytes),
        (HandoffPointerV1.from_canonical_bytes, pointer.canonical_bytes),
        (HandoffExportRequestV1.from_canonical_bytes, request.canonical_bytes),
        (HandoffTransferManifestV1.from_canonical_bytes, manifest.canonical_bytes),
        (TransportSealV1.from_canonical_bytes, seal.canonical_bytes),
        (TransportReadyMarkerV1.from_canonical_bytes, ready.canonical_bytes),
        (PolicyActivationMarkerV1.from_canonical_bytes, marker.canonical_bytes),
        (Ticket23PolicyHandoffV1.from_canonical_bytes, policy_handoff.canonical_bytes),
        (ActivatedPolicyExportRequestV1.from_canonical_bytes, activated.canonical_bytes),
        (PolicyActivationProofV1.from_canonical_bytes, proof.canonical_bytes),
    ):
        decoded = json.loads(payload)
        unknown = {**decoded, "unexpected": True}
        missing = dict(decoded)
        missing.pop(next(iter(missing)))
        duplicate = payload[:-1] + b',"schema":"duplicate"}'
        for mutated in (canonical_json_bytes(unknown), canonical_json_bytes(missing), duplicate, b" " + payload):
            with pytest.raises(ValueError):
                decoder(mutated)


def _resigned_policy_proof_with_embedded_field(
    proof_bytes: bytes, embedded_name: str, field: str, value: object,
) -> bytes:
    """Return a correctly signed proof whose embedded chain has one semantic mutation."""
    proof = json.loads(proof_bytes)

    def mutate_embedded(name: str, nested_field: str, nested_value: object) -> dict[str, object]:
        embedded = dict(proof[name])
        nested = json.loads(base64.b64decode(embedded["canonicalBytesB64"]))
        nested[nested_field] = nested_value
        encoded = canonical_json_bytes(nested)
        embedded["canonicalBytesB64"] = base64.b64encode(encoded).decode("ascii")
        embedded["digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
        proof[name] = embedded
        return embedded

    changed = mutate_embedded(embedded_name, field, value)
    if embedded_name == "activateIntent":
        marker = mutate_embedded("activationMarker", "activationIntentDigest", changed["digest"])
        mutate_embedded("activatedEntry", "previousEntryDigest", changed["digest"])
        mutate_embedded("activatedEntry", "activationMarkerDigest", marker["digest"])
    unsigned = dict(proof)
    del unsigned["brokerSignature"]
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    proof["brokerSignature"] = _test_ed25519_signature(
        seed,
        b"ai-video-reverse-engineer/policy-activation/v1\0" + canonical_json_bytes(unsigned),
    )
    return canonical_json_bytes(proof)


@pytest.mark.parametrize(
    ("embedded_name", "field", "value"),
    (
        ("activateIntent", "state", "PREPARED"),
        ("activateIntent", "fixedRefMustBeAbsent", False),
        ("activateIntent", "completionRecordDigest", "sha256:" + "f" * 64),
        ("activatedEntry", "pendingTransition", "ACTIVATE"),
        ("activatedEntry", "fixedRefMustBeAbsent", False),
    ),
)
def test_policy_activation_rejects_embedded_chain_state_and_authorization_mutations(
    tmp_path, embedded_name, field, value,
):
    """A valid activation signature cannot authorize an invalid intent/marker/entry chain."""
    source, _record, request_ref, _transport_seed, domains = _policy_transport_fixture(tmp_path)
    activated = ActivatedPolicyExportRequestV1.from_canonical_bytes(source.read_blob(request_ref))
    mutated = _resigned_policy_proof_with_embedded_field(
        source.read_blob(activated.policy_activation_proof_ref), embedded_name, field, value,
    )

    with pytest.raises(ValueError):
        verify_policy_activation_proof(
            mutated, domains,
            expected_transport={"consumerTask": 28, "destinationExecutorId": "executor-28"},
        )

    from tools.trusted_handoff_orchestrator import run_policy_transport

    transport_root = (tmp_path / f"transport-{embedded_name}-{field}").resolve()
    stage_root = (tmp_path / f"stage-{embedded_name}-{field}").resolve()
    transport_root.mkdir(mode=0o700)
    stage_root.mkdir(mode=0o700)
    key_path = tmp_path / f"transport-key-{embedded_name}-{field}"
    key_path.write_bytes(_transport_seed)
    key_path.chmod(0o400)
    key_fd = os.open(key_path, os.O_RDONLY)
    try:
        run_policy_transport(
            source_store=source, target_store=TrustedHandoffStoreV1(transport_root), stage_root=stage_root,
            export_request_ref=request_ref, handoff_id="ticket-23-policy-v1",
            transport_id="ticket-23-policy-v1-c28", consumer_task=28,
            store_namespace="ai-video-reverse-engineer/ticket-23", source_executor_id="policy-source",
            destination_executor_id="executor-28", nonce="policy-nonce-28", sequence=1,
            expires_at=None, signing_key_fd=key_fd, key_id="transport-fixed",
            signing_key_purpose="transport-seal",
            signature_domain="ai-video-reverse-engineer/transport-seal/v1", signing_domains=domains,
        )
    finally:
        os.close(key_fd)
    proof_path = transport_root / "sha256" / activated.policy_activation_proof_ref[7:9] / activated.policy_activation_proof_ref[9:]
    proof_path.write_bytes(mutated)
    cache_root = (tmp_path / f"cache-{embedded_name}-{field}").resolve()
    cache_root.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        HandoffRegistryV1(TrustedHandoffStoreV1(cache_root)).fetch(
            TrustedHandoffStoreV1(transport_root), request_ref, "ticket-23-policy-v1", consumer_task=28,
            transport_id="ticket-23-policy-v1-c28",
            expected_destination_executor_id="executor-28", signing_domains=domains,
        )
    assert not any(cache_root.iterdir())
