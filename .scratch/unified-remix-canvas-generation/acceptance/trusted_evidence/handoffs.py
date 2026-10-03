"""Portable codecs for authenticated cross-executor handoff records."""

from __future__ import annotations

from dataclasses import dataclass
import base64
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import secrets
import stat
import json
from collections.abc import Mapping

from .e2_policy import (
    GitObjectIdV1,
    ProductGitObjectIdV1,
    RegistrationBrokerAuthRegistryV1,
    RegistrationBrokerAuthRevocationsV1,
    canonical_json_bytes,
    verify_registration_broker_governance,
)


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
class PolicyHandoffRecordV1:
    """Strict policy record; unlike product records its Git object may be SHA-1 or SHA-256."""

    schema: str
    handoff_id: str
    kind: str
    producer_task: int
    producer_commit: GitObjectIdV1
    consumer_tasks: tuple[int, ...]
    bindings: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-policy-handoff-record/v1" or self.handoff_id != "ticket-23-policy-v1" or self.kind != "policy" or self.producer_task != 27 or type(self.producer_commit) is not GitObjectIdV1:
            raise ValueError("policy handoff record identity is invalid")
        if not self.consumer_tasks or tuple(sorted(self.consumer_tasks)) != self.consumer_tasks or len(set(self.consumer_tasks)) != len(self.consumer_tasks) or any(type(task) is not int or task <= 0 for task in self.consumer_tasks):
            raise ValueError("policy consumers are invalid")
        bindings = tuple(sorted(self.bindings, key=lambda item: item[0].encode("utf-8")))
        if not bindings or len({name for name, _ in bindings}) != len(bindings):
            raise ValueError("policy bindings are invalid")
        for name, digest in bindings:
            _require_transport_identity(name, "policy binding name")
            _require_digest(digest, "policy binding digest")
        object.__setattr__(self, "bindings", bindings)

    def to_wire(self) -> dict[str, object]:
        return {"bindings": [{"digest": digest, "name": name} for name, digest in self.bindings], "consumerTasks": list(self.consumer_tasks), "handoffId": self.handoff_id, "kind": self.kind, "producerCommit": self.producer_commit.to_wire(), "producerTask": self.producer_task, "schema": self.schema}

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
    def from_canonical_bytes(cls, payload: bytes) -> "PolicyHandoffRecordV1":
        raw = _strict_object(payload, {"bindings", "consumerTasks", "handoffId", "kind", "producerCommit", "producerTask", "schema"})
        if not isinstance(raw["bindings"], list) or not isinstance(raw["consumerTasks"], list) or not isinstance(raw["producerCommit"], dict) or set(raw["producerCommit"]) != {"algorithm", "hex"}:
            raise ValueError("policy handoff record wire is invalid")
        bindings = []
        for item in raw["bindings"]:
            if not isinstance(item, dict) or set(item) != {"digest", "name"}:
                raise ValueError("policy handoff binding wire is invalid")
            bindings.append((item["name"], item["digest"]))
        return cls(raw["schema"], raw["handoffId"], raw["kind"], raw["producerTask"], GitObjectIdV1(raw["producerCommit"]["algorithm"], raw["producerCommit"]["hex"]), tuple(raw["consumerTasks"]), tuple(bindings))


@dataclass(frozen=True, slots=True)
class PolicyHandoffPointerV1:
    schema: str
    handoff_id: str
    record_ref: str
    record_digest: str
    producer_task: int
    producer_commit: GitObjectIdV1

    def __post_init__(self) -> None:
        if self.schema != "trusted-policy-handoff-pointer/v1" or self.handoff_id != "ticket-23-policy-v1" or self.producer_task != 27 or type(self.producer_commit) is not GitObjectIdV1:
            raise ValueError("policy handoff pointer identity is invalid")
        _require_digest(self.record_ref, "policy record ref")
        _require_digest(self.record_digest, "policy record digest")
        if self.record_ref != self.record_digest:
            raise ValueError("policy pointer does not bind full record bytes")

    def to_wire(self) -> dict[str, object]:
        return {"handoffId": self.handoff_id, "producerCommit": self.producer_commit.to_wire(), "producerTask": self.producer_task, "recordDigest": self.record_digest, "recordRef": self.record_ref, "schema": self.schema}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "PolicyHandoffPointerV1":
        raw = _strict_object(payload, {"handoffId", "producerCommit", "producerTask", "recordDigest", "recordRef", "schema"})
        if not isinstance(raw["producerCommit"], dict) or set(raw["producerCommit"]) != {"algorithm", "hex"}:
            raise ValueError("policy handoff pointer wire is invalid")
        return cls(raw["schema"], raw["handoffId"], raw["recordRef"], raw["recordDigest"], raw["producerTask"], GitObjectIdV1(raw["producerCommit"]["algorithm"], raw["producerCommit"]["hex"]))


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


