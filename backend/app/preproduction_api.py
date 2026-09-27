"""Project-scoped API for assembling and exporting video pre-production work."""
from __future__ import annotations

import json
import hashlib
import math
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from contextlib import nullcontext
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from .asset_references import asset_references
from .timeline import TimelineStore
from .preproduction import NODE_KINDS, PreproductionStore
from .preproduction_handoff import render_handoff
from .preproduction_nodes import FFMPEG_TIMEOUT_SECONDS, MAX_MEDIA_DURATION_SECONDS, MAX_OUTPUT_DIMENSION, MAX_OUTPUT_PIXELS
from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path
from .reference_video import validate_storage_id
from .shot_results import MAX_RESULT_VERSIONS, public_result_versions, result_association, result_history, result_signature


MAX_ASSET_BYTES = 200_000_000
MAX_SHOTS = 500
MAX_NODES_PER_SHOT = 80
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_MEDIA_SUFFIXES = _IMAGE_SUFFIXES | {".mp4", ".mov", ".m4a", ".mp3", ".wav", ".aac", ".webm"}


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    inputKind: Literal["reference_video", "depth_video", "white_model_video"] = "reference_video"
    theme: str = Field(default="", max_length=4000)
    purpose: str = Field(default="", max_length=4000)
    style: str = Field(default="", max_length=4000)
    duration: float = Field(default=0, ge=0, le=36000)
    aspect: str = Field(default="", max_length=100)
    mustPreserve: str = Field(default="", max_length=12000)


class NodeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["reference", "trim", "first_frame", "last_frame", "crop", "resize", "prompt"]
    input: str = Field(default="", max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)
    # Workspace GET is also the PUT editing shape. These server-owned values are
    # deliberately accepted then discarded by derive_shots.
    status: Optional[str] = None
    error: Optional[str] = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        return validate_storage_id(value)


class ShotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=1000)
    duration: float = Field(ge=0, le=36000)
    prompt: str = Field(default="", max_length=12000)
    negativePrompt: str = Field(default="", max_length=12000)
    resultAssetId: Optional[str] = Field(default=None, max_length=100)
    # Read-only response metadata; accepted for round-trip editing and discarded.
    resultVersions: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_RESULT_VERSIONS)
    assetIds: list[str] = Field(default_factory=list, max_length=100)
    nodes: list[NodeUpdate] = Field(default_factory=list, max_length=MAX_NODES_PER_SHOT)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        return validate_storage_id(value)


class SaveWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(ge=0)
    brief: Brief
    shots: list[ShotUpdate] = Field(default_factory=list, max_length=MAX_SHOTS)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)


class ResultNote(RevisionRequest):
    note: str = Field(default="", max_length=4000)


class AdoptionReason(RevisionRequest):
    reason: str = Field(default="", max_length=1000)


class AssetMetadata(RevisionRequest):
    name: str = Field(min_length=1, max_length=255)
    notes: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("素材名称不能为空或包含控制字符")
        return value


