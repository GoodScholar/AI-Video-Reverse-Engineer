"""Durable, project-scoped definitions for the independent creative canvas."""
from __future__ import annotations

import json
import os
import re
import tempfile
from uuid import uuid4
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .preproduction import safe_child
from .reference_video import validate_storage_id


MAX_CANVAS_NODES = 500
MAX_CANVAS_EDGES = 1_000
MAX_COORDINATE = 1_000_000
MAX_CANVAS_IMAGE_DIMENSION = 8192
MAX_CANVAS_IMAGE_PIXELS = 64_000_000
_CANVAS_SIZE_PATTERN = re.compile(r"([1-9][0-9]{1,3})x([1-9][0-9]{1,3})$")
_CANVAS_RATIO_PATTERN = re.compile(r"([1-9][0-9]{0,3}):([1-9][0-9]{0,3})$")


class CanvasPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=-MAX_COORDINATE, le=MAX_COORDINATE, strict=True)
    y: float = Field(ge=-MAX_COORDINATE, le=MAX_COORDINATE, strict=True)


class CanvasViewport(CanvasPosition):
    zoom: float = Field(default=1, ge=0.1, le=8, strict=True)


class CanvasSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    comfyUrl: str = Field(default="", max_length=2048)
    checkpoint: str = Field(default="", max_length=512)


class CanvasParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    negativePrompt: str = Field(default="", max_length=12_000)
    width: int = Field(default=1024, ge=64, le=MAX_CANVAS_IMAGE_DIMENSION, strict=True)
    height: int = Field(default=1024, ge=64, le=MAX_CANVAS_IMAGE_DIMENSION, strict=True)
    # `model` is the legacy single-provider selection. New nodes keep both
    # stable provider/model identity and the configuration version they chose.
    model: Optional[str] = Field(default=None, min_length=1, max_length=256)
    providerId: Optional[str] = Field(default=None, min_length=1, max_length=64)
    modelId: Optional[str] = Field(default=None, min_length=1, max_length=256)
    configurationVersion: Optional[str] = Field(default=None, min_length=1, max_length=128)
    size: Optional[str] = Field(default=None, min_length=3, max_length=32)
    ratio: Optional[str] = Field(default=None, min_length=3, max_length=32)
    count: int = Field(default=1, ge=1, le=15, strict=True)
    steps: int = Field(default=20, ge=1, le=150, strict=True)
    seed: int = Field(default=-1, ge=-1, le=2_147_483_647, strict=True)
    denoise: float = Field(default=1, ge=0, le=1, strict=True)

    @model_validator(mode="after")
    def synchronize_explicit_size(self) -> "CanvasParams":
        if (self.providerId is None) != (self.modelId is None):
            raise ValueError("生成模型必须同时包含 providerId 和 modelId")
        if self.configurationVersion is not None and self.providerId is None:
            raise ValueError("配置版本必须与稳定生成模型一起保存")
        width, height = self.width, self.height
        if self.size is not None:
            match = _CANVAS_SIZE_PATTERN.fullmatch(self.size)
            if match is None:
                raise ValueError("图片尺寸必须是宽x高像素值")
            width, height = int(match.group(1)), int(match.group(2))
        if width > MAX_CANVAS_IMAGE_DIMENSION or height > MAX_CANVAS_IMAGE_DIMENSION or width * height > MAX_CANVAS_IMAGE_PIXELS:
            raise ValueError("图片尺寸超出画布服务安全范围")
        if width < 64 or height < 64:
            raise ValueError("图片尺寸不能小于 64 像素")
        if self.ratio is not None:
            ratio = _CANVAS_RATIO_PATTERN.fullmatch(self.ratio)
            if ratio is None or width * int(ratio.group(2)) != height * int(ratio.group(1)):
                raise ValueError("图片尺寸必须与图片比例一致")
        self.width, self.height = width, height
        return self


