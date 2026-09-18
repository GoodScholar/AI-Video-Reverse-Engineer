from app.analysis_providers.base import ProviderRequest
from app.analysis_providers.claude import _messages_payload
from app.analysis_providers.gemini import _generate_content_payload
from app.analysis_providers.openai_compatible_chat import build_chat_payload
from app.analysis_providers.responses_api import build_responses_payload
from app.prompt_generation import PromptTexts


def _request() -> ProviderRequest:
    return ProviderRequest(
        task="prompt_generation",
        prompt="只根据结构化分析生成四段提示词。",
        model="test-model",
        responseSchema=PromptTexts.model_json_schema(),
    )


def test_all_provider_wire_formats_send_a_text_only_prompt_generation_request_with_its_schema():
    request = _request()

    chat = build_chat_payload(request)
    responses = build_responses_payload(request)
    gemini = _generate_content_payload(request)
    claude = _messages_payload(request)

    assert chat["messages"][0]["content"] == [{"type": "text", "text": request.prompt}]
    assert chat["response_format"]["json_schema"]["schema"] == request.responseSchema
    assert responses["input"][0]["content"] == [{"type": "input_text", "text": request.prompt}]
    assert responses["text"]["format"]["schema"] == request.responseSchema
    assert gemini["contents"][0]["parts"] == [{"text": request.prompt}]
    assert set(gemini["generationConfig"]["responseJsonSchema"]["properties"]) == set(
        request.responseSchema["properties"],
    )
    assert claude["messages"][0]["content"] == [{"type": "text", "text": request.prompt}]
    assert set(claude["output_config"]["format"]["schema"]["properties"]) == set(
        request.responseSchema["properties"],
    )