def create_preproduction_router(
    data_dir: Path,
    get_project: Callable[[str], Any],
    compute_queue: Any,
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    source_lock: Any = None,
    runner: Optional[Callable[..., list[str]]] = None,
) -> APIRouter:
    """Create the router; ``runner`` is a test seam and defaults to execute_node."""
    root = Path(data_dir)
    store = PreproductionStore(root)
    lock = source_lock or RLock()
    router = APIRouter(prefix="/api/projects/{project_id}/preproduction")

    if runner is None:
        from .preproduction_nodes import execute_node
        runner = execute_node

    def fail(code: str, message: str, status: int = 409) -> None:
        raise HTTPException(status_code=status, detail={"code": code, "message": message})

    def project_for(project_id: str) -> Any:
        value = get_project(project_id)
        if value is None:
            fail("project_not_found", "复刻项目不存在。", 404)
        return as_object(value)

    def state_for(project_id: str) -> dict[str, Any]:
        try:
            return store.load(project_id)
        except (OSError, ValueError):
            fail("preproduction_storage_invalid", "前置工作台状态无法读取。", 503)

    def save(project_id: str, state: dict[str, Any]) -> None:
        try:
            store.save(project_id, state)
        except (OSError, ValueError):
            fail("preproduction_storage_failed", "前置工作台状态无法保存。", 503)

    def asset_path(project_id: str, asset: dict[str, Any]) -> Path:
        try:
            name = asset["file"]
            validate_storage_id(name)
            path = store.path(project_id, "assets", name)
            _regular_file(path)
            return path
        except (KeyError, OSError, ValueError):
            fail("preproduction_asset_unavailable", "素材文件不可用。", 409)

    def artifact_path(project_id: str, shot_id: str, node_id: str, name: str) -> Path:
        try:
            validate_storage_id(shot_id); validate_storage_id(node_id); validate_storage_id(name)
            path = store.path(project_id, "artifacts", shot_id, node_id, name)
            _regular_file(path)
            return path
        except (OSError, ValueError):
            fail("preproduction_artifact_unavailable", "节点产物不可用。", 409)

    def node_source(project_id: str, state: dict[str, Any], shot: dict[str, Any], node: dict[str, Any]) -> Optional[Path]:
        input_value = node["input"]
        if not input_value:
            return None
        prefix, _, identifier = input_value.partition(":")
        if prefix == "asset":
            asset = next((item for item in state["assets"] if item["id"] == identifier), None)
            if asset is None:
                fail("preproduction_input_missing", "节点输入素材不存在。")
            return asset_path(project_id, asset)
        if prefix == "node":
            upstream = next((item for item in shot["nodes"] if item["id"] == identifier), None)
            if upstream is None or upstream["status"] != "completed" or not upstream["artifacts"]:
                fail("preproduction_input_not_ready", "上游节点尚未生成当前产物。")
            return artifact_path(project_id, shot["id"], upstream["id"], upstream["artifacts"][0]["name"])
        fail("preproduction_input_invalid", "节点输入无效。", 422)

    def ensure_reference_asset(project_id: str, project: Any, state: dict[str, Any]) -> dict[str, Any]:
        reference = getattr(project, "referenceMedia", None)
        if reference is None or not managed_reference_media_is_safe(root, project_id, reference):
            fail("preproduction_reference_unavailable", "当前参考素材不可用。")
        source_id = getattr(reference, "id", None)
        existing = next((asset for asset in state["assets"] if asset.get("sourceReferenceId") == source_id), None)
        if existing is not None:
            return existing
        source = resolve_reference_media_path(root, project_id, reference)
        _regular_file(source)
        suffix = "." + str(getattr(reference, "format", "")).lower()
        if suffix not in _MEDIA_SUFFIXES:
            fail("preproduction_reference_unavailable", "当前参考素材格式不可用。")
        asset_id = "reference-" + uuid4().hex
        name = asset_id + suffix
        target = store.path(project_id, "assets", name)
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_regular(source, target)
        record = {"id": asset_id, "name": _display_name(getattr(reference, "originalName", "参考素材")), "kind": getattr(reference, "type", "video"),
                  "role": "reference", "file": name, "sourceReferenceId": source_id}
        for source_key, target_key in (("durationSeconds", "duration"), ("width", "width"), ("height", "height")):
            value = getattr(reference, source_key, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                record[target_key] = value
        state["assets"].append(record)
        return record

    def validate_shots(incoming: list[ShotUpdate], assets: list[dict[str, Any]]) -> None:
        asset_ids = {asset["id"] for asset in assets}
        if len({shot.id for shot in incoming}) != len(incoming):
            fail("preproduction_shot_invalid", "镜头标识符不能重复。", 422)
        for shot in incoming:
            if shot.resultAssetId is not None and not any(asset["id"] == shot.resultAssetId and asset["kind"] == "video" for asset in assets):
                fail("preproduction_result_invalid", "镜头结果必须是本项目的视频素材。", 422)
            if len(set(shot.assetIds)) != len(shot.assetIds) or any(value not in asset_ids for value in shot.assetIds):
                fail("preproduction_asset_missing", "镜头绑定了不存在的素材。", 422)
            seen: set[str] = set()
            for index, node in enumerate(shot.nodes):
                if node.id in seen:
                    fail("preproduction_node_invalid", "同一镜头中的节点标识符不能重复。", 422)
                _validate_node(node, index, seen, set(shot.assetIds), fail)
                seen.add(node.id)

    def derive_shots(previous: list[dict[str, Any]], incoming: list[ShotUpdate], brief_changed: bool,
                     brief: dict[str, Any], assets: list[dict[str, Any]], revision: int) -> list[dict[str, Any]]:
        old_shots = {shot["id"]: shot for shot in previous}
        result: list[dict[str, Any]] = []
        for item in incoming:
            old_shot = old_shots.get(item.id, {})
            old_nodes = {node["id"]: node for node in old_shot.get("nodes", [])}
            changed: set[str] = set()
            nodes: list[dict[str, Any]] = []
            for node in item.nodes:
                value = {"id": node.id, "kind": node.kind, "input": node.input, "params": node.params}
                old = old_nodes.get(node.id)
                same = old is not None and all(old.get(key) == value[key] for key in ("kind", "input", "params"))
                if same:
                    value.update(status=old["status"], error=old.get("error"), artifacts=old.get("artifacts", []))
                else:
                    if old and old.get("status") == "running":
                        fail("preproduction_node_running", "运行中的节点不能修改。")
                    value.update(status="stale" if old else "pending", error=None, artifacts=[])
                    changed.add(node.id)
                nodes.append(value)
            if brief_changed or old_shot.get("prompt") != item.prompt or old_shot.get("negativePrompt") != item.negativePrompt:
                for node in nodes:
                    if node["kind"] == "prompt":
                        if node["status"] == "running":
                            fail("preproduction_node_running", "运行中的节点依赖不能修改。")
                        node.update(status="stale", error=None, artifacts=[])
                        changed.add(node["id"])
            for index, node in enumerate(nodes):
                upstream = node["input"].removeprefix("node:") if node["input"].startswith("node:") else None
                if upstream in changed:
                    if node["status"] == "running":
                        fail("preproduction_node_running", "运行中的节点依赖不能修改。")
                    node.update(status="stale", error=None, artifacts=[])
                    changed.add(node["id"])
            record = {"id": item.id, "title": item.title, "duration": item.duration, "prompt": item.prompt,
                      "negativePrompt": item.negativePrompt, "assetIds": item.assetIds, "nodes": nodes, "resultAssetId": item.resultAssetId}
            history = result_history(old_shot)
            if item.resultAssetId and not any(version["assetId"] == item.resultAssetId for version in history):
                if len(history) >= MAX_RESULT_VERSIONS:
                    fail("preproduction_result_limit", "每个镜头最多保留 100 个候选结果。", 422)
                history.append({"assetId": item.resultAssetId, "signature": result_signature(brief, record), "reviewed": False,
                                "association": result_association(brief, record, assets, revision)})
            record["_resultVersions"] = history
            if "_importKey" in old_shot:
                record["_importKey"] = old_shot["_importKey"]
            result.append(record)
        return result

    def checks(project_id: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        assets = {asset["id"]: asset for asset in state["assets"]}
        brief = state["brief"]
        missing = [label for key, label in (("theme", "主题"), ("aspect", "画幅")) if not brief.get(key, "").strip()]
        if not brief.get("duration"):
            missing.append("目标时长")
        if missing:
            result.append({"level": "warning", "code": "brief_incomplete", "message": "创作需求尚未填写：" + "、".join(missing) + "。"})
        total_duration = sum(shot["duration"] for shot in state["shots"])
        if state["shots"] and brief.get("duration", 0) > 0 and not math.isclose(total_duration, brief["duration"], rel_tol=0, abs_tol=0.01):
            result.append({"level": "warning", "code": "duration_mismatch", "message": f"镜头计划总时长 {total_duration:.2f} 秒，与目标 {brief['duration']:.2f} 秒不一致，请核对镜头或需求。"})
        if not state["shots"]:
            result.append({"level": "error", "code": "shots_missing", "message": "尚未添加镜头，无法交付。"})
        for shot in state["shots"]:
            if not shot["assetIds"]:
                result.append({"level": "warning", "code": "shot_assets_missing", "shotId": shot["id"], "message": "镜头未绑定素材，请确认是否仅使用文字制作。"})
            if shot["duration"] <= 0:
                result.append({"level": "warning", "code": "shot_duration_missing", "shotId": shot["id"], "message": "镜头尚未设置有效计划时长。"})
            if not shot["prompt"].strip():
                result.append({"level": "warning", "code": "shot_prompt_missing", "shotId": shot["id"], "message": "镜头尚未填写提示词。"})
            adopted = next((version for version in public_result_versions(brief, shot) if version["assetId"] == shot.get("resultAssetId")), None)
            if adopted and (adopted["planChanged"] or not adopted["reviewed"]):
                result.append({"level": "warning", "code": "result_review_required", "shotId": shot["id"], "message": "采用结果的方案已变化或缺少关联记录，请重新检查。" if adopted["planChanged"] else "采用结果尚未按当前方案人工检查。"})
            referenced = set(shot["assetIds"])
            referenced.update(version["assetId"] for version in result_history(shot))
            if shot.get("resultAssetId"):
                referenced.add(shot["resultAssetId"])
            referenced.update(node["input"].split(":", 1)[1] for node in shot["nodes"] if node["input"].startswith("asset:"))
            for asset_id in referenced:
                asset = assets.get(asset_id)
                if asset is None or not _stored_file_available(store, project_id, "assets", asset.get("file")):
                    result.append({"level": "error", "code": "shot_asset_unavailable", "shotId": shot["id"], "message": "镜头引用的素材已不存在。"})
            for node in shot["nodes"]:
                if node["status"] == "failed":
                    result.append({"level": "error", "code": "node_failed", "shotId": shot["id"], "nodeId": node["id"], "message": "节点执行失败，请重试或修改输入。"})
                elif node["status"] in ("pending", "queued", "running", "stale"):
                    result.append({"level": "error", "code": "node_not_ready", "shotId": shot["id"], "nodeId": node["id"], "message": "节点尚未生成当前产物。"})
                elif node["status"] == "completed" and (not node["artifacts"] or any(
                    not _stored_file_available(store, project_id, "artifacts", shot["id"], node["id"], artifact.get("name"))
                    for artifact in node["artifacts"]
                )):
                    result.append({"level": "error", "code": "node_output_unavailable", "shotId": shot["id"], "nodeId": node["id"], "message": "节点当前产物不可用。"})
        return result

    def response(project_id: str, state: dict[str, Any]) -> dict[str, Any]:
        public = _public_state(project_id, state)
        by_id = {asset["id"]: asset for asset in state["assets"]}
        for asset in public["assets"]:
            asset["available"] = _stored_file_available(store, project_id, "assets", by_id[asset["id"]].get("file"))
        public["checks"] = checks(project_id, state)
        public["nodeCatalog"] = [{"kind": kind, "label": _node_label(kind)} for kind in NODE_KINDS]
        return public

    def recover() -> None:
        base = root / "project-files"
        if not base.is_dir() or base.is_symlink():
            return
        for path in base.glob("*/preproduction/state.json"):
            try:
                if path.is_symlink():
                    continue
                project_id = path.parents[1].name
                state = store.load(project_id)
                changed = False
                for shot in state["shots"]:
                    for node in shot["nodes"]:
                        if node["status"] in ("queued", "running"):
                            node.update(status="failed", error="服务重启中断了节点执行，可重试。", artifacts=[])
                            changed = True
                if changed:
                    store.save(project_id, state)
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    recover()

    @router.get("")
    def get_workspace(project_id: str):
        project_for(project_id)
        with lock:
            return response(project_id, state_for(project_id))

    @router.put("")
    def save_workspace(project_id: str, body: SaveWorkspace):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            if _has_inflight(state):
                fail("preproduction_node_running", "有节点正在执行，完成后再修改工作台。")
            validate_shots(body.shots, state["assets"])
            brief_changed = state["brief"] != body.brief.model_dump()
            state["brief"] = body.brief.model_dump()
            state["shots"] = derive_shots(state["shots"], body.shots, brief_changed, state["brief"], state["assets"], state["revision"] + 1)
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    def managed_asset(state, asset_id):
        asset = next((item for item in state["assets"] if item["id"] == asset_id), None)
        if asset is None:
            fail("preproduction_asset_missing", "素材不存在。", 404)
        return asset

    def references_for(project_id, state, asset_id):
        try:
            return asset_references(state, TimelineStore(root).load(project_id), asset_id)
        except (OSError, ValueError, KeyError, TypeError):
            fail("asset_references_unavailable", "无法完整读取素材引用，暂不能删除。", 503)

    def editable_assets(project_id, revision):
        state = state_for(project_id)
        if revision != state["revision"]:
            fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
        if _has_inflight(state):
            fail("preproduction_node_running", "有节点正在执行，完成后再修改素材。")
        return state

    @router.get("/assets/{asset_id}/references")
    def get_asset_references(project_id: str, asset_id: str):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            managed_asset(state, asset_id)
            return {"references": references_for(project_id, state, asset_id)}

    @router.put("/assets/{asset_id}")
    def update_asset(project_id: str, asset_id: str, body: AssetMetadata):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = editable_assets(project_id, body.revision)
            asset = managed_asset(state, asset_id)
            asset.update(name=body.name, notes=body.notes)
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.post("/assets/{asset_id}/delete")
    def delete_asset(project_id: str, asset_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = editable_assets(project_id, body.revision)
            asset = managed_asset(state, asset_id)
            references = references_for(project_id, state, asset_id)
            if references:
                fail("asset_in_use", "素材仍被引用：" + "；".join(item["label"] for item in references[:5]))
            path = asset_path(project_id, asset)
            # Stage removal so a failed state write leaves the original file usable.
            staged = path.with_name(f".delete-{uuid4().hex}.part")
            try:
                path.rename(staged)
                state["assets"] = [item for item in state["assets"] if item["id"] != asset_id]
                state["revision"] += 1
                try:
                    save(project_id, state)
                except Exception:
                    staged.rename(path)
                    raise
                staged.unlink()
            except OSError:
                fail("asset_delete_failed", "素材删除未能完成，请刷新检查后重试。", 503)
            return response(project_id, state)

    @router.post("/assets")
    async def upload_asset(project_id: str, role: Literal["character", "scene", "motion", "audio", "reference"] = Query(...), file: UploadFile = File(...), resultForShot: Optional[str] = Query(None), revision: Optional[int] = Query(None)):
        project_for(project_id)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _MEDIA_SUFFIXES:
            fail("preproduction_asset_invalid", "素材格式不受支持。", 422)
        temporary: Optional[Path] = None
        try:
            with source_lock or nullcontext(), lock:
                state = state_for(project_id)
                shot = None
                if resultForShot is not None:
                    if _has_inflight(state):
                        fail("preproduction_node_running", "有节点正在执行，完成后再关联镜头结果。")
                    if revision != state["revision"]:
                        fail("preproduction_conflict", "镜头方案已更新，请重新读取后上传结果。")
                    shot = next((item for item in state["shots"] if item["id"] == resultForShot), None)
                    if shot is None:
                        fail("preproduction_shot_missing", "镜头不存在。", 404)
                    if len(result_history(shot)) >= MAX_RESULT_VERSIONS:
                        fail("preproduction_result_limit", "每个镜头最多保留 100 个候选结果。", 422)
                temporary = _receive_upload(root, file, suffix)
                metadata = _probe_media(temporary, suffix, ffprobe_path, ffmpeg_path)
                if shot is not None and metadata["kind"] != "video":
                    fail("preproduction_result_invalid", "镜头结果必须上传视频。", 422)
                asset_id = "asset-" + uuid4().hex
                name = asset_id + suffix
                target = store.path(project_id, "assets", name)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary, target)
                temporary = None
                state["assets"].append({"id": asset_id, "name": _display_name(file.filename), "kind": metadata["kind"], "role": role,
                                        "file": name, **metadata})
                if shot is not None:
                    history = result_history(shot)
                    history.append({"assetId": asset_id, "signature": result_signature(state["brief"], shot), "reviewed": False,
                                    "association": result_association(state["brief"], shot, state["assets"], state["revision"] + 1)})
                    shot["_resultVersions"] = history
                    shot["resultAssetId"] = asset_id
                state["revision"] += 1
                save(project_id, state)
                return response(project_id, state)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            await file.close()

    def removable_candidate(state, shot_id, asset_id):
        shot = next((item for item in state["shots"] if item["id"] == shot_id), None)
        if shot is None or not any(item["assetId"] == asset_id for item in result_history(shot)):
            fail("preproduction_result_missing", "当前镜头不存在该候选结果。", 404)
        if shot.get("resultAssetId") == asset_id:
            fail("preproduction_result_adopted", "当前采用的结果不能移出历史，请先采用其他结果并保存。")
        return shot

    @router.get("/shots/{shot_id}/results/{asset_id}/cleanup-preview")
    def preview_candidate_removal(project_id: str, shot_id: str, asset_id: str):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            shot = removable_candidate(state, shot_id, asset_id)
            return {"revision": state["revision"], "bytes": 0, "fileCount": 0,
                    "description": f'仅从镜头「{shot["title"]}」移除这一候选及人工检查记录。素材文件、其他镜头和时间线均保留；如需释放空间，请另行在素材库检查引用后删除。'}

    @router.post("/shots/{shot_id}/results/{asset_id}/remove")
    def remove_candidate(project_id: str, shot_id: str, asset_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = editable_assets(project_id, body.revision)
            shot = removable_candidate(state, shot_id, asset_id)
            shot["_resultVersions"] = [item for item in result_history(shot) if item["assetId"] != asset_id]
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.post("/shots/{shot_id}/results/{asset_id}/review")
    def review_result(project_id: str, shot_id: str, asset_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "方案已更新，请重新读取后检查。")
            if _has_inflight(state):
                fail("preproduction_node_running", "有节点正在执行，完成后再记录检查结果。")
            shot = next((item for item in state["shots"] if item["id"] == shot_id), None)
            history = result_history(shot) if shot else []
            version = next((item for item in history if item["assetId"] == asset_id), None)
            if version is None:
                fail("preproduction_result_missing", "当前镜头不存在该候选结果。", 404)
            asset = next((item for item in state["assets"] if item["id"] == asset_id), None)
            if not asset or asset["kind"] != "video" or not _stored_file_available(store, project_id, "assets", asset.get("file")):
                fail("preproduction_result_missing", "候选视频文件不可用。")
            version.update(signature=result_signature(state["brief"], shot), reviewed=True)
            shot["_resultVersions"] = history
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.put("/shots/{shot_id}/results/{asset_id}/note")
    def update_result_note(project_id: str, shot_id: str, asset_id: str, body: ResultNote):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = editable_assets(project_id, body.revision)
            shot = next((item for item in state["shots"] if item["id"] == shot_id), None)
            history = result_history(shot) if shot else []
            version = next((item for item in history if item["assetId"] == asset_id), None)
            if version is None:
                fail("preproduction_result_missing", "当前镜头不存在该候选结果。", 404)
            version["externalNote"] = body.note.strip()
            shot["_resultVersions"] = history
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.put("/shots/{shot_id}/results/{asset_id}/adoption-reason")
    def update_adoption_reason(project_id: str, shot_id: str, asset_id: str, body: AdoptionReason):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = editable_assets(project_id, body.revision)
            shot = next((item for item in state["shots"] if item["id"] == shot_id), None)
            history = result_history(shot) if shot else []
            version = next((item for item in history if item["assetId"] == asset_id), None)
            if version is None:
                fail("preproduction_result_missing", "当前镜头不存在该候选结果。", 404)
            version["adoptionReason"] = body.reason.strip()
            shot["_resultVersions"] = history
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.post("/import-reference")
    def import_reference(project_id: str, body: RevisionRequest):
        project = project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            before = len(state["assets"])
            ensure_reference_asset(project_id, project, state)
            if len(state["assets"]) != before:
                state["revision"] += 1
                save(project_id, state)
            return response(project_id, state)

    @router.post("/import-shots")
    def import_shots(project_id: str, body: RevisionRequest):
        project = project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            if _has_inflight(state):
                fail("preproduction_node_running", "有节点正在执行，完成后再启动其他节点。")
            source_shots = _preparation_shots(root, project_id, project)
            before = (len(state["assets"]), len(state["shots"]))
            reference_asset = ensure_reference_asset(project_id, project, state)
            existing = {shot.get("_importKey", shot["id"]) for shot in state["shots"]}
            for source in source_shots:
                if source["importKey"] in existing:
                    continue
                shot_id = source["importKey"]
                state["shots"].append({"id": shot_id, "_importKey": source["importKey"], "title": source["id"], "duration": source["duration"],
                                       "prompt": source["prompt"], "negativePrompt": source["negativePrompt"], "assetIds": [reference_asset["id"]], "nodes": [{
                                           "id": "trim", "kind": "trim", "input": f"asset:{reference_asset['id']}", "params": {"start": source["start"], "end": source["end"]},
                                           "status": "pending", "error": None, "artifacts": [],
                                       }]})
                existing.add(source["importKey"])
            if (len(state["assets"]), len(state["shots"])) != before:
                state["revision"] += 1
                save(project_id, state)
            return response(project_id, state)

    @router.post("/import-toolkit")
    def import_toolkit(project_id: str, body: RevisionRequest):
        """Copy completed Toolkit video outputs into this workspace once per source."""
        project = project_for(project_id)
        from .video_toolkit import asset_paths
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            existing = {asset.get("sourceKey") for asset in state["assets"]}
            for source_key, (source, label) in asset_paths(root, project).items():
                if source_key.startswith("reference:") or source_key in existing:
                    continue
                try:
                    _regular_file(source)
                    suffix = source.suffix.lower()
                    if suffix not in _MEDIA_SUFFIXES:
                        continue
                    metadata = _probe_media(source, suffix, ffprobe_path, ffmpeg_path)
                    if metadata["kind"] != "video":
                        continue
                    asset_id = "toolkit-" + uuid4().hex
                    name = asset_id + suffix
                    target = store.path(project_id, "assets", name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _copy_regular(source, target)
                    state["assets"].append({"id": asset_id, "name": _display_name(label + suffix), "role": "motion", "file": name,
                                            "sourceKey": source_key, **metadata})
                    existing.add(source_key)
                except (OSError, ValueError, HTTPException):
                    continue
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.post("/shots/{shot_id}/nodes/{node_id}/run", status_code=202)
    def run_node(project_id: str, shot_id: str, node_id: str, body: RevisionRequest):
        project_for(project_id)
        validate_storage_id(shot_id); validate_storage_id(node_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            if _has_inflight(state):
                fail("preproduction_node_running", "有节点正在执行，完成后再启动其他节点。")
            shot, node = _find_node(state, shot_id, node_id, fail)
            if node["status"] in ("queued", "running"):
                fail("preproduction_node_running", "节点正在执行。")
            source = node_source(project_id, state, shot, node)
            run_id = uuid4().hex
            node.update(status="queued", error=None, artifacts=[], runId=run_id)
            _invalidate_descendants(shot, node_id)
            state["revision"] += 1
            save(project_id, state)
            if not compute_queue.submit("preproduction:" + run_id, project_id, lambda pid: process(pid, shot_id, node_id, run_id, source)):
                state = state_for(project_id)
                _, fresh = _find_node(state, shot_id, node_id, fail)
                if fresh.get("runId") == run_id:
                    fresh.update(status="failed", error="本地处理队列不可用，请重试。", artifacts=[])
                    state["revision"] += 1
                    save(project_id, state)
            return response(project_id, state_for(project_id))

    def process(project_id: str, shot_id: str, node_id: str, run_id: str, source: Optional[Path]) -> None:
        run_dir: Optional[Path] = None
        try:
            with lock:
                state = state_for(project_id)
                shot, node = _find_node(state, shot_id, node_id, fail)
                if node.get("runId") != run_id or node["status"] != "queued":
                    return
                node["status"] = "running"
                save(project_id, state)
            run_dir = store.path(project_id, "runs", run_id)
            run_dir.mkdir(parents=True, exist_ok=False)
            params = dict(node["params"])
            if node["kind"] == "prompt":
                sections = (
                    ("主题", state["brief"].get("theme", "")), ("用途", state["brief"].get("purpose", "")),
                    ("风格", state["brief"].get("style", "")), ("时长", state["brief"].get("duration", "")),
                    ("画幅", state["brief"].get("aspect", "")), ("必须保留", state["brief"].get("mustPreserve", "")),
                    ("镜头提示词", shot.get("prompt", "")), ("镜头负面提示词", shot.get("negativePrompt", "")),
                    ("节点文本", params.get("text", "")),
                )
                params["text"] = "\n".join(f"{label}: {value}" for label, value in sections if str(value).strip())
            names = runner(node["kind"], source, run_dir, params, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)
            if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
                raise ValueError("节点未生成有效产物")
            for name in names:
                validate_storage_id(name)
                _regular_file(run_dir / name)
            with lock:
                state = state_for(project_id)
                shot, node = _find_node(state, shot_id, node_id, fail)
                if node.get("runId") != run_id or node["status"] != "running":
                    return
                destination = store.path(project_id, "artifacts", shot_id, node_id)
                if destination.exists():
                    shutil.rmtree(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(run_dir, destination)
                run_dir = None
                node.update(status="completed", error=None, artifacts=[{"name": name, "url": f"/api/projects/{project_id}/preproduction/artifacts/{shot_id}/{node_id}/{name}"} for name in names])
                state["revision"] += 1
                save(project_id, state)
        except Exception as error:
            with lock:
                try:
                    state = state_for(project_id)
                    _, node = _find_node(state, shot_id, node_id, fail)
                    if node.get("runId") == run_id:
                        node.update(status="failed", error=_safe_error(error), artifacts=[])
                        state["revision"] += 1
                        save(project_id, state)
                except Exception:
                    pass
        finally:
            if run_dir is not None:
                shutil.rmtree(run_dir, ignore_errors=True)

    @router.get("/assets/{asset_id}/file")
    def download_asset(project_id: str, asset_id: str):
        validate_storage_id(asset_id)
        with lock:
            state = state_for(project_id)
            asset = next((item for item in state["assets"] if item["id"] == asset_id), None)
            if asset is None:
                fail("preproduction_asset_missing", "素材不存在。", 404)
            return FileResponse(asset_path(project_id, asset), filename=asset["name"])

    @router.get("/artifacts/{shot_id}/{node_id}/{name}")
    def download_artifact(project_id: str, shot_id: str, node_id: str, name: str):
        validate_storage_id(shot_id); validate_storage_id(node_id); validate_storage_id(name)
        with lock:
            state = state_for(project_id)
            _, node = _find_node(state, shot_id, node_id, fail)
            if node["status"] != "completed" or name not in {item["name"] for item in node["artifacts"]}:
                fail("preproduction_artifact_unavailable", "节点当前没有该产物。", 404)
            return FileResponse(artifact_path(project_id, shot_id, node_id, name), filename=name)

    @router.post("/package")
    def package(project_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            report = checks(project_id, state)
            if any(item["level"] == "error" for item in report):
                fail("preproduction_delivery_blocked", "交付检查存在错误，请先修复。")
            public = response(project_id, state)
            referenced = _referenced_assets(state)
            included_ids = {asset["id"] for asset in referenced}
            public["assets"] = [asset for asset in public["assets"] if asset["id"] in included_ids]
            for asset in public["assets"]:
                asset["url"] = f"assets/{asset['id']}/{_zip_name(asset['name'])}"
            for shot in public["shots"]:
                for node in shot["nodes"]:
                    for artifact in node["artifacts"]:
                        artifact["url"] = f"artifacts/{shot['id']}/{node['id']}/{artifact['name']}"
            root.mkdir(parents=True, exist_ok=True)
            descriptor, raw_path = tempfile.mkstemp(dir=root, prefix=".preproduction-package-", suffix=".zip")
            package_path = Path(raw_path)
            try:
                with os.fdopen(descriptor, "wb") as handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("workspace.json", json.dumps(public, ensure_ascii=False, indent=2))
                    archive.writestr("brief.json", json.dumps(public["brief"], ensure_ascii=False, indent=2))
                    archive.writestr("shots.json", json.dumps(public["shots"], ensure_ascii=False, indent=2))
                    archive.writestr("delivery-checks.json", json.dumps(report, ensure_ascii=False, indent=2))
                    archive.writestr("HANDOFF.md", render_handoff(public))
                    for asset in referenced:
                        archive.write(asset_path(project_id, asset), f"assets/{asset['id']}/{_zip_name(asset['name'])}")
                    for shot in state["shots"]:
                        for node in shot["nodes"]:
                            if node["status"] != "completed":
                                continue
                            for artifact in node["artifacts"]:
                                name = artifact["name"]
                                archive.write(artifact_path(project_id, shot["id"], node["id"], name), f"artifacts/{shot['id']}/{node['id']}/{name}")
            except BaseException:
                package_path.unlink(missing_ok=True)
                raise
            return FileResponse(package_path, media_type="application/zip", filename="preproduction.zip", background=BackgroundTask(package_path.unlink, missing_ok=True))

    return router


def _validate_node(node: NodeUpdate, index: int, seen: set[str], asset_ids: set[str], fail: Callable[..., None]) -> None:
    value = node.input
    if node.kind == "prompt":
        if value and not _valid_input(value, index, seen, asset_ids):
            fail("preproduction_input_invalid", "提示词节点输入无效。", 422)
        if not isinstance(node.params.get("text", ""), str) or len(node.params.get("text", "")) > 12000:
            fail("preproduction_node_invalid", "提示词节点参数无效。", 422)
        return
    if not _valid_input(value, index, seen, asset_ids):
        fail("preproduction_input_invalid", "节点输入必须是本镜头的素材或更早节点。", 422)
    if node.kind == "trim":
        _numbers(node.params, ("start", "end"), fail)
        if not node.params["start"] < node.params["end"]:
            fail("preproduction_node_invalid", "截取起止时间无效。", 422)
    elif node.kind == "crop":
        _numbers(node.params, ("x", "y", "width", "height"), fail)
        if any(not 0 <= node.params[key] <= 1 for key in ("x", "y", "width", "height")) or node.params["x"] + node.params["width"] > 1 or node.params["y"] + node.params["height"] > 1 or not node.params["width"] or not node.params["height"]:
            fail("preproduction_node_invalid", "裁切参数必须在 0 到 1 的范围内。", 422)
    elif node.kind == "resize":
        _numbers(node.params, ("width", "height"), fail)
        if any(int(node.params[key]) != node.params[key] or not 1 <= node.params[key] <= 8192 for key in ("width", "height")):
            fail("preproduction_node_invalid", "缩放尺寸无效。", 422)
    elif node.params:
        fail("preproduction_node_invalid", "该节点不接受参数。", 422)


def _valid_input(value: str, index: int, seen: set[str], asset_ids: set[str]) -> bool:
    prefix, separator, identifier = value.partition(":")
    return bool(separator and identifier and ((prefix == "asset" and identifier in asset_ids) or (prefix == "node" and identifier in seen)))


def _numbers(params: dict[str, Any], names: tuple[str, ...], fail: Callable[..., None]) -> None:
    if set(params) != set(names):
        fail("preproduction_node_invalid", "节点参数无效。", 422)
    for name in names:
        value = params.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            fail("preproduction_node_invalid", "节点参数必须是有限数值。", 422)


def _find_node(state: dict[str, Any], shot_id: str, node_id: str, fail: Callable[..., None]) -> tuple[dict[str, Any], dict[str, Any]]:
    shot = next((item for item in state["shots"] if item["id"] == shot_id), None)
    if shot is None:
        fail("preproduction_shot_missing", "镜头不存在。", 404)
    node = next((item for item in shot["nodes"] if item["id"] == node_id), None)
    if node is None:
        fail("preproduction_node_missing", "节点不存在。", 404)
    return shot, node


def _has_inflight(state: dict[str, Any]) -> bool:
    return any(node["status"] in ("queued", "running") for shot in state["shots"] for node in shot["nodes"])


def _invalidate_descendants(shot: dict[str, Any], source_id: str) -> None:
    stale = {source_id}
    for node in shot["nodes"]:
        if node["input"].startswith("node:") and node["input"].split(":", 1)[1] in stale:
            node.update(status="stale", error=None, artifacts=[])
            stale.add(node["id"])


def _stored_file_available(store: PreproductionStore, project_id: str, *parts: Any) -> bool:
    try:
        if any(not isinstance(part, str) for part in parts):
            return False
        _regular_file(store.path(project_id, *parts))
        return True
    except (OSError, ValueError):
        return False


def _referenced_assets(state: dict[str, Any]) -> list[dict[str, Any]]:
    ids = {asset_id for shot in state["shots"] for asset_id in shot["assetIds"]}
    ids.update(version["assetId"] for shot in state["shots"] for version in result_history(shot))
    ids.update(
        node["input"].split(":", 1)[1]
        for shot in state["shots"] for node in shot["nodes"] if node["input"].startswith("asset:")
    )
    return [asset for asset in state["assets"] if asset["id"] in ids]


def _regular_file(path: Path) -> None:
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or path.is_symlink():
        raise OSError("文件无效")


async def _discard_upload(file: UploadFile) -> None:
    await file.close()


def _receive_upload(root: Path, file: UploadFile, suffix: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(dir=root, prefix=".preproduction-", suffix=suffix)
    path = Path(raw)
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    raise HTTPException(413, detail={"code": "preproduction_asset_too_large", "message": "素材超过大小限制。"})
                target.write(chunk)
            target.flush(); os.fsync(target.fileno())
        if total <= 0:
            raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "素材为空。"})
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _probe_media(path: Path, suffix: str, ffprobe_path: str, ffmpeg_path: str = "ffmpeg") -> dict[str, Any]:
    if suffix in _IMAGE_SUFFIXES:
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                width, height = image.size
        except (OSError, UnidentifiedImageError):
            raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "图片内容无法读取。"})
        if width <= 0 or height <= 0 or width > MAX_OUTPUT_DIMENSION or height > MAX_OUTPUT_DIMENSION or width * height > MAX_OUTPUT_PIXELS:
            raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "图片尺寸无效。"})
        return {"kind": "image", "width": width, "height": height}
    try:
        result = subprocess.run([ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=False)
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        fmt = payload["format"]
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "媒体内容无法读取。"})
    if result.returncode or not isinstance(streams, list) or not isinstance(fmt, dict):
        raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "媒体内容无法读取。"})
    video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video" and not _attached_picture(item)), None)
    audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)
    try:
        duration = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        duration = 0.0
    if suffix == ".webm" and audio is not None and video is None and duration == 0:
        # MediaRecorder writes a streaming WebM without duration/cues. Remux
        # into a seekable container, retaining the encoded audio unchanged.
        descriptor, raw = tempfile.mkstemp(dir=path.parent, suffix=".webm")
        os.close(descriptor)
        normalized = Path(raw)
        try:
            result = subprocess.run([
                ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(path),
                "-map", "0:a:0", "-t", str(MAX_MEDIA_DURATION_SECONDS + 1), "-c:a", "copy", str(normalized),
            ], capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
            if result.returncode:
                raise ValueError("录音容器无法整理")
            probe = subprocess.run([ffprobe_path, "-v", "error", "-show_format", "-of", "json", str(normalized)],
                                   capture_output=True, text=True, timeout=30)
            duration = float(json.loads(probe.stdout)["format"]["duration"])
            if probe.returncode:
                raise ValueError("录音时长无法读取")
            os.replace(normalized, path)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "录音内容或时长无法读取。"})
        finally:
            normalized.unlink(missing_ok=True)
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_MEDIA_DURATION_SECONDS:
        raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "媒体时长无效。"})
    if video is not None:
        width, height = int(video["width"]), int(video["height"])
        if width <= 0 or height <= 0 or width > MAX_OUTPUT_DIMENSION or height > MAX_OUTPUT_DIMENSION or width * height > MAX_OUTPUT_PIXELS:
            raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "视频尺寸无效。"})
        _validate_decodable(path, "video", ffmpeg_path)
        return {"kind": "video", "duration": duration, "width": width, "height": height}
    if audio is not None:
        _validate_decodable(path, "audio", ffmpeg_path)
        return {"kind": "audio", "duration": duration}
    raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "媒体不含可用音视频流。"})


