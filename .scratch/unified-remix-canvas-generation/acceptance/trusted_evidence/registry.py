"""Load the reviewed, static trust-policy files without executable hooks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlparse

from .models import AcceptanceProfile


_LEVELS = frozenset({"E1", "E2", "E3", "E4"})
_IMPLEMENTATION_TYPES = MappingProxyType(
    {"e1.git-license.v1": "E1", "e2.argv-suite.v1": "E2"}
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SAFE_ENVIRONMENT = MappingProxyType(
    {"CI": "1", "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"}
)
_SUITE_PARSERS = frozenset({"pytest-json-v1", "vitest-json-v1"})
_GIT_SOURCE_KINDS = frozenset({"repository", "bundle"})
_REUSE_KINDS = frozenset(
    {"direct-copy", "algorithm-port", "structural-adaptation", "reference-only"}
)
_GIT_REMOTE_SCHEMES = frozenset({"https", "ssh", "git"})
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SUBJECT_REPOSITORY = "git://product/repository"


class PolicyError(ValueError):
    """A fail-closed static-policy error with a stable bridge error code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifierType:
    identifier: str
    level: str


@dataclass(frozen=True, slots=True)
class VerifierDefinition:
    verifier_id: str
    type_id: str
    level: str
    definition: E1GitDefinition | E2SuiteDefinition


@dataclass(frozen=True, slots=True)
class TrustedRegistry:
    verifier_types: Mapping[str, VerifierType]
    verifiers: Mapping[str, VerifierDefinition]

    def verifiers_for_level(self, level: str) -> tuple[VerifierDefinition, ...]:
        return tuple(verifier for verifier in self.verifiers.values() if verifier.level == level)


@dataclass(frozen=True, slots=True)
class Reviewer:
    principal: str
    fingerprint: str
    roles: frozenset[str]
    profile_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReviewerPolicy:
    reviewers: Mapping[str, Reviewer]


@dataclass(frozen=True, slots=True)
class RevocationSet:
    fingerprints: frozenset[str]
    signature_digests: frozenset[str]
    evidence_digests: frozenset[str]


@dataclass(frozen=True, slots=True)
class GitObjectSource:
    kind: Literal["repository", "bundle"]
    path: Path
    digest: str | None


@dataclass(frozen=True, slots=True)
class GitBlobFact:
    digest: str
    local_path: str | None
    attribution: str | None


@dataclass(frozen=True, slots=True)
class LicenseFact:
    path: str
    spdx: str
    digest: str


@dataclass(frozen=True, slots=True)
class ReuseFact:
    kind: Literal["direct-copy", "algorithm-port", "structural-adaptation", "reference-only"]
    attribution: str


@dataclass(frozen=True, slots=True)
class E1GitDefinition:
    repository_id: str
    object_source: GitObjectSource
    remote_urls: tuple[str, ...]
    commit: str
    blobs: Mapping[str, GitBlobFact]
    license_fact: LicenseFact
    reuse: ReuseFact


