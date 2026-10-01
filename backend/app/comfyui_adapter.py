"""Optional, process-separated ComfyUI video adapter.

This module only speaks ComfyUI's public HTTP API.  It intentionally does not
copy or import ComfyUI (GPL-3.0) code; a user-operated ComfyUI process remains
an external service.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from .comfyui_client import ComfyUIClient, ComfyUIClientError, validate_comfy_url
from .generation_service import GenerationError, VideoGenerationAdapter, VideoResultWriter
from .project_assets import ProjectAssets
from .workflow_templates import build_workflow, template_info


_FILE_NAME = "comfyui-adapter.json"
_WORKFLOW_ID = "wan22_i2v"
_MODEL_ID = "wan22-i2v"
_PROFILE_ID = "comfyui-wan22-i2v"
_ADAPTER_VERSION = "comfyui-http-v1"
_CAPABILITIES = {
    "version": "comfyui-wan22-i2v-api-v1",
    "profiles": [{
        "id": _PROFILE_ID, "mode": "image-to-video", "referencePatterns": [["first-frame"]],
        "combinations": [{"durationSeconds": 5, "aspectRatio": "16:9", "resolution": "1280x720", "audio": False}],
        "minResults": 1, "maxResults": 1,
    }],
    "cancel": True,
}


class ComfyUIConfigurationStore:
    """Atomic, non-secret local settings with distinct configuration states."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / _FILE_NAME
        self.lock = RLock()

    def status(self) -> dict[str, Any]:
        with self.lock:
            value = self._read()
            workflow = {
                "id": _WORKFLOW_ID,
                "version": template_info(_WORKFLOW_ID)["provenance"]["commit"],
                "requiredNodes": template_info(_WORKFLOW_ID)["requiredNodes"],
                "requiredModels": template_info(_WORKFLOW_ID)["requiredModels"],
            }
            if value is None:
                return {"state": "unconfigured", "workflow": workflow, "workflowVerified": False}
            return {
                "state": "workflow-verified" if value["workflowVerified"] else "configured-unverified",
                "baseUrl": value["baseUrl"], "workflowId": _WORKFLOW_ID,
                "workflowVersion": template_info(_WORKFLOW_ID)["provenance"]["commit"],
                "workflowVerified": value["workflowVerified"], "configurationVersion": value["revision"],
                "workflow": workflow,
            }

    def save(self, base_url: str) -> dict[str, Any]:
        normalized = validate_comfy_url(base_url)
        with self.lock:
            previous = self._read()
            revision = 1 if previous is None else previous["revision"] + 1
            self._write({"schemaVersion": 1, "revision": revision, "baseUrl": normalized, "workflowVerified": False})
            return self.status()

    def record_verified_workflow(self, verification: dict[str, Any]) -> dict[str, Any]:
        """Record a completed, externally evidenced workflow verification.

        This is deliberately not exposed as a configuration write API: a saved
        URL or successful dependency probe must never advertise execution support.
        """
        required = {"workflowId", "workflowVersion", "configurationVersion", "comfyVersion", "promptId", "historyStatus", "resultAssetId", "verifiedAt"}
        if not isinstance(verification, dict) or set(verification) != required or not all(isinstance(verification.get(key), str) and verification[key].strip() for key in required):
            raise ValueError("ComfyUI 工作流验证记录无效。")
        with self.lock:
            value = self._read()
            if value is None:
                raise ValueError("ComfyUI 尚未配置。")
            provenance = template_info(_WORKFLOW_ID)["provenance"]
            if (
                verification["workflowId"] != _WORKFLOW_ID
                or verification["workflowVersion"] != provenance["commit"]
                or verification["configurationVersion"] != str(value["revision"])
                or verification["historyStatus"] != "success"
            ):
                raise ValueError("ComfyUI 工作流验证记录无效。")
            value["workflowVerified"] = True
            value["verification"] = dict(verification)
            self._write(value)
            return self.status()

    def verified(self) -> dict[str, Any] | None:
        with self.lock:
            value = self._read()
            return dict(value) if value and value["workflowVerified"] else None

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("ComfyUI 配置无效。") from error
        required = {"schemaVersion", "revision", "baseUrl", "workflowVerified"}
        if not isinstance(value, dict) or set(value) not in (required, required | {"verification"}) or value.get("schemaVersion") != 1:
            raise ValueError("ComfyUI 配置无效。")
        if type(value.get("revision")) is not int or value["revision"] < 1 or type(value.get("workflowVerified")) is not bool:
            raise ValueError("ComfyUI 配置无效。")
        value["baseUrl"] = validate_comfy_url(value.get("baseUrl"))
        if value["workflowVerified"] != isinstance(value.get("verification"), dict):
            raise ValueError("ComfyUI 配置无效。")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw = tempfile.mkstemp(dir=self.path.parent, prefix=".comfyui-adapter-", suffix=".part")
        temporary = Path(raw)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(value, target, separators=(",", ":"), allow_nan=False)
                target.flush(); os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class ComfyUIAdapter:
    """Maps one pinned I2V workflow into the shared asynchronous video contract."""

    def __init__(self, root: Path, settings: ComfyUIConfigurationStore, *, client_factory: Callable[[str], ComfyUIClient] = ComfyUIClient) -> None:
        self.root = Path(root)
        self.assets = ProjectAssets(root)
        self.settings = settings
        self.client_factory = client_factory

    def as_generation_adapter(self) -> VideoGenerationAdapter:
        return VideoGenerationAdapter("comfyui", _ADAPTER_VERSION, "video", self.capability_catalog, self.catalog, self.submit, self.poll, self.download, self.cancel)

    def catalog(self) -> list[dict[str, Any]]:
        return [{"provider": "comfyui", "model": _MODEL_ID, "label": "ComfyUI · Wan2.2 I2V（已验证）"}] if self.settings.verified() else []

    def capability_catalog(self, provider: str, model: str) -> dict[str, Any] | None:
        config = self.settings.verified()
        if config is None or (provider, model) != ("comfyui", _MODEL_ID):
            return None
        return {"provider": provider, "model": model, "configurationVersion": str(config["revision"]), "capabilities": _CAPABILITIES}

    def submit(self, run: dict[str, Any]) -> dict[str, Any]:
        config = self._config_for_run(run)
        params = self._validated_params(run)
        reference = params["references"][0]
        try:
            source = self.assets.file(run["_projectId"], reference["assetId"])
            with self._client(config["baseUrl"]) as client:
                uploaded = client.upload_image(source, f"{reference['assetId']}.png")
                workflow = build_workflow(_WORKFLOW_ID, run["input"]["prompt"], "", 1280, 720, 81, 16, 0, uploaded)
                prompt_id = client.submit(workflow)
        except (OSError, ValueError, ComfyUIClientError) as error:
            raise GenerationError("generation_comfyui_submit_unknown", outcome="unknown", billing_unknown=True) from error
        return {"remoteTaskId": prompt_id, "status": "queued", "pollCursor": "history-v1", "callbackCursor": None}

    def poll(self, run: dict[str, Any]) -> dict[str, Any]:
        config = self._config_for_run(run)
        try:
            with self._client(config["baseUrl"]) as client:
                observed = client.poll(run["remoteTaskId"])
        except ComfyUIClientError:
            return {"status": "unknown", "pollCursor": "history-v1", "callbackCursor": None, "billingUnknown": True}
        state = observed["status"]
        base = {"pollCursor": "history-v1", "callbackCursor": None, "billingUnknown": state == "unknown"}
        if state in {"queued", "running", "unknown"}:
            return {"status": state, **base}
        if state == "failed":
            return {"status": "failed", "reason": "generation_comfyui_failed", **base}
        outputs = observed.get("outputs", [])
        if not outputs:
            return {"status": "failed", "reason": "generation_comfyui_output_missing", **base}
        try:
            result_id = _encode_output(outputs[0])
            name = outputs[0]["filename"]
        except (KeyError, TypeError, ValueError):
            return {"status": "failed", "reason": "generation_comfyui_output_invalid", **base}
        return {"status": "succeeded", "results": [{"index": 0, "remoteResultId": result_id, "name": name}], "failedItems": [], **base}

    def download(self, run: dict[str, Any], result: dict[str, Any], output: VideoResultWriter) -> None:
        config = self._config_for_run(run)
        try:
            with self._client(config["baseUrl"]) as client:
                output.write(client.fetch_output(_decode_output(result["remoteResultId"])))
        except (KeyError, TypeError, ValueError, ComfyUIClientError) as error:
            raise OSError("generation_result_download_failed") from error

    def cancel(self, run: dict[str, Any]) -> dict[str, Any]:
        config = self._config_for_run(run)
        try:
            with self._client(config["baseUrl"]) as client:
                result = client.cancel(run["remoteTaskId"])
        except ComfyUIClientError:
            return {"status": "unknown", "billingUnknown": True, "pollCursor": "history-v1"}
        return {"status": result, "billingUnknown": result != "cancelled", "pollCursor": "history-v1"}

    def _config_for_run(self, run: dict[str, Any]) -> dict[str, Any]:
        config = self.settings.verified()
        if config is None or str(config["revision"]) != str(run.get("configurationVersion")):
            raise GenerationError("generation_configuration_unavailable")
        return config

    @staticmethod
    def _validated_params(run: dict[str, Any]) -> dict[str, Any]:
        params = run.get("input", {}).get("videoParams")
        expected = {"profileId": _PROFILE_ID, "mode": "image-to-video", "durationSeconds": 5, "aspectRatio": "16:9", "resolution": "1280x720", "audio": False, "resultCount": 1}
        if not isinstance(params, dict) or any(params.get(key) != value for key, value in expected.items()):
            raise GenerationError("generation_request_invalid")
        references = params.get("references")
        if not isinstance(references, list) or len(references) != 1 or set(references[0]) != {"assetId", "assetVersionId", "referenceType"} or references[0].get("referenceType") != "first-frame":
            raise GenerationError("generation_reference_pattern_unsupported")
        return params

    def _client(self, base_url: str):
        client = self.client_factory(base_url)
        class _Scope:
            def __enter__(self): return client
            def __exit__(self, *args):
                close = getattr(client, "close", None)
                if close: close()
        return _Scope()