def _attached_picture(stream: dict[str, Any]) -> bool:
    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and disposition.get("attached_pic") == 1


def _validate_decodable(path: Path, kind: str, ffmpeg_path: str) -> None:
    mapping = ["-map", "0:a:0", "-t", "0.1"] if kind == "audio" else ["-map", "0:v:0", "-frames:v", "1", "-an"]
    try:
        result = subprocess.run([ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), *mapping, "-f", "null", "-"],
                                capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode:
        raise HTTPException(422, detail={"code": "preproduction_asset_invalid", "message": "媒体无法解码有效内容。"})


def _copy_regular(source: Path, target: Path) -> None:
    _regular_file(source)
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as stream, target.open("xb") as output:
            shutil.copyfileobj(stream, output)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _display_name(name: Optional[str]) -> str:
    value = (name or "素材").replace("\\", "/").rsplit("/", 1)[-1]
    return value[:255] or "素材"


def _public_state(project_id: str, value: dict[str, Any]) -> dict[str, Any]:
    result = {"revision": value["revision"], "brief": {"inputKind": "reference_video", **value["brief"]}, "assets": [], "shots": []}
    for asset in value["assets"]:
        result["assets"].append({key: item for key, item in asset.items() if key not in ("file", "sourceReferenceId") and not key.startswith("_")} | {"url": f"/api/projects/{project_id}/preproduction/assets/{asset['id']}/file"})
    for shot in value["shots"]:
        result["shots"].append({key: item for key, item in shot.items() if not key.startswith("_") and key != "nodes"} | {"resultVersions": public_result_versions(value["brief"], shot), "nodes": [
            {key: item for key, item in node.items() if key != "runId" and not key.startswith("_")} for node in shot["nodes"]
        ]})
    return result


