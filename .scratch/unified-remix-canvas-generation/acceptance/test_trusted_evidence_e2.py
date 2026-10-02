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


def _product_commit(
    tmp_path: Path,
    *,
    runner_body: str = 'for value; do test_id="$value"; done; printf \'{"tests":[{"nodeid":"%s","outcome":"passed"}]}\\n\' "$test_id" > .report.json',
    runner_path: str = "runner",
    version: str = "runner 1.0",
) -> tuple[Path, str]:
    product = tmp_path / "product"
    product.mkdir(parents=True)
    _run(["git", "init"], product)
    _run(["git", "config", "user.email", "test@example.invalid"], product)
    _run(["git", "config", "user.name", "Trusted Evidence Test"], product)
    (product / "requirements.lock").write_text("locked dependency\n", encoding="utf-8")
    runner = product / runner_path
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = \"--version\" ]; then echo '{version}'; exit 0; fi\n"
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
        "argv": ["./runner", "--json-report", "tests/test_contract.py::test_policy"],
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
        runner_body='for value; do test_id="$value"; done; printf \'{"tests":[{"nodeid":"%s","outcome":"passed"}]}\\n\' "$test_id" > .report.json; printf "CI:%s HOME:%s\\n" "$CI" "$HOME"',
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    suite = _suite(acceptance)
    monkeypatch.chdir(product)

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True
    assert result.error_code is None
    assert result.facts["selectedTestIds"] == ("tests/test_contract.py::test_policy",)
    assert "CI:1 HOME:" in result.facts["stdout"]
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
    assert result.facts["selectedTestIds"] == ("tests/test_contract.py::test_policy",)
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
    product, commit = _product_commit(
        tmp_path,
        runner_body=(
            "printf '%s\\n' 'Authorization: Bearer bearer-secret' 'Cookie: session=cookie-secret' "
            "'SSH_AUTH_SOCK=/private/agent.sock' 'key=/Users/test/.ssh/id_ed25519' "
            "'-----BEGIN PRIVATE KEY-----' 'private-key-secret' '-----END PRIVATE KEY-----' 'API_KEY=top-secret'; "
            "for value; do test_id=\"$value\"; done; printf '{\"tests\":[{\"nodeid\":\"%s\",\"outcome\":\"passed\"}]}\\n' \"$test_id\" > .report.json"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    runner = SuiteRunner(redaction_patterns=(re.compile(r"top-secret"),))

    result = runner.run(_suite(acceptance), context.subject_ref, context)
    receipt_ref = result.facts["receiptRef"]
    receipt = ContentAddressedStore(acceptance).read(receipt_ref)

    for secret in (b"top-secret", b"bearer-secret", b"cookie-secret", b"/private/agent.sock", b"id_ed25519", b"private-key-secret"):
        assert secret not in receipt
    assert b"[REDACTED]" in receipt
    digest = receipt_ref.removeprefix("sha256:")
    (acceptance / "evidence" / "sha256" / digest[:2] / digest).write_bytes(b"tampered")
    verification = E2SuiteVerifier("e2.fixture", _suite(acceptance), runner=runner).verify(
        _pointer(commit, receipt_ref), _profile(commit), context
    )
    assert verification.valid is False
    assert verification.error_code == "DIGEST_MISMATCH"


def test_e2_rejects_zero_collection_and_accepts_real_pytest_and_vitest_machine_reports(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path, runner_body="printf '{\"tests\":[]}\\n' > .report.json")
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)

    assert SuiteRunner().run(_suite(acceptance), context.subject_ref, context).error_code == "TEST_SELECTION_MISSING"

    vitest_product, vitest_commit = _product_commit(
        tmp_path / "vitest",
        runner_body=(
            "for value; do test_id=\"$value\"; done; printf '{\"testResults\":[{\"assertionResults\":[{\"fullName\":\"%s\",\"status\":\"passed\"}]}]}\\n' \"$test_id\""
        ),
    )
    monkeypatch.chdir(vitest_product)
    vitest_context = _context(acceptance, vitest_commit)
    vitest_suite = _suite(
        acceptance,
        parser={"kind": "vitest-json-v1"},
        argv=["./runner", "--reporter=json", "canvas saves independently"],
        expectedTestIds=["canvas saves independently"],
    )
    assert SuiteRunner().run(vitest_suite, vitest_context.subject_ref, vitest_context).valid is True


def test_e2_materialization_ignores_hostile_local_hook_and_smudge_configuration(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path, runner_body="exit 7")
    (product / ".gitattributes").write_text("runner filter=hostile\n", encoding="utf-8")
    _run(["git", "add", ".gitattributes"], product)
    _run(["git", "commit", "-m", "attributes"], product)
    commit = _run(["git", "rev-parse", "HEAD"], product)
    hooks = product / "hostile-hooks"
    hooks.mkdir()
    (hooks / "post-checkout").write_text(
        "#!/bin/sh\nprintf '%s\\n' '#!/bin/sh' 'if [ \"$1\" = \"--version\" ]; then echo runner 1.0; exit 0; fi' "
        "'for value; do test_id=\"$value\"; done; printf \"{\\\"tests\\\":[{\\\"nodeid\\\":\\\"%s\\\",\\\"outcome\\\":\\\"passed\\\"}]}\\\\n\" \"$test_id\" > .report.json' > runner\nchmod +x runner\n",
        encoding="utf-8",
    )
    (hooks / "post-checkout").chmod(0o755)
    _run(["git", "config", "core.hooksPath", str(hooks)], product)
    _run(["git", "config", "filter.hostile.smudge", "sed 's/exit 7/exit 0/'"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "TEST_REPLAY_FAILED"


def test_e2_rejects_annotated_tag_object_and_confirms_detached_head(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    _run(["git", "tag", "-a", "trusted-tag", "-m", "not a commit subject", commit], product)
    tag = _run(["git", "rev-parse", "trusted-tag^{tag}"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{tag}", _context(acceptance, tag))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"


def test_e2_runs_suite_from_a_safe_subdirectory(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path, runner_path="subdir/runner")
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    suite = _suite(acceptance, workingDirectory="subdir", argv=["./runner", "--json-report", "tests/test_contract.py::test_policy"])

    assert SuiteRunner().run(suite, context.subject_ref, context).valid is True


def test_e2_checks_setup_created_tool_after_setup_and_requires_exact_version(tmp_path, monkeypatch):
    bootstrap = (
        "mkdir -p generated; "
        "printf '%s\\n' '#!/bin/sh' 'if [ \"$1\" = \"--version\" ]; then echo runner 1.0; exit 0; fi' "
        "'for value; do test_id=\"$value\"; done; printf \"{\\\"tests\\\":[{\\\"nodeid\\\":\\\"%s\\\",\\\"outcome\\\":\\\"passed\\\"}]}\\\\n\" \"$test_id\" > .report.json' > generated/runner; "
        "chmod +x generated/runner"
    )
    product, commit = _product_commit(tmp_path, runner_body=bootstrap, runner_path="bootstrap")
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    suite = _suite(
        acceptance,
        setupArgv=["./bootstrap"],
        argv=["./generated/runner", "--json-report", "tests/test_contract.py::test_policy"],
    )

    assert SuiteRunner().run(suite, context.subject_ref, context).valid is True

    suffix_product, suffix_commit = _product_commit(tmp_path / "suffix", version="runner 1.0 unexpected")
    monkeypatch.chdir(suffix_product)
    suffix_context = _context(acceptance, suffix_commit)
    assert SuiteRunner().run(_suite(acceptance), suffix_context.subject_ref, suffix_context).error_code == "SOURCE_REF_UNREACHABLE"


@pytest.mark.parametrize("cleanup_failure", ("nonzero", "timeout"))
def test_e2_returns_blocked_when_temporary_worktree_cleanup_fails(tmp_path, monkeypatch, cleanup_failure):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    original_run = subprocess.run

    def run_with_cleanup_failure(argv, *args, **kwargs):
        if argv[0] == "git" and "worktree" in argv and "remove" in argv:
            if cleanup_failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 30)
            return subprocess.CompletedProcess(argv, 1, b"", b"cleanup failed")
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr("trusted_evidence.verifiers.e2_suite.subprocess.run", run_with_cleanup_failure)
    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
