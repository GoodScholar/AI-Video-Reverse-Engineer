import json
import shutil
import subprocess
import sys
from pathlib import Path


ACCEPTANCE_ROOT = Path(__file__).resolve().parent
VALIDATOR = ACCEPTANCE_ROOT / "validate_acceptance.py"


def run_validator(*args: str, root: Path = ACCEPTANCE_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), *args],
        capture_output=True,
        check=False,
        text=True,
    )


def test_real_contract_freezes_all_138_unique_matrix_items():
    result = run_validator("validate")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["matrixCounts"] == {
        "A": 18,
        "R": 40,
        "C": 35,
        "G": 27,
        "X": 18,
    }
    assert report["totalItems"] == 138


def test_real_contract_enforces_status_evidence_and_zero_tolerance_rules():
    result = run_validator("validate")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["statusCounts"] == {
        "通过": 0,
        "失败": 0,
        "阻塞": 138,
        "明确不适用": 0,
    }
    assert report["evidenceRequirementCount"] == 138
    assert report["gapCount"] == 138
    assert report["zeroToleranceErrors"] == [
        "黑边",
        "越界时码",
        "错误素材身份",
        "未披露外发",
        "未经批准交付",
    ]
    assert report["minimumEvidenceByItem"]["A18"] == ["E2", "E4"]
    for item_id in [f"R{number:02d}" for number in range(27, 36)]:
        assert report["minimumEvidenceByItem"][item_id] == ["E2", "E3", "E4"]


def test_controlled_sample_proves_all_four_legal_statuses():
    result = run_validator("validate-sample", "fixtures/controlled-status-sample.json")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["statusCounts"] == {
        "通过": 1,
        "失败": 1,
        "阻塞": 1,
        "明确不适用": 1,
    }


def test_every_follow_up_ticket_has_unique_primary_matrix_coverage():
    result = run_validator("validate")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ticketCoverageCount"] == 32
    assert report["primaryCoverageCount"] == 138
    assert report["auditTickets"] == ["33"]


def test_three_golden_record_templates_freeze_the_highest_seams():
    result = run_validator("validate")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["goldenRecordCount"] == 3
    assert report["goldenLoopIds"] == ["GL01", "GL02", "GL03"]
    assert report["goldenMatrixCoverage"] == {
        "GL01": ["X01", "X02", "X03", "X04", "X05", "X06", "X16", "X17", "X18"],
        "GL02": ["X07", "X08", "X09", "X10", "X16", "X17", "X18"],
        "GL03": ["X11", "X12", "X13", "X14", "X15", "X16", "X17", "X18"],
    }


def test_gap_report_is_read_only_and_lists_every_unclosed_item():
    tracked_files = sorted(path for path in ACCEPTANCE_ROOT.rglob("*") if path.is_file())
    before = {path: path.read_bytes() for path in tracked_files}

    result = run_validator("gaps")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["totalGaps"] == 138
    assert report["byMatrix"] == {"A": 18, "R": 40, "C": 35, "G": 27, "X": 18}
    assert {item["status"] for item in report["items"]} == {"阻塞"}
    assert all(item["primaryTicket"] for item in report["items"])
    assert before == {path: path.read_bytes() for path in tracked_files}


