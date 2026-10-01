#!/usr/bin/env python3
"""只读校验统一混剪、画布与生成验收资产。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_MATRIX_COUNTS = {"A": 18, "R": 40, "C": 35, "G": 27, "X": 18}
EXPECTED_STATUSES = ("通过", "失败", "阻塞", "明确不适用")
EXPECTED_EVIDENCE_LEVELS = {"E1", "E2", "E3", "E4"}
EXPECTED_ZERO_TOLERANCE = ("黑边", "越界时码", "错误素材身份", "未披露外发", "未经批准交付")
EXPECTED_FROZEN_AT = "2026-09-30"
EXPECTED_FROZEN_NOT_APPLICABLE_IDS: frozenset[str] = frozenset()
TRUTH_SOURCE_REF = re.compile(r"^(project|asset|run|review|delivery|source|provider-job)://[^\s]+$")
# 票据 01 不为正式矩阵注册占位验证器。后续票据只有在实现会读取既有
# 源码、测试运行或业务真相源的验证函数后，才能解锁对应 E1–E4。
PRODUCTION_EVIDENCE_VERIFIERS: dict[str, Any] = {}
EVIDENCE_KINDS = {
    "E1": {"source-license-mapping"},
    "E2": {"automated-behavior-test"},
    "E3": {"real-platform-run", "real-provider-run", "real-media-run"},
    "E4": {
        "manual-visual-acceptance",
        "manual-content-acceptance",
        "manual-rights-acceptance",
        "manual-delivery-acceptance",
    },
}
SELF_CERTIFYING_KINDS = {"acceptance-status", "checklist", "issue-status", "specification"}
SELF_CERTIFYING_FILES = {
    "matrix-contract.json",
    "evidence-catalog.json",
    "ticket-coverage.json",
    "golden-records.json",
    "validate_acceptance.py",
    "test_acceptance_contract.py",
}
EXPECTED_GOLDEN_COVERAGE = {
    "GL01": ["X01", "X02", "X03", "X04", "X05", "X06", "X16", "X17", "X18"],
    "GL02": ["X07", "X08", "X09", "X10", "X16", "X17", "X18"],
    "GL03": ["X11", "X12", "X13", "X14", "X15", "X16", "X17", "X18"],
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_controlled_e2_fixture(entry: dict[str, Any], root: Path) -> list[str]:
    """只服务 validate-sample 的四状态夹具，不能用于正式矩阵。"""
    expected_artifact = {
        "fixture": True,
        "purpose": "仅证明验收状态校验规则，不作为任何 A/R/C/G/X 项的通过证据。",
    }
    artifact = root / str(entry.get("artifactPath", ""))
    expected_entry = (
        entry.get("id") == "sample-e2"
        and entry.get("level") == "E2"
        and entry.get("supports") == ["sample-pass"]
        and artifact.resolve() == (root / "fixtures/controlled-e2-proof.json").resolve()
    )
    if expected_entry and artifact.is_file() and load_json(artifact) == expected_artifact:
        return []
    return ["受控 E2 夹具与冻结样例不一致。"]


def expand_ranges(ranges: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in ranges:
        if "-" not in value:
            expanded.append(value)
            continue
        start, end = value.split("-", 1)
        if start[0] != end[0]:
            raise ValueError(f"跨矩阵范围不合法：{value}")
        expanded.extend(f"{start[0]}{number:02d}" for number in range(int(start[1:]), int(end[1:]) + 1))
    return expanded


def resolve_evidence_requirements(contract: dict[str, Any], ids: set[str]) -> tuple[dict[str, set[str]], list[str]]:
    requirements: dict[str, set[str]] = {}
    errors: list[str] = []
    for rule in contract.get("minimumEvidenceRules", []):
        levels = set(rule.get("levels", []))
        if not levels or not levels <= EXPECTED_EVIDENCE_LEVELS:
            errors.append(f"非法证据等级组合：{sorted(levels)}")
        try:
            rule_ids = expand_ranges(rule.get("ranges", []))
        except (TypeError, ValueError) as error:
            errors.append(str(error))
            continue
        for item_id in rule_ids:
            if item_id in requirements:
                errors.append(f"{item_id} 重复声明最低证据。")
            requirements[item_id] = levels
    missing = sorted(ids - requirements.keys())
    extra = sorted(requirements.keys() - ids)
    if missing or extra:
        errors.append(f"最低证据映射错误：missing={missing}, extra={extra}")
    return requirements, errors


def evidence_index(
    evidence: list[dict[str, Any]],
    root: Path,
    valid_record_ids: set[str],
    trusted_verifiers: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for entry in evidence:
        evidence_id = entry.get("id")
        if not evidence_id or evidence_id in indexed:
            errors.append(f"证据编号缺失或重复：{evidence_id}")
            continue
        indexed[evidence_id] = entry
        level = entry.get("level")
        kind = entry.get("kind")
        if level not in EXPECTED_EVIDENCE_LEVELS:
            errors.append(f"{evidence_id} 使用非法证据等级。")
        elif kind not in EVIDENCE_KINDS[level]:
            errors.append(f"{evidence_id} 的 {level} 证据类型非法：{kind}。")
        if kind in SELF_CERTIFYING_KINDS:
            errors.append(f"{evidence_id} 使用循环自证类型 {kind}。")
        artifact_value = str(entry.get("artifactPath", ""))
        artifact = (root / artifact_value).resolve()
        try:
            artifact.relative_to(root.resolve())
        except ValueError:
            errors.append(f"{evidence_id} 的证据文件必须位于验收目录内。")
            continue
        if artifact.name in SELF_CERTIFYING_FILES:
            errors.append(f"{evidence_id} 引用了验收定义；验收定义不能作为自身通过证据。")
        if not artifact_value or not artifact.is_file():
            errors.append(f"{evidence_id} 的证据文件不存在：{entry.get('artifactPath')}")
        supports = set(entry.get("supports", []))
        unknown = sorted(supports - valid_record_ids)
        if not supports or unknown:
            errors.append(f"{evidence_id} 的支持对象非法：{unknown}")
        level_contracts = {
            "E1": ("sourceMapping", ("repository", "commit", "sourceFiles", "license", "reuseType"), "源码与许可映射"),
            "E2": ("automatedTest", ("command", "observedAt", "exitCode", "testIds"), "自动化测试真相源"),
            "E3": ("realRun", ("runId", "projectId", "inputSnapshotRef", "outputRefs", "observedAt", "environment"), "真实运行真相源"),
            "E4": ("manualAcceptance", ("reviewer", "reviewedAt", "decision", "subjectRefs", "criteria"), "人工验收真相源"),
        }
        if level in level_contracts:
            container, fields, label = level_contracts[level]
            metadata = entry.get(container, {})
            if any(metadata.get(field) in (None, "", []) for field in fields):
                errors.append(f"{evidence_id} 的 {level} 缺少{label}字段。")
            if level == "E2" and metadata.get("exitCode") != 0:
                errors.append(f"{evidence_id} 的 E2 自动化测试未成功退出。")
        verifier = trusted_verifiers.get(str(entry.get("verifierId", "")))
        if verifier is None:
            if level in {"E3", "E4"}:
                errors.append(f"{evidence_id} 的 E3/E4 证据没有受信真相源验证器。")
            elif level in EXPECTED_EVIDENCE_LEVELS:
                errors.append(f"{evidence_id} 的 {level} 证据没有受信验证器。")
        else:
            errors.extend(verifier(entry, root))
        if level in {"E3", "E4"}:
            truth_source_refs = entry.get("truthSourceRefs", [])
            if not truth_source_refs or any(not TRUTH_SOURCE_REF.fullmatch(str(ref)) for ref in truth_source_refs):
                errors.append(f"{evidence_id} 缺少合法的既有真相源引用。")
            if level == "E3":
                real_run = entry.get("realRun", {})
                required_refs = {
                    f"project://{real_run.get('projectId', '')}",
                    f"run://{real_run.get('runId', '')}",
                    str(real_run.get("inputSnapshotRef", "")),
                    *map(str, real_run.get("outputRefs", [])),
                }
                if not required_refs <= set(map(str, truth_source_refs)):
                    errors.append(f"{evidence_id} 的真实运行身份未完整链接到真相源。")
            else:
                subject_refs = set(map(str, entry.get("manualAcceptance", {}).get("subjectRefs", [])))
                if not subject_refs <= set(map(str, truth_source_refs)) or not any(
                    str(ref).startswith("review://") for ref in truth_source_refs
                ):
                    errors.append(f"{evidence_id} 的人工验收未链接审核记录或验收对象。")
    return indexed, errors


def validate_status_records(
    records: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    root: Path,
    allowed_statuses: list[str],
    required_by_id: dict[str, set[str]],
    frozen_at: str,
    frozen_not_applicable_ids: set[str],
    trusted_verifiers: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    allowed = set(allowed_statuses)
    if allowed != set(EXPECTED_STATUSES):
        errors.append(f"合法状态必须恰为 {list(EXPECTED_STATUSES)}。")
    record_ids = {str(record.get("id")) for record in records}
    unknown_frozen_na = sorted(frozen_not_applicable_ids - record_ids)
    if unknown_frozen_na:
        errors.append(f"冻结时不适用清单引用未知项：{unknown_frozen_na}")
    indexed_evidence, evidence_errors = evidence_index(evidence, root, record_ids, trusted_verifiers)
    errors.extend(evidence_errors)
    status_counts = {status: 0 for status in EXPECTED_STATUSES}

    for record in records:
        record_id = str(record.get("id"))
        status = record.get("status")
        if status not in allowed:
            errors.append(f"{record_id} 使用非法状态：{status}")
            continue
        status_counts[status] += 1
        evidence_refs = record.get("evidenceRefs", [])
        missing_refs = [reference for reference in evidence_refs if reference not in indexed_evidence]
        if missing_refs:
            errors.append(f"{record_id} 引用了不存在的证据：{missing_refs}")
        for reference in evidence_refs:
            if reference in indexed_evidence and record_id not in indexed_evidence[reference].get("supports", []):
                errors.append(f"{reference} 未声明支持 {record_id}。")
        violations = record.get("zeroToleranceViolations", [])
        unknown_violations = sorted(set(violations) - set(EXPECTED_ZERO_TOLERANCE))
        if unknown_violations:
            errors.append(f"{record_id} 声明未知零容忍错误：{unknown_violations}")
        if violations and status != "失败":
            errors.append(f"{record_id} 存在零容忍错误时必须标记失败。")
        if status == "通过":
            actual_levels = {indexed_evidence[reference]["level"] for reference in evidence_refs if reference in indexed_evidence}
            missing_levels = sorted(required_by_id.get(record_id, set()) - actual_levels)
            if missing_levels:
                errors.append(f"{record_id} 缺少通过所需证据：{missing_levels}")
            if violations:
                errors.append(f"{record_id} 存在零容忍错误，不能标记通过。")
        elif status == "失败" and not str(record.get("gap", "")).strip():
            errors.append(f"{record_id} 的失败状态缺少未闭合差异。")
        elif status == "阻塞":
            if not str(record.get("blockedReason", "")).strip() or not str(record.get("gap", "")).strip():
                errors.append(f"{record_id} 的阻塞状态缺少阻碍或未闭合差异。")
        elif status == "明确不适用":
            decision = record.get("frozenDecision", {})
            required_fields = ("decidedAt", "reason", "approvedBy", "alternativeCoverage")
            if any(not decision.get(field) for field in required_fields):
                errors.append(f"{record_id} 的明确不适用缺少冻结理由、批准者或替代覆盖。")
            if record_id not in frozen_not_applicable_ids:
                errors.append(f"{record_id} 不在冻结时的不适用清单中。")
            if decision.get("decidedAt") != frozen_at:
                errors.append(f"{record_id} 的不适用决定时间必须等于合同冻结时间 {frozen_at}。")

    return {"valid": not errors, "errors": errors, "statusCounts": status_counts}


def validate_ticket_coverage(coverage: dict[str, Any], root: Path, ids: set[str]) -> dict[str, Any]:
    errors: list[str] = []
    tickets = coverage.get("tickets", [])
    expected_tickets = {f"{number:02d}" for number in range(2, 34)}
    ticket_ids = [str(ticket.get("ticket", "")) for ticket in tickets]
    if set(ticket_ids) != expected_tickets or len(ticket_ids) != len(set(ticket_ids)):
        errors.append("票据覆盖必须恰好包含 02–33 且不能重复。")

    primary_owners: dict[str, str] = {}
    audit_tickets: list[str] = []
    for ticket in tickets:
        ticket_id = str(ticket.get("ticket", ""))
        issue_path = root / str(ticket.get("issue", ""))
        if not ticket.get("issue") or not issue_path.is_file():
            errors.append(f"票据 {ticket_id} 的来源文件不存在：{ticket.get('issue')}")
        for item_id in expand_ranges(ticket.get("primaryRanges", [])):
            if item_id not in ids:
                errors.append(f"票据 {ticket_id} 主责未知矩阵项 {item_id}。")
            elif item_id in primary_owners:
                errors.append(f"{item_id} 被票据 {primary_owners[item_id]} 与 {ticket_id} 重复主责宣称。")
            else:
                primary_owners[item_id] = ticket_id
        supporting = expand_ranges(ticket.get("supportRanges", []))
        unknown_support = sorted(set(supporting) - ids)
        if unknown_support:
            errors.append(f"票据 {ticket_id} 支持未知矩阵项：{unknown_support}")
        audited = expand_ranges(ticket.get("auditRanges", []))
        unknown_audit = sorted(set(audited) - ids)
        if unknown_audit:
            errors.append(f"票据 {ticket_id} 审计未知矩阵项：{unknown_audit}")
        if audited:
            audit_tickets.append(ticket_id)

    missing_primary = sorted(ids - primary_owners.keys())
    if missing_primary:
        errors.append(f"矩阵项缺少唯一主责票据：{missing_primary}")
    return {
        "valid": not errors,
        "errors": errors,
        "ticketCoverageCount": len(ticket_ids),
        "primaryCoverageCount": len(primary_owners),
        "auditTickets": sorted(audit_tickets),
        "primaryOwners": primary_owners,
    }


def validate_golden_records(golden: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    errors: list[str] = []
    records = golden.get("records", [])
    record_ids = [str(record.get("id", "")) for record in records]
    if set(record_ids) != set(EXPECTED_GOLDEN_COVERAGE) or len(record_ids) != len(set(record_ids)):
        errors.append("金标记录必须恰好包含 GL01、GL02、GL03。")
    required_incidents = set(golden.get("recordContract", {}).get("requiredIncidentSlots", []))
    expected_incidents = {"failure", "cancellation", "recovery", "unknownState", "lateResult", "retry", "restart"}
    if required_incidents != expected_incidents:
        errors.append("金标记录缺少失败、取消、恢复、未知、迟到、重试或重启证据槽位。")
    forbidden_state_keys = {"projectState", "assetState", "runState", "reviewState", "deliveryState", "statusSnapshot"}
    coverage: dict[str, list[str]] = {}
    for record in records:
        record_id = str(record.get("id", ""))
        matrix_items = record.get("matrixItems", [])
        coverage[record_id] = matrix_items
        if matrix_items != EXPECTED_GOLDEN_COVERAGE.get(record_id):
            errors.append(f"{record_id} 的 X 矩阵覆盖不正确。")
        if set(matrix_items) - ids:
            errors.append(f"{record_id} 引用未知矩阵项。")
        if record.get("recordStatus") != "模板":
            errors.append(f"{record_id} 在票据 01 中只能是模板。")
        if forbidden_state_keys & record.keys():
            errors.append(f"{record_id} 复制了业务状态字段。")
        identity = record.get("identityRefs", {})
        for field in ("acceptanceRunId", "commonRunId", "projectId", "inputSnapshotRef", "capabilitySnapshotRef"):
            if field not in identity:
                errors.append(f"{record_id} 缺少统一身份字段 {field}。")
        if not record.get("realInputs") or not record.get("checkpoints") or not record.get("manualAcceptance"):
            errors.append(f"{record_id} 缺少真实输入、关键检查点或人工验收槽位。")
        if set(record.get("incidentEvidence", {})) != expected_incidents:
            errors.append(f"{record_id} 的异常证据槽位不完整。")
        if not record.get("realMediaRefs") or not record.get("truthSourceRefs"):
            errors.append(f"{record_id} 缺少真实媒体或既有真相源引用。")
        if not record.get("openGaps"):
            errors.append(f"{record_id} 模板必须保留未闭合差异。")
        for checkpoint in record.get("checkpoints", []):
            levels = set(checkpoint.get("requiredEvidence", []))
            if not {"E3", "E4"} <= levels:
                errors.append(f"{record_id}/{checkpoint.get('id')} 未同时要求 E3 与 E4。")
    return {
        "valid": not errors,
        "errors": errors,
        "goldenRecordCount": len(records),
        "goldenLoopIds": sorted(record_ids),
        "goldenMatrixCoverage": coverage,
    }


def validate_matrix_contract(contract: dict[str, Any], root: Path) -> dict[str, Any]:
    errors: list[str] = []
    if contract.get("frozenAt") != EXPECTED_FROZEN_AT:
        errors.append(f"合同冻结日期不得变更：必须为 {EXPECTED_FROZEN_AT}。")
    if set(contract.get("frozenNotApplicableIds", [])) != set(EXPECTED_FROZEN_NOT_APPLICABLE_IDS):
        errors.append("冻结时不适用基线不得变更；后续受阻项必须继续留在分母。")
    matrices = contract.get("matrices", {})
    actual_counts = {prefix: len(matrices.get(prefix, [])) for prefix in EXPECTED_MATRIX_COUNTS}
    ids = [item.get("id") for prefix in EXPECTED_MATRIX_COUNTS for item in matrices.get(prefix, [])]

    if actual_counts != EXPECTED_MATRIX_COUNTS:
        errors.append(f"矩阵数量不符：{actual_counts}")
    if len(ids) != len(set(ids)):
        errors.append("矩阵编号存在重复。")
    for prefix, count in EXPECTED_MATRIX_COUNTS.items():
        expected_ids = {f"{prefix}{number:02d}" for number in range(1, count + 1)}
        actual_ids = {item.get("id") for item in matrices.get(prefix, [])}
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        if missing or extra:
            errors.append(f"{prefix} 矩阵编号错误：missing={missing}, extra={extra}")
        for item in matrices.get(prefix, []):
            if not re.fullmatch(rf"{prefix}\d{{2}}", str(item.get("id", ""))):
                errors.append(f"非法编号：{item.get('id')}")
            if not str(item.get("description", "")).strip():
                errors.append(f"{item.get('id')} 缺少负责人可读说明。")

    all_ids = {str(item_id) for item_id in ids}
    requirements, requirement_errors = resolve_evidence_requirements(contract, all_ids)
    errors.extend(requirement_errors)
    default_status = contract.get("statusPolicy", {}).get("default", {})
    overrides = contract.get("statusPolicy", {}).get("overrides", {})
    records = [{"id": item_id, **default_status, **overrides.get(item_id, {})} for item_id in sorted(all_ids)]
    status_report = validate_status_records(
        records,
        load_json(root / "evidence-catalog.json").get("evidence", []),
        root,
        contract.get("allowedStatuses", []),
        requirements,
        str(contract.get("frozenAt", "")),
        set(contract.get("frozenNotApplicableIds", [])),
        PRODUCTION_EVIDENCE_VERIFIERS,
    )
    errors.extend(status_report["errors"])
    zero_tolerance = contract.get("zeroToleranceErrors", [])
    if tuple(zero_tolerance) != EXPECTED_ZERO_TOLERANCE:
        errors.append(f"零容忍错误集合不完整：{zero_tolerance}")
    zero_tolerance_coverage: set[str] = set()
    for rule in contract.get("zeroToleranceRules", []):
        if rule.get("error") not in EXPECTED_ZERO_TOLERANCE:
            errors.append(f"未知零容忍错误：{rule.get('error')}")
        zero_tolerance_coverage.update(expand_ranges(rule.get("ranges", [])))
    unknown_zero_tolerance_items = sorted(zero_tolerance_coverage - all_ids)
    if unknown_zero_tolerance_items:
        errors.append(f"零容忍规则引用未知矩阵项：{unknown_zero_tolerance_items}")
    coverage_report = validate_ticket_coverage(load_json(root / "ticket-coverage.json"), root, all_ids)
    errors.extend(coverage_report["errors"])
    golden_report = validate_golden_records(load_json(root / "golden-records.json"), all_ids)
    errors.extend(golden_report["errors"])

    return {
        "valid": not errors,
        "errors": errors,
        "matrixCounts": actual_counts,
        "totalItems": len(ids),
        "statusCounts": status_report["statusCounts"],
        "evidenceRequirementCount": len(requirements),
        "minimumEvidenceByItem": {item_id: sorted(levels) for item_id, levels in sorted(requirements.items())},
        "gapCount": sum(1 for record in records if record.get("status") != "通过"),
        "zeroToleranceErrors": zero_tolerance,
        "ticketCoverageCount": coverage_report["ticketCoverageCount"],
        "primaryCoverageCount": coverage_report["primaryCoverageCount"],
        "auditTickets": coverage_report["auditTickets"],
        "goldenRecordCount": golden_report["goldenRecordCount"],
        "goldenLoopIds": golden_report["goldenLoopIds"],
        "goldenMatrixCoverage": golden_report["goldenMatrixCoverage"],
    }


def validate_sample(sample_path: Path, root: Path) -> dict[str, Any]:
    sample = load_json(sample_path)
    records = sample.get("records", [])
    requirements = {
        str(record.get("id")): set(
            sample.get("requiredEvidenceById", {}).get(str(record.get("id")), sample.get("requiredEvidence", []))
        )
        for record in records
    }
    return validate_status_records(
        records,
        sample.get("evidence", []),
        root,
        sample.get("allowedStatuses", []),
        requirements,
        str(sample.get("frozenAt", "")),
        set(sample.get("frozenNotApplicableIds", [])),
        {"controlled-e2-fixture": validate_controlled_e2_fixture},
    )


def build_gap_report(root: Path) -> dict[str, Any]:
    contract = load_json(root / "matrix-contract.json")
    validation = validate_matrix_contract(contract, root)
    if not validation["valid"]:
        return {"valid": False, "errors": validation["errors"], "totalGaps": 0, "byMatrix": {}, "items": []}

    matrix_items = {
        str(item["id"]): item
        for prefix in EXPECTED_MATRIX_COUNTS
        for item in contract["matrices"][prefix]
    }
    all_ids = set(matrix_items)
    requirements, _ = resolve_evidence_requirements(contract, all_ids)
    default_status = contract["statusPolicy"]["default"]
    overrides = contract["statusPolicy"].get("overrides", {})
    coverage = validate_ticket_coverage(load_json(root / "ticket-coverage.json"), root, all_ids)
    zero_tolerance_by_item = {item_id: [] for item_id in all_ids}
    for rule in contract.get("zeroToleranceRules", []):
        for item_id in expand_ranges(rule.get("ranges", [])):
            zero_tolerance_by_item[item_id].append(rule["error"])

    items: list[dict[str, Any]] = []
    for item_id in sorted(all_ids):
        status_record = {**default_status, **overrides.get(item_id, {})}
        if status_record.get("status") == "通过":
            continue
        items.append(
            {
                "id": item_id,
                "description": matrix_items[item_id]["description"],
                "status": status_record["status"],
                "gap": status_record.get("gap", ""),
                "blockedReason": status_record.get("blockedReason"),
                "requiredEvidence": sorted(requirements[item_id]),
                "evidenceRefs": status_record.get("evidenceRefs", []),
                "zeroToleranceErrors": zero_tolerance_by_item[item_id],
                "primaryTicket": coverage["primaryOwners"].get(item_id),
            }
        )
    by_matrix = {
        prefix: sum(1 for item in items if item["id"].startswith(prefix))
        for prefix in EXPECTED_MATRIX_COUNTS
    }
    return {"valid": True, "totalGaps": len(items), "byMatrix": by_matrix, "items": items}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("command", choices=("validate", "validate-sample", "gaps"))
    parser.add_argument("sample", nargs="?")
    args = parser.parse_args()

    if args.command == "validate":
        report = validate_matrix_contract(load_json(args.root / "matrix-contract.json"), args.root)
    elif args.command == "validate-sample":
        if not args.sample:
            parser.error("validate-sample 需要样例路径。")
        report = validate_sample(args.root / args.sample, args.root)
    else:
        report = build_gap_report(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
