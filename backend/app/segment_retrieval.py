"""Local visual retrieval and explicitly disclosed cloud reranking for source segments."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from PIL import Image, ImageDraw, ImageOps, ImageStat
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .project_assets import AssetFileUnavailableError, ProjectAssets
from .reference_video import validate_storage_id
from .source_segment_index import KEYFRAME_MODEL_VERSION, SEGMENT_ALGORITHM_VERSION, source_fingerprint
from .video_toolkit import safe_child


RETRIEVAL_VERSION = "local-visual-retrieval-v1"
DOUBAO_RERANK_MODEL = "doubao-seed-2.1-pro"
SIGLIP2_MODEL = "google/siglip2-base-patch16-224"
SIGLIP2_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
_RERANK_CANDIDATE_FIELDS = (
    "candidateId", "assetId", "inPointSeconds", "outPointSeconds", "keyframeTimeSeconds",
)


class VisualVectorizer(Protocol):
    version: str

    def embed_text(self, query: str) -> list[float]: ...

    def embed_image(self, path: Path) -> list[float]: ...


class VisualVectorizerUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class RerankProviderSnapshot:
    provider: Literal["doubao"]
    model: str
    configuration_revision: str


@dataclass(frozen=True)
class SegmentRerankRequest:
    query: str
    candidates: list[dict[str, Any]]
    prompt: str
    analysis_context: str
    configuration_revision: str
    contact_sheet_bytes: bytes
    contact_sheet_width: int
    contact_sheet_height: int
    response_schema: dict[str, Any]


class SegmentReranker(Protocol):
    def snapshot(self) -> RerankProviderSnapshot: ...

    def rerank(self, request: SegmentRerankRequest) -> str: ...


class SegmentRerankError(Exception):
    def __init__(self, code: str, message: str, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)

    @property
    def status_code(self) -> int:
        if self.code == "timeout":
            return 504
        if self.code == "provider_unconfigured":
            return 422
        return 502


class AnalysisProviderSegmentReranker:
    """Use the verified analysis-provider boundary without exposing credentials."""

    def __init__(self, configurations: Any):
        self._configurations = configurations

    def snapshot(self) -> RerankProviderSnapshot:
        from .analysis_settings import AnalysisProviderUnconfiguredError

        try:
            setting = self._configurations.require_configured(
                "doubao", DOUBAO_RERANK_MODEL, require_verified=True,
            )
        except AnalysisProviderUnconfiguredError as error:
            raise SegmentRerankError(
                "provider_unconfigured", "请先配置并验证豆包视觉复排服务。", False,
            ) from error
        if not setting.configurationRevision:
            raise SegmentRerankError(
                "provider_unconfigured", "请先配置并验证豆包视觉复排服务。", False,
            )
        return RerankProviderSnapshot(
            provider="doubao",
            model=setting.model,
            configuration_revision=setting.configurationRevision,
        )

    def rerank(self, request: SegmentRerankRequest) -> str:
        from .analysis_input import ImageAnalysisInput
        from .analysis_prompt import analysis_input_context
        from .analysis_providers.base import ProviderAnalysisError, ProviderRequest
        from .analysis_settings import (
            AnalysisProviderConfigurationChangedError,
            AnalysisProviderUnconfiguredError,
        )

        analysis_input = ImageAnalysisInput(
            analysisProxyBytes=request.contact_sheet_bytes,
            width=request.contact_sheet_width,
            height=request.contact_sheet_height,
            aspectRatio=request.contact_sheet_width / request.contact_sheet_height,
        )
        if analysis_input_context(analysis_input) != request.analysis_context:
            raise SegmentRerankError(
                "rerank_disclosure_stale", "豆包发送内容已变化，请重新核对。", True,
            )
        try:
            return self._configurations.use_provider(
                provider="doubao",
                model=DOUBAO_RERANK_MODEL,
                require_verified=True,
                expected_configuration_revision=request.configuration_revision,
                operation=lambda provider, configured_model: provider.analyze(ProviderRequest(
                    analysisInput=analysis_input,
                    prompt=request.prompt,
                    model=configured_model,
                    responseSchema=request.response_schema,
                )).rawText,
            )
        except AnalysisProviderConfigurationChangedError as error:
            raise SegmentRerankError(
                "provider_configuration_changed", "豆包配置已变化，请重新核对发送内容。", True,
            ) from error
        except AnalysisProviderUnconfiguredError as error:
            raise SegmentRerankError(
                "provider_unconfigured", "请先配置并验证豆包视觉复排服务。", False,
            ) from error
        except ProviderAnalysisError as error:
            raise SegmentRerankError(
                error.failure.code, error.failure.message, error.failure.retryable,
            ) from error


class Siglip2VisualVectorizer:
    """Multilingual image/text embeddings using Google's Apache-2.0 SigLIP 2 Base model."""

    version = "siglip2-base-p16-224-75de2d5"
    evidence_basis = "关键帧与查询文字的本地多语言图文向量，不使用素材名称或备注"

    def __init__(self, model_cache: Path):
        self._model_cache = Path(model_cache)
        self._runtime = None

    def _load(self):
        if self._runtime is not None:
            return self._runtime
        try:
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            import torch
            from transformers import AutoModel, AutoProcessor

            self._model_cache.mkdir(parents=True, exist_ok=True)
            model = AutoModel.from_pretrained(
                SIGLIP2_MODEL,
                revision=SIGLIP2_REVISION,
                cache_dir=self._model_cache,
            )
            processor = AutoProcessor.from_pretrained(
                SIGLIP2_MODEL,
                revision=SIGLIP2_REVISION,
                cache_dir=self._model_cache,
            )
            model.eval()
        except Exception as error:
            raise VisualVectorizerUnavailableError(
                "本地中文视觉模型无法加载，请检查模型文件和网络后重试。",
            ) from error
        self._runtime = (torch, model, processor)
        return self._runtime

    @staticmethod
    def _values(features) -> list[float]:
        normalized = features / features.norm(dim=-1, keepdim=True)
        return [float(value) for value in normalized[0].detach().cpu().tolist()]

    def embed_text(self, query: str) -> list[float]:
        torch, model, processor = self._load()
        inputs = processor(
            text=[query],
            return_tensors="pt",
            padding="max_length",
            truncation=True,
            max_length=model.config.text_config.max_position_embeddings,
        )
        with torch.no_grad():
            return self._values(model.get_text_features(**inputs))

    def embed_image(self, path: Path) -> list[float]:
        torch, model, processor = self._load()
        try:
            with Image.open(path) as image:
                inputs = processor(images=image.convert("RGB"), return_tensors="pt")
        except OSError as error:
            raise ValueError("片段关键帧无法读取") from error
        with torch.no_grad():
            return self._values(model.get_image_features(**inputs))


