import json
from pathlib import Path
from dataclasses import FrozenInstanceError
from copy import deepcopy

import pytest

from trusted_evidence.registry import (
    E2SuiteDefinition,
    E1GitDefinition,
    GitObjectSource,
    PolicyError,
    load_profiles,
    load_revocations,
    load_trusted_registry,
)


ACCEPTANCE = Path(__file__).parent


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _registry(verifiers: list[dict[str, object]]) -> dict[str, object]:
    return {
        "version": 1,
        "verifierTypes": {
            "e1.git-license.v1": {"level": "E1"},
            "e2.argv-suite.v1": {"level": "E2"},
        },
        "verifiers": verifiers,
    }


def _valid_suite(**overrides: object) -> dict[str, object]:
    suite = {
        "subjectRepository": "git://product/repository",
        "workingDirectory": ".",
        "argv": ["pytest", "-q"],
        "expectedTestIds": ["tests/test_contract.py::test_policy"],
        "parser": {"kind": "pytest-json-v1"},
        "allowedEnvironment": {"CI": "1"},
        "timeoutSeconds": 60,
        "lockfileDigests": {"requirements.lock": "sha256:" + "a" * 64},
        "toolVersions": {"python": "3.13"},
    }
    suite.update(overrides)
    return suite


def _direct_e2_suite(**overrides: object) -> E2SuiteDefinition:
    values: dict[str, object] = {
        "id": "e2.safe",
        "subject_repository": "git://product/repository",
        "working_directory": ".",
        "setup_argv": (),
        "argv": ("pytest", "-q"),
        "expected_test_ids": ("tests/test_contract.py::test_policy",),
        "parser": "pytest-json-v1",
        "allowed_environment": {"CI": "1"},
        "timeout_seconds": 60,
        "lockfile_digests": {"requirements.lock": "sha256:" + "a" * 64},
        "tool_versions": {"python": "3.13"},
    }
    values.update(overrides)
    return E2SuiteDefinition(**values)  # type: ignore[arg-type]


def _valid_e1_git(**overrides: object) -> dict[str, object]:
    definition = {
        "repositoryId": "toonflow",
        "objectSource": {
            "kind": "bundle",
            "path": "trust/git/toonflow.bundle",
            "sha256": "sha256:" + "a" * 64,
        },
        "remoteUrls": ["https://github.com/toonflow/toonflow.git"],
        "commit": "b" * 40,
        "blobs": {
            "packages/canvas/source.ts": {
                "sha256": "sha256:" + "c" * 64,
                "localPath": "frontend/src/CreativeCanvas.tsx",
                "attribution": "Toonflow canvas adaptation",
            }
        },
        "licenseFact": {
            "path": "LICENSE",
            "spdx": "MIT",
            "sha256": "sha256:" + "d" * 64,
        },
        "reuse": {"kind": "structural-adaptation", "attribution": "Toonflow MIT"},
    }
    definition.update(overrides)
    return definition


def _e1_entry(git: dict[str, object]) -> dict[str, object]:
    return {"id": "e1.source", "type": "e1.git-license.v1", "git": git}


def test_phase_one_registry_has_e1_e2_implementations_but_no_production_e3_or_e4_entry():
    registry = load_trusted_registry(ACCEPTANCE)

    assert set(registry.verifier_types) == {"e1.git-license.v1", "e2.argv-suite.v1"}
    assert registry.verifiers == {}
    assert registry.verifiers_for_level("E3") == ()
    assert registry.verifiers_for_level("E4") == ()


def test_e1_loader_builds_an_immutable_typed_git_policy(tmp_path):
    _write_json(tmp_path / "trusted-registry.json", _registry([_e1_entry(_valid_e1_git())]))

    definition = load_trusted_registry(tmp_path).verifiers["e1.source"].definition

    assert isinstance(definition, E1GitDefinition)
    assert isinstance(definition.object_source, GitObjectSource)
    assert definition.object_source.kind == "bundle"
    assert definition.object_source.path == (tmp_path / "trust/git/toonflow.bundle").resolve()
    assert definition.object_source.digest == "sha256:" + "a" * 64
    assert definition.blobs["packages/canvas/source.ts"].local_path == "frontend/src/CreativeCanvas.tsx"
    with pytest.raises(TypeError):
        definition.blobs["new"] = definition.blobs["packages/canvas/source.ts"]
    with pytest.raises(FrozenInstanceError):
        definition.object_source.path = tmp_path  # type: ignore[misc]

    repository_policy = _valid_e1_git(objectSource={"kind": "repository", "path": "trust/git/product"})
    _write_json(tmp_path / "trusted-registry.json", _registry([_e1_entry(repository_policy)]))
    repository_definition = load_trusted_registry(tmp_path).verifiers["e1.source"].definition
    assert isinstance(repository_definition, E1GitDefinition)
    assert repository_definition.object_source.kind == "repository"
    assert repository_definition.object_source.digest is None