@dataclass(frozen=True, slots=True, init=False)
class E2SuiteDefinition:
    id: str
    subject_repository: str
    working_directory: str
    setup_argv: tuple[str, ...]
    argv: tuple[str, ...]
    expected_test_ids: tuple[str, ...]
    parser: Literal["pytest-json-v1", "vitest-json-v1"]
    allowed_environment: Mapping[str, str]
    timeout_seconds: int
    lockfile_digests: Mapping[str, str]
    tool_versions: Mapping[str, str]

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "E2SuiteDefinition requires root-aware construction via "
            "E2SuiteDefinition.from_policy() or load_trusted_registry()"
        )

    @classmethod
    def from_policy(
        cls,
        *,
        root: Path,
        verifier_id: str,
        raw: object,
    ) -> E2SuiteDefinition:
        acceptance_root = _acceptance_root(root)
        suite = _snapshot_mapping(raw, "suite")
        allowed = {
            "subjectRepository",
            "workingDirectory",
            "setupArgv",
            "argv",
            "expectedTestIds",
            "parser",
            "allowedEnvironment",
            "timeoutSeconds",
            "lockfileDigests",
            "toolVersions",
        }
        required = allowed - {"setupArgv"}
        if set(suite) - allowed or required - set(suite):
            raise PolicyError("POINTER_INVALID", "suite contains an unsupported field")

        identifier = _nonempty_string(verifier_id, "suite.id")
        subject_repository = _nonempty_string(
            suite["subjectRepository"], "suite.subjectRepository"
        )
        if subject_repository != _SUBJECT_REPOSITORY:
            raise PolicyError("POINTER_INVALID", "suite.subjectRepository is not the product repository")
        working_directory = _controlled_relative_path(
            suite["workingDirectory"], "suite.workingDirectory", acceptance_root
        )
        setup_argv = (
            _literal_argv(suite["setupArgv"], "suite.setupArgv")
            if "setupArgv" in suite
            else ()
        )
        argv = _literal_argv(suite["argv"], "suite.argv")
        expected_test_ids = _literal_argv(suite["expectedTestIds"], "suite.expectedTestIds")
        if len(expected_test_ids) != len(set(expected_test_ids)):
            raise PolicyError("POINTER_INVALID", "suite.expectedTestIds must be unique")

        parser = _snapshot_mapping(suite["parser"], "suite.parser")
        _exact_keys(parser, {"kind"}, "suite.parser")
        parser_kind = _nonempty_string(parser["kind"], "suite.parser.kind")
        if parser_kind not in _SUITE_PARSERS:
            raise PolicyError("POINTER_INVALID", "suite.parser.kind is not registered")

        allowed_environment = _snapshot_mapping(
            suite["allowedEnvironment"], "suite.allowedEnvironment"
        )
        for name, value in allowed_environment.items():
            if name not in _SAFE_ENVIRONMENT or value != _SAFE_ENVIRONMENT[name]:
                raise PolicyError("POINTER_INVALID", "suite.allowedEnvironment has an unsafe value")

        timeout = suite["timeoutSeconds"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout <= 0
        ):
            raise PolicyError("POINTER_INVALID", "suite.timeoutSeconds must be a positive integer")

        lockfile_digests = _snapshot_mapping(
            suite["lockfileDigests"], "suite.lockfileDigests"
        )
        if not lockfile_digests:
            raise PolicyError("POINTER_INVALID", "suite.lockfileDigests must not be empty")
        for path, digest in lockfile_digests.items():
            _controlled_relative_path(path, "suite.lockfileDigests path", acceptance_root)
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise PolicyError(
                    "POINTER_INVALID", "suite.lockfileDigests values must be SHA-256 digests"
                )

        tool_versions = _snapshot_mapping(suite["toolVersions"], "suite.toolVersions")
        if not tool_versions:
            raise PolicyError("POINTER_INVALID", "suite.toolVersions must not be empty")
        for tool, version in tool_versions.items():
            if not isinstance(tool, str) or not _TOOL_NAME.fullmatch(tool):
                raise PolicyError("POINTER_INVALID", "suite.toolVersions keys must be tool names")
            if not isinstance(version, str) or not version or "\n" in version or "\r" in version:
                raise PolicyError(
                    "POINTER_INVALID", "suite.toolVersions values must be single-line strings"
                )

        definition = object.__new__(cls)
        object.__setattr__(definition, "id", identifier)
        object.__setattr__(definition, "subject_repository", subject_repository)
        object.__setattr__(definition, "working_directory", working_directory)
        object.__setattr__(definition, "setup_argv", setup_argv)
        object.__setattr__(definition, "argv", argv)
        object.__setattr__(definition, "expected_test_ids", expected_test_ids)
        object.__setattr__(definition, "parser", parser_kind)
        object.__setattr__(definition, "allowed_environment", MappingProxyType(allowed_environment))
        object.__setattr__(definition, "timeout_seconds", timeout)
        object.__setattr__(definition, "lockfile_digests", MappingProxyType(lockfile_digests))
        object.__setattr__(definition, "tool_versions", MappingProxyType(tool_versions))
        return definition


def _nonempty_string(value: object, field: str, code: str = "POINTER_INVALID") -> str:
    if not isinstance(value, str) or not value:
        raise PolicyError(code, f"{field} must be a non-empty string")
    return value


def _string_list(value: object, field: str, code: str = "POINTER_INVALID") -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise PolicyError(code, f"{field} must be a list of non-empty strings")
    return tuple(_nonempty_string(item, field, code) for item in value)


def _string_set(value: object, field: str, code: str = "POINTER_INVALID") -> frozenset[str]:
    return frozenset(_string_list(value, field, code))


