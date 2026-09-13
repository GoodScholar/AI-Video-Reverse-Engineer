from app.project_migrations import migrate_project_payload
from app.main import Project
from app.reference_media import ReferenceImage, ReferenceMedia
from app.reference_video import ReferenceVideo
from pydantic import TypeAdapter, ValidationError
import pytest


def test_migrates_legacy_reference_video_to_typed_reference_media():
    payload = {
        "id": "project-001",
        "referenceVideo": {
            "id": "video-001",
            "originalName": "clip.mp4",
            "format": "mp4",
            "sizeBytes": 12,
            "durationSeconds": 2.5,
            "width": 854,
            "height": 480,
            "frameRate": 24,
        },
    }

    migrated = migrate_project_payload(payload)

    assert migrated == {
        "id": "project-001",
        "referenceMedia": {
            "id": "video-001",
            "originalName": "clip.mp4",
            "format": "mp4",
            "sizeBytes": 12,
            "durationSeconds": 2.5,
            "width": 854,
            "height": 480,
            "frameRate": 24,
            "type": "video",
        },
    }
    assert "referenceMedia" not in payload
    assert "type" not in payload["referenceVideo"]


def test_migrates_legacy_preprocessing_source_to_typed_reference_media_source():
    payload = {
        "id": "project-001",
        "localPreprocessing": {"sourceReferenceVideoId": "video-001"},
    }

    migrated = migrate_project_payload(payload)

    assert migrated == {
        "id": "project-001",
        "localPreprocessing": {
            "sourceReferenceMediaId": "video-001",
            "mediaType": "video",
        },
    }
    assert payload["localPreprocessing"] == {"sourceReferenceVideoId": "video-001"}


def test_migrates_legacy_video_proxy_summary_to_discriminated_union_idempotently():
    preprocessing = _legacy_preprocessing("completed")
    preprocessing["proxySummary"] = {
        "keyframeCount": 4,
        "contactSheetCount": 1,
        "sceneChangeCount": 0,
        "motionP50": 1.0,
        "motionP90": 2.0,
        "motionPeak": 3.0,
        "motionLevel": "light",
    }
    payload = _legacy_project(local_preprocessing=preprocessing)

    migrated = migrate_project_payload(payload)

    assert migrated["localPreprocessing"]["proxySummary"]["mediaType"] == "video"
    assert Project.model_validate(migrated).localPreprocessing.proxySummary.mediaType == "video"
    assert migrate_project_payload(migrated) == migrated
    assert "mediaType" not in payload["localPreprocessing"]["proxySummary"]


def test_current_fields_win_while_input_remains_unchanged_and_result_is_idempotent():
    payload = {
        "referenceVideo": {"id": "video-legacy"},
        "referenceMedia": {"type": "image", "id": "image-current"},
        "localPreprocessing": {
            "sourceReferenceVideoId": "video-legacy",
            "sourceReferenceMediaId": "image-current",
            "mediaType": "image",
        },
    }

    migrated = migrate_project_payload(payload)

    assert migrated == {
        "referenceVideo": {"id": "video-legacy"},
        "referenceMedia": {"type": "image", "id": "image-current"},
        "localPreprocessing": {
            "sourceReferenceVideoId": "video-legacy",
            "sourceReferenceMediaId": "image-current",
            "mediaType": "image",
        },
    }
    assert payload == {
        "referenceVideo": {"id": "video-legacy"},
        "referenceMedia": {"type": "image", "id": "image-current"},
        "localPreprocessing": {
            "sourceReferenceVideoId": "video-legacy",
            "sourceReferenceMediaId": "image-current",
            "mediaType": "image",
        },
    }
    assert migrate_project_payload(migrated) == migrated


def test_reference_media_discriminates_image_and_video():
    image = ReferenceImage(
        id="image-001",
        originalName="frame.webp",
        format="webp",
        sizeBytes=30_000_000,
        width=256,
        height=640,
        hasTransparency=True,
    )
    video = ReferenceVideo(
        id="video-001",
        originalName="clip.mp4",
        format="mp4",
        sizeBytes=200_000_000,
        durationSeconds=2.0,
        width=854,
        height=480,
        frameRate=24,
    )

    assert image.type == "image"
    assert video.type == "video"
    assert TypeAdapter(ReferenceMedia).validate_python(image.model_dump()) == image
    assert TypeAdapter(ReferenceMedia).validate_python(video.model_dump()) == video


def test_reference_image_accepts_jpeg_at_the_smallest_dimensions_and_tallest_ratio():
    image = ReferenceImage(
        id="image-001",
        originalName="frame.jpeg",
        format="jpeg",
        sizeBytes=1,
        width=256,
        height=640,
        hasTransparency=False,
    )

    assert image.format == "jpeg"


def test_reference_image_rejects_an_unnormalized_jpg_format():
    with pytest.raises(ValidationError):
        ReferenceImage(
            id="image-001",
            originalName="frame.jpg",
            format="jpg",
            sizeBytes=1,
            width=256,
            height=640,
            hasTransparency=False,
        )


@pytest.mark.parametrize(
    ("width", "height"),
    [(256, 641), (641, 256)],
)
def test_reference_image_rejects_ratios_outside_supported_edges(width, height):
    with pytest.raises(ValidationError):
        ReferenceImage(
            id="image-001",
            originalName="frame.png",
            format="png",
            sizeBytes=1,
            width=width,
            height=height,
            hasTransparency=False,
        )