class TrustedHandoffStoreV1:
    """A durable store rooted in one held, no-follow directory capability."""

    __slots__ = ("root", "_root_fd", "_binding", "_sealed")

    def __init__(self, root: Path) -> None:
        root, descriptor, binding = self._open_root(root)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "_root_fd", descriptor)
        object.__setattr__(self, "_binding", binding)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError("handoff store capability is immutable")
        object.__setattr__(self, name, value)

    @staticmethod
    def _open_root(value: Path) -> tuple[Path, int, tuple[tuple[str, int, int, int, int, int], ...]]:
        if os.name != "posix" or not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise ValueError("handoff store owning-FD capability is unavailable")
        path = Path(value)
        if not path.is_absolute() or path == Path("/") or any(part in {".", ".."} for part in path.parts):
            raise ValueError("handoff store root must be a narrow absolute directory")
        protected = (Path.home(), Path.cwd())
        if any(path == item or path in item.parents for item in protected):
            raise ValueError("handoff store root is too broad")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open("/", flags)
        binding: list[tuple[str, int, int, int, int, int]] = []
        try:
            for component in path.parts[1:]:
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                info = os.fstat(descriptor)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("handoff store ancestor is not a directory")
                binding.append((component, info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode), os.fstatvfs(descriptor).f_fsid))
            info = os.fstat(descriptor)
            if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
                raise ValueError("handoff store root must be current-user 0700")
            return path, descriptor, tuple(binding)
        except OSError as error:
            os.close(descriptor)
            raise ValueError("handoff store root is unsafe") from error
        except Exception:
            os.close(descriptor)
            raise

    @classmethod
    def open_from_environment(cls) -> "TrustedHandoffStoreV1":
        raw = os.environ.get("TRUSTED_HANDOFF_STORE_ROOT")
        if not raw:
            raise ValueError("TRUSTED_HANDOFF_STORE_ROOT is required")
        store = cls(Path(raw))
        try:
            lease = os.environ.get("TRUSTED_HANDOFF_STORE_LEASE")
            if lease is None:
                raise ValueError("handoff store project lease is required")
            raw_lease = _strict_object(lease.encode("utf-8"), {"projectId", "rootDevice", "rootInode", "schema"})
            if raw_lease["schema"] != "trusted-handoff-store-lease/v1" or raw_lease["projectId"] != "ai-video-reverse-engineer":
                raise ValueError("handoff store project lease is invalid")
            if any(type(raw_lease[field]) is not int or raw_lease[field] < 0 for field in ("rootDevice", "rootInode")):
                raise ValueError("handoff store project lease is invalid")
            info = os.fstat(store._root_fd)
            if (raw_lease["rootDevice"], raw_lease["rootInode"]) != (info.st_dev, info.st_ino):
                raise ValueError("handoff store project lease does not bind root")
            store._verify_binding()
            return store
        except Exception:
            store.close()
            raise

    def __copy__(self):
        raise TypeError("handoff store capability cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("handoff store capability cannot be copied")

    def __reduce__(self):
        raise TypeError("handoff store capability cannot be serialized")

    def __replace__(self, **changes):
        raise TypeError("handoff store capability cannot be copied")

    def close(self) -> None:
        descriptor = getattr(self, "_root_fd", -1)
        if descriptor >= 0:
            os.close(descriptor)
            object.__setattr__(self, "_root_fd", -1)

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def _verify_binding(self) -> None:
        if self._root_fd < 0:
            raise ValueError("handoff store capability is closed")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open("/", flags)
        try:
            for component, device, inode, owner, mode, mount_id in self._binding:
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode), os.fstatvfs(descriptor).f_fsid) != (device, inode, owner, mode, mount_id):
                    raise ValueError("handoff store root binding changed")
            held = os.fstat(self._root_fd)
            if (held.st_dev, held.st_ino, held.st_uid, stat.S_IMODE(held.st_mode), os.fstatvfs(self._root_fd).f_fsid) != self._binding[-1][1:]:
                raise ValueError("handoff store root binding changed")
            if stat.S_IMODE(held.st_mode) != 0o700 or held.st_uid != os.getuid():
                raise ValueError("handoff store root binding changed")
        except OSError as error:
            raise ValueError("handoff store root binding changed") from error
        finally:
            os.close(descriptor)

    @staticmethod
    def _parts(*parts: str) -> tuple[str, ...]:
        if not parts or any(not isinstance(part, str) or not part or "/" in part or "\x00" in part or part in {".", ".."} for part in parts):
            raise ValueError("handoff store path is invalid")
        return parts

    def _open_directory(self, parts: tuple[str, ...], *, create: bool) -> int:
        self._verify_binding()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.dup(self._root_fd)
        try:
            for component in parts:
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise ValueError("handoff store directory is missing")
                    try:
                        os.mkdir(component, 0o700, dir_fd=descriptor)
                        os.fsync(descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(component, flags, dir_fd=descriptor)
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
                    os.close(child)
                    raise ValueError("handoff store directory is unsafe")
                os.close(descriptor)
                descriptor = child
            self._verify_binding()
            return descriptor
        except OSError as error:
            os.close(descriptor)
            raise ValueError("handoff store directory is unsafe") from error
        except Exception:
            os.close(descriptor)
            raise

    def _verify_relative_directory(self, parts: tuple[str, ...], expected_fd: int) -> None:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.dup(self._root_fd)
        try:
            for component in parts:
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            expected = os.fstat(expected_fd)
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino, actual.st_uid, stat.S_IMODE(actual.st_mode)) != (expected.st_dev, expected.st_ino, expected.st_uid, stat.S_IMODE(expected.st_mode)):
                raise ValueError("handoff store directory binding changed")
        except OSError as error:
            raise ValueError("handoff store directory binding changed") from error
        finally:
            os.close(descriptor)

    def _read_at(self, parent_fd: int, name: str) -> tuple[bytes, tuple[int, int, int]]:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("handoff store entry is unsafe")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
                raise ValueError("handoff store entry changed")
            payload = bytearray()
            while True:
                block = os.read(descriptor, 65536)
                if not block:
                    break
                payload.extend(block)
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (after.st_dev, after.st_ino, after.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                raise ValueError("handoff store entry changed")
            return bytes(payload), (opened.st_dev, opened.st_ino, opened.st_size)
        finally:
            os.close(descriptor)

    def _publish_parts(self, parts: tuple[str, ...], payload: bytes) -> bool:
        if not isinstance(payload, bytes):
            raise ValueError("handoff store payload must be bytes")
        parent_fd = self._open_directory(parts[:-1], create=True)
        name = parts[-1]
        temporary = ".handoff-" + secrets.token_hex(16)
        try:
            self._verify_relative_directory(parts[:-1], parent_fd)
            try:
                existing, identity = self._read_at(parent_fd, name)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if existing != payload:
                    raise ValueError("existing handoff object bytes differ")
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino, current.st_size) != identity:
                    raise ValueError("handoff store entry changed")
                self._verify_binding()
                return True
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=parent_fd)
            try:
                offset = 0
                while offset < len(payload):
                    offset += os.write(descriptor, payload[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            temporary_identity = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
            self._verify_relative_directory(parts[:-1], parent_fd)
            try:
                os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            except FileExistsError:
                existing, identity = self._read_at(parent_fd, name)
                if existing != payload:
                    raise ValueError("concurrent handoff publication differs")
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino, current.st_size) != identity:
                    raise ValueError("handoff store entry changed")
                self._verify_binding()
                return True
            try:
                installed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (installed.st_dev, installed.st_ino) != (temporary_identity.st_dev, temporary_identity.st_ino):
                    raise ValueError("handoff store entry changed")
                self._verify_relative_directory(parts[:-1], parent_fd)
                os.fsync(parent_fd)
                self._verify_binding()
                return False
            except Exception:
                try:
                    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (temporary_identity.st_dev, temporary_identity.st_ino):
                        os.unlink(name, dir_fd=parent_fd)
                        os.fsync(parent_fd)
                except OSError:
                    pass
                raise
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def _read_parts(self, parts: tuple[str, ...]) -> bytes:
        parent_fd = self._open_directory(parts[:-1], create=False)
        try:
            payload, _identity = self._read_at(parent_fd, parts[-1])
            self._verify_binding()
            return payload
        except FileNotFoundError as error:
            raise ValueError("handoff store entry is missing or unsafe") from error
        finally:
            os.close(parent_fd)

    def _blob_parts(self, ref: str) -> tuple[str, ...]:
        _require_digest(ref, "blob ref")
        return self._parts("sha256", ref[7:9], ref[9:])

    def put_blob(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise ValueError("handoff blob must be bytes")
        ref = _digest(payload)
        self._publish_parts(self._blob_parts(ref), payload)
        return ref

    def read_blob(self, ref: str) -> bytes:
        payload = self._read_parts(self._blob_parts(ref))
        if _digest(payload) != ref:
            raise ValueError("handoff blob digest mismatch")
        return payload

    def read_pointer(self, handoff_id: str) -> bytes:
        if not handoff_id or "/" in handoff_id:
            raise ValueError("invalid handoff pointer")
        return self._read_parts(self._parts("pointers", f"{handoff_id}.json"))

    def publish_pointer(self, handoff_id: str, payload: bytes) -> bool:
        if not handoff_id or "/" in handoff_id or not isinstance(payload, bytes):
            raise ValueError("invalid handoff pointer")
        return self._publish_parts(self._parts("pointers", f"{handoff_id}.json"), payload)

    def publish_export_request_ref(self, handoff_id: str, request_ref: str) -> bool:
        if not handoff_id or "/" in handoff_id:
            raise ValueError("invalid export request name")
        _require_digest(request_ref, "export request ref")
        return self._publish_parts(self._parts("export-requests", f"{handoff_id}.ref"), (request_ref + "\n").encode("ascii"))

    def read_export_request_ref(self, handoff_id: str) -> str:
        if not handoff_id or "/" in handoff_id:
            raise ValueError("invalid export request name")
        try:
            return _require_digest(self._read_parts(self._parts("export-requests", f"{handoff_id}.ref")).decode("ascii").removesuffix("\n"), "export request ref")
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("export request ref is invalid") from error

    def publish_policy_export_request_ref(self, handoff_id: str, request_ref: str) -> bool:
        if handoff_id != "ticket-23-policy-v1":
            raise ValueError("invalid policy export request name")
        _require_digest(request_ref, "policy export request ref")
        return self._publish_parts(self._parts("policy-export-requests", f"{handoff_id}.ref"), (request_ref + "\n").encode("ascii"))

    def read_policy_export_request_ref(self, handoff_id: str) -> str:
        if handoff_id != "ticket-23-policy-v1":
            raise ValueError("invalid policy export request name")
        try:
            return _require_digest(self._read_parts(self._parts("policy-export-requests", f"{handoff_id}.ref")).decode("ascii").removesuffix("\n"), "policy export request ref")
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("policy export request ref is invalid") from error

    def _transport_path(self, handoff_id: str, transport_id: str, name: str) -> tuple[str, ...]:
        if not all(isinstance(value, str) and value and "/" not in value for value in (handoff_id, transport_id, name)):
            raise ValueError("transport path identity is invalid")
        return self._parts("transports", handoff_id, transport_id, name)

    def publish_transport_file(self, handoff_id: str, transport_id: str, name: str, payload: bytes) -> bool:
        if not isinstance(payload, bytes):
            raise ValueError("transport payload must be bytes")
        return self._publish_parts(self._transport_path(handoff_id, transport_id, name), payload)

    def read_transport_file(self, handoff_id: str, transport_id: str, name: str) -> bytes:
        return self._read_parts(self._transport_path(handoff_id, transport_id, name))

    def publish_transport_replay(self, manifest: "HandoffTransferManifestV1") -> None:
        self._publish_parts(self._transport_replay_path(manifest), _transport_replay_bytes(manifest))

    def verify_transport_replay(self, manifest: "HandoffTransferManifestV1") -> None:
        try:
            payload = self._read_parts(self._transport_replay_path(manifest))
        except ValueError as error:
            raise ValueError("transport replay state is missing or mixed") from error
        if payload != _transport_replay_bytes(manifest):
            raise ValueError("transport replay state is missing or mixed")

    def _transport_replay_path(self, manifest: "HandoffTransferManifestV1") -> tuple[str, ...]:
        destination_key = hashlib.sha256(manifest.destination_executor_id.encode("utf-8")).hexdigest()
        return self._parts("transport-replays", manifest.handoff_id, f"{manifest.consumer_task}-{destination_key}-{manifest.sequence}.json")

    def _publish_file(self, parts: tuple[str, ...], payload: bytes) -> bool:
        return self._publish_parts(self._parts(*parts), payload)

    def _read_file(self, parts: tuple[str, ...]) -> bytes:
        return self._read_parts(self._parts(*parts))

    def verify_root(self, *, expected_mode: int | None = None, require_empty: bool = False) -> None:
        self._verify_binding()
        info = os.fstat(self._root_fd)
        if expected_mode is not None and stat.S_IMODE(info.st_mode) != expected_mode:
            raise ValueError("handoff store root mode is invalid")
        if require_empty:
            descriptor = os.dup(self._root_fd)
            try:
                if any(True for _ in os.scandir(descriptor)):
                    raise ValueError("cache is not empty")
            finally:
                os.close(descriptor)
        self._verify_binding()


def _require_transport_identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or "/" in value or "\x00" in value:
        raise ValueError(f"{field} is invalid")
    return value


def _require_nonce(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("transport nonce is invalid")
    return value


def _require_namespace(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\x00" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("store_namespace is invalid")
    return value


def _require_expiry(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("transport expiry is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("transport expiry is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("transport expiry must include a timezone")
    return value


def _proof_fields(ref: object, digest: object) -> tuple[str | None, str | None]:
    if ref is None and digest is None:
        return None, None
    _require_digest(ref, "policy_activation_proof_ref")
    _require_digest(digest, "policy_activation_proof_full_bytes_digest")
    if ref != digest:
        raise ValueError("policy proof reference must equal its full-bytes digest")
    return ref, digest


@dataclass(frozen=True, slots=True)
class TransportJobV1:
    """In-memory only complete invocation input for the four transport stages."""

    source_store_root: Path
    target_store_root: Path
    stage_root: Path
    export_request_ref: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    store_namespace: str
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    expires_at: str | None
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None
    signing_key_fd: int
    key_id: str
    signing_key_purpose: str = "transport-seal"
    signature_domain: str = "ai-video-reverse-engineer/transport-seal/v1"
    signing_domains: "VerifiedSigningDomainsV1 | None" = None

    def __post_init__(self) -> None:
        for path in (self.source_store_root, self.target_store_root, self.stage_root):
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError("transport roots must be absolute paths")
        for field in ("export_request_ref",):
            _require_digest(getattr(self, field), field)
        for field in ("handoff_id", "transport_id", "source_executor_id", "destination_executor_id", "key_id"):
            _require_transport_identity(getattr(self, field), field)
        _require_namespace(self.store_namespace)
        if not isinstance(self.consumer_task, int) or self.consumer_task <= 0 or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("transport consumer or sequence is invalid")
        _require_nonce(self.nonce)
        _require_expiry(self.expires_at)
        _proof_fields(self.policy_activation_proof_ref, self.policy_activation_proof_full_bytes_digest)
        if not isinstance(self.signing_key_fd, int) or self.signing_key_fd < 0 or self.signing_key_purpose != "transport-seal" or self.signature_domain != "ai-video-reverse-engineer/transport-seal/v1":
            raise ValueError("transport signing identity is invalid")


@dataclass(frozen=True, slots=True)
class HandoffTransferManifestV1:
    schema: str
    export_request_ref: str
    export_request_full_bytes_digest: str
    store_namespace: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    record_ref: str
    record_full_bytes_digest: str
    pointer_name: str
    pointer_bytes_digest: str
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    expires_at: str | None
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None
    blob_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-handoff-transfer/v1" or self.export_request_ref != self.export_request_full_bytes_digest:
            raise ValueError("transport manifest identity is invalid")
        for field in ("export_request_ref", "export_request_full_bytes_digest", "record_ref", "record_full_bytes_digest", "pointer_bytes_digest"):
            _require_digest(getattr(self, field), field)
        if self.record_ref != self.record_full_bytes_digest:
            raise ValueError("transport manifest record digest is invalid")
        for field in ("handoff_id", "transport_id", "pointer_name", "source_executor_id", "destination_executor_id"):
            _require_transport_identity(getattr(self, field), field)
        _require_namespace(self.store_namespace)
        if self.pointer_name != self.handoff_id or not isinstance(self.consumer_task, int) or self.consumer_task <= 0 or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("transport manifest binding is invalid")
        _require_nonce(self.nonce)
        _require_expiry(self.expires_at)
        _proof_fields(self.policy_activation_proof_ref, self.policy_activation_proof_full_bytes_digest)
        if not self.blob_refs or tuple(sorted(self.blob_refs)) != self.blob_refs or len(set(self.blob_refs)) != len(self.blob_refs):
            raise ValueError("transport manifest blobs are not a canonical closure")
        for ref in self.blob_refs:
            _require_digest(ref, "transport blob ref")

    def to_wire(self) -> dict[str, object]:
        return {"blobRefs": list(self.blob_refs), "consumerTask": self.consumer_task, "destinationExecutorId": self.destination_executor_id, "expiresAt": self.expires_at, "exportRequestFullBytesDigest": self.export_request_full_bytes_digest, "exportRequestRef": self.export_request_ref, "handoffId": self.handoff_id, "nonce": self.nonce, "pointerBytesDigest": self.pointer_bytes_digest, "pointerName": self.pointer_name, "policyActivationProofFullBytesDigest": self.policy_activation_proof_full_bytes_digest, "policyActivationProofRef": self.policy_activation_proof_ref, "recordFullBytesDigest": self.record_full_bytes_digest, "recordRef": self.record_ref, "schema": self.schema, "sequence": self.sequence, "sourceExecutorId": self.source_executor_id, "storeNamespace": self.store_namespace, "transportId": self.transport_id}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def digest(self) -> str:
        return _digest(self.canonical_bytes)

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "HandoffTransferManifestV1":
        raw = _strict_object(payload, {"blobRefs", "consumerTask", "destinationExecutorId", "expiresAt", "exportRequestFullBytesDigest", "exportRequestRef", "handoffId", "nonce", "pointerBytesDigest", "pointerName", "policyActivationProofFullBytesDigest", "policyActivationProofRef", "recordFullBytesDigest", "recordRef", "schema", "sequence", "sourceExecutorId", "storeNamespace", "transportId"})
        if not isinstance(raw["blobRefs"], list):
            raise ValueError("transport manifest blobs are invalid")
        return cls(raw["schema"], raw["exportRequestRef"], raw["exportRequestFullBytesDigest"], raw["storeNamespace"], raw["handoffId"], raw["transportId"], raw["consumerTask"], raw["recordRef"], raw["recordFullBytesDigest"], raw["pointerName"], raw["pointerBytesDigest"], raw["sourceExecutorId"], raw["destinationExecutorId"], raw["nonce"], raw["sequence"], raw["expiresAt"], raw["policyActivationProofRef"], raw["policyActivationProofFullBytesDigest"], tuple(raw["blobRefs"]))


def _transport_replay_bytes(manifest: HandoffTransferManifestV1) -> bytes:
    return canonical_json_bytes({"consumerTask": manifest.consumer_task, "destinationExecutorId": manifest.destination_executor_id, "handoffId": manifest.handoff_id, "nonce": manifest.nonce, "schema": "trusted-handoff-transport-replay/v1", "sequence": manifest.sequence, "transportId": manifest.transport_id})


@dataclass(frozen=True, slots=True)
class TransportSealV1:
    schema: str
    store_namespace: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    record_ref: str
    record_full_bytes_digest: str
    pointer_name: str
    pointer_bytes_digest: str
    transfer_manifest_digest: str
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    expires_at: str | None
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None
    signing_key_purpose: str
    signature_domain: str
    signature_algorithm: str
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-handoff-transport-seal/v1" or self.signing_key_purpose != "transport-seal" or self.signature_domain != "ai-video-reverse-engineer/transport-seal/v1" or self.signature_algorithm != "ssh-ed25519":
            raise ValueError("transport seal signing identity is invalid")
        for field in ("record_ref", "record_full_bytes_digest", "pointer_bytes_digest", "transfer_manifest_digest"):
            _require_digest(getattr(self, field), field)
        if self.record_ref != self.record_full_bytes_digest:
            raise ValueError("transport seal record digest is invalid")
        for field in ("handoff_id", "transport_id", "pointer_name", "source_executor_id", "destination_executor_id", "key_id"):
            _require_transport_identity(getattr(self, field), field)
        _require_namespace(self.store_namespace)
        if self.pointer_name != self.handoff_id or not isinstance(self.consumer_task, int) or self.consumer_task <= 0 or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("transport seal binding is invalid")
        _require_nonce(self.nonce)
        _require_expiry(self.expires_at)
        _proof_fields(self.policy_activation_proof_ref, self.policy_activation_proof_full_bytes_digest)
        _hex_signature(self.signature, "transport signature")

    def unsigned_wire(self) -> dict[str, object]:
        wire = self.to_wire()
        del wire["signature"]
        return wire

    def to_wire(self) -> dict[str, object]:
        return {"consumerTask": self.consumer_task, "destinationExecutorId": self.destination_executor_id, "expiresAt": self.expires_at, "handoffId": self.handoff_id, "keyId": self.key_id, "nonce": self.nonce, "pointerBytesDigest": self.pointer_bytes_digest, "pointerName": self.pointer_name, "policyActivationProofFullBytesDigest": self.policy_activation_proof_full_bytes_digest, "policyActivationProofRef": self.policy_activation_proof_ref, "recordFullBytesDigest": self.record_full_bytes_digest, "recordRef": self.record_ref, "schema": self.schema, "sequence": self.sequence, "signature": self.signature, "signatureAlgorithm": self.signature_algorithm, "signatureDomain": self.signature_domain, "signingKeyPurpose": self.signing_key_purpose, "sourceExecutorId": self.source_executor_id, "storeNamespace": self.store_namespace, "transferManifestDigest": self.transfer_manifest_digest, "transportId": self.transport_id}

    @property
    def unsigned_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.unsigned_wire())

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TransportSealV1":
        raw = _strict_object(payload, {"consumerTask", "destinationExecutorId", "expiresAt", "handoffId", "keyId", "nonce", "pointerBytesDigest", "pointerName", "policyActivationProofFullBytesDigest", "policyActivationProofRef", "recordFullBytesDigest", "recordRef", "schema", "sequence", "signature", "signatureAlgorithm", "signatureDomain", "signingKeyPurpose", "sourceExecutorId", "storeNamespace", "transferManifestDigest", "transportId"})
        return cls(raw["schema"], raw["storeNamespace"], raw["handoffId"], raw["transportId"], raw["consumerTask"], raw["recordRef"], raw["recordFullBytesDigest"], raw["pointerName"], raw["pointerBytesDigest"], raw["transferManifestDigest"], raw["sourceExecutorId"], raw["destinationExecutorId"], raw["nonce"], raw["sequence"], raw["expiresAt"], raw["policyActivationProofRef"], raw["policyActivationProofFullBytesDigest"], raw["signingKeyPurpose"], raw["signatureDomain"], raw["signatureAlgorithm"], raw["keyId"], raw["signature"])


@dataclass(frozen=True, slots=True)
class TransportReadyMarkerV1:
    schema: str
    store_namespace: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    transfer_manifest_digest: str
    transport_seal_digest: str
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None

    def __post_init__(self) -> None:
        if self.schema != "trusted-handoff-transport-ready/v1":
            raise ValueError("transport ready schema is invalid")
        for field in ("transfer_manifest_digest", "transport_seal_digest"):
            _require_digest(getattr(self, field), field)
        for field in ("handoff_id", "transport_id", "source_executor_id", "destination_executor_id"):
            _require_transport_identity(getattr(self, field), field)
        _require_namespace(self.store_namespace)
        if not isinstance(self.consumer_task, int) or self.consumer_task <= 0 or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("transport ready binding is invalid")
        _require_nonce(self.nonce)
        _proof_fields(self.policy_activation_proof_ref, self.policy_activation_proof_full_bytes_digest)

    def to_wire(self) -> dict[str, object]:
        return {"consumerTask": self.consumer_task, "destinationExecutorId": self.destination_executor_id, "handoffId": self.handoff_id, "nonce": self.nonce, "policyActivationProofFullBytesDigest": self.policy_activation_proof_full_bytes_digest, "policyActivationProofRef": self.policy_activation_proof_ref, "schema": self.schema, "sequence": self.sequence, "sourceExecutorId": self.source_executor_id, "storeNamespace": self.store_namespace, "transferManifestDigest": self.transfer_manifest_digest, "transportId": self.transport_id, "transportSealDigest": self.transport_seal_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TransportReadyMarkerV1":
        raw = _strict_object(payload, {"consumerTask", "destinationExecutorId", "handoffId", "nonce", "policyActivationProofFullBytesDigest", "policyActivationProofRef", "schema", "sequence", "sourceExecutorId", "storeNamespace", "transferManifestDigest", "transportId", "transportSealDigest"})
        return cls(raw["schema"], raw["storeNamespace"], raw["handoffId"], raw["transportId"], raw["consumerTask"], raw["sourceExecutorId"], raw["destinationExecutorId"], raw["nonce"], raw["sequence"], raw["transferManifestDigest"], raw["transportSealDigest"], raw["policyActivationProofRef"], raw["policyActivationProofFullBytesDigest"])


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

    def publish_policy_local(
        self,
        record: PolicyHandoffRecordV1,
        pointer: PolicyHandoffPointerV1,
        core_request: HandoffExportRequestV1,
        policy_handoff: "Ticket23PolicyHandoffV1",
        activated_request: "ActivatedPolicyExportRequestV1",
        activation_proof_bytes: bytes,
        blobs: tuple[bytes, ...] = (),
    ) -> str:
        """Publish the policy closure before its activated request pointer, no replace."""
        if pointer.handoff_id != record.handoff_id or pointer.record_ref != record.record_digest or pointer.producer_commit != record.producer_commit:
            raise ValueError("policy pointer does not bind record")
        for blob in blobs:
            self.store.put_blob(blob)
        for _, ref in record.bindings:
            self.store.read_blob(ref)
        record_ref = self.store.put_blob(record.canonical_bytes)
        core_ref = self.store.put_blob(core_request.canonical_bytes)
        proof_ref = self.store.put_blob(activation_proof_bytes)
        handoff_ref = self.store.put_blob(policy_handoff.canonical_bytes)
        activated_ref = self.store.put_blob(activated_request.canonical_bytes)
        if (record_ref, core_ref, proof_ref, handoff_ref) != (policy_handoff.core_record_ref, policy_handoff.core_export_request_ref, policy_handoff.policy_activation_proof_ref, activated_request.policy_handoff_ref):
            raise ValueError("policy publication closure refs are mixed")
        self.store.publish_pointer(record.handoff_id, pointer.canonical_bytes)
        self.store.publish_policy_export_request_ref(record.handoff_id, activated_ref)
        return activated_ref

    def publish_export_request(self, request: HandoffExportRequestV1) -> str:
        record, pointer, record_bytes, pointer_bytes = self._closure(self.store, request.record_ref, request.handoff_id)
        if request.record_full_bytes_digest != _digest(record_bytes) or request.pointer_bytes_digest != _digest(pointer_bytes):
            raise ValueError("export request full-byte digests do not match")
        if request.consumer_tasks != record.consumer_tasks or request.binding_set_digest != record.binding_set_digest:
            raise ValueError("export request does not close record consumers or bindings")
        request_ref = self.store.put_blob(request.canonical_bytes)
        self.store.publish_export_request_ref(request.handoff_id, request_ref)
        return request_ref

    def fetch(self, source_store: TrustedHandoffStoreV1, export_request_ref: str, handoff_id: str, *, consumer_task: int, transport_id: str | None = None, expected_destination_executor_id: str | None = None, signing_domains: "VerifiedSigningDomainsV1 | None" = None) -> None:
        if not isinstance(transport_id, str) or not transport_id or not isinstance(expected_destination_executor_id, str) or not expected_destination_executor_id or signing_domains is None:
            raise ValueError("authenticated transport fetch requires destination and signing domains")
        manifest_value, seal_value, ready_value = verify_transport_closure(
            source_store, handoff_id, transport_id, consumer_task,
            expected_destination_executor_id, signing_domains,
        )
        if export_request_ref != manifest_value.export_request_ref:
            raise ValueError("fetch request does not match transport manifest")
        if manifest_value.policy_activation_proof_ref is not None:
            _activated, handoff, _proof, _record, _pointer, _core, refs = verify_policy_transport_source_closure(
                source_store, export_request_ref, handoff_id, consumer_task,
                expected_destination_executor_id, signing_domains,
            )
            for ref in refs:
                self.store.put_blob(source_store.read_blob(ref))
            self.store.publish_pointer(handoff_id, source_store.read_pointer(handoff.core_pointer_name))
            self.store.publish_policy_export_request_ref(handoff_id, export_request_ref)
            self.store.publish_transport_file(handoff_id, transport_id, "manifest.json", manifest_value.canonical_bytes)
            self.store.publish_transport_file(handoff_id, transport_id, "seal.json", seal_value.canonical_bytes)
            self.store.publish_transport_file(handoff_id, transport_id, "ready.json", ready_value.canonical_bytes)
            self.store.publish_transport_replay(manifest_value)
            fetch = canonical_json_bytes({"consumerTask": consumer_task, "destinationExecutorId": expected_destination_executor_id, "handoffId": handoff_id, "pointerBytesDigest": handoff.core_pointer_bytes_digest, "recordRef": handoff.core_record_ref, "requestRef": export_request_ref, "schema": "trusted-handoff-fetch/v1", "transportId": transport_id})
            self.store._publish_file(("fetches", f"{handoff_id}-{consumer_task}.json"), fetch)
            return
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
        self.store.publish_transport_file(handoff_id, transport_id, "manifest.json", manifest_value.canonical_bytes)
        self.store.publish_transport_file(handoff_id, transport_id, "seal.json", seal_value.canonical_bytes)
        self.store.publish_transport_file(handoff_id, transport_id, "ready.json", ready_value.canonical_bytes)
        self.store.publish_transport_replay(manifest_value)
        manifest = canonical_json_bytes({"consumerTask": consumer_task, "destinationExecutorId": expected_destination_executor_id, "handoffId": handoff_id, "pointerBytesDigest": _digest(pointer_bytes), "recordRef": request.record_ref, "requestRef": export_request_ref, "schema": "trusted-handoff-fetch/v1", "transportId": transport_id})
        self.store._publish_file(("fetches", f"{handoff_id}-{consumer_task}.json"), manifest)

    def import_(self, handoff_id: str, *, consumer_task: int, expected_destination_executor_id: str | None = None, signing_domains: "VerifiedSigningDomainsV1 | None" = None) -> None:
        manifest = self._read_fetch_manifest(handoff_id, consumer_task)
        transport_id = manifest["transportId"]
        if not isinstance(transport_id, str) or not isinstance(expected_destination_executor_id, str) or not expected_destination_executor_id or signing_domains is None:
            raise ValueError("authenticated transport import requires destination and signing domains")
        transport, _seal, _ready = verify_transport_closure(
            self.store, handoff_id, transport_id, consumer_task,
            expected_destination_executor_id, signing_domains,
        )
        if (manifest["destinationExecutorId"], manifest["requestRef"], manifest["recordRef"], manifest["pointerBytesDigest"]) != (
            expected_destination_executor_id, transport.export_request_ref, transport.record_ref,
            transport.pointer_bytes_digest,
        ):
            raise ValueError("fetched handoff does not bind the verified transport")
        request_bytes = self.store.read_blob(manifest["requestRef"])
        try:
            request = HandoffExportRequestV1.from_canonical_bytes(request_bytes)
        except ValueError:
            activated = ActivatedPolicyExportRequestV1.from_canonical_bytes(request_bytes)
            if activated.handoff_id != handoff_id or consumer_task not in activated.consumer_tasks:
                raise ValueError("fetched policy request is not authorized")
            handoff = Ticket23PolicyHandoffV1.from_canonical_bytes(self.store.read_blob(activated.policy_handoff_ref))
            record_bytes = self.store.read_blob(handoff.core_record_ref)
            record = PolicyHandoffRecordV1.from_canonical_bytes(record_bytes)
            if handoff.core_record_full_bytes_digest != _digest(record_bytes):
                raise ValueError("fetched policy record is mixed")
        else:
            if request.handoff_id != handoff_id or consumer_task not in request.consumer_tasks:
                raise ValueError("fetched request is not authorized")
            record, _, _, _ = self._closure(self.store, request.record_ref, handoff_id)
        for _, ref in record.bindings:
            self.store.read_blob(ref)
        self.store._publish_file(("imports", f"{handoff_id}-{consumer_task}.json"), canonical_json_bytes({"consumerTask": consumer_task, "destinationExecutorId": manifest["destinationExecutorId"], "handoffId": handoff_id, "requestRef": manifest["requestRef"], "schema": "trusted-handoff-import/v1", "transportId": manifest["transportId"]}))

    def finalize(self, handoff_id: str, *, expected_kind: str, expected_producer_task: int, consumer_task: int, expected_destination_executor_id: str | None = None, signing_domains: "VerifiedSigningDomainsV1 | None" = None) -> None:
        imported = self._read_import_manifest(handoff_id, consumer_task)
        transport_id = imported["transportId"]
        if not isinstance(transport_id, str) or not isinstance(expected_destination_executor_id, str) or signing_domains is None or imported["destinationExecutorId"] != expected_destination_executor_id:
            raise ValueError("not finalized: transport seal and ready marker are required")
        manifest, _seal, ready = verify_transport_closure(self.store, handoff_id, transport_id, consumer_task, expected_destination_executor_id, signing_domains)
        if manifest.policy_activation_proof_ref is None:
            record, _pointer, _record_bytes, _pointer_bytes = self._closure(self.store, manifest.record_ref, handoff_id)
        else:
            _activated, _handoff, _proof, record, _pointer, _core, _refs = verify_policy_transport_source_closure(self.store, manifest.export_request_ref, handoff_id, consumer_task, expected_destination_executor_id, signing_domains)
        if record.kind != expected_kind or record.producer_task != expected_producer_task:
            raise ValueError("finalized handoff kind or producer is invalid")
        final = canonical_json_bytes({"consumerTask": consumer_task, "handoffId": handoff_id, "readyDigest": _digest(ready.canonical_bytes), "schema": "trusted-handoff-finalized/v1", "transportId": transport_id})
        self.store._publish_file(("finalized", f"{handoff_id}-{consumer_task}.json"), final)

    def read(self, handoff_id: str, *, consumer_task: int, field: str, expected_destination_executor_id: str | None = None, signing_domains: "VerifiedSigningDomainsV1 | None" = None) -> str:
        imported = self._read_import_manifest(handoff_id, consumer_task)
        self._read_finalized(handoff_id, consumer_task, imported["transportId"])
        if not isinstance(imported["transportId"], str) or not isinstance(expected_destination_executor_id, str) or signing_domains is None:
            raise ValueError("read requires authenticated transport verification")
        verify_transport_closure(self.store, handoff_id, imported["transportId"], consumer_task, expected_destination_executor_id, signing_domains)
        request_bytes = self.store.read_blob(imported["requestRef"])
        try:
            request = HandoffExportRequestV1.from_canonical_bytes(request_bytes)
            record, _pointer, _record_bytes, _pointer_bytes = self._closure(self.store, request.record_ref, handoff_id)
        except ValueError:
            _activated, _handoff, _proof, record, _pointer, _core, _refs = verify_policy_transport_source_closure(self.store, imported["requestRef"], handoff_id, consumer_task, expected_destination_executor_id, signing_domains)
        if field == "producerCommit.hex":
            return record.producer_commit.hex
        if field == "producerCommit.algorithm":
            return record.producer_commit.algorithm
        raise ValueError("read field is invalid")

    def read_product_subject(self, handoff_id: str, *, consumer_task: int, expected_destination_executor_id: str | None = None, signing_domains: "VerifiedSigningDomainsV1 | None" = None) -> ProductGitObjectIdV1:
        imported = self._read_import_manifest(handoff_id, consumer_task)
        self._read_finalized(handoff_id, consumer_task, imported["transportId"])
        if not isinstance(imported["transportId"], str) or not isinstance(expected_destination_executor_id, str) or signing_domains is None:
            raise ValueError("read requires authenticated transport verification")
        verify_transport_closure(self.store, handoff_id, imported["transportId"], consumer_task, expected_destination_executor_id, signing_domains)
        request = HandoffExportRequestV1.from_canonical_bytes(self.store.read_blob(imported["requestRef"]))
        record, _pointer, _record_bytes, _pointer_bytes = self._closure(self.store, request.record_ref, handoff_id)
        source_refs = [digest for name, digest in record.bindings if name == "product-source"]
        if len(source_refs) != 1:
            raise ValueError("product source binding is missing")
        source = ProductRepositorySourceV1.from_canonical_bytes(self.store.read_blob(source_refs[0]))
        if source.subject_commit != record.producer_commit:
            raise ValueError("product subject does not match finalized record")
        return source.subject_commit

    def _closure(self, store: TrustedHandoffStoreV1, record_ref: str, handoff_id: str) -> tuple[TrustedHandoffRecordV1, HandoffPointerV1, bytes, bytes]:
        record_bytes = store.read_blob(record_ref)
        record = TrustedHandoffRecordV1.from_canonical_bytes(record_bytes)
        pointer_bytes = store.read_pointer(handoff_id)
        pointer = HandoffPointerV1.from_canonical_bytes(pointer_bytes)
        if record.handoff_id != handoff_id or pointer.handoff_id != handoff_id or pointer.record_ref != record_ref or pointer.record_digest != _digest(record_bytes) or pointer.producer_task != record.producer_task or pointer.producer_commit != record.producer_commit:
            raise ValueError("record and pointer closure is mixed")
        return record, pointer, record_bytes, pointer_bytes

    def _read_fetch_manifest(self, handoff_id: str, consumer_task: int) -> dict[str, object]:
        try:
            payload = self.store._read_file(("fetches", f"{handoff_id}-{consumer_task}.json"))
        except ValueError as error:
            raise ValueError("fetched handoff is missing") from error
        raw = _strict_object(payload, {"consumerTask", "destinationExecutorId", "handoffId", "pointerBytesDigest", "recordRef", "requestRef", "schema", "transportId"})
        if raw["schema"] != "trusted-handoff-fetch/v1" or raw["handoffId"] != handoff_id or raw["consumerTask"] != consumer_task:
            raise ValueError("fetched handoff identity is invalid")
        _require_digest(raw["requestRef"], "requestRef")
        _require_digest(raw["recordRef"], "recordRef")
        _require_digest(raw["pointerBytesDigest"], "pointerBytesDigest")
        if raw["transportId"] is not None and not isinstance(raw["transportId"], str):
            raise ValueError("fetched transport identity is invalid")
        if raw["destinationExecutorId"] is not None and not isinstance(raw["destinationExecutorId"], str):
            raise ValueError("fetched destination identity is invalid")
        return raw

    def _read_import_manifest(self, handoff_id: str, consumer_task: int) -> dict[str, object]:
        try:
            payload = self.store._read_file(("imports", f"{handoff_id}-{consumer_task}.json"))
        except ValueError as error:
            raise ValueError("imported handoff is missing") from error
        raw = _strict_object(payload, {"consumerTask", "destinationExecutorId", "handoffId", "requestRef", "schema", "transportId"})
        if raw["schema"] != "trusted-handoff-import/v1" or raw["handoffId"] != handoff_id or raw["consumerTask"] != consumer_task:
            raise ValueError("imported handoff identity is invalid")
        _require_digest(raw["requestRef"], "requestRef")
        return raw

    def _read_finalized(self, handoff_id: str, consumer_task: int, transport_id: object) -> None:
        if not isinstance(transport_id, str):
            raise ValueError("not finalized: transport identity is missing")
        try:
            payload = self.store._read_file(("finalized", f"{handoff_id}-{consumer_task}.json"))
        except ValueError as error:
            raise ValueError("not finalized: finalization marker is missing") from error
        raw = _strict_object(payload, {"consumerTask", "handoffId", "readyDigest", "schema", "transportId"})
        if raw["schema"] != "trusted-handoff-finalized/v1" or raw["handoffId"] != handoff_id or raw["consumerTask"] != consumer_task or raw["transportId"] != transport_id:
            raise ValueError("not finalized: marker identity is invalid")
        _require_digest(raw["readyDigest"], "readyDigest")
        if _digest(self.store.read_transport_file(handoff_id, transport_id, "ready.json")) != raw["readyDigest"]:
            raise ValueError("not finalized: ready marker changed")


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


def _registrations(value: object, *, purpose: str, domain: str, label: str, allow_empty: bool = False) -> tuple[SigningKeyRegistrationV1, ...]:
    registry = _registry_object(value, label)
    keys = registry["keys"]
    if not isinstance(keys, list) or (not keys and not allow_empty):
        raise ValueError(f"{label} must contain keys")
    parsed = tuple(_registration(key, expected_purpose=purpose, expected_domain=domain) for key in keys)
    if tuple(sorted(key.key_id.encode("utf-8") for key in parsed)) != tuple(key.key_id.encode("utf-8") for key in parsed) or len({key.key_id for key in parsed}) != len(parsed):
        raise ValueError(f"{label} key IDs must be unique and ordered")
    return parsed


@dataclass(frozen=True, slots=True)
class VerifiedSigningDomainsV1:
    transport_keys: tuple[SigningKeyRegistrationV1, ...]
    activation_keys: tuple[SigningKeyRegistrationV1, ...]
    broker_keys: tuple[SigningKeyRegistrationV1, ...]
    canonical_digest: str


def _strict_broker_governance(
    registry_wire: object,
    revocations_wire: object,
    allowed_signers_wire: object,
) -> tuple[SigningKeyRegistrationV1, ...]:
    if not isinstance(registry_wire, bytes) or not isinstance(revocations_wire, bytes):
        raise ValueError("broker-auth governance must use canonical bytes")
    registry = RegistrationBrokerAuthRegistryV1.from_canonical_bytes(registry_wire)
    revocations = RegistrationBrokerAuthRevocationsV1.from_canonical_bytes(revocations_wire)
    if revocations.registry_digest != registry.canonical_digest:
        raise ValueError("broker-auth revocations do not bind the registry")
    allowed = _registrations(
        allowed_signers_wire,
        purpose=_BROKER_PURPOSE,
        domain=_BROKER_DOMAIN,
        label="broker-auth allowed signers",
    )
    if {item.key_id for item in allowed} != {item.key_id for item in registry.keys}:
        raise ValueError("broker-auth allowed signers do not close the registry")
    if {entry.key_id for entry in revocations.entries} - {item.key_id for item in registry.keys}:
        raise ValueError("broker-auth revocations contain an unknown key")
    verify_registration_broker_governance(
        registry,
        revocations,
        {item.key_id: item.resolved_public_key for item in allowed},
    )
    verified: list[SigningKeyRegistrationV1] = []
    for registered in registry.keys:
        signer = next(item for item in allowed if item.key_id == registered.key_id)
        if (
            signer.raw_public_key_bytes_digest != registered.public_key_digest
            or signer.raw_public_key_fingerprint != registered.public_key_fingerprint
            or signer.wrapper_identity_digest != registered.certificate_wrapper_digest
            or signer.resolved_public_key_bytes_digest != registered.resolved_leaf_public_key_digest
            or signer.resolved_public_key_fingerprint != registered.resolved_leaf_public_key_fingerprint
            or signer.registry_entry_bytes_digest != registered.allowed_signers_entry_digest
        ):
            raise ValueError("broker-auth signer does not match strict governance")
        verified.append(signer)
    return tuple(verified)


def load_disjoint_signing_domains(transport_registry: object, activation_registry: object, broker_auth_registry: object, broker_auth_revocations: object, broker_auth_allowed_signers: object) -> VerifiedSigningDomainsV1:
    transport = _registrations(transport_registry, purpose=_TRANSPORT_PURPOSE, domain=_TRANSPORT_DOMAIN, label="transport registry")
    activation = _registrations(activation_registry, purpose=_ACTIVATION_PURPOSE, domain=_ACTIVATION_DOMAIN, label="activation registry")
    broker = _strict_broker_governance(
        broker_auth_registry,
        broker_auth_revocations,
        broker_auth_allowed_signers,
    )
    domains = (transport, activation, broker)
    for left_index, left in enumerate(domains):
        for right in domains[left_index + 1:]:
            for attribute in ("key_id", "raw_public_key_bytes_digest", "raw_public_key_fingerprint", "wrapper_identity_digest", "resolved_public_key_bytes_digest", "resolved_public_key_fingerprint"):
                if {getattr(key, attribute) for key in left} & {getattr(key, attribute) for key in right}:
                    raise ValueError(f"signing domains collide on {attribute}")
    digest_wire = {"activation": [key.registry_entry_bytes_digest for key in activation], "broker": [key.registry_entry_bytes_digest for key in broker], "transport": [key.registry_entry_bytes_digest for key in transport]}
    return VerifiedSigningDomainsV1(transport, activation, broker, _digest_bytes(canonical_json_bytes(digest_wire)))


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


def verify_transport_closure(store: TrustedHandoffStoreV1, handoff_id: str, transport_id: str, consumer_task: int, expected_destination_executor_id: str, signing_domains: VerifiedSigningDomainsV1) -> tuple[HandoffTransferManifestV1, TransportSealV1, TransportReadyMarkerV1]:
    """Recompute the ready/seal/manifest closure before any consumer-visible use."""
    if not isinstance(signing_domains, VerifiedSigningDomainsV1):
        raise ValueError("transport signing domains are invalid")
    ready_bytes = store.read_transport_file(handoff_id, transport_id, "ready.json")
    ready = TransportReadyMarkerV1.from_canonical_bytes(ready_bytes)
    manifest_bytes = store.read_transport_file(handoff_id, transport_id, "manifest.json")
    manifest = HandoffTransferManifestV1.from_canonical_bytes(manifest_bytes)
    seal_bytes = store.read_transport_file(handoff_id, transport_id, "seal.json")
    seal = TransportSealV1.from_canonical_bytes(seal_bytes)
    if ready.handoff_id != handoff_id or ready.transport_id != transport_id or ready.consumer_task != consumer_task or ready.destination_executor_id != expected_destination_executor_id:
        raise ValueError("transport ready identity is invalid")
    if ready.transfer_manifest_digest != _digest(manifest_bytes) or ready.transport_seal_digest != _digest(seal_bytes):
        raise ValueError("transport ready digests are invalid")
    store.verify_transport_replay(manifest)
    for attribute in ("store_namespace", "handoff_id", "transport_id", "consumer_task", "source_executor_id", "destination_executor_id", "nonce", "sequence", "policy_activation_proof_ref", "policy_activation_proof_full_bytes_digest"):
        if getattr(ready, attribute) != getattr(manifest, attribute) or getattr(seal, attribute) != getattr(manifest, attribute):
            raise ValueError("transport closure bindings are mixed")
    if seal.transfer_manifest_digest != _digest(manifest_bytes) or seal.record_ref != manifest.record_ref or seal.record_full_bytes_digest != manifest.record_full_bytes_digest or seal.pointer_name != manifest.pointer_name or seal.pointer_bytes_digest != manifest.pointer_bytes_digest:
        raise ValueError("transport seal manifest binding is invalid")
    registration = next((key for key in signing_domains.transport_keys if key.key_id == seal.key_id), None)
    if registration is None or registration.purpose != seal.signing_key_purpose or registration.signature_domain != seal.signature_domain:
        raise ValueError("transport signing key is not registered for this purpose")
    message = seal.signature_domain.encode("utf-8") + b"\0" + seal.unsigned_canonical_bytes
    if not _ed25519_verify(registration.resolved_public_key, message, _hex_signature(seal.signature, "transport signature")):
        raise ValueError("transport seal signature is invalid")
    if manifest.expires_at is not None and datetime.fromisoformat(manifest.expires_at.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise ValueError("transport has expired")
    if (manifest.policy_activation_proof_ref is None) != (manifest.policy_activation_proof_full_bytes_digest is None):
        raise ValueError("transport policy proof fields are incomplete")
    if manifest.policy_activation_proof_ref is not None:
        activated, handoff, _proof, record, _pointer, _core_request, refs = verify_policy_transport_source_closure(
            store, manifest.export_request_ref, handoff_id, consumer_task, expected_destination_executor_id, signing_domains,
        )
        if (manifest.export_request_full_bytes_digest, manifest.record_ref, manifest.record_full_bytes_digest, manifest.pointer_bytes_digest, manifest.policy_activation_proof_ref, manifest.policy_activation_proof_full_bytes_digest, manifest.blob_refs) != (_digest(store.read_blob(manifest.export_request_ref)), handoff.core_record_ref, _digest(store.read_blob(handoff.core_record_ref)), _digest(store.read_pointer(handoff.core_pointer_name)), activated.policy_activation_proof_ref, activated.policy_activation_proof_full_bytes_digest, refs):
            raise ValueError("policy transport manifest closure is invalid")
        return manifest, seal, ready
    request_bytes = store.read_blob(manifest.export_request_ref)
    request = HandoffExportRequestV1.from_canonical_bytes(request_bytes)
    if _digest(request_bytes) != manifest.export_request_full_bytes_digest or request.handoff_id != handoff_id or consumer_task not in request.consumer_tasks or request.record_ref != manifest.record_ref:
        raise ValueError("transport export request closure is invalid")
    record, _pointer, record_bytes, pointer_bytes = HandoffRegistryV1(store)._closure(store, manifest.record_ref, handoff_id)
    if _digest(record_bytes) != manifest.record_full_bytes_digest or _digest(pointer_bytes) != manifest.pointer_bytes_digest or tuple(sorted(ref for _, ref in record.bindings)) != manifest.blob_refs:
        raise ValueError("transport record closure is invalid")
    for ref in manifest.blob_refs:
        store.read_blob(ref)
    return manifest, seal, ready


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


def verify_policy_transport_source_closure(
    store: TrustedHandoffStoreV1,
    activated_request_ref: str,
    handoff_id: str,
    consumer_task: int,
    destination_executor_id: str,
    signing_domains: VerifiedSigningDomainsV1,
) -> tuple[ActivatedPolicyExportRequestV1, Ticket23PolicyHandoffV1, PolicyActivationProofV1, PolicyHandoffRecordV1, PolicyHandoffPointerV1, HandoffExportRequestV1, tuple[str, ...]]:
    """Load the durable policy closure and bind it to one transport consumer."""
    if handoff_id != "ticket-23-policy-v1" or store.read_policy_export_request_ref(handoff_id) != activated_request_ref:
        raise ValueError("policy activated export request is not the source-store activation point")
    activated_bytes = store.read_blob(activated_request_ref)
    activated = ActivatedPolicyExportRequestV1.from_canonical_bytes(activated_bytes)
    if activated.handoff_id != handoff_id or consumer_task not in activated.consumer_tasks or activated.policy_handoff_ref != activated.policy_handoff_full_bytes_digest or activated.core_export_request_ref != activated.core_export_request_full_bytes_digest:
        raise ValueError("policy activated export request closure is invalid")
    handoff_bytes = store.read_blob(activated.policy_handoff_ref)
    handoff = Ticket23PolicyHandoffV1.from_canonical_bytes(handoff_bytes)
    proof_bytes = store.read_blob(activated.policy_activation_proof_ref)
    proof = PolicyActivationProofV1.from_canonical_bytes(proof_bytes)
    if _digest(handoff_bytes) != activated.policy_handoff_full_bytes_digest or _digest(proof_bytes) != activated.policy_activation_proof_full_bytes_digest:
        raise ValueError("policy activated closure bytes are mixed")
    if (handoff.core_export_request_ref, handoff.core_export_request_full_bytes_digest, handoff.policy_activation_proof_ref, handoff.policy_activation_proof_full_bytes_digest) != (activated.core_export_request_ref, activated.core_export_request_full_bytes_digest, activated.policy_activation_proof_ref, activated.policy_activation_proof_full_bytes_digest):
        raise ValueError("policy handoff and activated request are mixed")
    record_bytes = store.read_blob(handoff.core_record_ref)
    record = PolicyHandoffRecordV1.from_canonical_bytes(record_bytes)
    pointer_bytes = store.read_pointer(handoff.core_pointer_name)
    pointer = PolicyHandoffPointerV1.from_canonical_bytes(pointer_bytes)
    core_request_bytes = store.read_blob(handoff.core_export_request_ref)
    core_request = HandoffExportRequestV1.from_canonical_bytes(core_request_bytes)
    if (record.handoff_id != handoff_id or pointer.handoff_id != handoff_id or pointer.record_ref != handoff.core_record_ref or pointer.record_digest != _digest(record_bytes) or pointer.producer_commit != record.producer_commit or _digest(record_bytes) != handoff.core_record_full_bytes_digest or _digest(pointer_bytes) != handoff.core_pointer_bytes_digest):
        raise ValueError("policy record and pointer closure is mixed")
    if (core_request.handoff_id != handoff_id or core_request.record_ref != handoff.core_record_ref or core_request.record_full_bytes_digest != _digest(record_bytes) or core_request.pointer_bytes_digest != _digest(pointer_bytes) or core_request.consumer_tasks != record.consumer_tasks or core_request.binding_set_digest != record.binding_set_digest or consumer_task not in core_request.consumer_tasks):
        raise ValueError("policy core export request closure is invalid")
    verified = verify_policy_activation_proof(proof_bytes, signing_domains, {
        "consumerTask": consumer_task,
        "destinationExecutorId": destination_executor_id,
        "policyActivationProofRef": activated.policy_activation_proof_ref,
        "policyActivationProofFullBytesDigest": activated.policy_activation_proof_full_bytes_digest,
    })
    if (verified.policy_commit != record.producer_commit or proof.policy_record_ref != handoff.core_record_ref or proof.policy_record_full_bytes_digest != _digest(record_bytes) or proof.policy_pointer_name != handoff.core_pointer_name or proof.policy_pointer_bytes_digest != _digest(pointer_bytes) or proof.policy_core_export_request_ref != handoff.core_export_request_ref or proof.policy_core_export_request_full_bytes_digest != _digest(core_request_bytes)):
        raise ValueError("policy activation proof does not bind the durable core closure")
    refs = tuple(sorted({activated_request_ref, activated.policy_handoff_ref, activated.policy_activation_proof_ref, handoff.core_export_request_ref, handoff.core_record_ref, *(ref for _, ref in record.bindings)}))
    if any(store.read_blob(ref) is None for ref in refs):
        raise ValueError("policy closure blob is missing")
    return activated, handoff, proof, record, pointer, core_request, refs


def verify_registration_broker_startup_challenge(*_args: object, **_kwargs: object) -> None:
    """Slice E owns broker-auth slots; accepting here would create a bypass."""
    raise ValueError("registration broker startup challenge is unavailable until broker-auth is installed")