def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("POINTER_INVALID", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _acceptance_root(root: Path) -> Path:
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise PolicyError("SOURCE_REF_UNREACHABLE", "acceptance root does not exist")
    return resolved


def _inside_root(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PolicyError("POINTER_INVALID", "policy path escapes acceptance root") from error
    return resolved


def _policy_file(root: Path, relative: str, *, required: bool) -> Path | None:
    path = _inside_root(root, root / relative)
    if not path.exists():
        if required:
            raise PolicyError("SOURCE_REF_UNREACHABLE", f"missing policy file: {relative}")
        return None
    if not path.is_file():
        raise PolicyError("POINTER_INVALID", f"policy path is not a file: {relative}")
    return path


def _read_json(root: Path, relative: str, *, required: bool = True) -> Mapping[str, object] | None:
    path = _policy_file(root, relative, required=required)
    if path is None:
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_without_duplicate_keys)
    except (OSError, json.JSONDecodeError) as error:
        raise PolicyError("POINTER_INVALID", f"invalid JSON in {relative}") from error
    if not isinstance(value, Mapping):
        raise PolicyError("POINTER_INVALID", f"{relative} must contain a JSON object")
    return value


def _exact_keys(value: Mapping[str, object], expected: Iterable[str], field: str) -> None:
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        unknown = sorted(actual - expected_set)
        missing = sorted(expected_set - actual)
        detail = []
        if unknown:
            detail.append(f"unknown fields: {', '.join(unknown)}")
        if missing:
            detail.append(f"missing fields: {', '.join(missing)}")
        raise PolicyError("POINTER_INVALID", f"{field} has {'; '.join(detail)}")


def _literal_argv(value: object, field: str) -> tuple[str, ...]:
    argv = _string_list(value, field)
    if not argv:
        raise PolicyError("POINTER_INVALID", f"{field} must not be empty")
    return argv


def _controlled_relative_path_text(value: object, field: str) -> str:
    path_text = _nonempty_string(value, field)
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise PolicyError("POINTER_INVALID", f"{field} must remain below acceptance root")
    return path_text


def _controlled_relative_path(value: object, field: str, root: Path) -> str:
    path_text = _controlled_relative_path_text(value, field)
    _inside_root(root, root / path_text)
    return path_text


def _required_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PolicyError("POINTER_INVALID", f"{field} must be an object")
    return value


def _snapshot_mapping(value: object, field: str) -> dict[str, object]:
    source = _required_mapping(value, field)
    try:
        snapshot = dict(source.items())
    except (TypeError, ValueError) as error:
        raise PolicyError("POINTER_INVALID", f"{field} must be an object") from error
    if any(not isinstance(key, str) for key in snapshot):
        raise PolicyError("POINTER_INVALID", f"{field} keys must be strings")
    return snapshot


def _validate_suite(value: object, root: Path, verifier_id: str) -> E2SuiteDefinition:
    return E2SuiteDefinition.from_policy(root=root, verifier_id=verifier_id, raw=value)


def _repository_relative_path(value: object, field: str) -> str:
    path_text = _nonempty_string(value, field)
    if "\\" in path_text:
        raise PolicyError("POINTER_INVALID", f"{field} must use a relative Git path")
    path = PurePosixPath(path_text)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise PolicyError("POINTER_INVALID", f"{field} must use a relative Git path")
    return path.as_posix()


def _digest(value: object, field: str) -> str:
    digest = _nonempty_string(value, field)
    if not _DIGEST.fullmatch(digest):
        raise PolicyError("POINTER_INVALID", f"{field} must be sha256:<64-hex>")
    return digest


def _git_object_source(value: object, root: Path) -> GitObjectSource:
    source = _required_mapping(value, "git.objectSource")
    kind = _nonempty_string(source.get("kind"), "git.objectSource.kind")
    if kind not in _GIT_SOURCE_KINDS:
        raise PolicyError("POINTER_INVALID", "git.objectSource.kind is not registered")
    expected = {"kind", "path"} | ({"sha256"} if kind == "bundle" else set())
    _exact_keys(source, expected, "git.objectSource")
    path_text = _controlled_relative_path(source["path"], "git.objectSource.path", root)
    digest = _digest(source["sha256"], "git.objectSource.sha256") if kind == "bundle" else None
    return GitObjectSource(
        kind=kind,  # type: ignore[arg-type]
        path=(root / path_text).resolve(),
        digest=digest,
    )


def _trusted_remote_urls(value: object) -> tuple[str, ...]:
    urls = _literal_argv(value, "git.remoteUrls")
    if len(urls) != len(set(urls)):
        raise PolicyError("POINTER_INVALID", "git.remoteUrls must be unique")
    for remote_url in urls:
        parsed = urlparse(remote_url)
        if (
            parsed.scheme not in _GIT_REMOTE_SCHEMES
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise PolicyError("POINTER_INVALID", "git.remoteUrls must be trusted URLs")
    return urls


def _git_blobs(value: object) -> Mapping[str, GitBlobFact]:
    blobs = _required_mapping(value, "git.blobs")
    if not blobs:
        raise PolicyError("POINTER_INVALID", "git.blobs must not be empty")
    parsed: dict[str, GitBlobFact] = {}
    for path, raw_fact in blobs.items():
        normalized_path = _repository_relative_path(path, "git.blobs path")
        if normalized_path in parsed:
            raise PolicyError("POINTER_INVALID", "git.blobs contains duplicate paths")
        fact = _required_mapping(raw_fact, f"git.blobs.{normalized_path}")
        allowed = {"sha256", "localPath", "attribution"}
        if set(fact) - allowed or "sha256" not in fact:
            raise PolicyError("POINTER_INVALID", "git blob fact has unsupported fields")
        has_local_path = "localPath" in fact
        has_attribution = "attribution" in fact
        if has_local_path != has_attribution:
            raise PolicyError("POINTER_INVALID", "git blob localPath and attribution must appear together")
        parsed[normalized_path] = GitBlobFact(
            digest=_digest(fact["sha256"], "git blob sha256"),
            local_path=(
                _repository_relative_path(fact["localPath"], "git blob localPath")
                if has_local_path
                else None
            ),
            attribution=(
                _nonempty_string(fact["attribution"], "git blob attribution")
                if has_attribution
                else None
            ),
        )
    return MappingProxyType(parsed)


def _license_fact(value: object) -> LicenseFact:
    fact = _required_mapping(value, "git.licenseFact")
    _exact_keys(fact, {"path", "spdx", "sha256"}, "git.licenseFact")
    return LicenseFact(
        path=_repository_relative_path(fact["path"], "git.licenseFact.path"),
        spdx=_nonempty_string(fact["spdx"], "git.licenseFact.spdx"),
        digest=_digest(fact["sha256"], "git.licenseFact.sha256"),
    )


def _reuse_fact(value: object) -> ReuseFact:
    fact = _required_mapping(value, "git.reuse")
    _exact_keys(fact, {"kind", "attribution"}, "git.reuse")
    kind = _nonempty_string(fact["kind"], "git.reuse.kind")
    if kind not in _REUSE_KINDS:
        raise PolicyError("POINTER_INVALID", "git.reuse.kind is not registered")
    return ReuseFact(
        kind=kind,  # type: ignore[arg-type]
        attribution=_nonempty_string(fact["attribution"], "git.reuse.attribution"),
    )


def _validate_git_definition(value: object, root: Path) -> E1GitDefinition:
    definition = _required_mapping(value, "git definition")
    _exact_keys(
        definition,
        {"repositoryId", "objectSource", "remoteUrls", "commit", "blobs", "licenseFact", "reuse"},
        "git definition",
    )
    commit = _nonempty_string(definition["commit"], "git.commit")
    if not _COMMIT.fullmatch(commit):
        raise PolicyError("POINTER_INVALID", "git.commit must be a complete 40-hex commit")
    return E1GitDefinition(
        repository_id=_nonempty_string(definition["repositoryId"], "git.repositoryId"),
        object_source=_git_object_source(definition["objectSource"], root),
        remote_urls=_trusted_remote_urls(definition["remoteUrls"]),
        commit=commit,
        blobs=_git_blobs(definition["blobs"]),
        license_fact=_license_fact(definition["licenseFact"]),
        reuse=_reuse_fact(definition["reuse"]),
    )


def _parse_registry(raw: Mapping[str, object], root: Path) -> TrustedRegistry:
    _exact_keys(raw, {"version", "verifierTypes", "verifiers"}, "trusted registry")
    if raw["version"] != 1:
        raise PolicyError("POINTER_INVALID", "unsupported trusted registry version")
    types_raw = raw["verifierTypes"]
    entries_raw = raw["verifiers"]
    if not isinstance(types_raw, Mapping) or isinstance(types_raw, (str, bytes)):
        raise PolicyError("POINTER_INVALID", "verifierTypes must be an object")
    if isinstance(entries_raw, (str, bytes)) or not isinstance(entries_raw, Sequence):
        raise PolicyError("POINTER_INVALID", "verifiers must be a list")

    verifier_types: dict[str, VerifierType] = {}
    for type_id, type_raw in types_raw.items():
        identifier = _nonempty_string(type_id, "verifierTypes key")
        if identifier not in _IMPLEMENTATION_TYPES:
            raise PolicyError("VERIFIER_NOT_REGISTERED", f"unknown verifier implementation: {identifier}")
        if not isinstance(type_raw, Mapping):
            raise PolicyError("POINTER_INVALID", f"verifier type {identifier} must be an object")
        _exact_keys(type_raw, {"level"}, f"verifier type {identifier}")
        level = _nonempty_string(type_raw["level"], f"verifier type {identifier}.level")
        if level != _IMPLEMENTATION_TYPES[identifier]:
            raise PolicyError("VERIFIER_NOT_REGISTERED", f"verifier type {identifier} has the wrong level")
        verifier_types[identifier] = VerifierType(identifier, level)

    if set(verifier_types) != set(_IMPLEMENTATION_TYPES):
        raise PolicyError("VERIFIER_NOT_REGISTERED", "registry must declare exactly the approved implementations")

    verifiers: dict[str, VerifierDefinition] = {}
    for entry in entries_raw:
        if not isinstance(entry, Mapping):
            raise PolicyError("POINTER_INVALID", "verifier entry must be an object")
        if not {"id", "type"} <= set(entry):
            raise PolicyError("POINTER_INVALID", "verifier entry is missing id or type")
        verifier_id = _nonempty_string(entry["id"], "verifier.id")
        if verifier_id in verifiers:
            raise PolicyError("POINTER_INVALID", f"duplicate verifier id: {verifier_id}")
        type_id = _nonempty_string(entry["type"], "verifier.type")
        verifier_type = verifier_types.get(type_id)
        if verifier_type is None:
            raise PolicyError("VERIFIER_NOT_REGISTERED", f"unregistered verifier type: {type_id}")
        if type_id == "e2.argv-suite.v1":
            _exact_keys(entry, {"id", "type", "suite"}, "E2 verifier entry")
            definition = _validate_suite(entry["suite"], root, verifier_id)
        else:
            _exact_keys(entry, {"id", "type", "git"}, "E1 verifier entry")
            definition = _validate_git_definition(entry["git"], root)
        verifiers[verifier_id] = VerifierDefinition(verifier_id, type_id, verifier_type.level, definition)

    return TrustedRegistry(MappingProxyType(verifier_types), MappingProxyType(verifiers))


def load_trusted_registry(root: Path) -> TrustedRegistry:
    """Load approved implementation types and reviewed verifier instances only."""
    acceptance_root = _acceptance_root(root)
    raw = _read_json(acceptance_root, "trusted-registry.json")
    assert raw is not None
    return _parse_registry(raw, acceptance_root)


def _matrix_requirements(root: Path) -> tuple[frozenset[str], Mapping[str, frozenset[str]]]:
    raw = _read_json(root, "matrix-contract.json")
    assert raw is not None
    matrices = raw.get("matrices")
    rules = raw.get("minimumEvidenceRules")
    if not isinstance(matrices, Mapping) or not isinstance(rules, Sequence):
        raise PolicyError("POINTER_INVALID", "matrix contract is missing matrix requirements")
    item_ids = frozenset(
        item["id"]
        for items in matrices.values()
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes))
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    )
    requirements: dict[str, frozenset[str]] = {}
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise PolicyError("POINTER_INVALID", "minimum evidence rule must be an object")
        ranges = rule.get("ranges")
        levels = rule.get("levels")
        if not isinstance(ranges, Sequence) or isinstance(ranges, (str, bytes)):
            raise PolicyError("POINTER_INVALID", "minimum evidence ranges must be a list")
        level_set = frozenset(_string_list(levels, "minimum evidence levels"))
        if not level_set <= _LEVELS:
            raise PolicyError("POINTER_INVALID", "minimum evidence rule has an unknown level")
        for value in ranges:
            range_text = _nonempty_string(value, "minimum evidence range")
            prefix, start, end = _expand_range(range_text)
            for number in range(start, end + 1):
                item_id = f"{prefix}{number:02d}"
                if item_id not in item_ids:
                    raise PolicyError("POINTER_INVALID", f"unknown matrix item in rule: {item_id}")
                requirements[item_id] = level_set
    return item_ids, MappingProxyType(requirements)


