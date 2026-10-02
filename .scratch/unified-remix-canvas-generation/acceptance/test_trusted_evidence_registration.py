import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from trusted_evidence.models import EvidencePointer
from trusted_evidence.registry import PolicyError
from trusted_evidence.registration import EvidenceRegistrar
from trusted_evidence.storage import CatalogRepository, ContentAddressedStore, canonical_json_bytes


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _policy_root(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "trusted-registry.json",
        {
            "version": 1,
            "verifierTypes": {
                "e1.git-license.v1": {"level": "E1"},
                "e2.argv-suite.v1": {"level": "E2"},
            },
            "verifiers": [
                {
                    "id": "e2.sample",
                    "type": "e2.argv-suite.v1",
                    "suite": {
                        "subjectRepository": "git://product/repository",
                        "workingDirectory": ".",
                        "argv": ["pytest", "-q"],
                        "expectedTestIds": ["tests/test_contract.py::test_policy"],
                        "parser": {"kind": "pytest-json-v1"},
                        "allowedEnvironment": {"CI": "1"},
                        "timeoutSeconds": 60,
                        "lockfileDigests": {"requirements.lock": "sha256:" + "a" * 64},
                        "toolVersions": {"python": "3.13"},
                    },
                }
            ],
        },
    )
    _write_json(
        tmp_path / "matrix-contract.json",
        {
            "version": 1,
            "minimumEvidenceRules": [{"ranges": ["C01"], "levels": ["E2"]}],
            "matrices": {"C": [{"id": "C01"}]},
        },
    )
    _write_json(
        tmp_path / "profiles" / "sample.json",
        {
            "profileId": "sample/v1",
            "version": "v1",
            "subjectRef": "git://product@" + "a" * 40,
            "ownedItemIds": ["C01"],
            "requiredVerifierIds": {"C01": ["e2.sample"]},
            "supportingItemIds": [],
        },
    )
    _write_json(tmp_path / "evidence-catalog.json", {"version": 1, "evidence": []})
    return tmp_path


def _pointer(artifact_ref: str, *, pointer_id: str = "sample-e2-a1b2c3d4", verifier_id: str = "e2.sample") -> EvidencePointer:
    return EvidencePointer.from_dict(
        {
            "id": pointer_id,
            "profileId": "sample/v1",
            "verifierId": verifier_id,
            "subjectRef": "git://product@" + "a" * 40,
            "artifactRef": artifact_ref,
            "registeredAt": "2026-10-02T08:30:00Z",
        }
    )


def _append_in_process(root: str, raw_pointer: dict[str, object]) -> str:
    return CatalogRepository(Path(root)).append(EvidencePointer.from_dict(raw_pointer)).snapshot.content_digest


def _register_in_process(root: str, raw_pointer: dict[str, object]) -> bool:
    return EvidenceRegistrar(Path(root)).register(EvidencePointer.from_dict(raw_pointer)).idempotent


def test_content_store_derives_path_from_sha256_and_never_overwrites_different_bytes(tmp_path):
    store = ContentAddressedStore(tmp_path)
    payload = b'{"receipt":"original"}'
    digest = hashlib.sha256(payload).hexdigest()

    assert canonical_json_bytes({"b": 1, "a": [True, None]}) == b'{"a":[true,null],"b":1}'
    assert store.put(payload) == f"sha256:{digest}"
    assert store.read(f"sha256:{digest}") == payload

    object_path = tmp_path / "evidence" / "sha256" / digest[:2] / digest
    object_path.write_bytes(b"different bytes")
    with pytest.raises(PolicyError, match="DIGEST_MISMATCH"):
        store.put(payload)
    assert object_path.read_bytes() == b"different bytes"


def test_register_is_idempotent_only_for_byte_identical_pointer(tmp_path):
    root = _policy_root(tmp_path)
    store = ContentAddressedStore(root)
    pointer = _pointer(store.put(b"receipt"))
    registrar = EvidenceRegistrar(root)

    first = registrar.register(pointer)
    replay = registrar.register(pointer)

    assert first.idempotent is False
    assert replay.idempotent is True
    assert replay.snapshot.version == 2
    assert replay.snapshot.pointers == (pointer,)
    conflicting = EvidencePointer.from_dict(
        {
            "id": pointer.id,
            "profileId": "sample/v1",
            "verifierId": "e2.sample",
            "subjectRef": pointer.subject_ref,
            "artifactRef": pointer.artifact_ref,
            "registeredAt": "2026-10-02T08:31:00Z",
        }
    )
    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        registrar.register(conflicting)


def test_register_rejects_missing_or_mismatched_artifact_digest(tmp_path):
    root = _policy_root(tmp_path)
    registrar = EvidenceRegistrar(root)
    missing = _pointer("sha256:" + "b" * 64)

    with pytest.raises(PolicyError, match="ARTIFACT_MISSING"):
        registrar.register(missing)

    store = ContentAddressedStore(root)
    ref = store.put(b"receipt")
    digest = ref.removeprefix("sha256:")
    (root / "evidence" / "sha256" / digest[:2] / digest).write_bytes(b"tampered")
    with pytest.raises(PolicyError, match="DIGEST_MISMATCH"):
        registrar.register(_pointer(ref))


