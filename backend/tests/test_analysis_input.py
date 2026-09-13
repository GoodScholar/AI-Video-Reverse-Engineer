from datetime import datetime, timezone

import pytest
from PIL import Image

from app.analysis_input import ImageAnalysisInput, build_analysis_input
from app.local_preprocessing import ImageProxySummary, ImageSize, new_local_preprocessing
from app.main import Project
from app.reference_media import ReferenceImage


def completed_image_project(tmp_path):
    reference = ReferenceImage(
        id="image-001",
        originalName="private-original-name.png",
        format="png",
        sizeBytes=1024,
        width=256,
        height=256,
        hasTransparency=False,
    )
    preprocessing = new_local_preprocessing(
        preprocessing_id="preprocessing-001",
        reference_media_id=reference.id,
        media_type="image",
        now=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    preprocessing.status = "completed"
    preprocessing.currentStage = None
    preprocessing.completedAt = preprocessing.updatedAt
    for stage in preprocessing.stages:
        stage.status = "completed"
        stage.startedAt = preprocessing.updatedAt
        stage.completedAt = preprocessing.updatedAt
    preprocessing.proxySummary = ImageProxySummary(
        originalDisplaySize=ImageSize(width=256, height=256),
        normalizedSize=ImageSize(width=256, height=256),
        proxySize=ImageSize(width=256, height=256),
        transparencyFlattened=False,
        applicabilityStatus="pending_semantic_confirmation",
    )
    project = Project(
        id="project-001",
        name="绝不能外发的项目名称",
        createdAt=datetime(2026, 9, 13, tzinfo=timezone.utc).isoformat(),
        updatedAt=datetime(2026, 9, 13, tzinfo=timezone.utc).isoformat(),
        referenceMedia=reference,
        localPreprocessing=preprocessing,
    )
    artifacts = (
        tmp_path / "project-files" / project.id / "local-preprocessing" / preprocessing.id
    )
    artifacts.mkdir(parents=True)
    Image.new("RGB", (256, 256), (12, 34, 56)).save(artifacts / "normalized.png", format="PNG")
    Image.new("RGB", (256, 256), (12, 34, 56)).save(artifacts / "analysis-proxy.jpg", format="JPEG")
    (artifacts / "manifest.json").write_text(
        """{
          "schemaVersion": 1,
          "algorithmVersion": 1,
          "mediaType": "image",
          "sourceReferenceMediaId": "image-001",
          "proxySummary": {
            "mediaType": "image",
            "originalDisplaySize": {"width": 256, "height": 256},
            "normalizedSize": {"width": 256, "height": 256},
            "proxySize": {"width": 256, "height": 256},
            "transparencyFlattened": false,
            "applicabilityStatus": "pending_semantic_confirmation"
          },
          "reproducibilityAssessment": {
            "status": "pending_semantic_confirmation",
            "checks": []
          }
        }""",
        encoding="utf-8",
    )
    return project


def test_image_input_contains_only_proxy_and_dimensions(tmp_path):
    project = completed_image_project(tmp_path)

    value = build_analysis_input(tmp_path, project)
    serialized = value.model_dump_json()

    assert isinstance(value, ImageAnalysisInput)
    assert value.width == 256
    assert value.height == 256
    assert value.aspectRatio == 1.0
    assert "绝不能外发的项目名称" not in serialized
    assert "private-original-name.png" not in serialized
    assert str(tmp_path) not in serialized
    assert "originalName" not in serialized


def test_analysis_input_rejects_unfinished_preprocessing(tmp_path):
    project = completed_image_project(tmp_path)
    project.localPreprocessing.status = "running"

    with pytest.raises(ValueError, match="本地预处理尚未完成"):
        build_analysis_input(tmp_path, project)


def test_analysis_input_rejects_corrupted_completed_artifact(tmp_path):
    project = completed_image_project(tmp_path)
    artifact = (
        tmp_path / "project-files" / project.id / "local-preprocessing"
        / project.localPreprocessing.id / "analysis-proxy.jpg"
    )
    artifact.unlink()

    with pytest.raises(ValueError, match="本地预处理产物未通过校验"):
        build_analysis_input(tmp_path, project)


def test_image_input_uses_dimensions_from_validated_proxy_not_project_summary(tmp_path):
    project = completed_image_project(tmp_path)
    project.localPreprocessing.proxySummary.proxySize = ImageSize(width=512, height=512)

    value = build_analysis_input(tmp_path, project)

    assert value.width == 256
    assert value.height == 256