def _expand_range(value: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"([ARGCX])(\d{2})(?:-([ARGCX])?(\d{2}))?", value)
    if match is None:
        raise PolicyError("POINTER_INVALID", f"invalid matrix range: {value}")
    prefix, start_text, end_prefix, end_text = match.groups()
    if end_prefix not in (None, prefix):
        raise PolicyError("POINTER_INVALID", f"mixed matrix range: {value}")
    start = int(start_text)
    end = int(end_text or start_text)
    if end < start:
        raise PolicyError("POINTER_INVALID", f"reversed matrix range: {value}")
    return prefix, start, end


def _parse_profile(raw: Mapping[str, object], registry: TrustedRegistry, item_ids: frozenset[str], requirements: Mapping[str, frozenset[str]]) -> AcceptanceProfile:
    _exact_keys(
        raw,
        {"profileId", "version", "subjectRef", "ownedItemIds", "requiredVerifierIds", "supportingItemIds"},
        "profile",
    )
    owned = _string_set(raw["ownedItemIds"], "ownedItemIds", "PROFILE_SCOPE_MISMATCH")
    supporting = _string_set(raw["supportingItemIds"], "supportingItemIds", "PROFILE_SCOPE_MISMATCH")
    if not owned or owned - item_ids or supporting - item_ids or owned & supporting:
        raise PolicyError("PROFILE_SCOPE_MISMATCH", "profile owns an invalid or supporting matrix item")
    required = raw["requiredVerifierIds"]
    if not isinstance(required, Mapping):
        raise PolicyError("PROFILE_SCOPE_MISMATCH", "requiredVerifierIds must be an object")
    if set(required) != set(owned):
        raise PolicyError("PROFILE_SCOPE_MISMATCH", "requirements must name exactly the owned items")
    normalized: dict[str, tuple[str, ...]] = {}
    for item_id, verifier_ids_raw in required.items():
        verifier_ids = _string_list(verifier_ids_raw, f"requiredVerifierIds.{item_id}", "PROFILE_SCOPE_MISMATCH")
        definitions = []
        for verifier_id in verifier_ids:
            definition = registry.verifiers.get(verifier_id)
            if definition is None:
                raise PolicyError("VERIFIER_NOT_REGISTERED", f"unknown profile verifier: {verifier_id}")
            definitions.append(definition)
        levels = frozenset(definition.level for definition in definitions)
        if levels != requirements.get(item_id, frozenset()):
            raise PolicyError("PROFILE_SCOPE_MISMATCH", f"profile verifier levels do not match {item_id}")
        normalized[item_id] = verifier_ids
    return AcceptanceProfile(
        profile_id=_nonempty_string(raw["profileId"], "profileId", "PROFILE_SCOPE_MISMATCH"),
        version=_nonempty_string(raw["version"], "version", "PROFILE_SCOPE_MISMATCH"),
        subject_ref=_nonempty_string(raw["subjectRef"], "subjectRef", "PROFILE_SCOPE_MISMATCH"),
        owned_item_ids=owned,
        required_verifier_ids=normalized,
        supporting_item_ids=supporting,
    )