def test_register_rejects_unknown_profile_verifier_pair_and_self_reported_fields(tmp_path):
    root = _policy_root(tmp_path)
    pointer = _pointer(ContentAddressedStore(root).put(b"receipt"), verifier_id="e2.unknown")

    with pytest.raises(PolicyError, match="VERIFIER_NOT_REGISTERED"):
        EvidenceRegistrar(root).register(pointer)
    raw = {
        "id": "injected",
        "profileId": "sample/v1",
        "verifierId": "e2.sample",
        "subjectRef": "git://product@" + "a" * 40,
        "artifactRef": pointer.artifact_ref,
        "registeredAt": "2026-10-02T08:30:00Z",
        "status": "通过",
    }
    with pytest.raises(ValueError, match="unknown fields"):
        EvidencePointer.from_dict(raw)


def test_concurrent_catalog_appends_preserve_both_entries(tmp_path):
    root = _policy_root(tmp_path)
    repository = CatalogRepository(root)
    repository.migrate_empty_v1_for_write()
    pointers = (
        _pointer("sha256:" + "c" * 64, pointer_id="sample-e2-c"),
        _pointer("sha256:" + "d" * 64, pointer_id="sample-e2-d"),
    )

    raw_pointers = [
        {
            "id": pointer.id,
            "profileId": pointer.profile_id,
            "verifierId": pointer.verifier_id,
            "subjectRef": pointer.subject_ref,
            "artifactRef": pointer.artifact_ref,
            "registeredAt": pointer.registered_at,
        }
        for pointer in pointers
    ]
    with ProcessPoolExecutor(max_workers=2) as executor:
        digests = list(executor.map(_append_in_process, (str(root), str(root)), raw_pointers))

    assert all(digest.startswith("sha256:") for digest in digests)
    assert {pointer.id for pointer in repository.read().pointers} == {"sample-e2-c", "sample-e2-d"}


def test_register_leaves_matrix_contract_byte_identical(tmp_path):
    root = _policy_root(tmp_path)
    matrix_before = (root / "matrix-contract.json").read_bytes()
    pointer = _pointer(ContentAddressedStore(root).put(b"receipt"))

    EvidenceRegistrar(root).register(pointer)

    assert (root / "matrix-contract.json").read_bytes() == matrix_before


def test_catalog_rejects_non_integer_version_without_implicit_migration(tmp_path):
    root = _policy_root(tmp_path)
    catalog = root / "evidence-catalog.json"
    catalog.write_text('{"version":true,"evidence":[]}', encoding="utf-8")

    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        CatalogRepository(root).read()
    assert catalog.read_text(encoding="utf-8") == '{"version":true,"evidence":[]}'


@pytest.mark.parametrize(
    "overrides",
    (
        {"id": ""},
        {"subject_ref": "git://product@short"},
        {"artifact_ref": "sha256:not-a-digest"},
        {"registered_at": "not-rfc3339"},
    ),
    ids=("id", "subject", "artifact", "registered_at"),
)
def test_constructed_invalid_pointer_is_rejected_before_catalog_write(tmp_path, overrides):
    root = _policy_root(tmp_path)
    artifact_ref = ContentAddressedStore(root).put(b"receipt")
    values = {
        "id": "sample-e2-invalid",
        "profile_id": "sample/v1",
        "verifier_id": "e2.sample",
        "subject_ref": "git://product@" + "a" * 40,
        "artifact_ref": artifact_ref,
        "registered_at": "2026-10-02T08:30:00Z",
    }
    values.update(overrides)
    pointer = EvidencePointer(**values)
    catalog = root / "evidence-catalog.json"
    before = catalog.read_bytes()

    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        EvidenceRegistrar(root).register(pointer)

    assert catalog.read_bytes() == before


def test_catalog_append_validates_constructed_pointer_before_atomic_replace(tmp_path):
    root = _policy_root(tmp_path)
    repository = CatalogRepository(root)
    repository.migrate_empty_v1_for_write()
    before = (root / "evidence-catalog.json").read_bytes()
    pointer = EvidencePointer(
        id="",
        profile_id="sample/v1",
        verifier_id="e2.sample",
        subject_ref="git://product@" + "a" * 40,
        artifact_ref="sha256:" + "b" * 64,
        registered_at="2026-10-02T08:30:00Z",
    )

    with pytest.raises(PolicyError, match="POINTER_INVALID"):
        repository.append(pointer)

    assert (root / "evidence-catalog.json").read_bytes() == before


def test_concurrent_identical_registrations_have_one_first_write_and_idempotent_replays(tmp_path):
    root = _policy_root(tmp_path)
    pointer = _pointer(ContentAddressedStore(root).put(b"receipt"))
    raw_pointer = {
        "id": pointer.id,
        "profileId": pointer.profile_id,
        "verifierId": pointer.verifier_id,
        "subjectRef": pointer.subject_ref,
        "artifactRef": pointer.artifact_ref,
        "registeredAt": pointer.registered_at,
    }

    with ProcessPoolExecutor(max_workers=4) as executor:
        idempotent = list(executor.map(_register_in_process, (str(root),) * 4, (raw_pointer,) * 4))

    assert idempotent.count(False) == 1
    assert idempotent.count(True) == 3
    assert CatalogRepository(root).read().pointers == (pointer,)
