"""Portable codecs for authenticated cross-executor handoff records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import json
from collections.abc import Mapping

from .e2_policy import ProductGitObjectIdV1, canonical_json_bytes


_DIGEST_PREFIX = "sha256:"


def _digest(payload: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith(_DIGEST_PREFIX) or any(character not in "0123456789abcdef" for character in value[7:]):
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")
    return value


def _strict_object(payload: bytes, fields: set[str]) -> dict[str, object]:
    """Decode one exact canonical JSON object with a closed field set."""
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("handoff JSON is invalid") from error
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("handoff JSON fields are invalid")
    if canonical_json_bytes(value) != payload:
        raise ValueError("handoff JSON is not canonical")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _product_oid(value: object) -> ProductGitObjectIdV1:
    if not isinstance(value, dict) or set(value) != {"algorithm", "hex"}:
        raise ValueError("product commit is invalid")
    return ProductGitObjectIdV1(value["algorithm"], value["hex"])


@dataclass(frozen=True, slots=True)
class TrustedHandoffRecordV1:
    schema: str
    handoff_id: str
    kind: str
    producer_task: int
    producer_commit: ProductGitObjectIdV1
    consumer_tasks: tuple[int, ...]
    bindings: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-acceptance-handoff/v1" or self.kind != "product-source" or self.producer_task != 22:
            raise ValueError("invalid product-source handoff record")
        if not self.handoff_id or not isinstance(self.producer_commit, ProductGitObjectIdV1):
            raise ValueError("invalid handoff identity")
        if not self.consumer_tasks or tuple(sorted(self.consumer_tasks)) != self.consumer_tasks or len(set(self.consumer_tasks)) != len(self.consumer_tasks) or any(not isinstance(task, int) or task <= 0 for task in self.consumer_tasks):
            raise ValueError("consumerTasks must be sorted unique positive integers")
        bindings = tuple(sorted(self.bindings, key=lambda item: item[0].encode("utf-8")))
        if not bindings or len({name for name, _ in bindings}) != len(bindings):
            raise ValueError("bindings must be non-empty and name-unique")
        for name, digest in bindings:
            if not isinstance(name, str) or not name:
                raise ValueError("binding name must be non-empty")
            _require_digest(digest, "binding digest")
        object.__setattr__(self, "bindings", bindings)

    def to_wire(self) -> dict[str, object]:
        return {
            "bindings": [{"digest": digest, "name": name} for name, digest in self.bindings],
            "consumerTasks": list(self.consumer_tasks),
            "handoffId": self.handoff_id,
            "kind": self.kind,
            "producerCommit": self.producer_commit.to_wire(),
            "producerTask": self.producer_task,
            "schema": self.schema,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def record_digest(self) -> str:
        return _digest(self.canonical_bytes)

    @property
    def binding_set_digest(self) -> str:
        return _digest(canonical_json_bytes(self.to_wire()["bindings"]))

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TrustedHandoffRecordV1":
        raw = _strict_object(payload, {"bindings", "consumerTasks", "handoffId", "kind", "producerCommit", "producerTask", "schema"})
        bindings = raw["bindings"]
        if not isinstance(bindings, list) or not isinstance(raw["consumerTasks"], list):
            raise ValueError("handoff record collections are invalid")
        parsed_bindings: list[tuple[str, str]] = []
        for item in bindings:
            if not isinstance(item, dict) or set(item) != {"digest", "name"}:
                raise ValueError("handoff binding is invalid")
            parsed_bindings.append((item["name"], item["digest"]))
        return cls(raw["schema"], raw["handoffId"], raw["kind"], raw["producerTask"], _product_oid(raw["producerCommit"]), tuple(raw["consumerTasks"]), tuple(parsed_bindings))


@dataclass(frozen=True, slots=True)
class HandoffPointerV1:
    schema: str
    handoff_id: str
    record_ref: str
    record_digest: str
    producer_task: int
    producer_commit: ProductGitObjectIdV1

    def __post_init__(self) -> None:
        if self.schema != "trusted-acceptance-handoff-pointer/v1" or not self.handoff_id:
            raise ValueError("invalid handoff pointer")
        _require_digest(self.record_ref, "record_ref")
        _require_digest(self.record_digest, "record_digest")
        if self.record_ref != self.record_digest or self.producer_task != 22 or not isinstance(self.producer_commit, ProductGitObjectIdV1):
            raise ValueError("handoff pointer does not bind the product record")

    def to_wire(self) -> dict[str, object]:
        return {"handoffId": self.handoff_id, "producerCommit": self.producer_commit.to_wire(), "producerTask": self.producer_task, "recordDigest": self.record_digest, "recordRef": self.record_ref, "schema": self.schema}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "HandoffPointerV1":
        raw = _strict_object(payload, {"handoffId", "producerCommit", "producerTask", "recordDigest", "recordRef", "schema"})
        return cls(raw["schema"], raw["handoffId"], raw["recordRef"], raw["recordDigest"], raw["producerTask"], _product_oid(raw["producerCommit"]))


@dataclass(frozen=True, slots=True)
class HandoffExportRequestV1:
    schema: str
    handoff_id: str
    record_ref: str
    record_full_bytes_digest: str
    pointer_bytes_digest: str
    consumer_tasks: tuple[int, ...]
    binding_set_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-handoff-export-request/v1" or not self.handoff_id:
            raise ValueError("invalid handoff export request")
        for field in ("record_ref", "record_full_bytes_digest", "pointer_bytes_digest", "binding_set_digest"):
            _require_digest(getattr(self, field), field)
        if not self.consumer_tasks or tuple(sorted(self.consumer_tasks)) != self.consumer_tasks or len(set(self.consumer_tasks)) != len(self.consumer_tasks) or any(not isinstance(task, int) or task <= 0 for task in self.consumer_tasks):
            raise ValueError("consumerTasks must be sorted unique positive integers")

    def to_wire(self) -> dict[str, object]:
        return {"bindingSetDigest": self.binding_set_digest, "consumerTasks": list(self.consumer_tasks), "handoffId": self.handoff_id, "pointerBytesDigest": self.pointer_bytes_digest, "recordFullBytesDigest": self.record_full_bytes_digest, "recordRef": self.record_ref, "schema": self.schema}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "HandoffExportRequestV1":
        raw = _strict_object(payload, {"bindingSetDigest", "consumerTasks", "handoffId", "pointerBytesDigest", "recordFullBytesDigest", "recordRef", "schema"})
        if not isinstance(raw["consumerTasks"], list):
            raise ValueError("export request consumers are invalid")
        return cls(raw["schema"], raw["handoffId"], raw["recordRef"], raw["recordFullBytesDigest"], raw["pointerBytesDigest"], tuple(raw["consumerTasks"]), raw["bindingSetDigest"])


@dataclass(frozen=True, slots=True)
class ProductRepositorySourceV1:
    subject_repository: str
    subject_commit: ProductGitObjectIdV1
    source_ref: str
    source_digest: str
    container_format: str
    object_format: str
    budget_digest: str
    boundary_digest: str
    tracked_checkout_digest: str
    control_metadata_digest: str
    raw_inspection_proof_ref: str

    def __post_init__(self) -> None:
        if self.subject_repository != "product" or self.container_format != "trusted-product-raw-git-tar-zstd/v1" or self.object_format != "sha1":
            raise ValueError("product source must be path-free SHA-1 source data")
        if not isinstance(self.subject_commit, ProductGitObjectIdV1):
            raise ValueError("product source subject must be a ProductGitObjectIdV1")
        for field in ("source_ref", "source_digest", "budget_digest", "boundary_digest", "tracked_checkout_digest", "control_metadata_digest", "raw_inspection_proof_ref"):
            _require_digest(getattr(self, field), field)

    def to_wire(self) -> dict[str, object]:
        return {"boundaryDigest": self.boundary_digest, "budgetDigest": self.budget_digest, "containerFormat": self.container_format, "controlMetadataDigest": self.control_metadata_digest, "objectFormat": self.object_format, "rawInspectionProofRef": self.raw_inspection_proof_ref, "sourceDigest": self.source_digest, "sourceRef": self.source_ref, "subjectCommit": self.subject_commit.to_wire(), "subjectRepository": self.subject_repository, "trackedCheckoutDigest": self.tracked_checkout_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "ProductRepositorySourceV1":
        raw = _strict_object(payload, {"boundaryDigest", "budgetDigest", "containerFormat", "controlMetadataDigest", "objectFormat", "rawInspectionProofRef", "sourceDigest", "sourceRef", "subjectCommit", "subjectRepository", "trackedCheckoutDigest"})
        return cls(raw["subjectRepository"], _product_oid(raw["subjectCommit"]), raw["sourceRef"], raw["sourceDigest"], raw["containerFormat"], raw["objectFormat"], raw["budgetDigest"], raw["boundaryDigest"], raw["trackedCheckoutDigest"], raw["controlMetadataDigest"], raw["rawInspectionProofRef"])


@dataclass(frozen=True, slots=True)
class Ticket23ProductSourceHandoffV1:
    record: TrustedHandoffRecordV1
    source: ProductRepositorySourceV1
    producer_commit: ProductGitObjectIdV1

    def __post_init__(self) -> None:
        if not isinstance(self.record, TrustedHandoffRecordV1) or not isinstance(self.source, ProductRepositorySourceV1) or not isinstance(self.producer_commit, ProductGitObjectIdV1):
            raise ValueError("invalid product-source handoff view")
        if self.record.producer_commit != self.source.subject_commit or self.source.subject_commit != self.producer_commit:
            raise ValueError("record, source, and view product commits must match")


@dataclass(frozen=True, slots=True)
class TrustedHandoffStoreV1:
    """A durable, external content store with no-replace publication."""

    root: Path

    @classmethod
    def open_from_environment(cls) -> "TrustedHandoffStoreV1":
        raw = os.environ.get("TRUSTED_HANDOFF_STORE_ROOT")
        if not raw:
            raise ValueError("TRUSTED_HANDOFF_STORE_ROOT is required")
        path = Path(raw)
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            raise ValueError("handoff store root must be an absolute non-symlink directory")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o700 or path.stat().st_uid != os.getuid():
            raise ValueError("handoff store root must be current-user 0700")
        return cls(path.resolve(strict=True))

    def _blob_path(self, ref: str) -> Path:
        _require_digest(ref, "blob ref")
        return self.root / "sha256" / ref[7:9] / ref[9:]

    def _publish(self, path: Path, payload: bytes) -> bool:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            if path.is_symlink() or path.read_bytes() != payload:
                raise ValueError("existing handoff object bytes differ")
            return True
        descriptor, temporary_name = tempfile.mkstemp(prefix=".handoff-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != payload:
                    raise ValueError("concurrent handoff publication differs")
                return True
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return False
        finally:
            temporary.unlink(missing_ok=True)

    def put_blob(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise ValueError("handoff blob must be bytes")
        ref = _digest(payload)
        self._publish(self._blob_path(ref), payload)
        return ref

    def read_blob(self, ref: str) -> bytes:
        path = self._blob_path(ref)
        if path.is_symlink() or not path.is_file():
            raise ValueError("handoff blob is missing or unsafe")
        payload = path.read_bytes()
        if _digest(payload) != ref:
            raise ValueError("handoff blob digest mismatch")
        return payload

    def read_pointer(self, handoff_id: str) -> bytes:
        if not handoff_id or "/" in handoff_id:
            raise ValueError("invalid handoff pointer")
        path = self.root / "pointers" / f"{handoff_id}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("handoff pointer is missing or unsafe")
        return path.read_bytes()

    def publish_pointer(self, handoff_id: str, payload: bytes) -> bool:
        if not handoff_id or "/" in handoff_id or not isinstance(payload, bytes):
            raise ValueError("invalid handoff pointer")
        return self._publish(self.root / "pointers" / f"{handoff_id}.json", payload)

    def publish_export_request_ref(self, handoff_id: str, request_ref: str) -> bool:
        if not handoff_id or "/" in handoff_id:
            raise ValueError("invalid export request name")
        _require_digest(request_ref, "export request ref")
        return self._publish(self.root / "export-requests" / f"{handoff_id}.ref", (request_ref + "\n").encode("ascii"))

    def read_export_request_ref(self, handoff_id: str) -> str:
        if not handoff_id or "/" in handoff_id:
            raise ValueError("invalid export request name")
        path = self.root / "export-requests" / f"{handoff_id}.ref"
        if path.is_symlink() or not path.is_file():
            raise ValueError("export request is missing or unsafe")
        try:
            return _require_digest(path.read_text(encoding="ascii").removesuffix("\n"), "export request ref")
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("export request ref is invalid") from error


@dataclass(frozen=True, slots=True)
class HandoffRegistryV1:
    """Consumer lifecycle over authenticated, content-addressed handoff bytes."""

    store: TrustedHandoffStoreV1

    def publish_local(self, record: TrustedHandoffRecordV1, pointer: HandoffPointerV1, blobs: tuple[bytes, ...] = ()) -> None:
        if pointer.handoff_id != record.handoff_id or pointer.record_ref != record.record_digest or pointer.producer_commit != record.producer_commit:
            raise ValueError("pointer does not bind record")
        for blob in blobs:
            self.store.put_blob(blob)
        for _, ref in record.bindings:
            self.store.read_blob(ref)
        self.store.put_blob(record.canonical_bytes)
        self.store.publish_pointer(record.handoff_id, pointer.canonical_bytes)

    def publish_export_request(self, request: HandoffExportRequestV1) -> str:
        record, pointer, record_bytes, pointer_bytes = self._closure(self.store, request.record_ref, request.handoff_id)
        if request.record_full_bytes_digest != _digest(record_bytes) or request.pointer_bytes_digest != _digest(pointer_bytes):
            raise ValueError("export request full-byte digests do not match")
        if request.consumer_tasks != record.consumer_tasks or request.binding_set_digest != record.binding_set_digest:
            raise ValueError("export request does not close record consumers or bindings")
        request_ref = self.store.put_blob(request.canonical_bytes)
        self.store.publish_export_request_ref(request.handoff_id, request_ref)
        return request_ref

    def fetch(self, source_store: TrustedHandoffStoreV1, export_request_ref: str, handoff_id: str, *, consumer_task: int) -> None:
        request_bytes = source_store.read_blob(export_request_ref)
        request = HandoffExportRequestV1.from_canonical_bytes(request_bytes)
        if request.handoff_id != handoff_id or consumer_task not in request.consumer_tasks:
            raise ValueError("export request does not authorize this consumer")
        if source_store.read_export_request_ref(handoff_id) != export_request_ref:
            raise ValueError("export request is not the source-store activation point")
        record, pointer, record_bytes, pointer_bytes = self._closure(source_store, request.record_ref, handoff_id)
        if request.record_full_bytes_digest != _digest(record_bytes) or request.pointer_bytes_digest != _digest(pointer_bytes) or request.binding_set_digest != record.binding_set_digest:
            raise ValueError("export request closure digest mismatch")
        refs = tuple(ref for _, ref in record.bindings)
        if len(set(refs)) != len(refs):
            raise ValueError("handoff closure contains duplicate blob refs")
        for payload in (request_bytes, record_bytes, pointer_bytes, *(source_store.read_blob(ref) for ref in refs)):
            self.store.put_blob(payload)
        self.store.publish_pointer(handoff_id, pointer_bytes)
        manifest = canonical_json_bytes({"consumerTask": consumer_task, "handoffId": handoff_id, "pointerBytesDigest": _digest(pointer_bytes), "recordRef": request.record_ref, "requestRef": export_request_ref, "schema": "trusted-handoff-fetch/v1"})
        self.store._publish(self.store.root / "fetches" / f"{handoff_id}-{consumer_task}.json", manifest)

    def import_(self, handoff_id: str, *, consumer_task: int) -> None:
        manifest = self._read_fetch_manifest(handoff_id, consumer_task)
        request = HandoffExportRequestV1.from_canonical_bytes(self.store.read_blob(manifest["requestRef"]))
        if request.handoff_id != handoff_id or consumer_task not in request.consumer_tasks:
            raise ValueError("fetched request is not authorized")
        record, _, _, _ = self._closure(self.store, request.record_ref, handoff_id)
        for _, ref in record.bindings:
            self.store.read_blob(ref)
        self.store._publish(self.store.root / "imports" / f"{handoff_id}-{consumer_task}.json", canonical_json_bytes({"consumerTask": consumer_task, "handoffId": handoff_id, "requestRef": manifest["requestRef"], "schema": "trusted-handoff-import/v1"}))

    def finalize(self, handoff_id: str, *, expected_kind: str, expected_producer_task: int, consumer_task: int) -> None:
        self._read_import_manifest(handoff_id, consumer_task)
        raise ValueError("not finalized: transport seal and ready marker are required")

    def read(self, handoff_id: str, *, consumer_task: int, field: str) -> str:
        self._read_import_manifest(handoff_id, consumer_task)
        raise ValueError("not finalized: transport seal and ready marker are required")

    def read_product_subject(self, handoff_id: str, *, consumer_task: int) -> ProductGitObjectIdV1:
        self._read_import_manifest(handoff_id, consumer_task)
        raise ValueError("not finalized: transport seal and ready marker are required")

    def _closure(self, store: TrustedHandoffStoreV1, record_ref: str, handoff_id: str) -> tuple[TrustedHandoffRecordV1, HandoffPointerV1, bytes, bytes]:
        record_bytes = store.read_blob(record_ref)
        record = TrustedHandoffRecordV1.from_canonical_bytes(record_bytes)
        pointer_bytes = store.read_pointer(handoff_id)
        pointer = HandoffPointerV1.from_canonical_bytes(pointer_bytes)
        if record.handoff_id != handoff_id or pointer.handoff_id != handoff_id or pointer.record_ref != record_ref or pointer.record_digest != _digest(record_bytes) or pointer.producer_task != record.producer_task or pointer.producer_commit != record.producer_commit:
            raise ValueError("record and pointer closure is mixed")
        return record, pointer, record_bytes, pointer_bytes

    def _read_fetch_manifest(self, handoff_id: str, consumer_task: int) -> dict[str, object]:
        path = self.store.root / "fetches" / f"{handoff_id}-{consumer_task}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("fetched handoff is missing")
        raw = _strict_object(path.read_bytes(), {"consumerTask", "handoffId", "pointerBytesDigest", "recordRef", "requestRef", "schema"})
        if raw["schema"] != "trusted-handoff-fetch/v1" or raw["handoffId"] != handoff_id or raw["consumerTask"] != consumer_task:
            raise ValueError("fetched handoff identity is invalid")
        _require_digest(raw["requestRef"], "requestRef")
        _require_digest(raw["recordRef"], "recordRef")
        _require_digest(raw["pointerBytesDigest"], "pointerBytesDigest")
        return raw

    def _read_import_manifest(self, handoff_id: str, consumer_task: int) -> dict[str, object]:
        path = self.store.root / "imports" / f"{handoff_id}-{consumer_task}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("imported handoff is missing")
        raw = _strict_object(path.read_bytes(), {"consumerTask", "handoffId", "requestRef", "schema"})
        if raw["schema"] != "trusted-handoff-import/v1" or raw["handoffId"] != handoff_id or raw["consumerTask"] != consumer_task:
            raise ValueError("imported handoff identity is invalid")
        _require_digest(raw["requestRef"], "requestRef")
        return raw
