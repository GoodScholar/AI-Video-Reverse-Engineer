"""受限的本地 ComfyUI HTTP 客户端。"""

import ipaddress
import json
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx


_MAX_RESPONSE_BYTES = 2_000_000
_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_ALLOWED_OUTPUT_TYPES = {"output", "temp"}


class ComfyUIClientError(RuntimeError):
    """ComfyUI 响应、网络或安全边界不符合契约。"""

    def __init__(self, message: str, *, outcome_unknown: bool = False, status_code: Optional[int] = None) -> None:
        self.outcome_unknown = outcome_unknown
        self.status_code = status_code
        super().__init__(message)


def validate_comfy_url(base_url: str) -> str:
    """规范化仅可访问本机或私网的 ComfyUI HTTP 地址。"""

    if not isinstance(base_url, str) or not base_url:
        raise ValueError("ComfyUI 地址无效。")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("ComfyUI 地址必须是无路径的本机或私网 HTTP(S) 地址。")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as error:
            raise ValueError("ComfyUI 地址必须使用本机或私网 IP。") from error
        if not (address.is_loopback or address.is_private):
            raise ValueError("ComfyUI 地址必须使用本机或私网 IP。")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("ComfyUI 地址端口无效。") from error
    authority = hostname if ":" not in hostname else f"[{hostname}]"
    if port is not None:
        authority = f"{authority}:{port}"
    return urlunsplit((parsed.scheme, authority, "", "", ""))


class ComfyUIClient:
    def __init__(self, base_url: str, *, client: Optional[httpx.Client] = None) -> None:
        self.base_url = validate_comfy_url(base_url)
        self._owned_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30.0), trust_env=False, follow_redirects=False,
        )

    def __enter__(self) -> "ComfyUIClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def check(self, workflow: Mapping[str, Any]) -> dict:
        """检查连接、版本、节点和模型，网络失败不会抛给界面层。"""

        try:
            system_stats = self._get_json("/system_stats")
            object_info = self._get_json("/object_info")
        except ComfyUIClientError as error:
            return {
                "connected": False, "version": None, "missingNodes": [], "missingModels": [],
                "ready": False, "message": f"无法连接本地 ComfyUI：{error}",
            }
        if not isinstance(object_info, Mapping):
            return {
                "connected": True, "version": _version(system_stats), "missingNodes": [], "missingModels": [],
                "ready": False, "message": "ComfyUI 节点信息无效。",
            }
        node_types = sorted({node.get("class_type") for node in workflow.values() if isinstance(node, Mapping)
                             and isinstance(node.get("class_type"), str)})
        missing_nodes = [node_type for node_type in node_types if node_type not in object_info]
        available_models = _collect_strings(object_info)
        required_models = sorted({
            value for node in workflow.values() if isinstance(node, Mapping)
            for value in _collect_strings(node.get("inputs", {})) if value.endswith(".safetensors")
        })
        missing_models = [model for model in required_models if model not in available_models]
        ready = not missing_nodes and not missing_models
        if ready:
            message = "本地 ComfyUI 环境完整。"
        else:
            details = []
            if missing_nodes:
                details.append("缺少节点：" + "、".join(missing_nodes))
            if missing_models:
                details.append("缺少模型：" + "、".join(missing_models))
            message = "；".join(details) + "。"
        return {
            "connected": True, "version": _version(system_stats), "missingNodes": missing_nodes,
            "missingModels": missing_models, "ready": ready, "message": message,
        }

    def upload_image(self, path: Path, name: str) -> str:
        """上传输入图像或控制视频，返回可安全绑定到 LoadImage/LoadVideo 的名称。"""

        _validate_leaf_name(name)
        source = Path(path)
        if not source.is_file() or source.is_symlink():
            raise ComfyUIClientError("待上传输入文件不可用。")
        try:
            with source.open("rb") as file_handle:
                response = self._request(
                    "POST", "/upload/image",
                    files={"image": (name, file_handle), "type": (None, "input"), "overwrite": (None, "true")},
                )
        except OSError as error:
            raise ComfyUIClientError("无法读取待上传输入文件。") from error
        body = _decode_json(response)
        if not isinstance(body, Mapping):
            raise ComfyUIClientError("ComfyUI 上传响应无效。")
        returned_name = body.get("name")
        subfolder = body.get("subfolder", "")
        if not isinstance(returned_name, str) or not isinstance(subfolder, str):
            raise ComfyUIClientError("ComfyUI 上传响应无效。")
        _validate_leaf_name(returned_name)
        _validate_subfolder(subfolder)
        return f"{subfolder}/{returned_name}" if subfolder else returned_name

    def submit(self, workflow: Mapping[str, Any]) -> str:
        try:
            response = self._request("POST", "/prompt", json={"prompt": dict(workflow)})
            body = _decode_json(response)
        except ComfyUIClientError as error:
            if error.status_code is not None and 400 <= error.status_code < 500:
                raise
            raise ComfyUIClientError("无法确认 ComfyUI 是否已接收工作流。", outcome_unknown=True) from error
        prompt_id = body.get("prompt_id") if isinstance(body, Mapping) else None
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyUIClientError("ComfyUI 未返回 prompt ID，无法确认是否已接收工作流。", outcome_unknown=True)
        return prompt_id

    def poll(self, prompt_id: str) -> dict:
        _validate_prompt_id(prompt_id)
        try:
            history = self._get_json(f"/history/{prompt_id}")
        except ComfyUIClientError:
            return _unknown_poll_result("无法获取 ComfyUI 执行状态。")
        entry = history.get(prompt_id) if isinstance(history, Mapping) else None
        if isinstance(entry, Mapping):
            status = entry.get("status")
            if isinstance(status, Mapping):
                status_text = status.get("status_str")
                if status.get("completed") is True and status_text in {"success", "completed"}:
                    return {"status": "completed", "outputs": _extract_outputs(entry.get("outputs")), "error": None}
                if status_text in {"error", "failed"} or status.get("completed") is True:
                    return {"status": "failed", "outputs": [], "error": _history_error(status)}
        try:
            queue = self._get_json("/queue")
        except ComfyUIClientError:
            return _unknown_poll_result("无法获取 ComfyUI 执行状态。")
        if _queue_contains(queue, "queue_running", prompt_id):
            return {"status": "running", "outputs": [], "error": None}
        if _queue_contains(queue, "queue_pending", prompt_id):
            return {"status": "queued", "outputs": [], "error": None}
        return _unknown_poll_result("ComfyUI 尚未报告此执行状态。")

    def fetch_output(self, output: Mapping[str, str]) -> bytes:
        filename, subfolder, output_type = _validate_output(output)
        params = {"filename": filename, "subfolder": subfolder, "type": output_type}
        try:
            with self._client.stream("GET", self.base_url + "/view", params=params, follow_redirects=False) as response:
                if response.is_redirect or not 200 <= response.status_code < 300:
                    raise ComfyUIClientError(f"ComfyUI 请求失败（HTTP {response.status_code}）。", status_code=response.status_code)
                content_length = response.headers.get("content-length")
                if content_length is not None and content_length.isdigit() and int(content_length) > _MAX_OUTPUT_BYTES:
                    raise ComfyUIClientError("ComfyUI 输出文件超过大小限制。")
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > _MAX_OUTPUT_BYTES:
                        raise ComfyUIClientError("ComfyUI 输出文件超过大小限制。")
                    content.extend(chunk)
                return bytes(content)
        except httpx.RequestError as error:
            raise ComfyUIClientError("网络请求失败。") from error

    def _get_json(self, path: str) -> Any:
        return _decode_json(self._request("GET", path))

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.request(method, self.base_url + path, follow_redirects=False, **kwargs)
        except httpx.RequestError as error:
            raise ComfyUIClientError("网络请求失败。") from error
        if response.is_redirect or not 200 <= response.status_code < 300:
            response.close()
            raise ComfyUIClientError(f"ComfyUI 请求失败（HTTP {response.status_code}）。", status_code=response.status_code)
        return response