class SearchSegments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("检索文字不能为空白")
        return value


class SelectRerankCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidateIds: list[str] = Field(min_length=1, max_length=8)

    @field_validator("candidateIds")
    @classmethod
    def candidate_ids_are_unique_and_safe(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("复排候选不能重复")
        return [validate_storage_id(value) for value in values]


class ConfirmRerank(SelectRerankCandidates):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclosureAccepted: bool


class _RerankItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidateId: str
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)

    @field_validator("candidateId")
    @classmethod
    def candidate_id_is_safe(cls, value: str) -> str:
        return validate_storage_id(value)


class _RerankResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ranking: list[_RerankItem] = Field(min_length=1, max_length=8)


@dataclass(frozen=True)
class _Candidate:
    candidate_id: str
    asset_id: str
    source_fingerprint: str
    algorithm_version: str
    keyframe_model_version: str
    in_point: float
    out_point: float
    keyframe_time: float
    keyframe_path: Path

    def identity(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "assetId": self.asset_id,
            "sourceFingerprint": self.source_fingerprint,
            "algorithmVersion": self.algorithm_version,
            "keyframeModelVersion": self.keyframe_model_version,
            "inPointSeconds": self.in_point,
            "outPointSeconds": self.out_point,
            "keyframeTimeSeconds": self.keyframe_time,
        }


