"""Strict, immutable v2 replay-policy primitives.

This module intentionally owns only portable codecs.  Linux broker and
production-runtime capabilities are introduced by their dedicated tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import base64
import os
import re
import stat
import subprocess
import unicodedata
import fcntl
import socket
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType


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


def _task_output_policy(policy_bytes: bytes) -> dict[str, object]:
    """Decode the fixed Task 28/29 policy without accepting caller extensions."""
    raw = _strict_object(policy_bytes, {"protectedPaths", "schema", "task29ContractPath", "tasks"}, "task output policy")
    if raw["schema"] != "trusted-task-output-policy/v1" or not isinstance(raw["protectedPaths"], list) or not isinstance(raw["tasks"], list):
        raise ValueError("task output policy shape is invalid")
    task29_contract_path = _relative_path(raw["task29ContractPath"], "Task 29 contract path")

    protected: list[tuple[str, str]] = []
    for item in raw["protectedPaths"]:
        if not isinstance(item, dict) or set(item) != {"repositoryRelativePath", "role"} or item["role"] != "protected":
            raise ValueError("protected path policy is invalid")
        protected.append((_relative_path(item["repositoryRelativePath"], "protected path"), item["role"]))
    if not protected or protected != sorted(protected) or len(set(protected)) != len(protected):
        raise ValueError("protected paths must be non-empty and ordered")

    tasks: list[dict[str, object]] = []
    for task in raw["tasks"]:
        if not isinstance(task, dict) or set(task) != {"phase", "sourceDirtyInputs", "task", "transitions"}:
            raise ValueError("task output policy task is invalid")
        if type(task["task"]) is not int or _PHASES.get(task["task"]) != task["phase"] or not isinstance(task["sourceDirtyInputs"], list) or not isinstance(task["transitions"], list):
            raise ValueError("task output policy task identity is invalid")
        dirty: list[tuple[str, str]] = []
        for item in task["sourceDirtyInputs"]:
            if not isinstance(item, dict) or set(item) != {"repositoryRelativePath", "role"}:
                raise ValueError("source dirty policy is invalid")
            dirty.append((_relative_path(item["repositoryRelativePath"], "source dirty path"), _require_text(item["role"], "source dirty role")))
        transitions: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
        for item in task["transitions"]:
            if not isinstance(item, dict):
                raise ValueError("transition policy is invalid")
            if set(item) == {"allowedTransitions", "repositoryRelativePath", "role"} and isinstance(item["allowedTransitions"], list):
                path = _relative_path(item["repositoryRelativePath"], "transition path")
                options_wire = item["allowedTransitions"]
            elif task["task"] == 29 and set(item) == {"allowedTransition", "precondition", "role"} and item["role"] == "contract":
                path = task29_contract_path
                options_wire = [{"allowedTransition": item["allowedTransition"], "precondition": item["precondition"]}]
            else:
                raise ValueError("transition policy is invalid")
            allowed: list[tuple[str, str]] = []
            for option in options_wire:
                if not isinstance(option, dict) or set(option) != {"allowedTransition", "precondition"}:
                    raise ValueError("transition policy option is invalid")
                allowed.append((_require_text(option["precondition"], "transition precondition"), _require_text(option["allowedTransition"], "allowed transition")))
            options = tuple(allowed)
            if not options or len(set(options)) != len(options):
                raise ValueError("transition policy options must be unique")
            transitions.append((path, _require_text(item["role"], "transition role"), options))
        if dirty != sorted(dirty) or len(set(dirty)) != len(dirty) or transitions != sorted(transitions) or len({item[0] for item in transitions}) != len(transitions):
            raise ValueError("task output policy entries must be uniquely ordered")
        tasks.append({"phase": task["phase"], "task": task["task"], "sourceDirtyInputs": tuple(dirty), "transitions": tuple(transitions)})
    if tuple(item["task"] for item in tasks) != (28, 29):
        raise ValueError("task output policy must freeze Task 28 and Task 29")
    return {"protectedPaths": tuple(protected), "tasks": tuple(tasks)}


def _git_read(repository: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(("git", *arguments), cwd=repository, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Git repository inspection failed") from error
    return result.stdout


def _repository_regular_file(repository: Path, relative_path: str, *, required: bool) -> Path | None:
    current = repository
    for component in relative_path.split("/"):
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if required:
                raise ValueError("required repository path is absent")
            return None
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("repository path contains a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("repository path is not a regular file")
    return current


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob_digest(repository: Path, base_commit: GitObjectIdV1, relative_path: str) -> str | None:
    result = subprocess.run(
        ("git", "cat-file", "-e", f"{base_commit.hex}:{relative_path}"), cwd=repository,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    return "sha256:" + hashlib.sha256(_git_read(repository, "show", f"{base_commit.hex}:{relative_path}")).hexdigest()


def _git_worktree_changes(repository: Path) -> dict[str, str]:
    raw = _git_read(repository, "diff", "--name-status", "-z", "-M")
    values = raw.split(b"\0")
    changes: dict[str, str] = {}
    position = 0
    while position < len(values) - 1:
        status = values[position].decode("utf-8")
        position += 1
        if status.startswith(("R", "C")):
            raise ValueError("renames and copies are forbidden")
        if position >= len(values) or status not in {"M", "A", "D", "T"}:
            raise ValueError("worktree change is invalid")
        path = values[position].decode("utf-8")
        position += 1
        _relative_path(path, "Git worktree path")
        if status != "M":
            raise ValueError("deletes and type changes are forbidden")
        if path in changes:
            raise ValueError("duplicate Git worktree path")
        changes[path] = "modified"
    for raw_path in _git_read(repository, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")[:-1]:
        path = raw_path.decode("utf-8")
        _relative_path(path, "Git untracked path")
        if path in changes:
            raise ValueError("worktree path is both tracked and untracked")
        changes[path] = "untracked"
    return changes


def validate_task_output_repository_state(
    repository_root: str | Path,
    allowed_outputs: AllowedTaskOutputsV1,
    policy_bytes: bytes,
    candidate_paths: Mapping[str, str | Path],
    *,
    expected_allowed_outputs_digest: str,
) -> None:
    """Validate Task 28/29's real Git pre-state without mutating it.

    The caller supplies only already-held candidate paths.  Every mutable
    repository value is checked against the immutable manifest and canonical
    policy; no current observation can widen either authority.
    """
    repository = Path(repository_root)
    if not repository.is_absolute() or repository.is_symlink() or not repository.is_dir():
        raise ValueError("repository root must be an absolute non-symlink directory")
    _require_digest(expected_allowed_outputs_digest, "expected_allowed_outputs_digest")
    if allowed_outputs.canonical_digest != expected_allowed_outputs_digest:
        raise ValueError("allowed task outputs were refreshed or substituted")
    policy = _task_output_policy(policy_bytes)
    if allowed_outputs.task_output_policy_digest != "sha256:" + hashlib.sha256(policy_bytes).hexdigest() or allowed_outputs.protected_paths_digest != canonical_digest([{"repositoryRelativePath": path, "role": role} for path, role in policy["protectedPaths"]]):
        raise ValueError("task output policy digest is not bound to the manifest")
    task_policy = next((item for item in policy["tasks"] if item["task"] == allowed_outputs.task_id), None)
    if task_policy is None or task_policy["phase"] != allowed_outputs.phase:
        raise ValueError("task output phase is not allowed by policy")
    planned_dirty = tuple(item.repository_relative_path for item in allowed_outputs.source_dirty_inputs)
    planned_transitions = tuple((item.repository_relative_path, item.output_kind, item.precondition, item.allowed_transition) for item in allowed_outputs.transitions)
    allowed_transitions = {path: (role, options) for path, role, options in task_policy["transitions"]}
    if planned_dirty != tuple(item[0] for item in task_policy["sourceDirtyInputs"]) or set(item[0] for item in planned_transitions) != set(allowed_transitions) or any(allowed_transitions[path][0] != role or (precondition, transition) not in allowed_transitions[path][1] for path, role, precondition, transition in planned_transitions):
        raise ValueError("manifest paths or transitions exceed frozen policy")
    protected_paths = {item[0] for item in policy["protectedPaths"]}
    if protected_paths & (set(planned_dirty) | {item[0] for item in planned_transitions}):
        raise ValueError("protected paths cannot be mutable manifest paths")
    if _git_read(repository, "rev-parse", "--verify", "HEAD").decode("ascii").strip() != allowed_outputs.base_commit.hex:
        raise ValueError("repository HEAD is not the fixed base commit")
    if subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=repository).returncode != 0 or _git_read(repository, "ls-files", "-u"):
        raise ValueError("staged or unmerged index changes are forbidden")
    changes = _git_worktree_changes(repository)
    if set(changes) != set(planned_dirty):
        raise ValueError("worktree changes are not exactly the manifest source dirty set")
    for allowance in allowed_outputs.source_dirty_inputs:
        if changes[allowance.repository_relative_path] != allowance.expected_status:
            raise ValueError("source dirty status changed")
        source = _repository_regular_file(repository, allowance.repository_relative_path, required=True)
        if _file_digest(source) != allowance.expected_content_digest:
            raise ValueError("source dirty content changed")
    if allowed_outputs.task_id == 28:
        if set(candidate_paths) != set(allowed_outputs.candidate_refs):
            raise ValueError("candidate path set is not closed")
        held = {item.candidate_ref: item for item in allowed_outputs.staged_candidates}
        for ref in allowed_outputs.candidate_refs:
            path = Path(candidate_paths[ref])
            if path.is_symlink() or not path.is_file():
                raise ValueError("candidate is not a regular held file")
            metadata = path.stat()
            identity = held.get(ref)
            if identity is None or (metadata.st_dev, metadata.st_ino, metadata.st_size, _file_digest(path)) != (identity.device, identity.inode, identity.byte_length, identity.content_digest):
                raise ValueError("candidate identity drifted")
    elif candidate_paths:
        raise ValueError("Task 29 must not receive candidate files")
    for transition in allowed_outputs.transitions:
        current = _repository_regular_file(repository, transition.repository_relative_path, required=False)
        base_digest = _git_blob_digest(repository, allowed_outputs.base_commit, transition.repository_relative_path)
        if transition.allowed_transition == "create-or-resume-exact-cas":
            if current is not None or base_digest is not None:
                raise ValueError("new candidate CAS must be absent")
        elif transition.allowed_transition == "retain-exact-cas":
            if current is None or base_digest != transition.expected_pre_digest or _file_digest(current) != base_digest:
                raise ValueError("retained candidate CAS drifted from fixed base bytes")
        elif transition.precondition == "must-not-exist":
            if current is not None or base_digest is not None:
                raise ValueError("create output must be absent")
        elif transition.precondition == "equals-base-bytes":
            if current is None or base_digest != transition.expected_pre_digest or _file_digest(current) != base_digest:
                raise ValueError("base-byte output precondition failed")
        else:
            raise ValueError("unsupported output transition")


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
    __slots__ = ("_fds", "directory_identities", "_entry_bindings", "_mount_identities")
    _TOKEN = object()

    def __init__(
        self,
        token: object,
        descriptors: tuple[int, ...],
        entry_bindings: tuple[tuple[int, str, tuple[int, int]], ...],
        mount_identities: tuple[object, ...],
    ) -> None:
        if token is not self._TOKEN:
            raise TypeError("use open_precreated with directory file descriptors")
        self._fds = descriptors
        self.directory_identities = tuple((os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in descriptors)
        self._entry_bindings = entry_bindings
        self._mount_identities = mount_identities

    @staticmethod
    def _fd_path(fd: int) -> str:
        try:
            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)
            path = os.fsdecode(raw.split(b"\0", 1)[0])
        except (AttributeError, OSError, ValueError):
            try:
                path = os.readlink(f"/proc/self/fd/{fd}")
            except OSError as error:
                raise ValueError("precreated directory path is unavailable") from error
        if not os.path.isabs(path) or not path or path.endswith(" (deleted)"):
            raise ValueError("precreated directory path is unavailable")
        return path

    @staticmethod
    def _directory_metadata(fd: int) -> tuple[tuple[int, int], object]:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("precreated descriptor is not a directory")
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("precreated directory owner or mode is unsafe")
        mount_identity = getattr(os.fstatvfs(fd), "f_fsid", None)
        if mount_identity is None:
            raise ValueError("precreated directory mount identity is unavailable")
        return (info.st_dev, info.st_ino), mount_identity

    @classmethod
    def _bind_child(
        cls,
        parent_path: str,
        child_path: str,
        parent_fd_index: int,
        child_identity: tuple[int, int],
    ) -> tuple[int, str, tuple[int, int]]:
        if os.path.dirname(child_path) != parent_path:
            raise ValueError("precreated directory relationship is invalid")
        name = os.path.basename(child_path)
        if not name or name in {".", ".."}:
            raise ValueError("precreated directory entry is invalid")
        return parent_fd_index, name, child_identity

    @classmethod
    def open_precreated(cls, transaction_parent_fd: int, transaction_root_fd: int, operation_directory_fd: int, repository_fd: int) -> "RegistrationTransactionCapabilityV1":
        supplied = (transaction_parent_fd, transaction_root_fd, operation_directory_fd, repository_fd)
        if not all(type(fd) is int for fd in supplied):
            raise ValueError("precreated directory descriptors are required")
        copies: list[int] = []
        try:
            identities: list[tuple[int, int]] = []
            mount_identities: list[object] = []
            paths: list[str] = []
            for fd in supplied:
                identity, mount_identity = cls._directory_metadata(fd)
                identities.append(identity)
                mount_identities.append(mount_identity)
                paths.append(cls._fd_path(fd))
                copies.append(os.dup(fd))
            if len(set(identities)) != 4:
                raise ValueError("transaction and repository directories must have distinct identities")
            if len(set(mount_identities)) != 1:
                raise ValueError("precreated directories cross a mount boundary")
            bindings = (
                cls._bind_child(paths[0], paths[1], 0, identities[1]),
                cls._bind_child(paths[1], paths[2], 1, identities[2]),
                cls._bind_child(paths[2], paths[3], 2, identities[3]),
            )
            return cls(cls._TOKEN, tuple(copies), bindings, tuple(mount_identities))
        except (OSError, TypeError, ValueError) as error:
            for copied in copies:
                os.close(copied)
            raise ValueError("precreated directory validation failed") from error

    def validate_open_identities(self) -> None:
        try:
            current = tuple(self._directory_metadata(fd) for fd in self._fds)
            if tuple(identity for identity, _ in current) != self.directory_identities:
                raise ValueError("directory descriptor identity changed")
            if tuple(mount for _, mount in current) != self._mount_identities:
                raise ValueError("directory mount identity changed")
            for parent_index, name, expected_identity in self._entry_bindings:
                entry = os.stat(name, dir_fd=self._fds[parent_index], follow_symlinks=False)
                if not stat.S_ISDIR(entry.st_mode) or (entry.st_dev, entry.st_ino) != expected_identity:
                    raise ValueError("directory relationship identity changed")
        except OSError as error:
            raise ValueError("directory relationship validation failed") from error

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
    operation_id: str
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
    broker_auth_public_key_digest: str
    broker_auth_registry_generation: int
    broker_auth_registry_digest: str
    broker_auth_revocation_digest: str
    broker_auth_allowed_signers_digest: str
    broker_auth_sign_sequence: int

    def __post_init__(self) -> None:
        if self.schema != "trusted-broker-lifecycle-proof/v1" or type(self.subject_commit) is not ProductGitObjectIdV1:
            raise ValueError("lifecycle proof identity is invalid")
        for field in ("broker_key_id", "operation_id", "lifecycle_id", "lease_id"):
            _require_text(getattr(self, field), field)
        staged = _ordered(self.staged_artifact_digests, lambda item: (item.name.encode("utf-8"), item.digest.encode("ascii")), "staged_artifact_digests")
        if not staged or not all(type(item) is NamedDigest for item in staged) or not any(item.name == "record-payload" for item in staged):
            raise ValueError("lifecycle proof must bind record payload and staged artifacts")
        for field in ("producer_exit_digest", "destroyed_resources_digest", "shared_control_baseline_digest", "shared_object_store_baseline_digest", "proof_payload_digest"):
            _require_digest(getattr(self, field), field)
        for field in ("broker_auth_public_key_digest", "broker_auth_registry_digest", "broker_auth_revocation_digest", "broker_auth_allowed_signers_digest"):
            _require_digest(getattr(self, field), field)
        _u64(self.broker_auth_registry_generation, "broker_auth_registry_generation", positive=True)
        _u64(self.broker_auth_sign_sequence, "broker_auth_sign_sequence", positive=True)
        _signature(self.broker_signature, "broker_signature")
        object.__setattr__(self, "staged_artifact_digests", staged)

    def to_wire(self) -> dict[str, object]:
        return {**self.unsigned_wire(), "brokerSignature": self.broker_signature}

    def unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthAllowedSignersDigest": self.broker_auth_allowed_signers_digest, "brokerAuthPublicKeyDigest": self.broker_auth_public_key_digest, "brokerAuthRegistryDigest": self.broker_auth_registry_digest, "brokerAuthRegistryGeneration": self.broker_auth_registry_generation, "brokerAuthRevocationDigest": self.broker_auth_revocation_digest, "brokerAuthSignSequence": self.broker_auth_sign_sequence, "brokerKeyId": self.broker_key_id, "destroyedResourcesDigest": self.destroyed_resources_digest, "leaseId": self.lease_id, "lifecycleId": self.lifecycle_id, "operationId": self.operation_id, "producerExitDigest": self.producer_exit_digest, "proofPayloadDigest": self.proof_payload_digest, "schema": self.schema, "sharedControlBaselineDigest": self.shared_control_baseline_digest, "sharedObjectStoreBaselineDigest": self.shared_object_store_baseline_digest, "stagedArtifactDigests": [_wire(item) for item in self.staged_artifact_digests], "subjectCommit": self.subject_commit.to_wire()}

    @property
    def unsigned_bytes(self) -> bytes:
        return canonical_json_bytes(self.unsigned_wire())

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

    def verify_signature(self, registry: "RegistrationBrokerAuthRegistryV1", revocations: "RegistrationBrokerAuthRevocationsV1", allowed_signers: Mapping[str, bytes], allowed_signers_digest: str, current_sequence: int) -> None:
        _verify_broker_signed_record(
            registry, revocations, allowed_signers, allowed_signers_digest, current_sequence,
            self.broker_key_id, self.broker_auth_public_key_digest,
            self.broker_auth_registry_generation, self.broker_auth_registry_digest,
            self.broker_auth_revocation_digest, self.broker_auth_allowed_signers_digest,
            self.broker_auth_sign_sequence, self.broker_signature,
            _BROKER_LIFECYCLE_PROOF_DOMAIN, self.unsigned_bytes,
        )

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerLifecycleProofV1":
        fields = {"brokerAuthAllowedSignersDigest", "brokerAuthPublicKeyDigest", "brokerAuthRegistryDigest", "brokerAuthRegistryGeneration", "brokerAuthRevocationDigest", "brokerAuthSignSequence", "brokerKeyId", "brokerSignature", "destroyedResourcesDigest", "leaseId", "lifecycleId", "operationId", "producerExitDigest", "proofPayloadDigest", "schema", "sharedControlBaselineDigest", "sharedObjectStoreBaselineDigest", "stagedArtifactDigests", "subjectCommit"}
        raw = _strict_object(payload, fields, "lifecycle proof")
        if not isinstance(raw["stagedArtifactDigests"], list):
            raise ValueError("lifecycle proof staged artifacts are invalid")
        return cls(raw["schema"], raw["brokerKeyId"], raw["operationId"], raw["lifecycleId"], raw["leaseId"], ProductGitObjectIdV1(**raw["subjectCommit"]), tuple(_named_digest(item, "staged artifact") for item in raw["stagedArtifactDigests"]), raw["producerExitDigest"], raw["destroyedResourcesDigest"], raw["sharedControlBaselineDigest"], raw["sharedObjectStoreBaselineDigest"], raw["proofPayloadDigest"], raw["brokerSignature"], raw["brokerAuthPublicKeyDigest"], raw["brokerAuthRegistryGeneration"], raw["brokerAuthRegistryDigest"], raw["brokerAuthRevocationDigest"], raw["brokerAuthAllowedSignersDigest"], raw["brokerAuthSignSequence"])


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
    operation_id: str
    lifecycle_id: str
    broker_signature: str
    broker_auth_public_key_digest: str
    broker_auth_registry_generation: int
    broker_auth_registry_digest: str
    broker_auth_revocation_digest: str
    broker_auth_allowed_signers_digest: str
    broker_auth_sign_sequence: int

    def __post_init__(self) -> None:
        if self.schema != "trusted-task-completion-record/v1" or type(self.payload) is not Task26CompletionPayloadV1:
            raise ValueError("Task 26 completion record identity is invalid")
        if self.payload_digest != canonical_digest(self.payload.to_wire()):
            raise ValueError("Task 26 payload digest is invalid")
        for field in ("broker_key_id", "operation_id", "lifecycle_id"):
            _require_text(getattr(self, field), field)
        for field in ("broker_auth_public_key_digest", "broker_auth_registry_digest", "broker_auth_revocation_digest", "broker_auth_allowed_signers_digest"):
            _require_digest(getattr(self, field), field)
        _u64(self.broker_auth_registry_generation, "broker_auth_registry_generation", positive=True)
        _u64(self.broker_auth_sign_sequence, "broker_auth_sign_sequence", positive=True)
        _signature(self.broker_signature, "broker_signature")

    def to_wire(self) -> dict[str, object]:
        return {**self.unsigned_wire(), "brokerSignature": self.broker_signature}

    def unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthAllowedSignersDigest": self.broker_auth_allowed_signers_digest, "brokerAuthPublicKeyDigest": self.broker_auth_public_key_digest, "brokerAuthRegistryDigest": self.broker_auth_registry_digest, "brokerAuthRegistryGeneration": self.broker_auth_registry_generation, "brokerAuthRevocationDigest": self.broker_auth_revocation_digest, "brokerAuthSignSequence": self.broker_auth_sign_sequence, "brokerKeyId": self.broker_key_id, "lifecycleId": self.lifecycle_id, "operationId": self.operation_id, "payload": self.payload.to_wire(), "payloadDigest": self.payload_digest, "schema": self.schema}

    @property
    def unsigned_bytes(self) -> bytes:
        return canonical_json_bytes(self.unsigned_wire())

    def verify_signature(self, registry: "RegistrationBrokerAuthRegistryV1", revocations: "RegistrationBrokerAuthRevocationsV1", allowed_signers: Mapping[str, bytes], allowed_signers_digest: str, current_sequence: int) -> None:
        _verify_broker_signed_record(
            registry, revocations, allowed_signers, allowed_signers_digest, current_sequence,
            self.broker_key_id, self.broker_auth_public_key_digest,
            self.broker_auth_registry_generation, self.broker_auth_registry_digest,
            self.broker_auth_revocation_digest, self.broker_auth_allowed_signers_digest,
            self.broker_auth_sign_sequence, self.broker_signature,
            _TASK26_COMPLETION_RECORD_DOMAIN, self.unsigned_bytes,
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "Task26CompletionRecordV1":
        raw = _strict_object(payload, {"brokerAuthAllowedSignersDigest", "brokerAuthPublicKeyDigest", "brokerAuthRegistryDigest", "brokerAuthRegistryGeneration", "brokerAuthRevocationDigest", "brokerAuthSignSequence", "brokerKeyId", "brokerSignature", "lifecycleId", "operationId", "payload", "payloadDigest", "schema"}, "Task 26 completion record")
        return cls(raw["schema"], Task26CompletionPayloadV1._from_wire(raw["payload"]), raw["payloadDigest"], raw["brokerKeyId"], raw["operationId"], raw["lifecycleId"], raw["brokerSignature"], raw["brokerAuthPublicKeyDigest"], raw["brokerAuthRegistryGeneration"], raw["brokerAuthRegistryDigest"], raw["brokerAuthRevocationDigest"], raw["brokerAuthAllowedSignersDigest"], raw["brokerAuthSignSequence"])


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


# E.1 broker-auth records are portable contracts only.  Their persistence,
# process spawning and repository mutation executor deliberately belong to the
# later root-broker slice.
_BROKER_AUTH_PURPOSE = "registration-broker-auth"
_BROKER_ENVELOPE_DOMAIN = "ai-video-reverse-engineer/registration-broker-envelope/v1"
_BROKER_LEASE_DOMAIN = "ai-video-reverse-engineer/registration-broker-lease/v1"
_BROKER_CHALLENGE_DOMAIN = "ai-video-reverse-engineer/registration-broker-startup-challenge/v1"
_BROKER_LIFECYCLE_PROOF_DOMAIN = "ai-video-reverse-engineer/broker-lifecycle-proof/v1"
_TASK26_COMPLETION_RECORD_DOMAIN = "ai-video-reverse-engineer/task26-completion-record/v1"
_LEASE_IDENTITY_DOMAIN = "ai-video-reverse-engineer/registration-recovery-lease-identity/v1"
_U64_MAX = 2**64 - 1


def _u64(value: object, field: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > _U64_MAX:
        raise ValueError(f"{field} must be a canonical u64")
    return value


def _mac(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{field} must be lower-case HMAC-SHA256 hex")
    return value


def _broker_from_bytes(payload: bytes, fields: set[str], label: str) -> dict[str, object]:
    return _strict_object(payload, fields, label)


@dataclass(frozen=True, slots=True)
class RegistrationBrokerAuthKeyRegistrationV1(_SelfDigestRecord):
    schema: str
    key_id: str
    purpose: str
    signature_domain: str
    lease_signature_domain: str
    challenge_domain: str
    public_key_encoding: str
    public_key_digest: str
    public_key_fingerprint: str
    certificate_wrapper_digest: str | None
    resolved_leaf_public_key_digest: str
    resolved_leaf_public_key_fingerprint: str
    allowed_signers_entry_digest: str
    state: str
    effective_lease_sequence: int
    retire_after_lease_sequence: int | None

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-key-registration/v1" or (self.purpose, self.signature_domain, self.lease_signature_domain, self.challenge_domain, self.public_key_encoding) != (_BROKER_AUTH_PURPOSE, _BROKER_ENVELOPE_DOMAIN, _BROKER_LEASE_DOMAIN, _BROKER_CHALLENGE_DOMAIN, "raw-ed25519-32"):
            raise ValueError("broker auth key registration identity is invalid")
        _require_text(self.key_id, "key_id")
        for field in ("public_key_digest", "public_key_fingerprint", "resolved_leaf_public_key_digest", "resolved_leaf_public_key_fingerprint", "allowed_signers_entry_digest"):
            _require_digest(getattr(self, field), field)
        if self.certificate_wrapper_digest is not None:
            _require_digest(self.certificate_wrapper_digest, "certificate_wrapper_digest")
        if self.public_key_fingerprint != self.public_key_digest or self.resolved_leaf_public_key_fingerprint != self.resolved_leaf_public_key_digest:
            raise ValueError("broker auth public key fingerprint is invalid")
        if self.state not in {"active", "retiring", "revoked"}:
            raise ValueError("broker auth key state is invalid")
        _u64(self.effective_lease_sequence, "effective_lease_sequence", positive=True)
        if self.state == "active" and self.retire_after_lease_sequence is not None:
            raise ValueError("active broker key cannot retire")
        if self.state != "active":
            _u64(self.retire_after_lease_sequence, "retire_after_lease_sequence", positive=True)
            if self.retire_after_lease_sequence < self.effective_lease_sequence:  # type: ignore[operator]
                raise ValueError("broker key retirement precedes activation")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"allowedSignersEntryDigest": self.allowed_signers_entry_digest, "certificateWrapperDigest": self.certificate_wrapper_digest, "challengeDomain": self.challenge_domain, "effectiveLeaseSequence": self.effective_lease_sequence, "keyId": self.key_id, "leaseSignatureDomain": self.lease_signature_domain, "publicKeyDigest": self.public_key_digest, "publicKeyEncoding": self.public_key_encoding, "publicKeyFingerprint": self.public_key_fingerprint, "purpose": self.purpose, "resolvedLeafPublicKeyDigest": self.resolved_leaf_public_key_digest, "resolvedLeafPublicKeyFingerprint": self.resolved_leaf_public_key_fingerprint, "retireAfterLeaseSequence": self.retire_after_lease_sequence, "schema": self.schema, "signatureDomain": self.signature_domain, "state": self.state}

    @classmethod
    def _from_wire(cls, raw: object) -> "RegistrationBrokerAuthKeyRegistrationV1":
        fields = {"allowedSignersEntryDigest", "canonicalDigest", "certificateWrapperDigest", "challengeDomain", "effectiveLeaseSequence", "keyId", "leaseSignatureDomain", "publicKeyDigest", "publicKeyEncoding", "publicKeyFingerprint", "purpose", "resolvedLeafPublicKeyDigest", "resolvedLeafPublicKeyFingerprint", "retireAfterLeaseSequence", "schema", "signatureDomain", "state"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("broker auth key registration wire is invalid")
        item = cls(raw["schema"], raw["keyId"], raw["purpose"], raw["signatureDomain"], raw["leaseSignatureDomain"], raw["challengeDomain"], raw["publicKeyEncoding"], raw["publicKeyDigest"], raw["publicKeyFingerprint"], raw["certificateWrapperDigest"], raw["resolvedLeafPublicKeyDigest"], raw["resolvedLeafPublicKeyFingerprint"], raw["allowedSignersEntryDigest"], raw["state"], raw["effectiveLeaseSequence"], raw["retireAfterLeaseSequence"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth key registration digest is invalid")
        return item

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationBrokerAuthKeyRegistrationV1":
        return cls._from_wire(_broker_from_bytes(payload, {"allowedSignersEntryDigest", "canonicalDigest", "certificateWrapperDigest", "challengeDomain", "effectiveLeaseSequence", "keyId", "leaseSignatureDomain", "publicKeyDigest", "publicKeyEncoding", "publicKeyFingerprint", "purpose", "resolvedLeafPublicKeyDigest", "resolvedLeafPublicKeyFingerprint", "retireAfterLeaseSequence", "schema", "signatureDomain", "state"}, "broker auth key registration"))


@dataclass(frozen=True, slots=True)
class RegistrationBrokerAuthRegistryV1(_SelfDigestRecord):
    schema: str
    generation: int
    previous_registry_digest: str | None
    active_key_id: str
    keys: tuple[RegistrationBrokerAuthKeyRegistrationV1, ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-registry/v1":
            raise ValueError("broker auth registry schema is invalid")
        _u64(self.generation, "generation", positive=True)
        _require_text(self.active_key_id, "active_key_id")
        if self.generation == 1:
            if self.previous_registry_digest is not None:
                raise ValueError("first registry cannot have a predecessor")
        else:
            _require_digest(self.previous_registry_digest, "previous_registry_digest")
        keys = _ordered(self.keys, lambda item: item.key_id.encode("utf-8"), "keys")
        if not keys or not all(type(item) is RegistrationBrokerAuthKeyRegistrationV1 for item in keys):
            raise ValueError("broker auth registry keys are invalid")
        active = tuple(item for item in keys if item.state == "active")
        if len(active) != 1 or active[0].key_id != self.active_key_id:
            raise ValueError("broker auth registry needs one declared active key")
        object.__setattr__(self, "keys", keys)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"activeKeyId": self.active_key_id, "generation": self.generation, "keys": [_wire(item.to_wire()) for item in self.keys], "previousRegistryDigest": self.previous_registry_digest, "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationBrokerAuthRegistryV1":
        raw = _broker_from_bytes(payload, {"activeKeyId", "canonicalDigest", "generation", "keys", "previousRegistryDigest", "schema"}, "broker auth registry")
        if not isinstance(raw["keys"], list):
            raise ValueError("broker auth registry keys are invalid")
        item = cls(raw["schema"], raw["generation"], raw["previousRegistryDigest"], raw["activeKeyId"], tuple(RegistrationBrokerAuthKeyRegistrationV1._from_wire(key) for key in raw["keys"]))
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth registry digest is invalid")
        return item

    def key(self, key_id: str) -> RegistrationBrokerAuthKeyRegistrationV1:
        result = next((item for item in self.keys if item.key_id == key_id), None)
        if result is None:
            raise ValueError("broker auth key is unknown")
        return result


@dataclass(frozen=True, slots=True)
class RegistrationBrokerAuthRevocationEntryV1(_SelfDigestRecord):
    key_id: str
    revoked_at_broker_sequence: int
    reason_digest: str

    def __post_init__(self) -> None:
        _require_text(self.key_id, "key_id")
        _u64(self.revoked_at_broker_sequence, "revoked_at_broker_sequence", positive=True)
        _require_digest(self.reason_digest, "reason_digest")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"keyId": self.key_id, "reasonDigest": self.reason_digest, "revokedAtBrokerSequence": self.revoked_at_broker_sequence}

    @classmethod
    def _from_wire(cls, raw: object) -> "RegistrationBrokerAuthRevocationEntryV1":
        if not isinstance(raw, dict) or set(raw) != {"canonicalDigest", "keyId", "reasonDigest", "revokedAtBrokerSequence"}:
            raise ValueError("broker auth revocation wire is invalid")
        item = cls(raw["keyId"], raw["revokedAtBrokerSequence"], raw["reasonDigest"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth revocation digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class RegistrationBrokerAuthRevocationsV1(_SelfDigestRecord):
    schema: str
    registry_digest: str
    entries: tuple[RegistrationBrokerAuthRevocationEntryV1, ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-revocations/v1":
            raise ValueError("broker auth revocations schema is invalid")
        _require_digest(self.registry_digest, "registry_digest")
        entries = _ordered(self.entries, lambda item: (item.revoked_at_broker_sequence, item.key_id.encode("utf-8")), "entries")
        if not all(type(item) is RegistrationBrokerAuthRevocationEntryV1 for item in entries):
            raise ValueError("broker auth revocation entries are invalid")
        if len({item.key_id for item in entries}) != len(entries):
            raise ValueError("broker auth revocation key IDs must be unique")
        object.__setattr__(self, "entries", entries)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"entries": [item.to_wire() for item in self.entries], "registryDigest": self.registry_digest, "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationBrokerAuthRevocationsV1":
        raw = _broker_from_bytes(payload, {"canonicalDigest", "entries", "registryDigest", "schema"}, "broker auth revocations")
        if not isinstance(raw["entries"], list):
            raise ValueError("broker auth revocations entries are invalid")
        item = cls(raw["schema"], raw["registryDigest"], tuple(RegistrationBrokerAuthRevocationEntryV1._from_wire(entry) for entry in raw["entries"]))
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth revocations digest is invalid")
        return item

    def revoked_at(self, key_id: str) -> int | None:
        item = next((entry for entry in self.entries if entry.key_id == key_id), None)
        return None if item is None else item.revoked_at_broker_sequence


def verify_registration_broker_governance(
    registry: RegistrationBrokerAuthRegistryV1,
    revocations: RegistrationBrokerAuthRevocationsV1,
    allowed_signers: Mapping[str, bytes],
) -> None:
    """Close broker public keys to one strict registry and revocation generation."""
    if type(registry) is not RegistrationBrokerAuthRegistryV1 or type(revocations) is not RegistrationBrokerAuthRevocationsV1:
        raise ValueError("broker governance records are invalid")
    if revocations.registry_digest != registry.canonical_digest:
        raise ValueError("broker revocations do not bind the registry")
    if not isinstance(allowed_signers, Mapping) or set(allowed_signers) != {key.key_id for key in registry.keys}:
        raise ValueError("broker allowed signers do not close the registry")
    if {entry.key_id for entry in revocations.entries} - {key.key_id for key in registry.keys}:
        raise ValueError("broker revocations contain an unknown key")
    for key in registry.keys:
        public_key = allowed_signers[key.key_id]
        if not isinstance(public_key, bytes) or len(public_key) != 32 or "sha256:" + hashlib.sha256(public_key).hexdigest() != key.public_key_digest:
            raise ValueError("broker allowed signer is invalid")


class RegistrationBrokerStateMacCapabilityV1:
    """Root-only state-MAC material, injected separately from Ed25519 signing."""

    __slots__ = ("injected_fd_identity_digest", "_key", "_closed")
    _TOKEN = object()

    def __init__(self, token: object, key: bytes, identity: str) -> None:
        if token is not self._TOKEN:
            raise TypeError("state-MAC capabilities must be loaded from an already-open read-only FD")
        self._key, self.injected_fd_identity_digest, self._closed = key, identity, False

    @classmethod
    def load_from_readonly_fd(cls, descriptor: int) -> "RegistrationBrokerStateMacCapabilityV1":
        if type(descriptor) is not int:
            raise ValueError("state-MAC FD is invalid")
        try:
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            info = os.fstat(descriptor)
            if flags & os.O_ACCMODE != os.O_RDONLY or not stat.S_ISREG(info.st_mode):
                raise ValueError("state-MAC FD must be read-only regular file")
            copied = os.dup(descriptor)
            try:
                os.lseek(copied, 0, os.SEEK_SET)
                key = os.read(copied, 33)
            finally:
                os.close(copied)
        except OSError as error:
            raise ValueError("state-MAC FD is invalid") from error
        if len(key) != 32:
            raise ValueError("state-MAC key must be exactly 32 bytes")
        return cls(cls._TOKEN, key, canonical_digest({"device": info.st_dev, "inode": info.st_ino, "stateMacKeyDigest": "sha256:" + hashlib.sha256(key).hexdigest()}))

    def mac(self, payload: bytes) -> str:
        if self._closed or not isinstance(payload, bytes):
            raise ValueError("state-MAC capability is closed or payload is invalid")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, mac: str) -> None:
        if not hmac.compare_digest(self.mac(payload), _mac(mac, "broker_state_mac")):
            raise ValueError("broker state MAC is invalid")

    def close(self) -> None:
        self._key = b""
        self._closed = True

    def __copy__(self):
        raise TypeError("state-MAC capabilities are non-copyable")

    def __deepcopy__(self, memo):
        raise TypeError("state-MAC capabilities are non-copyable")

    def __reduce__(self):
        raise TypeError("state-MAC capabilities are non-serializable")


class RegistrationBrokerAuthGovernanceV1:
    """Canonical broker governance closure used by every lease authority API."""

    __slots__ = ("registry", "revocations", "allowed_signers", "allowed_signers_digest")
    _TOKEN = object()

    def __init__(self, token: object, registry: RegistrationBrokerAuthRegistryV1, revocations: RegistrationBrokerAuthRevocationsV1, allowed_signers: Mapping[str, bytes], allowed_signers_digest: str) -> None:
        if token is not self._TOKEN:
            raise TypeError("broker governance must be constructed from canonical governance bytes")
        self.registry, self.revocations = registry, revocations
        self.allowed_signers, self.allowed_signers_digest = MappingProxyType(dict(allowed_signers)), allowed_signers_digest

    @classmethod
    def from_canonical_bytes(
        cls, registry_bytes: bytes, revocations_bytes: bytes, allowed_signers_bytes: bytes,
    ) -> "RegistrationBrokerAuthGovernanceV1":
        registry = RegistrationBrokerAuthRegistryV1.from_canonical_bytes(registry_bytes)
        revocations = RegistrationBrokerAuthRevocationsV1.from_canonical_bytes(revocations_bytes)
        raw = _broker_from_bytes(allowed_signers_bytes, {"keys"}, "broker auth allowed signers")
        if canonical_json_bytes(raw) != allowed_signers_bytes or not isinstance(raw["keys"], list):
            raise ValueError("broker allowed signers must be canonical bytes")
        values: dict[str, bytes] = {}
        names: list[bytes] = []
        for entry in raw["keys"]:
            fields = {"keyId", "purpose", "signatureDomain", "rawPublicKey", "resolvedPublicKey", "wrapperIdentity"}
            if not isinstance(entry, dict) or set(entry) != fields or entry["purpose"] != _BROKER_AUTH_PURPOSE or entry["signatureDomain"] != _BROKER_ENVELOPE_DOMAIN:
                raise ValueError("broker allowed signer entry is invalid")
            key_id = _require_text(entry["keyId"], "keyId")
            try:
                raw_key = base64.b64decode(entry["rawPublicKey"], validate=True)
                leaf_key = base64.b64decode(entry["resolvedPublicKey"], validate=True)
            except (TypeError, ValueError) as error:
                raise ValueError("broker allowed signer public key is invalid") from error
            if len(raw_key) != 32 or len(leaf_key) != 32 or base64.b64encode(raw_key).decode("ascii") != entry["rawPublicKey"] or base64.b64encode(leaf_key).decode("ascii") != entry["resolvedPublicKey"]:
                raise ValueError("broker allowed signer public key is invalid")
            key = registry.key(key_id)
            entry_digest = canonical_digest(entry)
            if (
                key.allowed_signers_entry_digest != entry_digest
                or key.public_key_digest != "sha256:" + hashlib.sha256(raw_key).hexdigest()
                or key.resolved_leaf_public_key_digest != "sha256:" + hashlib.sha256(leaf_key).hexdigest()
                or key.certificate_wrapper_digest != "sha256:" + hashlib.sha256(_require_text(entry["wrapperIdentity"], "wrapperIdentity").encode("utf-8")).hexdigest()
            ):
                raise ValueError("broker allowed signer does not close registry identity")
            values[key_id] = leaf_key
            names.append(key_id.encode("utf-8"))
        if names != sorted(names) or len(values) != len(names):
            raise ValueError("broker allowed signer keys must be unique and ordered")
        verify_registration_broker_governance(registry, revocations, values)
        return cls(cls._TOKEN, registry, revocations, values, "sha256:" + hashlib.sha256(allowed_signers_bytes).hexdigest())


def _verify_broker_signed_record(
    registry: RegistrationBrokerAuthRegistryV1,
    revocations: RegistrationBrokerAuthRevocationsV1,
    allowed_signers: Mapping[str, bytes],
    allowed_signers_digest: str,
    current_sequence: int,
    key_id: str,
    public_key_digest: str,
    registry_generation: int,
    registry_digest: str,
    revocation_digest: str,
    recorded_allowed_signers_digest: str,
    sign_sequence: int,
    signature: str,
    domain: str,
    unsigned_bytes: bytes,
) -> None:
    """Verify a portable broker record before any later durable-write layer uses it."""
    _require_digest(allowed_signers_digest, "allowed_signers_digest")
    _u64(current_sequence, "current_sequence", positive=True)
    verify_registration_broker_governance(registry, revocations, allowed_signers)
    if (registry_generation, registry_digest, revocation_digest, recorded_allowed_signers_digest) != (
        registry.generation,
        registry.canonical_digest,
        revocations.canonical_digest,
        allowed_signers_digest,
    ):
        raise ValueError("broker record trust state changed")
    key = registry.key(key_id)
    if (
        key.purpose != _BROKER_AUTH_PURPOSE
        or key.state != "active"
        or key.public_key_digest != public_key_digest
        or sign_sequence < key.effective_lease_sequence
        or sign_sequence > current_sequence
    ):
        raise ValueError("broker record signing key is not eligible")
    revoked_at = revocations.revoked_at(key_id)
    if revoked_at is not None and current_sequence >= revoked_at:
        raise ValueError("broker record signing key is globally revoked")
    public_key = allowed_signers.get(key_id)
    if not isinstance(public_key, bytes) or len(public_key) != 32 or "sha256:" + hashlib.sha256(public_key).hexdigest() != public_key_digest:
        raise ValueError("broker record allowed signer is invalid")
    from .handoffs import _ed25519_verify
    if not _ed25519_verify(public_key, domain.encode("utf-8") + b"\0" + unsigned_bytes, bytes.fromhex(signature)):
        raise ValueError("broker record signature is invalid")


def _broker_identity_fields(value: object, field: str) -> str:
    return _require_digest(value, field)


@dataclass(frozen=True, slots=True)
class RegistrationWorkerSessionFamilyAuthorizationV1(_SelfDigestRecord):
    schema: str
    operation_id: str
    transaction_parent_identity_digest: str
    transaction_root_identity_digest: str
    operation_directory_identity_digest: str
    repository_identity_digest: str
    repository_boundary_identity_digest: str
    broker_auth_key_id: str
    broker_auth_key_registration_digest: str
    allowed_worker_modes: tuple[str, ...]
    allowed_mode_transition: str
    approved_launcher_family_digest: str
    trusted_toolchain_build_record_digest: str
    trusted_recovery_launcher_digest: str
    executor_identity_digest: str
    profile_identity_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-worker-session-family-authorization/v1" or tuple(self.allowed_worker_modes) != ("register", "recover") or self.allowed_mode_transition != "register-to-recover":
            raise ValueError("worker session family authorization is invalid")
        _require_text(self.operation_id, "operation_id"); _require_text(self.broker_auth_key_id, "broker_auth_key_id")
        for field in ("transaction_parent_identity_digest", "transaction_root_identity_digest", "operation_directory_identity_digest", "repository_identity_digest", "repository_boundary_identity_digest", "broker_auth_key_registration_digest", "approved_launcher_family_digest", "trusted_toolchain_build_record_digest", "trusted_recovery_launcher_digest", "executor_identity_digest", "profile_identity_digest"):
            _require_digest(getattr(self, field), field)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"allowedModeTransition": self.allowed_mode_transition, "allowedWorkerModes": list(self.allowed_worker_modes), "approvedLauncherFamilyDigest": self.approved_launcher_family_digest, "brokerAuthKeyId": self.broker_auth_key_id, "brokerAuthKeyRegistrationDigest": self.broker_auth_key_registration_digest, "executorIdentityDigest": self.executor_identity_digest, "operationDirectoryIdentityDigest": self.operation_directory_identity_digest, "operationId": self.operation_id, "profileIdentityDigest": self.profile_identity_digest, "repositoryBoundaryIdentityDigest": self.repository_boundary_identity_digest, "repositoryIdentityDigest": self.repository_identity_digest, "schema": self.schema, "transactionParentIdentityDigest": self.transaction_parent_identity_digest, "transactionRootIdentityDigest": self.transaction_root_identity_digest, "trustedRecoveryLauncherDigest": self.trusted_recovery_launcher_digest, "trustedToolchainBuildRecordDigest": self.trusted_toolchain_build_record_digest}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationWorkerSessionFamilyAuthorizationV1":
        fields = set(cls("trusted-registration-worker-session-family-authorization/v1", "x", *(["sha256:" + "0" * 64] * 5), "key", "sha256:" + "0" * 64, ("register", "recover"), "register-to-recover", *(["sha256:" + "0" * 64] * 5)).to_wire())
        raw = _broker_from_bytes(payload, fields, "worker session family authorization")
        item = cls(raw["schema"], raw["operationId"], raw["transactionParentIdentityDigest"], raw["transactionRootIdentityDigest"], raw["operationDirectoryIdentityDigest"], raw["repositoryIdentityDigest"], raw["repositoryBoundaryIdentityDigest"], raw["brokerAuthKeyId"], raw["brokerAuthKeyRegistrationDigest"], tuple(raw["allowedWorkerModes"]) if isinstance(raw["allowedWorkerModes"], list) else (), raw["allowedModeTransition"], raw["approvedLauncherFamilyDigest"], raw["trustedToolchainBuildRecordDigest"], raw["trustedRecoveryLauncherDigest"], raw["executorIdentityDigest"], raw["profileIdentityDigest"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("worker session family authorization digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryLeaseIdentityPreimageV1:
    schema: str; project_id: str; operation_id: str; lifecycle_nonce: str; session_family_authorization_digest: str; broker_auth_lease_request_digest: str; broker_auth_key_id: str; broker_auth_issue_entry_sequence: int; broker_auth_issue_entry_digest: str; broker_auth_effective_head_digest: str; broker_auth_registry_generation: int; broker_auth_registry_digest: str; broker_auth_revocation_digest: str; broker_auth_policy_digest: str; broker_auth_allowed_signers_digest: str; broker_auth_sequence_root_identity_digest: str; broker_auth_lease_expiry_sequence: int; transaction_parent_identity_digest: str; transaction_root_identity_digest: str; operation_directory_identity_digest: str; repository_identity_digest: str; repository_boundary_identity_digest: str; trusted_toolchain_build_record_digest: str; trusted_recovery_launcher_digest: str; executor_identity_digest: str; profile_identity_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-recovery-lease-identity-preimage/v1" or self.project_id != "ai-video-reverse-engineer":
            raise ValueError("lease identity preimage identity is invalid")
        for field in ("operation_id", "lifecycle_nonce", "broker_auth_key_id"):
            _require_text(getattr(self, field), field)
        for field in ("session_family_authorization_digest", "broker_auth_lease_request_digest", "broker_auth_issue_entry_digest", "broker_auth_effective_head_digest", "broker_auth_registry_digest", "broker_auth_revocation_digest", "broker_auth_policy_digest", "broker_auth_allowed_signers_digest", "broker_auth_sequence_root_identity_digest", "transaction_parent_identity_digest", "transaction_root_identity_digest", "operation_directory_identity_digest", "repository_identity_digest", "repository_boundary_identity_digest", "trusted_toolchain_build_record_digest", "trusted_recovery_launcher_digest", "executor_identity_digest", "profile_identity_digest"):
            _require_digest(getattr(self, field), field)
        for field in ("broker_auth_issue_entry_sequence", "broker_auth_registry_generation", "broker_auth_lease_expiry_sequence"):
            _u64(getattr(self, field), field, positive=True)

    def to_wire(self) -> dict[str, object]:
        return {"brokerAuthAllowedSignersDigest": self.broker_auth_allowed_signers_digest, "brokerAuthEffectiveHeadDigest": self.broker_auth_effective_head_digest, "brokerAuthIssueEntryDigest": self.broker_auth_issue_entry_digest, "brokerAuthIssueEntrySequence": self.broker_auth_issue_entry_sequence, "brokerAuthKeyId": self.broker_auth_key_id, "brokerAuthLeaseExpirySequence": self.broker_auth_lease_expiry_sequence, "brokerAuthLeaseRequestDigest": self.broker_auth_lease_request_digest, "brokerAuthPolicyDigest": self.broker_auth_policy_digest, "brokerAuthRegistryDigest": self.broker_auth_registry_digest, "brokerAuthRegistryGeneration": self.broker_auth_registry_generation, "brokerAuthRevocationDigest": self.broker_auth_revocation_digest, "brokerAuthSequenceRootIdentityDigest": self.broker_auth_sequence_root_identity_digest, "executorIdentityDigest": self.executor_identity_digest, "lifecycleNonce": self.lifecycle_nonce, "operationDirectoryIdentityDigest": self.operation_directory_identity_digest, "operationId": self.operation_id, "profileIdentityDigest": self.profile_identity_digest, "projectId": self.project_id, "repositoryBoundaryIdentityDigest": self.repository_boundary_identity_digest, "repositoryIdentityDigest": self.repository_identity_digest, "schema": self.schema, "sessionFamilyAuthorizationDigest": self.session_family_authorization_digest, "transactionParentIdentityDigest": self.transaction_parent_identity_digest, "transactionRootIdentityDigest": self.transaction_root_identity_digest, "trustedRecoveryLauncherDigest": self.trusted_recovery_launcher_digest, "trustedToolchainBuildRecordDigest": self.trusted_toolchain_build_record_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @property
    def lease_identity_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_LEASE_IDENTITY_DOMAIN.encode("utf-8") + b"\0" + self.canonical_bytes).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationRecoveryLeaseIdentityPreimageV1":
        fields = {"brokerAuthAllowedSignersDigest", "brokerAuthEffectiveHeadDigest", "brokerAuthIssueEntryDigest", "brokerAuthIssueEntrySequence", "brokerAuthKeyId", "brokerAuthLeaseExpirySequence", "brokerAuthLeaseRequestDigest", "brokerAuthPolicyDigest", "brokerAuthRegistryDigest", "brokerAuthRegistryGeneration", "brokerAuthRevocationDigest", "brokerAuthSequenceRootIdentityDigest", "executorIdentityDigest", "lifecycleNonce", "operationDirectoryIdentityDigest", "operationId", "profileIdentityDigest", "projectId", "repositoryBoundaryIdentityDigest", "repositoryIdentityDigest", "schema", "sessionFamilyAuthorizationDigest", "transactionParentIdentityDigest", "transactionRootIdentityDigest", "trustedRecoveryLauncherDigest", "trustedToolchainBuildRecordDigest"}
        raw = _broker_from_bytes(payload, fields, "lease identity preimage")
        return cls(raw["schema"], raw["projectId"], raw["operationId"], raw["lifecycleNonce"], raw["sessionFamilyAuthorizationDigest"], raw["brokerAuthLeaseRequestDigest"], raw["brokerAuthKeyId"], raw["brokerAuthIssueEntrySequence"], raw["brokerAuthIssueEntryDigest"], raw["brokerAuthEffectiveHeadDigest"], raw["brokerAuthRegistryGeneration"], raw["brokerAuthRegistryDigest"], raw["brokerAuthRevocationDigest"], raw["brokerAuthPolicyDigest"], raw["brokerAuthAllowedSignersDigest"], raw["brokerAuthSequenceRootIdentityDigest"], raw["brokerAuthLeaseExpirySequence"], raw["transactionParentIdentityDigest"], raw["transactionRootIdentityDigest"], raw["operationDirectoryIdentityDigest"], raw["repositoryIdentityDigest"], raw["repositoryBoundaryIdentityDigest"], raw["trustedToolchainBuildRecordDigest"], raw["trustedRecoveryLauncherDigest"], raw["executorIdentityDigest"], raw["profileIdentityDigest"])


@dataclass(frozen=True, slots=True)
class BrokerAuthLeaseIssueRequestV1(_SelfDigestRecord):
    schema: str; operation_id: str; lifecycle_nonce: str; session_family_authorization_digest: str; broker_auth_key_id: str; expected_current_sequence: int; expected_current_head_digest: str; expected_registry_digest: str; expected_revocation_digest: str; expected_policy_digest: str; expected_allowed_signers_digest: str; lease_ttl_sequences: int

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-lease-issue-request/v1":
            raise ValueError("broker auth lease request schema is invalid")
        for field in ("operation_id", "lifecycle_nonce", "broker_auth_key_id"):
            _require_text(getattr(self, field), field)
        for field in ("session_family_authorization_digest", "expected_current_head_digest", "expected_registry_digest", "expected_revocation_digest", "expected_policy_digest", "expected_allowed_signers_digest"):
            _require_digest(getattr(self, field), field)
        _u64(self.expected_current_sequence, "expected_current_sequence")
        _u64(self.lease_ttl_sequences, "lease_ttl_sequences", positive=True)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthKeyId": self.broker_auth_key_id, "expectedAllowedSignersDigest": self.expected_allowed_signers_digest, "expectedCurrentHeadDigest": self.expected_current_head_digest, "expectedCurrentSequence": self.expected_current_sequence, "expectedPolicyDigest": self.expected_policy_digest, "expectedRegistryDigest": self.expected_registry_digest, "expectedRevocationDigest": self.expected_revocation_digest, "leaseTtlSequences": self.lease_ttl_sequences, "lifecycleNonce": self.lifecycle_nonce, "operationId": self.operation_id, "schema": self.schema, "sessionFamilyAuthorizationDigest": self.session_family_authorization_digest}

    @property
    def request_digest(self) -> str:
        return self.canonical_digest

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthLeaseIssueRequestV1":
        fields = {"brokerAuthKeyId", "canonicalDigest", "expectedAllowedSignersDigest", "expectedCurrentHeadDigest", "expectedCurrentSequence", "expectedPolicyDigest", "expectedRegistryDigest", "expectedRevocationDigest", "leaseTtlSequences", "lifecycleNonce", "operationId", "schema", "sessionFamilyAuthorizationDigest"}
        raw = _broker_from_bytes(payload, fields, "broker auth lease request")
        item = cls(raw["schema"], raw["operationId"], raw["lifecycleNonce"], raw["sessionFamilyAuthorizationDigest"], raw["brokerAuthKeyId"], raw["expectedCurrentSequence"], raw["expectedCurrentHeadDigest"], raw["expectedRegistryDigest"], raw["expectedRevocationDigest"], raw["expectedPolicyDigest"], raw["expectedAllowedSignersDigest"], raw["leaseTtlSequences"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth lease request digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class BrokerAuthLeaseIssueEventPayloadV1(_SelfDigestRecord):
    schema: str; event_kind: str; event_id_digest: str; lease_request: BrokerAuthLeaseIssueRequestV1; lease_request_digest: str; operation_id: str; broker_auth_key_id: str; issued_sequence: int; lease_ttl_sequences: int; expiry_sequence: int; pre_policy_digest: str; post_policy_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-lease-issue-payload/v1" or self.event_kind != "issue-lease" or type(self.lease_request) is not BrokerAuthLeaseIssueRequestV1:
            raise ValueError("broker auth issue payload identity is invalid")
        for field in ("event_id_digest", "lease_request_digest", "pre_policy_digest", "post_policy_digest"):
            _require_digest(getattr(self, field), field)
        if (self.lease_request_digest, self.operation_id, self.broker_auth_key_id, self.lease_ttl_sequences) != (self.lease_request.request_digest, self.lease_request.operation_id, self.lease_request.broker_auth_key_id, self.lease_request.lease_ttl_sequences):
            raise ValueError("broker auth issue payload does not bind request")
        _u64(self.issued_sequence, "issued_sequence", positive=True); _u64(self.expiry_sequence, "expiry_sequence", positive=True)
        if self.expiry_sequence != _checked_add(self.issued_sequence, self.lease_ttl_sequences) or self.pre_policy_digest != self.post_policy_digest:
            raise ValueError("broker auth issue payload sequence or policy is invalid")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthKeyId": self.broker_auth_key_id, "eventIdDigest": self.event_id_digest, "eventKind": self.event_kind, "expirySequence": self.expiry_sequence, "issuedSequence": self.issued_sequence, "leaseRequest": self.lease_request.to_wire(), "leaseRequestDigest": self.lease_request_digest, "leaseTtlSequences": self.lease_ttl_sequences, "operationId": self.operation_id, "postPolicyDigest": self.post_policy_digest, "prePolicyDigest": self.pre_policy_digest, "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthLeaseIssueEventPayloadV1":
        fields = {"brokerAuthKeyId", "canonicalDigest", "eventIdDigest", "eventKind", "expirySequence", "issuedSequence", "leaseRequest", "leaseRequestDigest", "leaseTtlSequences", "operationId", "postPolicyDigest", "prePolicyDigest", "schema"}
        raw = _broker_from_bytes(payload, fields, "broker auth issue payload")
        request = BrokerAuthLeaseIssueRequestV1.from_canonical_bytes(canonical_json_bytes(raw["leaseRequest"]))
        item = cls(raw["schema"], raw["eventKind"], raw["eventIdDigest"], request, raw["leaseRequestDigest"], raw["operationId"], raw["brokerAuthKeyId"], raw["issuedSequence"], raw["leaseTtlSequences"], raw["expirySequence"], raw["prePolicyDigest"], raw["postPolicyDigest"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth issue payload digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class BrokerAuthEnvelopeSignEventPayloadV1(_SelfDigestRecord):
    schema: str; event_kind: str; event_id_digest: str; operation_id: str; lease_identity_digest: str; lease_request_digest: str; unsigned_envelope_digest: str; broker_auth_key_id: str; sign_sequence: int; pre_policy_digest: str; post_policy_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-envelope-sign-payload/v1" or self.event_kind != "sign-envelope":
            raise ValueError("broker auth envelope payload identity is invalid")
        for field in ("event_id_digest", "lease_identity_digest", "lease_request_digest", "unsigned_envelope_digest", "pre_policy_digest", "post_policy_digest"):
            _require_digest(getattr(self, field), field)
        _require_text(self.operation_id, "operation_id"); _require_text(self.broker_auth_key_id, "broker_auth_key_id"); _u64(self.sign_sequence, "sign_sequence", positive=True)
        if self.pre_policy_digest != self.post_policy_digest:
            raise ValueError("envelope signing cannot change policy")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthKeyId": self.broker_auth_key_id, "eventIdDigest": self.event_id_digest, "eventKind": self.event_kind, "leaseIdentityDigest": self.lease_identity_digest, "leaseRequestDigest": self.lease_request_digest, "operationId": self.operation_id, "postPolicyDigest": self.post_policy_digest, "prePolicyDigest": self.pre_policy_digest, "schema": self.schema, "signSequence": self.sign_sequence, "unsignedEnvelopeDigest": self.unsigned_envelope_digest}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthEnvelopeSignEventPayloadV1":
        fields = {"brokerAuthKeyId", "canonicalDigest", "eventIdDigest", "eventKind", "leaseIdentityDigest", "leaseRequestDigest", "operationId", "postPolicyDigest", "prePolicyDigest", "schema", "signSequence", "unsignedEnvelopeDigest"}
        raw = _broker_from_bytes(payload, fields, "broker auth envelope payload")
        item = cls(raw["schema"], raw["eventKind"], raw["eventIdDigest"], raw["operationId"], raw["leaseIdentityDigest"], raw["leaseRequestDigest"], raw["unsignedEnvelopeDigest"], raw["brokerAuthKeyId"], raw["signSequence"], raw["prePolicyDigest"], raw["postPolicyDigest"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth envelope payload digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class BrokerAuthRotatePolicyEventPayloadV1(_SelfDigestRecord):
    schema: str; event_kind: str; event_id_digest: str; previous_registry_digest: str; previous_revocations_digest: str; next_registry: RegistrationBrokerAuthRegistryV1; next_revocations: RegistrationBrokerAuthRevocationsV1; activated_key_id: str; retiring_key_id: str; effective_lease_sequence: int; pre_policy_digest: str; post_policy_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-rotate-payload/v1" or self.event_kind != "rotate-registry" or type(self.next_registry) is not RegistrationBrokerAuthRegistryV1 or type(self.next_revocations) is not RegistrationBrokerAuthRevocationsV1:
            raise ValueError("broker auth rotate payload identity is invalid")
        for field in ("event_id_digest", "previous_registry_digest", "previous_revocations_digest", "pre_policy_digest", "post_policy_digest"):
            _require_digest(getattr(self, field), field)
        _require_text(self.activated_key_id, "activated_key_id"); _require_text(self.retiring_key_id, "retiring_key_id"); _u64(self.effective_lease_sequence, "effective_lease_sequence", positive=True)
        if self.next_registry.previous_registry_digest != self.previous_registry_digest or self.next_registry.active_key_id != self.activated_key_id or self.next_registry.key(self.retiring_key_id).state != "retiring" or self.next_registry.key(self.activated_key_id).effective_lease_sequence != self.effective_lease_sequence or self.next_revocations.registry_digest != self.next_registry.canonical_digest or self.pre_policy_digest == self.post_policy_digest:
            raise ValueError("broker auth rotate closure is invalid")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"activatedKeyId": self.activated_key_id, "effectiveLeaseSequence": self.effective_lease_sequence, "eventIdDigest": self.event_id_digest, "eventKind": self.event_kind, "nextRegistry": self.next_registry.to_wire(), "nextRevocations": self.next_revocations.to_wire(), "postPolicyDigest": self.post_policy_digest, "prePolicyDigest": self.pre_policy_digest, "previousRegistryDigest": self.previous_registry_digest, "previousRevocationsDigest": self.previous_revocations_digest, "retiringKeyId": self.retiring_key_id, "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthRotatePolicyEventPayloadV1":
        fields = {"activatedKeyId", "canonicalDigest", "effectiveLeaseSequence", "eventIdDigest", "eventKind", "nextRegistry", "nextRevocations", "postPolicyDigest", "prePolicyDigest", "previousRegistryDigest", "previousRevocationsDigest", "retiringKeyId", "schema"}
        raw = _broker_from_bytes(payload, fields, "broker auth rotate payload")
        registry = RegistrationBrokerAuthRegistryV1.from_canonical_bytes(canonical_json_bytes(raw["nextRegistry"]))
        revocations = RegistrationBrokerAuthRevocationsV1.from_canonical_bytes(canonical_json_bytes(raw["nextRevocations"]))
        item = cls(raw["schema"], raw["eventKind"], raw["eventIdDigest"], raw["previousRegistryDigest"], raw["previousRevocationsDigest"], registry, revocations, raw["activatedKeyId"], raw["retiringKeyId"], raw["effectiveLeaseSequence"], raw["prePolicyDigest"], raw["postPolicyDigest"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth rotate payload digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class BrokerAuthRevokePolicyEventPayloadV1(_SelfDigestRecord):
    schema: str; event_kind: str; event_id_digest: str; unchanged_registry_digest: str; previous_revocations_digest: str; next_revocations: RegistrationBrokerAuthRevocationsV1; revoked_key_id: str; revoked_at_broker_sequence: int; pre_policy_digest: str; post_policy_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-revoke-payload/v1" or self.event_kind != "revoke-key" or type(self.next_revocations) is not RegistrationBrokerAuthRevocationsV1:
            raise ValueError("broker auth revoke payload identity is invalid")
        for field in ("event_id_digest", "unchanged_registry_digest", "previous_revocations_digest", "pre_policy_digest", "post_policy_digest"):
            _require_digest(getattr(self, field), field)
        _require_text(self.revoked_key_id, "revoked_key_id"); _u64(self.revoked_at_broker_sequence, "revoked_at_broker_sequence", positive=True)
        if self.next_revocations.registry_digest != self.unchanged_registry_digest or self.next_revocations.revoked_at(self.revoked_key_id) != self.revoked_at_broker_sequence or self.pre_policy_digest == self.post_policy_digest:
            raise ValueError("broker auth revoke closure is invalid")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"eventIdDigest": self.event_id_digest, "eventKind": self.event_kind, "nextRevocations": self.next_revocations.to_wire(), "postPolicyDigest": self.post_policy_digest, "prePolicyDigest": self.pre_policy_digest, "previousRevocationsDigest": self.previous_revocations_digest, "revokedAtBrokerSequence": self.revoked_at_broker_sequence, "revokedKeyId": self.revoked_key_id, "schema": self.schema, "unchangedRegistryDigest": self.unchanged_registry_digest}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthRevokePolicyEventPayloadV1":
        fields = {"canonicalDigest", "eventIdDigest", "eventKind", "nextRevocations", "postPolicyDigest", "prePolicyDigest", "previousRevocationsDigest", "revokedAtBrokerSequence", "revokedKeyId", "schema", "unchangedRegistryDigest"}
        raw = _broker_from_bytes(payload, fields, "broker auth revoke payload")
        revocations = RegistrationBrokerAuthRevocationsV1.from_canonical_bytes(canonical_json_bytes(raw["nextRevocations"]))
        item = cls(raw["schema"], raw["eventKind"], raw["eventIdDigest"], raw["unchangedRegistryDigest"], raw["previousRevocationsDigest"], revocations, raw["revokedKeyId"], raw["revokedAtBrokerSequence"], raw["prePolicyDigest"], raw["postPolicyDigest"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth revoke payload digest is invalid")
        return item


BrokerAuthPolicyEventPayloadV1 = BrokerAuthRotatePolicyEventPayloadV1 | BrokerAuthRevokePolicyEventPayloadV1
BrokerAuthSequenceEventPayloadV1 = BrokerAuthLeaseIssueEventPayloadV1 | BrokerAuthEnvelopeSignEventPayloadV1 | BrokerAuthPolicyEventPayloadV1


def _checked_add(left: int, right: int) -> int:
    _u64(left, "left"); _u64(right, "right")
    if left > _U64_MAX - right:
        raise ValueError("u64 sequence overflow")
    return left + right


class _BrokerStateRecord:
    def _unsigned_wire(self) -> dict[str, object]:
        raise NotImplementedError

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self._unsigned_wire())

    def to_wire(self) -> dict[str, object]:
        return {**self._unsigned_wire(), "canonicalDigest": self.canonical_digest, "brokerStateMacHex": self.broker_state_mac_hex}  # type: ignore[attr-defined]

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    def verify_state_mac(self, state_mac_key: bytes) -> None:
        if not isinstance(state_mac_key, bytes) or len(state_mac_key) != 32:
            raise ValueError("broker state MAC key is invalid")
        expected = hmac.new(state_mac_key, canonical_json_bytes(self._unsigned_wire()), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, self.broker_state_mac_hex):  # type: ignore[attr-defined]
            raise ValueError("broker state MAC is invalid")


@dataclass(frozen=True, slots=True)
class BrokerAuthSequenceJournalEntryV1(_BrokerStateRecord):
    schema: str; sequence: int; previous_entry_digest: str | None; event_id_digest: str; event_kind: str; event_payload_ref: str; event_payload_length: int; event_payload_digest: str; pre_policy_digest: str; post_policy_digest: str; post_broker_auth_registry_digest: str; post_broker_auth_revocation_digest: str; broker_auth_key_id: str; broker_state_mac_hex: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-sequence-entry/v1" or self.event_kind not in {"issue-lease", "sign-envelope", "rotate-registry", "revoke-key"}:
            raise ValueError("broker auth sequence entry identity is invalid")
        _u64(self.sequence, "sequence", positive=True); _u64(self.event_payload_length, "event_payload_length", positive=True)
        if self.previous_entry_digest is not None:
            _require_digest(self.previous_entry_digest, "previous_entry_digest")
        for field in ("event_id_digest", "event_payload_ref", "event_payload_digest", "pre_policy_digest", "post_policy_digest", "post_broker_auth_registry_digest", "post_broker_auth_revocation_digest"):
            _require_digest(getattr(self, field), field)
        _require_text(self.broker_auth_key_id, "broker_auth_key_id"); _mac(self.broker_state_mac_hex, "broker_state_mac_hex")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthKeyId": self.broker_auth_key_id, "eventIdDigest": self.event_id_digest, "eventKind": self.event_kind, "eventPayloadDigest": self.event_payload_digest, "eventPayloadLength": self.event_payload_length, "eventPayloadRef": self.event_payload_ref, "postBrokerAuthRegistryDigest": self.post_broker_auth_registry_digest, "postBrokerAuthRevocationDigest": self.post_broker_auth_revocation_digest, "postPolicyDigest": self.post_policy_digest, "prePolicyDigest": self.pre_policy_digest, "previousEntryDigest": self.previous_entry_digest, "schema": self.schema, "sequence": self.sequence}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthSequenceJournalEntryV1":
        fields = {"brokerAuthKeyId", "brokerStateMacHex", "canonicalDigest", "eventIdDigest", "eventKind", "eventPayloadDigest", "eventPayloadLength", "eventPayloadRef", "postBrokerAuthRegistryDigest", "postBrokerAuthRevocationDigest", "postPolicyDigest", "prePolicyDigest", "previousEntryDigest", "schema", "sequence"}
        raw = _broker_from_bytes(payload, fields, "broker auth sequence entry")
        item = cls(raw["schema"], raw["sequence"], raw["previousEntryDigest"], raw["eventIdDigest"], raw["eventKind"], raw["eventPayloadRef"], raw["eventPayloadLength"], raw["eventPayloadDigest"], raw["prePolicyDigest"], raw["postPolicyDigest"], raw["postBrokerAuthRegistryDigest"], raw["postBrokerAuthRevocationDigest"], raw["brokerAuthKeyId"], raw["brokerStateMacHex"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth sequence entry digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class BrokerAuthSequenceStateV1(_BrokerStateRecord):
    schema: str; root_identity_digest: str; current_sequence: int; head_entry_digest: str | None; head_event_payload_ref: str | None; head_event_payload_digest: str | None; current_policy_digest: str; broker_auth_registry_digest: str; broker_auth_revocation_digest: str; broker_state_mac_hex: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-sequence-state/v1":
            raise ValueError("broker auth sequence state schema is invalid")
        _require_digest(self.root_identity_digest, "root_identity_digest"); _u64(self.current_sequence, "current_sequence")
        for field in ("current_policy_digest", "broker_auth_registry_digest", "broker_auth_revocation_digest"):
            _require_digest(getattr(self, field), field)
        head = (self.head_entry_digest, self.head_event_payload_ref, self.head_event_payload_digest)
        if self.current_sequence == 0:
            if any(value is not None for value in head):
                raise ValueError("empty broker auth state has a head")
        else:
            if any(value is None for value in head):
                raise ValueError("nonempty broker auth state lacks a head")
            for value in head:
                _require_digest(value, "head identity")
        _mac(self.broker_state_mac_hex, "broker_state_mac_hex")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthRegistryDigest": self.broker_auth_registry_digest, "brokerAuthRevocationDigest": self.broker_auth_revocation_digest, "currentPolicyDigest": self.current_policy_digest, "currentSequence": self.current_sequence, "headEntryDigest": self.head_entry_digest, "headEventPayloadDigest": self.head_event_payload_digest, "headEventPayloadRef": self.head_event_payload_ref, "rootIdentityDigest": self.root_identity_digest, "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthSequenceStateV1":
        fields = {"brokerAuthRegistryDigest", "brokerAuthRevocationDigest", "brokerStateMacHex", "canonicalDigest", "currentPolicyDigest", "currentSequence", "headEntryDigest", "headEventPayloadDigest", "headEventPayloadRef", "rootIdentityDigest", "schema"}
        raw = _broker_from_bytes(payload, fields, "broker auth sequence state")
        item = cls(raw["schema"], raw["rootIdentityDigest"], raw["currentSequence"], raw["headEntryDigest"], raw["headEventPayloadRef"], raw["headEventPayloadDigest"], raw["currentPolicyDigest"], raw["brokerAuthRegistryDigest"], raw["brokerAuthRevocationDigest"], raw["brokerStateMacHex"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth sequence state digest is invalid")
        return item


@dataclass(frozen=True, slots=True)
class BrokerAuthSequenceJournalV1(_BrokerStateRecord):
    schema: str; root_identity_digest: str; first_sequence: int; entries: tuple[BrokerAuthSequenceJournalEntryV1, ...]; broker_state_mac_hex: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-broker-auth-sequence-journal/v1" or self.first_sequence != 1:
            raise ValueError("broker auth journal identity is invalid")
        _require_digest(self.root_identity_digest, "root_identity_digest"); _mac(self.broker_state_mac_hex, "broker_state_mac_hex")
        entries = tuple(self.entries)
        if not entries or not all(type(item) is BrokerAuthSequenceJournalEntryV1 for item in entries):
            raise ValueError("broker auth journal entries are invalid")
        for index, entry in enumerate(entries, start=1):
            if entry.sequence != index or entry.previous_entry_digest != (None if index == 1 else entries[index - 2].canonical_digest):
                raise ValueError("broker auth journal is not contiguous")
        object.__setattr__(self, "entries", entries)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"entries": [item.to_wire() for item in self.entries], "firstSequence": self.first_sequence, "rootIdentityDigest": self.root_identity_digest, "schema": self.schema}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerAuthSequenceJournalV1":
        fields = {"brokerStateMacHex", "canonicalDigest", "entries", "firstSequence", "rootIdentityDigest", "schema"}
        raw = _broker_from_bytes(payload, fields, "broker auth sequence journal")
        if not isinstance(raw["entries"], list):
            raise ValueError("broker auth sequence journal entries are invalid")
        item = cls(raw["schema"], raw["rootIdentityDigest"], raw["firstSequence"], tuple(BrokerAuthSequenceJournalEntryV1.from_canonical_bytes(canonical_json_bytes(entry)) for entry in raw["entries"]), raw["brokerStateMacHex"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("broker auth sequence journal digest is invalid")
        return item

    def validate_state(self, state: BrokerAuthSequenceStateV1, payloads: Mapping[str, BrokerAuthSequenceEventPayloadV1]) -> None:
        if type(state) is not BrokerAuthSequenceStateV1 or state.root_identity_digest != self.root_identity_digest or state.current_sequence != len(self.entries):
            raise ValueError("broker auth state does not close journal")
        prior_policy: str | None = None
        prior_registry: str | None = None
        prior_revocations: str | None = None
        for entry in self.entries:
            payload = payloads.get(entry.event_payload_ref)
            if payload is None or type(payload) not in {BrokerAuthLeaseIssueEventPayloadV1, BrokerAuthEnvelopeSignEventPayloadV1, BrokerAuthRotatePolicyEventPayloadV1, BrokerAuthRevokePolicyEventPayloadV1}:
                raise ValueError("broker auth event payload is missing or typed incorrectly")
            if (entry.event_kind, entry.event_id_digest, entry.event_payload_digest, entry.event_payload_length, entry.pre_policy_digest, entry.post_policy_digest) != (payload.event_kind, payload.event_id_digest, payload.canonical_digest, len(payload.canonical_bytes), payload.pre_policy_digest, payload.post_policy_digest):
                raise ValueError("broker auth event entry does not close payload")
            if prior_policy is not None and entry.pre_policy_digest != prior_policy:
                raise ValueError("broker auth policy chain is broken")
            if isinstance(payload, BrokerAuthLeaseIssueEventPayloadV1):
                if payload.issued_sequence != entry.sequence or entry.broker_auth_key_id != payload.broker_auth_key_id:
                    raise ValueError("broker auth issue sequence is invalid")
                registry, revocations = payload.lease_request.expected_registry_digest, payload.lease_request.expected_revocation_digest
            elif isinstance(payload, BrokerAuthEnvelopeSignEventPayloadV1):
                if payload.sign_sequence != entry.sequence or entry.broker_auth_key_id != payload.broker_auth_key_id:
                    raise ValueError("broker auth signing sequence is invalid")
                registry, revocations = prior_registry, prior_revocations
            elif isinstance(payload, BrokerAuthRotatePolicyEventPayloadV1):
                if payload.effective_lease_sequence != entry.sequence:
                    raise ValueError("broker auth rotate sequence is invalid")
                registry, revocations = payload.next_registry.canonical_digest, payload.next_revocations.canonical_digest
            else:
                if payload.revoked_at_broker_sequence != entry.sequence:
                    raise ValueError("broker auth revoke sequence is invalid")
                registry, revocations = payload.unchanged_registry_digest, payload.next_revocations.canonical_digest
            if (entry.post_broker_auth_registry_digest, entry.post_broker_auth_revocation_digest) != (registry, revocations):
                raise ValueError("broker auth post-policy state is invalid")
            prior_policy, prior_registry, prior_revocations = entry.post_policy_digest, registry, revocations
        head = self.entries[-1]
        if (state.head_entry_digest, state.head_event_payload_ref, state.head_event_payload_digest, state.current_policy_digest, state.broker_auth_registry_digest, state.broker_auth_revocation_digest) != (head.canonical_digest, head.event_payload_ref, head.event_payload_digest, head.post_policy_digest, head.post_broker_auth_registry_digest, head.post_broker_auth_revocation_digest):
            raise ValueError("broker auth state head is invalid")


def _ed_base() -> tuple[int, int, int, int]:
    q = 2**255 - 19
    d = (-121665 * pow(121666, q - 2, q)) % q
    base_y = (4 * pow(5, q - 2, q)) % q
    base_x = pow(((base_y * base_y - 1) * pow((d * base_y * base_y + 1) % q, q - 2, q)) % q, (q + 3) // 8, q)
    if base_x & 1:
        base_x = q - base_x
    return q, d, base_x, base_y


def _ed_add(first: tuple[int, int], second: tuple[int, int], q: int, d: int) -> tuple[int, int]:
    x1, y1 = first; x2, y2 = second
    return (((x1 * y2 + x2 * y1) * pow((1 + d * x1 * x2 * y1 * y2) % q, q - 2, q)) % q, ((y1 * y2 + x1 * x2) * pow((1 - d * x1 * x2 * y1 * y2) % q, q - 2, q)) % q)


def _ed_mul(point: tuple[int, int], scalar: int, q: int, d: int) -> tuple[int, int]:
    result = (0, 1)
    while scalar:
        if scalar & 1:
            result = _ed_add(result, point, q, d)
        point = _ed_add(point, point, q, d); scalar >>= 1
    return result


def _ed_public(seed: bytes) -> bytes:
    q, d, x, y = _ed_base()
    scalar = int.from_bytes(hashlib.sha512(seed).digest()[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    point = _ed_mul((x, y), scalar, q, d)
    return (point[1] | ((point[0] & 1) << 255)).to_bytes(32, "little")


def _ed_sign(seed: bytes, message: bytes) -> bytes:
    q, d, x, y = _ed_base(); order = 2**252 + 27742317777372353535851937790883648493
    hashed = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(hashed[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    nonce = int.from_bytes(hashlib.sha512(hashed[32:] + message).digest(), "little") % order
    point = _ed_mul((x, y), nonce, q, d)
    encoded = (point[1] | ((point[0] & 1) << 255)).to_bytes(32, "little")
    challenge = int.from_bytes(hashlib.sha512(encoded + _ed_public(seed) + message).digest(), "little") % order
    return encoded + ((nonce + challenge * scalar) % order).to_bytes(32, "little")


class RegistrationBrokerAuthPrivateKeySlotV1:
    """Root-only private seed held in memory; it has no wire representation."""
    __slots__ = ("key_id", "purpose", "signature_domain", "lease_signature_domain", "challenge_domain", "injected_fd_identity_digest", "public_key_digest", "_seed", "_public", "_closed")
    _TOKEN = object()

    def __init__(self, token: object, key: RegistrationBrokerAuthKeyRegistrationV1, seed: bytes, public_key: bytes, identity: str) -> None:
        if token is not self._TOKEN:
            raise TypeError("private key slots must be loaded from an already-open read-only FD")
        self.key_id, self.purpose, self.signature_domain = key.key_id, key.purpose, key.signature_domain
        self.lease_signature_domain, self.challenge_domain = key.lease_signature_domain, key.challenge_domain
        self.injected_fd_identity_digest, self.public_key_digest = identity, key.public_key_digest
        self._seed, self._public, self._closed = seed, public_key, False

    @classmethod
    def load_from_readonly_fd(cls, descriptor: int, registration: RegistrationBrokerAuthKeyRegistrationV1, resolved_public_key: bytes) -> "RegistrationBrokerAuthPrivateKeySlotV1":
        if type(descriptor) is not int or type(registration) is not RegistrationBrokerAuthKeyRegistrationV1 or not isinstance(resolved_public_key, bytes) or len(resolved_public_key) != 32:
            raise ValueError("private key slot inputs are invalid")
        try:
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            info = os.fstat(descriptor)
            if flags & os.O_ACCMODE != os.O_RDONLY or not stat.S_ISREG(info.st_mode):
                raise ValueError("private key FD must be read-only regular file")
            copied = os.dup(descriptor)
            try:
                os.lseek(copied, 0, os.SEEK_SET)
                seed = os.read(copied, 33)
            finally:
                os.close(copied)
        except OSError as error:
            raise ValueError("private key FD is invalid") from error
        if len(seed) != 32 or _ed_public(seed) != resolved_public_key:
            raise ValueError("private key does not resolve to registered leaf")
        digest = canonical_digest({"device": info.st_dev, "inode": info.st_ino, "publicKeyDigest": "sha256:" + hashlib.sha256(resolved_public_key).hexdigest()})
        if registration.public_key_digest != "sha256:" + hashlib.sha256(resolved_public_key).hexdigest() or registration.resolved_leaf_public_key_digest != registration.public_key_digest:
            raise ValueError("private key slot does not close registration")
        return cls(cls._TOKEN, registration, seed, resolved_public_key, digest)

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("private key slot is closed")

    def sign(self, domain: str, payload: bytes) -> str:
        self._require_open()
        if domain not in {self.signature_domain, self.lease_signature_domain, self.challenge_domain} or not isinstance(payload, bytes):
            raise ValueError("private key signing domain is invalid")
        return _ed_sign(self._seed, domain.encode("utf-8") + b"\0" + payload).hex()

    def close(self) -> None:
        self._seed = b""; self._closed = True

    def __copy__(self):
        raise TypeError("private key slots are non-copyable")

    def __deepcopy__(self, memo):
        raise TypeError("private key slots are non-copyable")

    def __reduce__(self):
        raise TypeError("private key slots are non-serializable")


def verify_registration_broker_startup_challenge(governance: RegistrationBrokerAuthGovernanceV1, slot: RegistrationBrokerAuthPrivateKeySlotV1, nonce: str) -> str:
    """Sign and independently verify the exact startup challenge before child creation."""
    if type(governance) is not RegistrationBrokerAuthGovernanceV1 or type(slot) is not RegistrationBrokerAuthPrivateKeySlotV1:
        raise ValueError("broker startup trust closure is invalid")
    _require_text(nonce, "nonce")
    active = governance.registry.key(governance.registry.active_key_id)
    if active.state != "active" or governance.revocations.revoked_at(active.key_id) is not None or (slot.key_id, slot.purpose, slot.signature_domain, slot.challenge_domain, slot.public_key_digest) != (active.key_id, _BROKER_AUTH_PURPOSE, _BROKER_ENVELOPE_DOMAIN, _BROKER_CHALLENGE_DOMAIN, active.public_key_digest):
        raise ValueError("broker startup key closure is invalid")
    message = canonical_json_bytes({"keyId": active.key_id, "nonce": nonce, "registryDigest": governance.registry.canonical_digest})
    signature = slot.sign(_BROKER_CHALLENGE_DOMAIN, message)
    from .handoffs import _ed25519_verify
    if not _ed25519_verify(slot._public, _BROKER_CHALLENGE_DOMAIN.encode("utf-8") + b"\0" + message, bytes.fromhex(signature)):
        raise ValueError("broker startup challenge signature is invalid")
    return signature


# E.2 worker bootstrap contracts.  Provenance is a broker assertion carried by
# the signed envelope; the worker verifies only the observable FD facts below.
_WORKER_INPUT_ROLES = (
    "product-handoff-cache", "policy-handoff-cache", "task-output-policy",
    "transaction-record", "quarantine-object", "toolchain-build-record",
    "toolchain-artifact", "candidate",
)
_FD_ROLES = {
    "worker-input", "input-snapshot", "transaction-record", "quarantine-object",
    "toolchain-build-record", "toolchain-artifact", "broker-control",
}
_REGISTER_FD_CLOSURE = (
    ("worker-input", "registration-worker-input", "worker-input-inbox"),
    ("input-snapshot", "product-handoff-cache", "worker-input-inbox"),
    ("input-snapshot", "policy-handoff-cache", "worker-input-inbox"),
    ("input-snapshot", "task-output-policy", "worker-input-inbox"),
    ("input-snapshot", "e1.ticket23.toonflow-source.v1", "worker-input-inbox"),
    ("input-snapshot", "e1.ticket23.product-adaptation.v1", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.backend-persistence.v2", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.frontend-persistence.v2", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.api.v2", "worker-input-inbox"),
    ("input-snapshot", "e2.ticket23.canvas-structure.v2", "worker-input-inbox"),
    ("input-snapshot", "e4.ticket23.canvas-review.v1", "worker-input-inbox"),
    ("toolchain-build-record", "registration-toolchain-build-record", "trusted-toolchain-root"),
    ("toolchain-artifact", "registration-launcher-runtime-closure", "trusted-toolchain-root"),
    ("broker-control", "registration-mutation-control", None),
)
_RECOVER_FD_CLOSURE = (
    ("worker-input", "registration-worker-input", "recovery-inbox"),
    ("transaction-record", "trusted-registration-pre-state", "transaction-operation-root"),
    ("transaction-record", "staged-candidate-set", "transaction-operation-root"),
    ("transaction-record", "quarantine-object-set", "transaction-operation-root"),
    ("transaction-record", "repository-recovery-binding", "transaction-operation-root"),
    ("transaction-record", "registration-transaction-intent", "transaction-operation-root"),
    ("transaction-record", "registration-plan", "transaction-operation-root"),
    ("transaction-record", "registration-journal-head", "transaction-operation-root"),
    ("quarantine-object", "planned-catalog", "transaction-quarantine-root"),
    ("quarantine-object", "registration-audit", "transaction-quarantine-root"),
    ("quarantine-object", "e1.ticket23.toonflow-source.v1", "transaction-quarantine-root"),
    ("quarantine-object", "e1.ticket23.product-adaptation.v1", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.backend-persistence.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.frontend-persistence.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.api.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e2.ticket23.canvas-structure.v2", "transaction-quarantine-root"),
    ("quarantine-object", "e4.ticket23.canvas-review.v1", "transaction-quarantine-root"),
    ("toolchain-build-record", "registration-toolchain-build-record", "trusted-toolchain-root"),
    ("toolchain-artifact", "registration-launcher-runtime-closure", "trusted-toolchain-root"),
    ("broker-control", "registration-mutation-control", None),
)
_ENVELOPE_DOMAIN = "ai-video-reverse-engineer/registration-broker-envelope/v1"


@dataclass(frozen=True, slots=True)
class RegistrationWorkerInputObjectV1:
    role: str; ordinal: int; display_path: str; expected_file_type: str; expected_byte_length: int; expected_content_digest: str; candidate_ref: str | None

    def __post_init__(self) -> None:
        if self.role not in _WORKER_INPUT_ROLES or self.expected_file_type != "regular":
            raise ValueError("worker input object role or type is invalid")
        _u64(self.ordinal, "ordinal"); _relative_path(self.display_path, "display_path")
        _u64(self.expected_byte_length, "expected_byte_length"); _require_digest(self.expected_content_digest, "expected_content_digest")
        if (self.role == "candidate") != (self.candidate_ref is not None):
            raise ValueError("candidate inputs must bind exactly one candidate ref")
        if self.candidate_ref is not None:
            _candidate_ref(self.candidate_ref)

    def to_wire(self) -> dict[str, object]:
        return {"candidateRef": self.candidate_ref, "displayPath": self.display_path, "expectedByteLength": self.expected_byte_length, "expectedContentDigest": self.expected_content_digest, "expectedFileType": self.expected_file_type, "ordinal": self.ordinal, "role": self.role}

    @classmethod
    def _from_wire(cls, raw: object) -> "RegistrationWorkerInputObjectV1":
        fields = {"candidateRef", "displayPath", "expectedByteLength", "expectedContentDigest", "expectedFileType", "ordinal", "role"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("worker input object fields are invalid")
        return cls(raw["role"], raw["ordinal"], raw["displayPath"], raw["expectedFileType"], raw["expectedByteLength"], raw["expectedContentDigest"], raw["candidateRef"])


@dataclass(frozen=True, slots=True)
class RegistrationWorkerInputV1(_SelfDigestRecord):
    schema: str; project_id: str; operation_id: str; worker_mode: str; expected_worker_sequence: int; registration_profile: str; subject: str; expected_policy_commit: GitObjectIdV1; fixed_ref_name: str; expected_fixed_ref_commit: GitObjectIdV1; expected_repository_display_path: str; expected_git_executable_digest: str; inputs: tuple[RegistrationWorkerInputObjectV1, ...]; candidate_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-worker-input/v1" or self.project_id != "ai-video-reverse-engineer" or self.worker_mode not in {"register", "recover"} or self.registration_profile != "ticket-23/v1" or type(self.expected_policy_commit) is not GitObjectIdV1 or type(self.expected_fixed_ref_commit) is not GitObjectIdV1:
            raise ValueError("worker input identity is invalid")
        for field in ("operation_id", "subject", "fixed_ref_name", "expected_repository_display_path"):
            _require_text(getattr(self, field), field)
        _u64(self.expected_worker_sequence, "expected_worker_sequence", positive=True); _require_digest(self.expected_git_executable_digest, "expected_git_executable_digest")
        values = tuple(self.inputs)
        if not values or not all(type(item) is RegistrationWorkerInputObjectV1 for item in values):
            raise ValueError("worker inputs are invalid")
        keys = tuple((_WORKER_INPUT_ROLES.index(item.role), item.ordinal) for item in values)
        if keys != tuple(sorted(keys)) or any(item.ordinal != sum(prior.role == item.role for prior in values[:index]) for index, item in enumerate(values)):
            raise ValueError("worker inputs are not in fixed role/ordinal order")
        refs = tuple(self.candidate_refs)
        if len(refs) != 7 or refs != tuple(sorted(refs)) or len(set(refs)) != 7:
            raise ValueError("worker input candidate refs must be seven sorted unique digests")
        if any(_candidate_ref(ref) != ref for ref in refs) or tuple(item.candidate_ref for item in values if item.role == "candidate") != refs:
            raise ValueError("worker input candidates do not close candidate refs")
        object.__setattr__(self, "inputs", values); object.__setattr__(self, "candidate_refs", refs)

    def _unsigned_wire(self) -> dict[str, object]:
        return {"candidateRefs": list(self.candidate_refs), "expectedFixedRefCommit": self.expected_fixed_ref_commit.to_wire(), "expectedGitExecutableDigest": self.expected_git_executable_digest, "expectedPolicyCommit": self.expected_policy_commit.to_wire(), "expectedRepositoryDisplayPath": self.expected_repository_display_path, "expectedWorkerSequence": self.expected_worker_sequence, "fixedRefName": self.fixed_ref_name, "inputs": [item.to_wire() for item in self.inputs], "operationId": self.operation_id, "projectId": self.project_id, "registrationProfile": self.registration_profile, "schema": self.schema, "subject": self.subject, "workerMode": self.worker_mode}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationWorkerInputV1":
        fields = {"candidateRefs", "canonicalDigest", "expectedFixedRefCommit", "expectedGitExecutableDigest", "expectedPolicyCommit", "expectedRepositoryDisplayPath", "expectedWorkerSequence", "fixedRefName", "inputs", "operationId", "projectId", "registrationProfile", "schema", "subject", "workerMode"}
        raw = _strict_object(payload, fields, "worker input")
        if not isinstance(raw["inputs"], list) or not isinstance(raw["candidateRefs"], list):
            raise ValueError("worker input list fields are invalid")
        result = cls(raw["schema"], raw["projectId"], raw["operationId"], raw["workerMode"], raw["expectedWorkerSequence"], raw["registrationProfile"], raw["subject"], _oid(raw["expectedPolicyCommit"], "expectedPolicyCommit"), raw["fixedRefName"], _oid(raw["expectedFixedRefCommit"], "expectedFixedRefCommit"), raw["expectedRepositoryDisplayPath"], raw["expectedGitExecutableDigest"], tuple(RegistrationWorkerInputObjectV1._from_wire(item) for item in raw["inputs"]), tuple(raw["candidateRefs"]))
        if raw[_SELF_DIGEST] != result.canonical_digest:
            raise ValueError("worker input self digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class FdOpenProvenanceV1(_SelfDigestRecord):
    broker_open_policy: str; approved_root_role: str | None; approved_root_identity_digest: str | None; relative_logical_path_digest: str | None; requested_flags: tuple[str, ...]; resolve_flags: tuple[str, ...]; boundary_identity_digest: str | None

    def __post_init__(self) -> None:
        if self.broker_open_policy not in {"openat2-beneath-no-symlinks", "broker-created-seqpacket"}:
            raise ValueError("FD provenance policy is invalid")
        if self.broker_open_policy == "broker-created-seqpacket":
            if any(value is not None for value in (self.approved_root_role, self.approved_root_identity_digest, self.relative_logical_path_digest, self.boundary_identity_digest)) or tuple(self.resolve_flags) or tuple(self.requested_flags) != ("SOCK_CLOEXEC", "SOCK_SEQPACKET"):
                raise ValueError("control FD provenance is invalid")
        else:
            if not isinstance(self.approved_root_role, str) or any(value is None for value in (self.approved_root_identity_digest, self.relative_logical_path_digest, self.boundary_identity_digest)) or tuple(self.requested_flags) != ("O_CLOEXEC", "O_NOFOLLOW", "O_RDONLY") or tuple(self.resolve_flags) != ("RESOLVE_BENEATH", "RESOLVE_NO_MAGICLINKS", "RESOLVE_NO_SYMLINKS", "RESOLVE_NO_XDEV"):
                raise ValueError("regular FD provenance is invalid")
            for value in (self.approved_root_identity_digest, self.relative_logical_path_digest, self.boundary_identity_digest): _require_digest(value, "FD provenance digest")
        object.__setattr__(self, "requested_flags", tuple(self.requested_flags)); object.__setattr__(self, "resolve_flags", tuple(self.resolve_flags))

    def _unsigned_wire(self) -> dict[str, object]:
        return {"approvedRootIdentityDigest": self.approved_root_identity_digest, "approvedRootRole": self.approved_root_role, "boundaryIdentityDigest": self.boundary_identity_digest, "brokerOpenPolicy": self.broker_open_policy, "relativeLogicalPathDigest": self.relative_logical_path_digest, "requestedFlags": list(self.requested_flags), "resolveFlags": list(self.resolve_flags)}

    def to_wire(self) -> dict[str, object]: return {**self._unsigned_wire(), "canonicalDigest": self.canonical_digest}

    @classmethod
    def _from_wire(cls, raw: object) -> "FdOpenProvenanceV1":
        fields = {"approvedRootIdentityDigest", "approvedRootRole", "boundaryIdentityDigest", "brokerOpenPolicy", "canonicalDigest", "relativeLogicalPathDigest", "requestedFlags", "resolveFlags"}
        if not isinstance(raw, dict) or set(raw) != fields or not isinstance(raw["requestedFlags"], list) or not isinstance(raw["resolveFlags"], list): raise ValueError("FD provenance wire is invalid")
        result = cls(raw["brokerOpenPolicy"], raw["approvedRootRole"], raw["approvedRootIdentityDigest"], raw["relativeLogicalPathDigest"], tuple(raw["requestedFlags"]), tuple(raw["resolveFlags"]), raw["boundaryIdentityDigest"])
        if raw["canonicalDigest"] != result.canonical_digest: raise ValueError("FD provenance digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class FdObservableIdentityV1(_SelfDigestRecord):
    file_type: str; access_mode: str; status_flags: tuple[str, ...]; fd_flags: tuple[str, ...]; device: int | None; inode: int | None; byte_length: int | None; content_digest: str | None; mount_identity_digest: str | None; socket_options: tuple[str, ...]; socket_identity_digest: str | None; identity_digest: str

    def __post_init__(self) -> None:
        if (self.file_type, self.access_mode) not in {("regular", "read-only"), ("socket-seqpacket", "bidirectional-control"), ("socket-stream", "bidirectional-control")} or tuple(self.fd_flags) != ("FD_CLOEXEC",):
            raise ValueError("FD observable identity is invalid")
        if self.file_type == "regular":
            if tuple(self.status_flags) != ("O_RDONLY",) or any(value is None for value in (self.device, self.inode, self.byte_length, self.content_digest, self.mount_identity_digest)) or self.socket_options or self.socket_identity_digest is not None:
                raise ValueError("regular FD observable identity is invalid")
            for field in ("device", "inode", "byte_length"): _u64(getattr(self, field), field)
            _require_digest(self.content_digest, "content_digest"); _require_digest(self.mount_identity_digest, "mount_identity_digest")
        else:
            expected_options = ("SO_PASSCRED=1", "SO_TYPE=SOCK_SEQPACKET") if self.file_type == "socket-seqpacket" else ("SO_TYPE=SOCK_STREAM",)
            if any(value is not None for value in (self.device, self.inode, self.byte_length, self.content_digest, self.mount_identity_digest)) or tuple(self.socket_options) != expected_options or self.socket_identity_digest is None:
                raise ValueError("control FD observable identity is invalid")
            _require_digest(self.socket_identity_digest, "socket_identity_digest")
        _require_digest(self.identity_digest, "identity_digest")
        object.__setattr__(self, "status_flags", tuple(self.status_flags)); object.__setattr__(self, "fd_flags", tuple(self.fd_flags)); object.__setattr__(self, "socket_options", tuple(self.socket_options))

    def _identity_wire(self) -> dict[str, object]:
        return {"accessMode": self.access_mode, "byteLength": self.byte_length, "contentDigest": self.content_digest, "device": self.device, "fdFlags": list(self.fd_flags), "fileType": self.file_type, "inode": self.inode, "mountIdentityDigest": self.mount_identity_digest, "socketIdentityDigest": self.socket_identity_digest, "socketOptions": list(self.socket_options), "statusFlags": list(self.status_flags)}

    def _unsigned_wire(self) -> dict[str, object]: return {**self._identity_wire(), "identityDigest": self.identity_digest}
    def to_wire(self) -> dict[str, object]: return {**self._unsigned_wire(), "canonicalDigest": self.canonical_digest}

    @classmethod
    def from_open_fd(cls, fd: int) -> "FdObservableIdentityV1":
        """Rebuild only observable FD facts; broker-open provenance remains unobservable."""
        try:
            if not isinstance(fd, int) or fd < 0:
                raise ValueError("received FD is invalid")
            if not fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
                raise ValueError("received FD lacks FD_CLOEXEC")
            flags, info = fcntl.fcntl(fd, fcntl.F_GETFL), os.fstat(fd)
            fd_flags = ("FD_CLOEXEC",)
            if stat.S_ISREG(info.st_mode):
                if flags != os.O_RDONLY:
                    raise ValueError("received regular FD state is invalid")
                filesystem_id = getattr(os.fstatvfs(fd), "f_fsid", None)
                if not isinstance(filesystem_id, int):
                    raise ValueError("received regular FD mount identity is unavailable")
                digest = hashlib.sha256()
                offset = 0
                while True:
                    chunk = os.pread(fd, 65536, offset)
                    if not chunk:
                        break
                    digest.update(chunk)
                    offset += len(chunk)
                content_digest = "sha256:" + digest.hexdigest()
                mount_identity_digest = canonical_digest({"filesystemId": filesystem_id, "kind": "fstatvfs-fsid/v1"})
                wire = {"accessMode": "read-only", "byteLength": info.st_size, "contentDigest": content_digest, "device": info.st_dev, "fdFlags": list(fd_flags), "fileType": "regular", "inode": info.st_ino, "mountIdentityDigest": mount_identity_digest, "socketIdentityDigest": None, "socketOptions": [], "statusFlags": ["O_RDONLY"]}
                return cls("regular", "read-only", ("O_RDONLY",), fd_flags, info.st_dev, info.st_ino, info.st_size, content_digest, mount_identity_digest, (), None, canonical_digest(wire))
            if not stat.S_ISSOCK(info.st_mode) or flags != os.O_RDWR:
                raise ValueError("received control FD state is invalid")
            probe = socket.socket(fileno=os.dup(fd))
            try:
                socket_type = probe.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
                family = probe.family
                if socket_type == socket.SOCK_SEQPACKET:
                    if family != socket.AF_UNIX or not hasattr(socket, "SO_PASSCRED") or probe.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) != 1:
                        raise ValueError("received control FD state is invalid")
                    file_type, options = "socket-seqpacket", ("SO_PASSCRED=1", "SO_TYPE=SOCK_SEQPACKET")
                elif socket_type == socket.SOCK_STREAM:
                    file_type, options = "socket-stream", ("SO_TYPE=SOCK_STREAM",)
                else:
                    raise ValueError("received control FD state is invalid")
            finally:
                probe.close()
            socket_identity_digest = canonical_digest({"family": family, "inode": info.st_ino, "socketType": socket_type})
            wire = {"accessMode": "bidirectional-control", "byteLength": None, "contentDigest": None, "device": None, "fdFlags": list(fd_flags), "fileType": file_type, "inode": None, "mountIdentityDigest": None, "socketIdentityDigest": socket_identity_digest, "socketOptions": list(options), "statusFlags": []}
            return cls(file_type, "bidirectional-control", (), fd_flags, None, None, None, None, None, options, socket_identity_digest, canonical_digest(wire))
        except OSError as error:
            raise ValueError("received FD cannot be observed") from error

    @classmethod
    def _from_wire(cls, raw: object) -> "FdObservableIdentityV1":
        fields = {"accessMode", "byteLength", "canonicalDigest", "contentDigest", "device", "fdFlags", "fileType", "identityDigest", "inode", "mountIdentityDigest", "socketIdentityDigest", "socketOptions", "statusFlags"}
        if not isinstance(raw, dict) or set(raw) != fields or any(not isinstance(raw[key], list) for key in ("fdFlags", "socketOptions", "statusFlags")): raise ValueError("FD observable wire is invalid")
        result = cls(raw["fileType"], raw["accessMode"], tuple(raw["statusFlags"]), tuple(raw["fdFlags"]), raw["device"], raw["inode"], raw["byteLength"], raw["contentDigest"], raw["mountIdentityDigest"], tuple(raw["socketOptions"]), raw["socketIdentityDigest"], raw["identityDigest"])
        if raw["canonicalDigest"] != result.canonical_digest: raise ValueError("FD observable digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class FdDescriptorV1(_SelfDigestRecord):
    role: str; ordinal: int; logical_name: str; cardinality: str; provenance_fields: FdOpenProvenanceV1; observable_fields: FdObservableIdentityV1

    def __post_init__(self) -> None:
        if self.role not in _FD_ROLES or self.cardinality != "exactly-one" or not isinstance(self.logical_name, str) or not self.logical_name or type(self.provenance_fields) is not FdOpenProvenanceV1 or type(self.observable_fields) is not FdObservableIdentityV1:
            raise ValueError("FD descriptor is invalid")
        _u64(self.ordinal, "ordinal")
        control = self.role == "broker-control"
        if control:
            valid = self.provenance_fields.broker_open_policy == "broker-created-seqpacket" and self.observable_fields.file_type == "socket-seqpacket"
        else:
            valid = self.provenance_fields.broker_open_policy == "openat2-beneath-no-symlinks" and self.observable_fields.file_type == "regular"
        if not valid:
            raise ValueError("FD descriptor mixes provenance and observable types")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"cardinality": self.cardinality, "logicalName": self.logical_name, "observableFields": self.observable_fields.to_wire(), "ordinal": self.ordinal, "provenanceFields": self.provenance_fields.to_wire(), "role": self.role}

    @classmethod
    def _from_wire(cls, raw: object) -> "FdDescriptorV1":
        fields = {"canonicalDigest", "cardinality", "logicalName", "observableFields", "ordinal", "provenanceFields", "role"}
        if not isinstance(raw, dict) or set(raw) != fields: raise ValueError("FD descriptor wire is invalid")
        result = cls(raw["role"], raw["ordinal"], raw["logicalName"], raw["cardinality"], FdOpenProvenanceV1._from_wire(raw["provenanceFields"]), FdObservableIdentityV1._from_wire(raw["observableFields"]))
        if raw["canonicalDigest"] != result.canonical_digest: raise ValueError("FD descriptor digest is invalid")
        return result


def _validate_fd_closure(worker_mode: str, descriptors: object) -> tuple[FdDescriptorV1, ...]:
    expected = _REGISTER_FD_CLOSURE if worker_mode == "register" else _RECOVER_FD_CLOSURE if worker_mode == "recover" else None
    values = tuple(descriptors) if isinstance(descriptors, (tuple, list)) else ()
    if expected is None or len(values) != len(expected) or not all(type(item) is FdDescriptorV1 for item in values):
        raise ValueError("FD closure count is invalid")
    for ordinal, (descriptor, (role, logical_name, root_role)) in enumerate(zip(values, expected)):
        if (descriptor.ordinal, descriptor.role, descriptor.logical_name) != (ordinal, role, logical_name):
            raise ValueError("FD closure order or name is invalid")
        if role == "broker-control":
            if descriptor.provenance_fields.broker_open_policy != "broker-created-seqpacket": raise ValueError("control FD closure is invalid")
        elif descriptor.provenance_fields.approved_root_role != root_role or descriptor.provenance_fields.broker_open_policy != "openat2-beneath-no-symlinks":
            raise ValueError("regular FD closure root is invalid")
    return values


@dataclass(frozen=True, slots=True)
class CapabilityEnvelopeUnsignedV1(_SelfDigestRecord):
    schema: str; broker_lease_identity_digest: str; operation_id: str; lifecycle_nonce: str; worker_mode: str; worker_session_id: str; worker_executable_digest: str; worker_process_identity_digest: str; worker_pidfd_identity_digest: str; worker_sequence: int; broker_session_nonce: str; worker_input_digest: str; fd_descriptors: tuple[FdDescriptorV1, ...]; broker_auth_key_id: str; broker_auth_public_key_digest: str; broker_auth_registry_generation: int; broker_auth_registry_digest: str; broker_auth_revocation_digest: str; broker_auth_allowed_signers_digest: str; broker_auth_lease_issued_sequence: int; broker_auth_lease_expiry_sequence: int; broker_auth_sign_sequence: int

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-capability-envelope/v1" or self.worker_mode not in {"register", "recover"}:
            raise ValueError("capability envelope identity is invalid")
        for field in ("operation_id", "lifecycle_nonce", "worker_session_id", "broker_session_nonce", "broker_auth_key_id"): _require_text(getattr(self, field), field)
        for field in ("broker_lease_identity_digest", "worker_executable_digest", "worker_process_identity_digest", "worker_pidfd_identity_digest", "worker_input_digest", "broker_auth_public_key_digest", "broker_auth_registry_digest", "broker_auth_revocation_digest", "broker_auth_allowed_signers_digest"): _require_digest(getattr(self, field), field)
        for field in ("worker_sequence", "broker_auth_registry_generation", "broker_auth_lease_issued_sequence", "broker_auth_lease_expiry_sequence", "broker_auth_sign_sequence"): _u64(getattr(self, field), field, positive=True)
        if self.broker_auth_lease_issued_sequence > self.broker_auth_sign_sequence or self.broker_auth_sign_sequence > self.broker_auth_lease_expiry_sequence: raise ValueError("capability envelope lease sequence is invalid")
        object.__setattr__(self, "fd_descriptors", _validate_fd_closure(self.worker_mode, self.fd_descriptors))

    def _unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthAllowedSignersDigest": self.broker_auth_allowed_signers_digest, "brokerAuthKeyId": self.broker_auth_key_id, "brokerAuthLeaseExpirySequence": self.broker_auth_lease_expiry_sequence, "brokerAuthLeaseIssuedSequence": self.broker_auth_lease_issued_sequence, "brokerAuthPublicKeyDigest": self.broker_auth_public_key_digest, "brokerAuthRegistryDigest": self.broker_auth_registry_digest, "brokerAuthRegistryGeneration": self.broker_auth_registry_generation, "brokerAuthRevocationDigest": self.broker_auth_revocation_digest, "brokerAuthSignSequence": self.broker_auth_sign_sequence, "brokerLeaseIdentityDigest": self.broker_lease_identity_digest, "brokerSessionNonce": self.broker_session_nonce, "fdDescriptors": [item.to_wire() for item in self.fd_descriptors], "lifecycleNonce": self.lifecycle_nonce, "operationId": self.operation_id, "schema": self.schema, "workerExecutableDigest": self.worker_executable_digest, "workerInputDigest": self.worker_input_digest, "workerMode": self.worker_mode, "workerPidfdIdentityDigest": self.worker_pidfd_identity_digest, "workerProcessIdentityDigest": self.worker_process_identity_digest, "workerSequence": self.worker_sequence, "workerSessionId": self.worker_session_id}

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "CapabilityEnvelopeUnsignedV1":
        fields = set(cls.__annotations__) | {"canonicalDigest"}
        wire = {"broker_lease_identity_digest": "brokerLeaseIdentityDigest", "operation_id": "operationId", "lifecycle_nonce": "lifecycleNonce", "worker_mode": "workerMode", "worker_session_id": "workerSessionId", "worker_executable_digest": "workerExecutableDigest", "worker_process_identity_digest": "workerProcessIdentityDigest", "worker_pidfd_identity_digest": "workerPidfdIdentityDigest", "worker_sequence": "workerSequence", "broker_session_nonce": "brokerSessionNonce", "worker_input_digest": "workerInputDigest", "fd_descriptors": "fdDescriptors", "broker_auth_key_id": "brokerAuthKeyId", "broker_auth_public_key_digest": "brokerAuthPublicKeyDigest", "broker_auth_registry_generation": "brokerAuthRegistryGeneration", "broker_auth_registry_digest": "brokerAuthRegistryDigest", "broker_auth_revocation_digest": "brokerAuthRevocationDigest", "broker_auth_allowed_signers_digest": "brokerAuthAllowedSignersDigest", "broker_auth_lease_issued_sequence": "brokerAuthLeaseIssuedSequence", "broker_auth_lease_expiry_sequence": "brokerAuthLeaseExpirySequence", "broker_auth_sign_sequence": "brokerAuthSignSequence"}
        fields = {"schema", "canonicalDigest", *wire.values()}; raw = _strict_object(payload, fields, "unsigned capability envelope")
        if not isinstance(raw["fdDescriptors"], list): raise ValueError("capability envelope FD descriptors are invalid")
        result = cls(raw["schema"], *(raw[wire[name]] if name != "fd_descriptors" else tuple(FdDescriptorV1._from_wire(item) for item in raw["fdDescriptors"]) for name in wire))
        if raw["canonicalDigest"] != result.canonical_digest: raise ValueError("capability envelope self digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class CapabilityEnvelopeV1:
    unsigned: CapabilityEnvelopeUnsignedV1
    broker_signature_hex: str

    def __post_init__(self) -> None:
        if type(self.unsigned) is not CapabilityEnvelopeUnsignedV1:
            raise ValueError("signed capability envelope requires an unsigned envelope")
        _signature(self.broker_signature_hex, "broker_signature_hex")

    @classmethod
    def create(cls, unsigned_envelope: CapabilityEnvelopeUnsignedV1, broker_signer: RegistrationBrokerAuthPrivateKeySlotV1) -> "CapabilityEnvelopeV1":
        if type(unsigned_envelope) is not CapabilityEnvelopeUnsignedV1 or type(broker_signer) is not RegistrationBrokerAuthPrivateKeySlotV1:
            raise TypeError("capability envelope must be signed by a broker private-key slot")
        if (broker_signer.key_id, broker_signer.signature_domain, broker_signer.public_key_digest) != (unsigned_envelope.broker_auth_key_id, _ENVELOPE_DOMAIN, unsigned_envelope.broker_auth_public_key_digest):
            raise ValueError("broker signer does not close capability envelope")
        return cls(unsigned_envelope, broker_signer.sign(_ENVELOPE_DOMAIN, unsigned_envelope.canonical_bytes))

    @property
    def canonical_digest(self) -> str: return canonical_digest(self.to_wire())
    @property
    def canonical_bytes(self) -> bytes: return canonical_json_bytes(self.to_wire())

    def to_wire(self) -> dict[str, object]:
        return {**self.unsigned.to_wire(), "brokerSignatureHex": self.broker_signature_hex}

    def verify(self, pinned_broker_allowed_signers: Mapping[str, bytes]) -> None:
        if not isinstance(pinned_broker_allowed_signers, Mapping): raise ValueError("pinned broker allowed signers are invalid")
        key = pinned_broker_allowed_signers.get(self.unsigned.broker_auth_key_id)
        if not isinstance(key, bytes) or len(key) != 32 or "sha256:" + hashlib.sha256(key).hexdigest() != self.unsigned.broker_auth_public_key_digest:
            raise ValueError("capability envelope signer is not pinned")
        from .handoffs import _ed25519_verify
        if not _ed25519_verify(key, _ENVELOPE_DOMAIN.encode("utf-8") + b"\0" + self.unsigned.canonical_bytes, bytes.fromhex(self.broker_signature_hex)):
            raise ValueError("capability envelope signature is invalid")

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "CapabilityEnvelopeV1":
        raw = _strict_object(payload, set(CapabilityEnvelopeUnsignedV1.from_canonical_bytes.__func__.__annotations__) if False else {"brokerAuthAllowedSignersDigest", "brokerAuthKeyId", "brokerAuthLeaseExpirySequence", "brokerAuthLeaseIssuedSequence", "brokerAuthPublicKeyDigest", "brokerAuthRegistryDigest", "brokerAuthRegistryGeneration", "brokerAuthRevocationDigest", "brokerAuthSignSequence", "brokerLeaseIdentityDigest", "brokerSessionNonce", "brokerSignatureHex", "canonicalDigest", "fdDescriptors", "lifecycleNonce", "operationId", "schema", "workerExecutableDigest", "workerInputDigest", "workerMode", "workerPidfdIdentityDigest", "workerProcessIdentityDigest", "workerSequence", "workerSessionId"}, "signed capability envelope")
        signature = raw.pop("brokerSignatureHex")
        unsigned = CapabilityEnvelopeUnsignedV1.from_canonical_bytes(canonical_json_bytes(raw))
        return cls(unsigned, signature)


class RegistrationRecoveryCapabilityV1:
    """Worker-only one-shot ownership of received descriptors; no path recovery."""
    __slots__ = ("envelope", "_ordered_fds", "_consumed")
    _TOKEN = object()

    def __init__(self, token: object, envelope: CapabilityEnvelopeV1, ordered_fds: tuple[int, ...]) -> None:
        if token is not self._TOKEN: raise TypeError("registration capability is received only through SCM_RIGHTS")
        self.envelope, self._ordered_fds, self._consumed = envelope, ordered_fds, False

    @classmethod
    def receive_once(cls, authenticated_seqpacket_fd: object, self_process_identity: str, pinned_broker_allowed_signers: Mapping[str, bytes]) -> "RegistrationRecoveryCapabilityV1":
        if not hasattr(socket, "MSG_CMSG_CLOEXEC"):
            raise ValueError("MSG_CMSG_CLOEXEC unavailable")
        if not isinstance(authenticated_seqpacket_fd, socket.socket): raise ValueError("authenticated seqpacket socket is required")
        _require_digest(self_process_identity, "self_process_identity")
        received: list[int] = []
        try:
            message, ancillary, flags, _address = authenticated_seqpacket_fd.recvmsg(1024 * 1024, socket.CMSG_SPACE(20 * 4), socket.MSG_CMSG_CLOEXEC)
            if flags & socket.MSG_CTRUNC: raise ValueError("SCM_RIGHTS message is truncated")
            if len(ancillary) != 1 or ancillary[0][0:2] != (socket.SOL_SOCKET, socket.SCM_RIGHTS): raise ValueError("SCM_RIGHTS message is invalid")
            import array
            values = array.array("i"); values.frombytes(ancillary[0][2][:len(ancillary[0][2]) - len(ancillary[0][2]) % values.itemsize]); received = list(values)
            envelope = CapabilityEnvelopeV1.from_canonical_bytes(message); envelope.verify(pinned_broker_allowed_signers)
            if envelope.unsigned.worker_process_identity_digest != self_process_identity: raise ValueError("capability process identity is invalid")
            if len(received) != len(envelope.unsigned.fd_descriptors): raise ValueError("SCM_RIGHTS FD closure count is invalid")
            for descriptor, fd in zip(envelope.unsigned.fd_descriptors, received): cls._verify_observable_fd(fd, descriptor.observable_fields)
            capability = cls(cls._TOKEN, envelope, tuple(received)); received = []
            authenticated_seqpacket_fd.close()
            return capability
        except Exception:
            for fd in received:
                try: os.close(fd)
                except OSError: pass
            raise

    @staticmethod
    def _verify_observable_fd(fd: int, expected: FdObservableIdentityV1) -> None:
        if type(expected) is not FdObservableIdentityV1:
            raise ValueError("expected FD observable identity is invalid")
        observed = FdObservableIdentityV1.from_open_fd(fd)
        if observed != expected:
            if expected.file_type == "regular":
                raise ValueError("received regular FD identity changed")
            raise ValueError("received control FD state is invalid")

    def take_once(self) -> tuple[int, ...]:
        if self._consumed: raise ValueError("registration capability was already consumed")
        self._consumed = True; values, self._ordered_fds = self._ordered_fds, (); return values

    def close(self) -> None:
        for fd in self._ordered_fds:
            try: os.close(fd)
            except OSError: pass
        self._ordered_fds = (); self._consumed = True

    def __copy__(self): raise TypeError("registration capability is non-copyable")
    def __deepcopy__(self, memo): raise TypeError("registration capability is non-copyable")
    def __reduce__(self): raise TypeError("registration capability is non-serializable")


def authenticated_type(fd: int) -> int:
    """Read the kernel socket type only; no provenance claim is made here."""
    duplicate = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try: return duplicate.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
    finally: duplicate.close()


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryLeaseV1:
    schema: str; project_id: str; operation_id: str; lifecycle_nonce: str; session_family_authorization_digest: str; next_recovery_sequence: int; transaction_parent_identity_digest: str; transaction_root_name: str; transaction_root_identity_digest: str; operation_directory_identity_digest: str; repository_identity_digest: str; repository_boundary_identity_digest: str; trusted_toolchain_build_record_digest: str; trusted_recovery_launcher_digest: str; executor_identity_digest: str; profile_identity_digest: str; broker_auth_registry_generation: int; broker_auth_registry_digest: str; broker_auth_revocation_digest: str; broker_auth_policy_digest: str; broker_auth_allowed_signers_digest: str; broker_auth_key_id: str; broker_auth_lease_request_digest: str; broker_auth_sequence_root_identity_digest: str; broker_auth_issue_entry_digest: str; broker_auth_sequence_head_digest: str; broker_auth_lease_issued_sequence: int; broker_auth_lease_expiry_sequence: int; lease_identity_digest: str; state: str; broker_signature_hex: str; broker_mac: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-recovery-lease/v1" or self.project_id != "ai-video-reverse-engineer" or self.state not in {"OPEN", "RECOVERY_RUNNING", "TERMINAL", "RECLAIMED", "FAILED_CLOSED"}:
            raise ValueError("registration recovery lease identity is invalid")
        for field in ("operation_id", "lifecycle_nonce", "transaction_root_name", "broker_auth_key_id"):
            _require_text(getattr(self, field), field)
        for field in ("session_family_authorization_digest", "transaction_parent_identity_digest", "transaction_root_identity_digest", "operation_directory_identity_digest", "repository_identity_digest", "repository_boundary_identity_digest", "trusted_toolchain_build_record_digest", "trusted_recovery_launcher_digest", "executor_identity_digest", "profile_identity_digest", "broker_auth_registry_digest", "broker_auth_revocation_digest", "broker_auth_policy_digest", "broker_auth_allowed_signers_digest", "broker_auth_lease_request_digest", "broker_auth_sequence_root_identity_digest", "broker_auth_issue_entry_digest", "broker_auth_sequence_head_digest", "lease_identity_digest"):
            _require_digest(getattr(self, field), field)
        for field in ("next_recovery_sequence", "broker_auth_registry_generation", "broker_auth_lease_issued_sequence", "broker_auth_lease_expiry_sequence"):
            _u64(getattr(self, field), field, positive=True)
        if self.broker_auth_lease_expiry_sequence < self.broker_auth_lease_issued_sequence:
            raise ValueError("lease expiry precedes issue")
        _signature(self.broker_signature_hex, "broker_signature_hex"); _mac(self.broker_mac, "broker_mac")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"brokerAuthAllowedSignersDigest": self.broker_auth_allowed_signers_digest, "brokerAuthIssueEntryDigest": self.broker_auth_issue_entry_digest, "brokerAuthKeyId": self.broker_auth_key_id, "brokerAuthLeaseExpirySequence": self.broker_auth_lease_expiry_sequence, "brokerAuthLeaseIssuedSequence": self.broker_auth_lease_issued_sequence, "brokerAuthLeaseRequestDigest": self.broker_auth_lease_request_digest, "brokerAuthPolicyDigest": self.broker_auth_policy_digest, "brokerAuthRegistryDigest": self.broker_auth_registry_digest, "brokerAuthRegistryGeneration": self.broker_auth_registry_generation, "brokerAuthRevocationDigest": self.broker_auth_revocation_digest, "brokerAuthSequenceHeadDigest": self.broker_auth_sequence_head_digest, "brokerAuthSequenceRootIdentityDigest": self.broker_auth_sequence_root_identity_digest, "executorIdentityDigest": self.executor_identity_digest, "leaseIdentityDigest": self.lease_identity_digest, "lifecycleNonce": self.lifecycle_nonce, "nextRecoverySequence": self.next_recovery_sequence, "operationDirectoryIdentityDigest": self.operation_directory_identity_digest, "operationId": self.operation_id, "profileIdentityDigest": self.profile_identity_digest, "projectId": self.project_id, "repositoryBoundaryIdentityDigest": self.repository_boundary_identity_digest, "repositoryIdentityDigest": self.repository_identity_digest, "schema": self.schema, "sessionFamilyAuthorizationDigest": self.session_family_authorization_digest, "state": self.state, "transactionParentIdentityDigest": self.transaction_parent_identity_digest, "transactionRootIdentityDigest": self.transaction_root_identity_digest, "transactionRootName": self.transaction_root_name, "trustedRecoveryLauncherDigest": self.trusted_recovery_launcher_digest, "trustedToolchainBuildRecordDigest": self.trusted_toolchain_build_record_digest}

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self._unsigned_wire())

    def to_wire(self) -> dict[str, object]:
        return {**self._unsigned_wire(), "brokerMac": self.broker_mac, "brokerSignatureHex": self.broker_signature_hex, "canonicalDigest": self.canonical_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationRecoveryLeaseV1":
        fields = {"brokerAuthAllowedSignersDigest", "brokerAuthIssueEntryDigest", "brokerAuthKeyId", "brokerAuthLeaseExpirySequence", "brokerAuthLeaseIssuedSequence", "brokerAuthLeaseRequestDigest", "brokerAuthPolicyDigest", "brokerAuthRegistryDigest", "brokerAuthRegistryGeneration", "brokerAuthRevocationDigest", "brokerAuthSequenceHeadDigest", "brokerAuthSequenceRootIdentityDigest", "brokerMac", "brokerSignatureHex", "canonicalDigest", "executorIdentityDigest", "leaseIdentityDigest", "lifecycleNonce", "nextRecoverySequence", "operationDirectoryIdentityDigest", "operationId", "profileIdentityDigest", "projectId", "repositoryBoundaryIdentityDigest", "repositoryIdentityDigest", "schema", "sessionFamilyAuthorizationDigest", "state", "transactionParentIdentityDigest", "transactionRootIdentityDigest", "transactionRootName", "trustedRecoveryLauncherDigest", "trustedToolchainBuildRecordDigest"}
        raw = _broker_from_bytes(payload, fields, "registration recovery lease")
        item = cls(raw["schema"], raw["projectId"], raw["operationId"], raw["lifecycleNonce"], raw["sessionFamilyAuthorizationDigest"], raw["nextRecoverySequence"], raw["transactionParentIdentityDigest"], raw["transactionRootName"], raw["transactionRootIdentityDigest"], raw["operationDirectoryIdentityDigest"], raw["repositoryIdentityDigest"], raw["repositoryBoundaryIdentityDigest"], raw["trustedToolchainBuildRecordDigest"], raw["trustedRecoveryLauncherDigest"], raw["executorIdentityDigest"], raw["profileIdentityDigest"], raw["brokerAuthRegistryGeneration"], raw["brokerAuthRegistryDigest"], raw["brokerAuthRevocationDigest"], raw["brokerAuthPolicyDigest"], raw["brokerAuthAllowedSignersDigest"], raw["brokerAuthKeyId"], raw["brokerAuthLeaseRequestDigest"], raw["brokerAuthSequenceRootIdentityDigest"], raw["brokerAuthIssueEntryDigest"], raw["brokerAuthSequenceHeadDigest"], raw["brokerAuthLeaseIssuedSequence"], raw["brokerAuthLeaseExpirySequence"], raw["leaseIdentityDigest"], raw["state"], raw["brokerSignatureHex"], raw["brokerMac"])
        if raw["canonicalDigest"] != item.canonical_digest:
            raise ValueError("registration recovery lease digest is invalid")
        return item

    def identity_preimage(self) -> RegistrationRecoveryLeaseIdentityPreimageV1:
        return RegistrationRecoveryLeaseIdentityPreimageV1(
            "trusted-registration-recovery-lease-identity-preimage/v1", self.project_id,
            self.operation_id, self.lifecycle_nonce, self.session_family_authorization_digest,
            self.broker_auth_lease_request_digest, self.broker_auth_key_id,
            self.broker_auth_lease_issued_sequence, self.broker_auth_issue_entry_digest,
            self.broker_auth_sequence_head_digest, self.broker_auth_registry_generation,
            self.broker_auth_registry_digest, self.broker_auth_revocation_digest,
            self.broker_auth_policy_digest, self.broker_auth_allowed_signers_digest,
            self.broker_auth_sequence_root_identity_digest, self.broker_auth_lease_expiry_sequence,
            self.transaction_parent_identity_digest, self.transaction_root_identity_digest,
            self.operation_directory_identity_digest, self.repository_identity_digest,
            self.repository_boundary_identity_digest, self.trusted_toolchain_build_record_digest,
            self.trusted_recovery_launcher_digest, self.executor_identity_digest,
            self.profile_identity_digest,
        )

    def verify_signature(self, governance: RegistrationBrokerAuthGovernanceV1, current_sequence: int) -> None:
        """Verify the public lease signature and its frozen trust-state facts."""
        if type(governance) is not RegistrationBrokerAuthGovernanceV1:
            raise ValueError("lease governance is invalid")
        registry, revocations, allowed_signers = governance.registry, governance.revocations, governance.allowed_signers
        if (registry.generation, registry.canonical_digest, revocations.canonical_digest, revocations.registry_digest, governance.allowed_signers_digest) != (self.broker_auth_registry_generation, self.broker_auth_registry_digest, self.broker_auth_revocation_digest, registry.canonical_digest, self.broker_auth_allowed_signers_digest):
            raise ValueError("lease trust state changed")
        key = registry.key(self.broker_auth_key_id)
        if key.state not in {"active", "retiring"} or self.broker_auth_lease_issued_sequence < key.effective_lease_sequence or (key.retire_after_lease_sequence is not None and self.broker_auth_lease_issued_sequence > key.retire_after_lease_sequence):
            raise ValueError("lease signing key is not eligible")
        public_key = allowed_signers.get(key.key_id)
        if not isinstance(public_key, bytes) or len(public_key) != 32 or "sha256:" + hashlib.sha256(public_key).hexdigest() != key.public_key_digest:
            raise ValueError("lease allowed signer is invalid")
        if self.lease_identity_digest != self.identity_preimage().lease_identity_digest:
            raise ValueError("lease identity preimage is invalid")
        self.assert_valid_at(current_sequence, revocations)
        from .handoffs import _ed25519_verify
        if not _ed25519_verify(public_key, _BROKER_LEASE_DOMAIN.encode("utf-8") + b"\0" + canonical_json_bytes(self._unsigned_wire()), bytes.fromhex(self.broker_signature_hex)):
            raise ValueError("lease signature is invalid")

    def assert_valid_at(self, current_sequence: int, revocations: RegistrationBrokerAuthRevocationsV1) -> None:
        _u64(current_sequence, "current_sequence")
        if type(revocations) is not RegistrationBrokerAuthRevocationsV1 or revocations.canonical_digest != self.broker_auth_revocation_digest or current_sequence > self.broker_auth_lease_expiry_sequence:
            raise ValueError("lease is expired or revocation state changed")
        revoked_at = revocations.revoked_at(self.broker_auth_key_id)
        if revoked_at is not None and current_sequence >= revoked_at:
            raise ValueError("lease signing key is globally revoked")


@dataclass(frozen=True, slots=True)
class BrokerAuthStateTransitionResultV1:
    """A verified in-memory broker-auth transition; no journal is persisted here."""

    payload: BrokerAuthSequenceEventPayloadV1
    entry: BrokerAuthSequenceJournalEntryV1
    state: BrokerAuthSequenceStateV1
    journal: BrokerAuthSequenceJournalV1
    payloads: Mapping[str, BrokerAuthSequenceEventPayloadV1]


@dataclass(frozen=True, slots=True)
class BrokerAuthLeaseIssuanceResultV1(BrokerAuthStateTransitionResultV1):
    lease: RegistrationRecoveryLeaseV1


class BrokerAuthAuthoritativeIssuerV1:
    """The sole portable authority for request → payload → head → signed lease.

    It deliberately retains only in-memory state.  Task 14 owns durable journal
    installation, broker process isolation, and any repository mutation.
    """

    __slots__ = (
        "_registry", "_revocations", "_state", "_entries", "_payloads", "_journal",
        "_root_identity_digest", "_allowed_signers_digest", "_signer", "_allowed_signers",
        "_governance", "_state_mac", "_frozen_profile_ttl_sequences", "_event_ids",
    )

    def __init__(
        self,
        governance: RegistrationBrokerAuthGovernanceV1,
        state: BrokerAuthSequenceStateV1,
        root_identity_digest: str,
        signer: RegistrationBrokerAuthPrivateKeySlotV1,
        state_mac: RegistrationBrokerStateMacCapabilityV1,
        frozen_profile_ttl_sequences: int,
    ) -> None:
        self._governance, self._registry, self._revocations, self._state = governance, governance.registry, governance.revocations, state
        self._entries: tuple[BrokerAuthSequenceJournalEntryV1, ...] = ()
        self._payloads: dict[str, BrokerAuthSequenceEventPayloadV1] = {}
        self._journal: BrokerAuthSequenceJournalV1 | None = None
        self._root_identity_digest, self._allowed_signers_digest = root_identity_digest, governance.allowed_signers_digest
        self._signer, self._allowed_signers, self._state_mac = signer, dict(governance.allowed_signers), state_mac
        self._frozen_profile_ttl_sequences, self._event_ids = _u64(frozen_profile_ttl_sequences, "frozen_profile_ttl_sequences", positive=True), set()

    @classmethod
    def create(
        cls,
        governance: RegistrationBrokerAuthGovernanceV1,
        root_identity_digest: str,
        policy_digest: str,
        signer: RegistrationBrokerAuthPrivateKeySlotV1,
        state_mac: RegistrationBrokerStateMacCapabilityV1,
        frozen_profile_ttl_sequences: int,
    ) -> "BrokerAuthAuthoritativeIssuerV1":
        if type(governance) is not RegistrationBrokerAuthGovernanceV1 or type(signer) is not RegistrationBrokerAuthPrivateKeySlotV1 or type(state_mac) is not RegistrationBrokerStateMacCapabilityV1:
            raise ValueError("broker issuer trust inputs are invalid")
        for value, field in ((root_identity_digest, "root_identity_digest"), (policy_digest, "policy_digest")):
            _require_digest(value, field)
        provisional = BrokerAuthSequenceStateV1(
            "trusted-registration-broker-auth-sequence-state/v1", root_identity_digest, 0,
            None, None, None, policy_digest, governance.registry.canonical_digest,
            governance.revocations.canonical_digest, "0" * 64,
        )
        state = replace(
            provisional,
            broker_state_mac_hex=state_mac.mac(canonical_json_bytes(provisional._unsigned_wire())),
        )
        issuer = cls(governance, state, root_identity_digest, signer, state_mac, frozen_profile_ttl_sequences)
        issuer._require_active_signer()
        return issuer

    @property
    def registry(self) -> RegistrationBrokerAuthRegistryV1:
        return self._registry

    @property
    def revocations(self) -> RegistrationBrokerAuthRevocationsV1:
        return self._revocations

    @property
    def state(self) -> BrokerAuthSequenceStateV1:
        return self._state

    @property
    def entries(self) -> tuple[BrokerAuthSequenceJournalEntryV1, ...]:
        return self._entries

    @property
    def payloads(self) -> dict[str, BrokerAuthSequenceEventPayloadV1]:
        return dict(self._payloads)

    def replace_signer(self, signer: RegistrationBrokerAuthPrivateKeySlotV1, governance: RegistrationBrokerAuthGovernanceV1) -> None:
        if type(signer) is not RegistrationBrokerAuthPrivateKeySlotV1 or type(governance) is not RegistrationBrokerAuthGovernanceV1:
            raise ValueError("broker signer is invalid")
        previous_signer, previous_allowed, previous_digest, previous_governance = self._signer, self._allowed_signers, self._allowed_signers_digest, self._governance
        self._signer, self._allowed_signers, self._allowed_signers_digest, self._governance = signer, dict(governance.allowed_signers), governance.allowed_signers_digest, governance
        try:
            self._require_active_signer()
        except Exception:
            self._signer, self._allowed_signers, self._allowed_signers_digest, self._governance = previous_signer, previous_allowed, previous_digest, previous_governance
            raise

    def _require_active_signer(self) -> RegistrationBrokerAuthKeyRegistrationV1:
        key = self._registry.key(self._registry.active_key_id)
        public = self._allowed_signers.get(key.key_id)
        if key.state != "active" or self._revocations.revoked_at(key.key_id) is not None or not isinstance(public, bytes) or len(public) != 32:
            raise ValueError("broker active signer is unavailable")
        if (self._signer.key_id, self._signer.purpose, self._signer.lease_signature_domain, self._signer.public_key_digest) != (key.key_id, _BROKER_AUTH_PURPOSE, key.lease_signature_domain, key.public_key_digest):
            raise ValueError("broker signer does not match active key")
        if "sha256:" + hashlib.sha256(public).hexdigest() != key.public_key_digest:
            raise ValueError("broker allowed signer does not match active key")
        return key

    def _next_sequence(self) -> int:
        return _checked_add(self._state.current_sequence, 1)

    def _transition(
        self,
        payload: BrokerAuthSequenceEventPayloadV1,
        next_registry: RegistrationBrokerAuthRegistryV1,
        next_revocations: RegistrationBrokerAuthRevocationsV1,
    ) -> BrokerAuthStateTransitionResultV1:
        signer_key = self._require_active_signer()
        if payload.event_id_digest in self._event_ids or payload.pre_policy_digest != self._state.current_policy_digest:
            raise ValueError("broker event is replayed or has the wrong pre-state")
        sequence = self._next_sequence()
        if isinstance(payload, BrokerAuthLeaseIssueEventPayloadV1):
            if payload.issued_sequence != sequence or payload.broker_auth_key_id != signer_key.key_id:
                raise ValueError("broker issue event sequence is invalid")
        elif isinstance(payload, BrokerAuthEnvelopeSignEventPayloadV1):
            if payload.sign_sequence != sequence or payload.broker_auth_key_id != signer_key.key_id:
                raise ValueError("broker sign event sequence is invalid")
        elif isinstance(payload, BrokerAuthRotatePolicyEventPayloadV1):
            if payload.effective_lease_sequence != sequence:
                raise ValueError("broker rotate event sequence is invalid")
        elif isinstance(payload, BrokerAuthRevokePolicyEventPayloadV1):
            if payload.revoked_at_broker_sequence != sequence:
                raise ValueError("broker revoke event sequence is invalid")
        else:
            raise ValueError("broker event payload is invalid")
        if next_revocations.registry_digest != next_registry.canonical_digest:
            raise ValueError("broker post-state revocations do not close registry")
        provisional_entry = BrokerAuthSequenceJournalEntryV1(
            "trusted-registration-broker-auth-sequence-entry/v1", sequence,
            self._state.head_entry_digest, payload.event_id_digest, payload.event_kind,
            payload.canonical_digest, len(payload.canonical_bytes), payload.canonical_digest,
            payload.pre_policy_digest, payload.post_policy_digest, next_registry.canonical_digest,
            next_revocations.canonical_digest, signer_key.key_id, "0" * 64,
        )
        entry = replace(
            provisional_entry,
            broker_state_mac_hex=self._state_mac.mac(canonical_json_bytes(provisional_entry._unsigned_wire())),
        )
        provisional_state = BrokerAuthSequenceStateV1(
            "trusted-registration-broker-auth-sequence-state/v1", self._root_identity_digest,
            sequence, entry.canonical_digest, payload.canonical_digest, payload.canonical_digest,
            payload.post_policy_digest, next_registry.canonical_digest, next_revocations.canonical_digest,
            "0" * 64,
        )
        state = replace(
            provisional_state,
            broker_state_mac_hex=self._state_mac.mac(canonical_json_bytes(provisional_state._unsigned_wire())),
        )
        entries = (*self._entries, entry)
        provisional_journal = BrokerAuthSequenceJournalV1(
            "trusted-registration-broker-auth-sequence-journal/v1", self._root_identity_digest, 1,
            entries, "0" * 64,
        )
        journal = replace(
            provisional_journal,
            broker_state_mac_hex=self._state_mac.mac(canonical_json_bytes(provisional_journal._unsigned_wire())),
        )
        payloads = MappingProxyType({**self._payloads, payload.canonical_digest: payload})
        journal.validate_state(state, payloads)
        return BrokerAuthStateTransitionResultV1(payload, entry, state, journal, payloads)

    def _commit(
        self,
        result: BrokerAuthStateTransitionResultV1,
        registry: RegistrationBrokerAuthRegistryV1,
        revocations: RegistrationBrokerAuthRevocationsV1,
    ) -> None:
        self._registry, self._revocations, self._state = registry, revocations, result.state
        self._entries, self._payloads, self._journal = (*self._entries, result.entry), dict(result.payloads), result.journal
        self._event_ids.add(result.payload.event_id_digest)

    def issue_lease(
        self,
        request: BrokerAuthLeaseIssueRequestV1,
        family: RegistrationWorkerSessionFamilyAuthorizationV1,
        lease_template: RegistrationRecoveryLeaseV1,
    ) -> BrokerAuthLeaseIssuanceResultV1:
        if type(request) is not BrokerAuthLeaseIssueRequestV1 or type(family) is not RegistrationWorkerSessionFamilyAuthorizationV1 or type(lease_template) is not RegistrationRecoveryLeaseV1:
            raise ValueError("broker lease issue inputs are invalid")
        key = self._require_active_signer()
        expected_head = self._state.head_entry_digest or self._state.root_identity_digest
        if (request.operation_id, request.session_family_authorization_digest, request.broker_auth_key_id, request.expected_current_sequence, request.expected_current_head_digest, request.expected_registry_digest, request.expected_revocation_digest, request.expected_policy_digest, request.expected_allowed_signers_digest) != (family.operation_id, family.canonical_digest, key.key_id, self._state.current_sequence, expected_head, self._registry.canonical_digest, self._revocations.canonical_digest, self._state.current_policy_digest, self._allowed_signers_digest):
            raise ValueError("broker lease request does not match authoritative pre-state")
        if family.broker_auth_key_id != key.key_id or family.broker_auth_key_registration_digest != key.canonical_digest:
            raise ValueError("broker lease family authorization is not active")
        if request.lease_ttl_sequences != self._frozen_profile_ttl_sequences:
            raise ValueError("broker lease request TTL differs from frozen profile")
        if (lease_template.operation_id, lease_template.lifecycle_nonce, lease_template.session_family_authorization_digest, lease_template.broker_auth_lease_request_digest, lease_template.transaction_parent_identity_digest, lease_template.transaction_root_identity_digest, lease_template.operation_directory_identity_digest, lease_template.repository_identity_digest, lease_template.repository_boundary_identity_digest, lease_template.trusted_toolchain_build_record_digest, lease_template.trusted_recovery_launcher_digest, lease_template.executor_identity_digest, lease_template.profile_identity_digest) != (request.operation_id, request.lifecycle_nonce, family.canonical_digest, request.request_digest, family.transaction_parent_identity_digest, family.transaction_root_identity_digest, family.operation_directory_identity_digest, family.repository_identity_digest, family.repository_boundary_identity_digest, family.trusted_toolchain_build_record_digest, family.trusted_recovery_launcher_digest, family.executor_identity_digest, family.profile_identity_digest):
            raise ValueError("broker lease template does not bind request")
        sequence = self._next_sequence()
        event_id_digest = "sha256:" + hashlib.sha256(b"ai-video-reverse-engineer/registration-broker-lease-issue-event/v1\0" + request.request_digest.encode("ascii")).hexdigest()
        payload = BrokerAuthLeaseIssueEventPayloadV1(
            "trusted-registration-broker-auth-lease-issue-payload/v1", "issue-lease", event_id_digest,
            request, request.request_digest, request.operation_id, key.key_id, sequence,
            request.lease_ttl_sequences, _checked_add(sequence, request.lease_ttl_sequences),
            self._state.current_policy_digest, self._state.current_policy_digest,
        )
        transition = self._transition(payload, self._registry, self._revocations)
        lease = replace(
            lease_template, broker_auth_registry_generation=self._registry.generation,
            broker_auth_registry_digest=self._registry.canonical_digest,
            broker_auth_revocation_digest=self._revocations.canonical_digest,
            broker_auth_policy_digest=transition.state.current_policy_digest,
            broker_auth_allowed_signers_digest=self._allowed_signers_digest, broker_auth_key_id=key.key_id,
            broker_auth_lease_request_digest=request.request_digest,
            broker_auth_sequence_root_identity_digest=self._root_identity_digest,
            broker_auth_issue_entry_digest=transition.entry.canonical_digest,
            broker_auth_sequence_head_digest=transition.state.head_entry_digest,
            broker_auth_lease_issued_sequence=sequence,
            broker_auth_lease_expiry_sequence=payload.expiry_sequence, lease_identity_digest="sha256:" + "0" * 64,
        )
        lease = replace(lease, lease_identity_digest=lease.identity_preimage().lease_identity_digest)
        lease = replace(lease, broker_signature_hex=self._signer.sign(_BROKER_LEASE_DOMAIN, canonical_json_bytes(lease._unsigned_wire())))
        lease = replace(lease, broker_mac=self._state_mac.mac(canonical_json_bytes(lease._unsigned_wire())))
        lease.verify_signature(self._governance, sequence)
        result = BrokerAuthLeaseIssuanceResultV1(transition.payload, transition.entry, transition.state, transition.journal, transition.payloads, lease)
        self._commit(result, self._registry, self._revocations)
        return result

    def rotate_registry(
        self,
        next_registry: RegistrationBrokerAuthRegistryV1,
        next_revocations: RegistrationBrokerAuthRevocationsV1,
        event_id_digest: str,
        post_policy_digest: str,
    ) -> BrokerAuthStateTransitionResultV1:
        if type(next_registry) is not RegistrationBrokerAuthRegistryV1 or type(next_revocations) is not RegistrationBrokerAuthRevocationsV1:
            raise ValueError("broker rotation inputs are invalid")
        sequence = self._next_sequence()
        payload = BrokerAuthRotatePolicyEventPayloadV1(
            "trusted-registration-broker-auth-rotate-payload/v1", "rotate-registry", event_id_digest,
            self._registry.canonical_digest, self._revocations.canonical_digest, next_registry,
            next_revocations, next_registry.active_key_id, self._registry.active_key_id, sequence,
            self._state.current_policy_digest, post_policy_digest,
        )
        if next_registry.generation != self._registry.generation + 1 or tuple(next_revocations.entries) != tuple(self._revocations.entries):
            raise ValueError("broker rotation does not preserve prior state")
        result = self._transition(payload, next_registry, next_revocations)
        self._commit(result, next_registry, next_revocations)
        return result

    def sign_envelope(
        self,
        operation_id: str,
        lease_identity_digest: str,
        lease_request_digest: str,
        unsigned_envelope_digest: str,
        event_id_digest: str,
    ) -> BrokerAuthStateTransitionResultV1:
        _require_text(operation_id, "operation_id")
        for value, field in (
            (lease_identity_digest, "lease_identity_digest"),
            (lease_request_digest, "lease_request_digest"),
            (unsigned_envelope_digest, "unsigned_envelope_digest"),
            (event_id_digest, "event_id_digest"),
        ):
            _require_digest(value, field)
        key = self._require_active_signer()
        sequence = self._next_sequence()
        payload = BrokerAuthEnvelopeSignEventPayloadV1(
            "trusted-registration-broker-auth-envelope-sign-payload/v1", "sign-envelope",
            event_id_digest, operation_id, lease_identity_digest, lease_request_digest,
            unsigned_envelope_digest, key.key_id, sequence, self._state.current_policy_digest,
            self._state.current_policy_digest,
        )
        result = self._transition(payload, self._registry, self._revocations)
        self._commit(result, self._registry, self._revocations)
        return result

    def revoke_key(
        self,
        revoked_key_id: str,
        next_revocations: RegistrationBrokerAuthRevocationsV1,
        event_id_digest: str,
        post_policy_digest: str,
    ) -> BrokerAuthStateTransitionResultV1:
        _require_text(revoked_key_id, "revoked_key_id")
        self._registry.key(revoked_key_id)
        if type(next_revocations) is not RegistrationBrokerAuthRevocationsV1:
            raise ValueError("broker revocation inputs are invalid")
        sequence = self._next_sequence()
        prior = {item.key_id: item for item in self._revocations.entries}
        following = {item.key_id: item for item in next_revocations.entries}
        if following.get(revoked_key_id) is None or following[revoked_key_id].revoked_at_broker_sequence != sequence or {key: value for key, value in following.items() if key != revoked_key_id} != prior:
            raise ValueError("broker revocation does not preserve prior entries")
        payload = BrokerAuthRevokePolicyEventPayloadV1(
            "trusted-registration-broker-auth-revoke-payload/v1", "revoke-key", event_id_digest,
            self._registry.canonical_digest, self._revocations.canonical_digest, next_revocations,
            revoked_key_id, sequence, self._state.current_policy_digest, post_policy_digest,
        )
        result = self._transition(payload, self._registry, next_revocations)
        self._commit(result, self._registry, next_revocations)
        return result


# E.3 is intentionally a portable, in-memory broker boundary.  It specifies
# exactly what Task 14 must validate before it reaches a repository syscall;
# it does not open a repository, spawn a worker, or implement a socket runtime.
_MUTATION_PHASES = {"PREPARED", "WRITING_CAS", "WRITING_AUDIT", "CATALOG_COMMITTING", "ACTIVATED", "FAILED_BLOCKED"}
_MUTATION_ACTIONS = {
    "persist-intent", "install-cas", "write-audit", "rename-catalog",
    "fsync-catalog-parent", "append-terminal", "safe-cleanup",
}
_MUTATION_RESULTS = {"EXECUTED", "NOT_EXECUTED", "ALREADY_EXACT"}
_FORWARD_ONLY_ACTIONS = ("fsync-catalog-parent", "append-terminal")


class RegistrationMutationChannelBindingV1:
    """Worker-local, non-serializable sequence state for one received channel."""

    __slots__ = (
        "operation_id", "lease_identity_digest", "lifecycle_nonce", "worker_session_id",
        "worker_process_identity_digest", "worker_sequence", "broker_session_nonce",
        "control_socket_identity_digest", "channel_binding_digest", "_next_sequence",
    )
    _TOKEN = object()

    def __init__(self, token: object, envelope: CapabilityEnvelopeUnsignedV1, control_socket_identity_digest: str) -> None:
        if token is not self._TOKEN:
            raise TypeError("mutation channel bindings are created from a received capability envelope")
        self.operation_id = envelope.operation_id
        self.lease_identity_digest = envelope.broker_lease_identity_digest
        self.lifecycle_nonce = envelope.lifecycle_nonce
        self.worker_session_id = envelope.worker_session_id
        self.worker_process_identity_digest = envelope.worker_process_identity_digest
        self.worker_sequence = envelope.worker_sequence
        self.broker_session_nonce = envelope.broker_session_nonce
        self.control_socket_identity_digest = control_socket_identity_digest
        self.channel_binding_digest = canonical_digest({
            "brokerSessionNonce": self.broker_session_nonce,
            "controlSocketIdentityDigest": control_socket_identity_digest,
            "leaseIdentityDigest": self.lease_identity_digest,
            "lifecycleNonce": self.lifecycle_nonce,
            "operationId": self.operation_id,
            "workerProcessIdentityDigest": self.worker_process_identity_digest,
            "workerSequence": self.worker_sequence,
            "workerSessionId": self.worker_session_id,
        })
        self._next_sequence = 0

    @classmethod
    def create(cls, envelope: CapabilityEnvelopeUnsignedV1, control_socket_identity_digest: str) -> "RegistrationMutationChannelBindingV1":
        if type(envelope) is not CapabilityEnvelopeUnsignedV1:
            raise TypeError("mutation channel binding requires an unsigned capability envelope")
        _require_digest(control_socket_identity_digest, "control_socket_identity_digest")
        return cls(cls._TOKEN, envelope, control_socket_identity_digest)

    def _consume_next_sequence(self) -> int:
        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def __copy__(self):
        raise TypeError("mutation channel bindings are non-copyable")

    def __deepcopy__(self, memo):
        raise TypeError("mutation channel bindings are non-copyable")

    def __reduce__(self):
        raise TypeError("mutation channel bindings are non-serializable")


@dataclass(frozen=True, slots=True)
class RegistrationMutationSenderEvidenceV1:
    """Task 14 supplied, already-authenticated sender facts; never worker input."""

    channel_binding_digest: str
    worker_process_identity_digest: str
    operation_id: str
    worker_session_id: str
    worker_sequence: int

    def __post_init__(self) -> None:
        for field in ("channel_binding_digest", "worker_process_identity_digest"):
            _require_digest(getattr(self, field), field)
        for field in ("operation_id", "worker_session_id"):
            _require_text(getattr(self, field), field)
        _u64(self.worker_sequence, "worker_sequence", positive=True)

    @classmethod
    def from_binding(cls, binding: RegistrationMutationChannelBindingV1) -> "RegistrationMutationSenderEvidenceV1":
        if type(binding) is not RegistrationMutationChannelBindingV1:
            raise TypeError("sender evidence requires a mutation channel binding")
        return cls(binding.channel_binding_digest, binding.worker_process_identity_digest, binding.operation_id, binding.worker_session_id, binding.worker_sequence)


@dataclass(frozen=True, slots=True)
class RegistrationMutationRequestV1:
    schema: str
    operation_id: str
    lifecycle_nonce: str
    worker_session_id: str
    worker_process_identity_digest: str
    worker_sequence: int
    mutation_sequence: int
    channel_binding_digest: str
    expected_registration_state_digest: str
    expected_transition_digest: str
    expected_phase: str
    action: str
    payload_digest: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-mutation-request/v1" or self.expected_phase not in _MUTATION_PHASES or self.action not in _MUTATION_ACTIONS:
            raise ValueError("registration mutation request identity is invalid")
        for field in ("operation_id", "lifecycle_nonce", "worker_session_id"):
            _require_text(getattr(self, field), field)
        for field in ("worker_process_identity_digest", "channel_binding_digest", "expected_registration_state_digest", "expected_transition_digest", "payload_digest"):
            _require_digest(getattr(self, field), field)
        _u64(self.worker_sequence, "worker_sequence", positive=True)
        _u64(self.mutation_sequence, "mutation_sequence")

    def _unsigned_wire(self) -> dict[str, object]:
        return {
            "action": self.action, "channelBindingDigest": self.channel_binding_digest,
            "expectedPhase": self.expected_phase, "expectedRegistrationStateDigest": self.expected_registration_state_digest,
            "expectedTransitionDigest": self.expected_transition_digest, "lifecycleNonce": self.lifecycle_nonce,
            "mutationSequence": self.mutation_sequence, "operationId": self.operation_id,
            "payloadDigest": self.payload_digest, "schema": self.schema,
            "workerProcessIdentityDigest": self.worker_process_identity_digest,
            "workerSequence": self.worker_sequence, "workerSessionId": self.worker_session_id,
        }

    @property
    def request_digest(self) -> str:
        return canonical_digest(self._unsigned_wire())

    def to_wire(self) -> dict[str, object]:
        return {**self._unsigned_wire(), "requestDigest": self.request_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def create(cls, channel_binding: RegistrationMutationChannelBindingV1, expected_state: object, transition: object, expected_phase: str, action: str, payload_digest: str) -> "RegistrationMutationRequestV1":
        if type(channel_binding) is not RegistrationMutationChannelBindingV1:
            raise TypeError("mutation request requires a worker mutation channel binding")
        state_digest = expected_state if isinstance(expected_state, str) else getattr(expected_state, "canonical_digest", None)
        transition_digest = transition if isinstance(transition, str) else getattr(transition, "canonical_digest", None)
        _require_digest(state_digest, "expected_state")
        _require_digest(transition_digest, "transition")
        _require_digest(payload_digest, "payload_digest")
        if expected_phase not in _MUTATION_PHASES or action not in _MUTATION_ACTIONS:
            raise ValueError("registration mutation request phase or action is invalid")
        return cls(
            "trusted-registration-mutation-request/v1", channel_binding.operation_id,
            channel_binding.lifecycle_nonce, channel_binding.worker_session_id,
            channel_binding.worker_process_identity_digest, channel_binding.worker_sequence,
            channel_binding._consume_next_sequence(), channel_binding.channel_binding_digest,
            state_digest, transition_digest, expected_phase, action, payload_digest,
        )

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "RegistrationMutationRequestV1":
        fields = {"action", "channelBindingDigest", "expectedPhase", "expectedRegistrationStateDigest", "expectedTransitionDigest", "lifecycleNonce", "mutationSequence", "operationId", "payloadDigest", "requestDigest", "schema", "workerProcessIdentityDigest", "workerSequence", "workerSessionId"}
        raw = _strict_object(payload, fields, "registration mutation request")
        result = cls(raw["schema"], raw["operationId"], raw["lifecycleNonce"], raw["workerSessionId"], raw["workerProcessIdentityDigest"], raw["workerSequence"], raw["mutationSequence"], raw["channelBindingDigest"], raw["expectedRegistrationStateDigest"], raw["expectedTransitionDigest"], raw["expectedPhase"], raw["action"], raw["payloadDigest"])
        if raw["requestDigest"] != result.request_digest:
            raise ValueError("registration mutation request digest is invalid")
        return result


@dataclass(frozen=True, slots=True)
class BrokerMutationReceiptV1:
    schema: str
    operation_id: str
    worker_session_id: str
    worker_sequence: int
    mutation_sequence: int
    request_digest: str
    result: str
    resulting_registration_state_digest: str
    broker_audit_mac: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-registration-mutation-receipt/v1" or self.result not in _MUTATION_RESULTS:
            raise ValueError("broker mutation receipt identity is invalid")
        for field in ("operation_id", "worker_session_id"):
            _require_text(getattr(self, field), field)
        for field in ("request_digest", "resulting_registration_state_digest"):
            _require_digest(getattr(self, field), field)
        _u64(self.worker_sequence, "worker_sequence", positive=True)
        _u64(self.mutation_sequence, "mutation_sequence")
        _mac(self.broker_audit_mac, "broker_audit_mac")

    def _unsigned_wire(self) -> dict[str, object]:
        return {"mutationSequence": self.mutation_sequence, "operationId": self.operation_id, "requestDigest": self.request_digest, "result": self.result, "resultingRegistrationStateDigest": self.resulting_registration_state_digest, "schema": self.schema, "workerSequence": self.worker_sequence, "workerSessionId": self.worker_session_id}

    @property
    def canonical_digest(self) -> str:
        return canonical_digest(self._unsigned_wire())

    def _mac_payload(self) -> bytes:
        return canonical_json_bytes({**self._unsigned_wire(), "canonicalDigest": self.canonical_digest})

    def to_wire(self) -> dict[str, object]:
        return {**self._unsigned_wire(), "brokerAuditMac": self.broker_audit_mac, "canonicalDigest": self.canonical_digest}

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_wire())

    @classmethod
    def create_after_result(cls, request: RegistrationMutationRequestV1, result: str, resulting_registration_state_digest: str, audit_key: bytes) -> "BrokerMutationReceiptV1":
        if type(request) is not RegistrationMutationRequestV1 or result not in _MUTATION_RESULTS or not isinstance(audit_key, bytes) or len(audit_key) < 16:
            raise ValueError("broker receipt inputs are invalid")
        unsigned = cls("trusted-registration-mutation-receipt/v1", request.operation_id, request.worker_session_id, request.worker_sequence, request.mutation_sequence, request.request_digest, result, resulting_registration_state_digest, "0" * 64)
        mac = hmac.new(audit_key, unsigned._mac_payload(), hashlib.sha256).hexdigest()
        return cls(unsigned.schema, unsigned.operation_id, unsigned.worker_session_id, unsigned.worker_sequence, unsigned.mutation_sequence, unsigned.request_digest, unsigned.result, unsigned.resulting_registration_state_digest, mac)

    def verify_audit_mac(self, audit_key: bytes) -> None:
        if not isinstance(audit_key, bytes) or len(audit_key) < 16 or not hmac.compare_digest(self.broker_audit_mac, hmac.new(audit_key, self._mac_payload(), hashlib.sha256).hexdigest()):
            raise ValueError("broker mutation receipt audit MAC is invalid")

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "BrokerMutationReceiptV1":
        fields = {"brokerAuditMac", "canonicalDigest", "mutationSequence", "operationId", "requestDigest", "result", "resultingRegistrationStateDigest", "schema", "workerSequence", "workerSessionId"}
        raw = _strict_object(payload, fields, "broker mutation receipt")
        result = cls(raw["schema"], raw["operationId"], raw["workerSessionId"], raw["workerSequence"], raw["mutationSequence"], raw["requestDigest"], raw["result"], raw["resultingRegistrationStateDigest"], raw["brokerAuditMac"])
        if raw["canonicalDigest"] != result.canonical_digest:
            raise ValueError("broker mutation receipt digest is invalid")
        return result


class RegistrationBrokerMutationVerifierV1:
    """Single-session verifier called before Task 14 invokes a repository action."""

    __slots__ = ("_binding", "_state_digest", "_transition_digest", "_phase", "_audit_key", "_next_sequence")
    _TOKEN = object()

    def __init__(self, token: object, binding: RegistrationMutationChannelBindingV1, state: str, transition: str, phase: str, audit_key: bytes) -> None:
        if token is not self._TOKEN:
            raise TypeError("broker mutation verifiers are created from an authenticated channel")
        self._binding, self._state_digest, self._transition_digest, self._phase, self._audit_key, self._next_sequence = binding, state, transition, phase, audit_key, 0

    @classmethod
    def create(cls, binding: RegistrationMutationChannelBindingV1, *, initial_state_digest: str, initial_transition_digest: str, initial_phase: str, audit_key: bytes) -> "RegistrationBrokerMutationVerifierV1":
        if type(binding) is not RegistrationMutationChannelBindingV1 or initial_phase not in _MUTATION_PHASES or not isinstance(audit_key, bytes) or len(audit_key) < 16:
            raise ValueError("broker mutation verifier inputs are invalid")
        _require_digest(initial_state_digest, "initial_state_digest")
        _require_digest(initial_transition_digest, "initial_transition_digest")
        return cls(cls._TOKEN, binding, initial_state_digest, initial_transition_digest, initial_phase, audit_key)

    def _verify_before_repository_syscall(self, request: RegistrationMutationRequestV1, sender: RegistrationMutationSenderEvidenceV1) -> None:
        if type(request) is not RegistrationMutationRequestV1 or type(sender) is not RegistrationMutationSenderEvidenceV1:
            raise ValueError("mutation request or sender evidence is invalid")
        binding = self._binding
        if (sender.channel_binding_digest, sender.worker_process_identity_digest, sender.operation_id, sender.worker_session_id, sender.worker_sequence) != (binding.channel_binding_digest, binding.worker_process_identity_digest, binding.operation_id, binding.worker_session_id, binding.worker_sequence):
            raise ValueError("actual sender does not match authenticated mutation channel")
        if (request.operation_id, request.lifecycle_nonce, request.worker_session_id, request.worker_process_identity_digest, request.worker_sequence, request.channel_binding_digest) != (binding.operation_id, binding.lifecycle_nonce, binding.worker_session_id, binding.worker_process_identity_digest, binding.worker_sequence, binding.channel_binding_digest):
            raise ValueError("mutation request does not match authenticated channel/session")
        if request.mutation_sequence != self._next_sequence:
            raise ValueError("mutation sequence is not the next single-use sequence")
        if (request.expected_registration_state_digest, request.expected_transition_digest, request.expected_phase) != (self._state_digest, self._transition_digest, self._phase):
            raise ValueError("mutation request expected state is stale or rebound")

    def dispatch(self, request: RegistrationMutationRequestV1, sender: RegistrationMutationSenderEvidenceV1, repository_action) -> BrokerMutationReceiptV1:
        self._verify_before_repository_syscall(request, sender)
        # Consume after all pure checks but before the callback: a retried packet
        # cannot become valid if a caller crashes while performing the action.
        self._next_sequence += 1
        outcome = repository_action()
        if not isinstance(outcome, tuple) or len(outcome) != 2:
            raise ValueError("repository action result must be (result, state digest)")
        result, state_digest = outcome
        receipt = BrokerMutationReceiptV1.create_after_result(request, result, state_digest, self._audit_key)
        self._state_digest = state_digest
        return receipt


class RegistrationForwardOnlyRecoveryAuthorityV1:
    """Root-broker-only capability for the two post-rename durability steps."""

    __slots__ = (
        "operation_id", "lease_identity_digest", "lease_request_digest", "broker_auth_key_id",
        "broker_auth_key_registration_digest", "rename_sequence", "rename_head_digest",
        "rename_policy_digest", "registration_transaction_intent_digest", "expected_catalog_digest",
        "catalog_parent_identity_digest", "registration_journal_identity_digest", "allowed_actions",
        "_canonical_digest", "_broker_state_mac",
    )
    _TOKEN = object()

    def __init__(self, token: object, *, operation_id: str, lease_identity_digest: str, lease_request_digest: str, broker_auth_key_id: str, broker_auth_key_registration_digest: str, rename_sequence: int, rename_head_digest: str, rename_policy_digest: str, registration_transaction_intent_digest: str, expected_catalog_digest: str, catalog_parent_identity_digest: str, registration_journal_identity_digest: str, allowed_actions: tuple[str, ...], broker_state_mac: str) -> None:
        if token is not self._TOKEN:
            raise TypeError("forward-only authorities are root-broker capabilities")
        _require_text(operation_id, "operation_id"); _require_text(broker_auth_key_id, "broker_auth_key_id")
        for value, field in ((lease_identity_digest, "lease_identity_digest"), (lease_request_digest, "lease_request_digest"), (broker_auth_key_registration_digest, "broker_auth_key_registration_digest"), (rename_head_digest, "rename_head_digest"), (rename_policy_digest, "rename_policy_digest"), (registration_transaction_intent_digest, "registration_transaction_intent_digest"), (expected_catalog_digest, "expected_catalog_digest"), (catalog_parent_identity_digest, "catalog_parent_identity_digest"), (registration_journal_identity_digest, "registration_journal_identity_digest")):
            _require_digest(value, field)
        _u64(rename_sequence, "rename_sequence", positive=True)
        if tuple(allowed_actions) != _FORWARD_ONLY_ACTIONS:
            raise ValueError("forward-only authority actions must be the exact fixed pair")
        self.operation_id, self.lease_identity_digest, self.lease_request_digest = operation_id, lease_identity_digest, lease_request_digest
        self.broker_auth_key_id, self.broker_auth_key_registration_digest = broker_auth_key_id, broker_auth_key_registration_digest
        self.rename_sequence, self.rename_head_digest, self.rename_policy_digest = rename_sequence, rename_head_digest, rename_policy_digest
        self.registration_transaction_intent_digest, self.expected_catalog_digest = registration_transaction_intent_digest, expected_catalog_digest
        self.catalog_parent_identity_digest, self.registration_journal_identity_digest = catalog_parent_identity_digest, registration_journal_identity_digest
        self.allowed_actions = _FORWARD_ONLY_ACTIONS
        self._canonical_digest = canonical_digest(self._unsigned_wire())
        _mac(broker_state_mac, "broker_state_mac")
        self._broker_state_mac = broker_state_mac

    def _unsigned_wire(self) -> dict[str, object]:
        return {"allowedActions": list(_FORWARD_ONLY_ACTIONS), "brokerAuthKeyId": self.broker_auth_key_id, "brokerAuthKeyRegistrationDigest": self.broker_auth_key_registration_digest, "catalogParentIdentityDigest": self.catalog_parent_identity_digest, "expectedCatalogDigest": self.expected_catalog_digest, "leaseIdentityDigest": self.lease_identity_digest, "leaseRequestDigest": self.lease_request_digest, "operationId": self.operation_id, "registrationJournalIdentityDigest": self.registration_journal_identity_digest, "registrationTransactionIntentDigest": self.registration_transaction_intent_digest, "renameHeadDigest": self.rename_head_digest, "renamePolicyDigest": self.rename_policy_digest, "renameSequence": self.rename_sequence, "schema": "trusted-registration-forward-only-recovery-authority/v1"}

    @property
    def canonical_digest(self) -> str:
        return self._canonical_digest

    @classmethod
    def create(cls, state_mac: RegistrationBrokerStateMacCapabilityV1, *, operation_id: str, lease_identity_digest: str, lease_request_digest: str, broker_auth_key_id: str, broker_auth_key_registration_digest: str, rename_sequence: int, rename_head_digest: str, rename_policy_digest: str, registration_transaction_intent_digest: str, expected_catalog_digest: str, catalog_parent_identity_digest: str, registration_journal_identity_digest: str, allowed_actions: tuple[str, ...] = _FORWARD_ONLY_ACTIONS) -> "RegistrationForwardOnlyRecoveryAuthorityV1":
        if type(state_mac) is not RegistrationBrokerStateMacCapabilityV1:
            raise ValueError("forward-only authority state MAC key is invalid")
        provisional = cls(cls._TOKEN, operation_id=operation_id, lease_identity_digest=lease_identity_digest, lease_request_digest=lease_request_digest, broker_auth_key_id=broker_auth_key_id, broker_auth_key_registration_digest=broker_auth_key_registration_digest, rename_sequence=rename_sequence, rename_head_digest=rename_head_digest, rename_policy_digest=rename_policy_digest, registration_transaction_intent_digest=registration_transaction_intent_digest, expected_catalog_digest=expected_catalog_digest, catalog_parent_identity_digest=catalog_parent_identity_digest, registration_journal_identity_digest=registration_journal_identity_digest, allowed_actions=allowed_actions, broker_state_mac="0" * 64)
        mac = state_mac.mac(canonical_json_bytes({**provisional._unsigned_wire(), "canonicalDigest": provisional.canonical_digest}))
        return cls(cls._TOKEN, operation_id=operation_id, lease_identity_digest=lease_identity_digest, lease_request_digest=lease_request_digest, broker_auth_key_id=broker_auth_key_id, broker_auth_key_registration_digest=broker_auth_key_registration_digest, rename_sequence=rename_sequence, rename_head_digest=rename_head_digest, rename_policy_digest=rename_policy_digest, registration_transaction_intent_digest=registration_transaction_intent_digest, expected_catalog_digest=expected_catalog_digest, catalog_parent_identity_digest=catalog_parent_identity_digest, registration_journal_identity_digest=registration_journal_identity_digest, allowed_actions=allowed_actions, broker_state_mac=mac)

    def verify_state_mac(self, state_mac: RegistrationBrokerStateMacCapabilityV1) -> None:
        if type(state_mac) is not RegistrationBrokerStateMacCapabilityV1:
            raise ValueError("forward-only authority state MAC key is invalid")
        state_mac.verify(canonical_json_bytes({**self._unsigned_wire(), "canonicalDigest": self.canonical_digest}), self._broker_state_mac)

    def __copy__(self):
        raise TypeError("forward-only authorities are non-copyable")

    def __deepcopy__(self, memo):
        raise TypeError("forward-only authorities are non-copyable")

    def __reduce__(self):
        raise TypeError("forward-only authorities are non-serializable")


def verify_registration_forward_only_recovery(
    authority: RegistrationForwardOnlyRecoveryAuthorityV1,
    state_mac: RegistrationBrokerStateMacCapabilityV1,
    journal: BrokerAuthSequenceJournalV1,
    state: BrokerAuthSequenceStateV1,
    payloads: Mapping[str, BrokerAuthSequenceEventPayloadV1],
    *,
    registry: RegistrationBrokerAuthRegistryV1,
    revocations: RegistrationBrokerAuthRevocationsV1,
    operation_id: str,
    lease_identity_digest: str,
    lease_request_digest: str,
    registration_transaction_intent_digest: str,
    catalog_digest: str,
    catalog_parent_identity_digest: str,
    registration_journal_identity_digest: str,
    action: str,
    resolved_issue_lease_identity_digests: Mapping[str, str] | None = None,
) -> None:
    """Fail closed unless the authenticated broker chain permits one fixed tail action.

    The caller supplies only broker-owned journal/state/policy views.  This
    verifier never receives a worker capability, a repository path, or an
    action callback, so it cannot itself perform a mutation.
    """
    if type(authority) is not RegistrationForwardOnlyRecoveryAuthorityV1:
        raise ValueError("forward-only authority is invalid")
    if type(journal) is not BrokerAuthSequenceJournalV1 or type(state) is not BrokerAuthSequenceStateV1:
        raise ValueError("forward-only broker chain is invalid")
    if type(registry) is not RegistrationBrokerAuthRegistryV1 or type(revocations) is not RegistrationBrokerAuthRevocationsV1:
        raise ValueError("forward-only broker policy is invalid")
    authority.verify_state_mac(state_mac)
    if action not in _FORWARD_ONLY_ACTIONS:
        raise ValueError("forward-only action is not authorized")
    if (operation_id, lease_identity_digest, lease_request_digest, registration_transaction_intent_digest, catalog_digest, catalog_parent_identity_digest, registration_journal_identity_digest) != (
        authority.operation_id, authority.lease_identity_digest, authority.lease_request_digest,
        authority.registration_transaction_intent_digest, authority.expected_catalog_digest,
        authority.catalog_parent_identity_digest, authority.registration_journal_identity_digest,
    ):
        raise ValueError("forward-only recovery identities changed")
    journal.validate_state(state, payloads)
    if state.current_sequence < authority.rename_sequence or state.broker_auth_registry_digest != registry.canonical_digest or state.broker_auth_revocation_digest != revocations.canonical_digest or revocations.registry_digest != registry.canonical_digest:
        raise ValueError("forward-only broker state is not current")
    bound_key = registry.key(authority.broker_auth_key_id)
    if bound_key.canonical_digest != authority.broker_auth_key_registration_digest:
        raise ValueError("forward-only authority key registration changed")
    rename_entry = next((entry for entry in journal.entries if entry.sequence == authority.rename_sequence), None)
    if rename_entry is None or (rename_entry.canonical_digest, rename_entry.post_policy_digest) != (authority.rename_head_digest, authority.rename_policy_digest):
        raise ValueError("forward-only rename head is not an authenticated ancestor")

    bound_key_revokes = 0
    for entry in journal.entries:
        if entry.sequence <= authority.rename_sequence:
            continue
        payload = payloads.get(entry.event_payload_ref)
        if payload is None:
            raise ValueError("forward-only chain payload is absent")
        if isinstance(payload, BrokerAuthLeaseIssueEventPayloadV1):
            if resolved_issue_lease_identity_digests is None:
                raise ValueError("post-rename issue lease identity is unavailable")
            derived_identity = resolved_issue_lease_identity_digests.get(entry.event_payload_ref)
            _require_digest(derived_identity, "post-rename issue lease identity")
            if (
                payload.operation_id == authority.operation_id
                or derived_identity == authority.lease_identity_digest
                or payload.lease_request_digest == authority.lease_request_digest
                or payload.broker_auth_key_id == authority.broker_auth_key_id
            ):
                raise ValueError("post-rename issue is not unrelated to forward-only authority")
        elif isinstance(payload, BrokerAuthEnvelopeSignEventPayloadV1):
            if (
                payload.operation_id == authority.operation_id
                or payload.lease_identity_digest == authority.lease_identity_digest
                or payload.lease_request_digest == authority.lease_request_digest
                or payload.broker_auth_key_id == authority.broker_auth_key_id
            ):
                raise ValueError("post-rename signing is not unrelated to forward-only authority")
        elif isinstance(payload, BrokerAuthRotatePolicyEventPayloadV1):
            try:
                next_bound_key = payload.next_registry.key(authority.broker_auth_key_id)
            except ValueError as error:
                raise ValueError("post-rename rotation removed authority key") from error
            if next_bound_key.canonical_digest != authority.broker_auth_key_registration_digest:
                raise ValueError("post-rename rotation changed authority key registration")
        elif isinstance(payload, BrokerAuthRevokePolicyEventPayloadV1):
            if payload.revoked_key_id == authority.broker_auth_key_id:
                bound_key_revokes += 1
                if bound_key_revokes > 1:
                    raise ValueError("authority key is revoked more than once")
        else:
            raise ValueError("post-rename event has an unknown type")

    current_revocation = revocations.revoked_at(authority.broker_auth_key_id)
    if (current_revocation is None and bound_key_revokes != 0) or (current_revocation is not None and bound_key_revokes != 1):
        raise ValueError("forward-only key revocation is not represented exactly once")
