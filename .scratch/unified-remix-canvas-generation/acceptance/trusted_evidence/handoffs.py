"""Portable codecs for authenticated cross-executor handoff records."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import json
from collections.abc import Mapping

from .e2_policy import GitObjectIdV1, ProductGitObjectIdV1, canonical_json_bytes


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


# C.1 signing-domain and policy-activation codecs.  These stay portable: the
# transport installer and broker capabilities deliberately live in later slices.
_TRANSPORT_PURPOSE = "transport-seal"
_TRANSPORT_DOMAIN = "ai-video-reverse-engineer/transport-seal/v1"
_ACTIVATION_PURPOSE = "policy-activation"
_ACTIVATION_DOMAIN = "ai-video-reverse-engineer/policy-activation/v1"
_BROKER_PURPOSE = "registration-broker-auth"
_BROKER_DOMAIN = "ai-video-reverse-engineer/registration-broker-envelope/v1"


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _policy_oid(value: object, field: str) -> GitObjectIdV1:
    if not isinstance(value, dict) or set(value) != {"algorithm", "hex"}:
        raise ValueError(f"{field} must be a policy Git object ID")
    return GitObjectIdV1(value["algorithm"], value["hex"])


def _strict_b64(value: object, field: str, *, length: int | None = None) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be canonical base64")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError(f"{field} must be canonical base64") from error
    if base64.b64encode(decoded).decode("ascii") != value or (length is not None and len(decoded) != length):
        raise ValueError(f"{field} must be canonical base64")
    return decoded


def _hex_signature(value: object, field: str) -> bytes:
    if not isinstance(value, str) or len(value) != 128 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a 64-byte lower-case hex signature")
    return bytes.fromhex(value)


def _strict_wire(payload: bytes, fields: set[str], label: str) -> dict[str, object]:
    try:
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is invalid") from error
    if not isinstance(decoded, dict) or set(decoded) != fields or canonical_json_bytes(decoded) != payload:
        raise ValueError(f"{label} is not an exact canonical object")
    return decoded


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class EmbeddedCanonicalBytesV1:
    encoding: str
    canonical_bytes_b64: str
    digest: str

    def __post_init__(self) -> None:
        if self.encoding != "base64":
            raise ValueError("embedded bytes must use base64")
        payload = _strict_b64(self.canonical_bytes_b64, "canonical_bytes_b64")
        _require_digest(self.digest, "digest")
        if _digest_bytes(payload) != self.digest:
            raise ValueError("embedded bytes digest mismatch")
        try:
            decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("embedded bytes must be canonical JSON") from error
        if canonical_json_bytes(decoded) != payload:
            raise ValueError("embedded bytes must be canonical JSON")

    @property
    def canonical_bytes(self) -> bytes:
        return _strict_b64(self.canonical_bytes_b64, "canonical_bytes")

    def to_wire(self) -> dict[str, str]:
        return {"canonicalBytesB64": self.canonical_bytes_b64, "digest": self.digest, "encoding": self.encoding}

    @classmethod
    def from_wire(cls, value: object) -> "EmbeddedCanonicalBytesV1":
        if not isinstance(value, dict) or set(value) != {"canonicalBytesB64", "digest", "encoding"}:
            raise ValueError("embedded bytes wire fields are invalid")
        return cls(value["encoding"], value["canonicalBytesB64"], value["digest"])


@dataclass(frozen=True, slots=True)
class PolicyActivationMarkerV1:
    schema: str
    operation_id: str
    activation_intent_digest: str
    policy_commit: GitObjectIdV1
    fixed_ref: str
    record_ref: str
    pointer_digest: str
    export_request_ref: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-policy-activation/v1" or self.fixed_ref != "refs/tags/trusted-acceptance/ticket-23-policy-v1":
            raise ValueError("policy activation marker identity is invalid")
        _require_text(self.operation_id, "operation_id")
        if not isinstance(self.policy_commit, GitObjectIdV1):
            raise ValueError("policy_commit is invalid")
        for field in ("activation_intent_digest", "record_ref", "pointer_digest", "export_request_ref"):
            _require_digest(getattr(self, field), field)

    def to_wire(self) -> dict[str, object]:
        return {"activationIntentDigest": self.activation_intent_digest, "exportRequestRef": self.export_request_ref, "fixedRef": self.fixed_ref, "operationId": self.operation_id, "pointerDigest": self.pointer_digest, "policyCommit": self.policy_commit.to_wire(), "recordRef": self.record_ref, "schema": self.schema}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "PolicyActivationMarkerV1":
        raw = _strict_wire(payload, {"activationIntentDigest", "exportRequestRef", "fixedRef", "operationId", "pointerDigest", "policyCommit", "recordRef", "schema"}, "policy activation marker")
        return cls(raw["schema"], raw["operationId"], raw["activationIntentDigest"], _policy_oid(raw["policyCommit"], "policyCommit"), raw["fixedRef"], raw["recordRef"], raw["pointerDigest"], raw["exportRequestRef"])


@dataclass(frozen=True, slots=True)
class SigningKeyRegistrationV1:
    key_id: str
    purpose: str
    signature_domain: str
    registry_entry_bytes_digest: str
    raw_public_key_bytes_digest: str
    raw_public_key_fingerprint: str
    wrapper_identity_digest: str
    resolved_public_key_bytes_digest: str
    resolved_public_key_fingerprint: str
    resolved_public_key: bytes

    def __post_init__(self) -> None:
        _require_text(self.key_id, "key_id")
        if (self.purpose, self.signature_domain) not in {(_TRANSPORT_PURPOSE, _TRANSPORT_DOMAIN), (_ACTIVATION_PURPOSE, _ACTIVATION_DOMAIN), (_BROKER_PURPOSE, _BROKER_DOMAIN)}:
            raise ValueError("signing registration purpose/domain is invalid")
        for field in ("registry_entry_bytes_digest", "raw_public_key_bytes_digest", "raw_public_key_fingerprint", "wrapper_identity_digest", "resolved_public_key_bytes_digest", "resolved_public_key_fingerprint"):
            _require_digest(getattr(self, field), field)
        if not isinstance(self.resolved_public_key, bytes) or len(self.resolved_public_key) != 32 or _digest_bytes(self.resolved_public_key) != self.resolved_public_key_bytes_digest or self.resolved_public_key_fingerprint != self.resolved_public_key_bytes_digest:
            raise ValueError("resolved public key identity is invalid")


def _registry_object(value: object, label: str) -> dict[str, object]:
    if isinstance(value, bytes):
        return _strict_wire(value, {"keys"}, label)
    if not isinstance(value, dict) or set(value) != {"keys"}:
        raise ValueError(f"{label} must be a strict registry")
    wire = canonical_json_bytes(value)
    if _strict_wire(wire, {"keys"}, label) != value:
        raise ValueError(f"{label} must be canonical")
    return value


def _registration(entry: object, *, expected_purpose: str, expected_domain: str) -> SigningKeyRegistrationV1:
    fields = {"keyId", "purpose", "signatureDomain", "rawPublicKey", "wrapperIdentity", "resolvedPublicKey"}
    if not isinstance(entry, dict) or set(entry) != fields:
        raise ValueError("registry entry fields are invalid")
    if entry["purpose"] != expected_purpose or entry["signatureDomain"] != expected_domain:
        raise ValueError("registry entry purpose/domain mismatch")
    raw_key = _strict_b64(entry["rawPublicKey"], "rawPublicKey", length=32)
    leaf_key = _strict_b64(entry["resolvedPublicKey"], "resolvedPublicKey", length=32)
    wrapper = _require_text(entry["wrapperIdentity"], "wrapperIdentity")
    raw_digest = _digest_bytes(raw_key)
    leaf_digest = _digest_bytes(leaf_key)
    return SigningKeyRegistrationV1(
        _require_text(entry["keyId"], "keyId"), expected_purpose, expected_domain,
        _digest_bytes(canonical_json_bytes(entry)), raw_digest, raw_digest,
        _digest_bytes(wrapper.encode("utf-8")), leaf_digest, leaf_digest, leaf_key,
    )


def _registrations(value: object, *, purpose: str, domain: str, label: str) -> tuple[SigningKeyRegistrationV1, ...]:
    registry = _registry_object(value, label)
    keys = registry["keys"]
    if not isinstance(keys, list) or not keys:
        raise ValueError(f"{label} must contain keys")
    parsed = tuple(_registration(key, expected_purpose=purpose, expected_domain=domain) for key in keys)
    if tuple(sorted(key.key_id.encode("utf-8") for key in parsed)) != tuple(key.key_id.encode("utf-8") for key in parsed) or len({key.key_id for key in parsed}) != len(parsed):
        raise ValueError(f"{label} key IDs must be unique and ordered")
    return parsed


@dataclass(frozen=True, slots=True)
class VerifiedSigningDomainsV1:
    transport_keys: tuple[SigningKeyRegistrationV1, ...]
    activation_keys: tuple[SigningKeyRegistrationV1, ...]
    canonical_digest: str


def load_disjoint_signing_domains(transport_registry: object, activation_registry: object, broker_auth_registry: object, broker_auth_revocations: object, broker_auth_allowed_signers: object) -> VerifiedSigningDomainsV1:
    transport = _registrations(transport_registry, purpose=_TRANSPORT_PURPOSE, domain=_TRANSPORT_DOMAIN, label="transport registry")
    activation = _registrations(activation_registry, purpose=_ACTIVATION_PURPOSE, domain=_ACTIVATION_DOMAIN, label="activation registry")
    broker = _registrations(broker_auth_registry, purpose=_BROKER_PURPOSE, domain=_BROKER_DOMAIN, label="broker-auth registry")
    allowed = _registrations(broker_auth_allowed_signers, purpose=_BROKER_PURPOSE, domain=_BROKER_DOMAIN, label="broker-auth allowed signers")
    if broker != allowed:
        raise ValueError("broker-auth allowed signers do not close the registry")
    if isinstance(broker_auth_revocations, bytes):
        revoked = _strict_wire(broker_auth_revocations, {"revokedKeyIds"}, "broker-auth revocations")
    else:
        revoked = broker_auth_revocations
    if not isinstance(revoked, dict) or set(revoked) != {"revokedKeyIds"} or not isinstance(revoked["revokedKeyIds"], list) or any(not isinstance(key_id, str) for key_id in revoked["revokedKeyIds"]) or len(set(revoked["revokedKeyIds"])) != len(revoked["revokedKeyIds"]):
        raise ValueError("broker-auth revocations are invalid")
    if {key.key_id for key in broker} & set(revoked["revokedKeyIds"]):
        raise ValueError("broker-auth registry includes a revoked key")
    domains = (transport, activation, broker)
    for left_index, left in enumerate(domains):
        for right in domains[left_index + 1:]:
            for attribute in ("key_id", "raw_public_key_bytes_digest", "raw_public_key_fingerprint", "wrapper_identity_digest", "resolved_public_key_bytes_digest", "resolved_public_key_fingerprint"):
                if {getattr(key, attribute) for key in left} & {getattr(key, attribute) for key in right}:
                    raise ValueError(f"signing domains collide on {attribute}")
    digest_wire = {"activation": [key.registry_entry_bytes_digest for key in activation], "broker": [key.registry_entry_bytes_digest for key in broker], "transport": [key.registry_entry_bytes_digest for key in transport]}
    return VerifiedSigningDomainsV1(transport, activation, _digest_bytes(canonical_json_bytes(digest_wire)))


# RFC 8032 Ed25519 verification expressed with Python integers.  The verifier
# accepts only 32-byte public keys and canonical 64-byte signatures; signing is
# intentionally absent so private material cannot enter this portable module.
_ED_Q = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, _ED_Q - 2, _ED_Q)) % _ED_Q
_ED_I = pow(2, (_ED_Q - 1) // 4, _ED_Q)


def _ed_add(first: tuple[int, int], second: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = first
    x2, y2 = second
    denominator = (1 + _ED_D * x1 * x2 * y1 * y2) % _ED_Q
    inverse = pow(denominator, _ED_Q - 2, _ED_Q)
    x3 = ((x1 * y2 + x2 * y1) * inverse) % _ED_Q
    y3 = ((y1 * y2 + x1 * x2) * pow((1 - _ED_D * x1 * x2 * y1 * y2) % _ED_Q, _ED_Q - 2, _ED_Q)) % _ED_Q
    return x3, y3


def _ed_mul(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    while scalar:
        if scalar & 1:
            result = _ed_add(result, point)
        point = _ed_add(point, point)
        scalar >>= 1
    return result


def _ed_decode(encoded: bytes) -> tuple[int, int]:
    if len(encoded) != 32:
        raise ValueError("Ed25519 point length is invalid")
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _ED_Q:
        raise ValueError("Ed25519 point is non-canonical")
    xx = ((y * y - 1) * pow((_ED_D * y * y + 1) % _ED_Q, _ED_Q - 2, _ED_Q)) % _ED_Q
    x = pow(xx, (_ED_Q + 3) // 8, _ED_Q)
    if (x * x - xx) % _ED_Q:
        x = (x * _ED_I) % _ED_Q
    if (x * x - xx) % _ED_Q:
        raise ValueError("Ed25519 point is invalid")
    if (x & 1) != sign:
        x = _ED_Q - x
    return x, y


def _ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        public = _ed_decode(public_key)
        encoded_r, encoded_s = signature[:32], signature[32:]
        point_r = _ed_decode(encoded_r)
        scalar_s = int.from_bytes(encoded_s, "little")
        if len(signature) != 64 or scalar_s >= _ED_L:
            return False
        base_y = (4 * pow(5, _ED_Q - 2, _ED_Q)) % _ED_Q
        base_x = pow(((base_y * base_y - 1) * pow((_ED_D * base_y * base_y + 1) % _ED_Q, _ED_Q - 2, _ED_Q)) % _ED_Q, (_ED_Q + 3) // 8, _ED_Q)
        if base_x & 1:
            base_x = _ED_Q - base_x
        challenge = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % _ED_L
        return _ed_mul((base_x, base_y), scalar_s) == _ed_add(point_r, _ed_mul(public, challenge))
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class PolicyActivationProofV1:
    schema: str
    operation_id: str
    state: str
    activate_intent: EmbeddedCanonicalBytesV1
    activation_marker: EmbeddedCanonicalBytesV1
    activated_entry: EmbeddedCanonicalBytesV1
    completion_record_ref: str
    completion_record_digest: str
    policy_commit: GitObjectIdV1
    target_head_ref: str
    target_head_value: GitObjectIdV1
    fixed_ref: str
    fixed_ref_value: GitObjectIdV1
    policy_record_ref: str
    policy_record_full_bytes_digest: str
    policy_pointer_name: str
    policy_pointer_bytes_digest: str
    policy_core_export_request_ref: str
    policy_core_export_request_full_bytes_digest: str
    profile_digest: str
    toolchain_build_record_ref: str
    trusted_git_executable_digest: str
    trusted_git_view_digest: str
    activation_trust_root_digest: str
    broker_key_id: str
    signing_key_purpose: str
    signature_domain: str
    signature_algorithm: str
    broker_signature: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-policy-activation-proof/v1" or self.state != "ACTIVATED" or self.fixed_ref != "refs/tags/trusted-acceptance/ticket-23-policy-v1" or self.signing_key_purpose != _ACTIVATION_PURPOSE or self.signature_domain != _ACTIVATION_DOMAIN or self.signature_algorithm != "ssh-ed25519":
            raise ValueError("policy activation proof identity is invalid")
        _require_text(self.operation_id, "operation_id")
        _require_text(self.target_head_ref, "target_head_ref")
        _require_text(self.policy_pointer_name, "policy_pointer_name")
        _require_text(self.broker_key_id, "broker_key_id")
        if not all(isinstance(value, EmbeddedCanonicalBytesV1) for value in (self.activate_intent, self.activation_marker, self.activated_entry)) or not all(isinstance(value, GitObjectIdV1) for value in (self.policy_commit, self.target_head_value, self.fixed_ref_value)):
            raise ValueError("policy activation proof nested values are invalid")
        for field in ("completion_record_ref", "completion_record_digest", "policy_record_ref", "policy_record_full_bytes_digest", "policy_pointer_bytes_digest", "policy_core_export_request_ref", "policy_core_export_request_full_bytes_digest", "profile_digest", "toolchain_build_record_ref", "trusted_git_executable_digest", "trusted_git_view_digest", "activation_trust_root_digest"):
            _require_digest(getattr(self, field), field)
        _hex_signature(self.broker_signature, "broker_signature")

    def unsigned_wire(self) -> dict[str, object]:
        wire = self.to_wire()
        del wire["brokerSignature"]
        return wire

    def to_wire(self) -> dict[str, object]:
        return {"activateIntent": self.activate_intent.to_wire(), "activatedEntry": self.activated_entry.to_wire(), "activationMarker": self.activation_marker.to_wire(), "activationTrustRootDigest": self.activation_trust_root_digest, "brokerKeyId": self.broker_key_id, "brokerSignature": self.broker_signature, "completionRecordDigest": self.completion_record_digest, "completionRecordRef": self.completion_record_ref, "fixedRef": self.fixed_ref, "fixedRefValue": self.fixed_ref_value.to_wire(), "operationId": self.operation_id, "policyCommit": self.policy_commit.to_wire(), "policyCoreExportRequestFullBytesDigest": self.policy_core_export_request_full_bytes_digest, "policyCoreExportRequestRef": self.policy_core_export_request_ref, "policyPointerBytesDigest": self.policy_pointer_bytes_digest, "policyPointerName": self.policy_pointer_name, "policyRecordFullBytesDigest": self.policy_record_full_bytes_digest, "policyRecordRef": self.policy_record_ref, "profileDigest": self.profile_digest, "schema": self.schema, "signatureAlgorithm": self.signature_algorithm, "signatureDomain": self.signature_domain, "signingKeyPurpose": self.signing_key_purpose, "state": self.state, "targetHeadRef": self.target_head_ref, "targetHeadValue": self.target_head_value.to_wire(), "toolchainBuildRecordRef": self.toolchain_build_record_ref, "trustedGitExecutableDigest": self.trusted_git_executable_digest, "trustedGitViewDigest": self.trusted_git_view_digest}

    @property
    def unsigned_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.unsigned_wire())

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "PolicyActivationProofV1":
        fields = {"activateIntent", "activatedEntry", "activationMarker", "activationTrustRootDigest", "brokerKeyId", "brokerSignature", "completionRecordDigest", "completionRecordRef", "fixedRef", "fixedRefValue", "operationId", "policyCommit", "policyCoreExportRequestFullBytesDigest", "policyCoreExportRequestRef", "policyPointerBytesDigest", "policyPointerName", "policyRecordFullBytesDigest", "policyRecordRef", "profileDigest", "schema", "signatureAlgorithm", "signatureDomain", "signingKeyPurpose", "state", "targetHeadRef", "targetHeadValue", "toolchainBuildRecordRef", "trustedGitExecutableDigest", "trustedGitViewDigest"}
        raw = _strict_wire(payload, fields, "policy activation proof")
        return cls(raw["schema"], raw["operationId"], raw["state"], EmbeddedCanonicalBytesV1.from_wire(raw["activateIntent"]), EmbeddedCanonicalBytesV1.from_wire(raw["activationMarker"]), EmbeddedCanonicalBytesV1.from_wire(raw["activatedEntry"]), raw["completionRecordRef"], raw["completionRecordDigest"], _policy_oid(raw["policyCommit"], "policyCommit"), raw["targetHeadRef"], _policy_oid(raw["targetHeadValue"], "targetHeadValue"), raw["fixedRef"], _policy_oid(raw["fixedRefValue"], "fixedRefValue"), raw["policyRecordRef"], raw["policyRecordFullBytesDigest"], raw["policyPointerName"], raw["policyPointerBytesDigest"], raw["policyCoreExportRequestRef"], raw["policyCoreExportRequestFullBytesDigest"], raw["profileDigest"], raw["toolchainBuildRecordRef"], raw["trustedGitExecutableDigest"], raw["trustedGitViewDigest"], raw["activationTrustRootDigest"], raw["brokerKeyId"], raw["signingKeyPurpose"], raw["signatureDomain"], raw["signatureAlgorithm"], raw["brokerSignature"])


@dataclass(frozen=True, slots=True)
class Ticket23PolicyHandoffV1:
    schema: str
    core_record_ref: str
    core_record_full_bytes_digest: str
    core_pointer_name: str
    core_pointer_bytes_digest: str
    core_export_request_ref: str
    core_export_request_full_bytes_digest: str
    policy_activation_proof_ref: str
    policy_activation_proof_full_bytes_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-ticket23-policy-handoff/v1":
            raise ValueError("policy handoff schema is invalid")
        _require_text(self.core_pointer_name, "core_pointer_name")
        for field in ("core_record_ref", "core_record_full_bytes_digest", "core_pointer_bytes_digest", "core_export_request_ref", "core_export_request_full_bytes_digest", "policy_activation_proof_ref", "policy_activation_proof_full_bytes_digest"):
            _require_digest(getattr(self, field), field)
        if self.policy_activation_proof_ref != self.policy_activation_proof_full_bytes_digest:
            raise ValueError("policy handoff proof reference must equal its full-bytes digest")

    def to_wire(self) -> dict[str, str]:
        return {"coreExportRequestFullBytesDigest": self.core_export_request_full_bytes_digest, "coreExportRequestRef": self.core_export_request_ref, "corePointerBytesDigest": self.core_pointer_bytes_digest, "corePointerName": self.core_pointer_name, "coreRecordFullBytesDigest": self.core_record_full_bytes_digest, "coreRecordRef": self.core_record_ref, "policyActivationProofFullBytesDigest": self.policy_activation_proof_full_bytes_digest, "policyActivationProofRef": self.policy_activation_proof_ref, "schema": self.schema}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "Ticket23PolicyHandoffV1":
        raw = _strict_wire(payload, {"coreExportRequestFullBytesDigest", "coreExportRequestRef", "corePointerBytesDigest", "corePointerName", "coreRecordFullBytesDigest", "coreRecordRef", "policyActivationProofFullBytesDigest", "policyActivationProofRef", "schema"}, "policy handoff")
        return cls(raw["schema"], raw["coreRecordRef"], raw["coreRecordFullBytesDigest"], raw["corePointerName"], raw["corePointerBytesDigest"], raw["coreExportRequestRef"], raw["coreExportRequestFullBytesDigest"], raw["policyActivationProofRef"], raw["policyActivationProofFullBytesDigest"])


@dataclass(frozen=True, slots=True)
class ActivatedPolicyExportRequestV1:
    schema: str
    handoff_id: str
    policy_handoff_ref: str
    policy_handoff_full_bytes_digest: str
    core_export_request_ref: str
    core_export_request_full_bytes_digest: str
    policy_activation_proof_ref: str
    policy_activation_proof_full_bytes_digest: str
    consumer_tasks: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-activated-policy-export-request/v1" or self.handoff_id != "ticket-23-policy-v1":
            raise ValueError("activated policy export request identity is invalid")
        for field in ("policy_handoff_ref", "policy_handoff_full_bytes_digest", "core_export_request_ref", "core_export_request_full_bytes_digest", "policy_activation_proof_ref", "policy_activation_proof_full_bytes_digest"):
            _require_digest(getattr(self, field), field)
        if self.policy_activation_proof_ref != self.policy_activation_proof_full_bytes_digest or not self.consumer_tasks or tuple(sorted(self.consumer_tasks)) != self.consumer_tasks or len(set(self.consumer_tasks)) != len(self.consumer_tasks) or any(not isinstance(task, int) or task <= 0 for task in self.consumer_tasks):
            raise ValueError("activated policy export request closure is invalid")

    def to_wire(self) -> dict[str, object]:
        return {"consumerTasks": list(self.consumer_tasks), "coreExportRequestFullBytesDigest": self.core_export_request_full_bytes_digest, "coreExportRequestRef": self.core_export_request_ref, "handoffId": self.handoff_id, "policyActivationProofFullBytesDigest": self.policy_activation_proof_full_bytes_digest, "policyActivationProofRef": self.policy_activation_proof_ref, "policyHandoffFullBytesDigest": self.policy_handoff_full_bytes_digest, "policyHandoffRef": self.policy_handoff_ref, "schema": self.schema}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "ActivatedPolicyExportRequestV1":
        raw = _strict_wire(payload, {"consumerTasks", "coreExportRequestFullBytesDigest", "coreExportRequestRef", "handoffId", "policyActivationProofFullBytesDigest", "policyActivationProofRef", "policyHandoffFullBytesDigest", "policyHandoffRef", "schema"}, "activated policy export request")
        if not isinstance(raw["consumerTasks"], list):
            raise ValueError("activated policy export request consumers are invalid")
        return cls(raw["schema"], raw["handoffId"], raw["policyHandoffRef"], raw["policyHandoffFullBytesDigest"], raw["coreExportRequestRef"], raw["coreExportRequestFullBytesDigest"], raw["policyActivationProofRef"], raw["policyActivationProofFullBytesDigest"], tuple(raw["consumerTasks"]))


@dataclass(frozen=True, slots=True)
class VerifiedPolicyActivationV1:
    proof_ref: str
    proof_full_bytes_digest: str
    policy_commit: GitObjectIdV1
    operation_id: str


def _journal_entry(value: EmbeddedCanonicalBytesV1, label: str) -> dict[str, object]:
    raw = _strict_wire(value.canonical_bytes, {"activationMarkerDigest", "completionRecordDigest", "completionRecordRef", "entryDigest", "exportRequestRef", "fixedRef", "fixedRefMustBeAbsent", "operationId", "pendingTransition", "pointerDigest", "policyCommit", "previousEntryDigest", "recordRef", "schema", "stagedPayloadDigest", "state", "targetHeadRef"}, label)
    if raw["schema"] != "trusted-policy-publish-transaction/v1":
        raise ValueError(f"{label} schema is invalid")
    return raw


def verify_policy_activation_proof(proof_bytes: bytes, signing_domains: VerifiedSigningDomainsV1, expected_transport: Mapping[str, object]) -> VerifiedPolicyActivationV1:
    if not isinstance(signing_domains, VerifiedSigningDomainsV1) or not isinstance(expected_transport, Mapping):
        raise ValueError("activation verifier inputs are invalid")
    proof = PolicyActivationProofV1.from_canonical_bytes(proof_bytes)
    proof_ref = _digest_bytes(proof_bytes)
    if proof.activation_trust_root_digest != signing_domains.canonical_digest:
        raise ValueError("activation proof trust root is not the loaded domain set")
    registration = next((key for key in signing_domains.activation_keys if key.key_id == proof.broker_key_id), None)
    if registration is None or registration.purpose != proof.signing_key_purpose or registration.signature_domain != proof.signature_domain:
        raise ValueError("activation signing key is not registered for this purpose")
    message = _ACTIVATION_DOMAIN.encode("utf-8") + b"\0" + proof.unsigned_canonical_bytes
    if not _ed25519_verify(registration.resolved_public_key, message, _hex_signature(proof.broker_signature, "broker_signature")):
        raise ValueError("activation proof signature is invalid")
    intent = _journal_entry(proof.activate_intent, "activation intent")
    marker = PolicyActivationMarkerV1.from_canonical_bytes(proof.activation_marker.canonical_bytes)
    activated = _journal_entry(proof.activated_entry, "activated entry")
    if intent["operationId"] != proof.operation_id or marker.operation_id != proof.operation_id or activated["operationId"] != proof.operation_id or intent["pendingTransition"] != "ACTIVATE" or activated["state"] != "ACTIVATED" or marker.activation_intent_digest != proof.activate_intent.digest or activated["activationMarkerDigest"] != proof.activation_marker.digest or activated["previousEntryDigest"] != proof.activate_intent.digest:
        raise ValueError("activation proof chain is invalid")
    if marker.policy_commit != proof.policy_commit or marker.fixed_ref != proof.fixed_ref or marker.record_ref != proof.policy_record_ref or marker.pointer_digest != proof.policy_pointer_bytes_digest or marker.export_request_ref != proof.policy_core_export_request_ref:
        raise ValueError("activation marker does not bind proof core fields")
    for field, expected in (("completionRecordRef", proof.completion_record_ref), ("completionRecordDigest", proof.completion_record_digest), ("recordRef", proof.policy_record_ref), ("pointerDigest", proof.policy_pointer_bytes_digest), ("exportRequestRef", proof.policy_core_export_request_ref), ("fixedRef", proof.fixed_ref), ("targetHeadRef", proof.target_head_ref)):
        if activated[field] != expected:
            raise ValueError("activated entry does not bind proof fields")
    if _policy_oid(activated["policyCommit"], "activated policyCommit") != proof.policy_commit or _policy_oid(proof.target_head_value.to_wire(), "targetHeadValue") != proof.policy_commit or _policy_oid(proof.fixed_ref_value.to_wire(), "fixedRefValue") != proof.policy_commit:
        raise ValueError("activation proof final Git values are inconsistent")
    for key in ("consumerTask", "destinationExecutorId"):
        if key not in expected_transport or not isinstance(expected_transport[key], (int, str)) or not expected_transport[key]:
            raise ValueError("expected transport binding is incomplete")
    for key, expected in (("policyActivationProofRef", proof_ref), ("policyActivationProofFullBytesDigest", proof_ref)):
        if key in expected_transport and expected_transport[key] != expected:
            raise ValueError("expected transport proof binding is invalid")
    return VerifiedPolicyActivationV1(proof_ref, proof_ref, proof.policy_commit, proof.operation_id)


def verify_registration_broker_startup_challenge(*_args: object, **_kwargs: object) -> None:
    """Slice E owns broker-auth slots; accepting here would create a bypass."""
    raise ValueError("registration broker startup challenge is unavailable until broker-auth is installed")
