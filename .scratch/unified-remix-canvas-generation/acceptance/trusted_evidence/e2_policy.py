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
