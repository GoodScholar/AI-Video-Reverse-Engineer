"""HTTP API for independent creative canvas documents and cloud image runs."""
from __future__ import annotations

import io
import os
import re
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from inspect import Parameter, signature
from pathlib import Path
from threading import Event, RLock
from typing import Any, Callable, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from .creative_canvas import (
    DEFAULT_CANVAS_ID, MAX_CANVAS_IMAGE_DIMENSION, MAX_CANVAS_IMAGE_PIXELS, CanvasDocument,
    CreativeCanvasStore, DocumentBatchStore, DocumentRunStore, DocumentTextRunStore, validate_document,
)
from .image_generation import ImageGenerationError
from .preproduction import PreproductionStore
from .project_assets import AssetFileUnavailableError, ProjectAssets


_SIZE_PATTERN = re.compile(r"([1-9][0-9]{1,3})x([1-9][0-9]{1,3})$")
MAX_TEXT_OUTPUT_CHARS = 12_000


class _TextOutputTooLarge(Exception):
    code = "text_output_too_large"
    billing_unknown = True


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(ge=0, le=2_147_483_647, strict=True)
    count: Optional[int] = Field(default=None, ge=1, le=15, strict=True)
    idempotencyKey: Optional[str] = Field(default=None, min_length=1, max_length=100)


class TextRunRequest(RunRequest):
    pass


class CanvasNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=120)


class ResultActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(ge=0, le=2_147_483_647, strict=True)


class BatchRequest(ResultActionRequest):
    nodeIds: list[str] = Field(min_length=1, max_length=500)
    idempotencyKey: Optional[str] = Field(default=None, min_length=1, max_length=100)


