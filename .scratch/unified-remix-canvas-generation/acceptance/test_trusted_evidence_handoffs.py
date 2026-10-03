import hashlib
import os

import pytest

from trusted_evidence.e2_policy import ProductGitObjectIdV1
from trusted_evidence.handoffs import (
    HandoffPointerV1,
    ProductRepositorySourceV1,
    Ticket23ProductSourceHandoffV1,
    TrustedHandoffRecordV1,
    TrustedHandoffStoreV1,
)


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
        budget_digest=DIGEST,
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
            budget_digest=DIGEST,
            boundary_digest=DIGEST,
            tracked_checkout_digest=DIGEST,
            control_metadata_digest=DIGEST,
            raw_inspection_proof_ref=DIGEST,
        )


def test_handoff_store_publishes_content_addressed_blob_and_no_replace_pointer(tmp_path, monkeypatch):
    """A record/pointer collision must never replace bytes selected by another producer."""
    root = (tmp_path / "external-store").resolve()
    root.mkdir(mode=0o700)
    monkeypatch.setenv("TRUSTED_HANDOFF_STORE_ROOT", str(root))
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
