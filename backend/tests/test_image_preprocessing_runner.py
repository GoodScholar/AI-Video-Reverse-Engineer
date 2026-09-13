import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from app.image_preprocessing_runner import run_image_preprocessing
from app.local_preprocessing import new_local_preprocessing
from app.local_preprocessing_runner import LocalPreprocessingFailure
from app.local_preprocessing_storage import preprocessing_directory
from app.reference_media import ReferenceImage


def reference() -> ReferenceImage:
    return ReferenceImage(
        id="image-001", originalName="private-reference.png", format="png", sizeBytes=1,
        width=256, height=256, hasTransparency=False,
    )


def preprocessing():
    return new_local_preprocessing(
        preprocessing_id="prep-001", reference_media_id="image-001", media_type="image",
        now=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )


def source_image(path: Path) -> Path:
    Image.new("RGB", (256, 256), (12, 34, 56)).save(path, format="PNG")
    return path


def test_image_runner_commits_valid_artifacts_before_marking_each_stage_completed(tmp_path):
    source = source_image(tmp_path / "private-reference.png")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    started = []
    completed = []

    result = run_image_preprocessing(
        source_path=source, reference=reference(), preprocessing=preprocessing(),
        output_directory=output, on_stage_started=started.append,
        on_stage_completed=completed.append,
    )

    assert started == completed == [
        "imageDecoding", "imageNormalization", "proxyGeneration", "reproducibilityAssessment",
    ]
    assert result.proxy_summary.mediaType == "image"
    assert result.assessment.status == "pending_semantic_confirmation"
    assert (output / "normalized.png").is_file()
    assert (output / "analysis-proxy.jpg").is_file()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mediaType"] == "image"
    assert manifest["sourceReferenceMediaId"] == "image-001"
    assert manifest["artifacts"] == {
        "normalized": "normalized.png", "analysisProxy": "analysis-proxy.jpg",
    }
    encoded = json.dumps(manifest, ensure_ascii=False)
    assert source.name not in encoded
    assert str(source.parent) not in encoded
    assert "project-001" not in encoded


def test_image_runner_persists_actual_sizes_transparency_and_applicability_facts(tmp_path):
    source = tmp_path / "private-reference.png"
    Image.new("RGBA", (4096, 2049), (12, 34, 56, 0)).save(source, format="PNG")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")

    result = run_image_preprocessing(
        source_path=source, reference=reference(), preprocessing=preprocessing(),
        output_directory=output, on_stage_started=lambda _: None,
        on_stage_completed=lambda _: None,
    )

    assert result.proxy_summary.model_dump() == {
        "mediaType": "image",
        "originalDisplaySize": {"width": 4096, "height": 2049},
        "normalizedSize": {"width": 4096, "height": 2049},
        "proxySize": {"width": 2048, "height": 1024},
        "transparencyFlattened": True,
        "applicabilityStatus": "pending_semantic_confirmation",
    }
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["proxySummary"] == result.proxy_summary.model_dump()


def test_image_runner_rewinds_from_first_invalid_completed_artifact(tmp_path):
    source = source_image(tmp_path / "private-reference.png")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")
    task = preprocessing()
    for state in task.stages:
        state.status = "completed"
    output.mkdir(parents=True)
    (output / "normalized.png").write_bytes(b"not a png")
    (output / "analysis-proxy.jpg").write_bytes(b"not a jpeg")
    (output / "manifest.json").write_text(json.dumps({
        "schemaVersion": 1, "algorithmVersion": 1, "mediaType": "image",
        "sourceReferenceMediaId": "image-001",
    }), encoding="utf-8")
    started = []

    run_image_preprocessing(
        source_path=source, reference=reference(), preprocessing=task,
        output_directory=output, on_stage_started=started.append,
        on_stage_completed=lambda _: None,
    )

    assert started == ["imageNormalization", "proxyGeneration", "reproducibilityAssessment"]


def test_image_runner_maps_decode_error_without_disclosing_source_path(tmp_path):
    source = tmp_path / "private-reference.png"
    source.write_bytes(b"not an image")
    output = preprocessing_directory(tmp_path, "project-001", "prep-001")

    with pytest.raises(LocalPreprocessingFailure) as captured:
        run_image_preprocessing(
            source_path=source, reference=reference(), preprocessing=preprocessing(),
            output_directory=output, on_stage_started=lambda _: None,
            on_stage_completed=lambda _: None,
        )

    assert (captured.value.code, captured.value.stage) == ("image_decode_failed", "imageDecoding")
    assert str(source) not in captured.value.message
