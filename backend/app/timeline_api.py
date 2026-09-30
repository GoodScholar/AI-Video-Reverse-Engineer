"""Project-scoped persistence and queued rendering endpoints for timelines."""
from __future__ import annotations

import os
import hashlib
import json
import re
import stat
from contextlib import nullcontext
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
from .timeline import TimelineStore, has_audible_audio, has_visible_video, validate_transitions, validate_workspace


ACTIVE_STATUSES = LOCAL_RUN_POLICY.active_statuses
TERMINAL_STATUSES = LOCAL_RUN_POLICY.allowed_statuses - ACTIVE_STATUSES


def create_timeline_router(data_dir, get_project, compute_queue, *, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", source_lock=None, renderer=None, reframe_analyzer=None):
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
        if run["status"] == "completed":
            public["url"] = "/api/projects/{}/timeline/runs/{}/output".format(project_id, run["id"])
        return public

    def response(project_id, state):
        return {"revision": state["revision"], "settings": state["settings"], "tracks": state["tracks"],
                "transitions": state.get("transitions", []), "assets": public_assets(project_id),
                "runs": [public_run(project_id, run) for run in state["runs"]],
                "reframeAnalysis": state.get("reframeAnalysis", {}), "packaging": state.get("packaging")}

    class ReframeSourceProofConflict(OSError):
        """A lock-free reframe source proof is no longer current."""

    def reframe_source_identity(source: Path) -> dict[str, Any]:
        source = Path(source)
        _regular_file(source)
        item = os.stat(source)
        if not stat.S_ISREG(item.st_mode):
            raise OSError("不是普通文件")
        return {"path": str(source), "dev": item.st_dev, "ino": item.st_ino, "size": item.st_size,
                "mtimeNs": item.st_mtime_ns, "ctimeNs": item.st_ctime_ns}

    def prove_reframe_source(source: Path, *, still_image: bool) -> dict[str, Any]:
        """Read costly source facts outside timeline/source locks, bound to one stat identity."""
        from .montage_reframe import probe_source_timing, source_fingerprint

        try:
            before = reframe_source_identity(source)
            fingerprint = source_fingerprint(source)
            try:
                from .timeline_render import _probe_media
                media = _probe_media(source, ffprobe_path)
                size = (media.width, media.height)
            except Exception:
                # Keep the established worker failure for an unprobeable
                # source rather than trusting dimensions supplied by a worker.
                size = (None, None)
            timing = None if still_image else probe_source_timing(source, ffprobe_path)
            after = reframe_source_identity(source)
        except (OSError, ValueError) as error:
            raise ReframeSourceProofConflict("素材在校验期间不可读取") from error
        if before != after:
            raise ReframeSourceProofConflict("素材在校验期间变化")
        return {"identity": before, "fingerprint": fingerprint, "timing": timing, "size": size}

    def reframe_fingerprint_fact(source: Path) -> dict[str, Any]:
        """Recheck content outside locks before a worker publishes its result."""
        from .montage_reframe import source_fingerprint

        try:
            before = reframe_source_identity(source)
            fingerprint = source_fingerprint(source)
            after = reframe_source_identity(source)
        except (OSError, ValueError) as error:
            raise ReframeSourceProofConflict("素材在校验期间不可读取") from error
        if before != after:
            raise ReframeSourceProofConflict("素材在校验期间变化")
        return {"identity": before, "fingerprint": fingerprint}

    def reframe_asset_identity(asset: dict[str, Any]) -> dict[str, Any]:
        return {key: asset.get(key) for key in ("id", "kind", "file", "sourceReferenceId")}

    def schedule_reframe(project_id, clip_id, *, expected_revision=None):
        run_id = uuid4().hex
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if expected_revision is not None and state["revision"] != expected_revision:
                fail("timeline_conflict", "时间线已更新，请刷新后重试。")
            clip = next((item for track in state["tracks"] for item in track["clips"] if item["id"] == clip_id), None)
            if clip is None:
                fail("reframe_clip_missing", "时间线片段不存在。", 404)
            asset = asset_index(project_id).get(clip["assetId"])
            if asset is None or asset.get("kind") not in ("video", "image"):
                fail("reframe_source_unavailable", "主体分析仅支持可用图片或视频素材。")
            try:
                source = source_path(project_id, asset)
            except HTTPException:
                fail("reframe_source_unavailable", "主体分析素材不可用。")
            still_image = asset.get("kind") == "image"
            try:
                source_identity = reframe_source_identity(source)
            except OSError:
                fail("reframe_source_unavailable", "主体分析素材不可用。")
            request = {"revision": state["revision"], "clip": {
                key: clip[key] for key in ("assetId", "inPoint", "duration", "speed")
            }, "asset": reframe_asset_identity(asset), "source": source_identity,
                       "targetAspect": {"width": state["settings"]["width"], "height": state["settings"]["height"]},
                       "stillImage": still_image}

        try:
            proof = prove_reframe_source(source, still_image=request["stillImage"])
        except ReframeSourceProofConflict:
            fail("reframe_source_unavailable", "主体分析素材在校验期间变化或不可用，请刷新后重试。")

        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if state["revision"] != request["revision"]:
                fail("timeline_conflict", "时间线已更新，请刷新后重试。")
            clip = next((item for track in state["tracks"] for item in track["clips"] if item["id"] == clip_id), None)
            if clip is None:
                fail("reframe_clip_missing", "时间线片段不存在。", 404)
            asset = asset_index(project_id).get(request["clip"]["assetId"])
            if asset is None or reframe_asset_identity(asset) != request["asset"]:
                fail("reframe_source_unavailable", "主体分析素材已变化，请刷新后重试。")
            try:
                current_source = source_path(project_id, asset)
            except HTTPException:
                fail("reframe_source_unavailable", "主体分析素材不可用。")
            try:
                current_source_identity = reframe_source_identity(current_source)
            except OSError:
                fail("reframe_source_unavailable", "主体分析素材不可用。")
            if (current_source_identity != request["source"]
                    or current_source_identity != proof["identity"]):
                fail("reframe_source_unavailable", "主体分析素材已变化，请刷新后重试。")
            current_clip = {key: clip.get(key) for key in request["clip"]}
            if current_clip != request["clip"]:
                fail("timeline_conflict", "时间线片段已更新，请刷新后重试。")
            current_aspect = {"width": state["settings"]["width"], "height": state["settings"]["height"]}
            if current_aspect != request["targetAspect"]:
                fail("timeline_conflict", "时间线输出比例已更新，请刷新后重试。")
            from .montage_reframe import pending_reframe
            clip.setdefault("reframe", pending_reframe(target_width=current_aspect["width"], target_height=current_aspect["height"], reason="已提交本地主体分析"))
            identity = {**request["clip"], "sourceFingerprint": proof["fingerprint"], "targetAspect": current_aspect}
            source = current_source
            source_timing, trusted_size = proof["timing"], proof["size"]
            state.setdefault("reframeAnalysis", {})[clip_id] = {"id": run_id, "status": "queued", "error": None, "identity": identity}
            save(project_id, state)
        def process(_pid):
            with lock:
                state = state_for(project_id)
                record = state.get("reframeAnalysis", {}).get(clip_id)
                if record is None or record.get("id") != run_id or record.get("status") != "queued":
                    return
                record["status"] = "running"
                save(project_id, state)
            try:
                from .montage_reframe import normalize_reframe, run_local_detector
                if not source.is_file():
                    raise ValueError("分析期间素材已变化，请重新主体分析。")
                if not all(type(value) is int and value > 0 for value in trusted_size):
                    raise ValueError("无法获取可信源尺寸，不能完成主体分析。")
                raw = reframe_analyzer(source, identity) if reframe_analyzer else run_local_detector(
                    source, start=0 if still_image else identity["inPoint"], end=0 if still_image else identity["inPoint"] + identity["duration"] * identity["speed"],
                    target_width=identity["targetAspect"]["width"], target_height=identity["targetAspect"]["height"],
                    source_timing=source_timing, media_kind="image" if still_image else "video",
                    cancelled=lambda: (state_for(project_id).get("reframeAnalysis", {}).get(clip_id, {}).get("id") != run_id
                                       or state_for(project_id).get("reframeAnalysis", {}).get(clip_id, {}).get("status") == "cancelled"))
                result = normalize_reframe(raw, source_fingerprint=identity["sourceFingerprint"],
                                           source_width=trusted_size[0], source_height=trusted_size[1], source_timing=source_timing,
                                           required_start=None if still_image else identity["inPoint"],
                                           required_end=None if still_image else identity["inPoint"] + identity["duration"] * identity["speed"],
                                           still_image=still_image)
                error = None
            except Exception as failure:
                result, error = None, str(failure)[:300] or "本地主体分析失败。"
            try:
                current_source_fact = reframe_fingerprint_fact(source) if result is not None else None
            except ReframeSourceProofConflict:
                current_source_fact = None
            with source_lock or nullcontext(), lock:
                state = state_for(project_id)
                record = state.get("reframeAnalysis", {}).get(clip_id)
                clip = next((item for track in state["tracks"] for item in track["clips"] if item["id"] == clip_id), None)
                if record is None or record.get("id") != run_id or record.get("status") == "cancelled" or clip is None:
                    return
                if result is None:
                    record.update(status="failed", error=error)
                else:
                    try:
                        current_asset = asset_index(project_id).get(identity["assetId"])
                        current_source = source_path(project_id, current_asset) if current_asset else None
                        current_source_identity = reframe_source_identity(current_source) if current_source else None
                    except (HTTPException, OSError, ValueError):
                        current_source_identity = None
                    if ({key: clip.get(key) for key in ("assetId", "inPoint", "duration", "speed")} !=
                            {key: identity[key] for key in ("assetId", "inPoint", "duration", "speed")}
                            or current_source_fact is None
                            or current_asset is None or reframe_asset_identity(current_asset) != request["asset"]
                            or current_source_identity != proof["identity"]
                            or current_source_identity != current_source_fact["identity"]
                            or current_source_fact["identity"] != proof["identity"]
                            or current_source_fact["fingerprint"] != identity["sourceFingerprint"]):
                        record.update(status="failed", error="分析期间素材或片段已变化，请重新主体分析。")
                    elif {"width": state["settings"]["width"], "height": state["settings"]["height"]} != identity["targetAspect"]:
                        record.update(status="failed", error="分析期间输出比例已变化，请重新主体分析。")
                    else:
                        override = clip.get("reframe", {}).get("override")
                        clip["reframe"] = {**result, **({"override": override} if isinstance(override, dict) and override.get("mode") != "auto" else {})}
                        record.update(status="completed", error=None)
                        state["revision"] += 1
                save(project_id, state)
        if not compute_queue.submit("timeline-reframe:" + run_id, project_id, process):
            with lock:
                state = state_for(project_id)
                record = state.get("reframeAnalysis", {}).get(clip_id)
                if record and record.get("id") == run_id:
                    record.update(status="failed", error="本地处理队列不可用。")
                    save(project_id, state)

    def find_run(state, run_id):
        try:
            validate_storage_id(run_id)
        except ValueError:
            fail("timeline_run_missing", "渲染任务不存在。", 404)
        run = next((item for item in state["runs"] if item.get("id") == run_id), None)
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

    def queue_run(project_id, run_id):
        accepted = compute_queue.submit("timeline:" + run_id, project_id, lambda pid: process(pid, run_id))
        if accepted:
            return
        state = state_for(project_id)
        run = find_run(state, run_id)
        if run["status"] == "queued":
            run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error="本地处理队列不可用。")
            save(project_id, state)

    def cancelled(project_id, run_id):
        with lock:
            state = state_for(project_id)
            run = find_run(state, run_id)
            return run["status"] == "cancelled"

    def process(project_id, run_id):
        staging = None
        try:
            with lock:
                state = state_for(project_id)
                run = next((item for item in state["runs"] if item["id"] == run_id), None)
                if run is None or run["status"] != "queued":
                    return
                run["status"] = LOCAL_RUN_POLICY.start(run["status"])
                save(project_id, state)
                active_workers.add((project_id, run_id))
                snapshot = run["snapshot"]
                source_names = dict(run["sources"])
            sources = render_inputs.resolve(store.path(project_id, "runs", run_id), source_names)
            suffix = ".wav" if run["format"] == "wav" else ".mp4"
            staging = store.path(project_id, "runs", run_id, "output" + suffix)
            actual_renderer = renderer
            if actual_renderer is None:
                from .timeline_render import render_timeline
                actual_renderer = render_timeline
            actual_renderer(snapshot, sources, staging, format=run["format"], ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, cancelled=lambda: cancelled(project_id, run_id))
            _regular_file(staging)
            with lock:
                state = state_for(project_id)
                run = find_run(state, run_id)
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
                save(project_id, state)
        except HTTPException:
            raise
        except BaseException as error:
            with lock:
                state = state_for(project_id)
                run = find_run(state, run_id)
                if run["status"] == "running":
                    run.update(status=LOCAL_RUN_POLICY.fail(run["status"]), error=_safe_error(error))
                    save(project_id, state)
        finally:
            try:
                if staging is not None:
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
                for run in state["runs"]:
                    recovery = LOCAL_RUN_POLICY.recover_after_restart(run.get("status"))
                    if recovery.reason == "interrupted":
                        run.update(status=recovery.status, error="服务重启中断了渲染，可重新提交。")
                        changed = True
                for record in state.get("reframeAnalysis", {}).values():
                    if record.get("status") in ("queued", "running"):
                        record.update(status="failed", error="服务重启中断了主体分析，可重新提交。")
                        changed = True
                if changed:
                    store.save(project_dir.name, state)
            except (OSError, ValueError):
                continue

    recover()

    @router.get("")
    def get_workspace(project_id: str):
        project_for(project_id)
        with lock:
            return response(project_id, state_for(project_id))

    @router.post("/reframes/{clip_id}/analyze", status_code=202)
    def analyze_reframe(project_id: str, clip_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"revision"} or type(body["revision"]) is not int:
            fail("reframe_request_invalid", "主体分析请求无效。", 422)
        schedule_reframe(project_id, clip_id, expected_revision=body["revision"])
        with lock:
            return response(project_id, state_for(project_id))

    @router.post("/reframes/{clip_id}/cancel")
    def cancel_reframe(project_id: str, clip_id: str, body: Dict[str, Any]):
        project_for(project_id)
        if not isinstance(body, dict) or set(body) != {"revision"} or type(body["revision"]) is not int:
            fail("reframe_request_invalid", "主体分析取消请求无效。", 422)
        with lock:
            state = state_for(project_id)
            if state["revision"] != body["revision"]:
                fail("timeline_conflict", "时间线已更新，请刷新后重试。")
            record = state.get("reframeAnalysis", {}).get(clip_id)
            if record is None or record.get("status") not in ("queued", "running"):
                fail("reframe_not_active", "主体分析未在运行。")
            record.update(status="cancelled", error=None)
            save(project_id, state)
            return response(project_id, state)

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
            checked = {"revision": body["revision"], "settings": settings, "tracks": tracks}
            if "transitions" in body:
                checked["transitions"] = validate_transitions(body["transitions"], tracks)
            if "packaging" in body:
                if body["packaging"] is None:
                    checked["packaging"] = None
                else:
                    from .montage_packaging import PackagingError, validate_packaging
                    try:
                        checked["packaging"] = validate_packaging(body["packaging"], [], tracks)
                    except PackagingError as error:
                        fail("timeline_packaging_invalid", str(error), 422)
            return checked

    @router.put("")
    def save_workspace(project_id: str, body: Dict[str, Any]):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            transition_input = body.get("transitions", state.get("transitions", [])) if isinstance(body, dict) else []
            try:
                value = {**body, "transitions": transition_input}
                settings, tracks = validate_workspace(value, asset_index(project_id))
            except ValueError as error:
                fail("timeline_invalid", str(error), 422)
            if body["revision"] != state["revision"]:
                fail("timeline_conflict", "时间线已更新，请刷新后重试。")
            try:
                from .montage_reframe import ReframeError, retain_server_reframe_facts
                tracks = retain_server_reframe_facts(state["tracks"], tracks)
            except ReframeError as error:
                fail("timeline_invalid", str(error), 422)
            state["settings"] = settings
            state["tracks"] = tracks
            state["transitions"] = validate_transitions(transition_input, tracks)
            if "packaging" in body:
                if body["packaging"] is None:
                    state.pop("packaging", None)
                else:
                    from .montage_packaging import PackagingError, validate_packaging
                    try:
                        state["packaging"] = validate_packaging(body["packaging"], [], tracks)
                    except PackagingError as error:
                        fail("timeline_packaging_invalid", str(error), 422)
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
                _, tracks = validate_workspace({"revision": state["revision"], "settings": state["settings"],
                                                "tracks": state["tracks"] + [track],
                                                "transitions": state.get("transitions", [])}, assets)
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
        if not isinstance(body, dict) or set(body) != {"revision", "format"} or not isinstance(body.get("revision"), int) or isinstance(body.get("revision"), bool) or body.get("format") not in ("preview", "mp4", "wav"):
            fail("timeline_invalid", "渲染请求无效。", 422)
        with source_lock or nullcontext(), lock:
            state = state_for(project_id)
            if body["revision"] != state["revision"]:
                fail("timeline_conflict", "时间线已更新，请先保存当前版本。")
            if any(run.get("status") in ACTIVE_STATUSES for run in state["runs"]):
                fail("timeline_run_active", "当前项目已有渲染任务正在执行。")
            assets = asset_index(project_id)
            try:
                settings, tracks = validate_workspace({"revision": state["revision"], "settings": state["settings"],
                                                       "tracks": state["tracks"],
                                                       "transitions": state.get("transitions", [])}, assets)
            except ValueError as error:
                fail("timeline_invalid", str(error), 422)
            if body["format"] == "wav":
                if not has_audible_audio(tracks, assets):
                    fail("timeline_audible_audio_required", "WAV 导出至少需要一个未静音的音频片段。", 422)
            elif not has_visible_video(tracks):
                fail("timeline_visible_video_required", "至少需要一个可见的视频片段。", 422)
            run_id = uuid4().hex
            sources = snapshot_sources(project_id, run_id, tracks, assets)
            run = {"id": run_id, "revision": state["revision"], "format": body["format"], "status": LOCAL_RUN_POLICY.initial_status, "error": None,
                   "snapshot": {"settings": settings, "tracks": tracks,
                                "transitions": validate_transitions(state.get("transitions", []), tracks)},
                   "sources": sources}
            if state.get("packaging") is not None:
                run["snapshot"]["packaging"] = state["packaging"]
            state["runs"].append(run)
            save(project_id, state)
            queue_run(project_id, run_id)
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
            run.update(status=LOCAL_RUN_POLICY.cancel(run["status"]), error=None)
            save(project_id, state)
            return response(project_id, state)

    def cleanup_paths(project_id, state, run_id):
        run = find_run(state, run_id)
        if run["status"] not in TERMINAL_STATUSES or (project_id, run_id) in active_workers:
            fail("timeline_run_active", "任务尚未完全停止，暂不能清理。")
        paths = [store.path(project_id, "runs", run_id)]
        if run.get("output"):
            expected = run_id + (".wav" if run["format"] == "wav" else ".mp4")
            if run["output"] != expected or any(item["id"] != run_id and item.get("output") == expected for item in state["runs"]):
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
                paths = cleanup_paths(project_id, state, run_id)
                state["runs"] = [item for item in state["runs"] if item["id"] != run_id]
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
                snapshot = run.get("snapshot", {})
                if snapshot.get("packaging") is not None:
                    from .montage_packaging import validate_packaging
                    validate_packaging(snapshot["packaging"], [], snapshot.get("tracks", []))
            except (KeyError, OSError, ValueError):
                fail("timeline_output_unavailable", "渲染结果不可用。")
            snapshot = json.loads(json.dumps(snapshot))
        try:
            from .montage_packaging import PackagingError, prepare_packaging_proof
            packaging_proof = prepare_packaging_proof(snapshot, [], batch=False)
        except PackagingError:
            fail("timeline_output_unavailable", "渲染结果不可用。")
        with lock:
            state = state_for(project_id)
            run = find_run(state, run_id)
            if run["status"] != "completed" or run.get("snapshot") != snapshot:
                fail("timeline_output_unavailable", "渲染结果不可用。")
            try:
                from .montage_packaging import consume_packaging_proof
                consume_packaging_proof(packaging_proof, run.get("snapshot", {}), [], batch=False)
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
