"""Project-scoped API for assembling and exporting video pre-production work."""
from __future__ import annotations

import json
import hashlib
import inspect
import math
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from .aigc_content_api import load_aigc_content_state
from .asset_references import (
    aigc_content_asset_references,
    batch_asset_references,
    preproduction_asset_references,
    timeline_asset_references,
)
from .batch_editing_api import BatchStore
from .canvas_layout import CanvasLayoutConflict, CanvasLayoutStore, validate_layout
from .durable_runs import LOCAL_RUN_POLICY, freeze_run_input
from .timeline import TimelineStore
from .preproduction import PreproductionStore
from .project_assets import (
    AssetFileUnavailableError,
    AssetInUseError,
    AssetReferencesUnavailableError,
    AssetStateConflict,
    LocalVideoInstallationConflict,
    ProjectAssets,
)
from .preproduction_handoff import render_handoff
from .preproduction_nodes import FFMPEG_TIMEOUT_SECONDS, MAX_MEDIA_DURATION_SECONDS, MAX_OUTPUT_DIMENSION, MAX_OUTPUT_PIXELS
from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path
from .reference_video import validate_storage_id
from .video_sources import utc_timestamp
from .youtube_import import (
    MAX_YOUTUBE_SOURCE_DURATION_SECONDS,
    SystemYoutubeCookieStore,
    YOUTUBE_ADAPTER,
    YtDlpYoutubeAdapter,
    YoutubeImportError,
    YoutubeImportCancelled,
    YoutubeImportRuns,
    public_youtube_import_run,
    normalize_youtube_url,
    probe_downloaded_youtube_video,
)
from .bilibili_import import (
    BILIBILI_ADAPTER,
    MAX_BILIBILI_SOURCE_DURATION_SECONDS,
    BilibiliImportError,
    BilibiliImportRuns,
    SystemBilibiliCookieStore,
    YtDlpBilibiliAdapter,
    bilibili_url_part,
    bilibili_video_source,
    normalize_bilibili_url,
    probe_downloaded_bilibili_video,
    public_bilibili_import_run,
    resolve_bilibili_short_url,
)
from .douyin_import import (
    DOUYIN_ADAPTER,
    MAX_DOUYIN_SOURCE_DURATION_SECONDS,
    DouyinImportError,
    DouyinImportRuns,
    SystemDouyinCookieStore,
    YtDlpDouyinAdapter,
    douyin_video_source,
    extract_douyin_share_url,
    normalize_douyin_url,
    probe_downloaded_douyin_video,
    public_douyin_import_run,
    resolve_douyin_short_url,
)
from .xiaohongshu_import import (
    XIAOHONGSHU_ADAPTER,
    MAX_XIAOHONGSHU_SOURCE_DURATION_SECONDS,
    XiaohongshuImportError,
    XiaohongshuImportRuns,
    SystemXiaohongshuCookieStore,
    SystemXiaohongshuRequestStore,
    YtDlpXiaohongshuAdapter,
    extract_xiaohongshu_share_url,
    normalize_xiaohongshu_url,
    probe_downloaded_xiaohongshu_video,
    public_xiaohongshu_import_run,
    resolve_xiaohongshu_short_url,
    xiaohongshu_source_facts,
    xiaohongshu_video_source,
)
from .kuaishou_import import (
    KUAISHOU_ADAPTER, MAX_KUAISHOU_SOURCE_DURATION_SECONDS, KuaishouImportError,
    KuaishouImportRuns, KuaishouSourceAdapter, SystemKuaishouCookieStore,
    extract_kuaishou_share_url, normalize_kuaishou_url, public_kuaishou_import_run,
    resolve_kuaishou_short_url, kuaishou_source_facts, kuaishou_video_source,
)
from .shot_production import MAX_RESULT_VERSIONS, NODE_KINDS, ShotProduction, ShotProductionError, StepSource, public_result_versions
from .shot_organization import ShotOrganization, ShotOrganizationError
from .preproduction_transaction import WorkspaceTransaction, WorkspaceTransactionError


MAX_ASSET_BYTES = 200_000_000
MAX_SHOTS = 500
MAX_NODES_PER_SHOT = 80
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_MEDIA_SUFFIXES = _IMAGE_SUFFIXES | {".mp4", ".mov", ".m4a", ".mp3", ".wav", ".aac", ".webm"}


def youtube_external_request() -> dict[str, Any]:
    """Pre-network disclosure used by both the UI and metadata response."""
    return {
        "probeHosts": ["www.youtube.com", "YouTube player metadata API"],
        "downloadHosts": ["*.googlevideo.com"],
        "purpose": "读取公开视频元数据；确认权利后下载到本项目受管暂存区。",
        "sessionPolicy": "默认不读取或发送会话；仅在你明确勾选后，才读取系统安全存储中的合法会话并仅发送给 YouTube。",
    }


def bilibili_external_request() -> dict[str, Any]:
    return {
        "probeHosts": ["www.bilibili.com", "api.bilibili.com"],
        "downloadHosts": ["B站授权媒体 CDN"],
        "purpose": "读取公开视频与分P元数据；确认权利并明确选择分P后下载到本项目受管暂存区。",
        "sessionPolicy": "默认不读取或发送会话；仅在你明确勾选后，才读取系统安全存储中的合法会话并仅发送给 B站。",
        "shortLinkPolicy": "b23.tv 短链只会经无代理、最多三次的 HTTPS 跳转解析，最终必须是标准 BV 视频页。",
    }


def douyin_external_request() -> dict[str, Any]:
    return {
        "probeHosts": ["www.douyin.com"],
        "downloadHosts": ["抖音授权媒体 CDN"],
        "purpose": "读取公开视频元数据；确认权利后下载到本项目受管暂存区。",
        "sessionPolicy": "默认不读取或发送会话；仅在你明确勾选后，才读取系统安全存储中的合法会话并仅发送给抖音。",
        "shortLinkPolicy": "v.douyin.com 短链只会经无代理、最多三次、DNS 固定的 HTTPS 跳转解析，最终必须是标准抖音视频页。",
    }


def xiaohongshu_external_request() -> dict[str, Any]:
    return {
        "probeHosts": ["www.xiaohongshu.com", "sns-video-bd.xhscdn.com（原始视频可用性探测）"],
        "downloadHosts": ["小红书授权媒体 CDN（*.xhscdn.com）"],
        "purpose": "读取公开小红书笔记的视频元数据；确认权利后下载到本项目受管暂存区。",
        "sessionPolicy": "默认不读取或发送会话；仅在你明确勾选后，才读取系统安全存储中的合法会话并仅发送给小红书。",
        "shortLinkPolicy": "xhslink.com 短链只会经无代理、最多三次、DNS 固定的 HTTPS 跳转解析，最终必须是标准小红书笔记页。",
    }

def kuaishou_external_request() -> dict[str, Any]:
    return {"probeHosts": ["www.kuaishou.com"], "downloadHosts": ["快手授权媒体 CDN（*.yximgs.com / *.kwaicdn.com）"],
            "purpose": "读取公开快手页面的元数据；确认权利后下载到本项目受管暂存区。",
            "sessionPolicy": "默认不读取或发送会话；仅在你明确勾选后，才读取系统安全存储中的合法会话并仅发送给快手。",
            "shortLinkPolicy": "v.kuaishou.com 短链只会经无代理、最多三次、DNS 固定的 HTTPS 跳转解析，最终必须是标准快手视频页。"}


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
    # Read-only v2 identity metadata; accepted for old whole-workspace clients and
    # preserved by ShotProduction rather than trusted from the request.
    sceneId: Optional[str] = Field(default=None, max_length=100)
    rank: Optional[str] = Field(default=None, max_length=100)
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


class SceneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=1000)
    rank: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=4000)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        return validate_storage_id(value)


class SaveWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(ge=0)
    brief: Brief
    scenes: Optional[list[SceneUpdate]] = Field(default=None, max_length=MAX_SHOTS)
    shots: list[ShotUpdate] = Field(default_factory=list, max_length=MAX_SHOTS)
    canvasLayout: Optional[dict[str, Any]] = None


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


class YoutubeImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    role: Literal["character", "scene", "motion", "audio", "reference"] = "reference"
    rightsConfirmed: bool = False
    externalRequestAccepted: bool = False
    useSession: bool = False
    idempotencyKey: str = Field(default="", max_length=128)


class BilibiliImportRequest(YoutubeImportRequest):
    part: int | None = Field(default=None, ge=1, le=9999)


class DouyinImportRequest(YoutubeImportRequest):
    pass


class XiaohongshuImportRequest(YoutubeImportRequest):
    pass

class KuaishouImportRequest(YoutubeImportRequest):
    pass


class CanvasLayoutUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    layoutRevision: int = Field(ge=0)
    nodes: dict[str, dict[str, Any]]
    viewport: Optional[dict[str, Any]] = None


