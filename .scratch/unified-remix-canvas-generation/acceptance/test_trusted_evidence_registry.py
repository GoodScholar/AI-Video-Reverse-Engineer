import json
from pathlib import Path

import pytest

from trusted_evidence.registry import (
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


def test_phase_one_registry_has_e1_e2_implementations_but_no_production_e3_or_e4_entry():
    registry = load_trusted_registry(ACCEPTANCE)

    assert set(registry.verifier_types) == {"e1.git-license.v1", "e2.argv-suite.v1"}
    assert registry.verifiers == {}
    assert registry.verifiers_for_level("E3") == ()
    assert registry.verifiers_for_level("E4") == ()


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


def test_loaded_suite_policy_is_deeply_immutable(tmp_path):
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
    assert definition["argv"] == ("pytest", "-q")
    assert definition["setupArgv"] == ("uv", "sync")
    assert definition["parser"]["kind"] == "pytest-json-v1"
    with pytest.raises((AttributeError, TypeError)):
        definition["argv"].append("--unsafe")
    with pytest.raises(TypeError):
        definition["parser"]["kind"] = "rewritten"


def test_empty_profiles_directory_returns_empty_without_other_policy_files(tmp_path):
    (tmp_path / "profiles").mkdir()

    assert load_profiles(tmp_path) == {}


def test_profile_rejects_unknown_items_verifiers_levels_and_supporting_range_ownership(tmp_path):
    _write_json(
        tmp_path / "trusted-registry.json",
        _registry([{"id": "e1.source", "type": "e1.git-license.v1", "git": {}}]),
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
