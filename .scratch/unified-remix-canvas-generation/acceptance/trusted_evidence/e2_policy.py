"""Strict, immutable v2 replay-policy primitives.

This module intentionally owns only portable codecs.  Linux broker and
production-runtime capabilities are introduced by their dedicated tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import stat
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


# D.1 phase-aware output and durable pre-state codecs.  These remain pure
# records: authorization writes belong to the later broker slice.
_PHASES = {28: "task28-register", 29: "task29-promote"}
_DIRTY_STATUSES = {"added", "modified", "untracked"}
_OUTPUT_STATUSES = {"unchanged", "modified", "added", "untracked"}
_SELF_DIGEST = "canonicalDigest"


def _strict_object(payload: bytes, fields: set[str], label: str) -> dict[str, object]:
    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} JSON is invalid") from error
    if not isinstance(raw, dict) or set(raw) != fields or canonical_json_bytes(raw) != payload:
        raise ValueError(f"{label} JSON is not canonical")
    return raw


def _int(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _relative_path(value: object, field: str) -> str:
    text = _require_text(value, field)
    if text.startswith("/") or "\\" in text or any(part in {"", ".", ".."} for part in text.split("/")):
        raise ValueError(f"{field} must be a normalized repository-relative path")
    return text


def _candidate_ref(value: object, field: str = "candidate_ref") -> str:
    return _require_digest(value, field)


def _oid(raw: object, field: str) -> GitObjectIdV1:
    if not isinstance(raw, dict) or set(raw) != {"algorithm", "hex"}:
        raise ValueError(f"{field} must be a Git object ID")
    return GitObjectIdV1(raw["algorithm"], raw["hex"])


def _ordered(values: object, key, field: str, *, count: int | None = None) -> tuple[object, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
        raise ValueError(f"{field} must be a tuple")
    result = tuple(values)
    if count is not None and len(result) != count:
        raise ValueError(f"{field} must contain exactly {count} entries")
    try:
        keys = tuple(key(item) for item in result)
    except (AttributeError, TypeError) as error:
        raise ValueError(f"{field} entries are invalid") from error
    if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
        raise ValueError(f"{field} must be uniquely canonically ordered")
    return result


def _self_digest(unsigned_wire: dict[str, object]) -> str:
    return canonical_digest(unsigned_wire)


class _SelfDigestRecord:
    def _unsigned_wire(self) -> dict[str, object]:
        raise NotImplementedError

    @property
    def canonical_digest(self) -> str:
        return _self_digest(self._unsigned_wire())

    def to_wire(self) -> dict[str, object]:
        return {**self._unsigned_wire(), _SELF_DIGEST: self.canonical_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())


@dataclass(frozen=True, slots=True)
class SourceDirtyAllowanceV1:
    repository_relative_path: str
    expected_status: str
    expected_content_digest: str

    def __post_init__(self) -> None:
        _relative_path(self.repository_relative_path, "repository_relative_path")
        if not isinstance(self.expected_status, str) or self.expected_status not in _DIRTY_STATUSES:
            raise ValueError("source dirty status must be added, modified, or untracked")
        _require_digest(self.expected_content_digest, "expected_content_digest")

    def to_wire(self) -> dict[str, object]:
        return {"expectedContentDigest": self.expected_content_digest, "expectedStatus": self.expected_status, "repositoryRelativePath": self.repository_relative_path}

    @classmethod
    def _from_wire(cls, raw: object) -> "SourceDirtyAllowanceV1":
        if not isinstance(raw, dict) or set(raw) != {"expectedContentDigest", "expectedStatus", "repositoryRelativePath"}:
            raise ValueError("source dirty allowance is invalid")
        return cls(raw["repositoryRelativePath"], raw["expectedStatus"], raw["expectedContentDigest"])


@dataclass(frozen=True, slots=True)
class ExpectedTaskOutputV1:
    status: str
    byte_length: int
    content_digest: str
    content_source_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or self.status not in _OUTPUT_STATUSES:
            raise ValueError("output status is invalid")
        _int(self.byte_length, "byte_length")
        _require_digest(self.content_digest, "content_digest")
        _candidate_ref(self.content_source_ref, "content_source_ref")

    def to_wire(self) -> dict[str, object]:
        return {"byteLength": self.byte_length, "contentDigest": self.content_digest, "contentSourceRef": self.content_source_ref, "status": self.status}

    @classmethod
    def _from_wire(cls, raw: object) -> "ExpectedTaskOutputV1":
        if not isinstance(raw, dict) or set(raw) != {"byteLength", "contentDigest", "contentSourceRef", "status"}:
            raise ValueError("expected task output is invalid")
        return cls(raw["status"], raw["byteLength"], raw["contentDigest"], raw["contentSourceRef"])


@dataclass(frozen=True, slots=True)
class StagedCandidateIdentityV1:
    candidate_ref: str
    device: int
    inode: int
    byte_length: int
    content_digest: str

    def __post_init__(self) -> None:
        _candidate_ref(self.candidate_ref)
        _int(self.device, "device")
        _int(self.inode, "inode")
        _int(self.byte_length, "byte_length")
        _require_digest(self.content_digest, "content_digest")
        if self.candidate_ref != self.content_digest:
            raise ValueError("candidate ref must bind staged content digest")

    def to_wire(self) -> dict[str, object]:
        return {"byteLength": self.byte_length, "candidateRef": self.candidate_ref, "contentDigest": self.content_digest, "device": self.device, "inode": self.inode}

    @classmethod
    def _from_wire(cls, raw: object) -> "StagedCandidateIdentityV1":
        if not isinstance(raw, dict) or set(raw) != {"byteLength", "candidateRef", "contentDigest", "device", "inode"}:
            raise ValueError("staged candidate identity is invalid")
        return cls(raw["candidateRef"], raw["device"], raw["inode"], raw["byteLength"], raw["contentDigest"])


@dataclass(frozen=True, slots=True)
class TaskOutputTransitionV1:
    repository_relative_path: str
    phase: str
    output_kind: str
    precondition: str
    expected_pre_digest: str | None
    allowed_transition: str
    expected_post: ExpectedTaskOutputV1
    candidate_ref: str | None

    def __post_init__(self) -> None:
        _relative_path(self.repository_relative_path, "repository_relative_path")
        if not isinstance(self.phase, str) or self.phase not in _PHASES.values() or not isinstance(self.output_kind, str) or not isinstance(self.precondition, str) or not isinstance(self.allowed_transition, str) or not isinstance(self.expected_post, ExpectedTaskOutputV1):
            raise ValueError("transition phase or postcondition is invalid")
        if self.expected_pre_digest is not None:
            _require_digest(self.expected_pre_digest, "expected_pre_digest")
        if self.candidate_ref is not None:
            _candidate_ref(self.candidate_ref)
        allowed: dict[tuple[str, str], tuple[str, str, bool, str | None, str | None]] = {
            ("task28-register", "catalog"): ("equals-base-bytes", "replace-with-exact-bytes", False, "modified", "digest"),
            ("task28-register", "audit"): ("must-not-exist", "create-exact-bytes", False, "added", None),
            ("task29-promote", "contract"): ("equals-base-bytes", "replace-with-exact-bytes", False, "modified", "digest"),
        }
        if self.phase == "task28-register" and self.output_kind == "evidence-cas":
            allowed[(self.phase, self.output_kind)] = (
                "must-not-exist-or-same-intent-recovery", "create-or-resume-exact-cas", True, "added", None
            ) if self.allowed_transition == "create-or-resume-exact-cas" else (
                "equals-base-bytes", "retain-exact-cas", True, "unchanged", "digest"
            )
        key = (self.phase, self.output_kind)
        if key not in allowed:
            raise ValueError("transition output kind is invalid for phase")
        precondition, transition, needs_candidate, status, digest_requirement = allowed[key]
        if self.precondition != precondition or self.allowed_transition != transition or (self.candidate_ref is not None) != needs_candidate or self.expected_post.status != status:
            raise ValueError("transition combination is invalid")
        if digest_requirement == "digest" and self.expected_pre_digest is None:
            raise ValueError("base-byte transition needs a digest")
        if digest_requirement is None and self.expected_pre_digest is not None:
            raise ValueError("absence transition cannot have a pre-digest")
        if needs_candidate and self.candidate_ref != self.expected_post.content_source_ref:
            raise ValueError("candidate transition source ref mismatch")

    def to_wire(self) -> dict[str, object]:
        return {"allowedTransition": self.allowed_transition, "candidateRef": self.candidate_ref, "expectedPost": self.expected_post.to_wire(), "expectedPreDigest": self.expected_pre_digest, "outputKind": self.output_kind, "phase": self.phase, "precondition": self.precondition, "repositoryRelativePath": self.repository_relative_path}

    @classmethod
    def _from_wire(cls, raw: object) -> "TaskOutputTransitionV1":
        fields = {"allowedTransition", "candidateRef", "expectedPost", "expectedPreDigest", "outputKind", "phase", "precondition", "repositoryRelativePath"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("task output transition is invalid")
        return cls(raw["repositoryRelativePath"], raw["phase"], raw["outputKind"], raw["precondition"], raw["expectedPreDigest"], raw["allowedTransition"], ExpectedTaskOutputV1._from_wire(raw["expectedPost"]), raw["candidateRef"])


@dataclass(frozen=True, slots=True)
class AllowedTaskOutputsV1(_SelfDigestRecord):
    schema: str
    task_id: int
    phase: str
    base_commit: GitObjectIdV1
    task_output_policy_digest: str
    protected_paths_digest: str
    source_dirty_inputs: tuple[SourceDirtyAllowanceV1, ...]
    staged_candidates: tuple[StagedCandidateIdentityV1, ...]
    transitions: tuple[TaskOutputTransitionV1, ...]
    candidate_refs: tuple[str, ...]
    new_candidate_refs: tuple[str, ...]
    idempotent_candidate_refs: tuple[str, ...]
    expected_post_tree_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-allowed-task-outputs/v1" or type(self.task_id) is not int or _PHASES.get(self.task_id) != self.phase or type(self.base_commit) is not GitObjectIdV1:
            raise ValueError("allowed task output identity is invalid")
        for field_name in ("task_output_policy_digest", "protected_paths_digest", "expected_post_tree_digest"):
            _require_digest(getattr(self, field_name), field_name)
        dirty = _ordered(self.source_dirty_inputs, lambda value: value.repository_relative_path.encode("utf-8"), "source_dirty_inputs")
        if not all(isinstance(value, SourceDirtyAllowanceV1) for value in dirty):
            raise ValueError("source dirty inputs are invalid")
        candidates = _ordered(self.staged_candidates, lambda value: value.candidate_ref, "staged_candidates")
        if not all(isinstance(value, StagedCandidateIdentityV1) for value in candidates):
            raise ValueError("staged candidates are invalid")
        transitions = _ordered(self.transitions, lambda value: value.repository_relative_path.encode("utf-8"), "transitions")
        if not transitions or not all(isinstance(value, TaskOutputTransitionV1) and value.phase == self.phase for value in transitions):
            raise ValueError("transitions are invalid")
        refs = _ordered(self.candidate_refs, lambda value: value, "candidate_refs", count=7)
        new = _ordered(self.new_candidate_refs, lambda value: value, "new_candidate_refs")
        retained = _ordered(self.idempotent_candidate_refs, lambda value: value, "idempotent_candidate_refs")
        if not all(isinstance(value, str) and _candidate_ref(value) for value in (*refs, *new, *retained)) or set(new) & set(retained) or set(new) | set(retained) != set(refs):
            raise ValueError("candidate refs are not a closed new/idempotent partition")
        if self.task_id == 28:
            if tuple(value.candidate_ref for value in candidates) != refs or len(transitions) != 9:
                raise ValueError("task28 needs seven staged candidates and nine transitions")
            cas = tuple(value for value in transitions if value.output_kind == "evidence-cas")
            new_from_transitions = {value.candidate_ref for value in cas if value.allowed_transition == "create-or-resume-exact-cas"}
            retained_from_transitions = {value.candidate_ref for value in cas if value.allowed_transition == "retain-exact-cas"}
            if len(cas) != 7 or {value.candidate_ref for value in cas} != set(refs) or {value.output_kind for value in transitions} != {"catalog", "audit", "evidence-cas"} or new_from_transitions != set(new) or retained_from_transitions != set(retained):
                raise ValueError("task28 candidate transition closure is invalid")
        elif candidates or len(transitions) != 1 or transitions[0].output_kind != "contract":
            raise ValueError("task29 only permits the contract transition")
        object.__setattr__(self, "source_dirty_inputs", dirty)
        object.__setattr__(self, "staged_candidates", candidates)
        object.__setattr__(self, "transitions", transitions)
        object.__setattr__(self, "candidate_refs", refs)
        object.__setattr__(self, "new_candidate_refs", new)
        object.__setattr__(self, "idempotent_candidate_refs", retained)
        if len(canonical_json_bytes(self.to_wire())) > 64 * 1024:
            raise ValueError("allowed task outputs canonical bytes exceed 64 KiB")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"baseCommit": self.base_commit.to_wire(), "candidateRefs": list(self.candidate_refs), "expectedPostTreeDigest": self.expected_post_tree_digest, "idempotentCandidateRefs": list(self.idempotent_candidate_refs), "newCandidateRefs": list(self.new_candidate_refs), "phase": self.phase, "protectedPathsDigest": self.protected_paths_digest, "schema": self.schema, "sourceDirtyInputs": [item.to_wire() for item in self.source_dirty_inputs], "stagedCandidates": [item.to_wire() for item in self.staged_candidates], "taskId": self.task_id, "taskOutputPolicyDigest": self.task_output_policy_digest, "transitions": [item.to_wire() for item in self.transitions]}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "AllowedTaskOutputsV1":
        fields = {"baseCommit", "candidateRefs", _SELF_DIGEST, "expectedPostTreeDigest", "idempotentCandidateRefs", "newCandidateRefs", "phase", "protectedPathsDigest", "schema", "sourceDirtyInputs", "stagedCandidates", "taskId", "taskOutputPolicyDigest", "transitions"}
        raw = _strict_object(payload, fields, "allowed task outputs")
        collections = ("candidateRefs", "idempotentCandidateRefs", "newCandidateRefs", "sourceDirtyInputs", "stagedCandidates", "transitions")
        if any(not isinstance(raw[name], list) for name in collections):
            raise ValueError("allowed task outputs collections are invalid")
        result = cls(raw["schema"], raw["taskId"], raw["phase"], _oid(raw["baseCommit"], "base_commit"), raw["taskOutputPolicyDigest"], raw["protectedPathsDigest"], tuple(SourceDirtyAllowanceV1._from_wire(item) for item in raw["sourceDirtyInputs"]), tuple(StagedCandidateIdentityV1._from_wire(item) for item in raw["stagedCandidates"]), tuple(TaskOutputTransitionV1._from_wire(item) for item in raw["transitions"]), tuple(raw["candidateRefs"]), tuple(raw["newCandidateRefs"]), tuple(raw["idempotentCandidateRefs"]), raw["expectedPostTreeDigest"])
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("allowed task outputs self digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class RegistrationPreStatePathV1:
    repository_relative_path: str
    role: str
    classification: str
    status: str
    content_digest: str | None

    def __post_init__(self) -> None:
        _relative_path(self.repository_relative_path, "repository_relative_path")
        valid = {
            "catalog": {("equals-base-bytes", "unchanged", True)},
            "audit": {("absent", "absent", False)},
            "evidence-cas": {("absent", "absent", False), ("base-tracked-exact", "unchanged", True)},
            "source-dirty": {( "allowed-dirty", status, True) for status in _DIRTY_STATUSES},
            "protected": {("protected-base-exact", "unchanged", True)},
        }
        if not isinstance(self.role, str) or not isinstance(self.classification, str) or not isinstance(self.status, str) or self.role not in valid or (self.classification, self.status, self.content_digest is not None) not in valid[self.role]:
            raise ValueError("pre-state path classification is invalid")
        if self.content_digest is not None:
            _require_digest(self.content_digest, "content_digest")

    def to_wire(self) -> dict[str, object]:
        return {"classification": self.classification, "contentDigest": self.content_digest, "repositoryRelativePath": self.repository_relative_path, "role": self.role, "status": self.status}

    @classmethod
    def _from_wire(cls, raw: object) -> "RegistrationPreStatePathV1":
        if not isinstance(raw, dict) or set(raw) != {"classification", "contentDigest", "repositoryRelativePath", "role", "status"}:
            raise ValueError("registration pre-state path is invalid")
        return cls(raw["repositoryRelativePath"], raw["role"], raw["classification"], raw["status"], raw["contentDigest"])


def _prestate_paths(value: object, role: str, field: str, *, count: int | None = None) -> tuple[RegistrationPreStatePathV1, ...]:
    values = _ordered(value, lambda item: (item.repository_relative_path.encode("utf-8"), item.role), field, count=count)
    if not all(isinstance(item, RegistrationPreStatePathV1) and item.role == role for item in values):
        raise ValueError(f"{field} must contain {role} paths")
    return values  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class TrustedRegistrationPreStateV1(_SelfDigestRecord):
    schema: str
    base_commit: GitObjectIdV1
    fixed_ref_name: str
    fixed_ref_commit: GitObjectIdV1
    catalog: RegistrationPreStatePathV1
    audit: RegistrationPreStatePathV1
    cas_entries: tuple[RegistrationPreStatePathV1, ...]
    source_dirty_inputs: tuple[RegistrationPreStatePathV1, ...]
    protected_paths: tuple[RegistrationPreStatePathV1, ...]
    index_classification: str
    index_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-pre-state/v1" or type(self.base_commit) is not GitObjectIdV1 or type(self.fixed_ref_commit) is not GitObjectIdV1:
            raise ValueError("pre-state identity is invalid")
        _require_text(self.fixed_ref_name, "fixed_ref_name")
        if not self.fixed_ref_name.startswith("refs/") or self.index_classification != "empty":
            raise ValueError("fixed ref or index classification is invalid")
        _require_digest(self.index_digest, "index_digest")
        if not isinstance(self.catalog, RegistrationPreStatePathV1) or self.catalog.role != "catalog" or not isinstance(self.audit, RegistrationPreStatePathV1) or self.audit.role != "audit":
            raise ValueError("catalog and audit pre-state are invalid")
        cas = _prestate_paths(self.cas_entries, "evidence-cas", "cas_entries", count=7)
        dirty = _prestate_paths(self.source_dirty_inputs, "source-dirty", "source_dirty_inputs")
        protected = _prestate_paths(self.protected_paths, "protected", "protected_paths")
        if not protected:
            raise ValueError("protected paths must not be empty")
        object.__setattr__(self, "cas_entries", cas)
        object.__setattr__(self, "source_dirty_inputs", dirty)
        object.__setattr__(self, "protected_paths", protected)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"audit": self.audit.to_wire(), "baseCommit": self.base_commit.to_wire(), "casEntries": [item.to_wire() for item in self.cas_entries], "catalog": self.catalog.to_wire(), "fixedRefCommit": self.fixed_ref_commit.to_wire(), "fixedRefName": self.fixed_ref_name, "indexClassification": self.index_classification, "indexDigest": self.index_digest, "protectedPaths": [item.to_wire() for item in self.protected_paths], "schema": self.schema, "sourceDirtyInputs": [item.to_wire() for item in self.source_dirty_inputs]}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TrustedRegistrationPreStateV1":
        fields = {"audit", "baseCommit", _SELF_DIGEST, "casEntries", "catalog", "fixedRefCommit", "fixedRefName", "indexClassification", "indexDigest", "protectedPaths", "schema", "sourceDirtyInputs"}
        raw = _strict_object(payload, fields, "registration pre-state")
        if any(not isinstance(raw[name], list) for name in ("casEntries", "protectedPaths", "sourceDirtyInputs")):
            raise ValueError("registration pre-state collections are invalid")
        result = cls(raw["schema"], _oid(raw["baseCommit"], "base_commit"), raw["fixedRefName"], _oid(raw["fixedRefCommit"], "fixed_ref_commit"), RegistrationPreStatePathV1._from_wire(raw["catalog"]), RegistrationPreStatePathV1._from_wire(raw["audit"]), tuple(RegistrationPreStatePathV1._from_wire(item) for item in raw["casEntries"]), tuple(RegistrationPreStatePathV1._from_wire(item) for item in raw["sourceDirtyInputs"]), tuple(RegistrationPreStatePathV1._from_wire(item) for item in raw["protectedPaths"]), raw["indexClassification"], raw["indexDigest"])
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("registration pre-state self digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class StagingCandidateProvenanceV1:
    candidate_ref: str
    source_device: int
    source_inode: int
    copied_byte_length: int
    copied_content_digest: str

    def __post_init__(self) -> None:
        _candidate_ref(self.candidate_ref)
        _int(self.source_device, "source_device")
        _int(self.source_inode, "source_inode")
        _int(self.copied_byte_length, "copied_byte_length")
        _require_digest(self.copied_content_digest, "copied_content_digest")
        if self.candidate_ref != self.copied_content_digest:
            raise ValueError("staging provenance ref must bind copied digest")

    def to_wire(self) -> dict[str, object]:
        return {"candidateRef": self.candidate_ref, "copiedByteLength": self.copied_byte_length, "copiedContentDigest": self.copied_content_digest, "sourceDevice": self.source_device, "sourceInode": self.source_inode}

    @classmethod
    def _from_wire(cls, raw: object) -> "StagingCandidateProvenanceV1":
        if not isinstance(raw, dict) or set(raw) != {"candidateRef", "copiedByteLength", "copiedContentDigest", "sourceDevice", "sourceInode"}:
            raise ValueError("staging candidate provenance is invalid")
        return cls(raw["candidateRef"], raw["sourceDevice"], raw["sourceInode"], raw["copiedByteLength"], raw["copiedContentDigest"])


@dataclass(frozen=True, slots=True)
class StagedCandidateSetV1(_SelfDigestRecord):
    schema: str
    provenance_only: bool
    candidates: tuple[StagingCandidateProvenanceV1, ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-staged-candidate-set/v1" or self.provenance_only is not True:
            raise ValueError("staged candidate set must be provenance-only")
        candidates = _ordered(self.candidates, lambda item: item.candidate_ref, "candidates", count=7)
        if not all(isinstance(item, StagingCandidateProvenanceV1) for item in candidates):
            raise ValueError("staging candidates are invalid")
        object.__setattr__(self, "candidates", candidates)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"candidates": [item.to_wire() for item in self.candidates], "provenanceOnly": self.provenance_only, "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "StagedCandidateSetV1":
        raw = _strict_object(payload, {"candidates", _SELF_DIGEST, "provenanceOnly", "schema"}, "staged candidate set")
        if not isinstance(raw["candidates"], list):
            raise ValueError("staged candidate set candidates are invalid")
        result = cls(raw["schema"], raw["provenanceOnly"], tuple(StagingCandidateProvenanceV1._from_wire(item) for item in raw["candidates"]))
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("staged candidate set self digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class QuarantineObjectIdentityV1:
    relative_path: str
    object_kind: str
    candidate_ref: str | None
    file_type: str
    owner_uid: int
    mode: int
    link_count: int
    device: int
    inode: int
    byte_length: int
    content_digest: str

    def __post_init__(self) -> None:
        _relative_path(self.relative_path, "relative_path")
        expected_ref = self.object_kind == "candidate"
        if not isinstance(self.object_kind, str) or not isinstance(self.file_type, str) or self.object_kind not in {"output-plan", "expected-audit", "candidate"} or self.file_type != "regular" or self.link_count != 1:
            raise ValueError("quarantine object kind or file identity is invalid")
        if (self.candidate_ref is not None) != expected_ref:
            raise ValueError("quarantine candidate ref is invalid")
        if self.candidate_ref is not None:
            _candidate_ref(self.candidate_ref)
        _int(self.owner_uid, "owner_uid")
        if type(self.mode) is not int or self.mode < 0 or self.mode > 0o777:
            raise ValueError("mode must be a POSIX permission value")
        for field_name in ("device", "inode", "byte_length"):
            _int(getattr(self, field_name), field_name)
        _require_digest(self.content_digest, "content_digest")
        if self.candidate_ref is not None and self.candidate_ref != self.content_digest:
            raise ValueError("quarantine candidate ref must bind content digest")

    def to_wire(self) -> dict[str, object]:
        return {"byteLength": self.byte_length, "candidateRef": self.candidate_ref, "contentDigest": self.content_digest, "device": self.device, "fileType": self.file_type, "inode": self.inode, "linkCount": self.link_count, "mode": self.mode, "objectKind": self.object_kind, "ownerUid": self.owner_uid, "relativePath": self.relative_path}

    @classmethod
    def _from_wire(cls, raw: object) -> "QuarantineObjectIdentityV1":
        fields = {"byteLength", "candidateRef", "contentDigest", "device", "fileType", "inode", "linkCount", "mode", "objectKind", "ownerUid", "relativePath"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("quarantine object identity is invalid")
        return cls(raw["relativePath"], raw["objectKind"], raw["candidateRef"], raw["fileType"], raw["ownerUid"], raw["mode"], raw["linkCount"], raw["device"], raw["inode"], raw["byteLength"], raw["contentDigest"])


@dataclass(frozen=True, slots=True)
class QuarantineObjectSetV1(_SelfDigestRecord):
    schema: str
    objects: tuple[QuarantineObjectIdentityV1, ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-quarantine-object-set/v1":
            raise ValueError("quarantine object set schema is invalid")
        objects = _ordered(self.objects, lambda item: (item.relative_path.encode("utf-8"), item.object_kind, item.candidate_ref or ""), "objects", count=9)
        if not all(isinstance(item, QuarantineObjectIdentityV1) for item in objects):
            raise ValueError("quarantine objects are invalid")
        kinds = [item.object_kind for item in objects]
        if kinds.count("output-plan") != 1 or kinds.count("expected-audit") != 1 or kinds.count("candidate") != 7:
            raise ValueError("quarantine set must close plan, audit, and seven candidates")
        object.__setattr__(self, "objects", objects)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"objects": [item.to_wire() for item in self.objects], "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "QuarantineObjectSetV1":
        raw = _strict_object(payload, {_SELF_DIGEST, "objects", "schema"}, "quarantine object set")
        if not isinstance(raw["objects"], list):
            raise ValueError("quarantine object set objects are invalid")
        result = cls(raw["schema"], tuple(QuarantineObjectIdentityV1._from_wire(item) for item in raw["objects"]))
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("quarantine object set self digest is invalid")
        return result


# D.2 keeps transaction contracts portable.  Opening Linux broker channels,
# performing repository mutation, and persisting journals remain Task 14 work.
def _named_digest(raw: object, field: str) -> NamedDigest:
    if not isinstance(raw, dict) or set(raw) != {"digest", "name"}:
        raise ValueError(f"{field} must be a NamedDigest")
    return NamedDigest(raw["name"], raw["digest"])


def _signature(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{128}", value):
        raise ValueError(f"{field} must be a 64-byte lower-case signature")
    return value


@dataclass(frozen=True, slots=True)
class RegistrationRepositoryRecoveryBindingV1(_SelfDigestRecord):
    schema: str
    repository_absolute_path: str
    repository_device: int
    repository_inode: int
    repository_mount_identity: str
    repository_boundary_digest: str
    base_commit: GitObjectIdV1
    fixed_ref_name: str
    fixed_ref_commit: GitObjectIdV1
    trusted_git_toolchain_build_record_ref: str
    trusted_git_toolchain_build_record_digest: str
    trusted_git_executable_digest: str
    trusted_config_snapshot_digest: str
    trusted_attribute_snapshot_digest: str
    trusted_worktree_snapshot_digest: str
    trusted_git_view_digest: str
    task_output_policy_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-repository-recovery-binding/v1":
            raise ValueError("recovery binding schema is invalid")
        path = _require_text(self.repository_absolute_path, "repository_absolute_path")
        if not path.startswith("/"):
            raise ValueError("repository_absolute_path is display-only but must be absolute")
        _int(self.repository_device, "repository_device")
        _int(self.repository_inode, "repository_inode", minimum=1)
        _require_text(self.repository_mount_identity, "repository_mount_identity")
        if type(self.base_commit) is not GitObjectIdV1 or type(self.fixed_ref_commit) is not GitObjectIdV1:
            raise ValueError("recovery binding Git identities are invalid")
        _require_text(self.fixed_ref_name, "fixed_ref_name")
        if not self.fixed_ref_name.startswith("refs/"):
            raise ValueError("fixed_ref_name is invalid")
        for field in ("repository_boundary_digest", "trusted_git_toolchain_build_record_ref", "trusted_git_toolchain_build_record_digest", "trusted_git_executable_digest", "trusted_config_snapshot_digest", "trusted_attribute_snapshot_digest", "trusted_worktree_snapshot_digest", "trusted_git_view_digest", "task_output_policy_digest"):
            _require_digest(getattr(self, field), field)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"baseCommit": self.base_commit.to_wire(), "fixedRefCommit": self.fixed_ref_commit.to_wire(), "fixedRefName": self.fixed_ref_name, "repositoryAbsolutePath": self.repository_absolute_path, "repositoryBoundaryDigest": self.repository_boundary_digest, "repositoryDevice": self.repository_device, "repositoryInode": self.repository_inode, "repositoryMountIdentity": self.repository_mount_identity, "schema": self.schema, "taskOutputPolicyDigest": self.task_output_policy_digest, "trustedAttributeSnapshotDigest": self.trusted_attribute_snapshot_digest, "trustedConfigSnapshotDigest": self.trusted_config_snapshot_digest, "trustedGitExecutableDigest": self.trusted_git_executable_digest, "trustedGitToolchainBuildRecordDigest": self.trusted_git_toolchain_build_record_digest, "trustedGitToolchainBuildRecordRef": self.trusted_git_toolchain_build_record_ref, "trustedGitViewDigest": self.trusted_git_view_digest, "trustedWorktreeSnapshotDigest": self.trusted_worktree_snapshot_digest}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationRepositoryRecoveryBindingV1":
        fields = {"baseCommit", "canonicalDigest", "fixedRefCommit", "fixedRefName", "repositoryAbsolutePath", "repositoryBoundaryDigest", "repositoryDevice", "repositoryInode", "repositoryMountIdentity", "schema", "taskOutputPolicyDigest", "trustedAttributeSnapshotDigest", "trustedConfigSnapshotDigest", "trustedGitExecutableDigest", "trustedGitToolchainBuildRecordDigest", "trustedGitToolchainBuildRecordRef", "trustedGitViewDigest", "trustedWorktreeSnapshotDigest"}
        raw = _strict_object(payload, fields, "recovery binding")
        result = cls(raw["schema"], raw["repositoryAbsolutePath"], raw["repositoryDevice"], raw["repositoryInode"], raw["repositoryMountIdentity"], raw["repositoryBoundaryDigest"], _oid(raw["baseCommit"], "baseCommit"), raw["fixedRefName"], _oid(raw["fixedRefCommit"], "fixedRefCommit"), raw["trustedGitToolchainBuildRecordRef"], raw["trustedGitToolchainBuildRecordDigest"], raw["trustedGitExecutableDigest"], raw["trustedConfigSnapshotDigest"], raw["trustedAttributeSnapshotDigest"], raw["trustedWorktreeSnapshotDigest"], raw["trustedGitViewDigest"], raw["taskOutputPolicyDigest"])
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("recovery binding self digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class RegistrationTransactionIntentV1(_SelfDigestRecord):
    schema: str
    operation_id: str
    output_plan_digest: str
    trusted_pre_state_digest: str
    staged_candidate_set_digest: str
    quarantine_object_set_digest: str
    repository_recovery_binding_digest: str
    registration_recovery_lease_identity_digest: str
    expected_cas_refs: tuple[str, ...]
    expected_audit_digest: str
    expected_catalog_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-transaction-intent/v1":
            raise ValueError("transaction intent schema is invalid")
        _require_text(self.operation_id, "operation_id")
        for field in ("output_plan_digest", "trusted_pre_state_digest", "staged_candidate_set_digest", "quarantine_object_set_digest", "repository_recovery_binding_digest", "registration_recovery_lease_identity_digest", "expected_audit_digest", "expected_catalog_digest"):
            _require_digest(getattr(self, field), field)
        refs = _ordered(self.expected_cas_refs, lambda item: item, "expected_cas_refs", count=7)
        if not all(isinstance(item, str) and _require_digest(item, "expected_cas_ref") for item in refs):
            raise ValueError("expected_cas_refs are invalid")
        object.__setattr__(self, "expected_cas_refs", refs)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"expectedAuditDigest": self.expected_audit_digest, "expectedCasRefs": list(self.expected_cas_refs), "expectedCatalogDigest": self.expected_catalog_digest, "operationId": self.operation_id, "outputPlanDigest": self.output_plan_digest, "quarantineObjectSetDigest": self.quarantine_object_set_digest, "registrationRecoveryLeaseIdentityDigest": self.registration_recovery_lease_identity_digest, "repositoryRecoveryBindingDigest": self.repository_recovery_binding_digest, "schema": self.schema, "stagedCandidateSetDigest": self.staged_candidate_set_digest, "trustedPreStateDigest": self.trusted_pre_state_digest}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationTransactionIntentV1":
        fields = {"canonicalDigest", "expectedAuditDigest", "expectedCasRefs", "expectedCatalogDigest", "operationId", "outputPlanDigest", "quarantineObjectSetDigest", "registrationRecoveryLeaseIdentityDigest", "repositoryRecoveryBindingDigest", "schema", "stagedCandidateSetDigest", "trustedPreStateDigest"}
        raw = _strict_object(payload, fields, "transaction intent")
        if not isinstance(raw["expectedCasRefs"], list):
            raise ValueError("expectedCasRefs is invalid")
        result = cls(raw["schema"], raw["operationId"], raw["outputPlanDigest"], raw["trustedPreStateDigest"], raw["stagedCandidateSetDigest"], raw["quarantineObjectSetDigest"], raw["repositoryRecoveryBindingDigest"], raw["registrationRecoveryLeaseIdentityDigest"], tuple(raw["expectedCasRefs"]), raw["expectedAuditDigest"], raw["expectedCatalogDigest"])
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("transaction intent self digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class RegistrationPendingTransitionV1:
    action: str
    target_repository_relative_path: str | None
    expected_content_digest: str

    def __post_init__(self) -> None:
        if self.action not in {"install-cas", "install-audit", "replace-catalog", "append-terminal-state"}:
            raise ValueError("pending transition action is invalid")
        if self.action == "append-terminal-state":
            if self.target_repository_relative_path is not None:
                raise ValueError("terminal transition must not have a target")
        elif self.target_repository_relative_path is None:
            raise ValueError("repository transition needs a target")
        else:
            _relative_path(self.target_repository_relative_path, "target_repository_relative_path")
        _require_digest(self.expected_content_digest, "expected_content_digest")

    def to_wire(self) -> dict[str, object]:
        return {"action": self.action, "expectedContentDigest": self.expected_content_digest, "targetRepositoryRelativePath": self.target_repository_relative_path}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self.to_wire())

    @classmethod
    def _from_wire(cls, raw: object) -> "RegistrationPendingTransitionV1":
        if not isinstance(raw, dict) or set(raw) != {"action", "expectedContentDigest", "targetRepositoryRelativePath"}:
            raise ValueError("pending transition wire is invalid")
        return cls(raw["action"], raw["targetRepositoryRelativePath"], raw["expectedContentDigest"])

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationPendingTransitionV1":
        raw = _strict_object(payload, {"action", "expectedContentDigest", "targetRepositoryRelativePath"}, "pending transition")
        return cls._from_wire(raw)


_REGISTRATION_STATES = ("PREPARED", "WRITING_CAS", "WRITING_AUDIT", "CATALOG_COMMITTING", "ACTIVATED", "FAILED_BLOCKED")
_REGISTRATION_ACTIONS = {"PREPARED": {None}, "WRITING_CAS": {None, "install-cas"}, "WRITING_AUDIT": {None, "install-audit"}, "CATALOG_COMMITTING": {None, "replace-catalog"}, "ACTIVATED": {None, "append-terminal-state"}, "FAILED_BLOCKED": {None}}


@dataclass(frozen=True, slots=True)
class RegistrationJournalEntryV1(_SelfDigestRecord):
    schema: str
    operation_id: str
    intent_digest: str
    state: str
    previous_entry_digest: str | None
    pending_transition: RegistrationPendingTransitionV1 | None
    expected_catalog_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-journal-entry/v1" or self.state not in _REGISTRATION_STATES:
            raise ValueError("registration journal identity is invalid")
        _require_text(self.operation_id, "operation_id")
        for field in ("intent_digest", "expected_catalog_digest"):
            _require_digest(getattr(self, field), field)
        if self.previous_entry_digest is not None:
            _require_digest(self.previous_entry_digest, "previous_entry_digest")
        if self.pending_transition is not None and type(self.pending_transition) is not RegistrationPendingTransitionV1:
            raise ValueError("pending transition is invalid")
        action = None if self.pending_transition is None else self.pending_transition.action
        if action not in _REGISTRATION_ACTIONS[self.state]:
            raise ValueError("pending transition does not match journal state")
        if self.state == "PREPARED" and self.previous_entry_digest is not None:
            raise ValueError("prepared journal entry must begin the chain")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"expectedCatalogDigest": self.expected_catalog_digest, "intentDigest": self.intent_digest, "operationId": self.operation_id, "pendingTransition": None if self.pending_transition is None else self.pending_transition.to_wire(), "previousEntryDigest": self.previous_entry_digest, "schema": self.schema, "state": self.state}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationJournalEntryV1":
        fields = {"canonicalDigest", "expectedCatalogDigest", "intentDigest", "operationId", "pendingTransition", "previousEntryDigest", "schema", "state"}
        raw = _strict_object(payload, fields, "registration journal entry")
        result = cls(raw["schema"], raw["operationId"], raw["intentDigest"], raw["state"], raw["previousEntryDigest"], None if raw["pendingTransition"] is None else RegistrationPendingTransitionV1._from_wire(raw["pendingTransition"]), raw["expectedCatalogDigest"])
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("registration journal entry self digest is invalid")
        return result

    @classmethod
    def validate_chain(cls, entries: object) -> None:
        if isinstance(entries, (str, bytes)) or not isinstance(entries, (tuple, list)) or not entries:
            raise ValueError("registration journal chain is invalid")
        values = tuple(entries)
        if not all(type(item) is cls for item in values):
            raise ValueError("registration journal chain entry is invalid")
        order = {state: index for index, state in enumerate(_REGISTRATION_STATES)}
        first = values[0]
        if first.state != "PREPARED" or first.previous_entry_digest is not None:
            raise ValueError("registration journal must begin prepared")
        for prior, current in zip(values, values[1:]):
            if current.previous_entry_digest != prior.canonical_digest or current.operation_id != first.operation_id or current.intent_digest != first.intent_digest or current.expected_catalog_digest != first.expected_catalog_digest:
                raise ValueError("registration journal chain binding is invalid")
            if current.state == "FAILED_BLOCKED":
                continue
            if prior.state == "FAILED_BLOCKED" or order[current.state] < order[prior.state] or order[current.state] > order[prior.state] + 1:
                raise ValueError("registration journal transition is not forward-only")


@dataclass(frozen=True, slots=True)
class RegistrationTransactionStateV1(_SelfDigestRecord):
    schema: str
    operation_id: str
    intent_digest: str
    journal_head_digest: str
    state: str
    previous_entry_digest: str | None
    pending_transition: RegistrationPendingTransitionV1 | None
    expected_catalog_digest: str
    observed_catalog_digest: str
    catalog_visible: bool
    catalog_parent_fsync_complete: bool

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-transaction-state/v1" or self.state not in _REGISTRATION_STATES:
            raise ValueError("transaction state identity is invalid")
        _require_text(self.operation_id, "operation_id")
        for field in ("intent_digest", "journal_head_digest", "expected_catalog_digest", "observed_catalog_digest"):
            _require_digest(getattr(self, field), field)
        if self.previous_entry_digest is not None:
            _require_digest(self.previous_entry_digest, "previous_entry_digest")
        if type(self.catalog_visible) is not bool or type(self.catalog_parent_fsync_complete) is not bool:
            raise ValueError("catalog visibility fields are invalid")
        if self.pending_transition is not None and type(self.pending_transition) is not RegistrationPendingTransitionV1:
            raise ValueError("transaction pending transition is invalid")
        action = None if self.pending_transition is None else self.pending_transition.action
        if action not in _REGISTRATION_ACTIONS[self.state]:
            raise ValueError("state pending transition is invalid")
        if self.catalog_parent_fsync_complete and not self.catalog_visible:
            raise ValueError("catalog parent fsync cannot precede visibility")
        if self.catalog_visible and (self.state not in {"CATALOG_COMMITTING", "ACTIVATED"} or self.observed_catalog_digest != self.expected_catalog_digest):
            raise ValueError("visible catalog state is invalid")
        if self.state == "ACTIVATED" and (not self.catalog_visible or not self.catalog_parent_fsync_complete or self.pending_transition is not None):
            raise ValueError("activated state is not durable")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"catalogParentFsyncComplete": self.catalog_parent_fsync_complete, "catalogVisible": self.catalog_visible, "expectedCatalogDigest": self.expected_catalog_digest, "intentDigest": self.intent_digest, "journalHeadDigest": self.journal_head_digest, "observedCatalogDigest": self.observed_catalog_digest, "operationId": self.operation_id, "pendingTransition": None if self.pending_transition is None else self.pending_transition.to_wire(), "previousEntryDigest": self.previous_entry_digest, "schema": self.schema, "state": self.state}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationTransactionStateV1":
        fields = {"canonicalDigest", "catalogParentFsyncComplete", "catalogVisible", "expectedCatalogDigest", "intentDigest", "journalHeadDigest", "observedCatalogDigest", "operationId", "pendingTransition", "previousEntryDigest", "schema", "state"}
        raw = _strict_object(payload, fields, "transaction state")
        result = cls(raw["schema"], raw["operationId"], raw["intentDigest"], raw["journalHeadDigest"], raw["state"], raw["previousEntryDigest"], None if raw["pendingTransition"] is None else RegistrationPendingTransitionV1._from_wire(raw["pendingTransition"]), raw["expectedCatalogDigest"], raw["observedCatalogDigest"], raw["catalogVisible"], raw["catalogParentFsyncComplete"])
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("transaction state self digest is invalid")
        return result


class RegistrationTransactionCapabilityV1:
    """In-memory owning directory handles; deliberately neither wire nor copyable."""
    __slots__ = ("_fds", "directory_identities")
    _TOKEN = object()

    def __init__(self, token: object, descriptors: tuple[int, ...]) -> None:
        if token is not self._TOKEN:
            raise TypeError("use open_precreated with directory file descriptors")
        self._fds = descriptors
        self.directory_identities = tuple((os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in descriptors)

    @classmethod
    def open_precreated(cls, transaction_parent_fd: int, transaction_root_fd: int, operation_directory_fd: int, repository_fd: int) -> "RegistrationTransactionCapabilityV1":
        supplied = (transaction_parent_fd, transaction_root_fd, operation_directory_fd, repository_fd)
        if not all(type(fd) is int for fd in supplied):
            raise ValueError("precreated directory descriptors are required")
        copies: list[int] = []
        try:
            identities = []
            for fd in supplied:
                info = os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("precreated descriptor is not a directory")
                identities.append((info.st_dev, info.st_ino))
                copies.append(os.dup(fd))
            if len(set(identities)) != 4:
                raise ValueError("transaction and repository directories must have distinct identities")
            return cls(cls._TOKEN, tuple(copies))
        except (OSError, TypeError, ValueError) as error:
            for copied in copies:
                os.close(copied)
            raise ValueError("precreated directory validation failed") from error

    def validate_open_identities(self) -> None:
        if tuple((os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in self._fds) != self.directory_identities:
            raise ValueError("directory descriptor identity changed")

    def close(self) -> None:
        for fd in self._fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = ()

    def __copy__(self):
        raise TypeError("transaction capability is non-copyable")

    def __deepcopy__(self, memo):
        raise TypeError("transaction capability is non-copyable")

    def __reduce__(self):
        raise TypeError("transaction capability is non-serializable")


@dataclass(frozen=True, slots=True)
class BrokerLifecycleProofV1:
    schema: str
    broker_key_id: str
    lifecycle_id: str
    lease_id: str
    subject_commit: ProductGitObjectIdV1
    staged_artifact_digests: tuple[NamedDigest, ...]
    producer_exit_digest: str
    destroyed_resources_digest: str
    shared_control_baseline_digest: str
    shared_object_store_baseline_digest: str
    proof_payload_digest: str
    broker_signature: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-broker-lifecycle-proof/v1" or type(self.subject_commit) is not ProductGitObjectIdV1:
            raise ValueError("lifecycle proof identity is invalid")
        for field in ("broker_key_id", "lifecycle_id", "lease_id"):
            _require_text(getattr(self, field), field)
        staged = _ordered(self.staged_artifact_digests, lambda item: (item.name.encode("utf-8"), item.digest.encode("ascii")), "staged_artifact_digests")
        if not staged or not all(type(item) is NamedDigest for item in staged) or not any(item.name == "record-payload" for item in staged):
            raise ValueError("lifecycle proof must bind record payload and staged artifacts")
        for field in ("producer_exit_digest", "destroyed_resources_digest", "shared_control_baseline_digest", "shared_object_store_baseline_digest", "proof_payload_digest"):
            _require_digest(getattr(self, field), field)
        _signature(self.broker_signature, "broker_signature")
        object.__setattr__(self, "staged_artifact_digests", staged)

    def to_wire(self) -> dict[str, object]:
        return {"brokerKeyId": self.broker_key_id, "brokerSignature": self.broker_signature, "destroyedResourcesDigest": self.destroyed_resources_digest, "leaseId": self.lease_id, "lifecycleId": self.lifecycle_id, "producerExitDigest": self.producer_exit_digest, "proofPayloadDigest": self.proof_payload_digest, "schema": self.schema, "sharedControlBaselineDigest": self.shared_control_baseline_digest, "sharedObjectStoreBaselineDigest": self.shared_object_store_baseline_digest, "stagedArtifactDigests": [_wire(item) for item in self.staged_artifact_digests], "subjectCommit": self.subject_commit.to_wire()}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self.to_wire())

    def validate_staged_artifacts(self, expected: object) -> None:
        expected_values = _ordered(expected, lambda item: (item.name.encode("utf-8"), item.digest.encode("ascii")), "expected_staged_artifacts")
        if tuple(expected_values) != self.staged_artifact_digests:
            raise ValueError("lifecycle proof staged artifact set changed")

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerLifecycleProofV1":
        fields = {"brokerKeyId", "brokerSignature", "destroyedResourcesDigest", "leaseId", "lifecycleId", "producerExitDigest", "proofPayloadDigest", "schema", "sharedControlBaselineDigest", "sharedObjectStoreBaselineDigest", "stagedArtifactDigests", "subjectCommit"}
        raw = _strict_object(payload, fields, "lifecycle proof")
        if not isinstance(raw["stagedArtifactDigests"], list):
            raise ValueError("lifecycle proof staged artifacts are invalid")
        return cls(raw["schema"], raw["brokerKeyId"], raw["lifecycleId"], raw["leaseId"], ProductGitObjectIdV1(**raw["subjectCommit"]), tuple(_named_digest(item, "staged artifact") for item in raw["stagedArtifactDigests"]), raw["producerExitDigest"], raw["destroyedResourcesDigest"], raw["sharedControlBaselineDigest"], raw["sharedObjectStoreBaselineDigest"], raw["proofPayloadDigest"], raw["brokerSignature"])


@dataclass(frozen=True, slots=True)
class Task26CompletionPayloadV1:
    project_id: str
    task_id: int
    reviewed_commit: GitObjectIdV1
    review_base_commit: GitObjectIdV1
    expected_old_head: GitObjectIdV1
    target_head_ref: str
    review_verdict: str
    review_evidence_ref: NamedDigest
    plan_ref: NamedDigest
    spec_ref: NamedDigest
    producer_id: str
    executor_id: str
    execution_profile_digest: str
    toolchain_build_record_ref: str
    trusted_git_executable_digest: str
    trusted_git_view_digest: str

    def __post_init__(self) -> None:
        if self.project_id != "ai-video-reverse-engineer" or self.task_id != 26 or self.review_verdict != "pass" or self.producer_id != "task26-review-controller/v1":
            raise ValueError("Task 26 completion payload is not a broker PASS payload")
        if not all(type(item) is GitObjectIdV1 for item in (self.reviewed_commit, self.review_base_commit, self.expected_old_head)):
            raise ValueError("Task 26 Git identities are invalid")
        _require_text(self.target_head_ref, "target_head_ref")
        if not self.target_head_ref.startswith("refs/"):
            raise ValueError("target_head_ref is invalid")
        if not all(type(item) is NamedDigest for item in (self.review_evidence_ref, self.plan_ref, self.spec_ref)):
            raise ValueError("Task 26 evidence refs are invalid")
        _require_text(self.executor_id, "executor_id")
        for field in ("execution_profile_digest", "toolchain_build_record_ref", "trusted_git_executable_digest", "trusted_git_view_digest"):
            _require_digest(getattr(self, field), field)

    def to_wire(self) -> dict[str, object]:
        return {"executionProfileDigest": self.execution_profile_digest, "executorId": self.executor_id, "expectedOldHead": self.expected_old_head.to_wire(), "planRef": self.plan_ref.to_wire(), "producerId": self.producer_id, "projectId": self.project_id, "reviewBaseCommit": self.review_base_commit.to_wire(), "reviewEvidenceRef": self.review_evidence_ref.to_wire(), "reviewVerdict": self.review_verdict, "reviewedCommit": self.reviewed_commit.to_wire(), "specRef": self.spec_ref.to_wire(), "targetHeadRef": self.target_head_ref, "taskId": self.task_id, "toolchainBuildRecordRef": self.toolchain_build_record_ref, "trustedGitExecutableDigest": self.trusted_git_executable_digest, "trustedGitViewDigest": self.trusted_git_view_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self.to_wire())

    @classmethod
    def _from_wire(cls, raw: object) -> "Task26CompletionPayloadV1":
        fields = {"executionProfileDigest", "executorId", "expectedOldHead", "planRef", "producerId", "projectId", "reviewBaseCommit", "reviewEvidenceRef", "reviewVerdict", "reviewedCommit", "specRef", "targetHeadRef", "taskId", "toolchainBuildRecordRef", "trustedGitExecutableDigest", "trustedGitViewDigest"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("Task 26 payload wire is invalid")
        return cls(raw["projectId"], raw["taskId"], _oid(raw["reviewedCommit"], "reviewedCommit"), _oid(raw["reviewBaseCommit"], "reviewBaseCommit"), _oid(raw["expectedOldHead"], "expectedOldHead"), raw["targetHeadRef"], raw["reviewVerdict"], _named_digest(raw["reviewEvidenceRef"], "reviewEvidenceRef"), _named_digest(raw["planRef"], "planRef"), _named_digest(raw["specRef"], "specRef"), raw["producerId"], raw["executorId"], raw["executionProfileDigest"], raw["toolchainBuildRecordRef"], raw["trustedGitExecutableDigest"], raw["trustedGitViewDigest"])


@dataclass(frozen=True, slots=True)
class Task26CompletionRecordV1:
    schema: str
    payload: Task26CompletionPayloadV1
    payload_digest: str
    broker_key_id: str
    broker_signature: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-task-completion-record/v1" or type(self.payload) is not Task26CompletionPayloadV1:
            raise ValueError("Task 26 completion record identity is invalid")
        if self.payload_digest != canonical_digest(self.payload.to_wire()):
            raise ValueError("Task 26 payload digest is invalid")
        _require_text(self.broker_key_id, "broker_key_id")
        _signature(self.broker_signature, "broker_signature")

    def to_wire(self) -> dict[str, object]:
        return {"brokerKeyId": self.broker_key_id, "brokerSignature": self.broker_signature, "payload": self.payload.to_wire(), "payloadDigest": self.payload_digest, "schema": self.schema}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "Task26CompletionRecordV1":
        raw = _strict_object(payload, {"brokerKeyId", "brokerSignature", "payload", "payloadDigest", "schema"}, "Task 26 completion record")
        return cls(raw["schema"], Task26CompletionPayloadV1._from_wire(raw["payload"]), raw["payloadDigest"], raw["brokerKeyId"], raw["brokerSignature"])


_POLICY_STATES = ("PREPARED", "QUARANTINED", "OBJECTS_PROMOTED", "REFS_INSTALLED", "HANDOFF_INSTALLED", "ACTIVATED")
_POLICY_NEXT = {"PREPARED": "QUARANTINE", "QUARANTINED": "PROMOTE_OBJECTS", "OBJECTS_PROMOTED": "INSTALL_REFS", "REFS_INSTALLED": "INSTALL_HANDOFF", "HANDOFF_INSTALLED": "ACTIVATE", "ACTIVATED": None, "FAILED_BLOCKED": None}
_POLICY_FIXED_REF = "refs/tags/trusted-acceptance/ticket-23-policy-v1"


@dataclass(frozen=True, slots=True)
class PolicyPublishTransactionJournalV1:
    schema: str
    operation_id: str
    completion_record_digest: str
    expected_old_head: GitObjectIdV1
    target_head_ref: str
    fixed_ref: str
    fixed_ref_must_be_absent: bool
    policy_commit: GitObjectIdV1 | None
    staged_payload_digest: str
    record_ref: str | None
    pointer_digest: str | None
    export_request_ref: str | None
    activation_marker_digest: str | None
    state: str
    pending_transition: str | None
    previous_entry_digest: str | None

    def __post_init__(self) -> None:
        if self.schema != "trusted-policy-publish-transaction/v1" or self.state not in {*_POLICY_STATES, "FAILED_BLOCKED"}:
            raise ValueError("policy journal identity is invalid")
        _require_text(self.operation_id, "operation_id")
        _require_digest(self.completion_record_digest, "completion_record_digest")
        if type(self.expected_old_head) is not GitObjectIdV1:
            raise ValueError("expected_old_head is invalid")
        _require_text(self.target_head_ref, "target_head_ref")
        if not self.target_head_ref.startswith("refs/") or self.fixed_ref != _POLICY_FIXED_REF or self.fixed_ref_must_be_absent is not True:
            raise ValueError("policy journal ref binding is invalid")
        if self.policy_commit is not None and type(self.policy_commit) is not GitObjectIdV1:
            raise ValueError("policy_commit is invalid")
        _require_digest(self.staged_payload_digest, "staged_payload_digest")
        for field in ("record_ref", "pointer_digest", "export_request_ref", "activation_marker_digest", "previous_entry_digest"):
            value = getattr(self, field)
            if value is not None:
                _require_digest(value, field)
        if self.pending_transition != _POLICY_NEXT[self.state]:
            raise ValueError("policy journal pending transition is invalid")
        closure = (self.record_ref, self.pointer_digest, self.export_request_ref)
        if any(value is not None for value in closure) and any(value is None for value in closure):
            raise ValueError("policy journal handoff closure is incomplete")
        if self.state == "PREPARED":
            if self.previous_entry_digest is not None or self.policy_commit is not None or any(value is not None for value in closure) or self.activation_marker_digest is not None:
                raise ValueError("prepared journal state is invalid")
        elif self.state in {"HANDOFF_INSTALLED", "ACTIVATED"} and any(value is None for value in closure):
            raise ValueError("published handoff closure is missing")
        if self.state == "ACTIVATED":
            if self.activation_marker_digest is None:
                raise ValueError("activated journal state needs marker")
        elif self.activation_marker_digest is not None:
            raise ValueError("activation marker is installed last")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"activationMarkerDigest": self.activation_marker_digest, "completionRecordDigest": self.completion_record_digest, "expectedOldHead": self.expected_old_head.to_wire(), "exportRequestRef": self.export_request_ref, "fixedRef": self.fixed_ref, "fixedRefMustBeAbsent": self.fixed_ref_must_be_absent, "operationId": self.operation_id, "pendingTransition": self.pending_transition, "pointerDigest": self.pointer_digest, "policyCommit": None if self.policy_commit is None else self.policy_commit.to_wire(), "previousEntryDigest": self.previous_entry_digest, "recordRef": self.record_ref, "schema": self.schema, "stagedPayloadDigest": self.staged_payload_digest, "state": self.state, "targetHeadRef": self.target_head_ref}

    @property
    def entry_digest(self) -> str:
        return canonical_digest(self._unsigned_wire())

    def to_wire(self) -> dict[str, object]:
        return {**self._unsigned_wire(), "entryDigest": self.entry_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "PolicyPublishTransactionJournalV1":
        fields = {"activationMarkerDigest", "completionRecordDigest", "entryDigest", "expectedOldHead", "exportRequestRef", "fixedRef", "fixedRefMustBeAbsent", "operationId", "pendingTransition", "pointerDigest", "policyCommit", "previousEntryDigest", "recordRef", "schema", "stagedPayloadDigest", "state", "targetHeadRef"}
        raw = _strict_object(payload, fields, "policy journal")
        result = cls(raw["schema"], raw["operationId"], raw["completionRecordDigest"], _oid(raw["expectedOldHead"], "expectedOldHead"), raw["targetHeadRef"], raw["fixedRef"], raw["fixedRefMustBeAbsent"], None if raw["policyCommit"] is None else _oid(raw["policyCommit"], "policyCommit"), raw["stagedPayloadDigest"], raw["recordRef"], raw["pointerDigest"], raw["exportRequestRef"], raw["activationMarkerDigest"], raw["state"], raw["pendingTransition"], raw["previousEntryDigest"])
        if raw["entryDigest"] != result.entry_digest:
            raise ValueError("policy journal entry digest is invalid")
        return result

    @classmethod
    def validate_forward_sequence(cls, entries: object, marker: object) -> None:
        from .handoffs import PolicyActivationMarkerV1
        if isinstance(entries, (str, bytes)) or not isinstance(entries, (tuple, list)) or tuple(type(item) for item in entries) != (cls,) * len(entries):
            raise ValueError("policy journal sequence is invalid")
        values = tuple(entries)
        if tuple(item.state for item in values) != _POLICY_STATES:
            raise ValueError("policy journal must contain fixed forward sequence")
        first = values[0]
        for prior, current in zip(values, values[1:]):
            if current.previous_entry_digest != prior.entry_digest or (current.operation_id, current.completion_record_digest, current.expected_old_head, current.target_head_ref, current.fixed_ref, current.fixed_ref_must_be_absent, current.staged_payload_digest) != (first.operation_id, first.completion_record_digest, first.expected_old_head, first.target_head_ref, first.fixed_ref, first.fixed_ref_must_be_absent, first.staged_payload_digest):
                raise ValueError("policy journal chain binding is invalid")
        if type(marker) is not PolicyActivationMarkerV1:
            raise ValueError("policy activation marker is invalid")
        active = values[-1]
        if active.activation_marker_digest != canonical_digest(marker.to_wire()) or marker.operation_id != active.operation_id or marker.activation_intent_digest != values[-2].entry_digest or marker.policy_commit != active.policy_commit or marker.fixed_ref != active.fixed_ref or (marker.record_ref, marker.pointer_digest, marker.export_request_ref) != (active.record_ref, active.pointer_digest, active.export_request_ref):
            raise ValueError("policy activation marker does not bind journal")