def _node_label(kind: str) -> str:
    return {"reference": "引用素材", "trim": "截取", "first_frame": "首帧", "last_frame": "尾帧", "crop": "裁切", "resize": "缩放", "prompt": "提示词"}[kind]


def _safe_error(error: Exception) -> str:
    message = str(error)
    if message and len(message) <= 300 and "/" not in message and "\\" not in message:
        return message
    return "节点执行失败，请检查输入和参数。"


def _zip_name(name: str) -> str:
    return _display_name(name).replace("..", "_")


def as_object(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: as_object(item) for key, item in value.items()})
    if isinstance(value, list):
        return [as_object(item) for item in value]
    return value


def _preparation_shots(root: Path, project_id: str, project: Any) -> list[dict[str, Any]]:
    from .shot_preparation import ShotPreparationStore, load_scene_changes, scene_boundaries

    reference = getattr(project, "referenceMedia", None)
    preprocessing = getattr(project, "localPreprocessing", None)
    if (
        reference is None or preprocessing is None or getattr(preprocessing, "status", None) != "completed"
        or getattr(preprocessing, "sourceReferenceMediaId", None) != getattr(reference, "id", None)
    ):
        raise HTTPException(409, detail={"code": "preproduction_preparation_unavailable", "message": "现有镜头准备不可用。"})
    try:
        duration = float(getattr(reference, "durationSeconds"))
        state = ShotPreparationStore(root).load(project_id, reference.id, preprocessing.id)
        changes = load_scene_changes(root / "project-files" / project_id / "local-preprocessing" / preprocessing.id / "scene-changes.json")
        if state.timelineOverride is not None:
            changes = [{"timeSeconds": cut} for cut in state.timelineOverride.cuts]
        boundaries = scene_boundaries(duration, changes)
    except (OSError, ValueError, TypeError):
        raise HTTPException(409, detail={"code": "preproduction_preparation_unavailable", "message": "现有镜头准备时间线无效。"})
    result = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        shot_id = f"shot-{index:03d}"
        draft = state.drafts.get(shot_id)
        prompts = draft.prompts if draft is not None else None
        identity = f"{reference.id}|{preprocessing.id}|{state.timelineEpoch}|{shot_id}"
        import_key = "preparation-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        result.append({"id": shot_id, "importKey": import_key, "start": start, "end": end, "duration": end - start,
                       "prompt": getattr(prompts, "positiveZh", ""), "negativePrompt": getattr(prompts, "negativeZh", "")})
    return result


def _shot_from_preparation(item: Any) -> dict[str, Any]:
    item = as_object(item)
    start = getattr(item, "startSeconds", 0.0)
    end = getattr(item, "endSeconds", 0.0)
    prompts = getattr(item, "prompts", None)
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
        raise HTTPException(409, detail={"code": "preproduction_preparation_unavailable", "message": "现有镜头准备时间线无效。"})
    return {"id": validate_storage_id(str(getattr(item, "id"))), "duration": float(end - start),
            "prompt": str(getattr(prompts, "positiveZh", "")), "negativePrompt": str(getattr(prompts, "negativeZh", ""))}
