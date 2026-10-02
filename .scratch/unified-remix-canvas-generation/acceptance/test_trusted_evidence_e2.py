import hashlib
import os
from pathlib import Path
import re
import subprocess

import pytest

from trusted_evidence.models import AcceptanceProfile, EvidencePointer, VerificationContext
from trusted_evidence.registry import E2SuiteDefinition
from trusted_evidence.storage import ContentAddressedStore
from trusted_evidence.verifiers.e2_suite import E2SuiteVerifier, SuiteRunner


OBSERVED_AT = "2026-10-02T08:30:00Z"


def _run(argv: list[str], cwd: Path) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _product_commit(tmp_path: Path, *, runner_body: str = 'printf "selected:%s\\n" "$*"') -> tuple[Path, str]:
    product = tmp_path / "product"
    product.mkdir(parents=True)
    _run(["git", "init"], product)
    _run(["git", "config", "user.email", "test@example.invalid"], product)
    _run(["git", "config", "user.name", "Trusted Evidence Test"], product)
    (product / "requirements.lock").write_text("locked dependency\n", encoding="utf-8")
    runner = product / "runner"
    runner.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'runner 1.0'; exit 0; fi\n"
        f"{runner_body}\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    _run(["git", "add", "."], product)
    _run(["git", "commit", "-m", "fixture"], product)
    return product, _run(["git", "rev-parse", "HEAD"], product)


def _suite(acceptance_root: Path, **overrides: object) -> E2SuiteDefinition:
    lock_digest = "sha256:" + hashlib.sha256(b"locked dependency\n").hexdigest()
    policy: dict[str, object] = {
        "subjectRepository": "git://product/repository",
        "workingDirectory": ".",
        "argv": ["./runner", "tests/test_contract.py::test_policy"],
        "expectedTestIds": ["tests/test_contract.py::test_policy"],
        "parser": {"kind": "pytest-json-v1"},
        "allowedEnvironment": {"CI": "1"},
        "timeoutSeconds": 5,
        "lockfileDigests": {"requirements.lock": lock_digest},
        "toolVersions": {"runner": "runner 1.0"},
    }
    policy.update(overrides)
    return E2SuiteDefinition.from_policy(root=acceptance_root, verifier_id="e2.fixture", raw=policy)


def _context(acceptance_root: Path, commit: str) -> VerificationContext:
    return VerificationContext(f"git://product@{commit}", OBSERVED_AT, str(acceptance_root))


def _profile(commit: str) -> AcceptanceProfile:
    return AcceptanceProfile(
        profile_id="fixture/v1",
        version="v1",
        subject_ref=f"git://product@{commit}",
        owned_item_ids=frozenset({"C01"}),
        required_verifier_ids={"C01": ("e2.fixture",)},
    )


def _pointer(commit: str, receipt_ref: str) -> EvidencePointer:
    return EvidencePointer.from_dict(
        {
            "id": "fixture-e2-receipt",
            "profileId": "fixture/v1",
            "verifierId": "e2.fixture",
            "subjectRef": f"git://product@{commit}",
            "artifactRef": receipt_ref,
            "registeredAt": OBSERVED_AT,
        }
    )


