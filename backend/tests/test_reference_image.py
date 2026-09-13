from io import BytesIO

import pytest
from PIL import Image

from app.reference_media import ReferenceMediaError
from app.reference_image import probe_reference_image


def write_image(path, image, *, format, **kwargs):
    image.save(path, format=format, **kwargs)
    return path


def minimal_png(width, height):
    import struct
    import zlib

    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")


def png_with_corrupted_idat_crc() -> bytes:
    output = BytesIO()
    Image.new("RGB", (256, 256), "red").save(output, format="PNG")
    contents = bytearray(output.getvalue())
    idat = contents.index(b"IDAT")
    crc_start = idat + 4 + int.from_bytes(contents[idat - 4:idat], "big")
    contents[crc_start] ^= 0x01
    return bytes(contents)


@pytest.mark.parametrize(
    ("suffix", "image_format", "expected_format"),
    [
        (".jpg", "JPEG", "jpeg"),
        (".jpeg", "JPEG", "jpeg"),
        (".png", "PNG", "png"),
        (".webp", "WEBP", "webp"),
    ],
)
def test_probe_normalizes_supported_real_image_formats(tmp_path, suffix, image_format, expected_format):
    path = write_image(tmp_path / f"reference{suffix}", Image.new("RGB", (256, 256), "red"), format=image_format)

    facts = probe_reference_image(path, f"spoofed-name.bin{suffix}", path.stat().st_size)

    assert facts.format == expected_format
    assert (facts.width, facts.height) == (256, 256)
    assert facts.has_transparency is False


def test_probe_reports_only_actual_transparent_pixels(tmp_path):
    opaque = write_image(tmp_path / "opaque.png", Image.new("RGBA", (256, 256), (0, 0, 0, 255)), format="PNG")
    transparent = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
    transparent.putpixel((0, 0), (0, 0, 0, 0))
    transparent_path = write_image(tmp_path / "transparent.png", transparent, format="PNG")

    opaque_facts = probe_reference_image(opaque, "opaque.jpg", opaque.stat().st_size)
    transparent_facts = probe_reference_image(transparent_path, "transparent.jpg", transparent_path.stat().st_size)

    assert opaque_facts.has_transparency is False
    assert transparent_facts.has_transparency is True


def test_probe_rejects_animated_webp(tmp_path):
    first = Image.new("RGB", (256, 256), "red")
    second = Image.new("RGB", (256, 256), "blue")
    path = write_image(tmp_path / "animated.webm", first, format="WEBP", save_all=True, append_images=[second], duration=100, loop=0)

    with pytest.raises(ReferenceMediaError) as captured:
        probe_reference_image(path, "looks-like-video.mov", path.stat().st_size)

    assert captured.value.code == "animated_image_unsupported"


@pytest.mark.parametrize(
    ("contents", "original_name", "expected_code"),
    [
        (b"not an image", "photo.png", "image_unreadable"),
        (b"not an image", "photo.mp4", "image_unreadable"),
    ],
)
def test_probe_rejects_unreadable_content_regardless_of_name(tmp_path, contents, original_name, expected_code):
    path = tmp_path / "payload"
    path.write_bytes(contents)

    with pytest.raises(ReferenceMediaError) as captured:
        probe_reference_image(path, original_name, len(contents))

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("size_bytes", "dimensions", "expected_code"),
    [
        (30_000_001, (256, 256), "image_too_large"),
        (1, (255, 256), "image_resolution_too_small"),
        (1, (5761, 256), "image_resolution_too_large"),
        (1, (256, 641), "image_aspect_ratio_unsupported"),
        (1, (641, 256), "image_aspect_ratio_unsupported"),
    ],
)
def test_probe_enforces_size_dimension_and_ratio_limits(tmp_path, size_bytes, dimensions, expected_code):
    path = write_image(tmp_path / "image.png", Image.new("RGB", dimensions, "red"), format="PNG")

    with pytest.raises(ReferenceMediaError) as captured:
        probe_reference_image(path, "photo.txt", size_bytes)

    assert captured.value.code == expected_code


@pytest.mark.parametrize("dimensions", [(256, 640), (640, 256)])
def test_probe_accepts_inclusive_aspect_ratio_endpoints(tmp_path, dimensions):
    path = write_image(tmp_path / "edge.png", Image.new("RGB", dimensions, "red"), format="PNG")

    facts = probe_reference_image(path, "edge.bin", path.stat().st_size)

    assert (facts.width, facts.height) == dimensions


def test_probe_applies_exif_orientation_to_dimensions(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    path = write_image(tmp_path / "rotated.jpg", Image.new("RGB", (256, 512), "red"), format="JPEG", exif=exif)

    facts = probe_reference_image(path, "rotated.jpg", path.stat().st_size)

    assert (facts.width, facts.height) == (512, 256)


def test_oversized_raw_dimensions_are_rejected_before_full_transpose_or_load(tmp_path, monkeypatch):
    path = tmp_path / "oversized.png"
    path.write_bytes(minimal_png(20_000, 20_000))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", None)

    def transpose_must_not_run(*args, **kwargs):
        raise AssertionError("超大图片不应进入完整转置或解码")

    monkeypatch.setattr("app.reference_image.ImageOps.exif_transpose", transpose_must_not_run)
    with pytest.raises(ReferenceMediaError) as captured:
        probe_reference_image(path, "oversized.png", path.stat().st_size)

    assert captured.value.code == "image_resolution_too_large"


def test_decompression_bomb_error_is_a_stable_image_validation_error(tmp_path, monkeypatch):
    path = write_image(tmp_path / "valid.png", Image.new("RGB", (256, 256), "red"), format="PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)

    with pytest.raises(ReferenceMediaError) as captured:
        probe_reference_image(path, "valid.png", path.stat().st_size)

    assert captured.value.code == "image_resolution_too_large"


def test_probe_maps_a_real_png_idat_crc_failure_to_image_unreadable(tmp_path):
    path = tmp_path / "corrupted.png"
    path.write_bytes(png_with_corrupted_idat_crc())

    with pytest.raises(ReferenceMediaError) as captured:
        probe_reference_image(path, "corrupted.png", path.stat().st_size)

    assert captured.value.code == "image_unreadable"
