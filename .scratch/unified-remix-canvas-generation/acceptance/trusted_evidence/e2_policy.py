"""Strict, immutable v2 replay-policy primitives.

This module intentionally owns only portable codecs.  Linux broker and
production-runtime capabilities are introduced by their dedicated tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]+$")


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")
    return value


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{field} must be a non-empty NFC string")
    return value


def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def canonical_json_bytes(value: object) -> bytes:
    """Encode a JSON value exactly once, refusing non-JSON values."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("value is not canonical JSON") from error


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _wire(value: object) -> object:
    if isinstance(value, (NamedDigest, GitObjectIdV1, ProductGitObjectIdV1)):
        return value.to_wire()
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    if isinstance(value, list):
        return [_wire(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _wire(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class NamedDigest:
    name: str
    digest: str

    def __post_init__(self) -> None:
        name = _require_text(self.name, "name")
        if any(unicodedata.category(character).startswith("C") for character in name):
            raise ValueError("name must not contain control characters")
        _require_digest(self.digest, "digest")

    def to_wire(self) -> dict[str, str]:
        return {"digest": self.digest, "name": self.name}


@dataclass(frozen=True, slots=True)
class GitObjectIdV1:
    algorithm: str
    hex: str

    def __post_init__(self) -> None:
        sizes = {"sha1": 40, "sha256": 64}
        if self.algorithm not in sizes or not isinstance(self.hex, str) or len(self.hex) != sizes[self.algorithm] or not _HEX.fullmatch(self.hex):
            raise ValueError("Git object ID algorithm and lower-case hex length must match")

    def to_wire(self) -> dict[str, str]:
        return {"algorithm": self.algorithm, "hex": self.hex}


@dataclass(frozen=True, slots=True)
class ProductGitObjectIdV1:
    algorithm: str
    hex: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha1" or not isinstance(self.hex, str) or len(self.hex) != 40 or not _HEX.fullmatch(self.hex):
            raise ValueError("product Git object IDs must be sha1 lower-case 40-hex")

    def to_wire(self) -> dict[str, str]:
        return {"algorithm": self.algorithm, "hex": self.hex}


def _normalise_named_digests(value: object, field: str, *, required: bool) -> tuple[NamedDigest, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ValueError(f"{field} must be a tuple of NamedDigest values")
    items = tuple(value)
    if not items and required:
        raise ValueError(f"{field} must not be empty")
    if not all(isinstance(item, NamedDigest) for item in items):
        raise ValueError(f"{field} must contain NamedDigest values")
    ordered = tuple(sorted(items, key=lambda item: (item.name.encode("utf-8"), item.digest.encode("ascii"))))
    if len({item.name for item in ordered}) != len(ordered):
        raise ValueError(f"{field} must not contain duplicate names")
    return ordered


@dataclass(frozen=True, slots=True)
class ReplayPolicyBindingV1:
    registry_digest: str
    suite_digests: tuple[NamedDigest, ...]
    profile_digests: tuple[NamedDigest, ...]
    revocation_digest: str
    reviewer_policy_digest: str
    toolchain_digests: tuple[NamedDigest, ...]
    dependency_digests: tuple[NamedDigest, ...]
    product_source_digests: tuple[NamedDigest, ...]
    containment_policy_digests: tuple[NamedDigest, ...]

    def __post_init__(self) -> None:
        for field in ("registry_digest", "revocation_digest", "reviewer_policy_digest"):
            _require_digest(getattr(self, field), field)
        for field, required in (
            ("suite_digests", True),
            ("profile_digests", True),
            ("toolchain_digests", True),
            ("dependency_digests", False),
            ("product_source_digests", True),
            ("containment_policy_digests", True),
        ):
            object.__setattr__(self, field, _normalise_named_digests(getattr(self, field), field, required=required))

    def to_wire(self) -> dict[str, object]:
        return {
            "containmentPolicyDigests": _wire(self.containment_policy_digests),
            "dependencyDigests": _wire(self.dependency_digests),
            "productSourceDigests": _wire(self.product_source_digests),
            "profileDigests": _wire(self.profile_digests),
            "registryDigest": self.registry_digest,
            "reviewerPolicyDigest": self.reviewer_policy_digest,
            "revocationDigest": self.revocation_digest,
            "suiteDigests": _wire(self.suite_digests),
            "toolchainDigests": _wire(self.toolchain_digests),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes).hexdigest()

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ReplayPolicyBindingV1":
        # v1 has no compatible upgrade path; v2 is constructed only from the strict wire form.
        raise ValueError("v1 replay policy bindings are not upgraded")


@dataclass(frozen=True, slots=True)
class AcceptanceOperationSnapshotV1:
    catalog_digest: str
    contract_digest: str
    replay_policy_binding_digest: str

    def __post_init__(self) -> None:
        _require_digest(self.catalog_digest, "catalog_digest")
        _require_digest(self.contract_digest, "contract_digest")
        _require_digest(self.replay_policy_binding_digest, "replay_policy_binding_digest")

    def to_wire(self) -> dict[str, str]:
        return {
            "catalogDigest": self.catalog_digest,
            "contractDigest": self.contract_digest,
            "replayPolicyBindingDigest": self.replay_policy_binding_digest,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes).hexdigest()
