"""Portable codecs for authenticated cross-executor handoff records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from collections.abc import Mapping

from .e2_policy import ProductGitObjectIdV1, canonical_json_bytes


_DIGEST_PREFIX = "sha256:"


def _digest(payload: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith(_DIGEST_PREFIX) or any(character not in "0123456789abcdef" for character in value[7:]):
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")
    return value


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

    def publish_pointer(self, handoff_id: str, payload: bytes) -> bool:
        if not handoff_id or "/" in handoff_id or not isinstance(payload, bytes):
            raise ValueError("invalid handoff pointer")
        return self._publish(self.root / "pointers" / f"{handoff_id}.json", payload)
