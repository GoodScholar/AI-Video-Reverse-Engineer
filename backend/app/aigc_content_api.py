"""Project-scoped marketing brief and script candidates."""

import json
import os
import tempfile
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .preproduction import safe_child
from .project_assets import ProjectAssets
from .prompt_generation import _redact_value
from .provider_models import PROVIDER_IDS
from .generation_service import GenerationAdapter, GenerationAdapterRegistry, GenerationError, GenerationService
from .video_aspect import valid_aspect_mode


class _Beat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=120)
    factIds: list[str] = Field(min_length=1, max_length=10)
    assetId: str = Field(min_length=1)

    @field_validator("text", "assetId")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("文案与素材不可为空")
        return value.strip()


class _Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sellingPoint: str = Field(min_length=1, max_length=120)
    beats: list[_Beat] = Field(min_length=2, max_length=8)

    @field_validator("sellingPoint")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("卖点不可为空")
        return value.strip()


class _CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[_Candidate] = Field(min_length=5, max_length=5)


def _empty_state():
    return {"schemaVersion": 1, "brief": {"revision": 0, "productName": "", "facts": [], "audience": "",
            "sellingPoints": [], "callToAction": "", "forbiddenPhrases": [], "assetIds": [], "aspectMode": "9:16"}, "candidates": []}


def _valid_brief(value):
    required = {"productName", "facts", "audience", "sellingPoints", "callToAction", "forbiddenPhrases", "assetIds"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"aspectMode"}:
        return False
    if "aspectMode" in value and not valid_aspect_mode(value["aspectMode"]):
        return False
    if any(not isinstance(value[key], str) or len(value[key]) > 2000 for key in ("productName", "audience", "callToAction")):
        return False
    if not isinstance(value["facts"], list) or len(value["facts"]) > 40 or any(
        not isinstance(item, dict) or set(item) != {"id", "text"} or not isinstance(item["id"], str)
        or not item["id"].strip() or not isinstance(item["text"], str) or not item["text"].strip()
        or len(item["text"]) > 500 for item in value["facts"]
    ) or len({item["id"] for item in value["facts"]}) != len(value["facts"]):
        return False
    for key in ("sellingPoints", "forbiddenPhrases", "assetIds"):
        if not isinstance(value[key], list) or len(value[key]) > 80 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 500 for item in value[key]
        ) or len(set(value[key])) != len(value[key]):
            return False
    return True


def _path(root, project_id):
    return safe_child(root, "project-files", project_id, "aigc-content", "state.json")


def load_aigc_content_state(root, project_id):
    path = _path(root, project_id)
    if not path.exists():
        return _empty_state()
    if path.is_symlink() or not path.is_file():
        raise ValueError("创作状态文件无效")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schemaVersion") != 1 or not isinstance(state.get("brief"), dict) or not isinstance(state.get("candidates"), list):
        raise ValueError("创作状态无效")
    state["brief"].setdefault("aspectMode", "9:16")
    return state


