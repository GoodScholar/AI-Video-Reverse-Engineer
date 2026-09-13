import json
import math
import re
from typing import Union

from pydantic import ValidationError

from .analysis_prompt import repair_prompt
from .analysis_providers.base import AnalysisProvider, ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult
from .semantic_analysis import StructuredVisualAnalysis, validate_analysis_for_media


MAX_RESPONSE_CHARS = 256_000
MAX_RESPONSE_DEPTH = 64
MAX_REPAIR_CANDIDATE_CHARS = 16_000
MAX_REPAIR_CANDIDATE_BYTES = 32_000
MAX_REPAIR_ERRORS = 8
MAX_REPAIR_JSON_DEPTH = 32
MAX_REPAIR_JSON_NODES = 64
MAX_REPAIR_JSON_STRING_CHARS = 4_096
_SCHEMA_LOCATION_SEGMENTS = frozenset({
    "version", "observedFacts", "staticVisual", "temporal",
    "generationSuggestions", "subject", "scene", "composition", "viewpoint",
    "lighting", "color", "visualStyle", "subjectMotion", "environmentalMotion",
    "cameraMotion", "rhythm", "suggestedDuration", "audio",
})
_DATA_URL = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_BEARER = re.compile(r"\bbearer\s+[^\s\"\\]+", re.IGNORECASE)
_SK_CREDENTIAL = re.compile(r"\bsk-[A-Za-z0-9_-]+", re.IGNORECASE)
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s\"\\/]+(?:/[^\s\"\\/]+)*)")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s\"\\]+[\\/]?)*")
_REPAIR_CANDIDATE_UNAVAILABLE = {"candidate": "<unavailable>"}


class _ResponseBoundaryError(ValueError):
    pass


def _raw_text(value: Union[ProviderResult, str]) -> str:
    if isinstance(value, ProviderResult):
        return value.rawText
    if isinstance(value, str):
        return value
    raise TypeError("供应商响应必须是文本")


def _validation_error_summary(error: Exception) -> str:
    if isinstance(error, ValidationError):
        items = [
            {
                "location": _safe_location(item["loc"]),
                "type": item["type"],
                "message": _redact(item["msg"]),
            }
            for item in error.errors(include_input=False)[:MAX_REPAIR_ERRORS]
        ]
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return json.dumps([{"type": "invalid_json", "message": "响应不是有效 JSON"}], ensure_ascii=False)


def _safe_location(parts: object) -> str:
    if not isinstance(parts, tuple) or not parts or any(
        not isinstance(part, str) or part not in _SCHEMA_LOCATION_SEGMENTS
        for part in parts
    ):
        return "<unknown>"
    return ".".join(parts)


def _redact(value: str) -> str:
    value = _DATA_URL.sub("<redacted-image>", value)
    value = _BEARER.sub("Bearer <redacted>", value)
    value = _SK_CREDENTIAL.sub("<redacted>", value)
    value = _WINDOWS_PATH.sub("<redacted-path>", value)
    return _POSIX_PATH.sub("<redacted-path>", value)


def _json_depth_exceeds_limit(value: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "{[":
            depth += 1
            if depth > MAX_RESPONSE_DEPTH:
                return True
        elif character in "}]":
            depth -= 1
    return False


def _assert_response_boundary(raw_text: str) -> None:
    if len(raw_text) > MAX_RESPONSE_CHARS or _json_depth_exceeds_limit(raw_text):
        raise _ResponseBoundaryError("供应商响应超过安全边界")


def _safe_repair_candidate(raw_text: str) -> str:
    """Return a valid, decoded-and-redacted candidate or a fixed placeholder.

    Raw provider text is never re-sent when it cannot be parsed as JSON: its
    escape sequences may conceal credentials or paths that a text regex cannot
    reliably identify.  The same placeholder is used for parsed candidates
    that exceed traversal/resource limits.
    """

    try:
        parsed = json.loads(raw_text)
        cleaned = _clean_repair_json(parsed, depth=0, nodes=[0])
        candidate = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if (
            len(candidate) > MAX_REPAIR_CANDIDATE_CHARS
            or len(candidate.encode("utf-8")) > MAX_REPAIR_CANDIDATE_BYTES
        ):
            raise _ResponseBoundaryError("供应商响应超过修复边界")
        return candidate
    except (ValueError, TypeError, RecursionError, _ResponseBoundaryError):
        return json.dumps(_REPAIR_CANDIDATE_UNAVAILABLE, ensure_ascii=False, separators=(",", ":"))


def _clean_repair_json(value, *, depth: int, nodes: list):
    if depth > MAX_REPAIR_JSON_DEPTH:
        raise _ResponseBoundaryError("修复候选超过嵌套边界")
    nodes[0] += 1
    if nodes[0] > MAX_REPAIR_JSON_NODES:
        raise _ResponseBoundaryError("修复候选超过节点边界")
    if isinstance(value, str):
        if len(value) > MAX_REPAIR_JSON_STRING_CHARS:
            raise _ResponseBoundaryError("修复候选字符串过长")
        return _redact(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ResponseBoundaryError("修复候选包含非 JSON 数值")
        return value
    if isinstance(value, list):
        return [_clean_repair_json(item, depth=depth + 1, nodes=nodes) for item in value]
    if isinstance(value, dict):
        cleaned = {}
        for key, nested in value.items():
            if not isinstance(key, str) or len(key) > MAX_REPAIR_JSON_STRING_CHARS:
                raise _ResponseBoundaryError("修复候选键无效")
            cleaned[_redact(key)] = _clean_repair_json(nested, depth=depth + 1, nodes=nodes)
        return cleaned
    raise _ResponseBoundaryError("修复候选包含未知 JSON 类型")


def _validate(raw_text: str, request: ProviderRequest) -> StructuredVisualAnalysis:
    if request.analysisInput is None:
        raise ValueError("修复请求不能作为初始响应校验输入")
    _assert_response_boundary(raw_text)
    return validate_analysis_for_media(json.loads(raw_text), request.analysisInput.mediaType)


def validate_or_repair(
    provider: AnalysisProvider,
    raw: Union[ProviderResult, str],
    request: ProviderRequest,
) -> StructuredVisualAnalysis:
    """Validate a response, then permit exactly one image-free retry."""

    if request.isRepair:
        raise ValueError("修复请求不能作为初始请求。")
    raw_text = _raw_text(raw)
    try:
        return _validate(raw_text, request)
    except _ResponseBoundaryError:
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None
    except (ValueError, TypeError, ValidationError, json.JSONDecodeError, RecursionError) as error:
        try:
            candidate = _safe_repair_candidate(raw_text)
        except _ResponseBoundaryError:
            raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None
        repair = request.repair(
            repair_prompt(
                candidate,
                _validation_error_summary(error),
                request.analysisInput.mediaType,
            )
        )
    try:
        repaired = provider.analyze(repair)
        return _validate(_raw_text(repaired), request)
    except (ProviderAnalysisError, ValueError, TypeError, ValidationError, json.JSONDecodeError, RecursionError):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None


__all__ = ["validate_or_repair"]