@pytest.mark.parametrize(
    "mutate",
    (
        lambda policy: policy["objectSource"].update(kind="unknown"),
        lambda policy: policy["objectSource"].update(path="../../outside"),
        lambda policy: policy["objectSource"].pop("sha256"),
        lambda policy: policy["objectSource"].update(kind="repository", sha256="sha256:" + "a" * 64),
        lambda policy: policy.update(commit="a" * 39),
        lambda policy: policy.update(remoteUrls=[]),
        lambda policy: policy.update(remoteUrls=["https://github.com/toonflow/toonflow.git"] * 2),
        lambda policy: policy.update(remoteUrls=["not-a-url"]),
        lambda policy: policy.update(blobs={}),
        lambda policy: policy.update(blobs={"../escape": {"sha256": "sha256:" + "c" * 64}}),
        lambda policy: policy["blobs"]["packages/canvas/source.ts"].update(sha256="not-a-digest"),
        lambda policy: policy["licenseFact"].update(spdx=""),
        lambda policy: policy["reuse"].update(kind="unreviewed"),
        lambda policy: policy.update(command=["git", "status"]),
    ),
)
def test_e1_loader_rejects_unclosed_or_invalid_git_policy(tmp_path, mutate):
    policy = deepcopy(_valid_e1_git())
    mutate(policy)
    _write_json(tmp_path / "trusted-registry.json", _registry([_e1_entry(policy)]))

    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        load_trusted_registry(tmp_path)


@pytest.mark.parametrize(
    "entry",
    (
        {"id": "e2.bad", "type": "e2.argv-suite.v1", "shell": "pytest -q"},
        {"id": "e2.bad", "type": "e2.unknown.v1"},
        {
            "id": "e2.bad",
            "type": "e2.argv-suite.v1",
            "suite": {"argv": "pytest -q"},
        },
        {
            "id": "e2.bad",
            "type": "e2.argv-suite.v1",
            "suite": {"argv": ["pytest", "-q"], "cwd": "../outside"},
        },
        {"id": "e2.bad", "type": "e2.argv-suite.v1", "suite": {}},
        {"type": "e2.argv-suite.v1", "suite": {"argv": ["pytest"]}},
        {"id": "e2.bad", "type": "e2.argv-suite.v1", "suite": {"argv": ["pytest"], "metadata": {}}},
    ),
)
def test_registry_rejects_shell_strings_unknown_verifier_types_and_nonliteral_argv(tmp_path, entry):
    _write_json(tmp_path / "trusted-registry.json", _registry([entry]))

    with pytest.raises(PolicyError, match="VERIFIER_NOT_REGISTERED|POINTER_INVALID"):
        load_trusted_registry(tmp_path)


def test_registry_rejects_suite_cwd_that_escapes_through_a_symlink(tmp_path):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "inside").symlink_to(outside, target_is_directory=True)
    _write_json(
        tmp_path / "trusted-registry.json",
        _registry(
            [
                {
                    "id": "e2.bad",
                    "type": "e2.argv-suite.v1",
                    "suite": _valid_suite(workingDirectory="inside"),
                }
            ]
        ),
    )

    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        load_trusted_registry(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    (
        {"workingDirectory": "../../outside"},
        {"workingDirectory": "/outside"},
        {"workingDirectory": 5},
        {"timeoutSeconds": "forever"},
        {"timeoutSeconds": 0},
        {"timeoutSeconds": -1},
        {"timeoutSeconds": True},
        {"timeoutSeconds": float("inf")},
        {"expectedTestIds": {"not": "a list"}},
        {"expectedTestIds": []},
        {"expectedTestIds": ["test", 1]},
        {"allowedEnvironment": ["SECRET=value"]},
        {"allowedEnvironment": {"SECRET_TOKEN": "value"}},
        {"allowedEnvironment": {"CI": "inherit"}},
        {"parser": {"kind": "untrusted-parser"}},
        {"parser": {"kind": "pytest-json-v1", "command": "injected"}},
        {"parser": ["pytest-json-v1"]},
        {"lockfileDigests": ["requirements.lock"]},
        {"lockfileDigests": {"../outside.lock": "sha256:" + "a" * 64}},
        {"lockfileDigests": {"requirements.lock": "not-a-digest"}},
        {"toolVersions": ["python=3.13"]},
        {"toolVersions": {"python": ""}},
    ),
)
def test_registry_rejects_invalid_allowed_e2_suite_field(tmp_path, overrides):
    _write_json(
        tmp_path / "trusted-registry.json",
        _registry([{"id": "e2.bad", "type": "e2.argv-suite.v1", "suite": _valid_suite(**overrides)}]),
    )

    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        load_trusted_registry(tmp_path)