def _save(root, project_id, state):
    path = _path(root, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".aigc-", suffix=".part")
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _disclosure(root, project_id, brief, provider, model):
    if provider not in PROVIDER_IDS or not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise HTTPException(422, detail={"code": "aigc_provider_invalid", "message": "请选择已配置的 AI 服务和模型。"})
    if not brief["productName"].strip() or not brief["facts"] or len(brief["assetIds"]) < 2:
        raise HTTPException(422, detail={"code": "aigc_brief_incomplete", "message": "请填写商品名称、商品事实并选择至少两段画面素材。"})
    assets = ProjectAssets(root).index(project_id)
    selected = []
    for asset_id in brief["assetIds"]:
        asset = assets.get(asset_id)
        if asset is None or asset.get("kind") not in ("image", "video"):
            raise HTTPException(422, detail={"code": "aigc_asset_unavailable", "message": "所选画面素材已不存在或类型不适用。"})
        selected.append({"id": asset_id, "name": asset["name"], "notes": asset.get("notes", "")})
    source = _redact_value({key: value for key, value in brief.items() if key not in ("revision", "assetIds")})
    source["assets"] = _redact_value(selected)
    prompt = ("你是电商商品短视频脚本助手。只使用下列商品事实，不得自行补充商品参数、价格、功效或承诺。"
              "请生成五条不同卖点或叙事角度的营销脚本候选。每条含 sellingPoint 与 beats；"
              "每个 beat 含 text、factIds 和 assetId。factIds 必须引用下列事实 ID，assetId 必须引用下列素材 ID。"
              "不要使用禁用表达；只返回符合响应结构的 JSON。\n创作简报：\n"
              + json.dumps(source, ensure_ascii=False, separators=(",", ":")))
    digest = sha256(json.dumps([brief["revision"], provider, model, prompt], ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"briefRevision": brief["revision"], "provider": provider, "model": model, "prompt": prompt, "digest": digest}


def _validate_candidates(raw, brief):
    try:
        response = _CandidateResponse.model_validate_json(raw)
    except (TypeError, ValueError, ValidationError):
        raise HTTPException(422, detail={"code": "aigc_candidates_invalid", "message": "AI 返回的五条脚本结构无效，请调整简报后重试。"}) from None
    signatures = set()
    for candidate in response.candidates:
        signature = tuple(beat.text.casefold() for beat in candidate.beats)
        if signature in signatures:
            raise HTTPException(422, detail={"code": "aigc_candidates_duplicate", "message": "AI 返回了重复脚本，请调整简报后重试。"})
        signatures.add(signature)
        _check_candidate_for_brief(candidate, brief)
    return response.candidates


def _check_candidate_for_brief(candidate, brief):
    _check_candidate(candidate, {fact["id"] for fact in brief["facts"]}, set(brief["assetIds"]),
                     [value.lower() for value in brief["forbiddenPhrases"]])


def _check_candidate(candidate, facts, assets, forbidden):
    if any(word in candidate.sellingPoint.lower() for word in forbidden):
        raise HTTPException(422, detail={"code": "aigc_forbidden_phrase", "message": "候选卖点含禁用表达，请修改。"})
    for beat in candidate.beats:
        if beat.assetId not in assets or not set(beat.factIds).issubset(facts) or len(set(beat.factIds)) != len(beat.factIds):
            raise HTTPException(422, detail={"code": "aigc_candidate_reference_invalid", "message": "候选引用了无效商品事实或素材，请修改。"})
        if any(word in beat.text.lower() for word in forbidden):
            raise HTTPException(422, detail={"code": "aigc_forbidden_phrase", "message": "候选文案含禁用表达，请修改。"})


def load_confirmed_handoff(data_dir, project_id, generation_id):
    """Return five current, validated candidates for batch editing."""
    content = load_aigc_content_state(Path(data_dir), project_id)
    brief = content["brief"]
    candidates = [item for item in content["candidates"] if item.get("generationId") == generation_id]
    if (len(candidates) != 5 or any(item.get("confirmedRevision") != item.get("revision")
                                    or item.get("briefRevision") != brief["revision"] for item in candidates)):
        raise HTTPException(409, detail={"code": "aigc_handoff_unconfirmed", "message": "五条脚本候选都须按当前简报确认。"})
    scripts = set()
    for item in candidates:
        try:
            checked = _Candidate.model_validate({"sellingPoint": item["sellingPoint"], "beats": item["beats"]})
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, detail={"code": "aigc_candidate_invalid", "message": "脚本候选无效，请重新编辑确认。"}) from None
        signature = tuple(beat.text.casefold() for beat in checked.beats)
        if signature in scripts:
            raise HTTPException(422, detail={"code": "aigc_handoff_duplicate", "message": "五条脚本文案不可重复，请修改后重新确认。"})
        scripts.add(signature)
        _check_candidate_for_brief(checked, brief)
    return brief, candidates


