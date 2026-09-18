"""Independent preparation API for all detected video shots."""
import json
import math
import os
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import nullcontext
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from .depth_capture_storage import DEPTH_ARTIFACTS, DepthPreviewUnavailableError, open_validated_depth_artifact
from .local_preprocessing_storage import preprocessing_directory
from .person_controls import (
    PERSON_ARTIFACTS, PersonControlError, PersonControlStore, append_queued_run,
    control_status, environment_status, execute_person_run, latest_run, open_artifact,
    open_preview, projected_run, validate_run_artifacts,
    PersonArtifactStream, parse_single_byte_range, stream_person_artifact,
)
from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path
from .reference_video import validate_storage_id
from .shot_preparation import (
    ShotDraft,
    ShotPreparationState,
    ShotPreparationStore,
    ShotPrompts,
    TimelineOverride,
    empty_draft,
    load_scene_changes,
    scene_boundaries,
)


PERSON_WORKER_ROOT = Path(__file__).resolve().parents[1] / "person_worker"


class ShotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    notes: str = Field(max_length=12_000)
    prompts: ShotPrompts


class SavePreparation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    sourceId: str = Field(min_length=1, max_length=200)
    preprocessingId: str = Field(min_length=1, max_length=200)
    shots: list[ShotUpdate] = Field(max_length=3_000)


class AnalyzeShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    sourceId: str = Field(min_length=1, max_length=200)
    preprocessingId: str = Field(min_length=1, max_length=200)
    disclosureAccepted: bool


class StartPersonControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    sourceId: str = Field(min_length=1, max_length=200)
    preprocessingId: str = Field(min_length=1, max_length=200)


class TimelineContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    sourceId: str = Field(min_length=1, max_length=200)
    preprocessingId: str = Field(min_length=1, max_length=200)


class ApplyToolkitTimeline(TimelineContext):
    toolkitRunId: str = Field(min_length=1, max_length=100)
    cutRevision: int = Field(ge=0)


