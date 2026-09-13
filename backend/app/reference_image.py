from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .reference_media import ReferenceMediaError


MAX_REFERENCE_IMAGE_BYTES = 30_000_000


@dataclass(frozen=True)
class ReferenceImageFacts:
    format: Literal["jpeg", "png", "webp"]
    width: int
    height: int
    has_transparency: bool


def probe_reference_image(path: Path, original_name: str, size_bytes: int) -> ReferenceImageFacts:
    del original_name
    if size_bytes > MAX_REFERENCE_IMAGE_BYTES:
        raise ReferenceMediaError(413, "image_too_large", "参考图片实测超过 30,000,000 字节。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as header:
                _reject_obviously_oversized_dimensions(*header.size)
            with Image.open(path) as verified:
                verified.verify()
            with Image.open(path) as image:
                image_format = _image_format(image.format)
                if getattr(image, "n_frames", 1) > 1:
                    raise ReferenceMediaError(422, "animated_image_unsupported", "暂不支持动画图片。")
                display = ImageOps.exif_transpose(image)
                try:
                    display.load()
                    width, height = display.size
                    _validate_dimensions(width, height)
                    return ReferenceImageFacts(
                        format=image_format,
                        width=width,
                        height=height,
                        has_transparency=_has_actual_transparency(display),
                    )
                finally:
                    if display is not image:
                        display.close()
    except ReferenceMediaError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ReferenceMediaError(
            422,
            "image_resolution_too_large",
            "参考图片宽高最大为 5760 像素。",
        ) from error
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError) as error:
        raise ReferenceMediaError(
            422,
            "image_unreadable",
            "参考图片无法读取有效图片数据。",
        ) from error


def _image_format(value: object) -> Literal["jpeg", "png", "webp"]:
    formats = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}
    if value in formats:
        return formats[value]
    raise ReferenceMediaError(415, "unsupported_image_format", "仅支持 JPG、PNG 或 WebP 图片。")


def _validate_dimensions(width: int, height: int) -> None:
    if min(width, height) < 256:
        raise ReferenceMediaError(422, "image_resolution_too_small", "参考图片宽高至少需要 256 像素。")
    if max(width, height) > 5760:
        raise ReferenceMediaError(422, "image_resolution_too_large", "参考图片宽高最大为 5760 像素。")
    if width * 5 < height * 2 or width * 2 > height * 5:
        raise ReferenceMediaError(422, "image_aspect_ratio_unsupported", "参考图片宽高比必须在 2:5 至 5:2 之间。")


def _reject_obviously_oversized_dimensions(width: int, height: int) -> None:
    if max(width, height) > 5760:
        raise ReferenceMediaError(422, "image_resolution_too_large", "参考图片宽高最大为 5760 像素。")


def _has_actual_transparency(image: Image.Image) -> bool:
    converted = image.convert("RGBA")
    try:
        low, _ = converted.getchannel("A").getextrema()
        return low < 255
    finally:
        converted.close()
