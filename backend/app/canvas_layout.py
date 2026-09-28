"""Independent durable storage for pre-production canvas layouts."""
from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .preproduction import safe_child
from .reference_video import validate_storage_id


LAYOUT_SCOPE_TYPES = ("project", "scene", "shot")
MAX_LAYOUT_NODES = 10_000


class CanvasLayoutConflict(Exception):
    pass


class CanvasLayoutStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)

    def path(self, project_id: str, scope_type: str, scope_id: str) -> Path:
        if scope_type not in LAYOUT_SCOPE_TYPES:
            raise ValueError("画布布局范围无效")
        validate_storage_id(scope_id)
        return safe_child(
            self.root,
            "project-files",
            project_id,
            "preproduction",
            "canvas-layouts",
            scope_type,
            scope_id + ".json",
        )

    def read(self, project_id: str, default_layout: dict[str, Any]) -> dict[str, Any]:
        validate_layout(default_layout)
        scope = default_layout["scope"]
        path = self.path(project_id, scope["type"], scope["id"])
        if not path.exists():
            return deepcopy(default_layout)
        if path.is_symlink() or not path.is_file():
            raise OSError("画布布局文件无效")
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            validate_layout(stored)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OSError("画布布局无法读取") from error
        if stored["scope"] != scope:
            raise OSError("画布布局范围无效")
        merged = deepcopy(default_layout)
        merged["layoutRevision"] = stored["layoutRevision"]
        merged["viewport"] = deepcopy(stored.get("viewport", default_layout.get("viewport")))
        merged["nodes"] = {
            node_id: {**node, **stored["nodes"].get(node_id, {})}
            for node_id, node in default_layout["nodes"].items()
        }
        return merged

    def apply(
        self,
        project_id: str,
        default_layout: dict[str, Any],
        expected_revision: int,
        nodes: dict[str, Any],
        viewport: dict[str, Any] | None,
    ) -> dict[str, Any]:
        current = self.read(project_id, default_layout)
        if expected_revision != current["layoutRevision"]:
            raise CanvasLayoutConflict
        next_layout = {
            "scope": deepcopy(default_layout["scope"]),
            "layoutRevision": expected_revision + 1,
            "nodes": {
                node_id: {**deepcopy(default_node), **deepcopy(nodes.get(node_id, {}))}
                for node_id, default_node in default_layout["nodes"].items()
            },
            "viewport": deepcopy(viewport),
        }
        validate_layout(next_layout)
        self._save(project_id, next_layout)
        return next_layout

    def _save(self, project_id: str, layout: dict[str, Any]) -> None:
        scope = layout["scope"]
        path = self.path(project_id, scope["type"], scope["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".layout-", suffix=".part")
            temporary = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(layout, target, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def validate_layout(layout: Any) -> None:
    if not isinstance(layout, dict):
        raise ValueError("画布布局无效")
    scope = layout.get("scope")
    if not isinstance(scope, dict) or set(scope) != {"type", "id"}:
        raise ValueError("画布布局范围无效")
    if scope["type"] not in LAYOUT_SCOPE_TYPES or not isinstance(scope["id"], str):
        raise ValueError("画布布局范围无效")
    validate_storage_id(scope["id"])
    revision = layout.get("layoutRevision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("画布布局版本无效")
    nodes = layout.get("nodes")
    if not isinstance(nodes, dict) or len(nodes) > MAX_LAYOUT_NODES:
        raise ValueError("画布布局节点无效")
    for node_id, node in nodes.items():
        if not isinstance(node_id, str) or not node_id or not isinstance(node, dict):
            raise ValueError("画布布局节点无效")
        if set(node) - {"x", "y", "width", "height", "collapsed"}:
            raise ValueError("画布布局节点字段无效")
        _finite_number(node.get("x"), "画布节点位置无效")
        _finite_number(node.get("y"), "画布节点位置无效")
        for key in ("width", "height"):
            if key in node and (_finite_number(node[key], "画布节点尺寸无效") <= 0 or node[key] > 100_000):
                raise ValueError("画布节点尺寸无效")
        if "collapsed" in node and not isinstance(node["collapsed"], bool):
            raise ValueError("画布节点折叠状态无效")
    viewport = layout.get("viewport")
    if viewport is not None:
        if not isinstance(viewport, dict) or set(viewport) != {"x", "y", "zoom"}:
            raise ValueError("画布视口无效")
        _finite_number(viewport["x"], "画布视口无效")
        _finite_number(viewport["y"], "画布视口无效")
        zoom = _finite_number(viewport["zoom"], "画布视口无效")
        if zoom < 0.05 or zoom > 8:
            raise ValueError("画布视口缩放无效")


def _finite_number(value: Any, message: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(message)
    return float(value)