def test_verifier_type_schema_rejects_e2_definition_and_e1_generic_definition(tmp_path):
    for entry in (
        {"id": "e2.bad", "type": "e2.argv-suite.v1", "definition": {"shell": "pytest"}},
        {"id": "e1.bad", "type": "e1.git-license.v1", "definition": {}},
    ):
        _write_json(tmp_path / "trusted-registry.json", _registry([entry]))

        with pytest.raises(PolicyError, match="POINTER_INVALID"):
            load_trusted_registry(tmp_path)


def test_e2_loader_builds_a_typed_deeply_immutable_suite_contract(tmp_path):
    _write_json(
        tmp_path / "trusted-registry.json",
        _registry(
            [
                {
                    "id": "e2.safe",
                    "type": "e2.argv-suite.v1",
                    "suite": _valid_suite(
                        setupArgv=["uv", "sync"],
                        parser={"kind": "pytest-json-v1"},
                    ),
                }
            ]
        ),
    )

    definition = load_trusted_registry(tmp_path).verifiers["e2.safe"].definition
    assert isinstance(definition, E2SuiteDefinition)
    assert definition.id == "e2.safe"
    assert definition.subject_repository == "git://product/repository"
    assert definition.working_directory == "."
    assert definition.argv == ("pytest", "-q")
    assert definition.setup_argv == ("uv", "sync")
    assert definition.expected_test_ids == ("tests/test_contract.py::test_policy",)
    assert definition.parser == "pytest-json-v1"
    assert definition.allowed_environment == {"CI": "1"}
    assert definition.timeout_seconds == 60
    assert definition.lockfile_digests == {"requirements.lock": "sha256:" + "a" * 64}
    assert definition.tool_versions == {"python": "3.13"}
    with pytest.raises((AttributeError, TypeError)):
        definition.argv.append("--unsafe")
    with pytest.raises(TypeError):
        definition.allowed_environment["CI"] = "rewritten"
    with pytest.raises(TypeError):
        definition.lockfile_digests["requirements.lock"] = "sha256:" + "b" * 64
    with pytest.raises(TypeError):
        definition.tool_versions["python"] = "rewritten"
    with pytest.raises(FrozenInstanceError):
        definition.timeout_seconds = 1  # type: ignore[misc]


def test_e2_loader_uses_an_empty_immutable_setup_argv_when_json_omits_it(tmp_path):
    _write_json(
        tmp_path / "trusted-registry.json",
        _registry(
            [
                {
                    "id": "e2.safe",
                    "type": "e2.argv-suite.v1",
                    "suite": _valid_suite(),
                }
            ]
        ),
    )

    definition = load_trusted_registry(tmp_path).verifiers["e2.safe"].definition

    assert isinstance(definition, E2SuiteDefinition)
    assert definition.setup_argv == ()