def _legacy_project(*, local_preprocessing=None):
    return {
        "id": "project-001",
        "name": "旧项目",
        "createdAt": "2026-09-10T10:00:00+00:00",
        "updatedAt": "2026-09-10T10:00:00+00:00",
        "referenceVideo": {
            "id": "video-001", "originalName": "clip.mp4", "format": "mp4",
            "sizeBytes": 12, "durationSeconds": 2.5, "width": 854,
            "height": 480, "frameRate": 24,
        },
        "localPreprocessing": local_preprocessing,
    }


def _legacy_preprocessing(status):
    stage_names = [
        "decoding", "sceneDetection", "keyframeExtraction", "motionAnalysis", "reproducibilityAssessment",
    ]
    stages = [
        {"name": name, "status": "pending", "startedAt": None, "completedAt": None}
        for name in stage_names
    ]
    if status in {"running", "failed", "completed"}:
        stages[0] = {
            "name": "decoding", "status": "completed",
            "startedAt": "2026-09-10T10:01:00+00:00", "completedAt": "2026-09-10T10:01:01+00:00",
        }
    if status == "running":
        stages[1] = {
            "name": "sceneDetection", "status": "running",
            "startedAt": "2026-09-10T10:01:01+00:00", "completedAt": None,
        }
    if status == "failed":
        stages[1] = {
            "name": "sceneDetection", "status": "failed",
            "startedAt": "2026-09-10T10:01:01+00:00", "completedAt": "2026-09-10T10:01:02+00:00",
        }
    if status == "completed":
        stages = [
            {"name": name, "status": "completed", "startedAt": "2026-09-10T10:01:00+00:00", "completedAt": "2026-09-10T10:01:01+00:00"}
            for name in stage_names
        ]
    payload = {
        "id": "preprocessing-001", "sourceReferenceVideoId": "video-001",
        "algorithmVersion": 1, "status": status,
        "currentStage": "sceneDetection" if status in {"running", "failed"} else None,
        "stages": stages,
        "queuedAt": "2026-09-10T10:00:00+00:00", "startedAt": None,
        "updatedAt": "2026-09-10T10:00:00+00:00", "completedAt": None,
        "proxySummary": None, "reproducibilityAssessment": None, "error": None,
    }
    if status == "failed":
        payload["error"] = {
            "code": "scene_detection_failed", "message": "镜头检测失败。",
            "stage": "sceneDetection", "retryable": True,
        }
    return payload


def test_empty_legacy_project_validates_without_reference_media():
    payload = _legacy_project()
    del payload["referenceVideo"]

    validated = Project.model_validate(migrate_project_payload(payload))

    assert validated.referenceMedia is None


@pytest.mark.parametrize("status", ["queued", "running", "failed", "completed"])
def test_migrates_legacy_preprocessing_for_each_status(status):
    migrated = migrate_project_payload(
        _legacy_project(local_preprocessing=_legacy_preprocessing(status))
    )

    preprocessing = migrated["localPreprocessing"]
    assert preprocessing["sourceReferenceMediaId"] == "video-001"
    assert preprocessing["mediaType"] == "video"
    assert "sourceReferenceVideoId" not in preprocessing
    assert Project.model_validate(migrated).localPreprocessing.status == status


def test_migration_keeps_existing_new_fields_when_legacy_fields_conflict():
    payload = _legacy_project(local_preprocessing=_legacy_preprocessing("completed"))
    payload["referenceMedia"] = {
        "type": "image", "id": "image-001", "originalName": "still.png", "format": "png",
        "sizeBytes": 12, "width": 800, "height": 800, "hasTransparency": False,
    }
    payload["localPreprocessing"]["sourceReferenceMediaId"] = "image-001"
    payload["localPreprocessing"]["mediaType"] = "image"

    migrated = migrate_project_payload(payload)

    assert migrated["referenceMedia"]["id"] == "image-001"
    assert migrated["localPreprocessing"]["sourceReferenceMediaId"] == "image-001"
    assert migrated["localPreprocessing"]["mediaType"] == "image"
    assert migrated["referenceVideo"]["id"] == "video-001"
    assert migrated["localPreprocessing"]["sourceReferenceVideoId"] == "video-001"


def test_migration_is_idempotent_without_mutating_the_legacy_payload():
    payload = _legacy_project(local_preprocessing=_legacy_preprocessing("running"))
    original_video = dict(payload["referenceVideo"])

    migrated_once = migrate_project_payload(payload)
    migrated_twice = migrate_project_payload(migrated_once)

    assert migrated_once == migrated_twice
    assert payload["referenceVideo"] == original_video
    assert "referenceMedia" not in payload


@pytest.mark.parametrize("legacy_type", ["image", "invalid", None])
def test_legacy_reference_video_type_is_preserved_for_project_validation(legacy_type):
    payload = _legacy_project()
    payload["referenceVideo"]["type"] = legacy_type

    migrated = migrate_project_payload(payload)

    assert migrated["referenceMedia"]["type"] == legacy_type
    with pytest.raises(ValidationError):
        Project.model_validate(migrated)


def test_malformed_legacy_reference_video_still_fails_project_validation():
    payload = _legacy_project()
    payload["referenceVideo"] = "not-a-video"

    with pytest.raises(ValidationError):
        Project.model_validate(migrate_project_payload(payload))