class _RetrievalStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def directory(self, project_id: str, *parts: str) -> Path:
        return safe_child(self.root, "project-files", project_id, "segment-retrieval", *parts)

    def save(self, project_id: str, name: str, value: dict[str, Any]) -> None:
        path = self.directory(project_id, *name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".retrieval-", suffix=".part")
        temporary = Path(raw)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, project_id: str, name: str) -> dict[str, Any]:
        try:
            value = json.loads(self.directory(project_id, *name.split("/")).read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except FileNotFoundError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise OSError("片段检索记录损坏") from error

    def searches(self, project_id: str) -> list[dict[str, Any]]:
        directory = self.directory(project_id, "searches")
        values = [self.load(project_id, f"searches/{path.name}") for path in directory.glob("*.json")] if directory.is_dir() else []
        return sorted(values, key=lambda item: (item.get("createdAt", ""), item.get("id", "")), reverse=True)


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("视觉向量维度不一致")
    if any(not math.isfinite(value) for value in [*left, *right]):
        raise ValueError("视觉向量包含无效数值")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _quality(path: Path) -> tuple[float, dict[str, Any]]:
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        grayscale = image.convert("L").resize((64, 64))
        contrast = min(1.0, ImageStat.Stat(grayscale).stddev[0] / 64)
    resolution = min(1.0, width * height / (640 * 360))
    score = round(0.55 * resolution + 0.45 * contrast, 6)
    return score, {"width": width, "height": height, "contrast": round(contrast, 6)}


def _catalog_version(candidates: list[_Candidate], vectorizer_version: str) -> str:
    payload = {
        "vectorizerVersion": vectorizer_version,
        "candidates": [candidate.identity() for candidate in candidates],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _contact_sheet(candidates: list[_Candidate]) -> tuple[bytes, int, int]:
    cell_width, cell_height = 320, 200
    width, height = _contact_sheet_dimensions(len(candidates))
    columns = width // cell_width
    sheet = Image.new("RGB", (width, height), "#11161d")
    draw = ImageDraw.Draw(sheet)
    for index, candidate in enumerate(candidates, start=1):
        with Image.open(candidate.keyframe_path) as source:
            frame = ImageOps.contain(source.convert("RGB"), (cell_width, cell_height - 20))
        left = ((index - 1) % columns) * cell_width
        top = ((index - 1) // columns) * cell_height
        sheet.paste(frame, (left + (cell_width - frame.width) // 2, top + 20 + (cell_height - 20 - frame.height) // 2))
        draw.rectangle((left, top, left + 34, top + 20), fill="#ffffff")
        draw.text((left + 8, top + 3), str(index), fill="#11161d")
    stream = io.BytesIO()
    sheet.save(stream, format="JPEG", quality=88, optimize=True)
    return stream.getvalue(), sheet.width, sheet.height


def _contact_sheet_dimensions(candidate_count: int) -> tuple[int, int]:
    if candidate_count < 1:
        raise ValueError("联系表至少需要一个候选")
    columns = min(2, candidate_count)
    rows = math.ceil(candidate_count / columns)
    return 320 * columns, 200 * rows


def _analysis_context(width: int, height: int) -> str:
    from .analysis_input import ImageAnalysisInput
    from .analysis_prompt import analysis_input_context

    return analysis_input_context(ImageAnalysisInput(
        analysisProxyBytes=b"disclosure-placeholder",
        width=width,
        height=height,
        aspectRatio=width / height,
    ))


def _rerank_prompt(query: str, candidates: list[dict[str, Any]]) -> str:
    allowed = [{
        "contactSheetPosition": index,
        "candidateId": item["candidateId"],
        "assetId": item["assetId"],
        "inPointSeconds": item["inPointSeconds"],
        "outPointSeconds": item["outPointSeconds"],
        "keyframeTimeSeconds": item["keyframeTimeSeconds"],
    } for index, item in enumerate(candidates, start=1)]
    return (
        "你是视频素材片段视觉复排器。只根据用户查询和联系表中的已选关键帧复排候选。"
        "不得推断音频、完整视频、未展示镜头或项目背景。每个候选必须恰好出现一次，理由须引用可见画面证据。"
        "只返回符合响应结构的 JSON。\n查询："
        + query
        + "\n候选："
        + json.dumps(allowed, ensure_ascii=False, separators=(",", ":"))
    )


def _rerank_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: candidate[key] for key in _RERANK_CANDIDATE_FIELDS}


def create_segment_retrieval_router(
    data_dir: Path,
    get_project: Callable[[str], Any],
    *,
    vectorizer: VisualVectorizer | None = None,
    reranker: Any = None,
    algorithm_version: str = SEGMENT_ALGORITHM_VERSION,
    keyframe_model_version: str = KEYFRAME_MODEL_VERSION,
    source_lock: Any = None,
) -> APIRouter:
    root = Path(data_dir).absolute()
    assets = ProjectAssets(root)
    store = _RetrievalStore(root)
    visual = vectorizer or Siglip2VisualVectorizer(root / "models" / "siglip2-base-p16-224")
    lock = source_lock or RLock()
    router = APIRouter(prefix="/api/projects/{project_id}/segment-searches")

    def fail(code: str, message: str, status: int = 409) -> None:
        raise HTTPException(status, detail={"code": code, "message": message})

    def ensure_project(project_id: str) -> None:
        if get_project(project_id) is None:
            fail("project_not_found", "内容项目不存在。", 404)

    def current_candidates(project_id: str) -> list[_Candidate]:
        segment_root = safe_child(root, "project-files", project_id, "source-segments")
        values: list[_Candidate] = []
        for state_path in segment_root.glob("*/state.json") if segment_root.is_dir() else []:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                asset_id = validate_storage_id(state_path.parent.name)
                asset = assets.get(project_id, asset_id)
                source = assets.file(project_id, asset)
                if (
                    state.get("status") != "completed"
                    or state.get("assetId") != asset_id
                    or state.get("sourceFingerprint") != source_fingerprint(source)
                    or state.get("algorithmVersion") != algorithm_version
                    or state.get("keyframeModelVersion") != keyframe_model_version
                ):
                    continue
                run_id = validate_storage_id(state["runId"])
                for item in state.get("candidates", []):
                    filename = validate_storage_id(item["keyframe"]["_filename"])
                    keyframe_path = safe_child(state_path.parent, "runs", run_id, filename)
                    if not keyframe_path.is_file() or keyframe_path.is_symlink():
                        continue
                    values.append(_Candidate(
                        candidate_id=validate_storage_id(item["id"]),
                        asset_id=asset_id,
                        source_fingerprint=state["sourceFingerprint"],
                        algorithm_version=state["algorithmVersion"],
                        keyframe_model_version=state["keyframeModelVersion"],
                        in_point=float(item["inPointSeconds"]),
                        out_point=float(item["outPointSeconds"]),
                        keyframe_time=float(item["keyframe"]["timeSeconds"]),
                        keyframe_path=keyframe_path,
                    ))
            except (AssetFileUnavailableError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(values, key=lambda item: (item.asset_id, item.in_point, item.candidate_id))

    def load_or_build_index(project_id: str, candidates: list[_Candidate]) -> tuple[str, dict[str, dict[str, Any]]]:
        version = _catalog_version(candidates, visual.version)
        try:
            cached = store.load(project_id, "visual-index.json")
            if cached.get("indexVersion") == version and cached.get("vectorizerVersion") == visual.version:
                entries = cached.get("entries")
                if isinstance(entries, list) and len(entries) == len(candidates):
                    return version, {entry["candidateId"]: entry for entry in entries}
        except FileNotFoundError:
            pass
        entries = []
        for candidate in candidates:
            vector = [float(value) for value in visual.embed_image(candidate.keyframe_path)]
            quality_score, quality_evidence = _quality(candidate.keyframe_path)
            entries.append({
                "candidateId": candidate.candidate_id,
                "identity": candidate.identity(),
                "vector": vector,
                "quality": {"score": quality_score, "evidence": quality_evidence},
            })
        store.save(project_id, "visual-index.json", {
            "schemaVersion": 1,
            "retrievalVersion": RETRIEVAL_VERSION,
            "vectorizerVersion": visual.version,
            "indexVersion": version,
            "entries": entries,
        })
        return version, {entry["candidateId"]: entry for entry in entries}

    def result_candidate(project_id: str, candidate: _Candidate, entry: dict[str, Any], relevance: float, duplicate: dict[str, Any]) -> dict[str, Any]:
        start, end = candidate.in_point, candidate.out_point
        return candidate.identity() | {
            "visualRelevance": round(relevance, 6),
            "quality": entry["quality"],
            "duplicateRisk": duplicate,
            "evidence": {
                "kind": "keyframe_visual_vector",
                "vectorizerVersion": visual.version,
                "basis": getattr(
                    visual, "evidence_basis",
                    "关键帧视觉向量，不使用素材名称或备注",
                ),
            },
            "keyframe": {
                "timeSeconds": candidate.keyframe_time,
                "url": (
                    f"/api/projects/{project_id}/source-segments/assets/{candidate.asset_id}"
                    f"/candidates/{candidate.candidate_id}/keyframe"
                ),
            },
            "preview": {
                "url": f"/api/projects/{project_id}/preproduction/assets/{candidate.asset_id}/file#t={start:.3f},{end:.3f}",
                "startSeconds": start,
                "endSeconds": end,
            },
        }

    def public(project_id: str, state: dict[str, Any]) -> dict[str, Any]:
        candidates = current_candidates(project_id)
        current_version = _catalog_version(candidates, visual.version)
        validity = {
            "status": "current" if state.get("indexVersion") == current_version else "stale",
            "reason": None if state.get("indexVersion") == current_version else "segment_index_changed",
        }
        value = dict(state)
        value["validity"] = validity
        history = []
        for attempt in state.get("rerankHistory", []):
            item = dict(attempt)
            item["validity"] = {
                "status": "current" if attempt.get("indexVersion") == current_version else "stale",
                "reason": None if attempt.get("indexVersion") == current_version else "segment_index_changed",
            }
            if item["validity"]["status"] == "current" and reranker is not None:
                try:
                    snapshot = reranker.snapshot()
                    if attempt.get("model") != snapshot.model:
                        item["validity"] = {"status": "stale", "reason": "model_changed"}
                    elif attempt.get("configurationRevision") != snapshot.configuration_revision:
                        item["validity"] = {"status": "stale", "reason": "provider_configuration_changed"}
                except Exception:
                    item["validity"] = {"status": "stale", "reason": "provider_configuration_changed"}
            history.append(item)
        value["rerankHistory"] = history
        value["rerank"] = history[-1] if history else None
        return value

    def load_search(project_id: str, search_id: str) -> dict[str, Any]:
        validate_storage_id(search_id)
        try:
            state = store.load(project_id, f"searches/{search_id}.json")
        except FileNotFoundError:
            fail("segment_search_not_found", "片段检索结果不存在。", 404)
        if state.get("id") != search_id or not isinstance(state.get("localCandidates"), list):
            fail("segment_search_invalid", "片段检索记录无法读取。", 503)
        return state

    def selected_results(state: dict[str, Any], candidate_ids: list[str]) -> list[dict[str, Any]]:
        by_id = {item.get("candidateId"): item for item in state["localCandidates"]}
        try:
            return [by_id[candidate_id] for candidate_id in candidate_ids]
        except KeyError:
            fail("rerank_candidate_invalid", "复排候选不属于当前本地检索结果。", 422)
        raise AssertionError("unreachable")

    def disclosure_value(project_id: str, state: dict[str, Any], candidate_ids: list[str]) -> dict[str, Any]:
        exposed = public(project_id, state)
        if exposed["validity"]["status"] != "current":
            fail("segment_search_stale", "片段索引已变化，请重新执行本地检索。")
        if reranker is None:
            fail("provider_unconfigured", "请先配置并验证豆包视觉复排服务。", 422)
        try:
            snapshot = reranker.snapshot()
        except SegmentRerankError as error:
            fail(error.code, error.message, error.status_code)
        if snapshot.provider != "doubao" or snapshot.model != DOUBAO_RERANK_MODEL:
            fail("provider_unconfigured", "豆包视觉复排必须使用已验证的 Doubao-Seed-2.1-pro。", 422)
        candidates = selected_results(state, candidate_ids)
        disclosed = [_rerank_candidate_payload(item) for item in candidates]
        width, height = _contact_sheet_dimensions(len(disclosed))
        prompt = _rerank_prompt(state["query"], disclosed)
        context = _analysis_context(width, height)
        identity = {
            "searchId": state["id"],
            "query": state["query"],
            "indexVersion": state["indexVersion"],
            "provider": snapshot.provider,
            "model": snapshot.model,
            "configurationRevision": snapshot.configuration_revision,
            "candidates": disclosed,
        }
        digest = hashlib.sha256(json.dumps(
            identity | {"sentText": [prompt, context]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return identity | {
            "digest": digest,
            "sentContent": {
                "text": prompt + "\n" + context,
                "prompt": prompt,
                "analysisContext": context,
                "keyframeCount": len(disclosed),
                "keyframes": [item["candidateId"] for item in disclosed],
            },
            "notSent": ["完整视频", "音频", "未选择的项目素材与候选"],
        }

    def record_failed_rerank(
        project_id: str,
        state: dict[str, Any],
        disclosure: dict[str, Any],
        candidate_ids: list[str],
        error: SegmentRerankError,
    ) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        attempt = {
            "id": f"segment-rerank-{uuid4().hex}",
            "status": "failed",
            "provider": disclosure["provider"],
            "model": disclosure["model"],
            "configurationRevision": disclosure["configurationRevision"],
            "indexVersion": state["indexVersion"],
            "candidateIds": candidate_ids,
            "createdAt": timestamp,
            "completedAt": None,
            "ranking": [],
            "error": {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
            },
        }
        state["updatedAt"] = timestamp
        state.setdefault("rerankHistory", []).append(attempt)
        store.save(project_id, f"searches/{state['id']}.json", state)

    @router.get("")
    def list_searches(project_id: str):
        ensure_project(project_id)
        with lock:
            try:
                return {"searches": [public(project_id, state) for state in store.searches(project_id)]}
            except OSError:
                fail("segment_search_storage_unavailable", "片段检索记录无法读取。", 503)

    @router.post("", status_code=201)
    def search_segments(project_id: str, request: SearchSegments):
        ensure_project(project_id)
        with lock:
            try:
                candidates = current_candidates(project_id)
                if not candidates:
                    fail("segment_index_required", "请先为至少一个项目视频建立当前片段索引。")
                index_version, entries = load_or_build_index(project_id, candidates)
                query_vector = [float(value) for value in visual.embed_text(request.query)]
                scored = []
                for candidate in candidates:
                    entry = entries[candidate.candidate_id]
                    cosine = _cosine(query_vector, entry["vector"])
                    relevance = max(0.0, min(1.0, (cosine + 1) / 2))
                    score = 0.85 * relevance + 0.15 * float(entry["quality"]["score"])
                    scored.append((score, relevance, candidate, entry))
                scored.sort(key=lambda item: (-item[0], item[2].asset_id, item[2].in_point))
                results = []
                selected_vectors: list[tuple[str, list[float]]] = []
                for _score, relevance, candidate, entry in scored[:request.limit]:
                    similar = max(
                        ((identifier, _cosine(entry["vector"], vector)) for identifier, vector in selected_vectors),
                        key=lambda item: item[1],
                        default=(None, 0.0),
                    )
                    duplicate = {
                        "level": "high" if similar[0] is not None and similar[1] >= 0.95 else "low",
                        "similarity": round(max(0.0, similar[1]), 6),
                        "candidateId": similar[0],
                    }
                    results.append(result_candidate(project_id, candidate, entry, relevance, duplicate))
                    selected_vectors.append((candidate.candidate_id, entry["vector"]))
                timestamp = datetime.now(timezone.utc).isoformat()
                state = {
                    "schemaVersion": 1,
                    "id": f"segment-search-{uuid4().hex}",
                    "query": request.query,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                    "retrievalVersion": RETRIEVAL_VERSION,
                    "vectorizerVersion": visual.version,
                    "indexVersion": index_version,
                    "localCandidates": results,
                    "rerank": None,
                    "rerankHistory": [],
                }
                store.save(project_id, f"searches/{state['id']}.json", state)
                return public(project_id, state)
            except HTTPException:
                raise
            except VisualVectorizerUnavailableError as error:
                fail("segment_vectorizer_unavailable", str(error), 503)
            except (OSError, ValueError) as error:
                fail("segment_retrieval_failed", "本地视觉检索失败，请检查片段关键帧后重试。", 422)
                raise AssertionError from error

    @router.get("/{search_id}")
    def get_search(project_id: str, search_id: str):
        ensure_project(project_id)
        with lock:
            try:
                return public(project_id, load_search(project_id, search_id))
            except HTTPException:
                raise
            except OSError:
                fail("segment_search_storage_unavailable", "片段检索记录无法读取。", 503)

    @router.post("/{search_id}/rerank-disclosure")
    def prepare_rerank(project_id: str, search_id: str, request: SelectRerankCandidates):
        ensure_project(project_id)
        with lock:
            try:
                return disclosure_value(project_id, load_search(project_id, search_id), request.candidateIds)
            except HTTPException:
                raise
            except OSError:
                fail("segment_search_storage_unavailable", "片段检索记录无法读取。", 503)

    @router.post("/{search_id}/rerank")
    def run_rerank(project_id: str, search_id: str, request: ConfirmRerank):
        ensure_project(project_id)
        if request.disclosureAccepted is not True:
            fail("rerank_disclosure_required", "请先核对并确认本次豆包发送内容。", 422)
        with lock:
            state = load_search(project_id, search_id)
            disclosure = disclosure_value(project_id, state, request.candidateIds)
            if request.digest != disclosure["digest"]:
                fail("rerank_disclosure_stale", "查询、候选或豆包配置已变化，请重新核对发送内容。")
            selected = selected_results(state, request.candidateIds)
            catalog = {candidate.candidate_id: candidate for candidate in current_candidates(project_id)}
            try:
                source_candidates = [catalog[candidate_id] for candidate_id in request.candidateIds]
            except KeyError:
                fail("segment_search_stale", "片段索引已变化，请重新执行本地检索。")
            contact_sheet, width, height = _contact_sheet(source_candidates)
            rerank_request = SegmentRerankRequest(
                query=state["query"],
                candidates=[_rerank_candidate_payload(item) for item in selected],
                prompt=disclosure["sentContent"]["prompt"],
                analysis_context=disclosure["sentContent"]["analysisContext"],
                configuration_revision=disclosure["configurationRevision"],
                contact_sheet_bytes=contact_sheet,
                contact_sheet_width=width,
                contact_sheet_height=height,
                response_schema=_RerankResponse.model_json_schema(),
            )
        try:
            raw = reranker.rerank(rerank_request)
        except SegmentRerankError as error:
            with lock:
                state = load_search(project_id, search_id)
                record_failed_rerank(project_id, state, disclosure, request.candidateIds, error)
            fail(error.code, error.message, error.status_code)
        try:
            parsed = _RerankResponse.model_validate_json(raw)
        except (ValidationError, ValueError, TypeError):
            error = SegmentRerankError(
                "invalid_rerank_response", "豆包复排返回的数据不符合结构化契约。", True,
            )
            with lock:
                state = load_search(project_id, search_id)
                record_failed_rerank(project_id, state, disclosure, request.candidateIds, error)
            fail(error.code, error.message, 502)
        returned_ids = [item.candidateId for item in parsed.ranking]
        if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(request.candidateIds):
            error = SegmentRerankError(
                "invalid_rerank_response", "豆包复排返回的数据不符合候选边界。", True,
            )
            with lock:
                state = load_search(project_id, search_id)
                record_failed_rerank(project_id, state, disclosure, request.candidateIds, error)
            fail(error.code, error.message, 502)
        with lock:
            state = load_search(project_id, search_id)
            fresh = disclosure_value(project_id, state, request.candidateIds)
            if fresh["digest"] != request.digest:
                error = SegmentRerankError(
                    "provider_configuration_changed", "豆包配置在复排期间发生变化，结果未采用。", True,
                )
                record_failed_rerank(project_id, state, disclosure, request.candidateIds, error)
                fail(error.code, error.message)
            timestamp = datetime.now(timezone.utc).isoformat()
            attempt = {
                "id": f"segment-rerank-{uuid4().hex}",
                "status": "completed",
                "provider": disclosure["provider"],
                "model": disclosure["model"],
                "configurationRevision": disclosure["configurationRevision"],
                "indexVersion": state["indexVersion"],
                "candidateIds": request.candidateIds,
                "createdAt": timestamp,
                "completedAt": timestamp,
                "ranking": [item.model_dump() for item in parsed.ranking],
                "error": None,
            }
            state["updatedAt"] = timestamp
            state.setdefault("rerankHistory", []).append(attempt)
            store.save(project_id, f"searches/{search_id}.json", state)
            return public(project_id, state)

    return router


__all__ = [
    "AnalysisProviderSegmentReranker",
    "DOUBAO_RERANK_MODEL",
    "RETRIEVAL_VERSION",
    "RerankProviderSnapshot",
    "SegmentRerankError",
    "SegmentRerankRequest",
    "SegmentReranker",
    "Siglip2VisualVectorizer",
    "VisualVectorizer",
    "VisualVectorizerUnavailableError",
    "create_segment_retrieval_router",
]