def test_e2_direct_construction_copies_and_deeply_freezes_source_containers():
    setup_argv = ["uv", "sync"]
    argv = ["pytest", "-q"]
    expected_test_ids = ["tests/test_contract.py::test_policy"]
    allowed_environment = {"CI": "1"}
    lockfile_digests = {"requirements.lock": "sha256:" + "a" * 64}
    tool_versions = {"python": "3.13"}

    definition = _direct_e2_suite(
        setup_argv=setup_argv,
        argv=argv,
        expected_test_ids=expected_test_ids,
        allowed_environment=allowed_environment,
        lockfile_digests=lockfile_digests,
        tool_versions=tool_versions,
    )
    setup_argv.append("--unsafe")
    argv.append("--unsafe")
    expected_test_ids.append("tests/test_contract.py::test_untrusted")
    allowed_environment["SECRET_TOKEN"] = "leaked"
    lockfile_digests["requirements.lock"] = "sha256:" + "b" * 64
    tool_versions["python"] = "rewritten"

    assert definition.setup_argv == ("uv", "sync")
    assert definition.argv == ("pytest", "-q")
    assert definition.expected_test_ids == ("tests/test_contract.py::test_policy",)
    assert definition.allowed_environment == {"CI": "1"}
    assert definition.lockfile_digests == {"requirements.lock": "sha256:" + "a" * 64}
    assert definition.tool_versions == {"python": "3.13"}
    with pytest.raises(TypeError):
        definition.allowed_environment["CI"] = "rewritten"
    with pytest.raises(TypeError):
        definition.lockfile_digests["requirements.lock"] = "sha256:" + "b" * 64
    with pytest.raises(TypeError):
        definition.tool_versions["python"] = "rewritten"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("id", ""),
        ("subject_repository", "git://untrusted/repository"),
        ("working_directory", "../../outside"),
        ("argv", ()),
        ("expected_test_ids", ("duplicate", "duplicate")),
        ("parser", "untrusted-parser"),
        ("allowed_environment", {"SECRET_TOKEN": "value"}),
        ("timeout_seconds", True),
        ("lockfile_digests", {"../outside.lock": "sha256:" + "a" * 64}),
        ("lockfile_digests", {"requirements.lock": "not-a-digest"}),
        ("tool_versions", {"python": ""}),
    ),
)
def test_e2_direct_construction_rejects_invalid_policy(field, value):
    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        _direct_e2_suite(**{field: value})


def test_empty_profiles_directory_returns_empty_without_other_policy_files(tmp_path):
    (tmp_path / "profiles").mkdir()

    assert load_profiles(tmp_path) == {}


def test_profile_rejects_unknown_items_verifiers_levels_and_supporting_range_ownership(tmp_path):
    _write_json(
        tmp_path / "trusted-registry.json",
        _registry([_e1_entry(_valid_e1_git())]),
    )
    _write_json(
        tmp_path / "matrix-contract.json",
        {
            "version": 1,
            "minimumEvidenceRules": [{"ranges": ["C01"], "levels": ["E1"]}],
            "matrices": {"C": [{"id": "C01"}]},
        },
    )
    profile = {
        "profileId": "sample/v1",
        "version": "v1",
        "subjectRef": "git://product@" + "a" * 40,
        "ownedItemIds": ["C01"],
        "requiredVerifierIds": {"C01": ["e1.source"]},
        "supportingItemIds": ["C01"],
    }
    _write_json(tmp_path / "profiles" / "sample.json", profile)

    with pytest.raises(PolicyError, match="PROFILE_SCOPE_MISMATCH"):
        load_profiles(tmp_path)

    profile["supportingItemIds"] = []
    profile["requiredVerifierIds"] = {"C01": ["missing"]}
    _write_json(tmp_path / "profiles" / "sample.json", profile)
    with pytest.raises(PolicyError, match="VERIFIER_NOT_REGISTERED"):
        load_profiles(tmp_path)

    profile["requiredVerifierIds"] = {"C99": ["e1.source"]}
    _write_json(tmp_path / "profiles" / "sample.json", profile)
    with pytest.raises(PolicyError, match="PROFILE_SCOPE_MISMATCH"):
        load_profiles(tmp_path)


def test_revocations_accept_only_fingerprints_signature_digests_and_evidence_digests(tmp_path):
    _write_json(
        tmp_path / "trust" / "revocations.json",
        {
            "version": 1,
            "fingerprints": ["SHA256:trusted-key"],
            "signatureDigests": ["sha256:" + "a" * 64],
            "evidenceDigests": ["sha256:" + "b" * 64],
        },
    )

    revocations = load_revocations(tmp_path)
    assert revocations.fingerprints == frozenset({"SHA256:trusted-key"})
    assert revocations.signature_digests == frozenset({"sha256:" + "a" * 64})
    assert revocations.evidence_digests == frozenset({"sha256:" + "b" * 64})

    _write_json(
        tmp_path / "trust" / "revocations.json",
        {"version": 1, "fingerprints": [], "evidenceDigests": ["not-a-digest"]},
    )
    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        load_revocations(tmp_path)
