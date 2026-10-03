import hashlib

import pytest

from trusted_evidence.e2_policy import (
    AcceptanceOperationSnapshotV1,
    GitObjectIdV1,
    NamedDigest,
    ProductGitObjectIdV1,
    ReplayPolicyBindingV1,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _binding(**overrides):
    values = {
        "registry_digest": DIGEST_A,
        "suite_digests": (NamedDigest("suite/main", DIGEST_B),),
        "profile_digests": (NamedDigest("profile/main", DIGEST_C),),
        "revocation_digest": DIGEST_A,
        "reviewer_policy_digest": DIGEST_B,
        "toolchain_digests": (NamedDigest("toolchain/linux", DIGEST_C),),
        "dependency_digests": (),
        "product_source_digests": (NamedDigest("product/main", DIGEST_A),),
        "containment_policy_digests": (NamedDigest("containment/linux", DIGEST_B),),
    }
    values.update(overrides)
    return ReplayPolicyBindingV1(**values)


def test_replay_binding_excludes_catalog_contract_and_observation_time():
    """Adding operation-only state must not change a replay-policy digest."""
    binding = _binding()

    assert binding.canonical_digest == "sha256:8649890b1c44801a18deb6b2a66fac64055585eb681a64b524da1e65e2651790"
    assert "catalog" not in binding.canonical_bytes.decode()
    assert "contract" not in binding.canonical_bytes.decode()
    assert "observed" not in binding.canonical_bytes.decode()


def test_operation_snapshot_binds_catalog_contract_and_replay_binding():
    """Changing any operation barrier input invalidates its snapshot digest."""
    binding = _binding()
    snapshot = AcceptanceOperationSnapshotV1(DIGEST_A, DIGEST_B, binding.canonical_digest)

    assert snapshot.canonical_digest == "sha256:b12fa2b49e1df947c6038e11c014564297afd51dad0adae44d66c776bdedf6a7"
    assert snapshot != AcceptanceOperationSnapshotV1(DIGEST_C, DIGEST_B, binding.canonical_digest)
    assert snapshot != AcceptanceOperationSnapshotV1(DIGEST_A, DIGEST_C, binding.canonical_digest)


def test_named_digest_order_is_identical_for_different_creation_order():
    """Filesystem/construction order must not enter a binding digest."""
    first = _binding(
        suite_digests=(NamedDigest("suite/z", DIGEST_A), NamedDigest("suite/a", DIGEST_B)),
    )
    second = _binding(
        suite_digests=(NamedDigest("suite/a", DIGEST_B), NamedDigest("suite/z", DIGEST_A)),
    )

    assert first.suite_digests == second.suite_digests
    assert first.canonical_digest == second.canonical_digest


def test_duplicate_name_and_illegal_empty_set_reject():
    """A duplicate logical name or missing required binding set weakens closure."""
    with pytest.raises(ValueError):
        _binding(suite_digests=(NamedDigest("suite/main", DIGEST_A), NamedDigest("suite/main", DIGEST_B)))
    with pytest.raises(ValueError):
        _binding(product_source_digests=())


def test_v1_is_not_upgraded():
    """A v1-shaped policy cannot silently acquire v2 defaults."""
    with pytest.raises(ValueError):
        ReplayPolicyBindingV1.from_dict({"version": 1})


@pytest.mark.parametrize(
    ("algorithm", "hex_value"),
    (("sha1", "a" * 40), ("sha256", "b" * 64)),
)
def test_policy_git_object_ids_allow_only_matching_algorithm_lengths(algorithm, hex_value):
    assert GitObjectIdV1(algorithm, hex_value).hex == hex_value


@pytest.mark.parametrize(
    ("algorithm", "hex_value"),
    (("sha512", "a" * 64), ("sha1", "a" * 64), ("sha256", "a" * 40)),
)
def test_policy_git_object_ids_reject_unknown_or_mismatched_algorithms(algorithm, hex_value):
    with pytest.raises(ValueError):
        GitObjectIdV1(algorithm, hex_value)


@pytest.mark.parametrize("value", ("b" * 64, "a" * 39, "a" * 41))
def test_product_git_object_id_is_sha1_only(value):
    with pytest.raises(ValueError):
        ProductGitObjectIdV1("sha1", value)