def create_preproduction_router(
    data_dir: Path,
    get_project: Callable[[str], Any],
    compute_queue: Any,
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    source_lock: Any = None,
    runner: Optional[Callable[..., list[str]]] = None,
    youtube_adapter: Any = None,
    youtube_session_store: Any = None,
    bilibili_adapter: Any = None,
    bilibili_session_store: Any = None,
    bilibili_short_link_resolver: Any = None,
    douyin_adapter: Any = None,
    douyin_session_store: Any = None,
    douyin_short_link_resolver: Any = None,
    xiaohongshu_adapter: Any = None,
    xiaohongshu_session_store: Any = None,
    xiaohongshu_request_store: Any = None,
    xiaohongshu_short_link_resolver: Any = None,
    kuaishou_adapter: Any = None,
    kuaishou_session_store: Any = None,
    kuaishou_short_link_resolver: Any = None,
) -> APIRouter:
    """Create the router; ``runner`` is a test seam and defaults to execute_node."""
    root = Path(data_dir)
    store = PreproductionStore(root)
    batch_store = BatchStore(root)
    layout_store = CanvasLayoutStore(root)
    workspace_transaction = WorkspaceTransaction(store, layout_store)
    project_assets = ProjectAssets(root, reference_facts=(
        lambda project_id, asset_id: preproduction_asset_references(store.load(project_id), asset_id),
        lambda project_id, asset_id: timeline_asset_references(TimelineStore(root).load(project_id), asset_id),
        lambda project_id, asset_id: aigc_content_asset_references(
            load_aigc_content_state(root, project_id), asset_id,
        ),
        lambda project_id, asset_id: batch_asset_references(batch_store.load(project_id), asset_id),
    ))
    lock = source_lock or RLock()
    youtube_adapter = youtube_adapter or YtDlpYoutubeAdapter()
    youtube_runs = YoutubeImportRuns(root)
    youtube_cancel_events: dict[str, Event] = {}
    bilibili_adapter = bilibili_adapter or YtDlpBilibiliAdapter(ffmpeg_path=ffmpeg_path)
    bilibili_short_link_resolver = bilibili_short_link_resolver or resolve_bilibili_short_url
    bilibili_runs = BilibiliImportRuns(root)
    bilibili_cancel_events: dict[str, Event] = {}
    douyin_adapter = douyin_adapter or YtDlpDouyinAdapter(ffmpeg_path=ffmpeg_path)
    douyin_short_link_resolver = douyin_short_link_resolver or resolve_douyin_short_url
    douyin_runs = DouyinImportRuns(root)
    douyin_cancel_events: dict[str, Event] = {}
    xiaohongshu_adapter = xiaohongshu_adapter or YtDlpXiaohongshuAdapter(ffmpeg_path=ffmpeg_path)
    xiaohongshu_short_link_resolver = xiaohongshu_short_link_resolver or resolve_xiaohongshu_short_url
    xiaohongshu_runs = XiaohongshuImportRuns(root)
    xiaohongshu_cancel_events: dict[str, Event] = {}
    kuaishou_adapter = kuaishou_adapter or KuaishouSourceAdapter()
    kuaishou_short_link_resolver = kuaishou_short_link_resolver or resolve_kuaishou_short_url
    kuaishou_runs = KuaishouImportRuns(root)
    kuaishou_cancel_events: dict[str, Event] = {}

    def xiaohongshu_request_url(run_id: str, fallback: str) -> str:
        try:
            value = (xiaohongshu_request_store or SystemXiaohongshuRequestStore()).get(run_id)
        except Exception:
            value = None
        return value or fallback
    router = APIRouter(prefix="/api/projects/{project_id}/preproduction")

    def reconcile_youtube_imports(project_id: str) -> None:
        """Close the post-install/pre-run-record crash window without redownloading."""
        assets = project_assets.index(project_id)
        for run in youtube_runs.list(project_id):
            if run["status"] not in {"failed", "cancelled"} or run.get("uploadedAssetId"):
                continue
            canonical = normalize_youtube_url(str(run.get("input", {}).get("url", "")))
            video_id = (canonical or "").removeprefix("https://www.youtube.com/watch?v=")
            asset = next((item for item in assets.values() if isinstance(item.get("videoSource"), dict)
                          and item["videoSource"].get("sourceId") == "source-youtube-" + video_id), None)
            if asset is None:
                continue
            try:
                project_assets.file(project_id, asset)
            except (OSError, ValueError, KeyError):
                continue
            youtube_runs.transition(project_id, run["id"], status="completed", error=None, uploadedAssetId=asset["id"])

    def reconcile_bilibili_imports(project_id: str) -> None:
        assets = project_assets.index(project_id)
        for run in bilibili_runs.list(project_id):
            if run["status"] not in {"failed", "cancelled"} or run.get("uploadedAssetId"):
                continue
            input_data = run.get("input", {})
            canonical = normalize_bilibili_url(str(input_data.get("url", "")))
            part = input_data.get("part")
            if canonical is None or not isinstance(part, int):
                continue
            bvid = canonical.split("/video/", 1)[-1].split("?", 1)[0]
            asset = next((item for item in assets.values() if isinstance(item.get("videoSource"), dict)
                          and item["videoSource"].get("sourceId") == f"source-bilibili-{bvid}-p{part}"), None)
            if asset is None:
                continue
            try:
                project_assets.file(project_id, asset)
            except (OSError, ValueError, KeyError):
                continue
            bilibili_runs.transition(project_id, run["id"], status="completed", error=None, uploadedAssetId=asset["id"])

    def reconcile_douyin_imports(project_id: str) -> None:
        assets = project_assets.index(project_id)
        for run in douyin_runs.list(project_id):
            if run["status"] not in {"failed", "cancelled"} or run.get("uploadedAssetId"):
                continue
            canonical = normalize_douyin_url(str(run.get("input", {}).get("url", "")))
            video_id = (canonical or "").rsplit("/", 1)[-1]
            asset = next((item for item in assets.values() if isinstance(item.get("videoSource"), dict)
                          and item["videoSource"].get("sourceId") == "source-douyin-" + video_id), None)
            if asset is None:
                continue
            try:
                project_assets.file(project_id, asset)
            except (OSError, ValueError, KeyError):
                continue
            douyin_runs.transition(project_id, run["id"], status="completed", error=None, uploadedAssetId=asset["id"])

    def reconcile_xiaohongshu_imports(project_id: str) -> None:
        assets = project_assets.index(project_id)
        for run in xiaohongshu_runs.list(project_id):
            if run["status"] not in {"failed", "cancelled"} or run.get("uploadedAssetId"):
                continue
            canonical = normalize_xiaohongshu_url(str(run.get("input", {}).get("url", "")))
            video_id = (canonical or "").split("?", 1)[0].rsplit("/", 1)[-1]
            asset = next((item for item in assets.values() if isinstance(item.get("videoSource"), dict)
                          and item["videoSource"].get("sourceId") == "source-xiaohongshu-" + video_id), None)
            if asset is None:
                continue
            try:
                project_assets.file(project_id, asset)
            except (OSError, ValueError, KeyError):
                continue
            xiaohongshu_runs.transition(project_id, run["id"], status="completed", error=None, uploadedAssetId=asset["id"])

    def reconcile_kuaishou_imports(project_id: str) -> None:
        assets = project_assets.index(project_id)
        for run in kuaishou_runs.list(project_id):
            if run["status"] not in {"failed", "cancelled"} or run.get("uploadedAssetId"): continue
            video_id = str(run.get("input", {}).get("url", "")).rsplit("/", 1)[-1]
            asset = next((item for item in assets.values() if isinstance(item.get("videoSource"), dict) and item["videoSource"].get("sourceId") == "source-kuaishou-" + video_id), None)
            if asset:
                try: project_assets.file(project_id, asset); kuaishou_runs.transition(project_id, run["id"], status="completed", error=None, uploadedAssetId=asset["id"])
                except (OSError, ValueError, KeyError): continue

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
            workspace_transaction.recover(project_id)
            project_assets.recover_local_video_installations(project_id)
            project_assets.recover_youtube_video_installations(project_id)
            project_assets.recover_bilibili_video_installations(project_id)
            project_assets.recover_douyin_video_installations(project_id)
            project_assets.recover_xiaohongshu_video_installations(project_id)
            project_assets.recover_kuaishou_video_installations(project_id)
            reconcile_youtube_imports(project_id)
            reconcile_bilibili_imports(project_id)
            reconcile_douyin_imports(project_id)
            reconcile_xiaohongshu_imports(project_id)
            reconcile_kuaishou_imports(project_id)
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

    def organize(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ShotOrganizationError as error:
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
        default_layout = _initial_canvas_layout(project_id, state, "project", project_id)
        try:
            project_layout = layout_store.read(project_id, default_layout)
        except (OSError, ValueError):
            # Layout persistence is intentionally independent: a broken layout
            # must not make otherwise valid production content unavailable.
            project_layout = default_layout
        public = _public_state(project_id, state, project_layout)
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
                workspace_transaction.recover(project_id)
                project_assets.recover_local_video_installations(project_id)
                project_assets.recover_youtube_video_installations(project_id)
                project_assets.recover_bilibili_video_installations(project_id)
                project_assets.recover_douyin_video_installations(project_id)
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
        for path in base.glob("*/preproduction/youtube-import/runs.json"):
            try:
                if not path.is_symlink():
                    project_id = path.parents[2].name
                    youtube_runs.recover(project_id)
                    reconcile_youtube_imports(project_id)
            except (OSError, ValueError):
                continue
        for path in base.glob("*/preproduction/bilibili-import/runs.json"):
            try:
                if not path.is_symlink():
                    project_id = path.parents[2].name
                    bilibili_runs.recover(project_id)
                    reconcile_bilibili_imports(project_id)
            except (OSError, ValueError):
                continue
        for path in base.glob("*/preproduction/douyin-import/runs.json"):
            try:
                if not path.is_symlink():
                    project_id = path.parents[2].name
                    douyin_runs.recover(project_id)
                    reconcile_douyin_imports(project_id)
            except (OSError, ValueError):
                continue

        for path in base.glob("*/preproduction/xiaohongshu-import/runs.json"):
            try:
                if not path.is_symlink():
                    project_id = path.parents[2].name
                    xiaohongshu_runs.recover(project_id)
                    reconcile_xiaohongshu_imports(project_id)
            except (OSError, ValueError):
                continue
        for path in base.glob("*/preproduction/kuaishou-import/runs.json"):
            try:
                if not path.is_symlink():
                    project_id = path.parents[2].name; kuaishou_runs.recover(project_id); reconcile_kuaishou_imports(project_id)
            except (OSError, ValueError): continue

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
            original_state = deepcopy(state)
            requested_layout = deepcopy(body.canvasLayout)
            current_layout = None
            if requested_layout is not None:
                try:
                    validate_layout(requested_layout)
                    if requested_layout["scope"] != {"type": "project", "id": project_id}:
                        raise ValueError("画布布局范围无效")
                    current_layout = layout_store.read(
                        project_id,
                        _initial_canvas_layout(project_id, state, "project", project_id),
                    )
                    if requested_layout["layoutRevision"] != current_layout["layoutRevision"]:
                        raise CanvasLayoutConflict
                except CanvasLayoutConflict:
                    fail("preproduction_layout_conflict", "画布布局已被更新，请重新读取后重试。")
                except ValueError as error:
                    fail("preproduction_layout_invalid", str(error), 422)
                except OSError:
                    fail("preproduction_layout_storage_invalid", "画布布局无法读取。", 503)
            production = production_for(project_id, state)
            decide(production.edit, body.brief.model_dump(), [shot.model_dump() for shot in body.shots])
            if body.scenes is not None:
                organize(
                    ShotOrganization(state).replace_snapshot,
                    [scene.model_dump() for scene in body.scenes],
                    [{"id": shot.id, "sceneId": shot.sceneId, "rank": shot.rank} for shot in body.shots],
                    increment_revision=False,
                )
            if requested_layout is None:
                save(project_id, state)
            else:
                try:
                    default_layout = _initial_canvas_layout(project_id, state, "project", project_id)
                    desired_nodes = {
                        node_id: {**default_node, **requested_layout["nodes"].get(node_id, {})}
                        for node_id, default_node in default_layout["nodes"].items()
                    }
                    requested_viewport = requested_layout.get("viewport")
                    if desired_nodes != current_layout["nodes"] or requested_viewport != current_layout.get("viewport"):
                        next_layout = {
                            "scope": deepcopy(default_layout["scope"]),
                            "layoutRevision": current_layout["layoutRevision"] + 1,
                            "nodes": desired_nodes,
                            "viewport": deepcopy(requested_viewport),
                        }
                        validate_layout(next_layout)
                        workspace_transaction.commit(
                            project_id,
                            original_state,
                            current_layout,
                            state,
                            next_layout,
                        )
                    else:
                        save(project_id, state)
                except WorkspaceTransactionError as error:
                    if error.recovery_pending:
                        fail("preproduction_transaction_failed", "镜头创建失败且无法恢复原工作区，请停止编辑并检查存储。", 503)
                    fail("preproduction_layout_storage_failed", "画布布局无法保存，镜头更改已撤销。", 503)
                except (OSError, ValueError):
                    fail("preproduction_layout_storage_failed", "画布布局无法保存，镜头更改未写入。", 503)
            return response(project_id, state)

    def layout_default(project_id: str, state: dict[str, Any], scope_type: str, scope_id: str) -> dict[str, Any]:
        if scope_type == "project" and scope_id != project_id:
            fail("preproduction_layout_scope_missing", "画布布局范围不存在。", 404)
        if scope_type == "scene" and not any(scene["id"] == scope_id for scene in state["scenes"]):
            fail("preproduction_layout_scope_missing", "画布布局范围不存在。", 404)
        if scope_type == "shot" and not any(shot["id"] == scope_id for shot in state["shots"]):
            fail("preproduction_layout_scope_missing", "画布布局范围不存在。", 404)
        try:
            return _initial_canvas_layout(project_id, state, scope_type, scope_id)
        except (KeyError, TypeError, ValueError):
            fail("preproduction_layout_scope_invalid", "画布布局范围无效。", 422)

    @router.get("/layouts/{scope_type}/{scope_id}")
    def get_canvas_layout(project_id: str, scope_type: Literal["project", "scene", "shot"], scope_id: str):
        project_for(project_id)
        with lock:
            state = state_for(project_id)
            try:
                return layout_store.read(project_id, layout_default(project_id, state, scope_type, scope_id))
            except (OSError, ValueError):
                fail("preproduction_layout_storage_invalid", "画布布局无法读取。", 503)

    @router.put("/layouts/{scope_type}/{scope_id}")
    def save_canvas_layout(
        project_id: str,
        scope_type: Literal["project", "scene", "shot"],
        scope_id: str,
        body: CanvasLayoutUpdate,
    ):
        project_for(project_id)
        with lock:
            state = state_for(project_id)
            try:
                return layout_store.apply(
                    project_id,
                    layout_default(project_id, state, scope_type, scope_id),
                    body.layoutRevision,
                    body.nodes,
                    body.viewport,
                )
            except CanvasLayoutConflict:
                fail("preproduction_layout_conflict", "画布布局已被更新，请重新读取后重试。")
            except ValueError as error:
                fail("preproduction_layout_invalid", str(error), 422)
            except OSError:
                fail("preproduction_layout_storage_failed", "画布布局无法保存。", 503)

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
            try:
                project_assets.update_metadata(
                    project_id, state, asset_id, name=body.name, notes=body.notes,
                    save=lambda value: save(project_id, value),
                )
            except AssetStateConflict:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            return response(project_id, state)

    @router.post("/assets/{asset_id}/delete")
    def delete_asset(project_id: str, asset_id: str, body: RevisionRequest):
        project_for(project_id)
        with source_lock or nullcontext(), lock:
            state = editable_assets(project_id, body.revision)
            managed_asset(state, asset_id)
            try:
                project_assets.delete(project_id, state, asset_id, save=lambda value: save(project_id, value))
            except AssetStateConflict:
                fail("preproduction_conflict", "前置工作台已被更新，请刷新后重试。")
            except AssetInUseError as error:
                raise HTTPException(status_code=409, detail={
                    "code": "asset_in_use",
                    "message": "素材仍被引用：" + "；".join(item["label"] for item in error.references[:5]),
                    "references": error.references,
                }) from None
            except AssetReferencesUnavailableError:
                fail("asset_references_unavailable", "无法完整读取素材引用，暂不能删除。", 503)
            except AssetFileUnavailableError:
                fail("preproduction_asset_unavailable", "素材文件不可用。", 409)
            except OSError:
                fail("asset_delete_failed", "素材删除未能完成，请刷新检查后重试。", 503)
            return response(project_id, state)

    def youtube_error(error: YoutubeImportError) -> None:
        messages = {
            "requires_login": "该 YouTube 内容需要已授权会话；系统不会自动登录、读取浏览器 Cookie 或绕过访问限制。",
            "temporarily_unavailable": "YouTube 暂时不可用或站点适配已变化，请稍后重试。",
            "unsupported_link": "该链接或内容受 DRM、私密或其他不支持的访问限制，不能导入。",
        }
        statuses = {"requires_login": 409, "temporarily_unavailable": 503, "unsupported_link": 422}
        fail(f"youtube_{error.status}", messages[error.status], statuses[error.status])

    def youtube_cookie() -> str | None:
        try:
            store = youtube_session_store or SystemYoutubeCookieStore()
            return store.get()
        except Exception:
            # Public videos continue without a session.  A protected video will
            # deterministically report requires_login; no credential detail leaks.
            return None

    def youtube_probe(body: YoutubeImportRequest, *, cancelled=None):
        canonical = normalize_youtube_url(body.url)
        if canonical is None:
            raise YoutubeImportError("unsupported_link")
        try:
            probe = youtube_adapter.probe
            kwargs = {"cookie": youtube_cookie() if body.useSession else None}
            if cancelled is not None and "cancelled" in inspect.signature(probe).parameters:
                kwargs["cancelled"] = cancelled
            result = probe(canonical, **kwargs)
        except YoutubeImportError:
            raise
        except Exception:
            raise YoutubeImportError("temporarily_unavailable") from None
        if result.status != "available":
            raise YoutubeImportError(result.status)
        if result.canonical_url != canonical:
            # An adapter may enrich metadata but may not turn a recognized URL
            # into another source identity after the user reviewed disclosure.
            raise YoutubeImportError("temporarily_unavailable")
        if result.duration is not None and result.duration > MAX_YOUTUBE_SOURCE_DURATION_SECONDS:
            raise YoutubeImportError("unsupported_link")
        return result

    @router.post("/youtube/probe")
    def probe_youtube(project_id: str, body: YoutubeImportRequest):
        project_for(project_id)
        if not body.externalRequestAccepted:
            fail("youtube_external_request_confirmation_required", "请先确认将向 YouTube 发起元数据请求。", 422)
        try:
            probe = youtube_probe(body)
        except YoutubeImportError as error:
            youtube_error(error)
        return {
            "canonicalUrl": probe.canonical_url, "videoId": probe.video_id, "title": probe.title,
            "durationSeconds": probe.duration, "uploader": probe.uploader, "status": "available",
            "adapter": {"id": "yt-dlp", "version": str(getattr(youtube_adapter, "version", YOUTUBE_ADAPTER["version"]))},
            "externalRequest": youtube_external_request(),
            # E2 controlled verification and ordinary metadata probing are not
            # E3 evidence.  Only an authorised gold-link record may set this.
            "lastRealVerificationAt": None,
        }

    @router.get("/youtube/disclosure")
    def youtube_disclosure(project_id: str):
        project_for(project_id)
        return youtube_external_request()

    @router.post("/youtube/import")
    def import_youtube(project_id: str, body: YoutubeImportRequest):
        project_for(project_id)
        if not body.rightsConfirmed:
            fail("youtube_video_rights_required", "请先确认你拥有该 YouTube 视频的使用权，再导入项目。", 422)
        if not body.externalRequestAccepted:
            fail("youtube_external_request_confirmation_required", "请先确认将向 YouTube 发起下载请求。", 422)
        canonical = normalize_youtube_url(body.url)
        if canonical is None:
            youtube_error(YoutubeImportError("unsupported_link"))
        snapshot = {"url": canonical, "role": body.role, "useSession": body.useSession, "adapter": dict(YOUTUBE_ADAPTER)}
        key = body.idempotencyKey.strip() or freeze_run_input(snapshot).fingerprint
        try:
            run, created = youtube_runs.create_or_reuse(project_id, snapshot, key)
        except OSError:
            fail("youtube_run_storage_failed", "YouTube 导入运行无法保存。", 503)
        if created:
            youtube_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("youtube-import", run["id"], lambda run_id: execute_youtube_import(project_id, run_id), project_id=project_id):
                youtube_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
                fail("youtube_queue_unavailable", "YouTube 导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_youtube_import_run(run)})

    @router.get("/youtube/imports/{run_id}")
    def get_youtube_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            return {"run": public_youtube_import_run(youtube_runs.get(project_id, run_id))}
        except (KeyError, ValueError):
            fail("youtube_import_not_found", "YouTube 导入运行不存在。", 404)
        except OSError:
            fail("youtube_run_storage_failed", "YouTube 导入运行无法读取。", 503)

    @router.get("/youtube/imports")
    def list_youtube_imports(project_id: str):
        project_for(project_id)
        try:
            return {"runs": [public_youtube_import_run(run) for run in youtube_runs.list(project_id)]}
        except OSError:
            fail("youtube_run_storage_failed", "YouTube 导入运行无法读取。", 503)

    @router.post("/youtube/imports/{run_id}/cancel")
    def cancel_youtube_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            with source_lock or nullcontext(), lock:
                run = youtube_runs.request_cancel(project_id, run_id)
        except KeyError:
            fail("youtube_import_not_found", "YouTube 导入运行不存在。", 404)
        except ValueError:
            fail("youtube_import_not_cancellable", "该 YouTube 导入运行当前不能取消。", 409)
        event = youtube_cancel_events.get(run_id)
        if event is not None: event.set()
        if run["status"] == "cancelled": shutil.rmtree(youtube_runs.staging_dir(project_id, run_id), ignore_errors=True)
        return JSONResponse(status_code=202, content={"run": public_youtube_import_run(run)})

    @router.post("/youtube/imports/{run_id}/retry")
    def retry_youtube_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            run, created = youtube_runs.retry(project_id, run_id)
        except KeyError:
            fail("youtube_import_not_found", "YouTube 导入运行不存在。", 404)
        except ValueError:
            fail("youtube_import_not_retryable", "该 YouTube 导入运行当前不能重试。", 409)
        except OSError:
            fail("youtube_run_storage_failed", "YouTube 导入运行无法保存。", 503)
        if not created:
            return JSONResponse(status_code=202, content={"run": public_youtube_import_run(run)})
        youtube_cancel_events[run["id"]] = Event()
        if not compute_queue.submit("youtube-import", run["id"], lambda candidate_id: execute_youtube_import(project_id, candidate_id), project_id=project_id):
            youtube_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
            fail("youtube_queue_unavailable", "YouTube 导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_youtube_import_run(run)})

    def execute_youtube_import(project_id: str, run_id: str) -> None:
        temporary: Path | None = None
        staging = youtube_runs.staging_dir(project_id, run_id)
        event = youtube_cancel_events.setdefault(run_id, Event())
        try:
            run = youtube_runs.get(project_id, run_id)
            if run["status"] == "cancelled" or event.is_set():
                youtube_runs.transition(project_id, run_id, status="cancelled", error="cancelled"); return
            if run["status"] != "queued": return
            youtube_runs.transition(project_id, run_id, status="running", error=None)
            body = YoutubeImportRequest(url=run["input"]["url"], role=run["input"]["role"], rightsConfirmed=True, externalRequestAccepted=True, useSession=bool(run["input"].get("useSession", False)))
            probe = youtube_probe(body, cancelled=event.is_set)
            if event.is_set(): raise YoutubeImportCancelled()
            staging.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=staging, prefix=".youtube-", suffix=".mp4"); os.close(descriptor); temporary = Path(name)
            download = youtube_adapter.download
            accepts_cancelled = "cancelled" in inspect.signature(download).parameters
            downloaded = Path(download(probe, temporary, cookie=youtube_cookie() if body.useSession else None, **({"cancelled": event.is_set} if accepts_cancelled else {})))
            if event.is_set(): raise YoutubeImportCancelled()
            if downloaded.parent.resolve(strict=True) != staging.resolve(strict=True) or not downloaded.name.startswith(".youtube-"):
                raise OSError("unmanaged YouTube staging output")
            if downloaded != temporary: temporary.unlink(missing_ok=True); temporary = downloaded
            metadata = probe_downloaded_youtube_video(temporary, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
            if event.is_set(): raise YoutubeImportCancelled()
            with source_lock or nullcontext(), lock:
                if event.is_set(): raise YoutubeImportCancelled()
                installed = project_assets.install_youtube_video(project_id, source=temporary, staging_root=staging, probe=probe,
                                                                  role=run["input"]["role"], metadata=metadata, confirmed_at=utc_timestamp())
                youtube_runs.transition(project_id, run_id, status="completed", error=None, uploadedAssetId=installed["id"])
                temporary = None
        except YoutubeImportCancelled:
            youtube_runs.transition(project_id, run_id, status="cancelled", error="cancelled")
        except YoutubeImportError as error:
            youtube_runs.transition(project_id, run_id, status="failed", error="youtube_" + error.status)
        except ValueError:
            youtube_runs.transition(project_id, run_id, status="failed", error="youtube_download_invalid")
        except Exception:
            youtube_runs.transition(project_id, run_id, status="failed", error="youtube_temporarily_unavailable")
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            youtube_cancel_events.pop(run_id, None)

    def bilibili_error(error: BilibiliImportError) -> None:
        messages = {
            "requires_login": "该 B站内容需要已授权会话；系统不会自动登录、读取浏览器 Cookie 或绕过访问限制。",
            "temporarily_unavailable": "B站暂时不可用或站点适配已变化，请稍后重试。",
            "unsupported_link": "该链接或内容受会员、私密、合集、互动、直播、DRM 或其他不支持的访问限制，不能导入。",
        }
        statuses = {"requires_login": 409, "temporarily_unavailable": 503, "unsupported_link": 422}
        fail(f"bilibili_{error.status}", messages[error.status], statuses[error.status])

    def bilibili_cookie(required: bool) -> str | None:
        if not required: return None
        try:
            cookie = (bilibili_session_store or SystemBilibiliCookieStore()).get()
        except Exception:
            cookie = None
        if not cookie: raise BilibiliImportError("requires_login")
        return cookie

    def bilibili_probe(body: BilibiliImportRequest, *, cancelled=None):
        canonical = normalize_bilibili_url(body.url) or bilibili_short_link_resolver(body.url)
        if canonical is None: raise BilibiliImportError("unsupported_link")
        try:
            probe = bilibili_adapter.probe
            kwargs = {"cookie": bilibili_cookie(body.useSession)}
            if cancelled is not None and "cancelled" in inspect.signature(probe).parameters: kwargs["cancelled"] = cancelled
            result = probe(canonical, **kwargs)
        except (BilibiliImportError, YoutubeImportCancelled):
            raise
        except Exception:
            raise BilibiliImportError("temporarily_unavailable") from None
        if result.status != "available" or result.canonical_url != canonical.split("?", 1)[0]:
            raise BilibiliImportError("temporarily_unavailable")
        if not result.parts or any(part.duration is not None and part.duration > MAX_BILIBILI_SOURCE_DURATION_SECONDS for part in result.parts):
            raise BilibiliImportError("unsupported_link")
        return result

    def selected_bilibili_part(body: BilibiliImportRequest, probe) -> Any:
        url_part = bilibili_url_part(body.url)
        if body.part is not None and url_part is not None and body.part != url_part:
            raise BilibiliImportError("unsupported_link")
        selected = body.part if body.part is not None else url_part
        if selected is None and len(probe.parts) != 1:
            fail("bilibili_part_selection_required", "该视频有多个分P；请明确选择一个分P，系统不会自动下载合集或默认第一集。", 422)
        selected = selected or 1
        part = next((candidate for candidate in probe.parts if candidate.number == selected), None)
        if part is None: raise BilibiliImportError("unsupported_link")
        return part

    @router.get("/bilibili/disclosure")
    def bilibili_disclosure(project_id: str):
        project_for(project_id)
        return bilibili_external_request()

    @router.post("/bilibili/probe")
    def probe_bilibili(project_id: str, body: BilibiliImportRequest):
        project_for(project_id)
        if not body.externalRequestAccepted:
            fail("bilibili_external_request_confirmation_required", "请先确认将向 B站 发起元数据请求。", 422)
        try:
            probe = bilibili_probe(body)
        except BilibiliImportError as error:
            bilibili_error(error)
        return {
            "canonicalUrl": probe.canonical_url, "videoId": probe.video_id, "title": probe.title, "uploader": probe.uploader,
            "parts": [{"number": part.number, "cid": part.cid, "title": part.title, "durationSeconds": part.duration} for part in probe.parts],
            "status": "available", "adapter": {"id": "yt-dlp", "version": str(getattr(bilibili_adapter, "version", BILIBILI_ADAPTER["version"]))},
            "externalRequest": bilibili_external_request(), "lastRealVerificationAt": None,
        }

    @router.post("/bilibili/import")
    def import_bilibili(project_id: str, body: BilibiliImportRequest):
        project_for(project_id)
        if not body.rightsConfirmed:
            fail("bilibili_video_rights_required", "请先确认你拥有该 B站视频的使用权，再导入项目。", 422)
        if not body.externalRequestAccepted:
            fail("bilibili_external_request_confirmation_required", "请先确认将向 B站 发起下载请求。", 422)
        try:
            probe = bilibili_probe(body)
            part = selected_bilibili_part(body, probe)
        except BilibiliImportError as error:
            bilibili_error(error)
        snapshot = {"url": probe.canonical_url, "part": part.number, "role": body.role, "useSession": body.useSession, "adapter": dict(BILIBILI_ADAPTER)}
        key = body.idempotencyKey.strip() or freeze_run_input(snapshot).fingerprint
        try:
            run, created = bilibili_runs.create_or_reuse(project_id, snapshot, key)
        except OSError:
            fail("bilibili_run_storage_failed", "B站导入运行无法保存。", 503)
        if created:
            bilibili_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("bilibili-import", run["id"], lambda run_id: execute_bilibili_import(project_id, run_id), project_id=project_id):
                bilibili_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
                fail("bilibili_queue_unavailable", "B站导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_bilibili_import_run(run)})

    @router.get("/bilibili/imports/{run_id}")
    def get_bilibili_import(project_id: str, run_id: str):
        project_for(project_id)
        try: return {"run": public_bilibili_import_run(bilibili_runs.get(project_id, run_id))}
        except (KeyError, ValueError): fail("bilibili_import_not_found", "B站导入运行不存在。", 404)
        except OSError: fail("bilibili_run_storage_failed", "B站导入运行无法读取。", 503)

    @router.post("/bilibili/imports/{run_id}/cancel")
    def cancel_bilibili_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            with source_lock or nullcontext(), lock: run = bilibili_runs.request_cancel(project_id, run_id)
        except KeyError: fail("bilibili_import_not_found", "B站导入运行不存在。", 404)
        except ValueError: fail("bilibili_import_not_cancellable", "该 B站导入运行当前不能取消。", 409)
        if (event := bilibili_cancel_events.get(run_id)) is not None: event.set()
        if run["status"] == "cancelled": shutil.rmtree(bilibili_runs.staging_dir(project_id, run_id), ignore_errors=True)
        return JSONResponse(status_code=202, content={"run": public_bilibili_import_run(run)})

    @router.post("/bilibili/imports/{run_id}/retry")
    def retry_bilibili_import(project_id: str, run_id: str):
        project_for(project_id)
        try: run, created = bilibili_runs.retry(project_id, run_id)
        except KeyError: fail("bilibili_import_not_found", "B站导入运行不存在。", 404)
        except ValueError: fail("bilibili_import_not_retryable", "该 B站导入运行当前不能重试。", 409)
        except OSError: fail("bilibili_run_storage_failed", "B站导入运行无法保存。", 503)
        if created:
            bilibili_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("bilibili-import", run["id"], lambda candidate: execute_bilibili_import(project_id, candidate), project_id=project_id):
                bilibili_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
                fail("bilibili_queue_unavailable", "B站导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_bilibili_import_run(run)})

    def execute_bilibili_import(project_id: str, run_id: str) -> None:
        temporary: Path | None = None
        staging = bilibili_runs.staging_dir(project_id, run_id)
        event = bilibili_cancel_events.setdefault(run_id, Event())
        try:
            run = bilibili_runs.get(project_id, run_id)
            if run["status"] == "cancelled" or event.is_set():
                bilibili_runs.transition(project_id, run_id, status="cancelled", error="cancelled"); return
            if run["status"] != "queued": return
            bilibili_runs.transition(project_id, run_id, status="running", error=None)
            body = BilibiliImportRequest(url=run["input"]["url"], part=run["input"]["part"], role=run["input"]["role"], rightsConfirmed=True, externalRequestAccepted=True, useSession=bool(run["input"].get("useSession", False)))
            probe = bilibili_probe(body, cancelled=event.is_set)
            part = selected_bilibili_part(body, probe)
            if event.is_set(): raise YoutubeImportCancelled()
            staging.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=staging, prefix=".bilibili-", suffix=".mp4"); os.close(descriptor); temporary = Path(name)
            download = bilibili_adapter.download
            accepts_cancelled = "cancelled" in inspect.signature(download).parameters
            downloaded = Path(download(probe, part, temporary, cookie=bilibili_cookie(body.useSession), **({"cancelled": event.is_set} if accepts_cancelled else {})))
            if event.is_set(): raise YoutubeImportCancelled()
            if downloaded.parent.resolve(strict=True) != staging.resolve(strict=True) or not downloaded.name.startswith(".bilibili-"):
                raise OSError("unmanaged Bilibili staging output")
            if downloaded != temporary: temporary.unlink(missing_ok=True); temporary = downloaded
            metadata = probe_downloaded_bilibili_video(temporary, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
            if event.is_set(): raise YoutubeImportCancelled()
            provenance = bilibili_video_source(probe=probe, part=part, metadata=metadata, content_hash=hashlib.sha256(temporary.read_bytes()).hexdigest(), confirmed_at=utc_timestamp())
            with source_lock or nullcontext(), lock:
                if event.is_set(): raise YoutubeImportCancelled()
                installed = project_assets.install_platform_video(project_id, source=temporary, staging_root=staging, platform="bilibili", probe=probe,
                                                                    role=run["input"]["role"], metadata=metadata, confirmed_at=utc_timestamp(), provenance=provenance)
                bilibili_runs.transition(project_id, run_id, status="completed", error=None, uploadedAssetId=installed["id"])
                temporary = None
        except YoutubeImportCancelled:
            bilibili_runs.transition(project_id, run_id, status="cancelled", error="cancelled")
        except BilibiliImportError as error:
            bilibili_runs.transition(project_id, run_id, status="failed", error="bilibili_" + error.status)
        except ValueError:
            bilibili_runs.transition(project_id, run_id, status="failed", error="bilibili_download_invalid")
        except Exception:
            bilibili_runs.transition(project_id, run_id, status="failed", error="bilibili_temporarily_unavailable")
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            bilibili_cancel_events.pop(run_id, None)

    def douyin_error(error: DouyinImportError) -> None:
        messages = {
            "requires_login": "该抖音内容需要已授权会话；系统不会自动登录、读取浏览器 Cookie 或绕过访问限制。",
            "temporarily_unavailable": "抖音暂时不可用或站点适配已变化，请稍后重试。",
            "unsupported_link": "该链接或内容受私密、地区、DRM 或其他不支持的访问限制，不能导入。",
        }
        statuses = {"requires_login": 409, "temporarily_unavailable": 503, "unsupported_link": 422}
        fail(f"douyin_{error.status}", messages[error.status], statuses[error.status])

    def douyin_cookie(required: bool) -> str | None:
        if not required:
            return None
        try:
            cookie = (douyin_session_store or SystemDouyinCookieStore()).get()
        except Exception:
            cookie = None
        if not cookie:
            raise DouyinImportError("requires_login")
        return cookie

    def douyin_probe(body: DouyinImportRequest, *, cancelled=None, redirect_facts: list[dict[str, Any]] | None = None):
        raw_url = extract_douyin_share_url(body.url)
        if raw_url is None:
            raise DouyinImportError("unsupported_link")
        canonical = normalize_douyin_url(raw_url)
        if canonical is None:
            canonical = douyin_short_link_resolver(raw_url, facts=redirect_facts) if redirect_facts is not None else douyin_short_link_resolver(raw_url)
        if canonical is None:
            raise DouyinImportError("unsupported_link")
        try:
            probe = douyin_adapter.probe
            kwargs = {"cookie": douyin_cookie(body.useSession)}
            if cancelled is not None and "cancelled" in inspect.signature(probe).parameters:
                kwargs["cancelled"] = cancelled
            result = probe(canonical, **kwargs)
        except (DouyinImportError, YoutubeImportCancelled):
            raise
        except Exception:
            raise DouyinImportError("temporarily_unavailable") from None
        if result.status != "available" or result.canonical_url != canonical:
            raise DouyinImportError("temporarily_unavailable")
        if result.duration is not None and result.duration > MAX_DOUYIN_SOURCE_DURATION_SECONDS:
            raise DouyinImportError("unsupported_link")
        return result

    @router.get("/douyin/disclosure")
    def douyin_disclosure(project_id: str):
        project_for(project_id)
        return douyin_external_request()

    @router.post("/douyin/probe")
    def probe_douyin(project_id: str, body: DouyinImportRequest):
        project_for(project_id)
        if not body.externalRequestAccepted:
            fail("douyin_external_request_confirmation_required", "请先确认将向抖音发起元数据请求。", 422)
        try:
            probe = douyin_probe(body, redirect_facts=[])
        except DouyinImportError as error:
            douyin_error(error)
        return {
            "canonicalUrl": probe.canonical_url, "videoId": probe.video_id, "title": probe.title,
            "durationSeconds": probe.duration, "uploader": probe.uploader, "status": "available",
            "adapter": {"id": "yt-dlp", "version": str(getattr(douyin_adapter, "version", DOUYIN_ADAPTER["version"]))},
            "externalRequest": douyin_external_request(), "lastRealVerificationAt": None,
        }

    @router.post("/douyin/import")
    def import_douyin(project_id: str, body: DouyinImportRequest):
        project_for(project_id)
        if not body.rightsConfirmed:
            fail("douyin_video_rights_required", "请先确认你拥有该抖音视频的使用权，再导入项目。", 422)
        if not body.externalRequestAccepted:
            fail("douyin_external_request_confirmation_required", "请先确认将向抖音发起下载请求。", 422)
        redirect_facts: list[dict[str, Any]] = []
        try:
            probe = douyin_probe(body, redirect_facts=redirect_facts)
        except DouyinImportError as error:
            douyin_error(error)
        snapshot = {"url": probe.canonical_url, "role": body.role, "useSession": body.useSession,
                    "adapter": dict(DOUYIN_ADAPTER), "redirectFacts": redirect_facts}
        key = body.idempotencyKey.strip() or freeze_run_input(snapshot).fingerprint
        try:
            run, created = douyin_runs.create_or_reuse(project_id, snapshot, key)
        except OSError:
            fail("douyin_run_storage_failed", "抖音导入运行无法保存。", 503)
        if created:
            douyin_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("douyin-import", run["id"], lambda run_id: execute_douyin_import(project_id, run_id), project_id=project_id):
                douyin_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
                fail("douyin_queue_unavailable", "抖音导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_douyin_import_run(run)})

    @router.get("/douyin/imports/{run_id}")
    def get_douyin_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            return {"run": public_douyin_import_run(douyin_runs.get(project_id, run_id))}
        except (KeyError, ValueError):
            fail("douyin_import_not_found", "抖音导入运行不存在。", 404)
        except OSError:
            fail("douyin_run_storage_failed", "抖音导入运行无法读取。", 503)

    @router.post("/douyin/imports/{run_id}/cancel")
    def cancel_douyin_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            with source_lock or nullcontext(), lock:
                run = douyin_runs.request_cancel(project_id, run_id)
        except KeyError:
            fail("douyin_import_not_found", "抖音导入运行不存在。", 404)
        except ValueError:
            fail("douyin_import_not_cancellable", "该抖音导入运行当前不能取消。", 409)
        if (event := douyin_cancel_events.get(run_id)) is not None:
            event.set()
        if run["status"] == "cancelled":
            shutil.rmtree(douyin_runs.staging_dir(project_id, run_id), ignore_errors=True)
        return JSONResponse(status_code=202, content={"run": public_douyin_import_run(run)})

    @router.post("/douyin/imports/{run_id}/retry")
    def retry_douyin_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            run, created = douyin_runs.retry(project_id, run_id)
        except KeyError:
            fail("douyin_import_not_found", "抖音导入运行不存在。", 404)
        except ValueError:
            fail("douyin_import_not_retryable", "该抖音导入运行当前不能重试。", 409)
        except OSError:
            fail("douyin_run_storage_failed", "抖音导入运行无法保存。", 503)
        if created:
            douyin_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("douyin-import", run["id"], lambda candidate: execute_douyin_import(project_id, candidate), project_id=project_id):
                douyin_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
                fail("douyin_queue_unavailable", "抖音导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_douyin_import_run(run)})

    def execute_douyin_import(project_id: str, run_id: str) -> None:
        temporary: Path | None = None
        staging = douyin_runs.staging_dir(project_id, run_id)
        event = douyin_cancel_events.setdefault(run_id, Event())
        try:
            run = douyin_runs.get(project_id, run_id)
            if run["status"] == "cancelled" or event.is_set():
                douyin_runs.transition(project_id, run_id, status="cancelled", error="cancelled"); return
            if run["status"] != "queued": return
            douyin_runs.transition(project_id, run_id, status="running", error=None)
            body = DouyinImportRequest(url=run["input"]["url"], role=run["input"]["role"], rightsConfirmed=True,
                                       externalRequestAccepted=True, useSession=bool(run["input"].get("useSession", False)))
            probe = douyin_probe(body, cancelled=event.is_set)
            if event.is_set(): raise YoutubeImportCancelled()
            staging.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=staging, prefix=".douyin-", suffix=".mp4"); os.close(descriptor); temporary = Path(name)
            download = douyin_adapter.download
            kwargs = {"cookie": douyin_cookie(body.useSession)}
            if "cancelled" in inspect.signature(download).parameters: kwargs["cancelled"] = event.is_set
            downloaded = Path(download(probe, temporary, **kwargs))
            if event.is_set(): raise YoutubeImportCancelled()
            if downloaded.parent.resolve(strict=True) != staging.resolve(strict=True) or not downloaded.name.startswith(".douyin-"):
                raise OSError("unmanaged Douyin staging output")
            if downloaded != temporary: temporary.unlink(missing_ok=True); temporary = downloaded
            metadata = probe_downloaded_douyin_video(temporary, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
            if event.is_set(): raise YoutubeImportCancelled()
            provenance = douyin_video_source(probe=probe, metadata=metadata, content_hash=hashlib.sha256(temporary.read_bytes()).hexdigest(),
                                              confirmed_at=utc_timestamp(), redirect_facts=run["input"].get("redirectFacts"))
            with source_lock or nullcontext(), lock:
                if event.is_set(): raise YoutubeImportCancelled()
                installed = project_assets.install_platform_video(project_id, source=temporary, staging_root=staging, platform="douyin", probe=probe,
                                                                    role=run["input"]["role"], metadata=metadata, confirmed_at=utc_timestamp(), provenance=provenance)
                douyin_runs.transition(project_id, run_id, status="completed", error=None, uploadedAssetId=installed["id"])
                temporary = None
        except YoutubeImportCancelled:
            douyin_runs.transition(project_id, run_id, status="cancelled", error="cancelled")
        except DouyinImportError as error:
            douyin_runs.transition(project_id, run_id, status="failed", error="douyin_" + error.status)
        except ValueError:
            douyin_runs.transition(project_id, run_id, status="failed", error="douyin_download_invalid")
        except Exception:
            douyin_runs.transition(project_id, run_id, status="failed", error="douyin_temporarily_unavailable")
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            douyin_cancel_events.pop(run_id, None)
    def xiaohongshu_error(error: XiaohongshuImportError) -> None:
        messages = {
            "requires_login": "该小红书内容需要已授权会话；系统不会自动登录、读取浏览器 Cookie 或绕过访问限制。",
            "temporarily_unavailable": "小红书暂时不可用或站点适配已变化，请稍后重试。",
            "unsupported_link": "该链接或内容受私密、地区、DRM 或其他不支持的访问限制，不能导入。",
        }
        statuses = {"requires_login": 409, "temporarily_unavailable": 503, "unsupported_link": 422}
        fail(f"xiaohongshu_{error.status}", messages[error.status], statuses[error.status])

    def xiaohongshu_cookie(required: bool) -> str | None:
        if not required:
            return None
        try:
            cookie = (xiaohongshu_session_store or SystemXiaohongshuCookieStore()).get()
        except Exception:
            cookie = None
        if not cookie:
            raise XiaohongshuImportError("requires_login")
        return cookie

    def xiaohongshu_probe(body: XiaohongshuImportRequest, *, cancelled=None, redirect_facts: list[dict[str, Any]] | None = None):
        raw_url = extract_xiaohongshu_share_url(body.url)
        if raw_url is None:
            raise XiaohongshuImportError("unsupported_link")
        canonical = normalize_xiaohongshu_url(raw_url)
        if canonical is None:
            canonical = xiaohongshu_short_link_resolver(raw_url, facts=redirect_facts) if redirect_facts is not None else xiaohongshu_short_link_resolver(raw_url)
        if canonical is None:
            raise XiaohongshuImportError("unsupported_link")
        try:
            probe = xiaohongshu_adapter.probe
            kwargs = {"cookie": xiaohongshu_cookie(body.useSession)}
            if cancelled is not None and "cancelled" in inspect.signature(probe).parameters:
                kwargs["cancelled"] = cancelled
            result = probe(canonical, **kwargs)
        except (XiaohongshuImportError, YoutubeImportCancelled):
            raise
        except Exception:
            raise XiaohongshuImportError("temporarily_unavailable") from None
        if result.status != "available" or result.canonical_url != canonical:
            raise XiaohongshuImportError("temporarily_unavailable")
        if result.duration is not None and result.duration > MAX_XIAOHONGSHU_SOURCE_DURATION_SECONDS:
            raise XiaohongshuImportError("unsupported_link")
        return result

    @router.get("/xiaohongshu/disclosure")
    def xiaohongshu_disclosure(project_id: str):
        project_for(project_id)
        return xiaohongshu_external_request()

    @router.post("/xiaohongshu/probe")
    def probe_xiaohongshu(project_id: str, body: XiaohongshuImportRequest):
        project_for(project_id)
        if not body.rightsConfirmed:
            fail("xiaohongshu_video_rights_required", "请先确认你拥有该小红书视频的使用权，再读取链接。", 422)
        if not body.externalRequestAccepted:
            fail("xiaohongshu_external_request_confirmation_required", "请先确认将向小红书发起元数据请求。", 422)
        try:
            probe = xiaohongshu_probe(body, redirect_facts=[])
        except XiaohongshuImportError as error:
            xiaohongshu_error(error)
        return {
            "canonicalUrl": probe.canonical_url, "videoId": probe.video_id, "title": probe.title,
            "durationSeconds": probe.duration, "uploader": probe.uploader, "status": "available",
            "adapter": {"id": "yt-dlp", "version": str(getattr(xiaohongshu_adapter, "version", XIAOHONGSHU_ADAPTER["version"]))},
            "externalRequest": xiaohongshu_external_request(), "lastRealVerificationAt": None,
        }

    @router.post("/xiaohongshu/import")
    def import_xiaohongshu(project_id: str, body: XiaohongshuImportRequest):
        project_for(project_id)
        if not body.rightsConfirmed:
            fail("xiaohongshu_video_rights_required", "请先确认你拥有该小红书视频的使用权，再导入项目。", 422)
        if not body.externalRequestAccepted:
            fail("xiaohongshu_external_request_confirmation_required", "请先确认将向小红书发起下载请求。", 422)
        redirect_facts: list[dict[str, Any]] = []
        rights_confirmed_at = utc_timestamp()
        try:
            probe = xiaohongshu_probe(body, redirect_facts=redirect_facts)
        except XiaohongshuImportError as error:
            xiaohongshu_error(error)
        request_url = probe.canonical_url
        snapshot = {"url": request_url.split("?", 1)[0], "role": body.role, "useSession": body.useSession, "rightsConfirmedAt": rights_confirmed_at,
                    "adapter": dict(XIAOHONGSHU_ADAPTER), "sourceFacts": xiaohongshu_source_facts(body.url, probe.canonical_url, redirect_facts)}
        key = body.idempotencyKey.strip() or freeze_run_input(snapshot).fingerprint
        try:
            run, created = xiaohongshu_runs.create_or_reuse(project_id, snapshot, key)
        except OSError:
            fail("xiaohongshu_run_storage_failed", "小红书导入运行无法保存。", 503)
        if created:
            if request_url != snapshot["url"]:
                try:
                    (xiaohongshu_request_store or SystemXiaohongshuRequestStore()).set(run["id"], request_url)
                except Exception:
                    xiaohongshu_runs.transition(project_id, run["id"], status="failed", error="request_url_storage_unavailable")
                    fail("xiaohongshu_request_url_storage_unavailable", "小红书请求链接安全存储不可用。", 503)
            xiaohongshu_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("xiaohongshu-import", run["id"], lambda run_id: execute_xiaohongshu_import(project_id, run_id), project_id=project_id):
                xiaohongshu_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
                fail("xiaohongshu_queue_unavailable", "小红书导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_xiaohongshu_import_run(run)})

    @router.get("/xiaohongshu/imports/{run_id}")
    def get_xiaohongshu_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            return {"run": public_xiaohongshu_import_run(xiaohongshu_runs.get(project_id, run_id))}
        except (KeyError, ValueError):
            fail("xiaohongshu_import_not_found", "小红书导入运行不存在。", 404)
        except OSError:
            fail("xiaohongshu_run_storage_failed", "小红书导入运行无法读取。", 503)

    @router.post("/xiaohongshu/imports/{run_id}/cancel")
    def cancel_xiaohongshu_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            with source_lock or nullcontext(), lock:
                run = xiaohongshu_runs.request_cancel(project_id, run_id)
        except KeyError:
            fail("xiaohongshu_import_not_found", "小红书导入运行不存在。", 404)
        except ValueError:
            fail("xiaohongshu_import_not_cancellable", "该小红书导入运行当前不能取消。", 409)
        if (event := xiaohongshu_cancel_events.get(run_id)) is not None:
            event.set()
        if run["status"] == "cancelled":
            shutil.rmtree(xiaohongshu_runs.staging_dir(project_id, run_id), ignore_errors=True)
        return JSONResponse(status_code=202, content={"run": public_xiaohongshu_import_run(run)})

    @router.post("/xiaohongshu/imports/{run_id}/retry")
    def retry_xiaohongshu_import(project_id: str, run_id: str):
        project_for(project_id)
        try:
            previous = xiaohongshu_runs.get(project_id, run_id)
            run, created = xiaohongshu_runs.retry(project_id, run_id)
        except KeyError:
            fail("xiaohongshu_import_not_found", "小红书导入运行不存在。", 404)
        except ValueError:
            fail("xiaohongshu_import_not_retryable", "该小红书导入运行当前不能重试。", 409)
        except OSError:
            fail("xiaohongshu_run_storage_failed", "小红书导入运行无法保存。", 503)
        if created:
            previous_url = xiaohongshu_request_url(run_id, previous["input"]["url"])
            if previous_url != run["input"]["url"]:
                try:
                    (xiaohongshu_request_store or SystemXiaohongshuRequestStore()).set(run["id"], previous_url)
                except Exception:
                    xiaohongshu_runs.transition(project_id, run["id"], status="failed", error="request_url_storage_unavailable")
                    fail("xiaohongshu_request_url_storage_unavailable", "小红书请求链接安全存储不可用。", 503)
            xiaohongshu_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("xiaohongshu-import", run["id"], lambda candidate: execute_xiaohongshu_import(project_id, candidate), project_id=project_id):
                xiaohongshu_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
                fail("xiaohongshu_queue_unavailable", "小红书导入队列不可用，请稍后重试。", 503)
        return JSONResponse(status_code=202, content={"run": public_xiaohongshu_import_run(run)})

    def execute_xiaohongshu_import(project_id: str, run_id: str) -> None:
        temporary: Path | None = None
        staging = xiaohongshu_runs.staging_dir(project_id, run_id)
        event = xiaohongshu_cancel_events.setdefault(run_id, Event())
        try:
            run = xiaohongshu_runs.get(project_id, run_id)
            if run["status"] == "cancelled" or event.is_set():
                xiaohongshu_runs.transition(project_id, run_id, status="cancelled", error="cancelled"); return
            if run["status"] != "queued": return
            xiaohongshu_runs.transition(project_id, run_id, status="running", error=None)
            body = XiaohongshuImportRequest(url=xiaohongshu_request_url(run_id, run["input"]["url"]), role=run["input"]["role"], rightsConfirmed=True,
                                       externalRequestAccepted=True, useSession=bool(run["input"].get("useSession", False)))
            probe = xiaohongshu_probe(body, cancelled=event.is_set)
            if event.is_set(): raise YoutubeImportCancelled()
            staging.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=staging, prefix=".xiaohongshu-", suffix=".mp4"); os.close(descriptor); temporary = Path(name)
            download = xiaohongshu_adapter.download
            kwargs = {"cookie": xiaohongshu_cookie(body.useSession)}
            if "cancelled" in inspect.signature(download).parameters: kwargs["cancelled"] = event.is_set
            downloaded = Path(download(probe, temporary, **kwargs))
            if event.is_set(): raise YoutubeImportCancelled()
            if downloaded.parent.resolve(strict=True) != staging.resolve(strict=True) or not downloaded.name.startswith(".xiaohongshu-"):
                raise OSError("unmanaged Xiaohongshu staging output")
            if downloaded != temporary: temporary.unlink(missing_ok=True); temporary = downloaded
            metadata = probe_downloaded_xiaohongshu_video(temporary, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
            if event.is_set(): raise YoutubeImportCancelled()
            provenance = xiaohongshu_video_source(
                probe=probe, metadata=metadata, content_hash=hashlib.sha256(temporary.read_bytes()).hexdigest(),
                confirmed_at=run["input"]["rightsConfirmedAt"], source_facts=run["input"].get("sourceFacts"),
            )
            with source_lock or nullcontext(), lock:
                if event.is_set(): raise YoutubeImportCancelled()
                installed = project_assets.install_platform_video(
                    project_id, source=temporary, staging_root=staging, platform="xiaohongshu", probe=probe,
                    role=run["input"]["role"], metadata=metadata, confirmed_at=run["input"]["rightsConfirmedAt"], provenance=provenance,
                )
                xiaohongshu_runs.transition(project_id, run_id, status="completed", error=None, uploadedAssetId=installed["id"])
                temporary = None
        except YoutubeImportCancelled:
            xiaohongshu_runs.transition(project_id, run_id, status="cancelled", error="cancelled")
        except XiaohongshuImportError as error:
            xiaohongshu_runs.transition(project_id, run_id, status="failed", error="xiaohongshu_" + error.status)
        except ValueError:
            xiaohongshu_runs.transition(project_id, run_id, status="failed", error="xiaohongshu_download_invalid")
        except Exception:
            xiaohongshu_runs.transition(project_id, run_id, status="failed", error="xiaohongshu_temporarily_unavailable")
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            xiaohongshu_cancel_events.pop(run_id, None)
            try:
                (xiaohongshu_request_store or SystemXiaohongshuRequestStore()).delete(run_id)
            except Exception:
                pass

    def kuaishou_cookie(required: bool):
        if not required: return None
        try: cookie = (kuaishou_session_store or SystemKuaishouCookieStore()).get()
        except Exception: cookie = None
        if not cookie: raise KuaishouImportError("requires_login")
        return cookie
    def kuaishou_probe(body, *, cancelled=None, facts=None):
        raw = extract_kuaishou_share_url(body.url)
        canonical = normalize_kuaishou_url(raw) if raw else None
        canonical = canonical or (kuaishou_short_link_resolver(raw, facts=facts) if raw else None)
        if not canonical: raise KuaishouImportError("unsupported_link")
        try: result = kuaishou_adapter.probe(canonical, cookie=kuaishou_cookie(body.useSession), cancelled=cancelled)
        except (KuaishouImportError, YoutubeImportCancelled): raise
        except Exception: raise KuaishouImportError("temporarily_unavailable") from None
        if result.status != "available":
            if result.status in {"requires_login", "temporarily_unavailable", "unsupported_link"}:
                raise KuaishouImportError(result.status)
            raise KuaishouImportError("temporarily_unavailable")
        if result.canonical_url != canonical or result.video_id != canonical.rsplit("/", 1)[-1] or not result.media_url:
            raise KuaishouImportError("temporarily_unavailable")
        if result.duration is not None and (not math.isfinite(result.duration) or result.duration <= 0 or result.duration > MAX_KUAISHOU_SOURCE_DURATION_SECONDS):
            raise KuaishouImportError("unsupported_link")
        return result
    def kuaishou_fail(error):
        messages = {"requires_login": "该快手内容需要已授权会话；系统不会自动登录或绕过访问限制。", "temporarily_unavailable": "快手暂时不可用或站点适配已变化，请稍后重试。", "unsupported_link": "该链接或内容受私密、地区、DRM 或其他不支持的访问限制，不能导入。"}
        fail("kuaishou_" + error.status, messages[error.status], {"requires_login":409,"temporarily_unavailable":503,"unsupported_link":422}[error.status])
    @router.get("/kuaishou/disclosure")
    def kuaishou_disclosure(project_id: str): project_for(project_id); return kuaishou_external_request()
    @router.post("/kuaishou/probe")
    def probe_kuaishou(project_id: str, body: KuaishouImportRequest):
        project_for(project_id)
        if not body.rightsConfirmed: fail("kuaishou_video_rights_required", "请先确认你拥有该快手视频的使用权，再读取链接。", 422)
        if not body.externalRequestAccepted: fail("kuaishou_external_request_confirmation_required", "请先确认将向快手发起元数据请求。", 422)
        try: probe = kuaishou_probe(body, facts=[])
        except KuaishouImportError as error: kuaishou_fail(error)
        return {"canonicalUrl": probe.canonical_url, "videoId": probe.video_id, "title": probe.title, "durationSeconds": probe.duration, "uploader": probe.uploader, "status":"available", "adapter":dict(KUAISHOU_ADAPTER), "externalRequest":kuaishou_external_request(), "lastRealVerificationAt":None}

    def execute_kuaishou_import(project_id: str, run_id: str):
        temporary = None; staging = kuaishou_runs.staging_dir(project_id, run_id); event = kuaishou_cancel_events.setdefault(run_id, Event())
        try:
            run = kuaishou_runs.get(project_id, run_id)
            if run["status"] == "cancelled" or event.is_set(): kuaishou_runs.transition(project_id, run_id, status="cancelled", error="cancelled"); return
            if run["status"] != "queued": return
            kuaishou_runs.transition(project_id, run_id, status="running", error=None)
            body = KuaishouImportRequest(url=run["input"]["url"], role=run["input"]["role"], rightsConfirmed=True, externalRequestAccepted=True, useSession=bool(run["input"].get("useSession")))
            probe = kuaishou_probe(body, cancelled=event.is_set)
            staging.mkdir(parents=True, exist_ok=True); descriptor, name = tempfile.mkstemp(dir=staging, prefix=".kuaishou-", suffix=".mp4"); os.close(descriptor); temporary = Path(name)
            downloaded = Path(kuaishou_adapter.download(probe, temporary, cookie=kuaishou_cookie(body.useSession), cancelled=event.is_set))
            if event.is_set(): raise YoutubeImportCancelled()
            if downloaded.parent.resolve(strict=True) != staging.resolve(strict=True) or not downloaded.name.startswith(".kuaishou-"): raise OSError("unmanaged Kuaishou staging output")
            if downloaded != temporary: temporary.unlink(missing_ok=True); temporary = downloaded
            metadata = probe_downloaded_xiaohongshu_video(temporary, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
            if event.is_set(): raise YoutubeImportCancelled()
            digest = hashlib.sha256()
            with temporary.open("rb") as source:
                for chunk in iter(lambda: source.read(65536), b""):
                    if event.is_set(): raise YoutubeImportCancelled()
                    digest.update(chunk)
            provenance = kuaishou_video_source(probe=probe, metadata=metadata, content_hash=digest.hexdigest(), confirmed_at=run["input"]["rightsConfirmedAt"], source_facts=run["input"].get("sourceFacts"))
            with source_lock or nullcontext(), lock:
                if event.is_set(): raise YoutubeImportCancelled()
                installed = project_assets.install_platform_video(project_id, source=temporary, staging_root=staging, platform="kuaishou", probe=probe, role=run["input"]["role"], metadata=metadata, confirmed_at=run["input"]["rightsConfirmedAt"], provenance=provenance)
                kuaishou_runs.transition(project_id, run_id, status="completed", error=None, uploadedAssetId=installed["id"]); temporary = None
        except YoutubeImportCancelled: kuaishou_runs.transition(project_id, run_id, status="cancelled", error="cancelled")
        except KuaishouImportError as error: kuaishou_runs.transition(project_id, run_id, status="failed", error="kuaishou_" + error.status)
        except ValueError: kuaishou_runs.transition(project_id, run_id, status="failed", error="kuaishou_download_invalid")
        except Exception: kuaishou_runs.transition(project_id, run_id, status="failed", error="kuaishou_temporarily_unavailable")
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True); kuaishou_cancel_events.pop(run_id, None)

    @router.post("/kuaishou/import")
    def import_kuaishou(project_id: str, body: KuaishouImportRequest):
        project_for(project_id)
        if not body.rightsConfirmed: fail("kuaishou_video_rights_required", "请先确认你拥有该快手视频的使用权，再导入项目。", 422)
        if not body.externalRequestAccepted: fail("kuaishou_external_request_confirmation_required", "请先确认将向快手发起下载请求。", 422)
        facts=[]
        try: probe = kuaishou_probe(body, facts=facts)
        except KuaishouImportError as error: kuaishou_fail(error)
        snapshot={"url":probe.canonical_url,"role":body.role,"useSession":body.useSession,"rightsConfirmedAt":utc_timestamp(),"adapter":dict(KUAISHOU_ADAPTER),"sourceFacts":kuaishou_source_facts(body.url,probe.canonical_url,facts)}
        run, created = kuaishou_runs.create_or_reuse(project_id, snapshot, body.idempotencyKey.strip() or freeze_run_input(snapshot).fingerprint)
        if created:
            kuaishou_cancel_events[run["id"]]=Event()
            if not compute_queue.submit("kuaishou-import", run["id"], lambda candidate: execute_kuaishou_import(project_id,candidate), project_id=project_id):
                kuaishou_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable"); fail("kuaishou_queue_unavailable","快手导入队列不可用，请稍后重试。",503)
        return JSONResponse(status_code=202, content={"run":public_kuaishou_import_run(run)})
    @router.get("/kuaishou/imports/{run_id}")
    def get_kuaishou_import(project_id: str, run_id: str):
        project_for(project_id)
        try: return {"run":public_kuaishou_import_run(kuaishou_runs.get(project_id,run_id))}
        except (KeyError,ValueError): fail("kuaishou_import_not_found","快手导入运行不存在。",404)
    @router.post("/kuaishou/imports/{run_id}/cancel")
    def cancel_kuaishou_import(project_id: str, run_id: str):
        project_for(project_id)
        try: run=kuaishou_runs.request_cancel(project_id,run_id)
        except KeyError: fail("kuaishou_import_not_found","快手导入运行不存在。",404)
        except ValueError: fail("kuaishou_import_not_cancellable","该快手导入运行当前不能取消。",409)
        if run_id in kuaishou_cancel_events: kuaishou_cancel_events[run_id].set()
        return JSONResponse(status_code=202,content={"run":public_kuaishou_import_run(run)})
    @router.post("/kuaishou/imports/{run_id}/retry")
    def retry_kuaishou_import(project_id: str, run_id: str):
        project_for(project_id)
        try: run, created = kuaishou_runs.retry(project_id, run_id)
        except KeyError: fail("kuaishou_import_not_found", "快手导入运行不存在。", 404)
        except ValueError: fail("kuaishou_import_not_retryable", "该快手导入运行当前不能重试。", 409)
        if created:
            kuaishou_cancel_events[run["id"]] = Event()
            if not compute_queue.submit("kuaishou-import", run["id"], lambda candidate: execute_kuaishou_import(project_id, candidate), project_id=project_id):
                kuaishou_runs.transition(project_id, run["id"], status="failed", error="queue_unavailable")
        return JSONResponse(status_code=202, content={"run": public_kuaishou_import_run(run)})

    @router.post("/assets")
    async def upload_asset(project_id: str, role: Literal["character", "scene", "motion", "audio", "reference"] = Query(...), file: UploadFile = File(...), resultForShot: Optional[str] = Query(None), revision: Optional[int] = Query(None), derivation: Optional[str] = Form(None), rightsConfirmed: bool = Form(False)):
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
                if metadata["kind"] == "video" and not rightsConfirmed:
                    fail("local_video_rights_required", "请先确认你拥有该本地视频的使用权，再导入项目。", 422)
                if metadata["kind"] == "video":
                    try:
                        installed = project_assets.install_local_video(
                            project_id,
                            source=temporary,
                            staging_root=root,
                            original_name=file.filename or "素材.mp4",
                            role=role,
                            metadata=metadata,
                            confirmed_at=utc_timestamp(),
                            apply=(
                                lambda latest, asset: decide(
                                    production_for(project_id, latest).attach_result,
                                    resultForShot,
                                    asset["id"],
                                )
                            ) if resultForShot is not None else None,
                            deduplicate=resultForShot is None,
                        )
                    except LocalVideoInstallationConflict as error:
                        fail("local_video_source_conflict", str(error), 409)
                    except OSError:
                        fail("preproduction_storage_failed", "本地视频未能安全入库，请检查存储后重试。", 503)
                    latest = state_for(project_id)
                    return {**response(project_id, latest), "uploadedAssetId": installed["id"]}
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
                return {**response(project_id, state), "uploadedAssetId": asset_id}
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


def _public_state(project_id: str, value: dict[str, Any], canvas_layout: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    result = {
        "schemaVersion": value["schemaVersion"],
        "revision": value["revision"],
        "brief": {"inputKind": "reference_video", **value["brief"]},
        "assets": [],
        "scenes": [dict(scene) for scene in value["scenes"]],
        "shots": [],
    }
    for asset in value["assets"]:
        result["assets"].append({key: item for key, item in asset.items() if key not in ("file", "sourceReferenceId") and not key.startswith("_")} | {"url": f"/api/projects/{project_id}/preproduction/assets/{asset['id']}/file"})
    for shot in value["shots"]:
        result["shots"].append({key: item for key, item in shot.items() if not key.startswith("_") and key != "nodes"} | {"resultVersions": public_result_versions(value["brief"], shot), "nodes": [
            {key: item for key, item in node.items() if key != "runId" and not key.startswith("_")} for node in shot["nodes"]
        ]})
    result["workflow"] = _workflow_projection(value)
    result["canvasLayout"] = canvas_layout or _initial_canvas_layout(project_id, value, "project", project_id)
    return result


def _workflow_projection(value: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    assets = {asset["id"]: asset for asset in value["assets"]}
    for shot in value["shots"]:
        shot_node_id = _workflow_id("shot", shot["id"])
        nodes.append({"id": shot_node_id, "type": "shot", "shotId": shot["id"]})
        asset_ids = list(dict.fromkeys([
            *shot.get("assetIds", []),
            *(step["input"].split(":", 1)[1] for step in shot["nodes"] if step["input"].startswith("asset:")),
        ]))
        for asset_id in asset_ids:
            asset = assets.get(asset_id, {})
            nodes.append({
                "id": _workflow_id("asset", shot["id"], asset_id),
                "type": "asset",
                "assetId": asset_id,
                "ownerShotId": shot["id"],
                "role": asset.get("role", "reference"),
            })
        for step in shot["nodes"]:
            process_id = _workflow_id("process", shot["id"], step["id"])
            nodes.append({
                "id": process_id,
                "type": "process",
                "ownerShotId": shot["id"],
                "processKind": step["kind"],
                "config": {"input": step["input"], "params": step["params"]},
                "status": step["status"],
                "error": step.get("error"),
                "artifacts": step.get("artifacts", []),
            })
            if step["input"].startswith("asset:"):
                source_id = _workflow_id("asset", shot["id"], step["input"].split(":", 1)[1])
                source_port = "asset"
            elif step["input"].startswith("node:"):
                source_id = _workflow_id("process", shot["id"], step["input"].split(":", 1)[1])
                source_port = "output"
            else:
                continue
            edges.append({
                "id": f"edge:{source_id}:{process_id}",
                "kind": "data",
                "source": {"nodeId": source_id, "portId": source_port},
                "target": {"nodeId": process_id, "portId": "input"},
            })
    return {"nodes": nodes, "edges": edges}


def _initial_canvas_layout(
    project_id: str,
    value: dict[str, Any],
    scope_type: str = "project",
    scope_id: Optional[str] = None,
) -> dict[str, Any]:
    scope_id = scope_id or project_id
    layout_nodes: dict[str, dict[str, Any]] = {}
    scenes = sorted(value["scenes"], key=lambda scene: scene["rank"])
    shots_by_scene: dict[str, list[dict[str, Any]]] = {scene["id"]: [] for scene in scenes}
    for shot in sorted(value["shots"], key=lambda item: item["rank"]):
        shots_by_scene.setdefault(shot["sceneId"], []).append(shot)
    if scope_type == "project":
        y = 0
        for scene in scenes:
            shots = shots_by_scene.get(scene["id"], [])
            layout_nodes[scene["id"]] = {
                "x": 0,
                "y": y,
                "width": max(320, 80 + 260 * len(shots)),
                "height": 240,
                "collapsed": False,
            }
            for index, shot in enumerate(shots):
                layout_nodes[_workflow_id("shot", shot["id"])] = {"x": 40 + index * 260, "y": y + 80}
            y += 280
    elif scope_type == "scene":
        shots = shots_by_scene.get(scope_id, [])
        layout_nodes = {
            _workflow_id("shot", shot["id"]): {"x": 40 + index * 260, "y": 80}
            for index, shot in enumerate(shots)
        }
    elif scope_type == "shot":
        workflow_nodes = [
            node for node in _workflow_projection(value)["nodes"]
            if node.get("ownerShotId") == scope_id and node["type"] in ("asset", "process")
        ]
        asset_index = process_index = 0
        for node in workflow_nodes:
            if node["type"] == "asset":
                layout_nodes[node["id"]] = {"x": 40, "y": 40 + asset_index * 140}
                asset_index += 1
            else:
                layout_nodes[node["id"]] = {"x": 320, "y": 40 + process_index * 140}
                process_index += 1
    else:
        raise ValueError("画布布局范围无效")
    return {
        "scope": {"type": scope_type, "id": scope_id},
        "layoutRevision": 0,
        "nodes": layout_nodes,
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _workflow_id(kind: str, *parts: str) -> str:
    return ":".join((kind, *(quote(part, safe="") for part in parts)))


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