def create_shot_preparation_router(
    data_dir: Path,
    get_project: Callable[[str], Any],
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    analyze_shot: Optional[Callable[[Any, dict[str, Any]], dict[str, Any]]] = None,
    compute_queue: Any = None,
    person_worker_root: Path = PERSON_WORKER_ROOT,
    person_worker_python: Optional[str] = None,
    person_worker_script: Optional[Path] = None,
    person_model: Optional[Path] = None,
    source_lock: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/preparation")
    store = ShotPreparationStore(Path(data_dir))
    lock = RLock()
    busy: set[tuple[str, str]] = set()
    person_store = PersonControlStore(Path(data_dir))
    person_store.reconcile_interrupted()
    person_worker_root = Path(person_worker_root)
    person_worker_python = person_worker_python or str(person_worker_root / ".venv/bin/python")
    person_worker_script = Path(person_worker_script or person_worker_root / "run_person.py")
    person_model = Path(person_model or person_worker_root / "models/pose_landmarker_full.task")

    def person_environment() -> dict[str, Any]:
        # GET must remain cheap: model import/load happens only in the queued worker.
        return environment_status(person_worker_python, person_worker_script, person_model)

    def fail(code: str, message: str, status: int = 409) -> None:
        raise HTTPException(status_code=status, detail={"code": code, "message": message})

    def source_context(project_id: str) -> tuple[Any, Any, Any, Path, list[dict[str, Any]]]:
        project = _as_object(get_project(project_id))
        reference = getattr(project, "referenceMedia", None)
        preprocessing = getattr(project, "localPreprocessing", None)
        if (
            reference is None
            or getattr(reference, "type", None) != "video"
            or preprocessing is None
            or getattr(preprocessing, "status", None) != "completed"
            or getattr(preprocessing, "mediaType", None) != "video"
            or getattr(preprocessing, "sourceReferenceMediaId", None) != getattr(reference, "id", None)
        ):
            fail("shot_preparation_preconditions_not_met", "仅已完成本地预处理的当前参考视频可以准备镜头。")
        duration = getattr(reference, "durationSeconds", None)
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            fail("shot_preparation_preconditions_not_met", "参考视频时长无效。")
        if not managed_reference_media_is_safe(Path(data_dir), project.id, reference):
            fail("shot_preparation_source_unavailable", "当前参考视频文件不可用或不安全。")
        try:
            source = resolve_reference_media_path(Path(data_dir), project.id, reference)
            artifacts = preprocessing_directory(Path(data_dir), project.id, preprocessing.id)
            scene_path = artifacts / "scene-changes.json"
            _ensure_safe_child(Path(data_dir), scene_path)
            changes = load_scene_changes(scene_path)
            scene_boundaries(float(duration), changes)
        except (OSError, ValueError) as error:
            fail("shot_preparation_artifacts_invalid", "本地镜头检测产物不可用。")
        return project, reference, preprocessing, source, changes

    def effective_changes(state: ShotPreparationState, detected_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if state.timelineOverride is None:
            return detected_changes
        return [{"timeSeconds": cut} for cut in state.timelineOverride.cuts]

    def timeline(project_id: str) -> tuple[Any, Any, Any, Path, ShotPreparationState, list[dict[str, Any]]]:
        project, reference, preprocessing, source, detected_changes = source_context(project_id)
        state = state_for(project, reference, preprocessing)
        changes = effective_changes(state, detected_changes)
        try:
            scene_boundaries(float(reference.durationSeconds), changes)
        except ValueError:
            fail("shot_preparation_artifacts_invalid", "镜头时间线记录无效。", 503)
        return project, reference, preprocessing, source, state, changes

    def state_for(project: Any, reference: Any, preprocessing: Any) -> ShotPreparationState:
        try:
            return store.load(project.id, reference.id, preprocessing.id)
        except OSError:
            fail("shot_preparation_storage_failed", "镜头准备草稿无法读取。", 503)

    def person_timeline_id(preprocessing: Any, state: ShotPreparationState) -> str:
        # Preserve all pre-linkage person-control runs. A changed timeline gets a
        # new namespace, so an old worker cannot surface data for a reused shot id.
        return preprocessing.id if state.timelineEpoch == 0 else f"{preprocessing.id}-timeline-{state.timelineEpoch}"

    def range_key(start: float, end: float) -> tuple[float, float]:
        return round(start, 9), round(end, 9)

    def replace_timeline_drafts(reference: Any, state: ShotPreparationState, current_changes: list[dict[str, Any]], next_changes: list[dict[str, Any]]) -> bool:
        current_boundaries = scene_boundaries(float(reference.durationSeconds), current_changes)
        next_boundaries = scene_boundaries(float(reference.durationSeconds), next_changes)
        drafts_by_range = {
            range_key(start, end): state.drafts.get(f"shot-{index:03d}", empty_draft())
            for index, (start, end) in enumerate(zip(current_boundaries, current_boundaries[1:]), start=1)
        }
        state.drafts = {
            f"shot-{index:03d}": drafts_by_range.get(range_key(start, end), empty_draft())
            for index, (start, end) in enumerate(zip(next_boundaries, next_boundaries[1:]), start=1)
        }
        return current_boundaries != next_boundaries

    def validate_context(state: ShotPreparationState, reference: Any, preprocessing: Any, body: TimelineContext) -> None:
        if body.sourceId != reference.id or body.preprocessingId != preprocessing.id or body.revision != state.revision:
            fail("shot_preparation_stale", "参考素材、预处理或镜头草稿已变化，请刷新后重试。")

    def validate_query_context(state: ShotPreparationState, reference: Any, preprocessing: Any, source_id: Optional[str], preprocessing_id: Optional[str], revision: Optional[int]) -> None:
        if source_id is None and preprocessing_id is None and revision is None:
            return
        if source_id is None or preprocessing_id is None or revision is None:
            fail("shot_preparation_stale", "镜头准备版本不完整，请刷新后重试。", 422)
        if source_id != reference.id or preprocessing_id != preprocessing.id or revision != state.revision:
            fail("shot_preparation_stale", "参考素材、预处理或镜头草稿已变化，请刷新后重试。")

    def toolkit_scene_snapshot(reference: Any, run_id: str, asset_id: Any, created_at: Any, scenes_path: Path, source_label: str) -> dict[str, Any]:
        if asset_id != f"reference:{reference.id}":
            fail("shot_preparation_toolkit_source_mismatch", "所选视频工具分镜并非当前参考视频的结果。")
        if scenes_path.is_symlink() or not scenes_path.is_file():
            raise OSError()
        scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
        cuts, revision, duration = scenes.get("cuts"), scenes.get("revision", 0), scenes.get("duration")
        if (
            not isinstance(cuts, list) or not isinstance(revision, int) or revision < 0
            or not isinstance(duration, (int, float)) or isinstance(duration, bool)
            or not math.isfinite(duration) or abs(float(duration) - float(reference.durationSeconds)) > 0.001
        ):
            raise ValueError()
        if any(not isinstance(cut, (int, float)) or isinstance(cut, bool) or not math.isfinite(cut) for cut in cuts):
            raise ValueError()
        changes = [{"timeSeconds": float(cut)} for cut in cuts]
        boundaries = scene_boundaries(float(reference.durationSeconds), changes)
        if len(boundaries) != len(cuts) + 2 or boundaries[1:-1] != [float(cut) for cut in cuts]:
            raise ValueError()
        return {
            "toolkitRunId": run_id, "cutRevision": revision, "cuts": [float(cut) for cut in cuts], "createdAt": created_at,
            "label": f"{source_label} · {run_id.split(':')[1][:8] if run_id.startswith('pipeline:') else run_id[:8]} · {len(cuts) + 1} 镜头",
        }

    def toolkit_scene(project: Any, reference: Any, run_id: str) -> dict[str, Any]:
        try:
            root = Path(os.path.abspath(data_dir))
            if run_id.startswith("pipeline:"):
                parts = run_id.split(":")
                if len(parts) != 3:
                    raise ValueError()
                _, pipeline_id, step_id = parts
                validate_storage_id(pipeline_id); validate_storage_id(step_id)
                pipeline_path = root / "project-files" / project.id / "toolkit" / "pipelines" / pipeline_id / "state.json"
                _ensure_safe_child(root, pipeline_path)
                if pipeline_path.is_symlink() or not pipeline_path.is_file():
                    raise OSError()
                pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
                if not isinstance(pipeline, dict) or pipeline.get("id") != pipeline_id or not isinstance(pipeline.get("steps"), list):
                    raise ValueError()
                step = next((item for item in pipeline["steps"] if isinstance(item, dict) and item.get("id") == step_id), None)
                if step is None or step.get("kind") != "scenes" or step.get("status") != "completed" or "scenes.json" not in step.get("artifacts", []):
                    raise ValueError()
                directory = step.get("directory")
                if not isinstance(directory, str):
                    raise ValueError()
                validate_storage_id(directory)
                scenes_path = pipeline_path.parent / directory / "scenes.json"
                _ensure_safe_child(root, scenes_path)
                return toolkit_scene_snapshot(reference, run_id, pipeline.get("assetId"), pipeline.get("createdAt"), scenes_path, "流水线分镜")
            validate_storage_id(run_id)
            run_path = root / "project-files" / project.id / "toolkit" / run_id / "state.json"
            scenes_path = run_path.parent / "scenes.json"
            _ensure_safe_child(root, run_path); _ensure_safe_child(root, scenes_path)
            if run_path.is_symlink() or not run_path.is_file():
                raise OSError()
            run = json.loads(run_path.read_text(encoding="utf-8"))
            if (
                not isinstance(run, dict) or run.get("id") != run_id or run.get("status") != "completed"
                or run.get("kind") != "scenes" or "scenes.json" not in run.get("artifacts", [])
            ):
                raise ValueError()
            return toolkit_scene_snapshot(reference, run_id, run.get("assetId"), run.get("createdAt"), scenes_path, "视频工具分镜")
        except HTTPException:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            fail("shot_preparation_toolkit_unavailable", "所选视频工具分镜结果不可用。")

    def toolkit_scenes(project: Any, reference: Any) -> list[dict[str, Any]]:
        root = Path(os.path.abspath(data_dir))
        directory = root / "project-files" / project.id / "toolkit"
        try:
            _ensure_safe_child(root, directory)
            if not directory.is_dir() or directory.is_symlink():
                return []
            values = []
            for run_path in directory.glob("*/state.json"):
                try:
                    _ensure_safe_child(root, run_path)
                    values.append(toolkit_scene(project, reference, run_path.parent.name))
                except HTTPException as error:
                    if error.detail.get("code") != "shot_preparation_toolkit_source_mismatch":
                        continue
                except (OSError, ValueError):
                    continue
            pipeline_directory = directory / "pipelines"
            _ensure_safe_child(root, pipeline_directory)
            if pipeline_directory.is_dir() and not pipeline_directory.is_symlink():
                for pipeline_path in pipeline_directory.glob("*/state.json"):
                    try:
                        _ensure_safe_child(root, pipeline_path)
                        pipeline_id = pipeline_path.parent.name
                        validate_storage_id(pipeline_id)
                        pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
                        for step in pipeline.get("steps", []) if isinstance(pipeline, dict) else []:
                            if isinstance(step, dict) and isinstance(step.get("id"), str) and step.get("kind") == "scenes" and step.get("status") == "completed":
                                values.append(toolkit_scene(project, reference, f"pipeline:{pipeline_id}:{step['id']}"))
                    except HTTPException as error:
                        if error.detail.get("code") != "shot_preparation_toolkit_source_mismatch":
                            continue
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        continue
            return sorted(values, key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        except OSError:
            return []

    def shot_records(reference: Any, preprocessing: Any, changes: list[dict[str, Any]], state: ShotPreparationState, project: Any) -> list[dict[str, Any]]:
        boundaries = scene_boundaries(float(reference.durationSeconds), changes)
        depth = depth_control_status(Path(data_dir), project, reference)
        environment = person_environment()
        shots = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
            shot_id = f"shot-{index:03d}"
            draft = state.drafts.get(shot_id, empty_draft())
            artifact_error = False
            try:
                person_state = person_store.load(project.id, reference.id, person_timeline_id(preprocessing, state), shot_id)
                person_run = latest_run(person_state)
                if person_run is not None and person_run["status"] == "completed":
                    # A persisted state is only a projection; revalidate bytes before reporting readiness.
                    validated_quality = validate_run_artifacts(person_store.run_directory(project.id, reference.id, person_timeline_id(preprocessing, state), shot_id, person_run["runId"]), ffprobe_path=ffprobe_path)
                    if person_run.get("quality") != validated_quality:
                        raise PersonControlError("人物控制质量记录与产物不一致")
                person = projected_run(person_state, environment, base_url=f"/api/projects/{project.id}/preparation/shots/{shot_id}/person-control")
            except (OSError, ValueError, PersonControlError):
                artifact_error = True
                person_state = None
                person = {"status": "failed", "error": "人物控制任务记录无法读取。", "quality": None, "runId": None, "outputs": {}}
            pose = "failed" if artifact_error else control_status(person_state, environment)
            shots.append({
                "id": shot_id,
                "startSeconds": start,
                "endSeconds": end,
                "representativeSeconds": (start + end) / 2,
                "notes": draft.notes,
                "prompts": draft.prompts.model_dump(),
                "controls": {"depth": depth, "pose": pose, "mask": pose},
                "personControl": person,
            })
        return shots

    def view(project_id: str) -> tuple[Any, Any, Any, Path, ShotPreparationState, list[dict[str, Any]]]:
        project, reference, preprocessing, source, state, changes = timeline(project_id)
        shots = shot_records(reference, preprocessing, changes, state, project)
        return project, reference, preprocessing, source, state, shots

    def response(project: Any, reference: Any, preprocessing: Any, state: ShotPreparationState, shots: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "sourceId": reference.id,
            "preprocessingId": preprocessing.id,
            "revision": state.revision,
            "timelineOverride": None if state.timelineOverride is None else {
                "toolkitRunId": state.timelineOverride.toolkitRunId,
                "cutRevision": state.timelineOverride.cutRevision,
            },
            "toolkitScenes": toolkit_scenes(project, reference),
            "canAnalyze": getattr(project, "semanticAnalysis", None) is not None,
            "personControlEnvironment": person_environment(),
            "shots": shots,
        }

    def person_job(project_id: str) -> None:
        """Drain queued current-source shots under the one shared local worker slot."""
        while True:
            with source_lock or nullcontext(), lock:
                try:
                    project, reference, preprocessing, source, preparation_state, shots = view(project_id)
                except HTTPException:
                    return
                candidate = None
                for shot in shots:
                    try:
                        timeline_id = person_timeline_id(preprocessing, preparation_state)
                        state = person_store.load(project.id, reference.id, timeline_id, shot["id"])
                    except (OSError, ValueError, PersonControlError):
                        continue
                    run = latest_run(state)
                    if run is not None and run["status"] == "queued":
                        run["status"] = "running"
                        run["error"] = None
                        person_store.save(project.id, reference.id, timeline_id, shot["id"], state)
                        candidate = (reference.id, timeline_id, source, shot, run["runId"])
                        break
                if candidate is None:
                    return
            source_id, preprocessing_id, original_source, shot, run_id = candidate

            def current() -> bool:
                with source_lock or nullcontext(), lock:
                    try:
                        active_project, active_reference, active_preprocessing, active_source, active_state, active_shots = view(project_id)
                    except HTTPException:
                        return False
                    active = next((item for item in active_shots if item["id"] == shot["id"]), None)
                    return bool(
                        active is not None and active_project.id == project_id and active_reference.id == source_id
                        and person_timeline_id(active_preprocessing, active_state) == preprocessing_id and active_source == original_source
                        and active["startSeconds"] == shot["startSeconds"] and active["endSeconds"] == shot["endSeconds"]
                    )

            quality, error = execute_person_run(
                store=person_store, project_id=project_id, source_id=source_id, preprocessing_id=preprocessing_id,
                shot_id=shot["id"], run_id=run_id, source=original_source, start_seconds=shot["startSeconds"],
                end_seconds=shot["endSeconds"], ffmpeg_path=ffmpeg_path, worker_python=person_worker_python,
                worker_script=person_worker_script, model=person_model, is_current=current, ffprobe_path=ffprobe_path,
            )
            with source_lock or nullcontext(), lock:
                try:
                    state = person_store.load(project_id, source_id, preprocessing_id, shot["id"])
                    run = next((item for item in state["runs"] if item["runId"] == run_id), None) if state else None
                    if run is not None:
                        run["status"] = "completed" if quality is not None else "failed"
                        run["quality"] = quality
                        run["error"] = error
                        person_store.save(project_id, source_id, preprocessing_id, shot["id"], state)
                except (OSError, ValueError, PersonControlError):
                    pass

    @router.post("/timeline/apply")
    def apply_toolkit_timeline(project_id: str, body: ApplyToolkitTimeline) -> dict[str, Any]:
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, _, state, current_changes = timeline(project_id)
            validate_context(state, reference, preprocessing, body)
            selected = toolkit_scene(project, reference, body.toolkitRunId)
            if selected["cutRevision"] != body.cutRevision:
                fail("shot_preparation_toolkit_cuts_stale", "视频工具切点已更新，请刷新后重新应用。")
            next_changes = [{"timeSeconds": cut} for cut in selected["cuts"]]
            changed = replace_timeline_drafts(reference, state, current_changes, next_changes)
            state.timelineOverride = TimelineOverride(
                toolkitRunId=selected["toolkitRunId"], cutRevision=selected["cutRevision"], cuts=selected["cuts"],
            )
            if changed:
                state.timelineEpoch += 1
            state.revision += 1
            try:
                store.save(project.id, state)
            except OSError:
                fail("shot_preparation_storage_failed", "镜头准备草稿无法保存。", 503)
            shots = shot_records(reference, preprocessing, next_changes, state, project)
            return response(project, reference, preprocessing, state, shots)

    @router.post("/timeline/restore")
    def restore_detected_timeline(project_id: str, body: TimelineContext) -> dict[str, Any]:
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, _, state, current_changes = timeline(project_id)
            validate_context(state, reference, preprocessing, body)
            if state.timelineOverride is None:
                return response(project, reference, preprocessing, state, shot_records(reference, preprocessing, current_changes, state, project))
            _, _, _, _, detected_changes = source_context(project_id)
            changed = replace_timeline_drafts(reference, state, current_changes, detected_changes)
            state.timelineOverride = None
            if changed:
                state.timelineEpoch += 1
            state.revision += 1
            try:
                store.save(project.id, state)
            except OSError:
                fail("shot_preparation_storage_failed", "镜头准备草稿无法保存。", 503)
            return response(project, reference, preprocessing, state, shot_records(reference, preprocessing, detected_changes, state, project))

    @router.get("")
    def get_preparation(project_id: str) -> dict[str, Any]:
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, _, state, shots = view(project_id)
            return response(project, reference, preprocessing, state, shots)

    @router.post("/shots/{shot_id}/person-control", response_model=None)
    def start_person_control(project_id: str, shot_id: str, body: StartPersonControl):
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, _, state, shots = view(project_id)
            if body.sourceId != reference.id or body.preprocessingId != preprocessing.id or body.revision != state.revision:
                fail("shot_preparation_stale", "参考素材、预处理或镜头草稿已变化，请刷新后重试。")
            if shot_id not in {item["id"] for item in shots}:
                fail("shot_preparation_shot_not_found", "镜头不存在。", 404)
            if compute_queue is None:
                fail("person_control_unavailable", "本地人物控制队列不可用。", 503)
            if getattr(compute_queue, "is_active", lambda *_: False)("person_control", project.id):
                fail("person_control_in_progress", "该项目已有镜头正在提取人物控制，请完成后再开始下一镜头。")
            try:
                timeline_id = person_timeline_id(preprocessing, state)
                stored = person_store.load(project.id, reference.id, timeline_id, shot_id)
                if stored is None:
                    stored = person_store.new_run(reference.id, timeline_id, shot_id)
                append_queued_run(stored)
                person_store.save(project.id, reference.id, timeline_id, shot_id, stored)
            except PersonControlError as error:
                fail("person_control_in_progress", str(error))
            accepted = compute_queue.submit("person_control", project.id, person_job)
            if not accepted:
                current = latest_run(stored)
                current["status"] = "failed"
                current["error"] = "本地人物控制队列不可用，请重试。"
                person_store.save(project.id, reference.id, timeline_id, shot_id, stored)
                fail("person_control_unavailable", "本地人物控制队列不可用。", 503)
            return JSONResponse(status_code=202, content=response(project, reference, preprocessing, state, shot_records(reference, preprocessing, timeline(project_id)[5], state, project)))

    @router.get("/shots/{shot_id}/person-control/{kind}", response_model=None)
    def get_person_control_preview(project_id: str, shot_id: str, kind: str, request: Request, runId: str = Query(min_length=1, max_length=100)):
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, _, state, shots = view(project_id)
            if shot_id not in {item["id"] for item in shots}:
                fail("shot_preparation_shot_not_found", "镜头不存在。", 404)
            try:
                descriptor, size = open_preview(person_store, project_id=project.id, source_id=reference.id,
                    preprocessing_id=person_timeline_id(preprocessing, state), shot_id=shot_id, run_id=runId, kind=kind, ffprobe_path=ffprobe_path)
            except (OSError, ValueError, PersonControlError):
                fail("person_control_preview_unavailable", "人物控制预览不可用。", 404)

        try:
            start, length, partial = parse_single_byte_range(request.headers.get("range"), size)
        except ValueError:
            os.close(descriptor)
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        stream = PersonArtifactStream(descriptor, start, length)
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store", "Content-Length": str(length)}
        if partial:
            headers["Content-Range"] = f"bytes {start}-{start + length - 1}/{size}"
        return StreamingResponse(
            stream_person_artifact(stream), status_code=206 if partial else 200,
            media_type="video/mp4", headers=headers, background=BackgroundTask(stream.aclose),
        )

    @router.put("")
    def save_preparation(project_id: str, body: SavePreparation) -> dict[str, Any]:
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, _, state, shots = view(project_id)
            if body.sourceId != reference.id or body.preprocessingId != preprocessing.id:
                fail("shot_preparation_stale", "参考素材或预处理已变化，请刷新后重试。")
            if body.revision != state.revision:
                fail("shot_preparation_conflict", "镜头草稿已发生变化，请刷新后重试。")
            expected_ids = {shot["id"] for shot in shots}
            received_ids = [shot.id for shot in body.shots]
            if set(received_ids) != expected_ids or len(received_ids) != len(expected_ids):
                fail("shot_preparation_shots_invalid", "保存内容必须包含当前时间线中的每个镜头，且不能重复。", 422)
            state.drafts = {
                item.id: ShotDraft(notes=item.notes, prompts=item.prompts)
                for item in body.shots
            }
            state.revision += 1
            try:
                store.save(project.id, state)
            except OSError:
                fail("shot_preparation_storage_failed", "镜头准备草稿无法保存。", 503)
            return response(project, reference, preprocessing, state, shot_records(reference, preprocessing, timeline(project_id)[5], state, project))

    @router.get("/shots/{shot_id}/frame", response_model=None)
    def get_representative_frame(project_id: str, shot_id: str, sourceId: Optional[str] = Query(default=None), preprocessingId: Optional[str] = Query(default=None), revision: Optional[int] = Query(default=None)):
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, source, state, shots = view(project_id)
            validate_query_context(state, reference, preprocessing, sourceId, preprocessingId, revision)
            shot = next((item for item in shots if item["id"] == shot_id), None)
            if shot is None:
                fail("shot_preparation_shot_not_found", "镜头不存在。", 404)
            frame = _new_temp_path(".jpg")
            try:
                _extract_frame(
                    source, shot["startSeconds"], shot["endSeconds"], shot["representativeSeconds"], frame,
                    ffmpeg_path,
                )
            except ShotFrameError as error:
                frame.unlink(missing_ok=True)
                fail(error.code, error.message, error.status)
        return FileResponse(
            frame,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store"},
            background=BackgroundTask(frame.unlink, missing_ok=True),
        )

    @router.post("/shots/{shot_id}/analyze")
    def analyze(project_id: str, shot_id: str, body: AnalyzeShot) -> dict[str, Any]:
        if body.disclosureAccepted is not True:
            fail("shot_analysis_disclosure_required", "请先确认向所选分析服务发送镜头分析输入。", 422)
        key = (project_id, shot_id)
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, _, state, shots = view(project_id)
            if getattr(project, "semanticAnalysis", None) is None:
                fail("shot_analysis_unavailable", "请先选择并完成全局语义分析配置。")
            if analyze_shot is None:
                fail("shot_analysis_unavailable", "镜头分析服务暂不可用。", 503)
            if body.sourceId != reference.id or body.preprocessingId != preprocessing.id or body.revision != state.revision:
                fail("shot_preparation_stale", "参考素材、预处理或镜头草稿已变化，请刷新后重试。")
            shot = next((item for item in shots if item["id"] == shot_id), None)
            if shot is None:
                fail("shot_preparation_shot_not_found", "镜头不存在。", 404)
            if key in busy:
                fail("shot_analysis_in_progress", "该镜头正在分析，请稍候。")
            busy.add(key)
        try:
            try:
                result = ShotDraft.model_validate(analyze_shot(project, shot))
            except Exception as error:
                if isinstance(error, HTTPException):
                    raise
                fail("shot_analysis_failed", "镜头分析失败，原有草稿未被修改。", 502)
            with source_lock or nullcontext(), lock:
                current_project, current_reference, current_preprocessing, _, current_state, current_shots = view(project_id)
                if (
                    current_reference.id != body.sourceId
                    or current_preprocessing.id != body.preprocessingId
                    or current_state.revision != body.revision
                ):
                    fail("shot_preparation_stale", "分析期间镜头草稿已变化，结果未写入。")
                if shot_id not in {item["id"] for item in current_shots}:
                    fail("shot_preparation_shot_not_found", "镜头不存在。", 404)
                current_state.drafts[shot_id] = result
                current_state.revision += 1
                try:
                    store.save(current_project.id, current_state)
                except OSError:
                    fail("shot_preparation_storage_failed", "镜头准备草稿无法保存。", 503)
                return response(
                    current_project, current_reference, current_preprocessing, current_state,
                    shot_records(current_reference, current_preprocessing, timeline(project_id)[5], current_state, current_project),
                )
        finally:
            with lock:
                busy.discard(key)

    @router.get("/package", response_model=None)
    def package(project_id: str, sourceId: Optional[str] = Query(default=None), preprocessingId: Optional[str] = Query(default=None), revision: Optional[int] = Query(default=None)):
        with source_lock or nullcontext(), lock:
            project, reference, preprocessing, source, state, shots = view(project_id)
            validate_query_context(state, reference, preprocessing, sourceId, preprocessingId, revision)
            archive_path = _new_temp_path(".zip")
            try:
                with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                    timeline_payload = response(project, reference, preprocessing, state, shots)
                    archive.writestr("timeline.json", json.dumps(timeline_payload, ensure_ascii=False, indent=2))
                    for shot in shots:
                        archive.writestr(
                            f"shots/{shot['id']}.json",
                            json.dumps({key: shot[key] for key in ("id", "notes", "prompts")}, ensure_ascii=False, indent=2),
                        )
                        frame = _new_temp_path(".jpg")
                        try:
                            _extract_frame(
                                source, shot["startSeconds"], shot["endSeconds"], shot["representativeSeconds"], frame,
                                ffmpeg_path,
                            )
                            archive.write(frame, f"frames/{shot['id']}.jpg")
                        finally:
                            frame.unlink(missing_ok=True)
                    capture = active_depth_capture(project, reference)
                    depth_included = _write_depth_assets(archive, Path(data_dir), project, capture) if capture else False
                    person_controls = []
                    for shot in shots:
                        control = shot["personControl"]
                        included = _write_person_assets(
                            archive, person_store, project_id=project.id, source_id=reference.id,
                            preprocessing_id=person_timeline_id(preprocessing, state), shot_id=shot["id"], run_id=control["runId"],
                            status=control["status"], ffprobe_path=ffprobe_path,
                        )
                        person_controls.append({
                            "shotId": shot["id"], "runId": control["runId"], "status": control["status"],
                            "quality": control["quality"], "included": included,
                        })
                    prepared_shot_count = sum(
                        all(value.strip() for value in shot["prompts"].values())
                        for shot in shots
                    )
                    manifest = {
                        "schemaVersion": 1,
                        "sourceId": reference.id,
                        "preprocessingId": preprocessing.id,
                        "revision": state.revision,
                        "shotCount": len(shots),
                        "depth": _depth_manifest(capture, depth_included),
                        "controls": {
                            "pose": _package_person_status(shots, "pose"),
                            "mask": _package_person_status(shots, "mask"),
                        },
                        "personControls": {"sourceId": reference.id, "shots": person_controls},
                        "promptReadiness": {
                            "allShotsPrepared": bool(shots) and prepared_shot_count == len(shots),
                            "preparedShotCount": prepared_shot_count,
                        },
                    }
                    analysis = getattr(project, "semanticAnalysis", None)
                    if analysis is not None:
                        manifest["globalSemanticAnalysis"] = {"scope": "global_only", "id": getattr(analysis, "id", None)}
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    archive.writestr("README.txt", "镜头时间线覆盖完整参考视频时长；本包不包含原始参考视频。\n"
                        "controls/ 下仅包含已验证完成的人物控制素材（pose、mask 和 overlay）。镜头草稿和提示词是可编辑候选内容，不代表生成已执行或已验证可用。\n")
            except ShotFrameError as error:
                archive_path.unlink(missing_ok=True)
                fail(error.code, error.message, error.status)
            except (OSError, DepthPreviewUnavailableError):
                archive_path.unlink(missing_ok=True)
                fail("shot_preparation_package_failed", "镜头准备包无法生成。", 503)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=f"shot-preparation-{project_id}-v{state.revision}.zip",
            background=BackgroundTask(archive_path.unlink, missing_ok=True),
        )

    return router


class ShotFrameError(Exception):
    def __init__(self, code: str, message: str, status: int):
        self.code = code
        self.message = message
        self.status = status


def _new_temp_path(suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".shot-preparation-", suffix=suffix)
    os.close(descriptor)
    return Path(raw_path)


def _extract_frame(
    source: Path,
    start_seconds: float,
    end_seconds: float,
    representative_seconds: float,
    target: Path,
    ffmpeg_path: str,
) -> None:
    target.unlink(missing_ok=True)
    duration = end_seconds - start_seconds
    offset = representative_seconds - start_seconds
    if duration <= 0 or offset < 0 or offset >= duration:
        raise ShotFrameError("shot_frame_extraction_failed", "镜头时间范围无效。", 422)
    try:
        result = _run_frame_command(
            ffmpeg_path, source, start_seconds, duration,
            f"trim=start=0:end={duration:.6f},select=gte(t\\,{offset:.6f})", target,
        )
        # A very short VFR segment can contain no frame at or after its midpoint.
        # In that case retain the segment boundary and use its first decodable frame.
        if result.returncode == 0 and not _valid_frame(target):
            result = _run_frame_command(
                ffmpeg_path, source, start_seconds, duration,
                f"trim=start=0:end={duration:.6f}", target,
            )
    except FileNotFoundError as error:
        raise ShotFrameError("ffmpeg_unavailable", "本地未找到 FFmpeg，无法提取镜头代表帧。", 503) from error
    except subprocess.TimeoutExpired as error:
        raise ShotFrameError("shot_frame_extraction_failed", "镜头代表帧提取超时。", 422) from error
    except OSError as error:
        raise ShotFrameError("shot_frame_extraction_failed", "镜头代表帧提取失败。", 503) from error
    if result.returncode != 0 or not _valid_frame(target):
        raise ShotFrameError("shot_frame_extraction_failed", "镜头代表帧提取失败。", 422)


def _run_frame_command(
    ffmpeg_path: str, source: Path, start_seconds: float, duration: float, video_filter: str, target: Path,
) -> subprocess.CompletedProcess[bytes]:
    target.unlink(missing_ok=True)
    return subprocess.run([
        ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", f"{start_seconds:.6f}",
        "-i", str(source), "-map", "0:V:0", "-vf", video_filter,
        "-frames:v", "1", "-an", "-q:v", "3", str(target),
    ], capture_output=True, timeout=30, check=False)


def _valid_frame(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_size > 0


def _ensure_safe_child(data_dir: Path, path: Path) -> None:
    root = Path(os.path.abspath(data_dir))
    if root.is_symlink():
        raise OSError("数据目录无效")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise OSError("路径超出数据目录") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise OSError("路径包含符号链接")


def _as_object(value: Any) -> Any:
    if isinstance(value, Mapping):
        return SimpleNamespace(**{key: _as_object(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_as_object(item) for item in value]
    return value


def active_depth_capture(project: Any, reference: Any) -> Any:
    active_id = getattr(project, "activeDepthCaptureId", None)
    for capture in getattr(project, "depthCaptures", []):
        if (
            getattr(capture, "id", None) == active_id
            and getattr(capture, "sourceReferenceVideoId", None) == getattr(reference, "id", None)
            and getattr(capture, "status", None) in {"completed", "failed"}
        ):
            return capture
    return None


def depth_control_status(data_dir: Path, project: Any, reference: Any) -> str:
    capture = active_depth_capture(project, reference)
    if capture is None:
        return "missing"
    if getattr(capture, "status", None) == "failed":
        return "failed"
    quality = getattr(getattr(capture, "qualityAssessment", None), "status", None)
    if quality == "failed":
        return "failed"
    if quality == "review_required" and not getattr(capture, "reviewConfirmedAt", None):
        return "review_required"
    if quality in {"passed", "review_required"} and _depth_assets_available(data_dir, project, capture):
        return "available"
    return "missing"


def _depth_assets_available(data_dir: Path, project: Any, capture: Any) -> bool:
    try:
        for name in DEPTH_ARTIFACTS:
            descriptor, _ = open_validated_depth_artifact(
                artifact=name, data_dir=data_dir, project_id=project.id,
                capture_id=capture.id, source_reference_video_id=capture.sourceReferenceVideoId,
                algorithm=getattr(capture, "algorithmVersion", 1),
            )
            os.close(descriptor)
        return True
    except (OSError, DepthPreviewUnavailableError):
        return False


def _write_depth_assets(archive: zipfile.ZipFile, data_dir: Path, project: Any, capture: Any) -> bool:
    descriptors: list[tuple[str, int]] = []
    try:
        for name in DEPTH_ARTIFACTS:
            descriptor, _ = open_validated_depth_artifact(
                artifact=name, data_dir=data_dir, project_id=project.id, capture_id=capture.id,
                source_reference_video_id=capture.sourceReferenceVideoId,
                algorithm=getattr(capture, "algorithmVersion", 1),
            )
            descriptors.append((name, descriptor))
    except (OSError, DepthPreviewUnavailableError):
        for _, descriptor in descriptors:
            os.close(descriptor)
        return False
    try:
        for name, descriptor in descriptors:
            with os.fdopen(descriptor, "rb") as source, archive.open(f"depth/{name}", "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        return True
    finally:
        # Descriptors passed to fdopen are closed by their context managers; retain this
        # guard only for a write error before a later descriptor is consumed.
        for _, descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_person_assets(
    archive: zipfile.ZipFile, store: PersonControlStore, *, project_id: str, source_id: str,
    preprocessing_id: str, shot_id: str, run_id: Any, status: str, ffprobe_path: str,
) -> bool:
    if status != "completed" or not isinstance(run_id, str):
        return False
    descriptors: list[tuple[str, int]] = []
    try:
        for name in PERSON_ARTIFACTS:
            descriptor, _ = open_artifact(
                store, project_id=project_id, source_id=source_id, preprocessing_id=preprocessing_id,
                shot_id=shot_id, run_id=run_id, artifact=name, ffprobe_path=ffprobe_path,
            )
            descriptors.append((name, descriptor))
    except (OSError, PersonControlError, ValueError):
        for _, descriptor in descriptors:
            os.close(descriptor)
        return False
    try:
        for name, descriptor in descriptors:
            with os.fdopen(descriptor, "rb") as source, archive.open(f"controls/{shot_id}/{name}", "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        return True
    finally:
        for _, descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _package_person_status(shots: list[dict[str, Any]], control: str) -> str:
    statuses = {shot["controls"][control] for shot in shots}
    if "failed" in statuses:
        return "failed"
    if "review_required" in statuses:
        return "review_required"
    if statuses == {"available"}:
        return "available"
    if "running" in statuses:
        return "running"
    if "unavailable" in statuses:
        return "unavailable"
    return "missing"


def _depth_manifest(capture: Any, included: bool) -> dict[str, Any]:
    if capture is None:
        return {"included": False, "status": "missing"}
    return {
        "included": included,
        "captureId": getattr(capture, "id", None),
        "status": getattr(getattr(capture, "qualityAssessment", None), "status", getattr(capture, "status", None)),
    }