def _encode_output(value: dict[str, Any]) -> str:
    if set(value) != {"filename", "subfolder", "type"} or not all(isinstance(value.get(key), str) for key in value):
        raise ValueError("output")
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_output(value: str) -> dict[str, str]:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("output")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict) or set(decoded) != {"filename", "subfolder", "type"}:
        raise ValueError("output")
    return decoded


def create_comfyui_adapter_router(settings: ComfyUIConfigurationStore) -> Any:
    """Settings endpoint: saving and connectivity are deliberately separate."""
    from fastapi import APIRouter, HTTPException, Request

    router = APIRouter(prefix="/api/comfyui-adapter")

    @router.get("/status")
    def status():
        return settings.status()

    @router.put("/configuration")
    async def save_configuration(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"baseUrl"}:
                raise ValueError()
            return settings.save(body["baseUrl"])
        except (TypeError, ValueError):
            raise HTTPException(422, "ComfyUI 配置输入无效。") from None

    @router.post("/connection-check")
    def connection_check():
        status = settings.status()
        base_url = status.get("baseUrl")
        if not isinstance(base_url, str):
            raise HTTPException(422, "请先保存 ComfyUI 地址；保存配置不等于连接或工作流验证。")
        try:
            workflow = build_workflow(_WORKFLOW_ID, "verification only", "", 1280, 720, 81, 16, 0, "verification.png")
            with ComfyUIClient(base_url) as client:
                report = client.check(workflow)
        except (ValueError, ComfyUIClientError):
            report = {"connected": False, "ready": False, "message": "无法连接本地 ComfyUI。"}
        return {"configuration": status, "connection": report, "workflowVerified": status["workflowVerified"]}

    return router


__all__ = ["ComfyUIAdapter", "ComfyUIConfigurationStore", "create_comfyui_adapter_router"]
