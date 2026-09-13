import base64
import json
from typing import Any, Optional, Union

import httpx

from ..analysis_input import ImageAnalysisInput, VideoAnalysisInput
from ..analysis_prompt import analysis_input_context
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult, failure_for_status


MAX_PROVIDER_RESPONSE_BYTES = 256_000


def image_data_url(value: Union[ImageAnalysisInput, VideoAnalysisInput]) -> str:
    if isinstance(value, ImageAnalysisInput):
        image_bytes = value.analysisProxyBytes
    else:
        image_bytes = value.contactSheetBytes
    mime_type = "image/png" if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return "data:%s;base64,%s" % (mime_type, encoded)


def build_chat_payload(request: ProviderRequest) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
    if request.analysisInput is not None:
        content.append({"type": "text", "text": analysis_input_context(request.analysisInput)})
        content.append({"type": "image_url", "image_url": {"url": image_data_url(request.analysisInput)}})
    return {
        "model": request.model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }


def post_chat_completion(
    client: httpx.Client,
    url: str,
    credential: Optional[str],
    request: ProviderRequest,
    *,
    credential_required: bool = True,
) -> ProviderResult:
    if credential_required and (credential is None or not credential.strip()):
        raise ProviderAnalysisError(ProviderFailure.for_code("provider_unconfigured"))
    headers = {"Content-Type": "application/json"}
    if credential is not None and credential.strip():
        headers["Authorization"] = "Bearer " + credential
    try:
        response = client.post(
            url,
            headers=headers,
            json=build_chat_payload(request),
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        raise ProviderAnalysisError(ProviderFailure.for_code("timeout")) from None
    except httpx.RequestError:
        raise ProviderAnalysisError(ProviderFailure.for_code("network_error")) from None
    if response.status_code >= 300:
        raise ProviderAnalysisError(failure_for_status(response.status_code))
    try:
        if len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ValueError("响应超过安全边界")
        payload = response.json()
        raw_text = payload["choices"][0]["message"]["content"]
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None
    if not isinstance(raw_text, str):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response"))
    request_id = response.headers.get("x-request-id")
    return ProviderResult(rawText=raw_text, providerRequestId=request_id)


__all__ = ["build_chat_payload", "image_data_url", "post_chat_completion"]
