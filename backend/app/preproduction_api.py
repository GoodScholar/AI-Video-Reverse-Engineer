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

from .asset_references import preproduction_asset_references, timeline_asset_references
from .durable_runs import LOCAL_RUN_POLICY
from .timeline import TimelineStore
from .preproduction import PreproductionStore
from .project_assets import (
    AssetFileUnavailableError,
    AssetInUseError,
    AssetReferencesUnavailableError,
    ProjectAssets,
)
from .preproduction_handoff import render_handoff
from .preproduction_nodes import FFMPEG_TIMEOUT_SECONDS, MAX_MEDIA_DURATION_SECONDS, MAX_OUTPUT_DIMENSION, MAX_OUTPUT_PIXELS
from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path
from .reference_video import validate_storage_id
from .shot_production import MAX_RESULT_VERSIONS, NODE_KINDS, ShotProduction, ShotProductionError, StepSource, public_result_versions


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
    project_assets = ProjectAssets(root, reference_facts=(
        lambda project_id, asset_id: preproduction_asset_references(store.load(project_id), asset_id),
        lambda project_id, asset_id: timeline_asset_references(TimelineStore(root).load(project_id), asset_id),
    ))
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
            return project_assets.file(project_id, asset)
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

    def production_for(project_id: str, state: dict[str, Any]) -> ShotProduction:
        return ShotProduction(
            state,
            asset_available=lambda asset: project_assets.available(project_id, asset),
            artifact_available=lambda shot, step, artifact: _stored_file_available(
                store, project_id, "artifacts", shot["id"], step["id"], artifact.get("name"),
            ),
        )

    def decide(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ShotProductionError as error:
            fail(error.code, error.message, error.status)

    def step_source_path(project_id: str, state: dict[str, Any], shot_id: str, source: StepSource | None) -> Optional[Path]:
        if source is None:
            return None
        if source.kind == "asset":
            try:
                asset = project_assets.find_in(state, source.asset_id)
            except (KeyError, TypeError, ValueError):
                fail("preproduction_input_missing", "节点输入素材不存在。")
            return asset_path(project_id, asset)
        if source.kind == "artifact":
            return artifact_path(project_id, shot_id, source.step_id, source.artifact_name)
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
        asset_id = project_assets.new_id("reference")
        name = asset_id + suffix
        record = {"id": asset_id, "name": _display_name(getattr(reference, "originalName", "参考素材")), "kind": getattr(reference, "type", "video"),
                  "role": "reference", "file": name, "sourceReferenceId": source_id}
        for source_key, target_key in (("durationSeconds", "duration"), ("width", "width"), ("height", "height")):
            value = getattr(reference, source_key, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                record[target_key] = value
        with project_assets.install(project_id, state, source, record):
            pass
        return record

    def response(project_id: str, state: dict[str, Any]) -> dict[str, Any]:
        public = _public_state(project_id, state)
        by_id = project_assets.index_from(state)
        for asset in public["assets"]:
            asset["available"] = project_assets.available(project_id, by_id[asset["id"]])
        public["checks"] = production_for(project_id, state).delivery_checks()
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
                        if node["status"] in LOCAL_RUN_POLICY.active_statuses:
                            recovery = LOCAL_RUN_POLICY.recover_after_restart(node["status"])
                            node.update(status=recovery.status, error="服务重启中断了节点执行，可重试。", artifacts=[])
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
            production = production_for(project_id, state)
            decide(production.edit, body.brief.model_dump(), [shot.model_dump() for shot in body.shots])
            save(project_id, state)
            return response(project_id, state)

    def managed_asset(state, asset_id):
        try:
            return project_assets.find_in(state, asset_id)
        except (KeyError, TypeError, ValueError):
            fail("preproduction_asset_missing", "素材不存在。", 404)

    def references_for(project_id, state, asset_id):
        try:
            return project_assets.references(project_id, asset_id)
        except (AssetReferencesUnavailableError, OSError, ValueError, KeyError, TypeError):
            fail("asset_references_unavailable", "无法完整读取素材引用，暂不能删除。", 503)

    def editable_assets(project_id, revision):
        state = state_for(project_id)
        if revision != state["revision"]:
            fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
        if production_for(project_id, state).has_inflight():
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
            managed_asset(state, asset_id)
            project_assets.update_metadata(
                state, asset_id, name=body.name, notes=body.notes,
                save=lambda value: save(project_id, value),
            )
            return response(project_id, state)

    @router.post("/assets/{asset_id}/delete")
    def delete_asset(project_id: str, asset_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = editable_assets(project_id, body.revision)
            managed_asset(state, asset_id)
            try:
                project_assets.delete(project_id, state, asset_id, save=lambda value: save(project_id, value))
            except AssetInUseError as error:
                fail("asset_in_use", "素材仍被引用：" + "；".join(item["label"] for item in error.references[:5]))
            except AssetReferencesUnavailableError:
                fail("asset_references_unavailable", "无法完整读取素材引用，暂不能删除。", 503)
            except AssetFileUnavailableError:
                fail("preproduction_asset_unavailable", "素材文件不可用。", 409)
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
                production = production_for(project_id, state)
                if resultForShot is not None:
                    decide(production.prepare_result_attachment, resultForShot, revision)
                temporary = _receive_upload(root, file, suffix)
                metadata = _probe_media(temporary, suffix, ffprobe_path, ffmpeg_path)
                if resultForShot is not None and metadata["kind"] != "video":
                    fail("preproduction_result_invalid", "镜头结果必须上传视频。", 422)
                asset_id = project_assets.new_id()
                name = asset_id + suffix
                record = {"id": asset_id, "name": _display_name(file.filename), "kind": metadata["kind"], "role": role,
                          "file": name, **metadata}
                with project_assets.install(project_id, state, temporary, record, move=True):
                    if resultForShot is not None:
                        decide(production.attach_result, resultForShot, asset_id)
                    else:
                        state["revision"] += 1
                    save(project_id, state)
                temporary = None
                return response(project_id, state)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            await file.close()

    @router.get("/shots/{shot_id}/results/{asset_id}/cleanup-preview")
    def preview_candidate_removal(project_id: str, shot_id: str, asset_id: str):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            production = production_for(project_id, state)
            shot = decide(production.result_for_removal, shot_id, asset_id)
            return {"revision": state["revision"], "bytes": 0, "fileCount": 0,
                    "description": f'仅从镜头「{shot["title"]}」移除这一候选及人工检查记录。素材文件、其他镜头和时间线均保留；如需释放空间，请另行在素材库检查引用后删除。'}

    @router.post("/shots/{shot_id}/results/{asset_id}/remove")
    def remove_candidate(project_id: str, shot_id: str, asset_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            decide(production_for(project_id, state).remove_result, shot_id, asset_id)
            save(project_id, state)
            return response(project_id, state)

    @router.post("/shots/{shot_id}/results/{asset_id}/review")
    def review_result(project_id: str, shot_id: str, asset_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "方案已更新，请重新读取后检查。")
            production = production_for(project_id, state)
            decide(production.review_result, shot_id, asset_id)
            save(project_id, state)
            return response(project_id, state)

    @router.put("/shots/{shot_id}/results/{asset_id}/note")
    def update_result_note(project_id: str, shot_id: str, asset_id: str, body: ResultNote):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            decide(production_for(project_id, state).set_result_note, shot_id, asset_id, body.note.strip())
            save(project_id, state)
            return response(project_id, state)

    @router.put("/shots/{shot_id}/results/{asset_id}/adoption-reason")
    def update_adoption_reason(project_id: str, shot_id: str, asset_id: str, body: AdoptionReason):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body.revision != state["revision"]:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            decide(production_for(project_id, state).set_adoption_reason, shot_id, asset_id, body.reason.strip())
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
            if production_for(project_id, state).has_inflight():
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
                    asset_id = project_assets.new_id("toolkit")
                    name = asset_id + suffix
                    record = {"id": asset_id, "name": _display_name(label + suffix), "role": "motion", "file": name,
                              "sourceKey": source_key, **metadata}
                    with project_assets.install(project_id, state, source, record):
                        pass
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
            production = production_for(project_id, state)
            run_id = uuid4().hex
            prepared = decide(production.prepare_run, shot_id, node_id, run_id)
            source = step_source_path(project_id, state, shot_id, prepared.source)
            save(project_id, state)
            if not compute_queue.submit(
                "preproduction:" + run_id,
                project_id,
                lambda pid: process(pid, shot_id, node_id, run_id, source, prepared.kind, prepared.params),
            ):
                state = state_for(project_id)
                if decide(production_for(project_id, state).fail_run, shot_id, node_id, run_id, "本地处理队列不可用，请重试。"):
                    save(project_id, state)
            return response(project_id, state_for(project_id))

    def process(
        project_id: str,
        shot_id: str,
        node_id: str,
        run_id: str,
        source: Optional[Path],
        kind: str,
        params: dict[str, Any],
    ) -> None:
        run_dir: Optional[Path] = None
        try:
            with lock:
                state = state_for(project_id)
                if production_for(project_id, state).start_run(shot_id, node_id, run_id) is None:
                    return
                save(project_id, state)
            run_dir = store.path(project_id, "runs", run_id)
            run_dir.mkdir(parents=True, exist_ok=False)
            names = runner(kind, source, run_dir, params, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)
            if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
                raise ValueError("节点未生成有效产物")
            for name in names:
                validate_storage_id(name)
                _regular_file(run_dir / name)
            with lock:
                state = state_for(project_id)
                artifacts = [{"name": name, "url": f"/api/projects/{project_id}/preproduction/artifacts/{shot_id}/{node_id}/{name}"} for name in names]
                production = production_for(project_id, state)
                if not production.complete_run(shot_id, node_id, run_id, artifacts):
                    return
                destination = store.path(project_id, "artifacts", shot_id, node_id)
                if destination.exists():
                    shutil.rmtree(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(run_dir, destination)
                run_dir = None
                save(project_id, state)
        except Exception as error:
            with lock:
                try:
                    state = state_for(project_id)
                    if production_for(project_id, state).fail_run(shot_id, node_id, run_id, _safe_error(error)):
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
            asset = managed_asset(state, asset_id)
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
            production = production_for(project_id, state)
            report = production.delivery_checks()
            if any(item["level"] == "error" for item in report):
                fail("preproduction_delivery_blocked", "交付检查存在错误，请先修复。")
            public = response(project_id, state)
            referenced = production.referenced_assets()
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


def _find_node(state: dict[str, Any], shot_id: str, node_id: str, fail: Callable[..., None]) -> tuple[dict[str, Any], dict[str, Any]]:
    shot = next((item for item in state["shots"] if item["id"] == shot_id), None)
    if shot is None:
        fail("preproduction_shot_missing", "镜头不存在。", 404)
    node = next((item for item in shot["nodes"] if item["id"] == node_id), None)
    if node is None:
        fail("preproduction_node_missing", "节点不存在。", 404)
    return shot, node


def _stored_file_available(store: PreproductionStore, project_id: str, *parts: Any) -> bool:
    try:
        if any(not isinstance(part, str) for part in parts):
            return False
        _regular_file(store.path(project_id, *parts))
        return True
    except (OSError, ValueError):
        return False


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
