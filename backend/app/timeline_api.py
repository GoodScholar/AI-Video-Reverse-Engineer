"""Project-scoped persistence and queued rendering endpoints for timelines."""
from __future__ import annotations

import os
import hashlib
import json
import re
import stat
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .durable_runs import LOCAL_RUN_POLICY, freeze_run_input
from .history_cleanup import inventory, remove_history_files
from .audio_waveform import WaveformCache
from .preproduction import PreproductionStore
from .project_assets import ProjectAssets
from .reference_video import validate_storage_id
from .render_inputs import RenderInputError, RenderInputPreparation
from .timeline import TimelineStore, has_audible_audio, has_visible_video, validate_workspace


ACTIVE_STATUSES = LOCAL_RUN_POLICY.active_statuses
TERMINAL_STATUSES = LOCAL_RUN_POLICY.allowed_statuses - ACTIVE_STATUSES


def create_timeline_router(data_dir, get_project, compute_queue, *, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", source_lock=None, renderer=None):
    root = Path(data_dir)
    store = TimelineStore(root)
    preproduction = PreproductionStore(root)
    project_assets = ProjectAssets(root)
    render_inputs = RenderInputPreparation(project_assets)
    lock = RLock()
    waveform_cache = WaveformCache()
    active_workers = set()
    router = APIRouter(prefix="/api/projects/{project_id}/timeline")

    def fail(code, message, status=409):
        raise HTTPException(status_code=status, detail={"code": code, "message": message})

    def project_for(project_id):
        value = get_project(project_id)
        if value is None:
            fail("project_not_found", "复刻项目不存在。", 404)
        return value

    def state_for(project_id):
        try:
            return store.load(project_id)
        except (OSError, ValueError):
            fail("timeline_storage_invalid", "时间线状态无法读取。", 503)

    def save(project_id, state):
        try:
            store.save(project_id, state)
        except (OSError, ValueError):
            fail("timeline_storage_failed", "时间线状态无法保存。", 503)

    def asset_index(project_id):
        try:
            return project_assets.index(project_id)
        except (OSError, ValueError):
            fail("preproduction_storage_invalid", "前置工作台状态无法读取。", 503)

    def public_assets(project_id):
        try:
            return project_assets.public(project_id, include_source_reference=True)
        except (OSError, ValueError):
            fail("preproduction_storage_invalid", "前置工作台状态无法读取。", 503)

    def public_run(project_id, run):
        public = {key: value for key, value in run.items() if key not in ("snapshot", "sources", "output") and not key.startswith("_")}
        public.setdefault("commonRunId", "timeline:" + run["id"])
        public.setdefault("domainKind", "timeline.render")
        public.setdefault("inputSnapshot", run.get("snapshot"))
        public.setdefault("inputFingerprint", None)
        public.setdefault("idempotencyKey", None)
        public.setdefault("retryOf", None)
        public.setdefault("attempt", 1)
        public.setdefault("logs", [])
        if run["status"] == "completed":
            public["url"] = "/api/projects/{}/timeline/runs/{}/output".format(project_id, run["id"])
        return public

    def response(project_id, state):
        return {"revision": state["revision"], "settings": state["settings"], "tracks": state["tracks"], "assets": public_assets(project_id), "runs": [public_run(project_id, run) for run in state["runs"]]}

    def find_run(state, run_id):
        domain_run_id = run_id.removeprefix("timeline:") if run_id.startswith("timeline:") else run_id
        try:
            validate_storage_id(domain_run_id)
        except ValueError:
            fail("timeline_run_missing", "渲染任务不存在。", 404)
        run = next((item for item in state["runs"] if item.get("id") == domain_run_id and (run_id == domain_run_id or item.get("commonRunId", "timeline:" + domain_run_id) == run_id)), None)
        if run is None:
            fail("timeline_run_missing", "渲染任务不存在。", 404)
        return run

    def source_path(project_id, asset):
        try:
            return project_assets.file(project_id, asset)
        except (KeyError, OSError, ValueError):
            fail("timeline_asset_unavailable", "素材文件不可用。", 409)

    def snapshot_sources(project_id, run_id, tracks, assets):
        run_dir = store.path(project_id, "runs", run_id)
        try:
            return render_inputs.freeze_current(project_id, run_dir, tracks, assets)
        except RenderInputError:
            fail("timeline_asset_unavailable", "素材文件不可用。", 409)

    def retry_sources(project_id, original_run, retry_run_id):
        try:
            return render_inputs.clone_frozen(
                store.path(project_id, "runs", retry_run_id),
                store.path(project_id, "runs", original_run["id"]),
                dict(original_run["sources"]),
            )
        except (KeyError, RenderInputError):
            fail("timeline_retry_inputs_unavailable", "原渲染任务的冻结输入不可用，无法重试。")

    def queue_run(project_id, run_id):
        accepted = compute_queue.submit("timeline:" + run_id, project_id, lambda pid: process(pid, run_id))
        if accepted:
            return
        state = state_for(project_id)
        run = find_run(state, run_id)
        if run["status"] == "queued":
            run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error="本地处理队列不可用。")
            run.setdefault("logs", []).append({"event": "failed", "attempt": run.get("attempt", 1), "reason": "queue_unavailable"})
            save(project_id, state)

    def cancelled(project_id, run_id):
        with lock:
            state = state_for(project_id)
            run = find_run(state, run_id)
            return run["status"] in {"cancel_requested", "cancelled"}

    def process(project_id, run_id):
        staging = None
        retained_staging = False
        try:
            with lock:
                state = state_for(project_id)
                run = next((item for item in state["runs"] if item["id"] == run_id), None)
                if run is None or run["status"] != "queued":
                    return
                run["status"] = LOCAL_RUN_POLICY.start(run["status"])
                run.setdefault("logs", []).append({"event": "running", "attempt": run.get("attempt", 1)})
                save(project_id, state)
                active_workers.add((project_id, run_id))
                snapshot = run["snapshot"]
                source_names = dict(run["sources"])
                attempt = run.get("attempt", 1)
            sources = render_inputs.resolve(store.path(project_id, "runs", run_id), source_names)
            suffix = ".wav" if run["format"] == "wav" else ".mp4"
            staging = store.path(project_id, "runs", run_id, "attempt-{}.staging{}".format(attempt, suffix))
            actual_renderer = renderer
            if actual_renderer is None:
                from .timeline_render import render_timeline
                actual_renderer = render_timeline
            actual_renderer(snapshot, sources, staging, format=run["format"], ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, cancelled=lambda: cancelled(project_id, run_id))
            _regular_file(staging)
            with lock:
                state = state_for(project_id)
                run = find_run(state, run_id)
                if run.get("attempt", 1) != attempt:
                    _retain_late_output(staging, store.path(project_id, "runs", run_id, "attempt-{}".format(attempt) + suffix))
                    run.setdefault("logs", []).append({"event": "retained_not_adopted", "attempt": attempt, "reason": "superseded_attempt"})
                    save(project_id, state)
                    retained_staging = True
                    return
                if run["status"] == "cancel_requested":
                    _retain_late_output(staging, store.path(project_id, "runs", run_id, "attempt-{}".format(attempt) + suffix))
                    run.update(status=LOCAL_RUN_POLICY.acknowledge_cancel(run["status"]), error=None)
                    run.setdefault("logs", []).append({"event": "retained_not_adopted", "attempt": attempt, "reason": "cancelled"})
                    save(project_id, state)
                    retained_staging = True
                    return
                if run["status"] != "running":
                    return
                output = store.path(project_id, "outputs", run_id + suffix)
                output.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(staging), str(output))
                completion = LOCAL_RUN_POLICY.complete(
                    run["status"],
                    frozen_input=freeze_run_input(run["snapshot"]),
                    current_input=run["snapshot"],
                    result={"output": output.name},
                )
                run.update(status=completion.status, error=None, **completion.result)
                run.setdefault("logs", []).append({"event": "completed", "attempt": attempt})
                save(project_id, state)
        except HTTPException:
            raise
        except BaseException as error:
            with lock:
                state = state_for(project_id)
                run = find_run(state, run_id)
                if run.get("attempt", 1) != locals().get("attempt"):
                    if staging is not None and staging.exists():
                        _retain_late_output(staging, store.path(project_id, "runs", run_id, "attempt-{}{}".format(attempt, staging.suffix)))
                        run.setdefault("logs", []).append({"event": "retained_not_adopted", "attempt": attempt, "reason": "superseded_attempt"})
                        retained_staging = True
                        save(project_id, state)
                    return
                if run.get("attempt", 1) == locals().get("attempt") and run["status"] == "cancel_requested":
                    if staging is not None and staging.exists():
                        _retain_late_output(staging, store.path(project_id, "runs", run_id, "attempt-{}{}".format(attempt, staging.suffix)))
                        run.setdefault("logs", []).append({"event": "retained_not_adopted", "attempt": attempt, "reason": "cancelled"})
                        retained_staging = True
                    run.update(status=LOCAL_RUN_POLICY.acknowledge_cancel(run["status"]), error=None)
                    save(project_id, state)
                    return
                if run["status"] == "running":
                    run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error=_safe_error(error))
                    run.setdefault("logs", []).append({"event": "failed", "attempt": locals().get("attempt", 1)})
                    save(project_id, state)
        finally:
            try:
                if staging is not None and not retained_staging:
                    staging.unlink(missing_ok=True)
            finally:
                with lock:
                    active_workers.discard((project_id, run_id))

    def recover():
        base = root / "project-files"
        if not base.is_dir() or base.is_symlink():
            return
        for project_dir in base.iterdir():
            if project_dir.is_symlink() or not project_dir.is_dir():
                continue
            try:
                state = store.load(project_dir.name)
                changed = False
                resumable = []
                for run in state["runs"]:
                    if run.get("status") in {"queued", "running"}:
                        next_attempt = run.get("attempt", 1) + 1
                        run.update(status="queued", error=None, attempt=next_attempt,
                                   recovery={"reason": "restart_resumed", "attempt": next_attempt})
                        run.setdefault("logs", []).append({"event": "recovered", "attempt": next_attempt, "reason": "restart_resumed"})
                        resumable.append(run["id"])
                        changed = True
                    elif run.get("status") == "cancel_requested":
                        run.update(status=LOCAL_RUN_POLICY.acknowledge_cancel(run["status"]), error=None,
                                   recovery={"reason": "restart_cancelled", "attempt": run.get("attempt", 1)})
                        run.setdefault("logs", []).append({"event": "recovered", "attempt": run.get("attempt", 1), "reason": "restart_cancelled"})
                        changed = True
                if changed:
                    store.save(project_dir.name, state)
                for run_id in resumable:
                    queue_run(project_dir.name, run_id)
            except (OSError, ValueError):
                continue

    recover()

    @router.get("")
    def get_workspace(project_id: str):
        project_for(project_id)
        with lock:
            return response(project_id, state_for(project_id))

    @router.post("/validate-draft")
    def validate_draft(project_id: str, body: Dict[str, Any]):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            assets = asset_index(project_id)
            try:
                settings, tracks = validate_workspace(body, assets)
            except ValueError as error:
                fail("timeline_invalid", str(error), 422)
            for asset_id in {clip["assetId"] for track in tracks for clip in track["clips"]}:
                source_path(project_id, assets[asset_id])
            return {"revision": body["revision"], "settings": settings, "tracks": tracks}

    @router.put("")
    def save_workspace(project_id: str, body: Dict[str, Any]):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            try:
                settings, tracks = validate_workspace(body, asset_index(project_id))
            except ValueError as error:
                fail("timeline_invalid", str(error), 422)
            if body["revision"] != state["revision"]:
                fail("timeline_conflict", "时间线已更新，请刷新后重试。")
            state["settings"] = settings
            state["tracks"] = tracks
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.post("/import-shot-results")
    def import_shot_results(project_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if set(body) != {"revision", "preproductionRevision"} or any(type(value) is not int or value < 0 for value in body.values()):
            fail("timeline_invalid", "请提供时间线与镜头方案版本。", 422)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body["revision"] != state["revision"]:
                fail("timeline_conflict", "时间线已更新，请重新读取后重试。")
            try:
                preparation = preproduction.load(project_id)
            except (OSError, ValueError):
                fail("preproduction_storage_invalid", "镜头方案无法读取。", 503)
            if preparation["revision"] != body["preproductionRevision"]:
                fail("preproduction_conflict", "镜头方案已更新，请重新读取工作台后重试。")
            if not preparation["shots"]:
                fail("shot_results_missing", "请先添加镜头并导入生成结果。")
            identity = [[shot["id"], shot.get("resultAssetId"), float(shot.get("duration", 0))] for shot in preparation["shots"]]
            fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            track_id = "shot-results-" + fingerprint
            for track in state["tracks"]:
                if track["id"] == track_id or _legacy_shot_results_match(track, identity):
                    return response(project_id, state)
            assets = asset_index(project_id)
            start = max((clip["start"] + clip["duration"] for track in state["tracks"] for clip in track["clips"]), default=0)
            clips = []
            for index, shot in enumerate(preparation["shots"]):
                asset = assets.get(shot.get("resultAssetId"))
                duration = shot.get("duration", 0)
                if not asset or asset.get("kind") != "video" or duration <= 0 or asset.get("duration", 0) < duration:
                    fail("shot_result_invalid", "第 {} 个镜头需要视频结果，且视频时长必须覆盖有效计划时长。".format(index + 1))
                source_path(project_id, asset)
                clips.append({"id": "result-" + uuid4().hex, "assetId": asset["id"], "start": start,
                              "inPoint": 0, "duration": duration, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0})
                start = round(start + duration, 6)
            track = {"id": track_id, "name": "镜头结果 · 方案 {}".format(preparation["revision"]), "kind": "video", "muted": False, "hidden": False, "clips": clips}
            try:
                _, tracks = validate_workspace({"revision": state["revision"], "settings": state["settings"], "tracks": state["tracks"] + [track]}, assets)
            except ValueError as error:
                fail("shot_result_invalid", str(error))
            state["tracks"] = tracks
            state["revision"] += 1
            save(project_id, state)
            return response(project_id, state)

    @router.post("/assets/{asset_id}/waveform")
    def asset_waveform(project_id: str, asset_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if body != {}:
            fail("waveform_invalid", "波形请求无效。", 422)
        with source_lock or nullcontext(), lock:
            asset = asset_index(project_id).get(asset_id)
            if asset is None:
                fail("timeline_asset_missing", "素材不存在。", 404)
            path = source_path(project_id, asset)
        try:
            return waveform_cache.get(path, ffmpeg_path, ffprobe_path)
        except (OSError, ValueError) as error:
            fail("waveform_unavailable", str(error) if isinstance(error, ValueError) else "素材不可读取，请重新加载波形。", 422)

    @router.post("/preflight")
    def preflight(project_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if set(body) != {"revision", "format"} or type(body.get("revision")) is not int or body.get("format") not in ("preview", "mp4", "wav"):
            fail("timeline_invalid", "预检请求无效。", 422)
        from .timeline_render import inspect_timeline_media
        # Capture a saved version briefly; probing must not hold the global project lock.
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body["revision"] != state["revision"]:
                fail("timeline_conflict", "时间线已更新，请重新读取后预检。")
            assets = asset_index(project_id)
        def resolve(asset):
            try:
                return source_path(project_id, asset)
            except HTTPException as error:
                raise ValueError("素材文件缺失或不可读取。") from error
        issues = inspect_timeline_media(state, assets, resolve, body["format"], ffprobe_path)
        with source_lock or nullcontext(), lock:
            if state_for(project_id)["revision"] != state["revision"] or asset_index(project_id) != assets:
                fail("timeline_conflict", "检查期间素材或时间线已更新，请重新预检。")
        return {"revision": state["revision"], "format": body["format"], "ready": not any(item["level"] == "error" for item in issues), "issues": issues}

    @router.post("/runs", status_code=202)
    def submit_run(project_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) - {"revision", "format", "idempotencyKey"} or not {"revision", "format"}.issubset(body) or not isinstance(body.get("revision"), int) or isinstance(body.get("revision"), bool) or body.get("format") not in ("preview", "mp4", "wav") or ("idempotencyKey" in body and (not isinstance(body["idempotencyKey"], str) or not body["idempotencyKey"] or len(body["idempotencyKey"]) > 200)):
            fail("timeline_invalid", "渲染请求无效。", 422)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body["revision"] != state["revision"]:
                fail("timeline_conflict", "时间线已更新，请先保存当前版本。")
            assets = asset_index(project_id)
            try:
                settings, tracks = validate_workspace({"revision": state["revision"], "settings": state["settings"], "tracks": state["tracks"]}, assets)
            except ValueError as error:
                fail("timeline_invalid", str(error), 422)
            if body["format"] == "wav":
                if not has_audible_audio(tracks, assets):
                    fail("timeline_audible_audio_required", "WAV 导出至少需要一个未静音的音频片段。", 422)
            elif not has_visible_video(tracks):
                fail("timeline_visible_video_required", "至少需要一个可见的视频片段。", 422)
            input_snapshot = {"revision": state["revision"], "format": body["format"], "settings": settings, "tracks": tracks}
            frozen_input = freeze_run_input(input_snapshot)
            idempotency_key = body.get("idempotencyKey")
            if idempotency_key is not None:
                existing = next((run for run in state["runs"] if run.get("idempotencyKey") == idempotency_key), None)
                if existing is not None:
                    if existing.get("inputFingerprint") != frozen_input.fingerprint:
                        fail("timeline_idempotency_conflict", "幂等键已用于不同的渲染输入。")
                    return response(project_id, state)
            if any(run.get("status") in ACTIVE_STATUSES for run in state["runs"]):
                fail("timeline_run_active", "当前项目已有渲染任务正在执行。")
            run_id = uuid4().hex
            sources = snapshot_sources(project_id, run_id, tracks, assets)
            run = {"id": run_id, "revision": state["revision"], "format": body["format"], "status": LOCAL_RUN_POLICY.initial_status, "error": None,
                   "snapshot": {"settings": settings, "tracks": tracks}, "sources": sources,
                   "commonRunId": "timeline:" + run_id, "domainKind": "timeline.render", "inputSnapshot": input_snapshot,
                   "inputFingerprint": frozen_input.fingerprint, "idempotencyKey": idempotency_key, "retryOf": None,
                   "attempt": 1, "logs": [{"event": "created", "attempt": 1}, {"event": "queued", "attempt": 1}]}
            state["runs"].append(run)
            save(project_id, state)
            queue_run(project_id, run_id)
            return response(project_id, state_for(project_id))

    @router.get("/runs/{run_id}")
    def get_run(project_id: str, run_id: str):
        project_for(project_id)
        with lock:
            return public_run(project_id, find_run(state_for(project_id), run_id))

    @router.post("/runs/{run_id}/retry", status_code=202)
    def retry_run(project_id: str, run_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"idempotencyKey"} or not isinstance(body.get("idempotencyKey"), str) or not body["idempotencyKey"] or len(body["idempotencyKey"]) > 200:
            fail("timeline_invalid", "重试请求无效。", 422)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            original = find_run(state, run_id)
            if original["status"] not in TERMINAL_STATUSES:
                fail("timeline_run_active", "任务尚未结束，不能重试。")
            key = body["idempotencyKey"]
            existing = next((item for item in state["runs"] if item.get("idempotencyKey") == key), None)
            if existing is not None:
                if existing.get("retryOf") != original["id"] or existing.get("inputFingerprint") != original.get("inputFingerprint"):
                    fail("timeline_idempotency_conflict", "幂等键已用于不同的重试请求。")
                return response(project_id, state)
            if any(item.get("status") in ACTIVE_STATUSES for item in state["runs"]):
                fail("timeline_run_active", "当前项目已有渲染任务正在执行。")
            retry_id = uuid4().hex
            sources = retry_sources(project_id, original, retry_id)
            retry = {
                "id": retry_id, "revision": original["revision"], "format": original["format"], "status": LOCAL_RUN_POLICY.initial_status, "error": None,
                "snapshot": original["snapshot"], "sources": sources,
                "commonRunId": "timeline:" + retry_id, "domainKind": "timeline.render",
                "inputSnapshot": original.get("inputSnapshot", original["snapshot"]), "inputFingerprint": original.get("inputFingerprint"),
                "idempotencyKey": key, "retryOf": original["id"], "attempt": 1,
                "logs": [{"event": "created", "attempt": 1}, {"event": "queued", "attempt": 1}, {"event": "retry_created", "retryOf": original["id"]}],
            }
            state["runs"].append(retry)
            save(project_id, state)
            queue_run(project_id, retry_id)
            return response(project_id, state_for(project_id))

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(project_id: str, run_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if body != {}:
            fail("timeline_invalid", "取消请求无效。", 422)
        with lock:
            state = state_for(project_id)
            run = find_run(state, run_id)
            if run["status"] not in ACTIVE_STATUSES:
                fail("timeline_run_finished", "渲染任务已经结束。")
            cancellation = LOCAL_RUN_POLICY.request_cancel(run["status"])
            run.update(status=cancellation, error=None)
            if cancellation == "cancel_requested":
                run["cancelRequestedAt"] = datetime.now(timezone.utc).isoformat()
                run.setdefault("logs", []).append({"event": "cancel_requested", "attempt": run.get("attempt", 1)})
            else:
                run.setdefault("logs", []).append({"event": "cancelled", "attempt": run.get("attempt", 1)})
            save(project_id, state)
            return response(project_id, state)

    def cleanup_paths(project_id, state, run_id):
        run = find_run(state, run_id)
        domain_run_id = run["id"]
        if run["status"] not in TERMINAL_STATUSES or (project_id, domain_run_id) in active_workers:
            fail("timeline_run_active", "任务尚未完全停止，暂不能清理。")
        paths = [store.path(project_id, "runs", domain_run_id)]
        if run.get("output"):
            expected = domain_run_id + (".wav" if run["format"] == "wav" else ".mp4")
            if run["output"] != expected or any(item["id"] != domain_run_id and item.get("output") == expected for item in state["runs"]):
                fail("timeline_cleanup_unsafe", "输出文件关联异常，不能自动清理。")
            paths.append(store.path(project_id, "outputs", expected))
        return paths

    @router.get("/runs/{run_id}/cleanup-preview")
    def preview_run_cleanup(project_id: str, run_id: str):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            try:
                size, count = inventory(cleanup_paths(project_id, state, run_id))
            except (OSError, ValueError):
                fail("timeline_cleanup_unavailable", "历史文件不可安全读取，暂不能清理。")
            return {"revision": state["revision"], "bytes": size, "fileCount": count,
                    "description": "将删除这条渲染记录、对应预览或导出文件及该次渲染的源文件副本。原素材、当前时间线和其他输出保留；清理后此输出不能再下载。"}

    @router.post("/runs/{run_id}/delete")
    def delete_run(project_id: str, run_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if set(body) != {"revision"} or type(body.get("revision")) is not int:
            fail("timeline_invalid", "清理请求无效。", 422)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body["revision"] != state["revision"]:
                fail("timeline_conflict", "时间线已更新，请重新预览清理范围。")
            try:
                domain_run_id = find_run(state, run_id)["id"]
                paths = cleanup_paths(project_id, state, domain_run_id)
                state["runs"] = [item for item in state["runs"] if item["id"] != domain_run_id]
                state["revision"] += 1
                warning = remove_history_files(paths, store.path(project_id), lambda: save(project_id, state))
            except (OSError, ValueError):
                fail("timeline_cleanup_failed", "历史文件清理失败，请检查本地存储后重试。", 503)
            result = response(project_id, state)
            if warning:
                result["cleanupWarning"] = warning
            return result

    @router.get("/runs/{run_id}/output")
    def download_output(project_id: str, run_id: str):
        project_for(project_id)
        with lock:
            state = state_for(project_id)
            run = find_run(state, run_id)
            if run["status"] != "completed":
                fail("timeline_output_unavailable", "渲染结果尚不可用。")
            try:
                output = run["output"]
                validate_storage_id(output)
                path = store.path(project_id, "outputs", output)
                _regular_file(path)
            except (KeyError, OSError, ValueError):
                fail("timeline_output_unavailable", "渲染结果不可用。")
        media_type = "audio/wav" if run["format"] == "wav" else "video/mp4"
        return FileResponse(path, media_type=media_type, filename="timeline" + path.suffix)

    return router


def _regular_file(path):
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("不是普通文件")
    finally:
        os.close(descriptor)


def _retain_late_output(staging, retained):
    if not staging.exists():
        return
    _regular_file(staging)
    os.replace(str(staging), str(retained))


def _safe_error(error):
    message = str(error)
    if message and len(message) <= 300 and "/" not in message and "\\" not in message:
        return message
    return "渲染失败，请检查时间线和素材。"


def _legacy_shot_results_match(track, identity):
    """Only infer an old revision-based import when its source clips still match."""
    if not re.fullmatch(r"shot-results-\d+", track["id"]) or track["kind"] != "video" or len(track["clips"]) != len(identity):
        return False
    for clip, (_, asset_id, duration) in zip(track["clips"], identity):
        if clip["assetId"] != asset_id or clip["duration"] != duration or clip["inPoint"] != 0 or clip["speed"] != 1:
            return False
    return True
