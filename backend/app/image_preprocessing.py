import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .local_preprocessing import ReproducibilityAssessment, ReproducibilityCheck


ANALYSIS_PROXY_LONG_EDGE = 2048
MAX_ANALYSIS_PROXY_BYTES = 8_000_000
_JPEG_QUALITIES = (90, 85, 80, 75, 70, 65, 60)


class ImagePreprocessingFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DecodedImageFacts:
    displaySize: tuple[int, int]
    colorMode: str
    exifOrientation: Optional[int]
    hasTransparency: bool


@dataclass(frozen=True)
class NormalizedImageFacts:
    displaySize: tuple[int, int]
    transparencyFlattened: bool


@dataclass(frozen=True)
class ProxyImageFacts:
    displaySize: tuple[int, int]
    byteSize: int


def inspect_image(path: Path) -> DecodedImageFacts:
    try:
        with Image.open(path) as image:
            _ensure_single_frame(image)
            orientation = _exif_orientation(image)
            display = ImageOps.exif_transpose(image)
            try:
                display.load()
                return DecodedImageFacts(
                    displaySize=display.size,
                    colorMode=display.mode,
                    exifOrientation=orientation,
                    hasTransparency=_has_actual_transparency(display),
                )
            finally:
                if display is not image:
                    display.close()
    except ImagePreprocessingFailure:
        raise
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError) as error:
        raise ImagePreprocessingFailure(
            "image_decode_failed", "参考图片无法完成本地解码。",
        ) from error


def normalize_image(source: Path, destination: Path) -> NormalizedImageFacts:
    try:
        with Image.open(source) as image:
            _ensure_single_frame(image)
            display = ImageOps.exif_transpose(image)
            try:
                display.load()
                normalized = _convert_to_srgb(display)
            finally:
                if display is not image:
                    display.close()
        try:
            transparency_flattened = _has_actual_transparency(normalized)
            output = _flatten_to_rgb(normalized, transparency_flattened)
        finally:
            normalized.close()
        try:
            output.info.clear()
            output.save(destination, format="PNG")
            return NormalizedImageFacts(
                displaySize=output.size,
                transparencyFlattened=transparency_flattened,
            )
        finally:
            output.close()
    except ImagePreprocessingFailure:
        raise
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError) as error:
        raise ImagePreprocessingFailure(
            "image_normalization_failed", "参考图片标准化失败，请重试。",
        ) from error


def write_analysis_proxy(source: Path, destination: Path) -> ProxyImageFacts:
    candidate: Optional[Path] = None
    try:
        with Image.open(source) as image:
            _ensure_single_frame(image)
            image.load()
            proxy = image.convert("RGB")
        try:
            proxy.info.clear()
            resized = _resize_for_proxy(proxy)
        finally:
            proxy.close()
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".jpg",
                delete=False,
            ) as handle:
                candidate = Path(handle.name)
            for quality in _JPEG_QUALITIES:
                resized.save(candidate, format="JPEG", quality=quality, optimize=True)
                if candidate.stat().st_size <= MAX_ANALYSIS_PROXY_BYTES:
                    byte_size = candidate.stat().st_size
                    os.replace(candidate, destination)
                    candidate = None
                    return ProxyImageFacts(displaySize=resized.size, byteSize=byte_size)
        finally:
            resized.close()
    except ImagePreprocessingFailure:
        raise
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError) as error:
        raise ImagePreprocessingFailure(
            "proxy_generation_failed", "分析代理无法压缩到安全上限。",
        ) from error
    finally:
        if candidate is not None:
            candidate.unlink(missing_ok=True)
    raise ImagePreprocessingFailure(
        "proxy_generation_failed", "分析代理无法压缩到安全上限。",
    )


def assess_image_reproducibility(
    normalized_path: Path,
    proxy_path: Path,
) -> ReproducibilityAssessment:
    try:
        _validate_normalized_artifact(normalized_path)
        _validate_proxy_artifact(proxy_path)
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError) as error:
        raise ImagePreprocessingFailure(
            "assessment_failed", "图片预处理产物无法完成适用性检查。",
        ) from error
    return ReproducibilityAssessment(
        status="pending_semantic_confirmation",
        checks=[
            ReproducibilityCheck(
                criterion="single_shot",
                status="not_assessed",
                message="静态参考图片不包含时间序列，无法评估镜头切换。",
                evidence="本地预处理只读取单张静态图片。",
            ),
            ReproducibilityCheck(
                criterion="motion_range",
                status="not_assessed",
                message="静态参考图片不包含时间序列，无法评估运动范围。",
                evidence="本地预处理不从单张图片推断运动。",
            ),
            ReproducibilityCheck(
                criterion="primary_subject_count",
                status="not_assessed",
                message="主要主体数量待语义分析确认。",
                evidence="本地预处理不执行主体识别。",
            ),
            ReproducibilityCheck(
                criterion="complex_interaction",
                status="not_assessed",
                message="复杂交互待语义分析确认。",
                evidence="本地预处理不执行交互识别。",
            ),
        ],
    )


def _ensure_single_frame(image: Image.Image) -> None:
    if getattr(image, "n_frames", 1) != 1:
        raise ImagePreprocessingFailure(
            "image_decode_failed", "参考图片必须只包含一个静态帧。",
        )


def _exif_orientation(image: Image.Image) -> Optional[int]:
    orientation = image.getexif().get(274)
    return orientation if isinstance(orientation, int) else None


def _has_actual_transparency(image: Image.Image) -> bool:
    rgba = image.convert("RGBA")
    try:
        alpha = rgba.getchannel("A")
        try:
            low, _ = alpha.getextrema()
            return low < 255
        finally:
            alpha.close()
    finally:
        rgba.close()


def _convert_to_srgb(image: Image.Image) -> Image.Image:
    working = image.convert("RGBA" if _has_actual_transparency(image) else "RGB")
    icc_profile = image.info.get("icc_profile")
    if not isinstance(icc_profile, bytes):
        return working
    try:
        source_profile = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
        srgb_profile = ImageCms.createProfile("sRGB")
        converted = ImageCms.profileToProfile(
            working,
            source_profile,
            srgb_profile,
            outputMode=working.mode,
        )
    except (OSError, ValueError, ImageCms.PyCMSError):
        return working
    working.close()
    return converted


def _flatten_to_rgb(image: Image.Image, has_transparency: bool) -> Image.Image:
    if not has_transparency:
        return image.convert("RGB")
    background = Image.new("RGB", image.size, (255, 255, 255))
    alpha = image.getchannel("A")
    try:
        background.paste(image, mask=alpha)
        return background
    finally:
        alpha.close()


def _resize_for_proxy(image: Image.Image) -> Image.Image:
    long_edge = max(image.size)
    if long_edge <= ANALYSIS_PROXY_LONG_EDGE:
        return image.copy()
    scale = ANALYSIS_PROXY_LONG_EDGE / long_edge
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _validate_normalized_artifact(path: Path) -> None:
    with Image.open(path) as image:
        _ensure_single_frame(image)
        image.load()
        if image.format != "PNG" or image.mode != "RGB" or image.getexif():
            raise ValueError("标准化图片无效")


def _validate_proxy_artifact(path: Path) -> None:
    if path.stat().st_size > MAX_ANALYSIS_PROXY_BYTES:
        raise ValueError("分析代理超过上限")
    with Image.open(path) as image:
        _ensure_single_frame(image)
        image.load()
        if (
            image.format != "JPEG"
            or image.mode != "RGB"
            or max(image.size) > ANALYSIS_PROXY_LONG_EDGE
            or image.getexif()
        ):
            raise ValueError("分析代理无效")
