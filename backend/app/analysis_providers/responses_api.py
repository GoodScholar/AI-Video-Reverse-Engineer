"""Protocol helpers shared by the fixed-endpoint Responses API adapters."""

from typing import Any, Optional

from ..analysis_prompt import analysis_input_context, structured_analysis_schema
from ..semantic_analysis import StructuredVisualAnalysis
from .base import ProviderRequest, ProviderResult
from .openai_compatible_chat import image_data_url


def build_responses_payload(request: ProviderRequest) -> dict[str, Any]:
    """Encode the one allowed media proxy as a stateless Responses request."""

    content: list[dict[str, Any]] = [{"type": "input_text", "text": request.prompt}]
    if request.analysisInput is not None:
        content.extend([
            {"type": "input_text", "text": analysis_input_context(request.analysisInput)},
            {"type": "input_image", "image_url": image_data_url(request.analysisInput)},
        ])
        schema = structured_analysis_schema(request.analysisInput.mediaType)
    else:
        schema = StructuredVisualAnalysis.model_json_schema()
    return {
        "model": request.model,
        "store": False,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {
            "type": "json_schema",
            "name": "structured_analysis",
            "strict": True,
            "schema": schema,
        }},
    }


def result_from_responses_body(body: Any, request_id: Optional[str]) -> ProviderResult:
    """Extract completed message text without accepting refusals or partial output."""

    if not isinstance(body, dict):
        raise ValueError("Responses 响应不是对象")
    if body.get("status") not in (None, "completed") or body.get("incomplete_details") is not None:
        raise ValueError("Responses 响应未完成")
    output = body.get("output")
    if not isinstance(output, list):
        raise ValueError("Responses 响应缺少输出")
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("Responses 输出项无效")
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise ValueError("Responses 消息内容无效")
        for block in content:
            if not isinstance(block, dict):
                raise ValueError("Responses 内容块无效")
            if block.get("type") == "refusal":
                raise ValueError("Responses 拒绝了请求")
            if block.get("type") == "output_text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise ValueError("Responses 文本无效")
                chunks.append(text)
    raw_text = "".join(chunks)
    if not raw_text.strip():
        raise ValueError("Responses 响应没有文本")
    return ProviderResult(rawText=raw_text, providerRequestId=request_id)


__all__ = ["build_responses_payload", "result_from_responses_body"]
