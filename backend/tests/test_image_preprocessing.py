from pathlib import Path

from PIL import Image, ImageCms, PngImagePlugin

from app.image_preprocessing import (
    assess_image_reproducibility,
    inspect_image,
    normalize_image,
    write_analysis_proxy,
)


def _srgb_icc_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _write_oriented_transparent_source(path: Path) -> Path:
    image = Image.new("RGBA", (2, 3), (0, 0, 255, 255))
    image.putpixel((0, 0), (255, 0, 0, 255))
    image.putpixel((0, 2), (0, 0, 0, 0))
    exif = Image.Exif()
    exif[274] = 6
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source_name", "private-reference.png")
    metadata.add_text("source_path", "/private/project/private-reference.png")
    image.save(
        path,
        format="PNG",
        exif=exif,
        icc_profile=_srgb_icc_bytes(),
        pnginfo=metadata,
    )
    return path


def _write_rgb(path: Path, size: tuple[int, int]) -> Path:
    Image.new("RGB", size, (12, 34, 56)).save(path, format="PNG")
    return path


def test_inspect_reads_display_facts_after_exif_orientation_and_actual_alpha(tmp_path):
    source = _write_oriented_transparent_source(tmp_path / "private-reference.png")

    facts = inspect_image(source)

    assert facts.displaySize == (3, 2)
    assert facts.colorMode == "RGBA"
    assert facts.exifOrientation == 6
    assert facts.hasTransparency is True


def test_normalize_applies_orientation_converts_srgb_flattens_alpha_and_removes_metadata(tmp_path):
    source = _write_oriented_transparent_source(tmp_path / "private-reference.png")
    output = tmp_path / "normalized.png"

    facts = normalize_image(source, output)

    with Image.open(output) as image:
        image.load()
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (3, 2)
        assert image.size == facts.displaySize
        assert facts.transparencyFlattened is True
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((2, 0)) == (255, 0, 0)
        assert image.getexif() == {}
        assert "exif" not in image.info
        assert "icc_profile" not in image.info
        assert "source_name" not in image.info
        assert "source_path" not in image.info


def test_write_analysis_proxy_scales_long_edge_and_strips_source_metadata(tmp_path):
    source = _write_rgb(tmp_path / "normalized.png", (4096, 2048))
    output = tmp_path / "analysis-proxy.jpg"

    facts = write_analysis_proxy(source, output)

    assert output.stat().st_size == facts.byteSize
    assert 0 < facts.byteSize <= 8_000_000
    with Image.open(output) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (2048, 1024)
        assert image.size == facts.displaySize
        assert image.getexif() == {}
        assert "exif" not in image.info
        assert "icc_profile" not in image.info


def test_write_analysis_proxy_never_enlarges_a_smaller_image(tmp_path):
    source = _write_rgb(tmp_path / "normalized.png", (640, 400))
    output = tmp_path / "analysis-proxy.jpg"

    facts = write_analysis_proxy(source, output)

    assert facts.displaySize == (640, 400)
    assert output.stat().st_size == facts.byteSize
    assert facts.byteSize <= 8_000_000
    with Image.open(output) as image:
        assert image.size == (640, 400)


def test_assessment_validates_image_artifacts_and_does_not_retain_source_identity(tmp_path):
    source = _write_oriented_transparent_source(tmp_path / "private-reference.png")
    normalized = tmp_path / "normalized.png"
    proxy = tmp_path / "analysis-proxy.jpg"
    normalize_image(source, normalized)
    write_analysis_proxy(normalized, proxy)

    assessment = assess_image_reproducibility(normalized, proxy)

    assert assessment.status == "pending_semantic_confirmation"
    assert [check.criterion for check in assessment.checks] == [
        "single_shot",
        "motion_range",
        "primary_subject_count",
        "complex_interaction",
    ]
    assert all(check.status == "not_assessed" for check in assessment.checks)
    encoded = str(assessment.model_dump())
    assert source.name not in encoded
    assert str(source.parent) not in encoded
