"""固定的 ComfyUI API 工作流模板。

模板基于 Comfy-Org 的官方工作流；本地尚未完成真实 Queue 验证，因此永远
以 ``candidate`` 暴露。这里只绑定明确的输入，不推断节点或模型版本。
"""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Optional


_TEMPLATE_DIRECTORY = Path(__file__).with_name("workflow_templates")
_SUPPORTED_STRATEGIES = {"wan22_i2v", "wan22_fun_control"}


def template_info(strategy: str) -> dict:
    """返回固定模板的非敏感能力和来源信息。"""

    return deepcopy(_load_template(strategy)["info"])


def build_workflow(
    strategy: str,
    positive: str,
    negative: str,
    width: int,
    height: int,
    frames: int,
    fps: int,
    seed: int,
    input_image: str,
    depth_video: Optional[str] = None,
) -> dict:
    """绑定固定的候选模板，返回可提交给 ``/prompt`` 的 API 图。"""

    _validate_inputs(strategy, positive, negative, width, height, frames, fps, seed, input_image, depth_video)
    replacements = {
        "$positive": positive,
        "$negative": negative,
        "$width": width,
        "$height": height,
        "$frames": frames,
        "$fps": fps,
        "$seed": seed,
        "$input_image": input_image,
        "$depth_video": depth_video,
    }
    return _bind(_load_template(strategy)["workflow"], replacements)


def _load_template(strategy: str) -> dict:
    if strategy not in _SUPPORTED_STRATEGIES:
        raise ValueError("不支持的 ComfyUI 工作流策略。")
    path = _TEMPLATE_DIRECTORY / f"{strategy}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("固定 ComfyUI 模板无效。") from error
    if not isinstance(data, dict) or data.get("info", {}).get("strategy") != strategy:
        raise ValueError("固定 ComfyUI 模板无效。")
    return data


def _validate_inputs(
    strategy: str,
    positive: str,
    negative: str,
    width: int,
    height: int,
    frames: int,
    fps: int,
    seed: int,
    input_image: str,
    depth_video: Optional[str],
) -> None:
    if strategy not in _SUPPORTED_STRATEGIES:
        raise ValueError("不支持的 ComfyUI 工作流策略。")
    if not isinstance(positive, str) or not positive.strip() or not isinstance(negative, str):
        raise ValueError("提示词无效。")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (width, height, frames, fps, seed)):
        raise ValueError("工作流参数必须是整数。")
    if width < 16 or height < 16 or width % 16 or height % 16:
        raise ValueError("工作流宽高必须是 16 的倍数。")
    if frames < 17 or frames > 161 or frames % 4 != 1:
        raise ValueError("工作流帧数必须在 17 到 161 之间且符合 4n+1。")
    if fps < 8 or fps > 24:
        raise ValueError("工作流帧率必须在 8 到 24 FPS 之间。")
    if seed < 0:
        raise ValueError("随机种子必须为非负整数。")
    _validate_comfy_input_name(input_image)
    if strategy == "wan22_fun_control":
        if depth_video is None:
            raise ValueError("Wan2.2 Fun Control 必须提供深度控制素材。")
        _validate_comfy_input_name(depth_video)
    elif depth_video is not None:
        raise ValueError("Wan2.2 I2V 模板不接受深度控制素材。")


def _validate_comfy_input_name(value: object) -> None:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")):
        raise ValueError("ComfyUI 输入文件名无效。")
    normalized = value.replace("\\", "/")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("ComfyUI 输入文件名无效。")


def _bind(value: Any, replacements: dict) -> Any:
    if isinstance(value, str) and value in replacements:
        return replacements[value]
    if isinstance(value, list):
        return [_bind(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _bind(item, replacements) for key, item in value.items()}
    return value


__all__ = ["build_workflow", "template_info"]
