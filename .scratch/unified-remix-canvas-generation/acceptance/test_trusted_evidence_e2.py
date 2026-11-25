import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import pytest

from trusted_evidence.models import AcceptanceProfile, EvidencePointer, VerificationContext
from trusted_evidence.registry import E2SuiteDefinition
from trusted_evidence.storage import ContentAddressedStore
from trusted_evidence.verifiers.e2_suite import E2SuiteVerifier, SuiteRunner


OBSERVED_AT = "2026-10-02T08:30:00Z"
TEST_NODE = Path(shutil.which("node") or "").resolve(strict=True)
TEST_NODE_VERSION = subprocess.run(
    [TEST_NODE, "--version"], check=True, capture_output=True, text=True
).stdout.strip()


@pytest.fixture(autouse=True)
def _trusted_test_node_on_path(monkeypatch):
    monkeypatch.setenv("PATH", str(TEST_NODE.parent) + os.pathsep + os.environ["PATH"])


def _run(argv: list[str], cwd: Path) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _product_commit(
    tmp_path: Path,
    *,
    runner_body: str = (
        "const testId = process.argv.at(-1);\n"
        "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: "
        "{total: 1, collected: 1, passed: 1, failed: 0, skipped: 0}, "
        "tests: [{nodeid: testId, outcome: 'passed'}]}), 'utf8');"
    ),
    runner_path: str = "runner",
    version: str = "runner 1.0",
    files: dict[str, str] | None = None,
) -> tuple[Path, str]:
    product = tmp_path / "product"
    product.mkdir(parents=True)
    _run(["git", "init"], product)
    _run(["git", "config", "user.email", "test@example.invalid"], product)
    _run(["git", "config", "user.name", "Trusted Evidence Test"], product)
    (product / "requirements.lock").write_text("locked dependency\n", encoding="utf-8")
    for relative, contents in (files or {}).items():
        target = product / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    runner = product / runner_path
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(
        f"#!{TEST_NODE}\n"
        "const fs = require('fs'); const cp = require('child_process');\n"
        f"if (process.argv[2] === '--version') {{ console.log({version!r}); process.exit(0); }}\n"
        f"{runner_body}\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    _run(["git", "add", "."], product)
    _run(["git", "commit", "-m", "fixture"], product)
    _run(["git", "config", "--unset", "user.email"], product)
    _run(["git", "config", "--unset", "user.name"], product)
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
        "toolVersions": {
            "git": subprocess.run(
                [shutil.which("git") or "git", "--version"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "runner": "runner 1.0",
            TEST_NODE.name: TEST_NODE_VERSION,
        },
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
        runner_body=(
            "const testId = process.argv.at(-1);\n"
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
            "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
            "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');\n"
            "console.log(`CI:${process.env.CI ?? ''} HOME:${process.env.HOME ?? ''}`);"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    suite = _suite(acceptance)
    monkeypatch.chdir(product)

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True
    assert result.error_code is None
    assert result.facts["selectedTestIds"] == ("tests/test_contract.py::test_policy",)
    assert "CI:1 HOME:" in result.facts["invocations"][0]["stdout"]
    assert result.facts["receiptRef"].startswith("sha256:")


def test_e2_flat_and_single_nested_argv_have_the_same_normalized_input_digest(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    command = ["./runner", "--json-report", "tests/test_contract.py::test_policy"]

    flat = SuiteRunner().run(_suite(acceptance, argv=command), context.subject_ref, context)
    nested = SuiteRunner().run(_suite(acceptance, argv=[command]), context.subject_ref, context)

    assert flat.valid is True
    assert nested.valid is True
    assert flat.input_digest == nested.input_digest


def test_e2_runs_each_registered_invocation_and_merges_exact_test_selection(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)

    result = SuiteRunner().run(
        _suite(
            acceptance,
            argv=[
                ["./runner", "--json-report", "tests/test_contract.py::test_first"],
                ["./runner", "--json-report", "tests/test_contract.py::test_second"],
            ],
            expectedTestIds=[
                "tests/test_contract.py::test_first",
                "tests/test_contract.py::test_second",
            ],
        ),
        context.subject_ref,
        context,
    )

    assert result.valid is True
    assert result.facts["selectedTestIds"] == (
        "tests/test_contract.py::test_first",
        "tests/test_contract.py::test_second",
    )


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
    assert "compromised" not in result.facts["invocations"][0]["stdout"]


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
    product, commit = _product_commit(tmp_path, runner_body="process.exit(7);")
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
    slow_product, slow_commit = _product_commit(
        tmp_path / "slow",
        runner_body="Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2000);",
    )
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
            "console.log(['Authorization: Bearer bearer-secret', 'Cookie: session=cookie-secret', "
            "'SSH_AUTH_SOCK=/private/agent.sock', 'key=/Users/test/.ssh/id_ed25519', "
            "'-----BEGIN PRIVATE KEY-----', 'private-key-secret', '-----END PRIVATE KEY-----', "
            "'API_KEY=top-secret', 'AWS_ACCESS_KEY_ID=AKIAEXAMPLE', "
            "'AWS_SESSION_TOKEN=session-secret', 'COOKIE=environment-cookie', "
            "'https://username:url-password@example.invalid/path'].join('\\n'));\n"
            "const testId = process.argv.at(-1);\n"
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
            "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
            "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');"
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

    for secret in (b"top-secret", b"bearer-secret", b"cookie-secret", b"/private/agent.sock", b"id_ed25519", b"private-key-secret", b"AKIAEXAMPLE", b"session-secret", b"environment-cookie", b"url-password"):
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
    product, commit = _product_commit(
        tmp_path,
        runner_body=(
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 0, "
            "collected: 0, passed: 0, failed: 0, skipped: 0}, tests: []}), 'utf8');"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)

    assert SuiteRunner().run(_suite(acceptance), context.subject_ref, context).error_code == "TEST_SELECTION_MISSING"

    vitest_product, vitest_commit = _product_commit(
        tmp_path / "vitest",
        runner_body=(
            "console.log(JSON.stringify({numTotalTestSuites: 1, numPassedTestSuites: 1, "
            "numFailedTestSuites: 0, numPendingTestSuites: 0, numTotalTests: 1, "
            "numPassedTests: 1, numFailedTests: 0, numPendingTests: 0, numTodoTests: 0, "
            "success: true, testResults: [{name: 'frontend/src/Canvas.test.tsx', "
            "status: 'passed', assertionResults: [{fullName: 'canvas saves independently', "
            "status: 'passed'}]}]}));"
        ),
        files={"frontend/src/Canvas.test.tsx": "test placeholder\n"},
    )
    monkeypatch.chdir(vitest_product)
    vitest_context = _context(acceptance, vitest_commit)
    vitest_suite = _suite(
        acceptance,
        parser={"kind": "vitest-json-v1"},
        argv=["./runner", "--reporter=json", "frontend/src/Canvas.test.tsx::canvas saves independently"],
        expectedTestIds=["frontend/src/Canvas.test.tsx::canvas saves independently"],
    )
    assert SuiteRunner().run(vitest_suite, vitest_context.subject_ref, vitest_context).valid is True


def test_e2_materialization_ignores_hostile_local_hook_and_smudge_configuration(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path, runner_body="process.exit(7);")
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
    assert result.error_code == "SOURCE_REF_UNREACHABLE"


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


def test_e2_rejects_setup_created_regular_tools_before_setup_side_effects(tmp_path, monkeypatch):
    marker = tmp_path / "setup-ran"
    bootstrap = (
        f"fs.writeFileSync({str(marker)!r}, 'ran');\n"
        "fs.mkdirSync('generated', {recursive: true});\n"
        "fs.writeFileSync('generated/runner', 'never trusted', 'utf8');"
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
        toolVersions={
            "git": _suite(acceptance).tool_versions["git"],
            "bootstrap": "runner 1.0",
            "runner": "runner 1.0",
            TEST_NODE.name: TEST_NODE_VERSION,
        },
    )

    blocked = SuiteRunner().run(suite, context.subject_ref, context)
    assert blocked.error_code == "SOURCE_REF_UNREACHABLE"
    assert not marker.exists()

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
    original_git = SuiteRunner._git

    def git_with_cleanup_failure(self, argv, *args, **kwargs):
        if "worktree" in argv and "remove" in argv:
            if cleanup_failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 30)
            return subprocess.CompletedProcess(argv, 1, b"", b"cleanup failed")
        return original_git(self, argv, *args, **kwargs)

    monkeypatch.setattr(SuiteRunner, "_git", git_with_cleanup_failure)
    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"


@pytest.mark.parametrize("attack", ("alternates", "promisor"))
def test_e2_blocks_common_object_indirection_and_promisor_configuration(tmp_path, monkeypatch, attack):
    product, commit = _product_commit(tmp_path)
    if attack == "alternates":
        alternate = product / ".git" / "objects" / "info" / "alternates"
        alternate.parent.mkdir(parents=True, exist_ok=True)
        alternate.write_text("/untrusted/objects\n", encoding="utf-8")
    else:
        _run(["git", "config", "remote.origin.promisor", "true"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"


def test_e2_rejects_stale_or_symlinked_pytest_report_before_execution(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path, runner_body="void 0;")
    (product / ".report.json").write_text('{"tests":[{"nodeid":"tests/test_contract.py::test_policy","outcome":"passed"}]}', encoding="utf-8")
    _run(["git", "add", ".report.json"], product)
    _run(["git", "commit", "-m", "stale report"], product)
    commit = _run(["git", "rev-parse", "HEAD"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    stale = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert stale.error_code == "TEST_SELECTION_MISSING"


def test_e2_rejects_null_vitest_assertions_and_plain_text_receipts(tmp_path, monkeypatch):
    product, commit = _product_commit(
        tmp_path,
        runner_body="console.log(JSON.stringify({testResults: [{assertionResults: null}]}));",
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    suite = _suite(
        acceptance,
        parser={"kind": "vitest-json-v1"},
        argv=["./runner", "--reporter=json", "canvas test"],
        expectedTestIds=["canvas test"],
    )
    assert SuiteRunner().run(suite, context.subject_ref, context).error_code == "TEST_SELECTION_MISSING"

    valid_product, valid_commit = _product_commit(tmp_path / "valid")
    monkeypatch.chdir(valid_product)
    valid_context = _context(acceptance, valid_commit)
    plain = ContentAddressedStore(acceptance).put(b"not a receipt")
    verification = E2SuiteVerifier("e2.fixture", _suite(acceptance)).verify(
        _pointer(valid_commit, plain), _profile(valid_commit), valid_context
    )
    assert verification.error_code == "POINTER_INVALID"


def test_e2_fails_closed_for_npm_until_a_fixed_node_and_cli_closure_is_registered(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    versions = {
        name: subprocess.run([name, "--version"], check=True, capture_output=True, text=True).stdout.strip()
        for name in ("uv", "npm", "node")
    }
    result = SuiteRunner().run(
        _suite(
            acceptance,
            toolVersions={
                "git": _suite(acceptance).tool_versions["git"],
                "runner": "runner 1.0",
                **versions,
            },
        ),
        f"git://product@{commit}",
        _context(acceptance, commit),
    )

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
    assert "receiptRef" not in result.facts


def test_e2_bounds_output_and_kills_a_timed_out_process_group(tmp_path, monkeypatch):
    product, commit = _product_commit(
        tmp_path,
        runner_body=(
            "process.stdout.write('output\\n'.repeat(20000));\n"
            "const testId = process.argv.at(-1);\n"
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
            "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
            "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    bounded = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))
    assert bounded.valid is True
    assert len(bounded.facts["invocations"][0]["stdout"].encode()) <= 32 * 1024 + len("\n...[truncated]".encode())

    sleepy, sleepy_commit = _product_commit(
        tmp_path / "sleepy",
        runner_body=(
            "cp.spawnSync(process.execPath, ['-e', 'setTimeout(() => {}, 10000)'], {stdio: 'inherit'});"
        ),
    )
    monkeypatch.chdir(sleepy)
    started = __import__("time").monotonic()
    timed = SuiteRunner().run(_suite(acceptance, timeoutSeconds=1), f"git://product@{sleepy_commit}", _context(acceptance, sleepy_commit))
    assert timed.error_code == "TEST_REPLAY_TIMEOUT"
    assert __import__("time").monotonic() - started < 3


@pytest.mark.parametrize(
    "attack",
    ("git-symlink", "http-alternates", "shallow", "grafts", "replace-ref", "packed-replace", "fsmonitor"),
)
def test_e2_rejects_untrusted_raw_git_metadata_before_object_resolution(tmp_path, monkeypatch, attack):
    product, commit = _product_commit(tmp_path)
    git_dir = product / ".git"
    if attack == "git-symlink":
        real_git = product / "real.git"
        git_dir.rename(real_git)
        git_dir.symlink_to(real_git, target_is_directory=True)
    elif attack == "http-alternates":
        path = git_dir / "objects" / "info" / "http-alternates"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("https://untrusted.invalid/objects\n", encoding="utf-8")
    elif attack == "shallow":
        (git_dir / "shallow").write_text(commit + "\n", encoding="ascii")
    elif attack == "grafts":
        (git_dir / "info" / "grafts").write_text(commit + "\n", encoding="ascii")
    elif attack == "replace-ref":
        path = git_dir / "refs" / "replace" / commit
        path.parent.mkdir(parents=True)
        path.write_text(commit + "\n", encoding="ascii")
    elif attack == "packed-replace":
        (git_dir / "packed-refs").write_text(
            f"# pack-refs with: peeled fully-peeled sorted\n{commit} refs/replace/{commit}\n",
            encoding="ascii",
        )
    else:
        _run(["git", "config", "core.fsmonitor", "touch fsmonitor-executed"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
    assert not (product / "fsmonitor-executed").exists()


def test_e2_accepts_a_real_git_worktree_pointer_without_trusting_external_symlinks(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    linked = tmp_path / "linked"
    _run(["git", "worktree", "add", "--detach", str(linked), commit], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(linked)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True


def test_e2_requires_internal_git_to_be_declared_before_any_git_operation(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(
        _suite(acceptance, toolVersions={"runner": "runner 1.0"}),
        f"git://product@{commit}",
        _context(acceptance, commit),
    )

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"


def test_e2_blocks_a_host_tool_that_replaces_itself_during_version_probe_without_running_it(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    marker = tmp_path / "replacement-executed"
    replacement = host_bin / "replacement"
    replacement.write_text(
        f"#!{TEST_NODE}\n"
        "const fs = require('fs');\n"
        f"fs.writeFileSync({str(marker)!r}, 'ran');\n"
        "const testId = process.argv.at(-1);\n"
        "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
        "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
        "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');\n",
        encoding="utf-8",
    )
    replacement.chmod(0o755)
    tool = host_bin / "mutable-tool"
    tool.write_text(
        f"#!{TEST_NODE}\n"
        "const fs = require('fs');\n"
        "if (process.argv[2] === '--version') {\n"
        f"  const replacement = {str(replacement)!r};\n"
        "  const target = process.argv[1]; const nextPath = `${target}.next`;\n"
        "  fs.copyFileSync(replacement, nextPath); fs.chmodSync(nextPath, 0o500);\n"
        "  fs.renameSync(nextPath, target); console.log('mutable-tool 1.0'); process.exit(0);\n"
        "}\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    monkeypatch.setenv("PATH", str(host_bin) + os.pathsep + os.environ["PATH"])
    git_version = _suite(acceptance).tool_versions["git"]
    suite = _suite(
        acceptance,
        argv=["mutable-tool", "--json-report", "tests/test_contract.py::test_policy"],
        toolVersions={
            "git": git_version,
            TEST_NODE.name: TEST_NODE_VERSION,
            "mutable-tool": "mutable-tool 1.0",
        },
    )

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
    assert not marker.exists()


def test_e2_allows_only_a_venv_symlink_to_the_prevalidated_declared_host_tool(tmp_path, monkeypatch):
    node = TEST_NODE
    runner_body = (
        "const fs = require('fs'); const testId = process.argv.at(-1);\n"
        "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
        "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
        "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');\n"
    )
    product, commit = _product_commit(tmp_path, runner_body="void 0;", files={"runner.js": runner_body})
    venv = product / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "node").symlink_to(node)
    _run(["git", "add", "venv/bin/node", "runner.js"], product)
    _run(["git", "commit", "-m", "venv symlink"], product)
    commit = _run(["git", "rev-parse", "HEAD"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    git_version = _suite(acceptance).tool_versions["git"]
    suite = _suite(
        acceptance,
        argv=["./venv/bin/node", "runner.js", "--json-report", "tests/test_contract.py::test_policy"],
        toolVersions={"git": git_version, node.name: TEST_NODE_VERSION},
    )

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True
    assert result.facts["toolIdentities"][node.name]["sha256"].startswith("sha256:")


def test_e2_rejects_a_venv_symlink_to_an_unvalidated_external_executable(tmp_path, monkeypatch):
    marker = tmp_path / "untrusted-tool-executed"
    untrusted = tmp_path / "untrusted-python"
    untrusted.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    untrusted.chmod(0o755)
    product, commit = _product_commit(tmp_path / "product-root")
    venv = product / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(untrusted)
    _run(["git", "add", "venv/bin/python"], product)
    _run(["git", "commit", "-m", "untrusted venv symlink"], product)
    commit = _run(["git", "rev-parse", "HEAD"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    suite = _suite(
        acceptance,
        argv=["./venv/bin/python", "runner.py", "--json-report", "tests/test_contract.py::test_policy"],
        toolVersions={
            "git": _suite(acceptance).tool_versions["git"],
            TEST_NODE.name: TEST_NODE_VERSION,
        },
    )

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
    assert not marker.exists()


def test_e2_redacts_extended_secret_vocabulary_and_truncated_values_with_one_residual_guard(tmp_path, monkeypatch):
    secrets = (
        "OPENAI_API_KEY=openai-secret",
        "GITHUB_TOKEN=github-secret",
        "AZURE_CLIENT_SECRET=azure-secret",
        "AWS_SECRET_ACCESS_KEY=aws-secret",
        "AWS_SECURITY_TOKEN=aws-security-secret",
        '{"password":"json-password","client_secret":"json-client","token":"json-token","api key":"json-api"}',
        "HTTP_X_COOKIE=prefix-cookie",
        "REQUEST_AUTH=prefix-auth",
        "X-Auth: prefix-hyphen-auth",
        "CI_SSH_AUTH_SOCK=/tmp/prefix-agent",
        "Proxy-Authorization: Basic proxy-secret",
        "https://user:url-secret@example.invalid/path",
        '{"client_secret":"truncated-secret',
    )
    product, commit = _product_commit(
        tmp_path,
        runner_body=(
            f"console.log({list(secrets)!r}.join('\\n'));\n"
            "const testId = process.argv.at(-1);\n"
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
            "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
            "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))
    receipt = ContentAddressedStore(acceptance).read(result.facts["receiptRef"])

    for secret in (
        b"openai-secret", b"github-secret", b"azure-secret", b"aws-secret", b"aws-security-secret",
        b"json-password", b"json-client", b"json-token", b"json-api", b"prefix-cookie", b"prefix-auth",
        b"prefix-hyphen-auth",
        b"/tmp/prefix-agent", b"proxy-secret", b"url-secret", b"truncated-secret",
    ):
        assert secret not in receipt


@pytest.mark.parametrize(
    ("parser", "body", "expected_id", "expected_code"),
    (
        (
            {"kind": "pytest-json-v1"},
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
            "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: null}), 'utf8');",
            "tests/test_contract.py::test_policy",
            "TEST_SELECTION_MISSING",
        ),
        (
            {"kind": "pytest-json-v1"},
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 2, "
            "collected: 2, passed: 2, failed: 0, skipped: 0}, tests: "
            "[{nodeid: 'tests/test_contract.py::test_policy', outcome: 'passed'}, "
            "{nodeid: 'tests/test_contract.py::test_policy', outcome: 'passed'}]}), 'utf8');",
            "tests/test_contract.py::test_policy",
            "TEST_SELECTION_MISSING",
        ),
        (
            {"kind": "pytest-json-v1"},
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
            "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
            "[{nodeid: 'tests/test_contract.py::test_policy', outcome: 'failed'}]}), 'utf8');",
            "tests/test_contract.py::test_policy",
            "TEST_SELECTION_MISSING",
        ),
        (
            {"kind": "vitest-json-v1"},
            "console.log(JSON.stringify({success: true, testResults: [{name: "
            "'frontend/src/Sample.test.tsx', status: 'passed', assertionResults: "
            "[{fullName: 'sample works', status: 'passed'}]}]}));",
            "frontend/src/Sample.test.tsx::sample works",
            "TEST_SELECTION_MISSING",
        ),
    ),
)
def test_e2_reporters_fail_closed_on_malformed_rows_and_count_mismatches(
    tmp_path, monkeypatch, parser, body, expected_id, expected_code
):
    product, commit = _product_commit(tmp_path, runner_body=body)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    reporter_flag = "--json-report" if parser["kind"] == "pytest-json-v1" else "--reporter=json"
    suite = _suite(
        acceptance,
        parser=parser,
        argv=["./runner", reporter_flag, expected_id],
        expectedTestIds=[expected_id],
    )

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == expected_code


@pytest.mark.parametrize("kind", ("pytest", "vitest"))
def test_e2_accepts_realistic_reporter_fixtures_and_binds_vitest_file_identity(tmp_path, monkeypatch, kind):
    fixture_root = Path(__file__).parent / "fixtures" / "reporters"
    fixture = (fixture_root / f"{kind}-pass.json").read_text(encoding="utf-8")
    if kind == "pytest":
        body = "fs.copyFileSync('reporter.json', '.report.json');"
        parser = {"kind": "pytest-json-v1"}
        flag = "--json-report"
        expected_id = "tests/test_contract.py::test_policy"
    else:
        body = "console.log(fs.readFileSync('reporter.json', 'utf8'));"
        parser = {"kind": "vitest-json-v1"}
        flag = "--reporter=json"
        expected_id = "frontend/src/CreativeCanvas.test.tsx::Creative canvas saves independently"
    files = {"reporter.json": fixture}
    if kind == "vitest":
        files["frontend/src/CreativeCanvas.test.tsx"] = "test placeholder\n"
    product, commit = _product_commit(tmp_path, runner_body=body, files=files)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    suite = _suite(acceptance, parser=parser, argv=["./runner", flag, expected_id], expectedTestIds=[expected_id])

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True
    assert result.facts["selectedTestIds"] == (expected_id,)


def test_e2_failed_report_keeps_only_safely_observed_expected_ids(tmp_path, monkeypatch):
    expected = "tests/test_contract.py::test_policy"
    product, commit = _product_commit(
        tmp_path,
        runner_body=(
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 1, summary: {total: 2, "
            "collected: 2, passed: 0, failed: 2, skipped: 0}, tests: "
            "[{nodeid: 'tests/test_contract.py::test_policy', outcome: 'failed'}, "
            "{nodeid: 'GITHUB_TOKEN=must-not-persist', outcome: 'failed'}]}), 'utf8');\n"
            "process.exit(1);"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.error_code == "TEST_REPLAY_FAILED"
    assert result.facts["selectedTestIds"] == (expected,)
    assert "must-not-persist" not in repr(result.facts)


def test_e2_receipt_binds_each_invocation_and_rejects_secret_or_policy_mutations(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    suite = _suite(acceptance)
    result = SuiteRunner().run(suite, context.subject_ref, context)
    raw = ContentAddressedStore(acceptance).read(result.facts["receiptRef"])
    receipt = json.loads(raw)
    invocation = receipt["invocations"][0]

    verification = E2SuiteVerifier("e2.fixture", suite).verify(
        _pointer(commit, result.facts["receiptRef"]), _profile(commit), context
    )
    assert verification.valid is True
    assert invocation["argv"] == list(suite.argv[0])
    assert invocation["durationMilliseconds"] >= 0
    assert set(invocation) == {
        "argv", "durationMilliseconds", "exitCode", "selectedTestIds", "stderr", "stdout"
    }

    mutations = []
    for mutate in (
        lambda value: value["invocations"][0].__setitem__("argv", ["./other"]),
        lambda value: value["invocations"][0].__setitem__("durationMilliseconds", -1),
        lambda value: value["invocations"][0].__setitem__("selectedTestIds", ["unknown-id"]),
        lambda value: value["invocations"][0].__setitem__("stdout", "GITHUB_TOKEN=receipt-secret"),
        lambda value: value.__setitem__("toolVersions", {"git": "wrong"}),
        lambda value: value.__setitem__("lockfileDigests", {"requirements.lock": "sha256:" + "0" * 64}),
    ):
        changed = json.loads(raw)
        mutate(changed)
        mutations.append(changed)
    for changed in mutations:
        ref = ContentAddressedStore(acceptance).put(
            json.dumps(changed, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        )
        verification = E2SuiteVerifier("e2.fixture", suite).verify(_pointer(commit, ref), _profile(commit), context)
        assert verification.valid is False
        assert verification.error_code == "POINTER_INVALID"


@pytest.mark.parametrize("failure", ("temporary-directory", "cas-write"))
def test_e2_converts_os_failures_to_stable_blocking_results_and_writes_no_receipt(tmp_path, monkeypatch, failure):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    if failure == "temporary-directory":
        monkeypatch.setattr(
            "trusted_evidence.verifiers.e2_suite.tempfile.TemporaryDirectory",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("temp unavailable")),
        )
    else:
        monkeypatch.setattr(
            "trusted_evidence.verifiers.e2_suite.ContentAddressedStore.put",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cas unavailable")),
        )

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
    assert "receiptRef" not in result.facts


def test_e2_converts_selector_read_failure_to_a_stable_block_and_reaps_the_process(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    original_selector = __import__("selectors").DefaultSelector
    calls = 0

    class FailingSelector:
        def __new__(cls):
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise OSError("selector unavailable")
            return original_selector()

    monkeypatch.setattr("trusted_evidence.verifiers.e2_suite.selectors.DefaultSelector", FailingSelector)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is False
    assert result.error_code == "SOURCE_REF_UNREACHABLE"
    assert "receiptRef" not in result.facts


def test_e2_receipt_cas_read_oserror_is_a_stable_pointer_rejection(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    suite = _suite(acceptance)
    pointer = _pointer(commit, "sha256:" + "a" * 64)
    monkeypatch.setattr(
        "trusted_evidence.verifiers.e2_suite.ContentAddressedStore.read",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cas unavailable")),
    )

    result = E2SuiteVerifier("e2.fixture", suite).verify(pointer, _profile(commit), context)

    assert result.valid is False
    assert result.error_code == "POINTER_INVALID"


def test_e2_executes_a_private_snapshot_when_the_host_path_is_replaced_between_check_and_popen(
    tmp_path, monkeypatch
):
    product, commit = _product_commit(tmp_path)
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    marker = tmp_path / "replacement-executed"
    tool = host_bin / "host-runner"
    tool.write_text(
        f"#!{TEST_NODE}\n"
        "const fs = require('fs');\n"
        "if (process.argv[2] === '--version') { console.log('host-runner 1.0'); process.exit(0); }\n"
        "const testId = process.argv.at(-1);\n"
        "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
        "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
        "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    monkeypatch.setenv("PATH", str(host_bin) + os.pathsep + os.environ["PATH"])
    base = _suite(acceptance).tool_versions
    suite = _suite(
        acceptance,
        argv=["host-runner", "--json-report", "tests/test_contract.py::test_policy"],
        toolVersions={
            "git": base["git"],
            TEST_NODE.name: TEST_NODE_VERSION,
            "host-runner": "host-runner 1.0",
        },
    )
    original_popen = subprocess.Popen

    def replace_before_popen(argv, *args, **kwargs):
        if argv and argv[0] == str(tool) and "--version" not in argv:
            tool.write_text(
                f"#!{TEST_NODE}\n"
                "const fs = require('fs');\n"
                f"fs.writeFileSync({str(marker)!r}, 'ran');\n"
                "const testId = process.argv.at(-1);\n"
                "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
                "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
                "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');\n",
                encoding="utf-8",
            )
            tool.chmod(0o755)
        return original_popen(argv, *args, **kwargs)

    monkeypatch.setattr("trusted_evidence.verifiers.e2_suite.subprocess.Popen", replace_before_popen)

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.valid is True
    assert not marker.exists()


def test_e2_validates_the_shebang_interpreter_before_probing_a_script(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    marker = tmp_path / "script-probed-before-interpreter"
    tool = host_bin / "script-tool"
    tool.write_text(
        f"#!{TEST_NODE}\n"
        "const fs = require('fs');\n"
        f"fs.writeFileSync({str(marker)!r}, 'probed');\n"
        "if (process.argv[2] === '--version') { console.log('script-tool 1.0'); process.exit(0); }\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    monkeypatch.setenv("PATH", str(host_bin) + os.pathsep + os.environ["PATH"])
    base = _suite(acceptance).tool_versions
    suite = _suite(
        acceptance,
        argv=["script-tool", "--json-report", "tests/test_contract.py::test_policy"],
        toolVersions={
            "git": base["git"],
            TEST_NODE.name: "not-the-host-node",
            "script-tool": "script-tool 1.0",
        },
    )

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.error_code == "SOURCE_REF_UNREACHABLE"
    assert not marker.exists()


@pytest.mark.parametrize("attack", ("pack", "idx", "loose", "promisor"))
def test_e2_rejects_symlinks_and_promisor_sidecars_anywhere_in_the_object_tree(
    tmp_path, monkeypatch, attack
):
    product, commit = _product_commit(tmp_path)
    objects = product / ".git" / "objects"
    if attack in {"pack", "idx", "promisor"}:
        _run(["git", "gc"], product)
        pack = next((objects / "pack").glob("*.pack"))
        idx = pack.with_suffix(".idx")
        if attack in {"pack", "idx"}:
            target = pack if attack == "pack" else idx
            external = tmp_path / f"external-{target.name}"
            shutil.copyfile(target, external)
            target.unlink()
            target.symlink_to(external)
        else:
            pack.with_suffix(".promisor").write_text("", encoding="ascii")
    else:
        loose = next(
            path for path in objects.glob("[0-9a-f][0-9a-f]/*") if path.is_file()
        )
        external = tmp_path / "external-loose-object"
        shutil.copyfile(loose, external)
        loose.unlink()
        loose.symlink_to(external)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.error_code == "SOURCE_REF_UNREACHABLE"


def test_e2_redacts_assignment_headers_and_common_raw_provider_tokens_from_cas(tmp_path, monkeypatch):
    secrets = (
        "AUTHORIZATION=Bearer header-secret",
        "ghp_0123456789abcdefghijklmnopqrstuvwxyzAB",
        "github_pat_11AA0123456789abcdefghijklmnopqrstuvwxyz",
        "sk-0123456789abcdefghijklmnopqrstuv",
        "sk-proj-0123456789abcdefghijklmnopqrstuvwxyz",
        "AKIAABCDEFGHIJKLMNOP",
        "AZURE_OPENAI_API_KEY=azure-provider-secret",
        "AWS_SESSION_TOKEN=aws-provider-secret",
    )
    product, commit = _product_commit(
        tmp_path,
        runner_body=(
            f"console.log({list(secrets)!r}.join('\\n'));\n"
            "const testId = process.argv.at(-1);\n"
            "fs.writeFileSync('.report.json', JSON.stringify({exitcode: 0, summary: {total: 1, "
            "collected: 1, passed: 1, failed: 0, skipped: 0}, tests: "
            "[{nodeid: testId, outcome: 'passed'}]}), 'utf8');"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))
    receipt = ContentAddressedStore(acceptance).read(result.facts["receiptRef"])

    for secret in (
        b"header-secret", b"ghp_", b"github_pat_", b"sk-", b"AKIAABCDEFGHIJKLMNOP",
        b"azure-provider-secret", b"aws-provider-secret",
    ):
        assert secret not in receipt


@pytest.mark.parametrize("kind", ("missing-file", "failed-suite-with-passed-assertion"))
def test_e2_vitest_requires_a_real_allowed_file_and_consistent_suite_status(tmp_path, monkeypatch, kind):
    report = {
        "numTotalTestSuites": 1,
        "numPassedTestSuites": 1 if kind == "missing-file" else 0,
        "numFailedTestSuites": 0 if kind == "missing-file" else 1,
        "numPendingTestSuites": 0,
        "numTotalTests": 1,
        "numPassedTests": 1,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "numTodoTests": 0,
        "success": True,
        "testResults": [{
            "name": "frontend/src/Missing.test.tsx" if kind == "missing-file" else "frontend/src/Present.test.tsx",
            "status": "passed" if kind == "missing-file" else "failed",
            "assertionResults": [{"fullName": "suite behavior", "status": "passed"}],
        }],
    }
    relative = report["testResults"][0]["name"]
    files = {} if kind == "missing-file" else {relative: "test placeholder\n"}
    product, commit = _product_commit(
        tmp_path,
        runner_body="console.log(fs.readFileSync('reporter.json', 'utf8'));",
        files={**files, "reporter.json": json.dumps(report)},
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    expected_id = f"{relative}::suite behavior"
    suite = _suite(
        acceptance,
        parser={"kind": "vitest-json-v1"},
        argv=["./runner", "--reporter=json", expected_id],
        expectedTestIds=[expected_id],
    )

    result = SuiteRunner().run(suite, f"git://product@{commit}", _context(acceptance, commit))

    assert result.error_code == "TEST_SELECTION_MISSING"


@pytest.mark.parametrize(
    "summary",
    (
        {"total": 1, "collected": 2, "passed": 1, "failed": 0, "skipped": 0},
        {"total": 1, "collected": 1, "passed": 1, "failed": 0},
    ),
)
def test_e2_pytest_requires_complete_consistent_collected_passed_failed_skipped_counts(
    tmp_path, monkeypatch, summary
):
    report = {
        "exitcode": 0,
        "summary": summary,
        "tests": [{"nodeid": "tests/test_contract.py::test_policy", "outcome": "passed"}],
    }
    product, commit = _product_commit(
        tmp_path,
        runner_body="fs.copyFileSync('reporter.json', '.report.json');",
        files={"reporter.json": json.dumps(report)},
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.error_code == "TEST_SELECTION_MISSING"


def test_e2_kills_the_process_group_when_the_parent_exits_before_a_pipe_holding_child(
    tmp_path, monkeypatch
):
    pid_file = tmp_path / "background.pid"
    product, commit = _product_commit(
        tmp_path,
        runner_body=(
            "const child = cp.spawn(process.execPath, ['-e', 'setTimeout(() => {}, 30000)'], "
            "{stdio: 'inherit'});\n"
            f"fs.writeFileSync({str(pid_file)!r}, String(child.pid), 'ascii');\n"
            "process.exit(0);"
        ),
    )
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(
        _suite(acceptance, timeoutSeconds=1), f"git://product@{commit}", _context(acceptance, commit)
    )
    child_pid = int(pid_file.read_text(encoding="ascii").strip())
    time.sleep(0.1)
    alive = True
    try:
        os.kill(child_pid, 0)
    except OSError:
        alive = False
    finally:
        if alive:
            os.kill(child_pid, 9)

    assert result.error_code == "TEST_REPLAY_TIMEOUT"
    assert alive is False


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("diff.external", "touch diff-executed"),
        ("core.gitProxy", "touch proxy-executed"),
        ("core.askPass", "touch askpass-executed"),
        ("core.alternateRefsCommand", "touch refs-executed"),
        ("difftool.fixture.cmd", "touch difftool-executed"),
        ("mergetool.fixture.cmd", "touch mergetool-executed"),
        ("submodule.fixture.update", "!touch submodule-executed"),
        ("user.email", "unknown@example.invalid"),
    ),
)
def test_e2_git_local_config_is_a_fail_closed_allowlist(tmp_path, monkeypatch, key, value):
    product, commit = _product_commit(tmp_path)
    _run(["git", "config", "--local", key, value], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)

    result = SuiteRunner().run(_suite(acceptance), f"git://product@{commit}", _context(acceptance, commit))

    assert result.error_code == "SOURCE_REF_UNREACHABLE"


def test_e2_receipt_binds_current_tool_snapshots_per_invocation_ids_and_total_duration(
    tmp_path, monkeypatch
):
    product, commit = _product_commit(tmp_path)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    context = _context(acceptance, commit)
    suite = _suite(
        acceptance,
        argv=[
            ["./runner", "--json-report", "tests/test_contract.py::test_first"],
            ["./runner", "--json-report", "tests/test_contract.py::test_second"],
        ],
        expectedTestIds=[
            "tests/test_contract.py::test_first",
            "tests/test_contract.py::test_second",
        ],
    )
    result = SuiteRunner().run(suite, context.subject_ref, context)
    raw = ContentAddressedStore(acceptance).read(result.facts["receiptRef"])

    mutations = []
    swapped_tools = json.loads(raw)
    swapped_tools["toolIdentities"]["git"], swapped_tools["toolIdentities"]["runner"] = (
        swapped_tools["toolIdentities"]["runner"], swapped_tools["toolIdentities"]["git"]
    )
    mutations.append(swapped_tools)
    swapped_ids = json.loads(raw)
    swapped_ids["invocations"][0]["selectedTestIds"], swapped_ids["invocations"][1]["selectedTestIds"] = (
        swapped_ids["invocations"][1]["selectedTestIds"], swapped_ids["invocations"][0]["selectedTestIds"]
    )
    mutations.append(swapped_ids)
    impossible_duration = json.loads(raw)
    for invocation in impossible_duration["invocations"]:
        invocation["durationMilliseconds"] = 10
    impossible_duration["durationMilliseconds"] = 0
    mutations.append(impossible_duration)
    raw_secret = json.loads(raw)
    raw_secret["invocations"][0]["stdout"] = "ghp_0123456789abcdefghijklmnopqrstuvwxyzAB"
    mutations.append(raw_secret)

    for changed in mutations:
        ref = ContentAddressedStore(acceptance).put(
            json.dumps(changed, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        )
        verification = E2SuiteVerifier("e2.fixture", suite).verify(
            _pointer(commit, ref), _profile(commit), context
        )
        assert verification.valid is False
        assert verification.error_code == "POINTER_INVALID"


def test_e2_bounds_git_command_capture_and_machine_report_reads(tmp_path, monkeypatch):
    product, commit = _product_commit(tmp_path)
    for _ in range(20):
        _run(["git", "config", "--add", "core.ignorecase", "true"], product)
    acceptance = tmp_path / "acceptance"
    acceptance.mkdir()
    monkeypatch.chdir(product)
    monkeypatch.setattr("trusted_evidence.verifiers.e2_suite._MAX_GIT_CAPTURE", 64, raising=False)

    git_result = SuiteRunner().run(
        _suite(acceptance), f"git://product@{commit}", _context(acceptance, commit)
    )

    assert git_result.error_code == "SOURCE_REF_UNREACHABLE"
    monkeypatch.setattr("trusted_evidence.verifiers.e2_suite._MAX_GIT_CAPTURE", 64 * 1024)

    report = {
        "exitcode": 0,
        "summary": {"total": 1, "collected": 1, "passed": 1, "failed": 0, "skipped": 0},
        "tests": [{"nodeid": "tests/test_contract.py::test_policy", "outcome": "passed"}],
        "padding": "x" * 512,
    }
    report_product, report_commit = _product_commit(
        tmp_path / "report",
        runner_body="fs.copyFileSync('oversized.json', '.report.json');",
        files={"oversized.json": json.dumps(report)},
    )
    monkeypatch.chdir(report_product)
    monkeypatch.setattr("trusted_evidence.verifiers.e2_suite._MAX_REPORT_BYTES", 128, raising=False)

    report_result = SuiteRunner().run(
        _suite(acceptance), f"git://product@{report_commit}", _context(acceptance, report_commit)
    )

    assert report_result.error_code == "TEST_SELECTION_MISSING"