def test_evidence_cannot_escape_the_acceptance_root(tmp_path):
    outside = tmp_path / "outside-proof.json"
    outside.write_text("{}", encoding="utf-8")
    root = tmp_path / "acceptance"
    root.mkdir()
    sample = root / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "allowedStatuses": ["通过", "失败", "阻塞", "明确不适用"],
                "requiredEvidence": ["E2"],
                "records": [
                    {
                        "id": "sample-pass",
                        "status": "通过",
                        "evidenceRefs": ["outside"],
                        "zeroToleranceViolations": [],
                    }
                ],
                "evidence": [
                    {
                        "id": "outside",
                        "level": "E2",
                        "kind": "automated-behavior-test",
                        "artifactPath": "../outside-proof.json",
                        "supports": ["sample-pass"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_validator("validate-sample", "sample.json", root=root)

    assert result.returncode == 1
    assert "证据文件必须位于验收目录内" in result.stdout


def test_acceptance_contract_cannot_certify_itself(tmp_path):
    root = tmp_path / "acceptance"
    root.mkdir()
    (root / "matrix-contract.json").write_text("{}", encoding="utf-8")
    sample = root / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "allowedStatuses": ["通过", "失败", "阻塞", "明确不适用"],
                "requiredEvidence": ["E2"],
                "records": [
                    {
                        "id": "sample-pass",
                        "status": "通过",
                        "evidenceRefs": ["self"],
                        "zeroToleranceViolations": [],
                    }
                ],
                "evidence": [
                    {
                        "id": "self",
                        "level": "E2",
                        "kind": "automated-behavior-test",
                        "artifactPath": "matrix-contract.json",
                        "supports": ["sample-pass"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_validator("validate-sample", "sample.json", root=root)

    assert result.returncode == 1
    assert "验收定义不能作为自身通过证据" in result.stdout


def test_invalid_status_claims_are_rejected(tmp_path):
    root = tmp_path / "acceptance"
    root.mkdir()
    sample = root / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "allowedStatuses": ["通过", "失败", "阻塞", "明确不适用"],
                "requiredEvidence": ["E2"],
                "records": [
                    {"id": "pass-without-evidence", "status": "通过", "evidenceRefs": [], "zeroToleranceViolations": []},
                    {"id": "pass-with-zero-tolerance", "status": "通过", "evidenceRefs": [], "zeroToleranceViolations": ["黑边"]},
                    {"id": "failure-without-gap", "status": "失败", "evidenceRefs": [], "gap": "", "zeroToleranceViolations": []},
                    {"id": "blocked-without-reason", "status": "阻塞", "evidenceRefs": [], "gap": "仍有差异", "zeroToleranceViolations": []},
                    {"id": "late-na", "status": "明确不适用", "evidenceRefs": [], "gap": "", "zeroToleranceViolations": []},
                    {"id": "illegal", "status": "已完成", "evidenceRefs": [], "zeroToleranceViolations": []},
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_validator("validate-sample", "sample.json", root=root)

    assert result.returncode == 1
    assert "缺少通过所需证据" in result.stdout
    assert "存在零容忍错误，不能标记通过" in result.stdout
    assert "失败状态缺少未闭合差异" in result.stdout
    assert "阻塞状态缺少阻碍或未闭合差异" in result.stdout
    assert "明确不适用缺少冻结理由、批准者或替代覆盖" in result.stdout
    assert "使用非法状态" in result.stdout


def test_zero_tolerance_violation_forces_failure_status(tmp_path):
    root = tmp_path / "acceptance"
    root.mkdir()
    sample = root / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "frozenAt": "2026-09-30",
                "frozenNotApplicableIds": [],
                "allowedStatuses": ["通过", "失败", "阻塞", "明确不适用"],
                "requiredEvidence": [],
                "records": [
                    {
                        "id": "blocked-with-zero-tolerance",
                        "status": "阻塞",
                        "evidenceRefs": [],
                        "blockedReason": "等待修复。",
                        "gap": "存在黑边。",
                        "zeroToleranceViolations": ["黑边"],
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_validator("validate-sample", "sample.json", root=root)

    assert result.returncode == 1
    assert "存在零容忍错误时必须标记失败" in result.stdout


def test_not_applicable_must_be_in_the_frozen_denominator_decision(tmp_path):
    root = tmp_path / "acceptance"
    root.mkdir()
    sample = root / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "frozenAt": "2026-09-30",
                "frozenNotApplicableIds": [],
                "allowedStatuses": ["通过", "失败", "阻塞", "明确不适用"],
                "requiredEvidence": [],
                "records": [
                    {
                        "id": "late-na",
                        "status": "明确不适用",
                        "evidenceRefs": [],
                        "zeroToleranceViolations": [],
                        "frozenDecision": {
                            "decidedAt": "2026-10-01",
                            "reason": "运行受阻后排除。",
                            "approvedBy": "后续票据",
                            "alternativeCoverage": ["another-item"],
                        },
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_validator("validate-sample", "sample.json", root=root)

    assert result.returncode == 1
    assert "不在冻结时的不适用清单中" in result.stdout
    assert "不适用决定时间必须等于合同冻结时间" in result.stdout


def test_e3_and_e4_require_level_specific_truth_source_metadata(tmp_path):
    root = tmp_path / "acceptance"
    root.mkdir()
    (root / "declared-proof.json").write_text("{}", encoding="utf-8")
    sample = root / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "frozenAt": "2026-09-30",
                "frozenNotApplicableIds": [],
                "allowedStatuses": ["通过", "失败", "阻塞", "明确不适用"],
                "requiredEvidenceById": {
                    "fake-e3": ["E3"],
                    "fake-e4": ["E4"],
                },
                "records": [
                    {"id": "fake-e3", "status": "通过", "evidenceRefs": ["e3"], "zeroToleranceViolations": []},
                    {"id": "fake-e4", "status": "通过", "evidenceRefs": ["e4"], "zeroToleranceViolations": []},
                ],
                "evidence": [
                    {
                        "id": "e3",
                        "level": "E3",
                        "kind": "real-provider-run",
                        "artifactPath": "declared-proof.json",
                        "supports": ["fake-e3"],
                    },
                    {
                        "id": "e4",
                        "level": "E4",
                        "kind": "manual-visual-acceptance",
                        "artifactPath": "declared-proof.json",
                        "supports": ["fake-e4"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_validator("validate-sample", "sample.json", root=root)

    assert result.returncode == 1
    assert "E3 缺少真实运行真相源字段" in result.stdout
    assert "E4 缺少人工验收真相源字段" in result.stdout


def test_production_not_applicable_baseline_cannot_be_backdated(tmp_path):
    root = tmp_path / "acceptance"
    shutil.copytree(ACCEPTANCE_ROOT, root)
    contract_path = root / "matrix-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["frozenNotApplicableIds"] = ["A01"]
    contract["statusPolicy"]["overrides"]["A01"] = {
        "status": "明确不适用",
        "evidenceRefs": [],
        "gap": "",
        "zeroToleranceViolations": [],
        "frozenDecision": {
            "decidedAt": contract["frozenAt"],
            "reason": "事后回填。",
            "approvedBy": "后续票据",
            "alternativeCoverage": ["A02"],
        },
    }
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")

    result = run_validator("validate", root=root)

    assert result.returncode == 1
    assert "冻结时不适用基线不得变更" in result.stdout


def test_structurally_valid_e3_e4_stay_blocked_without_trusted_truth_source_verifier(tmp_path):
    root = tmp_path / "acceptance"
    root.mkdir()
    real_run = {
        "runId": "run-1",
        "projectId": "project-1",
        "inputSnapshotRef": "run://run-1/input-snapshot",
        "outputRefs": ["asset://asset-1/version-1"],
        "observedAt": "2026-10-01T15:00:00+08:00",
        "environment": "local-real-provider",
    }
    manual_acceptance = {
        "reviewer": "reviewer-1",
        "reviewedAt": "2026-10-01T15:10:00+08:00",
        "decision": "accepted",
        "subjectRefs": ["asset://asset-1/version-1"],
        "criteria": ["无黑边", "素材身份正确"],
    }
    entries = [
        {
            "id": "e3",
            "level": "E3",
            "kind": "real-provider-run",
            "artifactPath": "e3.json",
            "supports": ["real-run"],
            "truthSourceRefs": [
                "project://project-1",
                "run://run-1",
                "run://run-1/input-snapshot",
                "asset://asset-1/version-1",
            ],
            "realRun": real_run,
        },
        {
            "id": "e4",
            "level": "E4",
            "kind": "manual-visual-acceptance",
            "artifactPath": "e4.json",
            "supports": ["manual-review"],
            "truthSourceRefs": ["review://review-1", "asset://asset-1/version-1"],
            "manualAcceptance": manual_acceptance,
        },
    ]
    for entry in entries:
        (root / entry["artifactPath"]).write_text("existing evidence", encoding="utf-8")
    sample = root / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "frozenAt": "2026-09-30",
                "frozenNotApplicableIds": [],
                "allowedStatuses": ["通过", "失败", "阻塞", "明确不适用"],
                "requiredEvidenceById": {"real-run": ["E3"], "manual-review": ["E4"]},
                "records": [
                    {"id": "real-run", "status": "通过", "evidenceRefs": ["e3"], "zeroToleranceViolations": []},
                    {"id": "manual-review", "status": "通过", "evidenceRefs": ["e4"], "zeroToleranceViolations": []},
                ],
                "evidence": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_validator("validate-sample", "sample.json", root=root)

    assert result.returncode == 1
    assert "e3 的 E3/E4 证据没有受信真相源验证器" in result.stdout
    assert "e4 的 E3/E4 证据没有受信真相源验证器" in result.stdout


def test_production_e2_cannot_pass_with_self_reported_test_result(tmp_path):
    root = tmp_path / "acceptance"
    shutil.copytree(ACCEPTANCE_ROOT, root)
    artifact = root / "fake-e2.json"
    artifact.write_text('{"exitCode": 0}', encoding="utf-8")
    evidence_path = root / "evidence-catalog.json"
    evidence_path.write_text(
        json.dumps(
            {
                "version": 1,
                "evidence": [
                    {
                        "id": "fake-e2",
                        "level": "E2",
                        "kind": "automated-behavior-test",
                        "verifierId": "controlled-e2-fixture",
                        "artifactPath": "fake-e2.json",
                        "supports": ["R01"],
                        "automatedTest": {
                            "command": "pytest fake_test.py",
                            "observedAt": "2026-10-01",
                            "exitCode": 0,
                            "testIds": ["fake_test"],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    contract_path = root / "matrix-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["statusPolicy"]["overrides"]["R01"] = {
        "status": "通过",
        "evidenceRefs": ["fake-e2"],
        "gap": "",
        "zeroToleranceViolations": [],
    }
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")

    result = run_validator("validate", root=root)

    assert result.returncode == 1
    assert "fake-e2 的 E2 证据没有受信验证器" in result.stdout