def _decode_json(response: httpx.Response) -> Any:
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise ComfyUIClientError("ComfyUI 响应超过大小限制。")
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise ComfyUIClientError("ComfyUI 返回了无效 JSON。") from error


def _version(system_stats: Any) -> Optional[str]:
    system = system_stats.get("system") if isinstance(system_stats, Mapping) else None
    version = system.get("comfyui_version") if isinstance(system, Mapping) else None
    return version if isinstance(version, str) else None


def _collect_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return set().union(*(_collect_strings(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_collect_strings(item) for item in value)) if value else set()
    return set()


def _queue_contains(queue: Any, key: str, prompt_id: str) -> bool:
    entries = queue.get(key, []) if isinstance(queue, Mapping) else []
    return isinstance(entries, list) and any(
        isinstance(entry, list) and len(entry) > 1 and entry[1] == prompt_id for entry in entries
    )


def _unknown_poll_result(message: str) -> dict:
    return {"status": "unknown", "outputs": [], "error": message}


def _history_error(status: Mapping[str, Any]) -> str:
    return "ComfyUI 工作流执行失败。"


def _extract_outputs(outputs: Any) -> list[dict]:
    found = []
    if not isinstance(outputs, Mapping):
        return found
    for node_output in outputs.values():
        if not isinstance(node_output, Mapping):
            continue
        for values in node_output.values():
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, Mapping):
                    try:
                        filename, subfolder, output_type = _validate_output(value)
                    except ComfyUIClientError:
                        continue
                    found.append({"filename": filename, "subfolder": subfolder, "type": output_type})
    return found


def _validate_output(output: Mapping[str, str]) -> tuple[str, str, str]:
    if not isinstance(output, Mapping):
        raise ComfyUIClientError("ComfyUI 输出引用无效。")
    filename, subfolder, output_type = output.get("filename"), output.get("subfolder", ""), output.get("type")
    if not isinstance(filename, str) or not isinstance(subfolder, str) or output_type not in _ALLOWED_OUTPUT_TYPES:
        raise ComfyUIClientError("ComfyUI 输出引用无效。")
    try:
        _validate_leaf_name(filename)
        _validate_subfolder(subfolder)
    except ValueError as error:
        raise ComfyUIClientError("ComfyUI 输出引用无效。") from error
    return filename, subfolder, output_type


def _validate_leaf_name(value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("文件名无效。")


def _validate_subfolder(value: str) -> None:
    if value and (value.startswith(("/", "\\")) or any(
        part in {"", ".", ".."} for part in value.replace("\\", "/").split("/")
    )):
        raise ValueError("子目录无效。")


def _validate_prompt_id(value: str) -> None:
    if not isinstance(value, str) or not value or "/" in value or "\\" in value or ".." in value:
        raise ComfyUIClientError("ComfyUI prompt ID 无效。")


__all__ = ["ComfyUIClient", "ComfyUIClientError", "validate_comfy_url"]
