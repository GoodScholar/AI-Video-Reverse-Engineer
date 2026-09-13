from io import BytesIO

from PIL import Image

from ..analysis_input import ImageAnalysisInput
from ..analysis_prompt import build_analysis_prompt
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
    image = connection_test_image()
    return ProviderRequest(
        analysisInput=image,
        prompt=build_analysis_prompt(image),
        model=model,
    )


__all__ = ["connection_test_image", "connection_test_request"]