def load_profiles(root: Path) -> dict[str, AcceptanceProfile]:
    """Load profiles only from the acceptance-root ``profiles`` directory."""
    acceptance_root = _acceptance_root(root)
    profiles_dir = _inside_root(acceptance_root, acceptance_root / "profiles")
    if not profiles_dir.exists():
        return {}
    if not profiles_dir.is_dir():
        raise PolicyError("POINTER_INVALID", "profiles must be a directory")
    profile_paths = sorted(profiles_dir.glob("*.json"))
    if not profile_paths:
        return {}
    registry = load_trusted_registry(acceptance_root)
    item_ids, requirements = _matrix_requirements(acceptance_root)
    profiles: dict[str, AcceptanceProfile] = {}
    for path in profile_paths:
        _inside_root(acceptance_root, path)
        raw = _read_json(acceptance_root, str(path.relative_to(acceptance_root)))
        assert raw is not None
        profile = _parse_profile(raw, registry, item_ids, requirements)
        if profile.profile_id in profiles:
            raise PolicyError("PROFILE_SCOPE_MISMATCH", f"duplicate profile id: {profile.profile_id}")
        profiles[profile.profile_id] = profile
    return profiles


def load_reviewer_policy(root: Path) -> ReviewerPolicy:
    """Load future E4 reviewer identities; absent phase-one policy is empty."""
    acceptance_root = _acceptance_root(root)
    raw = _read_json(acceptance_root, "trust/reviewer-policy.json", required=False)
    if raw is None:
        return ReviewerPolicy(MappingProxyType({}))
    _exact_keys(raw, {"version", "reviewers"}, "reviewer policy")
    if raw["version"] != 1 or not isinstance(raw["reviewers"], Sequence) or isinstance(raw["reviewers"], (str, bytes)):
        raise PolicyError("POINTER_INVALID", "invalid reviewer policy")
    reviewers: dict[str, Reviewer] = {}
    for raw_reviewer in raw["reviewers"]:
        if not isinstance(raw_reviewer, Mapping):
            raise PolicyError("POINTER_INVALID", "reviewer must be an object")
        _exact_keys(raw_reviewer, {"principal", "fingerprint", "roles", "profileIds"}, "reviewer")
        principal = _nonempty_string(raw_reviewer["principal"], "reviewer.principal")
        if principal in reviewers:
            raise PolicyError("POINTER_INVALID", f"duplicate reviewer principal: {principal}")
        reviewers[principal] = Reviewer(
            principal=principal,
            fingerprint=_nonempty_string(raw_reviewer["fingerprint"], "reviewer.fingerprint"),
            roles=_string_set(raw_reviewer["roles"], "reviewer.roles"),
            profile_ids=_string_set(raw_reviewer["profileIds"], "reviewer.profileIds"),
        )
    return ReviewerPolicy(MappingProxyType(reviewers))