def create_creative_canvas_router(
    data_dir: Path,
    get_project: Callable[[str], Any],
    *,
    source_lock: Any = None,
    configurations: Any = None,
    image_service: Any = None,
    text_service: Any = None,
    compute_queue: Any = None,
) -> APIRouter:
    root = Path(data_dir)
    store = CreativeCanvasStore(root)
    run_store = DocumentRunStore(root)
    text_run_store = DocumentTextRunStore(root)
    batch_store = DocumentBatchStore(root)
    preproduction_store = PreproductionStore(root)
    project_assets = ProjectAssets(root)
    lock = RLock()
    active_text_streams: dict[tuple[str, str, str, str], Any] = {}
    active_text_cancel_events: dict[tuple[str, str, str, str], Event] = {}
    router = APIRouter(prefix="/api/projects/{project_id}/creative-canvas")

    def fail(code: str, message: str, status: int = 409) -> None:
        raise HTTPException(status_code=status, detail={"code": code, "message": message})

    def ensure_project(project_id: str) -> None:
        if get_project(project_id) is None:
            fail("project_not_found", "复刻项目不存在。", 404)

    def assets_for(project_id: str) -> dict[str, dict[str, Any]]:
        try:
            return project_assets.index(project_id)
        except (OSError, ValueError, KeyError, TypeError):
            fail("creative_canvas_assets_unavailable", "项目素材无法读取。", 503)

    def selected_canvas_id(canvas_id: Optional[str]) -> str:
        return canvas_id or DEFAULT_CANVAS_ID

    def require_canvas(project_id: str, canvas_id: Optional[str], *, include_deleted: bool = False) -> dict[str, Any]:
        try:
            return store.canvas(project_id, selected_canvas_id(canvas_id), include_deleted=include_deleted)
        except KeyError:
            fail("creative_canvas_missing", "创作画布不存在或已删除。", 404)
        except ValueError:
            fail("creative_canvas_invalid", "画布标识无效。", 422)
        except OSError:
            fail("creative_canvas_storage_invalid", "创作画布目录无法读取。", 503)

    def runs_for(project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        try:
            return run_store.load(project_id, selected_canvas_id(canvas_id))
        except (OSError, ValueError):
            fail("creative_canvas_runs_invalid", "创作画布运行记录无法读取。", 503)

    def save_runs(project_id: str, runs: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        try:
            selected = selected_canvas_id(canvas_id)
            if selected == DEFAULT_CANVAS_ID:
                run_store.save(project_id, runs)
            else:
                run_store.save(project_id, runs, selected)
        except (OSError, ValueError):
            fail("creative_canvas_runs_failed", "创作画布运行记录无法保存。", 503)

    def text_runs_for(project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        try:
            return text_run_store.load(project_id, selected_canvas_id(canvas_id))
        except (OSError, ValueError):
            fail("creative_canvas_text_runs_invalid", "文本生成运行记录无法读取。", 503)

    def save_text_runs(project_id: str, runs: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        try:
            text_run_store.save(project_id, runs, selected_canvas_id(canvas_id))
        except (OSError, ValueError):
            fail("creative_canvas_text_runs_failed", "文本生成运行记录无法保存。", 503)

    def batches_for(project_id: str, canvas_id: Optional[str] = None) -> list[dict[str, Any]]:
        try:
            return batch_store.load(project_id, selected_canvas_id(canvas_id))
        except (OSError, ValueError):
            fail("creative_canvas_batches_invalid", "创作画布批量记录无法读取。", 503)

    def save_batches(project_id: str, batches: list[dict[str, Any]], canvas_id: Optional[str] = None) -> None:
        try:
            batch_store.save(project_id, batches, selected_canvas_id(canvas_id))
        except (OSError, ValueError):
            fail("creative_canvas_batches_failed", "创作画布批量记录无法保存。", 503)

    def output_items_for(run: dict[str, Any]) -> list[dict[str, Any]]:
        items = run.get("outputItems")
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            return items
        run_id = run.get("id")
        outputs = run.get("outputAssetIds")
        if not isinstance(run_id, str) or not isinstance(outputs, list):
            return []
        return [{"resultId": f"{run_id}:legacy:{index}", "assetId": asset_id, "mediaType": "image", "status": "installed",
                 "sourceRunId": run_id, "createdAt": run.get("createdAt")} for index, asset_id in enumerate(outputs)
                if isinstance(asset_id, str)]

    def public_run(run: dict[str, Any]) -> dict[str, Any]:
        return {**run, "outputItems": output_items_for(run)}

    def response(project_id: str, document: dict[str, Any], canvas_id: Optional[str] = None) -> dict[str, Any]:
        assets = [{**project_assets.public_record(project_id, asset), "available": project_assets.available(project_id, asset)}
                  for asset in assets_for(project_id).values()]
        catalog = getattr(text_service, "catalog", None)
        text_models = catalog() if callable(catalog) else []
        payload = {"document": document, "runs": [public_run(run) for run in runs_for(project_id, canvas_id)], "textRuns": text_runs_for(project_id, canvas_id), "batches": batches_for(project_id, canvas_id), "assets": assets, "textModels": text_models}
        if canvas_id is not None:
            payload["canvas"] = require_canvas(project_id, canvas_id)
        return payload

    def find_run(runs: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
        run = next((candidate for candidate in runs if candidate.get("id") == run_id), None)
        if run is None:
            fail("creative_canvas_run_missing", "运行记录不存在。", 404)
        return run

    def active_run(runs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return next((run for run in runs if run.get("status") in {"queued", "running", "cancel_requested"}), None)

    def ordered_references(document: CanvasDocument, node_id: str) -> list[Any]:
        node = next(item for item in document.nodes if item.id == node_id)
        order = {edge_id: index for index, edge_id in enumerate(node.referenceOrder or [])}
        edges = [edge for edge in document.edges if edge.target == node_id]
        return [edge for _, edge in sorted(enumerate(edges), key=lambda item: (order.get(item[1].id, len(order) + item[0]), item[0]))]

    def selected_generated_asset(
        project_id: str, canvas_id: str, source: Any, assets: dict[str, dict[str, Any]],
        forced_output: Optional[dict[str, Any]] = None,
    ) -> tuple[str, Optional[str], Optional[str]]:
        candidate = source.outputAssetId
        source_run_id, source_result_id = None, None
        runs = runs_for(project_id, canvas_id)
        if forced_output is not None:
            candidate, source_run_id, source_result_id = forced_output.get("assetId"), forced_output.get("runId"), forced_output.get("resultId")
            run = next((item for item in runs if item.get("id") == source_run_id and item.get("nodeId") == source.id), None)
            item = result_item(run or {}, source_result_id) if isinstance(source_result_id, str) else None
            if not isinstance(item, dict) or item.get("status") != "installed" or item.get("assetId") != candidate:
                fail("creative_canvas_reference_unavailable", "批量生成依赖的结果已不可用。", 422)
        elif source.currentOutputRef is not None:
            source_run_id, source_result_id = source.currentOutputRef.runId, source.currentOutputRef.resultId
            run = next((item for item in runs if item.get("id") == source_run_id and item.get("nodeId") == source.id), None)
            item = next((item for item in output_items_for(run or {}) if item.get("resultId") == source_result_id), None)
            candidate = item.get("assetId") if isinstance(item, dict) and item.get("status") == "installed" else None
            if source.outputAssetId is not None and candidate != source.outputAssetId:
                fail("creative_canvas_output_mismatch", "生成节点当前输出与结果身份不一致。", 422)
        elif candidate is not None:
            for run in reversed(runs):
                if run.get("nodeId") != source.id:
                    continue
                item = next((item for item in output_items_for(run) if item.get("assetId") == candidate and item.get("status") == "installed" and not item.get("cancellationRequested")), None)
                if item is not None:
                    source_run_id, source_result_id = run.get("id"), item.get("resultId")
                    break
        else:
            for run in reversed(runs):
                if run.get("nodeId") != source.id or run.get("status") != "completed" or run.get("cancellationRequested"):
                    continue
                item = next((item for item in output_items_for(run) if item.get("status") == "installed" and not item.get("cancellationRequested")), None)
                if item is not None:
                    candidate, source_run_id, source_result_id = item.get("assetId"), run.get("id"), item.get("resultId")
                    break
                # New output history deliberately records deletions. Do not silently
                # substitute an older run when the newest completed history is empty.
                if isinstance(run.get("outputItems"), list):
                    break
        asset = assets.get(candidate or "")
        if asset is None or asset.get("kind") != "image" or not project_assets.available(project_id, asset):
            fail("creative_canvas_reference_unavailable", "生成节点引用尚无可用输出图片。", 422)
        return asset["id"], source_run_id, source_result_id

    def snapshot_for(
        project_id: str, canvas_id: str, document: CanvasDocument, node_id: str,
        forced_outputs: Optional[dict[str, dict[str, Any]]] = None, count: Optional[int] = None,
    ) -> tuple[dict[str, Any], list[Path], list[str]]:
        node_index = {node.id: node for node in document.nodes}
        node = node_index.get(node_id)
        if node is None or node.kind != "generate":
            fail("creative_canvas_generate_missing", "生成节点不存在。", 404)
        if node.params.negativePrompt:
            fail("creative_canvas_unsupported_parameters", "当前云端图片服务不支持负面提示词。", 422)
        size = node.params.size or f"{node.params.width}x{node.params.height}"
        match = _SIZE_PATTERN.fullmatch(size)
        if match is None or max(int(match.group(1)), int(match.group(2))) > MAX_CANVAS_IMAGE_DIMENSION or int(match.group(1)) * int(match.group(2)) > MAX_CANVAS_IMAGE_PIXELS:
            fail("creative_canvas_size_unsupported", "图片尺寸必须是合法的宽x高像素值。", 422)
        assets = assets_for(project_id)
        prompt_parts = [node.text.strip()] if isinstance(node.text, str) and node.text.strip() else []
        input_asset_ids: list[str] = []
        input_paths: list[Path] = []
        references: list[dict[str, Any]] = []
        for index, edge in enumerate(ordered_references(document, node_id), start=1):
            source = node_index[edge.source]
            reference = {"edgeId": edge.id, "source": edge.source, "sourceNodeId": edge.source, "sourceHandle": edge.sourceHandle,
                         "target": edge.target, "targetHandle": edge.targetHandle, "order": index}
            if edge.sourceHandle == "text":
                text = source.text.strip() if isinstance(source.text, str) else ""
                reference.update(dataType="text", text=text, textVersion=f"legacy-text:{source.id}:r{document.revision}")
                if text:
                    prompt_parts.append(f"参考 {index}：\n{text}")
            else:
                if source.kind == "media":
                    asset_id, source_run_id, source_result_id = source.assetId, None, None
                else:
                    asset_id, source_run_id, source_result_id = selected_generated_asset(
                        project_id, canvas_id, source, assets, (forced_outputs or {}).get(source.id),
                    )
                asset = assets.get(asset_id or "")
                if asset is None or asset.get("kind") != "image" or not project_assets.available(project_id, asset):
                    fail("creative_canvas_image_unavailable", "图像输入素材不可用。", 422)
                try:
                    input_paths.append(project_assets.file(project_id, asset))
                except AssetFileUnavailableError:
                    fail("creative_canvas_image_unavailable", "图像输入素材不可用。", 422)
                input_asset_ids.append(asset["id"])
                reference.update(dataType="image", assetId=asset["id"], sourceRunId=source_run_id)
                if source_result_id is not None:
                    reference["sourceResultId"] = source_result_id
            references.append(reference)
        prompt = "\n\n".join(prompt_parts)
        if not prompt:
            fail("creative_canvas_prompt_missing", "生成节点需要提示词。", 422)
        if configurations is None:
            fail("image_generation_unconfigured", "请先在图片生成设置中配置云端服务和密钥。", 422)
        configuration = configurations.snapshot()
        provider, default_model = configuration.get("provider"), configuration.get("model")
        models = configuration.get("models")
        if not isinstance(models, list):
            models = [default_model]
        provider_id, model = node.params.providerId, node.params.modelId
        catalog = configuration.get("modelCatalog")
        stable_selection = provider_id is not None and model is not None
        legacy_explicit_model = node.params.model is not None
        legacy_entry = None
        catalog_item = None
        if stable_selection:
            if node.params.configurationVersion is not None and node.params.configurationVersion != configuration.get("configVersion", configuration.get("revision")):
                fail("creative_canvas_configuration_changed", "生成节点使用的模型配置已更新，请重新选择模型。", 422)
            if isinstance(catalog, list):
                catalog_item = next((item for item in catalog if isinstance(item, dict) and item.get("providerId") == provider_id and item.get("modelId") == model and item.get("mediaType") == "image"), None)
            if catalog_item is None:
                fail("creative_canvas_model_unsupported", "生成节点选择的稳定模型不在当前服务配置中。", 422)
            declared = catalog_item.get("capabilities")
            if not isinstance(declared, dict) or not declared:
                fail("creative_canvas_model_capabilities_unknown", "该模型尚未声明可验证能力，不能提交生成。", 422)
            supported_sizes = declared.get("imageSizes")
            if isinstance(supported_sizes, list) and size not in supported_sizes:
                fail("creative_canvas_size_unsupported", "所选模型不支持该图片尺寸。", 422)
            ratio = node.params.ratio
            supported_ratios = declared.get("imageRatios")
            if isinstance(supported_ratios, list) and ratio is not None and ratio not in supported_ratios:
                fail("creative_canvas_ratio_unsupported", "所选模型不支持该图片比例。", 422)
            max_references = declared.get("maxReferences")
            if isinstance(max_references, int) and len(input_paths) > max_references:
                fail("creative_canvas_reference_limit", "所选模型的图片参考数量超限。", 422)
            entries = configuration.get("providers")
            entry = next((item for item in entries if isinstance(item, dict) and item.get("providerId") == provider_id), None) if isinstance(entries, list) else None
            if entry is None:
                fail("creative_canvas_model_unsupported", "生成节点选择的供应商不在当前服务配置中。", 422)
            if entry.get("credentialState", configuration.get("credentialState")) != "configured":
                fail("image_generation_unconfigured", "请先为所选图片生成供应商配置云端服务和密钥。", 422)
            provider, default_model = entry.get("protocol"), entry.get("defaultModelId")
        else:
            entries = configuration.get("providers")
            if legacy_explicit_model and isinstance(entries, list):
                legacy_entry = next((item for item in entries if isinstance(item, dict) and item.get("providerId") == "default"), None)
                if legacy_entry is None:
                    fail("creative_canvas_model_unsupported", "旧生成节点原有的默认供应商已不存在，请重新选择模型。", 422)
                if legacy_entry.get("credentialState", configuration.get("credentialState")) != "configured":
                    fail("image_generation_unconfigured", "请先为旧生成节点的默认图片供应商配置密钥。", 422)
                provider_id = "default"
                provider, default_model = legacy_entry.get("protocol"), legacy_entry.get("defaultModelId")
                models = [item.get("modelId") for item in legacy_entry.get("models", []) if isinstance(item, dict)]
            else:
                if configuration.get("credentialState") != "configured":
                    fail("image_generation_unconfigured", "请先在图片生成设置中配置云端服务和密钥。", 422)
            model = node.params.model or default_model
        capabilities = _protocol_capabilities_for(configuration, provider_id) if (stable_selection or legacy_entry is not None) else configuration.get("protocolCapabilities")
        if not isinstance(capabilities, dict) or capabilities.get("referenceImages") not in {"single", "multiple"}:
            fail("image_generation_unconfigured", "图片生成服务配置无效，请重新保存设置。", 422)
        if not stable_selection and model not in models:
            fail("creative_canvas_model_unsupported", "生成节点选择的模型不在当前服务配置中。", 422)
        if capabilities["referenceImages"] == "single" and len(input_paths) > 1:
            fail("creative_canvas_multiple_images_unsupported", "当前图片服务只支持单张参考图片。", 422)
        config_version = configuration.get("configVersion", configuration.get("revision"))
        snapshot = {"baseUrl": configuration.get("baseUrl"), "provider": provider, "model": model,
                    "providerId": provider_id or configuration.get("activeProviderId", "default"), "modelId": model,
                    "configVersion": config_version, "capabilityVersion": catalog_item.get("capabilityVersion") if catalog_item else "legacy-protocol",
                    "revision": configuration.get("revision"), "prompt": prompt, "size": size,
                    "inputAssetIds": input_asset_ids, "references": references}
        if node.params.ratio is not None:
            snapshot["ratio"] = node.params.ratio
        snapshot["count"] = node.params.count if count is None else count
        if stable_selection or legacy_entry is not None:
            entries = configuration.get("providers")
            selected_entry = next((item for item in entries if isinstance(item, dict) and item.get("providerId") == provider_id), None) if isinstance(entries, list) else None
            if isinstance(selected_entry, dict): snapshot["baseUrl"] = selected_entry.get("baseUrl")
        if not all(isinstance(snapshot[key], str) and snapshot[key] for key in ("baseUrl", "provider", "model", "providerId", "modelId", "configVersion", "revision")):
            fail("image_generation_unconfigured", "图片生成服务配置无效，请重新保存设置。", 422)
        return snapshot, input_paths, input_asset_ids

    def _protocol_capabilities_for(configuration: dict[str, Any], provider_id: str) -> Optional[dict[str, Any]]:
        entries = configuration.get("providers")
        entry = next((item for item in entries if isinstance(item, dict) and item.get("providerId") == provider_id), None) if isinstance(entries, list) else None
        protocol = entry.get("protocol") if isinstance(entry, dict) else None
        if protocol == "openai-compatible": return {"referenceImages": "single"}
        if protocol == "volcengine-ark": return {"referenceImages": "multiple"}
        return None

    def schedule_batch_advance(project_id: str, canvas_id: str, batch_id: str) -> None:
        if compute_queue is None:
            return
        queue_key = f"{project_id}:{canvas_id}:{batch_id}"
        handler = lambda _queue_project: advance_batch(project_id, canvas_id, batch_id)
        compute_queue.submit("creative-canvas-batch", queue_key, handler, project_id=project_id)

    def update_batch_for_run(project_id: str, canvas_id: str, run: dict[str, Any]) -> None:
        batch_id, step_node_id = run.get("batchId"), run.get("batchStepNodeId")
        if not isinstance(batch_id, str) or not isinstance(step_node_id, str) or run.get("status") not in {"completed", "failed", "cancelled", "unknown"}:
            return
        batches = batches_for(project_id, canvas_id)
        batch = next((item for item in batches if item.get("id") == batch_id), None)
        if batch is None:
            return
        step = next((item for item in batch.get("steps", []) if item.get("nodeId") == step_node_id), None)
        if not isinstance(step, dict):
            return
        status = run["status"]
        if status == "completed":
            result = next((item for item in output_items_for(run) if item.get("status") == "installed" and not item.get("cancellationRequested") and isinstance(item.get("assetId"), str)), None)
            if result is None:
                status = "unknown"
                step["error"] = "result_missing"
            else:
                step["selectedOutputRef"] = {"runId": run["id"], "resultId": result["resultId"], "assetId": result["assetId"]}
        step.update(status=status, runId=run["id"])
        if status != "completed":
            step["error"] = run.get("error") or status
        if batch.get("cancellationRequested"):
            batch["status"] = "cancelled"
            save_batches(project_id, batches, canvas_id)
            return
        batch["status"] = "running"
        save_batches(project_id, batches, canvas_id)
        schedule_batch_advance(project_id, canvas_id, batch_id)

    def batch_step_dependencies(batch: dict[str, Any], step: dict[str, Any]) -> Optional[dict[str, dict[str, Any]]]:
        by_node = {item.get("nodeId"): item for item in batch.get("steps", []) if isinstance(item, dict)}
        forced: dict[str, dict[str, Any]] = {}
        for dependency_id in step.get("dependsOn", []):
            dependency = by_node.get(dependency_id)
            selected = dependency.get("selectedOutputRef") if isinstance(dependency, dict) else None
            if not isinstance(selected, dict) or not all(isinstance(selected.get(key), str) and selected[key] for key in ("runId", "resultId", "assetId")):
                return None
            forced[dependency_id] = selected
        for reference in batch.get("externalOutputRefs", []):
            if not isinstance(reference, dict) or reference.get("targetNodeId") != step.get("nodeId"):
                continue
            if not all(isinstance(reference.get(key), str) and reference[key] for key in ("sourceNodeId", "runId", "resultId", "assetId")):
                return None
            forced[reference["sourceNodeId"]] = {key: reference[key] for key in ("runId", "resultId", "assetId")}
        return forced

    def enqueue_batch_step(project_id: str, canvas_id: str, batch: dict[str, Any], step: dict[str, Any]) -> None:
        document = CanvasDocument.model_validate(batch["documentSnapshot"])
        forced_outputs = batch_step_dependencies(batch, step)
        if forced_outputs is None:
            step.update(status="blocked", error="dependency_output_missing")
            return
        node = next(node for node in document.nodes if node.id == step["nodeId"])
        count = node.params.count
        snapshot, _paths, input_asset_ids = snapshot_for(project_id, canvas_id, document, step["nodeId"], forced_outputs, count=count)
        run_id = "canvas-run-" + uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        run = {"id": run_id, "canvasId": canvas_id, "nodeId": step["nodeId"], "status": "queued", "error": None,
               "createdAt": created_at, "documentRevision": batch["documentRevision"], "configurationRevision": snapshot["revision"],
               "inputAssetIds": input_asset_ids, "outputAssetIds": [],
               "outputItems": [{"resultId": "canvas-result-" + uuid4().hex, "requestId": "canvas-request-" + uuid4().hex, "mediaType": "image", "status": "pending", "sourceRunId": run_id, "createdAt": created_at, "cancellationRequested": False, "localDisposition": "none", "providerDisposition": "unknown", "billingUnknown": False} for _ in range(count)],
               "snapshot": snapshot, "idempotencyKey": step["idempotencyKey"], "batchId": batch["id"], "batchStepNodeId": step["nodeId"],
               "cancellationRequested": False, "localDisposition": "none", "providerDisposition": "unknown", "billingUnknown": False}
        runs = runs_for(project_id, canvas_id)
        runs.append(run)
        save_runs(project_id, runs, canvas_id)
        step.update(status="running", runId=run_id)
        for item in run["outputItems"]:
            result_id = item["resultId"]
            queue_key = f"{project_id}:{canvas_id}:{run_id}:{result_id}"
            handler = lambda _queue_project, rid=run_id, oid=result_id: run_job(project_id, canvas_id, rid, oid)
            if compute_queue is None or not compute_queue.submit("creative-canvas-candidate", queue_key, handler, project_id=project_id):
                finish_item(project_id, canvas_id, run_id, result_id, "failed", "queue_unavailable")

    def advance_batch(project_id: str, canvas_id: str, batch_id: str) -> None:
        with source_lock or nullcontext(), lock:
            batches = batches_for(project_id, canvas_id)
            batch = next((item for item in batches if item.get("id") == batch_id), None)
            if batch is None or batch.get("status") in {"completed", "failed", "blocked", "cancelled", "unknown"}:
                return
            if batch.get("cancellationRequested"):
                for step in batch.get("steps", []):
                    if isinstance(step, dict) and step.get("status") == "queued":
                        step.update(status="cancelled", error="batch_cancelled")
                batch["status"] = "cancelled"
                save_batches(project_id, batches, canvas_id)
                return
            steps = batch.get("steps", [])
            by_node = {step.get("nodeId"): step for step in steps if isinstance(step, dict)}
            changed = False
            for step in steps:
                if not isinstance(step, dict) or step.get("status") != "queued":
                    continue
                dependency_states = [by_node.get(node_id, {}).get("status") for node_id in step.get("dependsOn", [])]
                if any(state in {"failed", "cancelled", "unknown", "blocked"} for state in dependency_states):
                    step.update(status="blocked", error="dependency_not_completed"); changed = True
            if any(step.get("status") == "running" for step in steps if isinstance(step, dict)):
                if changed: save_batches(project_id, batches, canvas_id)
                return
            ready = next((step for step in steps if isinstance(step, dict) and step.get("status") == "queued" and all(by_node.get(node_id, {}).get("status") == "completed" for node_id in step.get("dependsOn", []))), None)
            if ready is not None:
                existing_runs = runs_for(project_id, canvas_id)
                if any(run.get("status") == "unknown" and run.get("nodeId") == ready.get("nodeId") for run in existing_runs):
                    ready.update(status="blocked", error="prior_result_unknown")
                    batch["status"] = "blocked"
                    save_batches(project_id, batches, canvas_id)
                    return
                if active_run(existing_runs) is not None:
                    if changed: save_batches(project_id, batches, canvas_id)
                    return
                batch["status"] = "running"
                try:
                    enqueue_batch_step(project_id, canvas_id, batch, ready)
                except HTTPException as error:
                    detail = error.detail if isinstance(error.detail, dict) else {}
                    ready.update(status="failed", error=detail.get("code", "snapshot_failed"))
                    for step in steps:
                        if isinstance(step, dict) and step.get("status") == "queued" and ready["nodeId"] in step.get("dependsOn", []):
                            step.update(status="blocked", error="dependency_not_completed")
                    batch["status"] = "failed"
                save_batches(project_id, batches, canvas_id)
                return
            states = [step.get("status") for step in steps if isinstance(step, dict)]
            if states and all(state == "completed" for state in states):
                batch["status"] = "completed"
            elif any(state == "blocked" for state in states):
                batch["status"] = "blocked"
            elif any(state in {"failed", "cancelled", "unknown"} for state in states):
                batch["status"] = "failed"
            if changed or batch.get("status") != "queued":
                save_batches(project_id, batches, canvas_id)


    def aggregate_run_status(run: dict[str, Any], error: Optional[str] = None) -> None:
        items = output_items_for(run)
        states = [item.get("status") for item in items]
        active = {"pending", "running", "cancel_requested"}
        run["outputAssetIds"] = [item["assetId"] for item in items if item.get("status") in {"installed", "deleted"} and isinstance(item.get("assetId"), str)]
        run["billingUnknown"] = any(item.get("status") == "unknown" or item.get("billingUnknown") for item in items)
        if run.get("cancellationRequested"):
            if any(state in active for state in states):
                run.update(status="cancel_requested", error=None)
            elif any(state == "unknown" for state in states):
                run.update(status="unknown", error=error or "candidate_outcome_unknown")
            else:
                run.update(status="cancelled", error="cancelled")
        elif any(state in active for state in states):
            # A candidate-level cancellation still occupies the canvas until its local
            # provider call returns, without upgrading it to a whole-run cancellation.
            run.update(status="cancel_requested" if "cancel_requested" in states else "running", error=None)
        elif any(state == "installed" and not item.get("cancellationRequested") for state, item in zip(states, items)):
            run.update(status="completed", error=None)
        elif any(state == "installed" for state in states) and any(item.get("cancellationRequested") for item in items):
            # A lone late result is retained for history, but is not a completed run.
            run.update(status="cancelled", error="cancelled")
        elif any(state == "installed" for state in states):
            run.update(status="completed", error=None)
        elif run["billingUnknown"]:
            run.update(status="unknown", error=error or "candidate_outcome_unknown")
        elif states and all(state == "cancelled" for state in states):
            run.update(status="cancelled", error=error or "cancelled")
        else:
            run.update(status="failed", error=error or "all_candidates_failed")

    def aggregate_text_run_status(run: dict[str, Any]) -> None:
        versions = [item for item in run.get("versions", []) if isinstance(item, dict)]
        states = [item.get("status") for item in versions]
        active = {"pending", "running", "cancel_requested"}
        run["billingUnknown"] = any(item.get("billingUnknown") or item.get("status") == "unknown" for item in versions)
        provider_outcome_unknown = any(
            item.get("status") == "unknown" or (
                item.get("billingUnknown") and (
                    item.get("status") == "cancelled" or item.get("cancellationRequested")
                )
            )
            for item in versions
        )
        if run.get("cancellationRequested"):
            if any(state in active for state in states):
                run.update(status="cancel_requested", error=None)
            elif provider_outcome_unknown:
                run.update(status="unknown", error="candidate_outcome_unknown")
            else:
                run.update(status="cancelled", error="cancelled")
        elif any(state in active for state in states):
            if run.get("cancellationRequested") or "cancel_requested" in states:
                run["status"] = "cancel_requested"
            elif "running" in states:
                run["status"] = "running"
            else:
                run["status"] = "queued"
            run["error"] = None
        elif provider_outcome_unknown:
            run.update(status="unknown", error="candidate_outcome_unknown")
        elif any(state == "installed" and not item.get("cancellationRequested") for state, item in zip(states, versions)):
            run.update(status="completed", error=None)
        elif any(state == "installed" for state in states) or states and all(state == "cancelled" for state in states):
            run.update(status="cancelled", error="cancelled")
        else:
            run.update(status="failed", error="all_candidates_failed")

    def close_text_stream(stream: Any) -> None:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Closing a local response/iterator is best effort; a provider cannot
                # be reported as cancelled merely because its local stream closed.
                pass

    def text_stream_for(snapshot: dict[str, Any], cancel_event: Event, cancelled: Callable[[], bool]) -> Any:
        generate = text_service.generate
        try:
            parameters = signature(generate).parameters.values()
            accepts_cancel_event = any(parameter.name == "cancel_event" or parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters)
            accepts_cancelled = any(parameter.name == "cancelled" or parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters)
        except (TypeError, ValueError):
            accepts_cancel_event = False
            accepts_cancelled = False
        if accepts_cancel_event:
            return generate(snapshot, cancel_event=cancel_event)
        return generate(snapshot, cancelled=cancelled) if accepts_cancelled else generate(snapshot)

    def finish_item(project_id, canvas_id, run_id, result_id, status, error=None, output_asset_id=None):
        runs = runs_for(project_id, canvas_id)
        run = find_run(runs, run_id)
        item = result_item(run, result_id)
        if item is None:
            return
        if output_asset_id is not None:
            item.update(assetId=output_asset_id, status="installed", sourceRunId=run_id, error=None)
            if not item.get("cancellationRequested"):
                item["billingUnknown"] = False
        else:
            item.update(status=status, error=error)
            if status != "unknown":
                item["billingUnknown"] = False
        aggregate_run_status(run, error)
        save_runs(project_id, runs, canvas_id)
        if run["status"] in {"completed", "failed", "cancelled", "unknown"}:
            update_batch_for_run(project_id, canvas_id, run)

    def install_result(project_id, canvas_id, run_id, result_id, image_bytes):
        try:
            with Image.open(io.BytesIO(image_bytes)) as image: image.verify()
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load(); output=io.BytesIO(); image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output,format="PNG"); width,height=image.size
            state=preproduction_store.load(project_id); asset_id=project_assets.new_id("canvas"); record={"id":asset_id,"name":"创作画布生成结果.png","kind":"image","role":"reference","file":asset_id+".png","width":width,"height":height}
            descriptor,name=tempfile.mkstemp(dir=root,prefix=".creative-canvas-",suffix=".png")
            with os.fdopen(descriptor,"wb") as target: target.write(output.getvalue())
            with project_assets.install(project_id,state,Path(name),record,move=True): state["revision"]+=1; preproduction_store.save(project_id,state)
        except Exception: finish_item(project_id,canvas_id,run_id,result_id,"unknown","result_install_failed"); return
        try: finish_item(project_id,canvas_id,run_id,result_id,"completed",output_asset_id=asset_id)
        except HTTPException:
            runs=runs_for(project_id,canvas_id); run=find_run(runs,run_id); item=result_item(run,result_id)
            if item is not None: item.update(assetId=asset_id,status="unknown",error="result_record_failed")
            run.update(status="unknown",error="result_record_failed",billingUnknown=True); run["outputAssetIds"]=[asset_id]
            save_runs(project_id,runs,canvas_id)

    def run_job(project_id, canvas_id, run_id, result_id):
        with source_lock or nullcontext(), lock:
            runs = runs_for(project_id,canvas_id); run=find_run(runs,run_id); item=result_item(run,result_id)
            if item is None: return
            if item.get("status") != "pending":
                if run.get("localDisposition") == "queued_cancelled":
                    run["localDisposition"] = "drained"; save_runs(project_id, runs, canvas_id)
                return
            item["status"]="running"; run["status"]="running"; save_runs(project_id, runs, canvas_id)
            paths=[]
            try:
                for asset_id in run.get("inputAssetIds",[]): paths.append(project_assets.file(project_id,asset_id))
            except AssetFileUnavailableError: finish_item(project_id,canvas_id,run_id,result_id,"failed","input_asset_unavailable"); return
            snapshot=dict(run["snapshot"])
        try: image=image_service.generate(snapshot,input_images=paths) if image_service else (_ for _ in ()).throw(ImageGenerationError("provider_unconfigured"))
        except ImageGenerationError as error:
            with source_lock or nullcontext(),lock: finish_item(project_id,canvas_id,run_id,result_id,"unknown" if error.outcome_unknown else "failed",error.code)
            return
        with source_lock or nullcontext(),lock:
            try: install_result(project_id,canvas_id,run_id,result_id,image)
            except HTTPException: return

    @router.get("/canvases")
    def list_canvases(project_id: str, includeDeleted: bool = False):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            try:
                return {"canvases": store.list_canvases(project_id, include_deleted=includeDeleted)}
            except OSError:
                fail("creative_canvas_storage_invalid", "创作画布目录无法读取。", 503)

    @router.post("/canvases", status_code=201)
    def create_canvas(project_id: str, body: CanvasNameRequest):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            try:
                canvas = store.create_canvas(project_id, body.name, datetime.now(timezone.utc).isoformat())
            except ValueError as error:
                fail("creative_canvas_invalid", str(error), 422)
            except OSError:
                fail("creative_canvas_storage_failed", "创作画布目录无法保存。", 503)
            return {"canvas": canvas, "document": store.load(project_id, canvas["id"]), "runs": [], "batches": []}

    @router.patch("/canvases/{canvas_id}")
    def rename_canvas(project_id: str, canvas_id: str, body: CanvasNameRequest):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            try:
                return {"canvas": store.rename_canvas(project_id, canvas_id, body.name, datetime.now(timezone.utc).isoformat())}
            except KeyError:
                fail("creative_canvas_missing", "创作画布不存在或已删除。", 404)
            except ValueError as error:
                fail("creative_canvas_invalid", str(error), 422)
            except OSError:
                fail("creative_canvas_storage_failed", "创作画布目录无法保存。", 503)

    @router.delete("/canvases/{canvas_id}")
    def delete_canvas(project_id: str, canvas_id: str):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            require_canvas(project_id, canvas_id)
            if active_run(runs_for(project_id, canvas_id)) is not None:
                fail("creative_canvas_runs_active", "画布有进行中的生成运行，不能删除。")
            try:
                return {"canvas": store.set_canvas_deleted(project_id, canvas_id, True, datetime.now(timezone.utc).isoformat())}
            except KeyError:
                fail("creative_canvas_missing", "创作画布不存在。", 404)
            except ValueError as error:
                fail("creative_canvas_invalid", str(error), 422)
            except OSError:
                fail("creative_canvas_storage_failed", "创作画布目录无法保存。", 503)

    @router.post("/canvases/{canvas_id}/restore")
    def restore_canvas(project_id: str, canvas_id: str):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            try:
                return {"canvas": store.set_canvas_deleted(project_id, canvas_id, False, datetime.now(timezone.utc).isoformat())}
            except KeyError:
                fail("creative_canvas_missing", "创作画布不存在。", 404)
            except ValueError as error:
                fail("creative_canvas_invalid", str(error), 422)
            except OSError:
                fail("creative_canvas_storage_failed", "创作画布目录无法保存。", 503)

    def recover_after_restart() -> None:
        base = root / "project-files"
        if not base.is_dir() or base.is_symlink():
            return
        for project_dir in base.iterdir():
            if not project_dir.is_dir() or project_dir.is_symlink():
                continue
            project_id = project_dir.name
            try:
                canvases = store.list_canvases(project_id, include_deleted=True)
            except OSError:
                continue
            for canvas in canvases:
                canvas_id = canvas["id"]
                try:
                    with source_lock or nullcontext(), lock:
                        runs = run_store.load(project_id, canvas_id)
                        changed = False
                        for run in runs:
                            if run.get("status") in {"running", "cancel_requested"}:
                                reason = "cancel_requested_service_restarted" if run.get("cancellationRequested") else "service_restarted"
                                for item in output_items_for(run):
                                    if item.get("status") in {"running", "cancel_requested"}: item.update(status="unknown", error=reason, billingUnknown=True)
                                    elif item.get("status") == "pending": item.update(status="failed", error="queue_interrupted")
                                run.update(status="unknown", error=reason, billingUnknown=True); changed = True
                            elif run.get("status") == "queued":
                                run.update(status="failed", error="queue_interrupted"); changed = True
                            elif run.get("status") == "cancelled" and run.get("localDisposition") == "queued_cancelled":
                                # The prior process owned the queue callback. It cannot drain
                                # after a restart, so release only the run-level admission gate.
                                run["localDisposition"] = "drained"; changed = True
                        if changed:
                            run_store.save(project_id, runs, canvas_id)
                            for run in runs:
                                update_batch_for_run(project_id, canvas_id, run)
                        text_runs = text_run_store.load(project_id, canvas_id)
                        text_changed = False
                        for text_run in text_runs:
                            if text_run.get("status") in {"running", "cancel_requested"}:
                                reason = "cancel_requested_service_restarted" if text_run.get("cancellationRequested") else "service_restarted"
                                for version in text_run.get("versions", []):
                                    if version.get("status") in {"running", "cancel_requested"}:
                                        version.update(status="unknown", error=reason, billingUnknown=True)
                                    elif version.get("status") == "pending":
                                        version.update(status="failed", error="queue_interrupted")
                                text_run.update(status="unknown", error=reason, billingUnknown=True)
                                text_changed = True
                            elif text_run.get("status") == "queued":
                                for version in text_run.get("versions", []):
                                    if version.get("status") == "pending":
                                        version.update(status="failed", error="queue_interrupted")
                                text_run.update(status="failed", error="queue_interrupted")
                                text_changed = True
                        if text_changed:
                            text_run_store.save(project_id, text_runs, canvas_id)
                except (OSError, ValueError):
                    continue

    recover_after_restart()

    @router.get("")
    def get_canvas(project_id: str, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            try:
                document = store.load(project_id, selected_canvas_id(canvasId))
            except (OSError, ValueError):
                fail("creative_canvas_storage_invalid", "创作画布状态无法读取。", 503)
            return response(project_id, document, canvasId)

    @router.put("")
    def save_canvas(project_id: str, body: CanvasDocument, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            try:
                current = store.load(project_id, selected)
            except (OSError, ValueError):
                fail("creative_canvas_storage_invalid", "创作画布状态无法读取。", 503)
            if body.revision != current["revision"]:
                fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
            try:
                validate_document(body, assets_for(project_id))
                validate_current_outputs(project_id, selected, body)
            except ValueError as error:
                fail("creative_canvas_invalid", str(error), 422)
            saved = body.model_copy(update={"revision": current["revision"] + 1}).model_dump(mode="json")
            try:
                store.save(project_id, saved, selected)
            except (OSError, ValueError):
                fail("creative_canvas_storage_failed", "创作画布状态无法保存。", 503)
            return response(project_id, saved, canvasId)

    def result_item(run: dict[str, Any], result_id: str) -> Optional[dict[str, Any]]:
        return next((item for item in output_items_for(run) if item.get("resultId") == result_id), None)

    def validate_current_outputs(project_id: str, canvas_id: str, document: CanvasDocument) -> None:
        runs = runs_for(project_id, canvas_id)
        assets = assets_for(project_id)
        for node in document.nodes:
            ref = node.currentOutputRef
            if node.kind != "generate" or ref is None:
                continue
            run = next((item for item in runs if item.get("id") == ref.runId and item.get("nodeId") == node.id), None)
            item = result_item(run or {}, ref.resultId)
            asset_id = item.get("assetId") if isinstance(item, dict) and item.get("status") == "installed" else None
            if asset_id is None or assets.get(asset_id, {}).get("kind") != "image":
                raise ValueError("生成节点当前输出结果不存在或不可用")
            if node.outputAssetId is not None and node.outputAssetId != asset_id:
                raise ValueError("生成节点当前输出与结果身份不一致")

    def save_document_update(project_id: str, canvas_id: str, document: dict[str, Any], revision: int) -> dict[str, Any]:
        current = store.load(project_id, canvas_id)
        if revision != current["revision"]:
            fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
        try:
            validated = CanvasDocument.model_validate(document)
            validate_document(validated, assets_for(project_id))
            validate_current_outputs(project_id, canvas_id, validated)
        except ValueError as error:
            fail("creative_canvas_invalid", str(error), 422)
        saved = validated.model_copy(update={"revision": current["revision"] + 1}).model_dump(mode="json")
        try:
            store.save(project_id, saved, canvas_id)
        except (OSError, ValueError):
            fail("creative_canvas_storage_failed", "创作画布状态无法保存。", 503)
        return saved

    def mark_downstream_stale(document: dict[str, Any], node_id: str) -> None:
        nodes = {node.get("id"): node for node in document.get("nodes", []) if isinstance(node, dict)}
        pending = [node_id]
        visited: set[str] = set()
        while pending:
            source_id = pending.pop()
            if source_id in visited:
                continue
            visited.add(source_id)
            for edge in document.get("edges", []):
                if not isinstance(edge, dict) or edge.get("source") != source_id:
                    continue
                target = nodes.get(edge.get("target"))
                if target is not None:
                    if target.get("kind") == "generate":
                        target["stale"] = True
                    pending.append(target["id"])

    def update_current_output(project_id: str, canvas_id: str, run_id: str, result_id: str, revision: int, *, remove: bool = False) -> dict[str, Any]:
        document = store.load(project_id, canvas_id)
        if revision != document.get("revision"):
            fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
        runs = runs_for(project_id, canvas_id)
        run = find_run(runs, run_id)
        if not isinstance(run.get("outputItems"), list):
            # A legacy run stays byte-for-byte unchanged until a user performs a result action.
            run["outputItems"] = output_items_for(run)
        item = result_item(run, result_id)
        if item is None:
            fail("creative_canvas_result_missing", "生成结果不存在。", 404)
        node_id = run.get("nodeId")
        node = next((candidate for candidate in document.get("nodes", []) if candidate.get("id") == node_id and candidate.get("kind") == "generate"), None)
        if node is None:
            fail("creative_canvas_generate_missing", "生成结果所属节点不存在。", 409)
        current_ref = {"runId": run_id, "resultId": result_id}
        output_changed = False
        if remove:
            if item.get("status") not in {"installed", "failed", "cancelled"}:
                fail("creative_canvas_result_not_removable", "该生成结果当前不能删除。", 409)
            terminal_batch_states = {"completed", "failed", "blocked", "cancelled", "unknown"}
            for batch in batches_for(project_id, canvas_id):
                if batch.get("status") in terminal_batch_states:
                    continue
                direct_reference = any(isinstance(step, dict) and step.get("selectedOutputRef", {}).get("runId") == run_id and step.get("selectedOutputRef", {}).get("resultId") == result_id for step in batch.get("steps", []))
                direct_reference = direct_reference or any(isinstance(reference, dict) and reference.get("runId") == run_id and reference.get("resultId") == result_id for reference in batch.get("externalOutputRefs", []))
                snapshot = batch.get("documentSnapshot")
                step_ids = {step.get("nodeId") for step in batch.get("steps", []) if isinstance(step, dict) and isinstance(step.get("nodeId"), str)}
                snapshot_reference = False
                if isinstance(snapshot, dict):
                    try:
                        frozen_document = CanvasDocument.model_validate(snapshot)
                        frozen_nodes = {candidate.id: candidate for candidate in frozen_document.nodes}
                        for edge in frozen_document.edges:
                            if edge.target not in step_ids or edge.sourceHandle != "image":
                                continue
                            source = frozen_nodes.get(edge.source)
                            if source is None or source.kind != "generate":
                                continue
                            _asset_id, frozen_run_id, frozen_result_id = selected_generated_asset(
                                project_id, canvas_id, source, assets_for(project_id), None,
                            )
                            if frozen_run_id == run_id and frozen_result_id == result_id:
                                snapshot_reference = True
                                break
                    except (HTTPException, ValueError):
                        # An already-invalid frozen source cannot be made worse by this delete.
                        pass
                if direct_reference or snapshot_reference:
                    fail("creative_canvas_result_batch_active", "活动批次仍引用该生成结果，不能删除。", 409)
            output_changed = node.get("currentOutputRef") == current_ref
            item["status"] = "deleted"
            if output_changed:
                replacement = next((candidate for candidate in output_items_for(run) if candidate.get("status") == "installed" and not candidate.get("cancellationRequested")), None)
                if replacement is None:
                    node.pop("currentOutputRef", None); node.pop("outputAssetId", None)
                else:
                    node["currentOutputRef"] = {"runId": run_id, "resultId": replacement["resultId"]}
                    node["outputAssetId"] = replacement["assetId"]
            run["outputAssetIds"] = [candidate["assetId"] for candidate in output_items_for(run) if candidate.get("status") in {"installed", "deleted"} and isinstance(candidate.get("assetId"), str)]
        else:
            if item.get("status") != "installed" or not isinstance(item.get("assetId"), str):
                fail("creative_canvas_result_unavailable", "该生成结果尚不可作为当前输出。", 409)
            asset = assets_for(project_id).get(item["assetId"])
            if asset is None or asset.get("kind") != "image" or not project_assets.available(project_id, asset):
                fail("creative_canvas_result_unavailable", "该生成结果素材不可用。", 422)
            output_changed = node.get("currentOutputRef") != current_ref or node.get("outputAssetId") != item["assetId"]
            if not output_changed:
                return response(project_id, document, None if canvas_id == DEFAULT_CANVAS_ID else canvas_id)
            node["currentOutputRef"] = current_ref
            node["outputAssetId"] = item["assetId"]
        if not output_changed:
            if remove:
                save_runs(project_id, runs, canvas_id)
            return response(project_id, document, None if canvas_id == DEFAULT_CANVAS_ID else canvas_id)
        mark_downstream_stale(document, node_id)
        saved = save_document_update(project_id, canvas_id, document, revision)
        if remove:
            # The document update is first so a run-record write failure leaves a valid,
            # retryable state: the candidate remains installed and no node references deletion.
            save_runs(project_id, runs, canvas_id)
        return response(project_id, saved, None if canvas_id == DEFAULT_CANVAS_ID else canvas_id)

    @router.post("/runs/{run_id}/outputs/{result_id}/select")
    def select_output(project_id: str, run_id: str, result_id: str, body: ResultActionRequest, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            return update_current_output(project_id, selected_canvas_id(canvasId), run_id, result_id, body.revision)

    @router.delete("/runs/{run_id}/outputs/{result_id}")
    def delete_output(project_id: str, run_id: str, result_id: str, body: ResultActionRequest, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            return update_current_output(project_id, selected_canvas_id(canvasId), run_id, result_id, body.revision, remove=True)

    @router.post("/runs/{run_id}/outputs/{result_id}/cancel")
    def cancel_output(project_id: str, run_id: str, result_id: str, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            document = store.load(project_id, selected)
            runs = runs_for(project_id, selected)
            run = find_run(runs, run_id)
            item = result_item(run, result_id)
            if item is None:
                fail("creative_canvas_result_missing", "生成结果不存在。", 404)
            if item.get("status") == "pending":
                item.update(status="cancelled", cancellationRequested=True, localDisposition="queued_cancelled", providerDisposition="not_submitted", billingUnknown=False)
            elif item.get("status") == "running":
                # The local provider call is still in flight. Keep the whole run active until
                # its handler reports a definite outcome, so no overlapping run can begin.
                item.update(status="cancel_requested", cancellationRequested=True, localDisposition="none", providerDisposition="unsupported", billingUnknown=True)
            elif item.get("cancellationRequested"):
                return response(project_id, document, canvasId)
            else:
                fail("creative_canvas_result_not_cancellable", "生成结果已结束，不能取消。")
            aggregate_run_status(run)
            save_runs(project_id, runs, selected)
            return response(project_id, document, canvasId)

    @router.post("/runs/{run_id}/outputs/{result_id}/retry")
    def retry_output(project_id: str, run_id: str, result_id: str, body: ResultActionRequest, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            document = store.load(project_id, selected)
            if body.revision != document["revision"]:
                fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
            runs = runs_for(project_id, selected)
            run = find_run(runs, run_id)
            item = result_item(run, result_id)
            if item is None or item.get("status") != "failed" or run.get("status") in {"queued", "running", "cancel_requested", "unknown"}:
                fail("creative_canvas_result_not_retryable", "只有确定失败的生成结果可以重试。", 409)
            if active_run(runs) is not None:
                fail("creative_canvas_run_active", "画布有进行中的生成运行。")
            item.pop("error", None); item["status"] = "pending"
            run.update(status="queued", error=None, cancellationRequested=False, localDisposition="none", providerDisposition="unknown", billingUnknown=False)
            save_runs(project_id, runs, selected)
            batch_id, step_node_id = run.get("batchId"), run.get("batchStepNodeId")
            if isinstance(batch_id, str) and isinstance(step_node_id, str):
                batches = batches_for(project_id, selected)
                batch = next((item for item in batches if item.get("id") == batch_id), None)
                step = next((item for item in batch.get("steps", []) if item.get("nodeId") == step_node_id), None) if isinstance(batch, dict) else None
                if isinstance(step, dict):
                    step.pop("error", None); step["status"] = "running"
                    batch["status"] = "running"
                    save_batches(project_id, batches, selected)
            queue_key = f"{project_id}:{selected}:{run_id}:{result_id}"
            handler = lambda _queue_project: run_job(project_id, selected, run_id, result_id)
            if compute_queue is None or not compute_queue.submit("creative-canvas-candidate", queue_key, handler, project_id=project_id):
                finish_item(project_id, selected, run_id, result_id, "failed", "queue_unavailable")
            return response(project_id, document, canvasId)

    @router.post("/batches")
    def start_batch(project_id: str, body: BatchRequest, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            document_data = store.load(project_id, selected)
            if body.revision != document_data["revision"]:
                fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
            document = CanvasDocument.model_validate(document_data)
            if len(body.nodeIds) != len(set(body.nodeIds)):
                fail("creative_canvas_batch_invalid", "批量节点不能重复。", 422)
            node_index = {node.id: node for node in document.nodes}
            if any(node_id not in node_index or node_index[node_id].kind != "generate" for node_id in body.nodeIds):
                fail("creative_canvas_batch_invalid", "批量只能包含存在的生成节点。", 422)
            batches = batches_for(project_id, selected)
            if body.idempotencyKey is not None:
                existing = next((batch for batch in batches if batch.get("idempotencyKey") == body.idempotencyKey), None)
                if existing is not None:
                    return response(project_id, document_data, canvasId)
            existing_runs = runs_for(project_id, selected)
            if active_run(existing_runs) is not None:
                fail("creative_canvas_run_active", "画布有进行中的生成运行，不能开始批量。")
            if any(run.get("status") == "unknown" and run.get("nodeId") in body.nodeIds for run in existing_runs):
                fail("creative_canvas_run_active", "批量节点存在结果或计费状态未知的运行。")
            selected_nodes = set(body.nodeIds)
            dependencies = {node_id: [] for node_id in body.nodeIds}
            for edge in document.edges:
                if edge.source in selected_nodes and edge.target in selected_nodes and edge.sourceHandle == "image":
                    dependencies[edge.target].append(edge.source)
            # CanvasDocument has already rejected cycles. This Kahn pass preserves an explicit batch invariant.
            pending = {node_id: set(items) for node_id, items in dependencies.items()}
            resolved: set[str] = set()
            while pending:
                roots = [node_id for node_id, items in pending.items() if not items]
                if not roots:
                    fail("creative_canvas_batch_invalid", "批量生成节点不能形成循环。", 422)
                for node_id in roots:
                    pending.pop(node_id); resolved.add(node_id)
                for items in pending.values():
                    items.difference_update(resolved)
            external_output_refs: list[dict[str, str]] = []
            frozen_assets = assets_for(project_id)
            for edge in document.edges:
                if edge.target not in selected_nodes or edge.source in selected_nodes or edge.sourceHandle != "image":
                    continue
                source = node_index.get(edge.source)
                if source is None or source.kind != "generate":
                    continue
                asset_id, source_run_id, source_result_id = selected_generated_asset(project_id, selected, source, frozen_assets)
                if not all(isinstance(value, str) and value for value in (asset_id, source_run_id, source_result_id)):
                    fail("creative_canvas_batch_invalid", "批量外部生成输入无法冻结。", 422)
                external_output_refs.append({"edgeId": edge.id, "targetNodeId": edge.target, "sourceNodeId": edge.source,
                                             "runId": source_run_id, "resultId": source_result_id, "assetId": asset_id})
            batch_id = "canvas-batch-" + uuid4().hex
            batch = {"id": batch_id, "canvasId": selected, "status": "queued", "createdAt": datetime.now(timezone.utc).isoformat(),
                     "documentRevision": body.revision, "documentSnapshot": document.model_dump(mode="json"), "idempotencyKey": body.idempotencyKey,
                     "externalOutputRefs": external_output_refs,
                     "steps": [{"nodeId": node_id, "dependsOn": dependencies[node_id], "idempotencyKey": f"{batch_id}:{node_id}", "status": "queued"} for node_id in body.nodeIds]}
            batches.append(batch)
            save_batches(project_id, batches, selected)
            schedule_batch_advance(project_id, selected, batch_id)
            return response(project_id, document_data, canvasId)

    @router.post("/batches/{batch_id}/refresh")
    def refresh_batch(project_id: str, batch_id: str, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            document = store.load(project_id, selected)
            batch = next((item for item in batches_for(project_id, selected) if item.get("id") == batch_id), None)
            if batch is None:
                fail("creative_canvas_batch_missing", "批量运行不存在。", 404)
            if batch.get("status") not in {"completed", "failed", "blocked", "cancelled", "unknown"}:
                schedule_batch_advance(project_id, selected, batch_id)
            return response(project_id, document, canvasId)

    @router.post("/batches/{batch_id}/cancel")
    def cancel_batch(project_id: str, batch_id: str, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            document = store.load(project_id, selected)
            batches = batches_for(project_id, selected)
            batch = next((item for item in batches if item.get("id") == batch_id), None)
            if batch is None:
                fail("creative_canvas_batch_missing", "批量运行不存在。", 404)
            if batch.get("status") in {"completed", "failed", "blocked", "cancelled", "unknown"}:
                return response(project_id, document, canvasId)
            batch.update(status="cancelled", cancellationRequested=True, requestedAt=datetime.now(timezone.utc).isoformat())
            runs = runs_for(project_id, selected)
            for step in batch.get("steps", []):
                if not isinstance(step, dict):
                    continue
                if step.get("status") == "queued":
                    step.update(status="cancelled", error="batch_cancelled")
                    continue
                if step.get("status") != "running":
                    continue
                run = next((item for item in runs if item.get("id") == step.get("runId")), None)
                if run is None or run.get("cancellationRequested"):
                    continue
                if run.get("status") == "queued":
                    for output in output_items_for(run):
                        if output.get("status") == "pending":
                            output.update(status="cancelled", cancellationRequested=True,
                                          localDisposition="queued_cancelled", providerDisposition="not_submitted", billingUnknown=False)
                    run.update(cancellationRequested=True, localDisposition="queued_cancelled",
                               providerDisposition="not_submitted", billingUnknown=False)
                    aggregate_run_status(run, "cancelled")
                    step.update(status="cancelled", error="cancelled")
                elif run.get("status") == "running":
                    for output in output_items_for(run):
                        if output.get("status") == "running":
                            output.update(status="cancel_requested", cancellationRequested=True,
                                          localDisposition="none", providerDisposition="unsupported", billingUnknown=True)
                        elif output.get("status") == "pending":
                            output.update(status="cancelled", cancellationRequested=True,
                                          localDisposition="queued_cancelled", providerDisposition="not_submitted", billingUnknown=False)
                    run.update(cancellationRequested=True, localDisposition="none",
                               providerDisposition="unsupported", billingUnknown=True)
                    aggregate_run_status(run)
            save_runs(project_id, runs, selected)
            save_batches(project_id, batches, selected)
            return response(project_id, document, canvasId)

    def text_run_job(project_id: str, canvas_id: str, run_id: str, version_id: str) -> None:
        stream = None
        stream_key = (project_id, canvas_id, run_id, version_id)
        cancel_event = Event()

        def cancelled() -> bool:
            with source_lock or nullcontext(), lock:
                runs = text_runs_for(project_id, canvas_id)
                run = next((item for item in runs if item.get("id") == run_id), None)
                version = next((item for item in (run or {}).get("versions", []) if item.get("versionId") == version_id), None)
                return cancel_event.is_set() or bool((run or {}).get("cancellationRequested") or (version or {}).get("cancellationRequested"))

        with source_lock or nullcontext(), lock:
            runs = text_runs_for(project_id, canvas_id)
            run = next((item for item in runs if item.get("id") == run_id), None)
            version = next((item for item in (run or {}).get("versions", []) if item.get("versionId") == version_id), None)
            if run is None or version is None or version.get("status") != "pending":
                return
            run["status"] = "running"; version["status"] = "running"; save_text_runs(project_id, runs, canvas_id)
            snapshot = {**run["snapshot"], "versionId": version_id, "candidateIndex": version.get("index", 0)}
            active_text_cancel_events[stream_key] = cancel_event
        try:
            stream = iter(text_stream_for(snapshot, cancel_event, cancelled))
            with source_lock or nullcontext(), lock:
                active_text_streams[stream_key] = stream
            while not cancelled():
                try:
                    delta = next(stream)
                except StopIteration:
                    break
                if not isinstance(delta, str):
                    raise ValueError("文本服务增量无效")
                with source_lock or nullcontext(), lock:
                    runs = text_runs_for(project_id, canvas_id); run = next(item for item in runs if item.get("id") == run_id)
                    version = next(item for item in run["versions"] if item.get("versionId") == version_id)
                    text = str(version.get("text") or "")
                    available = MAX_TEXT_OUTPUT_CHARS - len(text)
                    if len(delta) > available:
                        if available > 0:
                            version["text"] = text + delta[:available]
                            version["seq"] += 1
                        version.update(status="failed", error="text_output_too_large", billingUnknown=True)
                        aggregate_text_run_status(run)
                        save_text_runs(project_id, runs, canvas_id)
                        raise _TextOutputTooLarge()
                    version["text"] = text + delta; version["seq"] += 1; save_text_runs(project_id, runs, canvas_id)
                # A delta that returned while cancellation was in flight is retained in
                # its original version, then no further next() call is made.
                if cancelled():
                    break
            with source_lock or nullcontext(), lock:
                runs = text_runs_for(project_id, canvas_id); run = next(item for item in runs if item.get("id") == run_id)
                version = next(item for item in run["versions"] if item.get("versionId") == version_id)
                if run.get("cancellationRequested") or version.get("cancellationRequested"):
                    version.update(status="installed" if version.get("text") else "cancelled",
                                   localDisposition="stream_closed", providerDisposition="unsupported", billingUnknown=True)
                else:
                    version["status"] = "installed"
                aggregate_text_run_status(run)
                save_text_runs(project_id, runs, canvas_id)
        except Exception as error:
            with source_lock or nullcontext(), lock:
                runs = text_runs_for(project_id, canvas_id); run = next(item for item in runs if item.get("id") == run_id)
                version = next(item for item in run["versions"] if item.get("versionId") == version_id)
                outcome_unknown = bool(getattr(error, "outcome_unknown", False))
                cancellation_requested = bool(run.get("cancellationRequested") or version.get("cancellationRequested"))
                billing_unknown = cancellation_requested or outcome_unknown or bool(getattr(error, "billing_unknown", False))
                version.update(status="unknown" if cancellation_requested or outcome_unknown else "failed",
                               error=str(getattr(error, "code", "text_generation_failed")),
                               billingUnknown=billing_unknown)
                if cancellation_requested:
                    version.setdefault("localDisposition", "stream_closed")
                    version.setdefault("providerDisposition", "unsupported")
                aggregate_text_run_status(run)
                save_text_runs(project_id, runs, canvas_id)
        finally:
            close_text_stream(stream)
            with source_lock or nullcontext(), lock:
                active_text_streams.pop(stream_key, None)
                active_text_cancel_events.pop(stream_key, None)

    @router.post("/nodes/{node_id}/text-runs")
    def start_text_run(project_id: str, node_id: str, body: TextRunRequest, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            selected = selected_canvas_id(canvasId)
            document_data = store.load(project_id, selected)
            runs = text_runs_for(project_id, selected)
            if body.idempotencyKey:
                existing = next((item for item in runs if item.get("nodeId") == node_id and item.get("idempotencyKey") == body.idempotencyKey), None)
                if existing is not None:
                    return response(project_id, document_data, canvasId)
            if body.revision != document_data["revision"]:
                fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
            document = CanvasDocument.model_validate(document_data)
            node = next((item for item in document.nodes if item.id == node_id and item.kind == "text"), None)
            if node is None:
                fail("creative_canvas_text_missing", "文本节点不存在。", 404)
            if not node.prompt or not node.prompt.strip() or not node.providerId or not node.modelId or not node.configurationVersion:
                fail("text_generation_unconfigured", "请先选择已配置的文本生成模型并填写提示词。", 422)
            if any(item.get("status") in {"queued", "running", "cancel_requested", "unknown"} and item.get("nodeId") == node_id for item in runs):
                fail("creative_canvas_text_run_active", "文本节点已有未完成或状态未知的生成。")
            if text_service is None:
                fail("text_generation_unconfigured", "当前没有已验证的自由文本生成服务。", 422)
            run_id = "text-run-" + uuid4().hex
            references = []
            nodes = {item.id: item for item in document.nodes}
            for order, edge in enumerate(ordered_references(document, node_id), start=1):
                source = nodes[edge.source]
                if edge.sourceHandle == "text":
                    text = source.text or ""
                    source_version_id = source.currentTextVersionId if source.kind == "text" else None
                    if source_version_id:
                        version = next((version for run in text_runs_for(project_id, selected) if run.get("nodeId") == source.id for version in run.get("versions", []) if version.get("versionId") == source_version_id and version.get("status") in {"completed", "installed"}), None)
                        if version is None: fail("creative_canvas_reference_unavailable", "文本引用版本不可用。", 422)
                        text = version.get("text", "")
                    references.append({"edgeId": edge.id, "sourceNodeId": source.id, "sourceHandle": "text", "order": order, "dataType": "text", "textVersionId": source_version_id, "text": text})
                else:
                    source_run_id = source_result_id = None
                    if source.kind == "media":
                        asset_id = source.assetId
                    elif source.kind == "generate" and edge.sourceHandle == "image":
                        asset_id, source_run_id, source_result_id = selected_generated_asset(
                            project_id, selected, source, assets_for(project_id),
                        )
                    else:
                        fail("creative_canvas_reference_unavailable", "文本生成媒体引用不可用。", 422)
                    asset = assets_for(project_id).get(asset_id or "")
                    if not isinstance(asset, dict) or not project_assets.available(project_id, asset): fail("creative_canvas_reference_unavailable", "媒体引用不可用。", 422)
                    if asset.get("kind") != edge.sourceHandle: fail("creative_canvas_reference_unavailable", "媒体端口与素材类型不一致。", 422)
                    if edge.sourceHandle != "image": fail("creative_canvas_text_media_unsupported", "当前文本模型未验证视频或音频引用传输能力。", 422)
                    references.append({"edgeId": edge.id, "sourceNodeId": source.id, "sourceHandle": edge.sourceHandle,
                                       "order": order, "dataType": edge.sourceHandle, "assetId": asset_id,
                                       **({"sourceRunId": source_run_id, "sourceResultId": source_result_id} if source_run_id and source_result_id else {})})
            snapshot = {"projectId": project_id, "canvasId": selected, "runId": run_id,
                        "prompt": node.prompt.strip(), "providerId": node.providerId, "modelId": node.modelId,
                        "configurationVersion": node.configurationVersion, "references": references, "count": body.count or 1}
            validator = getattr(text_service, "validate", None)
            if callable(validator):
                try: validator({**snapshot, "versionId": "validation"})
                except Exception: fail("text_generation_model_unavailable", "所选文本模型或配置版本当前不可用。", 422)
            created_at = datetime.now(timezone.utc).isoformat()
            versions = [{"versionId": "text-version-" + uuid4().hex, "runId": run_id, "index": index,
                         "status": "pending", "text": "", "seq": 0, "createdAt": created_at,
                         "cancellationRequested": False, "billingUnknown": False}
                        for index in range(body.count or 1)]
            run = {"id": run_id, "canvasId": selected, "nodeId": node_id, "status": "queued", "createdAt": datetime.now(timezone.utc).isoformat(),
                   "snapshot": snapshot, "idempotencyKey": body.idempotencyKey, "versions": versions,
                   "cancellationRequested": False, "billingUnknown": False}
            runs.append(run); save_text_runs(project_id, runs, selected)
            for version in versions:
                output_version_id = version["versionId"]
                queue_key = f"{project_id}:{selected}:{run_id}:{output_version_id}"
                handler = lambda _owner, version_id=output_version_id: text_run_job(project_id, selected, run_id, version_id)
                if compute_queue is None or not compute_queue.submit("creative-canvas-text", queue_key, handler, project_id=project_id):
                    version.update(status="failed", error="queue_unavailable")
            aggregate_text_run_status(run)
            save_text_runs(project_id, runs, selected)
            return response(project_id, document_data, canvasId)

    @router.post("/text-runs/{run_id}/versions/{version_id}/select")
    def select_text_version(project_id: str, run_id: str, version_id: str, body: ResultActionRequest, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            selected = selected_canvas_id(canvasId); document = store.load(project_id, selected)
            if body.revision != document["revision"]: fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
            run = next((item for item in text_runs_for(project_id, selected) if item.get("id") == run_id), None)
            version = next((item for item in (run or {}).get("versions", []) if item.get("versionId") == version_id and item.get("status") in {"completed", "installed"}), None)
            node = next((item for item in document.get("nodes", []) if item.get("id") == (run or {}).get("nodeId")), None)
            if version is None or node is None: fail("creative_canvas_text_version_unavailable", "文本版本不可采用。", 409)
            node["text"] = version["text"]; node["currentTextVersionId"] = version_id
            mark_downstream_stale(document, node["id"])
            saved = save_document_update(project_id, selected, document, body.revision)
            return response(project_id, saved, canvasId)

    @router.post("/text-runs/{run_id}/versions/{version_id}/assets")
    def install_text_version_asset(project_id: str, run_id: str, version_id: str, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            selected = selected_canvas_id(canvasId)
            runs = text_runs_for(project_id, selected)
            run = next((item for item in runs if item.get("id") == run_id), None)
            version = next((item for item in (run or {}).get("versions", []) if item.get("versionId") == version_id), None)
            if version is None or version.get("status") not in {"installed", "failed"} or not str(version.get("text") or "").strip():
                fail("creative_canvas_text_version_unavailable", "文本版本没有可入库的正文。", 409)
            if isinstance(version.get("assetId"), str):
                return response(project_id, store.load(project_id, selected), canvasId)
            state = preproduction_store.load(project_id)
            existing = next((asset for asset in state.get("assets", []) if asset.get("sourceTextRunId") == run_id and asset.get("sourceTextVersionId") == version_id), None)
            if existing is None:
                asset_id = project_assets.new_id("canvas-text")
                encoded = str(version["text"]).encode("utf-8")
                descriptor, name = tempfile.mkstemp(dir=root, prefix=".creative-canvas-text-", suffix=".txt")
                try:
                    with os.fdopen(descriptor, "wb") as target:
                        target.write(encoded)
                    record = {"id": asset_id, "name": "创作画布文本.txt", "kind": "text", "role": "reference",
                              "file": asset_id + ".txt", "mimeType": "text/plain", "bytes": len(encoded),
                              "sourceTextRunId": run_id, "sourceTextVersionId": version_id}
                    with project_assets.install(project_id, state, Path(name), record, move=True):
                        state["revision"] += 1
                        preproduction_store.save(project_id, state)
                finally:
                    Path(name).unlink(missing_ok=True)
            else:
                asset_id = existing["id"]
            version["assetId"] = asset_id
            save_text_runs(project_id, runs, selected)
            return response(project_id, store.load(project_id, selected), canvasId)

    @router.post("/text-runs/{run_id}/cancel")
    def cancel_text_run(project_id: str, run_id: str, canvasId: Optional[str] = None):
        streams: list[Any] = []
        cancel_events: list[Event] = []
        with source_lock or nullcontext(), lock:
            selected=selected_canvas_id(canvasId); runs=text_runs_for(project_id, selected); run=next((item for item in runs if item.get("id")==run_id),None)
            if run is None: fail("creative_canvas_text_run_missing", "文本运行不存在。", 404)
            if run.get("status")=="queued":
                run.update(status="cancelled", cancellationRequested=True, localDisposition="queued_cancelled")
                for version in run.get("versions",[]): version.update(status="cancelled", cancellationRequested=True)
            elif run.get("status")=="running":
                run.update(status="cancel_requested", cancellationRequested=True, billingUnknown=True, providerDisposition="unsupported")
                for version in run.get("versions", []):
                    if version.get("status") == "pending": version.update(status="cancelled", cancellationRequested=True)
                    elif version.get("status") == "running":
                        version.update(status="cancel_requested", cancellationRequested=True, billingUnknown=True,
                                       localDisposition="stream_closing", providerDisposition="unsupported")
                        stream = active_text_streams.get((project_id, selected, run_id, version.get("versionId")))
                        if stream is not None: streams.append(stream)
                        cancel_event = active_text_cancel_events.get((project_id, selected, run_id, version.get("versionId")))
                        if cancel_event is not None: cancel_events.append(cancel_event)
            else: return response(project_id, store.load(project_id,selected), canvasId)
            save_text_runs(project_id,runs,selected)
        for cancel_event in cancel_events:
            cancel_event.set()
        for stream in streams:
            close_text_stream(stream)
        return response(project_id,store.load(project_id,selected),canvasId)

    @router.post("/nodes/{node_id}/runs")
    def start_run(project_id: str, node_id: str, body: RunRequest, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            try:
                document_data = store.load(project_id, selected)
            except (OSError, ValueError):
                fail("creative_canvas_storage_invalid", "创作画布状态无法读取。", 503)
            runs = runs_for(project_id, selected)
            if body.idempotencyKey is not None:
                existing = next((run for run in runs if run.get("nodeId") == node_id and run.get("idempotencyKey") == body.idempotencyKey), None)
                if existing is not None:
                    return response(project_id, document_data, canvasId)
            if body.revision != document_data["revision"]:
                fail("creative_canvas_conflict", "创作画布已被更新，请刷新后重试。")
            document = CanvasDocument.model_validate(document_data)
            if any(run.get("localDisposition") == "queued_cancelled" for run in runs):
                fail("creative_canvas_queue_draining", "上一次任务仍在清理队列，请稍后重试。")
            if active_run(runs) is not None or any(run.get("status") == "unknown" and run.get("nodeId") == node_id for run in runs):
                fail("creative_canvas_run_active", "当前节点已有未完成或状态未知的生成运行。")
            queue_key = f"{project_id}:{selected}"
            if compute_queue is not None and getattr(compute_queue, "is_active", lambda *_: False)("creative-canvas", queue_key):
                fail("creative_canvas_queue_draining", "上一次任务仍在清理队列，请稍后重试。")
            count = body.count if body.count is not None else document.nodes[[node.id for node in document.nodes].index(node_id)].params.count
            snapshot, _input_paths, input_asset_ids = snapshot_for(project_id, selected, document, node_id, count=count)
            run_id = "canvas-run-" + uuid4().hex
            created_at = datetime.now(timezone.utc).isoformat()
            run = {"id": run_id, "canvasId": selected, "nodeId": node_id, "status": "queued", "error": None,
                   "createdAt": created_at, "documentRevision": body.revision,
                   "configurationRevision": snapshot["revision"], "inputAssetIds": input_asset_ids,
                   "outputAssetIds": [], "outputItems": [{"resultId": "canvas-result-" + uuid4().hex, "requestId": "canvas-request-" + uuid4().hex, "mediaType": "image", "status": "pending", "sourceRunId": run_id, "createdAt": created_at, "cancellationRequested": False, "localDisposition": "none", "providerDisposition": "unknown", "billingUnknown": False} for _ in range(count)],
                   "snapshot": snapshot, "idempotencyKey": body.idempotencyKey,
                   "cancellationRequested": False, "localDisposition": "none", "providerDisposition": "unknown",
                   "billingUnknown": False}
            runs.append(run); save_runs(project_id, runs, selected)
            for item in run["outputItems"]:
                key = f"{project_id}:{selected}:{run_id}:{item['resultId']}"
                handler = lambda _owner, rid=run_id, oid=item["resultId"]: run_job(project_id, selected, rid, oid)
                if compute_queue is None or not compute_queue.submit("creative-canvas-candidate", key, handler, project_id=project_id):
                    finish_item(project_id, selected, run_id, item["resultId"], "failed", "queue_unavailable")
            return response(project_id, document_data, canvasId)

    @router.post("/runs/{run_id}/refresh")
    def refresh_run(project_id: str, run_id: str, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            document = store.load(project_id, selected)
            find_run(runs_for(project_id, selected), run_id)
            return response(project_id, document, canvasId)

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(project_id: str, run_id: str, canvasId: Optional[str] = None):
        with source_lock or nullcontext(), lock:
            ensure_project(project_id)
            if canvasId is not None:
                require_canvas(project_id, canvasId)
            selected = selected_canvas_id(canvasId)
            document = store.load(project_id, selected)
            runs = runs_for(project_id, selected)
            run = find_run(runs, run_id)
            if run.get("cancellationRequested"):
                return response(project_id, document, canvasId)
            requested_at = datetime.now(timezone.utc).isoformat()
            if run.get("status") == "queued":
                for item in output_items_for(run):
                    if item.get("status") == "pending": item.update(status="cancelled", cancellationRequested=True, localDisposition="queued_cancelled", providerDisposition="not_submitted", billingUnknown=False)
                run.update(status="cancelled", error="cancelled", cancellationRequested=True, requestedAt=requested_at, localDisposition="queued_cancelled", providerDisposition="not_submitted", billingUnknown=False)
            elif run.get("status") == "running":
                for item in output_items_for(run):
                    if item.get("status") == "running": item.update(status="cancel_requested", cancellationRequested=True, localDisposition="none", providerDisposition="unsupported", billingUnknown=True)
                    elif item.get("status") == "pending": item.update(status="cancelled", cancellationRequested=True, localDisposition="queued_cancelled", providerDisposition="not_submitted", billingUnknown=False)
                run.update(status="cancel_requested", cancellationRequested=True, requestedAt=requested_at, localDisposition="none", providerDisposition="unsupported", billingUnknown=True)
            else:
                fail("creative_canvas_run_not_cancellable", "运行已结束，不能取消。")
            save_runs(project_id, runs, selected)
            update_batch_for_run(project_id, selected, run)
            return response(project_id, document, canvasId)

    return router