_RESULT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class CanvasOutputRef(BaseModel):
    """Stable identity of one concrete result within an immutable generation run."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    runId: str = Field(min_length=1, max_length=160)
    resultId: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_ids(self) -> "CanvasOutputRef":
        if not _RESULT_ID_PATTERN.fullmatch(self.runId) or not _RESULT_ID_PATTERN.fullmatch(self.resultId):
            raise ValueError("输出结果标识无效")
        return self


class CanvasNode(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["text", "media", "generate"]
    position: CanvasPosition
    label: str = Field(default="", max_length=1_000)
    text: Optional[str] = Field(default=None, max_length=12_000)
    prompt: Optional[str] = Field(default=None, max_length=12_000)
    providerId: Optional[str] = Field(default=None, min_length=1, max_length=64)
    modelId: Optional[str] = Field(default=None, min_length=1, max_length=256)
    configurationVersion: Optional[str] = Field(default=None, min_length=1, max_length=128)
    currentTextVersionId: Optional[str] = Field(default=None, min_length=1, max_length=160)
    fontSize: Optional[int] = Field(default=None, ge=10, le=32, strict=True)
    assetId: Optional[str] = Field(default=None, max_length=100)
    outputAssetId: Optional[str] = Field(default=None, max_length=100)
    currentOutputRef: Optional[CanvasOutputRef] = None
    stale: Optional[bool] = Field(default=None, strict=True)
    referenceOrder: Optional[list[str]] = Field(default=None, max_length=MAX_CANVAS_EDGES)
    params: CanvasParams = Field(default_factory=CanvasParams)
    width: Optional[float] = Field(default=None, ge=64, le=MAX_CANVAS_IMAGE_DIMENSION, strict=True)
    height: Optional[float] = Field(default=None, ge=64, le=MAX_CANVAS_IMAGE_DIMENSION, strict=True)
    freeResize: Optional[bool] = Field(default=None, strict=True)
    parentId: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_fields_for_kind(self) -> "CanvasNode":
        try:
            validate_storage_id(self.id)
            if self.assetId is not None:
                validate_storage_id(self.assetId)
            if self.outputAssetId is not None:
                validate_storage_id(self.outputAssetId)
            if self.parentId is not None:
                validate_storage_id(self.parentId)
            for edge_id in self.referenceOrder or []:
                validate_storage_id(edge_id)
        except ValueError as error:
            raise ValueError("节点或素材标识无效") from error
        if self.kind == "text" and self.text is None:
            raise ValueError("文本节点必须包含 text")
        if (self.providerId is None) != (self.modelId is None):
            raise ValueError("文本生成模型必须同时包含 providerId 和 modelId")
        if self.configurationVersion is not None and self.providerId is None:
            raise ValueError("文本生成配置版本必须与模型一起保存")
        if self.kind != "text" and any(value is not None for value in (self.prompt, self.providerId, self.modelId, self.configurationVersion, self.currentTextVersionId, self.fontSize)):
            raise ValueError("只有文本节点可以包含文本生成字段")
        if self.kind == "media" and self.assetId is None:
            raise ValueError("媒体节点必须包含 assetId")
        if self.kind == "media" and self.text is not None:
            raise ValueError("媒体节点不能包含 text")
        if self.kind != "media" and self.assetId is not None:
            raise ValueError("只有媒体节点可以包含 assetId")
        if self.kind not in {"generate", "text"} and (self.outputAssetId is not None or self.currentOutputRef is not None or self.stale is not None or self.referenceOrder is not None):
            raise ValueError("只有生成或文本节点可以包含输出、失效状态或引用顺序")
        if self.kind == "text" and any(value is not None for value in (self.outputAssetId, self.currentOutputRef, self.stale)):
            raise ValueError("文本节点不能包含图片输出字段")
        return self


class CanvasGroup(BaseModel):
    """Source-compatible group node; child nodes use coordinates relative to parentId."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(default="", max_length=1_000)
    parentId: Optional[str] = Field(default=None, max_length=100)
    position: CanvasPosition = Field(default_factory=lambda: CanvasPosition(x=0, y=0))
    width: float = Field(default=220, ge=64, le=MAX_CANVAS_IMAGE_DIMENSION, strict=True)
    height: float = Field(default=160, ge=64, le=MAX_CANVAS_IMAGE_DIMENSION, strict=True)

    @model_validator(mode="after")
    def validate_ids(self) -> "CanvasGroup":
        try:
            validate_storage_id(self.id)
            if self.parentId is not None:
                validate_storage_id(self.parentId)
        except ValueError as error:
            raise ValueError("分组标识无效") from error
        return self


class CanvasEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    sourceHandle: Literal["text", "image", "video", "audio"]
    targetHandle: Literal["prompt", "image", "in"]

    @model_validator(mode="after")
    def validate_ids(self) -> "CanvasEdge":
        try:
            validate_storage_id(self.id)
            validate_storage_id(self.source)
            validate_storage_id(self.target)
        except ValueError as error:
            raise ValueError("连线标识无效") from error
        return self


class CanvasDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(default=0, ge=0, le=2_147_483_647, strict=True)
    nodes: list[CanvasNode] = Field(default_factory=list, max_length=MAX_CANVAS_NODES)
    edges: list[CanvasEdge] = Field(default_factory=list, max_length=MAX_CANVAS_EDGES)
    groups: list[CanvasGroup] = Field(default_factory=list, max_length=MAX_CANVAS_NODES)
    viewport: CanvasViewport = Field(default_factory=lambda: CanvasViewport(x=0, y=0))
    settings: CanvasSettings = Field(default_factory=CanvasSettings)


def default_document() -> dict[str, Any]:
    return CanvasDocument().model_dump(mode="json")


def validate_document(document: CanvasDocument, assets: Optional[dict[str, dict[str, Any]]]) -> None:
    node_ids = [node.id for node in document.nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("画布节点标识重复")
    edge_ids = [edge.id for edge in document.edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("画布连线标识重复")

    nodes = {node.id: node for node in document.nodes}
    group_ids = [group.id for group in document.groups]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("画布分组标识重复")
    if set(group_ids) & set(nodes):
        raise ValueError("分组标识不能与节点标识重复")
    groups = {group.id: group for group in document.groups}
    for node in document.nodes:
        if node.parentId is not None and node.parentId not in groups:
            raise ValueError("节点分组不存在")
    for group in document.groups:
        if group.parentId is not None and group.parentId not in groups:
            raise ValueError("父分组不存在")

    visited_groups: set[str] = set()
    visiting_groups: set[str] = set()

    def visit_group(group_id: str) -> None:
        if group_id in visiting_groups:
            raise ValueError("画布分组不能形成循环")
        if group_id in visited_groups:
            return
        visiting_groups.add(group_id)
        parent_id = groups[group_id].parentId
        if parent_id is not None:
            visit_group(parent_id)
        visiting_groups.remove(group_id)
        visited_groups.add(group_id)

    for group_id in groups:
        visit_group(group_id)
    for node in document.nodes:
        if assets is not None and node.kind == "media" and node.assetId not in assets:
            raise ValueError("媒体节点引用的项目素材不存在")

    edge_shapes: set[tuple[str, str, str, str]] = set()
    inbound_edges: dict[str, set[str]] = {node.id: set() for node in document.nodes}
    for edge in document.edges:
        source = nodes.get(edge.source)
        target = nodes.get(edge.target)
        if source is None or target is None:
            raise ValueError("连线引用的节点不存在")
        if edge.source == edge.target:
            raise ValueError("节点不能连接到自身")
        text_input = target.kind == "text" and edge.targetHandle == "in"
        generate_input = target.kind == "generate" and (
            (edge.sourceHandle == "text" and edge.targetHandle == "prompt")
            or (edge.sourceHandle == "image" and edge.targetHandle == "image")
        )
        if not text_input and not generate_input:
            raise ValueError("连线目标必须是生成节点或文本节点")
        if edge.sourceHandle == "text" and source.kind != "text":
            raise ValueError("text 输出必须来自文本节点")
        if edge.sourceHandle in {"image", "video", "audio"}:
            if source.kind == "media":
                source_asset_id = source.assetId
            elif source.kind == "generate":
                if edge.sourceHandle != "image":
                    raise ValueError("生成节点当前只能输出图片")
                source_asset_id = source.outputAssetId
            else:
                raise ValueError("媒体输出必须来自媒体或生成节点")
            if source_asset_id is not None and assets is not None and assets.get(source_asset_id, {}).get("kind") != edge.sourceHandle:
                raise ValueError("媒体端口与项目素材类型不一致")
        shape = (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)
        if shape in edge_shapes:
            raise ValueError("连线重复")
        edge_shapes.add(shape)
        inbound_edges[edge.target].add(edge.source)

    for node in document.nodes:
        if node.kind not in {"generate", "text"}:
            continue
        if node.kind == "generate" and assets is not None and node.outputAssetId is not None:
            asset = assets.get(node.outputAssetId)
            if asset is None or asset.get("kind") != "image":
                raise ValueError("生成节点输出引用的图片素材不存在")
        if node.referenceOrder is not None:
            order = node.referenceOrder
            if len(order) != len(set(order)):
                raise ValueError("引用顺序包含重复连线")
            inbound = {edge.id for edge in document.edges if edge.target == node.id}
            if any(edge_id not in inbound for edge_id in order):
                raise ValueError("引用顺序包含非本节点输入连线")

    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("画布连线不能形成循环")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child_id in inbound_edges[node_id]:
            visit(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)


DEFAULT_CANVAS_ID = "default"


class CreativeCanvasStore:
    """Durable documents for a project; the legacy default files remain untouched."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)

    def path(self, project_id: str, *parts: str) -> Path:
        """Legacy default-canvas path. Keep this API for existing callers and data."""
        return safe_child(self.root, "project-files", project_id, "creative-canvas", *parts)

    def _canvas_id(self, canvas_id: Optional[str]) -> str:
        value = canvas_id or DEFAULT_CANVAS_ID
        try:
            validate_storage_id(value)
        except ValueError as error:
            raise ValueError("画布标识无效") from error
        return value

    def canvas_path(self, project_id: str, canvas_id: Optional[str], *parts: str) -> Path:
        canvas_id = self._canvas_id(canvas_id)
        if canvas_id == DEFAULT_CANVAS_ID:
            return self.path(project_id, *parts)
        return self.path(project_id, "canvases", canvas_id, *parts)

    def catalog_path(self, project_id: str) -> Path:
        return self.path(project_id, "canvases", "index.json")

    @staticmethod
    def _default_canvas() -> dict[str, Any]:
        return {"id": DEFAULT_CANVAS_ID, "name": "默认画布", "createdAt": None, "updatedAt": None, "deletedAt": None}

    @staticmethod
    def _validate_canvas_record(record: Any) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise ValueError("画布目录无效")
        canvas_id, name = record.get("id"), record.get("name")
        if not isinstance(canvas_id, str) or not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("画布目录无效")
        try:
            validate_storage_id(canvas_id)
        except ValueError as error:
            raise ValueError("画布目录无效") from error
        normalized = {
            "id": canvas_id,
            "name": name.strip(),
            "createdAt": record.get("createdAt"),
            "updatedAt": record.get("updatedAt"),
            "deletedAt": record.get("deletedAt"),
        }
        if any(normalized[key] is not None and not isinstance(normalized[key], str) for key in ("createdAt", "updatedAt", "deletedAt")):
            raise ValueError("画布目录无效")
        return normalized

    def load_catalog(self, project_id: str, *, include_deleted: bool = True) -> list[dict[str, Any]]:
        path = self.catalog_path(project_id)
        if path.is_symlink():
            raise OSError("创作画布目录无效")
        if not path.exists():
            records = [self._default_canvas()]
        elif not path.is_file():
            raise OSError("创作画布目录无效")
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or not isinstance(payload.get("canvases"), list):
                    raise ValueError("创作画布目录无效")
                records = [self._validate_canvas_record(record) for record in payload["canvases"]]
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise OSError("创作画布目录无法读取") from error
            if not any(record["id"] == DEFAULT_CANVAS_ID for record in records):
                records.insert(0, self._default_canvas())
        if len({record["id"] for record in records}) != len(records):
            raise OSError("创作画布目录无效")
        return [dict(record) for record in records if include_deleted or record["deletedAt"] is None]

    def _save_catalog(self, project_id: str, records: list[dict[str, Any]]) -> None:
        validated = [self._validate_canvas_record(record) for record in records]
        if len({record["id"] for record in validated}) != len(validated):
            raise ValueError("画布标识重复")
        if not any(record["id"] == DEFAULT_CANVAS_ID for record in validated):
            validated.insert(0, self._default_canvas())
        path = self.catalog_path(project_id)
        self._write_json(path, {"schemaVersion": 1, "canvases": validated}, ".canvas-index-")

    def canvas(self, project_id: str, canvas_id: Optional[str], *, include_deleted: bool = False) -> dict[str, Any]:
        target = self._canvas_id(canvas_id)
        record = next((item for item in self.load_catalog(project_id, include_deleted=True) if item["id"] == target), None)
        if record is None or (record["deletedAt"] is not None and not include_deleted):
            raise KeyError(target)
        return record

    def list_canvases(self, project_id: str, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        return self.load_catalog(project_id, include_deleted=include_deleted)

    def create_canvas(self, project_id: str, name: str, now: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValueError("画布名称必须为 1 到 120 个字符")
        records = self.load_catalog(project_id, include_deleted=True)
        record = {"id": "canvas-" + uuid4().hex, "name": name.strip(), "createdAt": now, "updatedAt": now, "deletedAt": None}
        records.append(record)
        self._save_catalog(project_id, records)
        return dict(record)

    def rename_canvas(self, project_id: str, canvas_id: str, name: str, now: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValueError("画布名称必须为 1 到 120 个字符")
        records = self.load_catalog(project_id, include_deleted=True)
        record = next((item for item in records if item["id"] == self._canvas_id(canvas_id)), None)
        if record is None or record["deletedAt"] is not None:
            raise KeyError(canvas_id)
        record.update(name=name.strip(), updatedAt=now)
        self._save_catalog(project_id, records)
        return dict(record)

    def set_canvas_deleted(self, project_id: str, canvas_id: str, deleted: bool, now: str) -> dict[str, Any]:
        records = self.load_catalog(project_id, include_deleted=True)
        record = next((item for item in records if item["id"] == self._canvas_id(canvas_id)), None)
        if record is None:
            raise KeyError(canvas_id)
        if record["id"] == DEFAULT_CANVAS_ID and deleted:
            raise ValueError("默认画布不能删除")
        if deleted and record["deletedAt"] is not None:
            return dict(record)
        if not deleted and record["deletedAt"] is None:
            return dict(record)
        record.update(deletedAt=now if deleted else None, updatedAt=now)
        self._save_catalog(project_id, records)
        return dict(record)

    @staticmethod
    def _write_json(path: Path, payload: Any, prefix: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Optional[Path] = None
        try:
            descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=".part")
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(payload, target, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _load_document_path(self, path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise OSError("创作画布状态文件无效")
        if not path.exists():
            return default_document()
        if not path.is_file():
            raise OSError("创作画布状态文件无效")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("schemaVersion") != 1:
                raise ValueError("创作画布状态版本无效")
            document = CanvasDocument.model_validate(state.get("document"))
            validate_document(document, None)
        except (OSError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise OSError("创作画布状态无法读取") from error
        return document.model_dump(mode="json")

    def load(self, project_id: str, canvas_id: Optional[str] = None, *, include_deleted: bool = False) -> dict[str, Any]:
        if canvas_id not in (None, DEFAULT_CANVAS_ID):
            self.canvas(project_id, canvas_id, include_deleted=include_deleted)
        return self._load_document_path(self.canvas_path(project_id, canvas_id, "state.json"))

    def load_runs(self, project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        if canvas_id not in (None, DEFAULT_CANVAS_ID):
            self.canvas(project_id, canvas_id, include_deleted=True)
        path = self.canvas_path(project_id, canvas_id, "runs.json")
        if path.is_symlink():
            raise OSError("创作画布运行记录文件无效")
        if not path.exists():
            return []
        if not path.is_file():
            raise OSError("创作画布运行记录文件无效")
        try:
            runs = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
                raise ValueError("创作画布运行记录无效")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OSError("创作画布运行记录无法读取") from error
        return runs

    def save_runs(self, project_id: str, runs: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
            raise ValueError("创作画布运行记录无效")
        self._write_json(self.canvas_path(project_id, canvas_id, "runs.json"), runs, ".runs-")

    def load_text_runs(self, project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        path = self.canvas_path(project_id, canvas_id, "text-runs.json")
        if path.is_symlink():
            raise OSError("文本运行记录文件无效")
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError("文本运行记录无效")
            return value
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OSError("文本运行记录无法读取") from error

    def save_text_runs(self, project_id: str, runs: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        if not isinstance(runs, list) or any(not isinstance(item, dict) for item in runs):
            raise ValueError("文本运行记录无效")
        self._write_json(self.canvas_path(project_id, canvas_id, "text-runs.json"), runs, ".text-runs-")

    def load_batches(self, project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        if canvas_id not in (None, DEFAULT_CANVAS_ID):
            self.canvas(project_id, canvas_id, include_deleted=True)
        path = self.canvas_path(project_id, canvas_id, "batches.json")
        if path.is_symlink():
            raise OSError("创作画布批量记录文件无效")
        if not path.exists():
            return []
        if not path.is_file():
            raise OSError("创作画布批量记录文件无效")
        try:
            batches = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(batches, list) or any(not isinstance(batch, dict) for batch in batches):
                raise ValueError("创作画布批量记录无效")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OSError("创作画布批量记录无法读取") from error
        return batches

    def save_batches(self, project_id: str, batches: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        if not isinstance(batches, list) or any(not isinstance(batch, dict) for batch in batches):
            raise ValueError("创作画布批量记录无效")
        self._write_json(self.canvas_path(project_id, canvas_id, "batches.json"), batches, ".batches-")

    def save(self, project_id: str, document: dict[str, Any], canvas_id: Optional[str] = None) -> None:
        try:
            validated = CanvasDocument.model_validate(document)
        except (TypeError, ValueError, ValidationError) as error:
            raise ValueError("创作画布状态无效") from error
        self._write_json(
            self.canvas_path(project_id, canvas_id, "state.json"),
            {"schemaVersion": 1, "document": validated.model_dump(mode="json")},
            ".state-",
        )


class DocumentBatchStore:
    """Durable batch plans, isolated with their canvas run records."""

    def __init__(self, data_dir: Path):
        self._store = CreativeCanvasStore(data_dir)

    def path(self, project_id: str, canvas_id: Optional[str] = None) -> Path:
        return self._store.canvas_path(project_id, canvas_id, "batches.json")

    def load(self, project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self._store.load_batches(project_id, canvas_id)

    def save(self, project_id: str, batches: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        self._store.save_batches(project_id, batches, canvas_id)


class DocumentTextRunStore:
    """Durable text-generation runs, isolated from image runs and by canvas."""

    def __init__(self, data_dir: Path):
        self._store = CreativeCanvasStore(data_dir)

    def load(self, project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self._store.load_text_runs(project_id, canvas_id)

    def save(self, project_id: str, runs: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        self._store.save_text_runs(project_id, runs, canvas_id)


class DocumentRunStore:
    """Narrow persistence boundary for runs, isolated by canvas ID."""

    def __init__(self, data_dir: Path):
        self._store = CreativeCanvasStore(data_dir)

    def path(self, project_id: str, canvas_id: Optional[str] = None) -> Path:
        return self._store.canvas_path(project_id, canvas_id, "runs.json")

    def load(self, project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self._store.load_runs(project_id, canvas_id)

    def save(self, project_id: str, runs: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        self._store.save_runs(project_id, runs, canvas_id)


def _asset_references_for_canvas(store: CreativeCanvasStore, project_id: str, asset_id: str, canvas: dict[str, Any]) -> list[dict[str, str]]:
    canvas_id, name = canvas["id"], canvas["name"]
    suffix = "" if canvas_id == DEFAULT_CANVAS_ID else f" · {name}"
    extra = {} if canvas_id == DEFAULT_CANVAS_ID else {"canvasId": canvas_id}
    document = store.load(project_id, canvas_id, include_deleted=True)
    references = [
        {"kind": "creative_canvas_media", "label": f"创作画布{suffix} · 媒体节点 {node['id']}", "nodeId": node["id"], **extra}
        for node in document["nodes"] if node.get("kind") == "media" and node.get("assetId") == asset_id
    ]
    references.extend(
        {"kind": "creative_canvas_generate_output", "label": f"创作画布{suffix} · 生成节点 {node['id']} 的当前输出", "nodeId": node["id"], **extra}
        for node in document["nodes"] if node.get("kind") == "generate" and node.get("outputAssetId") == asset_id
    )
    for run in store.load_runs(project_id, canvas_id):
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise OSError("创作画布运行记录无效")
        run_extra = {"runId": run_id, **extra}
        if asset_id in run.get("inputAssetIds", []):
            references.append({"kind": "creative_canvas_run_input", "label": f"创作画布{suffix} · 运行 {run_id} 的输入素材", **run_extra})
        snapshot = run.get("snapshot", {})
        if not isinstance(snapshot, dict):
            raise OSError("创作画布运行快照无效")
        if asset_id in snapshot.get("inputAssetIds", []):
            references.append({"kind": "creative_canvas_run_snapshot", "label": f"创作画布{suffix} · 运行 {run_id} 的输入快照", **run_extra})
        snapshot_references = snapshot.get("references", [])
        if not isinstance(snapshot_references, list) or any(not isinstance(reference, dict) for reference in snapshot_references):
            raise OSError("创作画布运行快照无效")
        if any(reference.get("assetId") == asset_id for reference in snapshot_references):
            references.append({"kind": "creative_canvas_run_reference", "label": f"创作画布{suffix} · 运行 {run_id} 的引用快照", **run_extra})
        output_items = run.get("outputItems")
        if isinstance(output_items, list):
            for item in output_items:
                if isinstance(item, dict) and item.get("assetId") == asset_id:
                    references.append({"kind": "creative_canvas_run_output", "label": f"创作画布{suffix} · 运行 {run_id} 的输出素材", "resultId": item.get("resultId", ""), **run_extra})
        elif asset_id in run.get("outputAssetIds", []):
            references.append({"kind": "creative_canvas_run_output", "label": f"创作画布{suffix} · 运行 {run_id} 的输出素材", **run_extra})
    for run in store.load_text_runs(project_id, canvas_id):
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise OSError("文本运行记录无效")
        run_extra = {"runId": run_id, **extra}
        snapshot = run.get("snapshot", {})
        if not isinstance(snapshot, dict):
            raise OSError("文本运行快照无效")
        snapshot_references = snapshot.get("references", [])
        if not isinstance(snapshot_references, list) or any(not isinstance(reference, dict) for reference in snapshot_references):
            raise OSError("文本运行引用快照无效")
        if any(reference.get("assetId") == asset_id for reference in snapshot_references):
            references.append({"kind": "creative_canvas_text_run_reference", "label": f"创作画布{suffix} · 文本运行 {run_id} 的引用快照", **run_extra})
        for version in run.get("versions", []):
            if isinstance(version, dict) and version.get("assetId") == asset_id:
                references.append({"kind": "creative_canvas_text_version", "label": f"创作画布{suffix} · 文本运行 {run_id} 的入库版本", "versionId": str(version.get("versionId") or ""), **run_extra})
    for batch in store.load_batches(project_id, canvas_id):
        batch_id = batch.get("id")
        if not isinstance(batch_id, str) or not batch_id:
            raise OSError("创作画布批量记录无效")
        batch_extra = {"batchId": batch_id, **extra}
        snapshot = batch.get("documentSnapshot", {})
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), list):
            raise OSError("创作画布批量快照无效")
        for node in snapshot["nodes"]:
            if not isinstance(node, dict):
                raise OSError("创作画布批量快照无效")
            if node.get("kind") == "media" and node.get("assetId") == asset_id:
                references.append({"kind": "creative_canvas_batch_snapshot", "label": f"创作画布{suffix} · 批量 {batch_id} 的素材快照", **batch_extra})
            if node.get("kind") == "generate" and node.get("outputAssetId") == asset_id:
                references.append({"kind": "creative_canvas_batch_snapshot", "label": f"创作画布{suffix} · 批量 {batch_id} 的输出快照", **batch_extra})
        for reference in batch.get("externalOutputRefs", []):
            if isinstance(reference, dict) and reference.get("assetId") == asset_id:
                references.append({"kind": "creative_canvas_batch_external_output", "label": f"创作画布{suffix} · 批量 {batch_id} 的外部生成输入", "nodeId": reference.get("targetNodeId", ""), **batch_extra})
        for step in batch.get("steps", []):
            selected = step.get("selectedOutputRef") if isinstance(step, dict) else None
            if isinstance(selected, dict) and selected.get("assetId") == asset_id:
                references.append({"kind": "creative_canvas_batch_output", "label": f"创作画布{suffix} · 批量 {batch_id} 的步骤输出", "nodeId": step.get("nodeId", ""), **batch_extra})
    return references


def creative_canvas_asset_references(root: Path, project_id: str, asset_id: str) -> list[dict[str, str]]:
    """Return references from all canvases, including soft-deleted restorable ones."""
    store = CreativeCanvasStore(root)
    references: list[dict[str, str]] = []
    for canvas in store.list_canvases(project_id, include_deleted=True):
        references.extend(_asset_references_for_canvas(store, project_id, asset_id, canvas))
    return references
