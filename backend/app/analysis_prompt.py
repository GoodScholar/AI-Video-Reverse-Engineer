import json
from typing import Literal, Union

from .analysis_input import AnalysisInput, ImageAnalysisInput, VideoAnalysisInput


PROMPT_VERSION = 1


def build_analysis_prompt(media_type: Union[Literal["image", "video"], AnalysisInput]) -> str:
    """Build the versioned instruction without embedding local identifiers."""

    if not isinstance(media_type, str):
        media_type = media_type.mediaType
    if media_type == "image":
        temporal_rule = (
            "这是参考图片：observedFacts.temporal 必须为 null。不得把动作、环境动态、"
            "运镜、节奏或音频写成可观察事实；它们只能作为 generationSuggestions。"
        )
    elif media_type == "video":
        temporal_rule = (
            "这是参考视频的分析代理：只有联系表与代理 JSON 直接支持的动作、环境动态、"
            "运镜和节奏，才可以写入 observedFacts.temporal。音频仍只能作为生成建议。"
        )
    else:
        raise ValueError("不支持的媒体类型")
    return (
        "你是结构化视觉分析器。仅根据提供的分析代理回答，不得把猜测、文件来源、"
        "项目资料或本地信息描述为事实。可观察事实必须写入 observedFacts，生成策略"
        "必须写入 generationSuggestions。"
        + temporal_rule
        + "只返回一个符合约定结构的 JSON 对象，不要使用 Markdown、解释或代码围栏。"
    )


def analysis_input_context(value: AnalysisInput) -> str:
    """Return the non-binary, explicitly allowed companion data for a request."""

    if isinstance(value, ImageAnalysisInput):
        context = {
            "mediaType": "image",
            "width": value.width,
            "height": value.height,
            "aspectRatio": value.aspectRatio,
        }
    elif isinstance(value, VideoAnalysisInput):
        context = {
            "mediaType": "video",
            "analysisProxy": value.analysisProxy.model_dump(mode="json"),
        }
    else:
        raise ValueError("不支持的媒体类型")
    return json.dumps(context, ensure_ascii=False, separators=(",", ":"))


def repair_prompt(candidate_json: str, error_summary: str) -> str:
    return (
        "下方候选 JSON 不符合结构化契约。仅修复其 JSON 结构和缺失字段；"
        "不要加入解释、Markdown 或任何未提供的媒体内容。只返回修复后的 JSON 对象。\n"
        "候选 JSON：\n"
        + candidate_json
        + "\n校验错误摘要：\n"
        + error_summary
    )


__all__ = ["PROMPT_VERSION", "analysis_input_context", "build_analysis_prompt", "repair_prompt"]