def test_e2_runs_only_registry_argv_in_detached_clean_checkout_with_minimal_environment(tmp_path, monkeypatch):
    product, commit = _product_commit(
        tmp_path,
        runner_body='printf "selected:%s CI:%s HOME:%s SECRET:%s\\n" "$*" "$CI" "$HOME" "$SECRET_TOKEN"',
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    suite = _suite(acceptance)
    monkeypatch.chdir(product)

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True
    assert result.error_code is None
    assert result.facts["selectedTestIds"] == ("tests/test_contract.py::test_policy",)
    assert "CI:1 HOME: SECRET:" in result.facts["stdout"]
    assert result.facts["receiptRef"].startswith("sha256:")


def test_e2_dirty_current_workspace_cannot_change_fixed_commit_result(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    (product / "runner").write_text("#!/bin/sh\necho compromised\n", encoding="utf-8")
    (product / "runner").chmod(0o755)
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True
    assert "selected:tests/test_contract.py::test_policy" in result.facts["stdout"]
    assert "compromised" not in result.facts["stdout"]


def test_e2_rejects_catalog_command_argument_cwd_and_environment_injection(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    runner = SuiteRunner()
    suite = _suite(acceptance)

    with pytest.raises(TypeError):
        runner.run(suite, f"git://product@{commit}", _context(acceptance, commit), argv=("./runner", "--injected"))
    with pytest.raises(ValueError, match="unknown fields"):
        EvidencePointer.from_dict(
            {
                "id": "injected",
                "profileId": "fixture/v1",
                "verifierId": "e2.fixture",
                "subjectRef": f"git://product@{commit}",
                "artifactRef": "sha256:" + "a" * 64,
                "registeredAt": OBSERVED_AT,
                "argv": ["./runner", "--injected"],
                "cwd": "/tmp",
                "environment": {"SECRET_TOKEN": "injected"},
            }
        )
    assert "SECRET_TOKEN" not in SuiteRunner()._environment(suite)


def test_e2_distinguishes_assertion_failure_missing_selection_timeout_and_missing_tool(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path, runner_body="exit 7")
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    runner = SuiteRunner()

    assert runner.run(_suite(acceptance), context.subject_ref, context).error_code == "TEST_REPLAY_FAILED"
    assert runner.run(
        _suite(acceptance, expectedTestIds=["tests/test_contract.py::test_missing"]), context.subject_ref, context
    ).error_code == "TEST_SELECTION_MISSING"
    assert runner.run(
        _suite(acceptance, argv=["./runner"], expectedTestIds=["tests/test_contract.py::test_policy"]), context.subject_ref, context
    ).error_code == "TEST_SELECTION_MISSING"
    assert runner.run(
        _suite(acceptance, argv=["./missing-tool", "tests/test_contract.py::test_policy"], toolVersions={"missing-tool": "1"}),
        context.subject_ref,
        context,
    ).error_code == "SOURCE_REF_UNREACHABLE"
    slow_product, slow_commit = _product_commit(tmp_path / "slow", runner_body="sleep 2")
    monkeypatch.chdir(slow_product)
    slow_context = _context(acceptance, slow_commit)
    assert runner.run(
        _suite(acceptance, timeoutSeconds=1), slow_context.subject_ref, slow_context
    ).error_code == "TEST_REPLAY_TIMEOUT"


def test_e2_blocks_when_lockfile_digest_or_tool_version_changes(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)

    bad_lock = _suite(acceptance, lockfileDigests={"requirements.lock": "sha256:" + "0" * 64})
    bad_tool = _suite(acceptance, toolVersions={"runner": "runner 9.9"})

    assert SuiteRunner().run(bad_lock, context.subject_ref, context).error_code == "SOURCE_REF_UNREACHABLE"
    assert SuiteRunner().run(bad_tool, context.subject_ref, context).error_code == "SOURCE_REF_UNREACHABLE"


def test_e2_redacts_secrets_before_storing_receipt_and_detects_receipt_tampering(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path, runner_body="echo API_KEY=top-secret")
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    runner = SuiteRunner(redaction_patterns=(re.compile(r"top-secret"),))

    result = runner.run(_suite(acceptance), context.subject_ref, context)
    receipt_ref = result.facts["receiptRef"]
    receipt = ContentAddressedStore(acceptance).read(receipt_ref)

    assert b"top-secret" not in receipt
    assert b"[REDACTED]" in receipt
    digest = receipt_ref.removeprefix("sha256:")
    (acceptance / "evidence" / "sha256" / digest[:2] / digest).write_bytes(b"tampered")
    verification = E2SuiteVerifier("e2.fixture", _suite(acceptance), runner=runner).verify(
        _pointer(commit, receipt_ref), _profile(commit), context
    )
    assert verification.valid is False
    assert verification.error_code == "DIGEST_MISMATCH"
