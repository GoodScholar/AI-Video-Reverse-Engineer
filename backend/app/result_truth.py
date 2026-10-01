"""Immutable generated-result records and explicit text adoption commands."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


_TARGETS = {
    "canvas_text": "canvasTexts",
    "script": "scripts",
    "remix_plan": "remixPlans",
}


def completed_text_result(run: dict[str, Any], *, result_id: str, text: str) -> dict[str, Any]:
    """Return a completed text run value without mutating a domain object."""
    if not isinstance(run.get("id"), str) or not run["id"]:
        raise ValueError("文本运行标识无效")
    if not isinstance(result_id, str) or not result_id or not isinstance(text, str):
        raise ValueError("文本结果无效")
    completed = deepcopy(run)
    completed["status"] = "completed"
    items = completed.setdefault("outputItems", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("文本运行输出无效")
    if any(item.get("resultId") == result_id for item in items):
        raise ValueError("文本结果标识重复")
    items.append({"resultId": result_id, "status": "completed", "text": text})
    return completed


def adopt_text_result(
    run: dict[str, Any], *, result_id: str, target: str, domains: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Explicitly materialize an immutable text output as one domain version."""
    collection = _TARGETS.get(target)
    if collection is None:
        raise ValueError("文本采用目标无效")
    run_id = run.get("id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("文本运行标识无效")
    item = _text_item(run, result_id)
    if item is None or item.get("status", "completed") != "completed" or not isinstance(item.get("text"), str):
        raise ValueError("文本结果不可采用")
    versions = domains.setdefault(collection, [])
    if not isinstance(versions, list):
        raise ValueError("文本领域版本无效")
    version_id = "text-" + hashlib.sha256(
        json.dumps([run_id, result_id, target], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    existing = next((value for value in versions if value.get("versionId") == version_id), None)
    expected = {
        "versionId": version_id,
        "sourceRunId": run_id,
        "sourceResultId": result_id,
        "text": item["text"],
    }
    if existing is not None:
        if existing != expected:
            raise ValueError("文本采用版本冲突")
        return existing
    versions.append(expected)
    return expected


def _text_item(run: dict[str, Any], result_id: str) -> dict[str, Any] | None:
    items = run.get("outputItems")
    if isinstance(items, list):
        return next((item for item in items if isinstance(item, dict) and item.get("resultId") == result_id), None)
    # Legacy text runners commonly stored a result list with ``id`` or one
    # result object. Read those shapes without rewriting their historical run.
    legacy = run.get("results")
    if isinstance(legacy, list):
        return next((item for item in legacy if isinstance(item, dict) and item.get("id", item.get("resultId")) == result_id), None)
    result = run.get("result")
    if isinstance(result, dict) and result.get("id", result.get("resultId")) == result_id:
        return result
    return None
