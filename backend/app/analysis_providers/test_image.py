from io import BytesIO

from PIL import Image

from ..analysis_input import ImageAnalysisInput
from .base import ProviderRequest


def connection_test_image() -> ImageAnalysisInput:
    """Create a deterministic probe image without touching user material."""

    output = BytesIO()
    Image.new("RGB", (16, 16), (18, 52, 86)).save(output, format="PNG")
    return ImageAnalysisInput(
        analysisProxyBytes=output.getvalue(),
        width=16,
        height=16,
        aspectRatio=1.0,
    )


def connection_test_request(model: str) -> ProviderRequest:
    return ProviderRequest(
        analysisInput=connection_test_image(),
        prompt="这是一张固定的 16×16 RGB 测试图。只返回一个 JSON 对象：{\"ok\":true}。",
        model=model,
    )


__all__ = ["connection_test_image", "connection_test_request"]
