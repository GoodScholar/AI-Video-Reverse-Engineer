import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trusted_evidence.e2_policy import ProductGitObjectIdV1
from trusted_evidence.handoffs import (
    HandoffRegistryV1,
    HandoffPointerV1,
    HandoffExportRequestV1,
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
    environment = {**os.environ, "TRUSTED_HANDOFF_STORE_ROOT": str(root), "PYTHONPATH": str(Path(__file__).parent)}
    cli = Path(__file__).parent / "tools" / "trusted_handoff_store.py"
    read = subprocess.run([sys.executable, str(cli), "read", "ticket-23-product-v1"], env=environment, capture_output=True)
    publish = subprocess.run([sys.executable, str(cli), "publish-local", "--producer-task", "22"], env=environment, capture_output=True)
    assert read.returncode != 0
    assert publish.returncode != 0
    assert b"not finalized" in read.stderr
    assert b"reserved producer" in publish.stderr


def test_registry_fetches_only_verified_canonical_request_closure_and_import_stays_unfinalized(tmp_path):
    """A consumer accepts source CAS bytes only after the request closes every digest."""
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
    consumer.fetch(source, request_ref, record.handoff_id, consumer_task=23)
    consumer.import_(record.handoff_id, consumer_task=23)

    with pytest.raises(ValueError, match="not finalized"):
        consumer.finalize(record.handoff_id, expected_kind="product-source", expected_producer_task=22, consumer_task=23)
    with pytest.raises(ValueError, match="not finalized"):
        consumer.read_product_subject(record.handoff_id, consumer_task=23)


def test_registry_rejects_tampered_or_noncanonical_request_bytes_before_cache_write(tmp_path):
    """Replacing a request's canonical bytes must fail before it is copied to a consumer cache."""
    source_root = (tmp_path / "source").resolve()
    cache_root = (tmp_path / "cache").resolve()
    source_root.mkdir(mode=0o700)
    cache_root.mkdir(mode=0o700)
    source = TrustedHandoffStoreV1(source_root)
    raw = b'{"bindingSetDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "consumerTasks":[23],"handoffId":"x","pointerBytesDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","recordFullBytesDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","recordRef":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","schema":"trusted-handoff-export-request/v1"}'
    ref = source.put_blob(raw)

    with pytest.raises(ValueError, match="canonical"):
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