def create_aigc_content_router(data_dir, get_project, script_generator=None, source_lock=None,
                               generation_service=None, compute_queue=None, capability_catalog=None):
    root = Path(data_dir)
    project_assets = ProjectAssets(root)
    router = APIRouter(prefix="/api/projects/{project_id}/aigc-content")
    lock = RLock()

    def run_adapter(run):
        if script_generator is None:
            raise GenerationError("provider_unconfigured")
        return script_generator(run["provider"], run["model"], run["input"]["prompt"], run["input"]["textParams"].get("structuredSchema"))

    if generation_service is None:
        registry = GenerationAdapterRegistry()
        registry.register(GenerationAdapter("aigc-script-text", "compat-1", "text", run_adapter,
            capability_catalog or GenerationService._default, result_validator=lambda text: _CandidateResponse.model_validate_json(text)))
        service = GenerationService(root, registry=registry, queue=compute_queue)
    else:
        service = generation_service

    def generation_error(error):
        if error.code in {"generation_adapter_missing", "generation_adapter_resolution_failed"}:
            error = GenerationError("provider_unconfigured")
        messages = {
            "generation_run_missing": (404, "生成运行不存在。"),
            "generation_idempotency_conflict": (409, "同一幂等键已用于不同输入，不能重复提交。"),
            "generation_capability_unavailable": (422, "所选服务未声明本次结构化文本生成能力。"),
            "generation_cancel_unsupported": (422, "所选服务未声明远端取消能力。"),
            "generation_timeout_unsupported": (422, "所选服务未声明超时控制能力。"),
            "provider_unconfigured": (422, "请先配置并验证所选 AI 服务。"),
            "generation_adapter_missing": (422, "请先配置并验证所选 AI 服务。"),
        }
        status, message = messages.get(error.code, (422, "文本生成请求无效。"))
        raise HTTPException(status, detail={"code": error.code, "message": message}) from None

    @router.get("")
    def workspace(project_id: str):
        get_project(project_id)
        with lock:
            state = load_aigc_content_state(root, project_id)
            assets = project_assets.public(
                project_id, kinds=("image", "video"), include_url=False,
                include_source_reference=True,
            )
        return {"brief": state["brief"], "candidates": state["candidates"], "assets": assets,
                "generationRuns": service.list_runs(project_id)}

    @router.put("/brief")
    def save_brief(project_id: str, body: dict):
        get_project(project_id)
        if not isinstance(body, dict) or set(body) != {"revision", "brief"} or type(body["revision"]) is not int or not _valid_brief(body["brief"]):
            raise HTTPException(422, detail={"code": "aigc_brief_invalid", "message": "创作简报无效。"})
        with source_lock or nullcontext(), lock:
            state = load_aigc_content_state(root, project_id)
            if body["revision"] != state["brief"]["revision"]:
                raise HTTPException(409, detail={"code": "aigc_brief_conflict", "message": "创作简报已更新，请刷新后重试。"})
            state["brief"] = {**body["brief"], "aspectMode": body["brief"].get("aspectMode", "9:16"),
                              "revision": body["revision"] + 1}
            _save(root, project_id, state)
        return {"brief": state["brief"]}

    @router.post("/disclosure")
    def disclose(project_id: str, body: dict):
        get_project(project_id)
        if not isinstance(body, dict) or set(body) != {"provider", "model"}:
            raise HTTPException(422, detail={"code": "aigc_provider_invalid", "message": "请选择已配置的 AI 服务和模型。"})
        with source_lock or nullcontext(), lock:
            state = load_aigc_content_state(root, project_id)
            return _disclosure(root, project_id, state["brief"], body["provider"], body["model"])

    @router.post("/candidates")
    def generate_candidates(project_id: str, body: dict):
        get_project(project_id)
        raise HTTPException(410, detail={"code": "aigc_generation_run_required", "message": "请先创建生成运行，再显式采用结果。"})

    @router.post("/generation-runs", status_code=202)
    def submit_generation_run(project_id: str, body: dict):
        get_project(project_id)
        expected = {"briefRevision", "provider", "model", "digest", "disclosureAccepted", "idempotencyKey"}
        if not isinstance(body, dict) or set(body) != expected or body.get("disclosureAccepted") is not True or type(body.get("briefRevision")) is not int or not isinstance(body.get("digest"), str) or not isinstance(body.get("idempotencyKey"), str) or not body["idempotencyKey"]:
            raise HTTPException(422, detail={"code": "generation_request_invalid", "message": "文本生成提交无效，请重新核对发送内容。"})
        with source_lock or nullcontext(), lock:
            state = load_aigc_content_state(root, project_id)
            disclosure = _disclosure(root, project_id, state["brief"], body["provider"], body["model"])
            if body["briefRevision"] != state["brief"]["revision"] or body["digest"] != disclosure["digest"]:
                raise HTTPException(409, detail={"code": "aigc_disclosure_stale", "message": "创作简报或素材信息已更新，请重新核对发送内容。"})
            frozen_disclosure = {key: disclosure[key] for key in ("provider", "model", "prompt", "digest", "briefRevision")}
            frozen_disclosure["accepted"] = True
            try:
                run = service.submit(project_id, disclosure=frozen_disclosure, text_params={"structuredSchema": _CandidateResponse.model_json_schema()}, idempotency_key=body["idempotencyKey"], caller="aigc-script-candidates")
            except GenerationError as error:
                generation_error(error)
        return {"run": run}

    @router.get("/generation-capabilities")
    def generation_capabilities(project_id: str, provider: str, model: str):
        get_project(project_id)
        try:
            return service.capabilities(provider, model)
        except GenerationError as error:
            generation_error(error)

    @router.get("/generation-runs/{run_id}")
    def get_generation_run(project_id: str, run_id: str):
        get_project(project_id)
        try:
            return service.get_run(project_id, run_id)
        except GenerationError as error:
            generation_error(error)

    @router.post("/generation-runs/{run_id}/cancel")
    def cancel_generation_run(project_id: str, run_id: str):
        get_project(project_id)
        try:
            return {"run": service.cancel(project_id, run_id)}
        except GenerationError as error:
            generation_error(error)

    @router.post("/generation-runs/{run_id}/outputs/{result_id}/adopt")
    def adopt_generation_result(project_id: str, run_id: str, result_id: str):
        get_project(project_id)
        with source_lock or nullcontext(), lock:
            try:
                run = service.get_run(project_id, run_id)
            except GenerationError as error:
                generation_error(error)
            item = next((value for value in run.get("outputItems", []) if value.get("resultId") == result_id and value.get("status") == "completed"), None)
            if item is None or not isinstance(item.get("text"), str):
                raise HTTPException(409, detail={"code": "generation_result_unavailable", "message": "文本结果尚不可采用。"})
            state = load_aigc_content_state(root, project_id)
            current_disclosure = _disclosure(root, project_id, state["brief"], run["provider"], run["model"])
            if (run["disclosure"].get("briefRevision") != state["brief"]["revision"]
                    or run["disclosure"].get("digest") != current_disclosure["digest"]):
                raise HTTPException(409, detail={"code": "aigc_disclosure_stale", "message": "创作简报或素材信息已更新，请重新生成后采用。"})
            existing = [candidate for candidate in state["candidates"] if candidate.get("sourceRunId") == run_id and candidate.get("sourceResultId") == result_id]
            if existing:
                return {"candidates": existing}
            candidates = _validate_candidates(item["text"], state["brief"])
            generation_id = uuid4().hex
            saved = [{**candidate.model_dump(), "id": uuid4().hex, "generationId": generation_id, "revision": 0,
                      "briefRevision": state["brief"]["revision"], "confirmedRevision": None,
                      "provider": run["provider"], "model": run["model"], "promptDigest": run["disclosure"]["digest"],
                      "sourceRunId": run_id, "sourceResultId": result_id} for candidate in candidates]
            state["candidates"].extend(saved)
            _save(root, project_id, state)
            return {"candidates": saved}

    def candidate_for(state, candidate_id):
        candidate = next((item for item in state["candidates"] if item["id"] == candidate_id), None)
        if candidate is None:
            raise HTTPException(404, detail={"code": "aigc_candidate_missing", "message": "脚本候选不存在。"})
        return candidate

    @router.put("/candidates/{candidate_id}")
    def save_candidate(project_id: str, candidate_id: str, body: dict):
        get_project(project_id)
        if (not isinstance(body, dict) or set(body) != {"revision", "sellingPoint", "beats"}
                or type(body["revision"]) is not int):
            raise HTTPException(422, detail={"code": "aigc_candidate_invalid", "message": "脚本候选内容无效。"})
        try:
            candidate_input = _Candidate.model_validate({"sellingPoint": body["sellingPoint"], "beats": body["beats"]})
        except ValidationError:
            raise HTTPException(422, detail={"code": "aigc_candidate_invalid", "message": "脚本候选内容无效。"}) from None
        with source_lock or nullcontext(), lock:
            state = load_aigc_content_state(root, project_id)
            candidate = candidate_for(state, candidate_id)
            if candidate["revision"] != body["revision"]:
                raise HTTPException(409, detail={"code": "aigc_candidate_conflict", "message": "脚本候选已更新，请刷新后重试。"})
            brief = state["brief"]
            _check_candidate_for_brief(candidate_input, brief)
            candidate.update(candidate_input.model_dump())
            candidate.update(revision=candidate["revision"] + 1, briefRevision=brief["revision"], confirmedRevision=None)
            _save(root, project_id, state)
            return {"candidate": candidate}

    @router.post("/candidates/{candidate_id}/confirm")
    def confirm_candidate(project_id: str, candidate_id: str, body: dict):
        get_project(project_id)
        if not isinstance(body, dict) or set(body) != {"revision"} or type(body["revision"]) is not int:
            raise HTTPException(422, detail={"code": "aigc_candidate_invalid", "message": "脚本候选版本无效。"})
        with source_lock or nullcontext(), lock:
            state = load_aigc_content_state(root, project_id)
            candidate = candidate_for(state, candidate_id)
            if candidate["revision"] != body["revision"]:
                raise HTTPException(409, detail={"code": "aigc_candidate_conflict", "message": "脚本候选已更新，请刷新后重试。"})
            brief = state["brief"]
            if candidate["briefRevision"] != brief["revision"]:
                raise HTTPException(409, detail={"code": "aigc_brief_stale", "message": "创作简报已更新，请重新核对脚本。"})
            candidate_input = _Candidate.model_validate({"sellingPoint": candidate["sellingPoint"], "beats": candidate["beats"]})
            _check_candidate_for_brief(candidate_input, brief)
            candidate["confirmedRevision"] = candidate["revision"]
            _save(root, project_id, state)
            return {"candidate": candidate}

    return router
