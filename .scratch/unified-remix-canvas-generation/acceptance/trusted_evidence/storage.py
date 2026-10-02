"""Immutable evidence objects and append-only catalog persistence."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .models import EvidencePointer
from .registry import PolicyError


_ARTIFACT_REF = re.compile(r"^sha256:([0-9a-f]{64})$")
_CATALOG_NAME = "evidence-catalog.json"
_LOCK_NAME = ".evidence-catalog.lock"
_thread_locks: dict[Path, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def canonical_json_bytes(value: object) -> bytes:
    """Return the single canonical JSON representation used for receipts."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PolicyError("POINTER_INVALID", "value is not canonical JSON") from error


def _digest_ref(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _digest_from_ref(ref: str) -> str:
    match = _ARTIFACT_REF.fullmatch(ref)
    if match is None:
        raise PolicyError("POINTER_INVALID", "artifact reference must be sha256:<64-hex>")
    return match.group(1)


def _root(root: Path) -> Path:
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise PolicyError("SOURCE_REF_UNREACHABLE", "acceptance root does not exist")
    return resolved


def _inside_root(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PolicyError("POINTER_INVALID", "evidence path escapes acceptance root") from error
    return resolved


def _read_bytes_no_symlink(path: Path, missing_code: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as error:
        raise PolicyError(missing_code, f"missing file: {path.name}") from error
    except OSError as error:
        raise PolicyError("POINTER_INVALID", f"unsafe file: {path.name}") from error
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


class ContentAddressedStore:
    """Store opaque evidence payloads under their SHA-256-derived path."""

    def __init__(self, root: Path) -> None:
        self.root = _root(root)

    def _path(self, ref: str) -> Path:
        digest = _digest_from_ref(ref)
        path = self.root / "evidence" / "sha256" / digest[:2] / digest
        _inside_root(self.root, path.parent)
        return path

    def put(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise PolicyError("POINTER_INVALID", "evidence payload must be bytes")
        ref = _digest_ref(payload)
        path = self._path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        _inside_root(self.root, path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = self.read(ref)
            if existing != payload:
                raise PolicyError("DIGEST_MISMATCH", "existing object bytes do not match its digest")
            return ref
        except OSError as error:
            raise PolicyError("POINTER_INVALID", "cannot safely create evidence object") from error
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return ref

    def read(self, ref: str) -> bytes:
        payload = _read_bytes_no_symlink(self._path(ref), "ARTIFACT_MISSING")
        if _digest_ref(payload) != ref:
            raise PolicyError("DIGEST_MISMATCH", "evidence object does not match its digest")
        return payload


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    version: int
    pointers: tuple[EvidencePointer, ...]
    content_digest: str


def _pointer_dict(pointer: EvidencePointer) -> dict[str, str]:
    return {
        "id": pointer.id,
        "profileId": pointer.profile_id,
        "verifierId": pointer.verifier_id,
        "subjectRef": pointer.subject_ref,
        "artifactRef": pointer.artifact_ref,
        "registeredAt": pointer.registered_at,
    }


class CatalogRepository:
    """Catalog writer with a root-local exclusive lock and content CAS."""

    def __init__(self, root: Path) -> None:
        self.root = _root(root)
        self.path = self.root / _CATALOG_NAME
        self.lock_path = self.root / _LOCK_NAME

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        with _thread_locks_guard:
            thread_lock = _thread_locks.setdefault(self.root, threading.Lock())
        with thread_lock:
            with self.lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_raw(self) -> bytes:
        return _read_bytes_no_symlink(self.path, "SOURCE_REF_UNREACHABLE")

    def _snapshot(self, raw: bytes) -> CatalogSnapshot:
        try:
            value = json.loads(raw, object_pairs_hook=_without_duplicate_keys)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise PolicyError("POINTER_INVALID", "invalid evidence catalog JSON") from error
        if not isinstance(value, Mapping) or set(value) != {"version", "evidence"}:
            raise PolicyError("POINTER_INVALID", "invalid evidence catalog schema")
        version = value["version"]
        evidence = value["evidence"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise PolicyError("POINTER_INVALID", "evidence catalog version must be an integer")
        if version == 1:
            if evidence != []:
                raise PolicyError("POINTER_INVALID", "v1 catalog must be empty before migration")
            return CatalogSnapshot(1, (), _digest_ref(raw))
        if version != 2 or not isinstance(evidence, list):
            raise PolicyError("POINTER_INVALID", "unsupported evidence catalog version")
        try:
            pointers = tuple(EvidencePointer.from_dict(entry) for entry in evidence)
        except (TypeError, ValueError) as error:
            raise PolicyError("POINTER_INVALID", "invalid catalog pointer") from error
        if len({pointer.id for pointer in pointers}) != len(pointers):
            raise PolicyError("POINTER_INVALID", "duplicate catalog pointer id")
        return CatalogSnapshot(2, pointers, _digest_ref(raw))

    def read(self) -> CatalogSnapshot:
        return self._snapshot(self._read_raw())

    def _replace_if_current(self, expected_digest: str, replacement: bytes) -> None:
        current = self._read_raw()
        if _digest_ref(current) != expected_digest:
            raise PolicyError("ACCEPTANCE_STATE_CHANGED", "evidence catalog changed concurrently")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".evidence-catalog-", dir=self.root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(replacement)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def migrate_empty_v1_for_write(self) -> CatalogSnapshot:
        """Explicitly migrate only the known empty v1 catalog before a write."""
        with self._exclusive_lock():
            raw = self._read_raw()
            snapshot = self._snapshot(raw)
            if snapshot.version == 2:
                return snapshot
            replacement = canonical_json_bytes({"version": 2, "evidence": []})
            self._replace_if_current(snapshot.content_digest, replacement)
            return self._snapshot(replacement)

    def append(self, pointer: EvidencePointer) -> CatalogSnapshot:
        if not isinstance(pointer, EvidencePointer):
            raise PolicyError("POINTER_INVALID", "catalog requires an EvidencePointer")
        with self._exclusive_lock():
            raw = self._read_raw()
            snapshot = self._snapshot(raw)
            if snapshot.version != 2:
                raise PolicyError("POINTER_INVALID", "catalog must be explicitly migrated before append")
            existing = next((item for item in snapshot.pointers if item.id == pointer.id), None)
            if existing is not None:
                if existing != pointer:
                    raise PolicyError("POINTER_INVALID", "catalog pointer id cannot be overwritten")
                return snapshot
            replacement = canonical_json_bytes(
                {
                    "version": 2,
                    "evidence": [*(_pointer_dict(item) for item in snapshot.pointers), _pointer_dict(pointer)],
                }
            )
            self._replace_if_current(snapshot.content_digest, replacement)
            return self._snapshot(replacement)


def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("POINTER_INVALID", f"duplicate JSON key: {key}")
        result[key] = value
    return result
