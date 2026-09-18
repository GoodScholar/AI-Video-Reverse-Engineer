import base64
from typing import Any, Optional, Union

import httpx

from ..analysis_input import ImageAnalysisInput, VideoAnalysisInput
from ..analysis_prompt import analysis_input_context
from .base import ProviderAnalysisError, ProviderFailure, ProviderRequest, ProviderResult, failure_for_status
from .http_transport import (
    ProviderHTTPTransportError,
    new_provider_http_client,
    post_provider_json,
)


def new_cancellable_client(timeout: float = 30.0) -> httpx.Client:
    """Compatibility alias for callers predating the shared transport."""

    return new_provider_http_client(timeout)


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
    payload = {
        "model": request.model,
        "messages": [{"role": "user", "content": content}],
    }
    if request.responseSchema is None:
        payload["response_format"] = {"type": "json_object"}
    else:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": request.task,
            "strict": True,
            "schema": request.responseSchema,
        }}
    return payload


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
        response = post_provider_json(client, url, headers=headers, payload=build_chat_payload(request))
    except ProviderHTTPTransportError as error:
        raise ProviderAnalysisError(ProviderFailure.for_code(error.failure_code)) from None
    if response.status_code >= 300:
        raise ProviderAnalysisError(failure_for_status(response.status_code))
    try:
        raw_text = response.json_body["choices"][0]["message"]["content"]
    except (TypeError, KeyError, IndexError):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response")) from None
    if not isinstance(raw_text, str):
        raise ProviderAnalysisError(ProviderFailure.for_code("invalid_analysis_response"))
    return ProviderResult(rawText=raw_text, providerRequestId=response.request_id)


__all__ = ["build_chat_payload", "image_data_url", "new_cancellable_client", "post_chat_completion"]
