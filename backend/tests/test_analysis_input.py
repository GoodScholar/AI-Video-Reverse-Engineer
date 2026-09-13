from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from PIL import Image

from app.analysis_input import ImageAnalysisInput, VideoAnalysisInput, build_analysis_input
from app.local_preprocessing import ImageProxySummary, ImageSize, new_local_preprocessing
from app.local_preprocessing_runner import run_local_preprocessing
from app.main import Project
from app.reference_media import ReferenceImage
from app.reference_video import ReferenceVideo


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


def test_video_input_accepts_the_complete_validated_preprocessing_proxy(tmp_path):
    reference = ReferenceVideo(
        id="video-001",
        originalName="private-original-name.mp4",
        format="mp4",
        sizeBytes=5,
        durationSeconds=2.5,
        width=854,
        height=480,
        frameRate=24.0,
    )
    preprocessing = new_local_preprocessing(
        preprocessing_id="preprocessing-001",
        reference_media_id=reference.id,
        media_type="video",
        now=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    source = tmp_path / "private-source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "project-files/project-001/local-preprocessing/preprocessing-001"
    result = run_local_preprocessing(
        source_path=source,
        reference=reference,
        preprocessing=preprocessing,
        output_directory=output,
        ffmpeg_path="ffmpeg",
        on_stage_started=lambda _: None,
        on_stage_completed=lambda _: None,
        run=_fake_full_run,
    )
    preprocessing.status = "completed"
    preprocessing.completedAt = preprocessing.updatedAt
    preprocessing.proxySummary = result.proxy_summary
    preprocessing.reproducibilityAssessment = result.assessment
    for stage in preprocessing.stages:
        stage.status = "completed"
        stage.startedAt = preprocessing.updatedAt
        stage.completedAt = preprocessing.updatedAt
    project = Project(
        id="project-001",
        name="绝不能外发的项目名称",
        createdAt=preprocessing.updatedAt,
        updatedAt=preprocessing.updatedAt,
        referenceMedia=reference,
        localPreprocessing=preprocessing,
    )

    value = build_analysis_input(tmp_path, project)

    assert isinstance(value, VideoAnalysisInput)
    assert value.analysisProxy.source.durationSeconds == 2.5
    assert value.analysisProxy.source.width == 854
    assert value.analysisProxy.source.height == 480
    assert value.analysisProxy.source.frameRate == 24.0


def _fake_full_run(command, **kwargs):
    if command[-1] == "-filters":
        return _completed("\n".join([
            " T. scdet V->V", " T.C scale V->V", " ... metadata V->V",
            " ... drawtext V->V", " ... tile V->V",
        ]))
    if command[-1] == "-version":
        return _completed("ffmpeg version 8.1.1 Copyright")
    if "scdet=t=10" in " ".join(command):
        return _completed(
            "frame:0 pts:0 pts_time:0.000\n"
            "lavfi.scd.mafd=0.000\nlavfi.scd.score=0.000\n"
            "frame:1 pts:1 pts_time:0.125\n"
            "lavfi.scd.mafd=1.000\nlavfi.scd.score=0.000\n"
        )
    target = Path(command[-1])
    if target.suffix.lower() == ".jpg":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"jpeg")
    return _completed()


def _completed(stdout=""):
    return CompletedProcess(["ffmpeg"], 0, stdout=stdout, stderr="")