def load_revocations(root: Path) -> RevocationSet:
    """Load the only three revocable trust references accepted by the bridge."""
    acceptance_root = _acceptance_root(root)
    raw = _read_json(acceptance_root, "trust/revocations.json")
    assert raw is not None
    _exact_keys(raw, {"version", "fingerprints", "signatureDigests", "evidenceDigests"}, "revocations")
    if raw["version"] != 1:
        raise PolicyError("POINTER_INVALID", "unsupported revocations version")
    fingerprints = _string_set(raw["fingerprints"], "fingerprints")
    signature_digests = _string_set(raw["signatureDigests"], "signatureDigests")
    evidence_digests = _string_set(raw["evidenceDigests"], "evidenceDigests")
    if any(not _DIGEST.fullmatch(value) for value in signature_digests | evidence_digests):
        raise PolicyError("POINTER_INVALID", "revocation digest must be sha256:<64-hex>")
    return RevocationSet(fingerprints, signature_digests, evidence_digests)


def validate_registry_graph(
    registry: TrustedRegistry,
    profiles: Mapping[str, AcceptanceProfile] | None = None,
    reviewer_policy: ReviewerPolicy | None = None,
    revocations: RevocationSet | None = None,
) -> None:
    """Validate cross-file references without loading or executing anything."""
    for verifier in registry.verifiers.values():
        if verifier.type_id not in registry.verifier_types or verifier.level not in _LEVELS:
            raise PolicyError("VERIFIER_NOT_REGISTERED", "verifier is outside the static registry")
    for profile_id, profile in (profiles or {}).items():
        if profile_id != profile.profile_id:
            raise PolicyError("PROFILE_SCOPE_MISMATCH", "profile mapping key differs from profile id")
        for verifier_ids in profile.required_verifier_ids.values():
            if any(verifier_id not in registry.verifiers for verifier_id in verifier_ids):
                raise PolicyError("VERIFIER_NOT_REGISTERED", "profile references an unknown verifier")
    if reviewer_policy is not None and profiles is not None:
        known_profiles = set(profiles)
        for reviewer in reviewer_policy.reviewers.values():
            if not reviewer.profile_ids <= known_profiles:
                raise PolicyError("PROFILE_NOT_FOUND", "reviewer references an unknown profile")
    if revocations is not None and not isinstance(revocations, RevocationSet):
        raise PolicyError("POINTER_INVALID", "invalid revocation set")
