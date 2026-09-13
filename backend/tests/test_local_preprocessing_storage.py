import json
from datetime import datetime, timezone

import pytest
from PIL import Image

from app.local_preprocessing import new_local_preprocessing
from app.local_preprocessing_storage import (
    atomic_write_json,
    commit_keyframe_stage,
    discard_preprocessing,
    inspect_completed_stages,
    preprocessing_directory,
    reset_stage_artifacts,
    validate_completed_stages,
    write_stage_json,
)


@pytest.fixture
def preprocessing():
    task = new_local_preprocessing(
        preprocessing_id="prep-001",
        reference_media_id="video-001", media_type="video",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    for stage in task.stages:
        stage.status = "completed"
    return task


@pytest.fixture
def image_preprocessing():
    task = new_local_preprocessing(
        preprocessing_id="prep-image-001",
        reference_media_id="image-001", media_type="image",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    for stage in task.stages:
        stage.status = "completed"
    return task


def storage_directory(tmp_path):
    return preprocessing_directory(tmp_path, "project-001", "prep-001")


def write_valid_image_artifacts(directory):
    Image.new("RGB", (256, 256), (12, 34, 56)).save(directory / "normalized.png", format="PNG")
    Image.new("RGB", (256, 256), (12, 34, 56)).save(directory / "analysis-proxy.jpg", format="JPEG")


def write_image_manifest(directory, source_reference_media_id):
    write_stage_json(directory, "manifest.json", {
        "schemaVersion": 1,
        "algorithmVersion": 1,
        "mediaType": "image",
        "sourceReferenceMediaId": source_reference_media_id,
    })


def test_preprocessing_path_stays_below_data_directory(tmp_path):
    path = preprocessing_directory(tmp_path, "project-001", "prep-001")

    assert path == tmp_path / "project-files/project-001/local-preprocessing/prep-001"
    with pytest.raises(OSError, match="本地预处理路径超出数据目录"):
        preprocessing_directory(tmp_path, "../outside", "prep-001")


def test_preprocessing_path_rejects_parent_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "project-files").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="本地预处理路径超出数据目录"):
        preprocessing_directory(tmp_path, "project-001", "prep-001")


def test_atomic_json_never_leaves_part_file(tmp_path):
    target = tmp_path / "prep/decode.json"

    atomic_write_json(target, {"sourceReferenceVideoId": "video-001"})

    assert json.loads(target.read_text(encoding="utf-8"))["sourceReferenceVideoId"] == "video-001"
    assert list(tmp_path.rglob("*.part")) == []


def test_atomic_json_removes_part_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "prep/decode.json"

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("app.local_preprocessing_storage.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_json(target, {"sourceReferenceVideoId": "video-001"})

    assert list(tmp_path.rglob("*.part")) == []
    assert not target.exists()


def test_completed_stage_with_missing_artifact_resets_it_and_followers(tmp_path, preprocessing):
    directory = preprocessing_directory(tmp_path, "project-001", preprocessing.id)
    write_stage_json(directory, "decode.json", {"durationSeconds": 2.0})
    write_stage_json(directory, "scene-changes.json", {"changes": []})

    valid = validate_completed_stages(preprocessing, directory)

    assert valid.firstInvalidStage == "keyframeExtraction"
    assert [stage.status for stage in valid.preprocessing.stages] == [
        "completed", "completed", "pending", "pending", "pending",
    ]


def test_inspecting_invalid_completed_stages_keeps_all_artifacts_and_returns_a_copy(tmp_path, preprocessing):
    directory = preprocessing_directory(tmp_path, "project-001", preprocessing.id)
    directory.mkdir(parents=True)
    (directory / "decode.json").write_bytes(b"")
    write_stage_json(directory, "scene-changes.json", {"changes": []})
    artifacts_before = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }

    inspected = inspect_completed_stages(preprocessing, directory)

    assert inspected.firstInvalidStage == "decoding"
    assert [stage.status for stage in inspected.preprocessing.stages] == ["pending"] * 5
    assert [stage.status for stage in preprocessing.stages] == ["completed"] * 5
    assert {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    } == artifacts_before


def test_reset_stage_artifacts_removes_current_stage_and_followers(tmp_path):
    directory = storage_directory(tmp_path)
    directory.mkdir(parents=True)
    for name in ("motion.json", "analysis-proxy.json", "manifest.json"):
        (directory / name).write_text("{}", encoding="utf-8")
    (directory / "contact-sheet.jpg").write_bytes(b"sheet")
    (directory / "keyframes").mkdir()

    reset_stage_artifacts(directory, "keyframeExtraction", "video")

    assert not (directory / "contact-sheet.jpg").exists()
    assert not (directory / "keyframes").exists()
    assert not (directory / "motion.json").exists()
    assert not (directory / "analysis-proxy.json").exists()
    assert not (directory / "manifest.json").exists()


def test_keyframe_stage_is_all_or_nothing(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "keyframes").mkdir(parents=True)
    for index in range(1, 5):
        (workspace / "keyframes" / f"frame-{index:04d}.jpg").write_bytes(b"jpeg")
    (workspace / "contact-sheet.jpg").write_bytes(b"sheet")
    destination = storage_directory(tmp_path)

    commit_keyframe_stage(
        workspace,
        destination,
        [f"frame-{index:04d}.jpg" for index in range(1, 5)],
    )

    assert (destination / "keyframes/frame-0001.jpg").read_bytes() == b"jpeg"
    assert (destination / "contact-sheet.jpg").read_bytes() == b"sheet"


def test_keyframe_stage_rejects_missing_frame_without_exposing_partial_results(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "keyframes").mkdir(parents=True)
    for index in range(1, 4):
        (workspace / "keyframes" / f"frame-{index:04d}.jpg").write_bytes(b"jpeg")
    (workspace / "contact-sheet.jpg").write_bytes(b"sheet")
    destination = storage_directory(tmp_path)

    with pytest.raises(ValueError):
        commit_keyframe_stage(
            workspace,
            destination,
            [f"frame-{index:04d}.jpg" for index in range(1, 5)],
        )

    assert not (destination / "keyframes").exists()
    assert not (destination / "contact-sheet.jpg").exists()


def test_keyframe_stage_failure_discards_its_previous_artifacts(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "keyframes").mkdir(parents=True)
    (workspace / "contact-sheet.jpg").write_bytes(b"sheet")
    destination = storage_directory(tmp_path)
    (destination / "keyframes").mkdir(parents=True)
    (destination / "keyframes" / "frame-0001.jpg").write_bytes(b"old")
    (destination / "contact-sheet.jpg").write_bytes(b"old-sheet")

    with pytest.raises(ValueError):
        commit_keyframe_stage(
            workspace,
            destination,
            [f"frame-{index:04d}.jpg" for index in range(1, 5)],
        )

    assert not (destination / "keyframes").exists()
    assert not (destination / "contact-sheet.jpg").exists()


def test_invalid_manifest_algorithm_version_resets_final_stage(tmp_path, preprocessing):
    directory = preprocessing_directory(tmp_path, "project-001", preprocessing.id)
    write_stage_json(directory, "decode.json", {"durationSeconds": 2.0})
    write_stage_json(directory, "scene-changes.json", {"changes": []})
    workspace = tmp_path / "workspace"
    (workspace / "keyframes").mkdir(parents=True)
    names = [f"frame-{index:04d}.jpg" for index in range(1, 5)]
    for name in names:
        (workspace / "keyframes" / name).write_bytes(b"jpeg")
    (workspace / "contact-sheet.jpg").write_bytes(b"sheet")
    commit_keyframe_stage(workspace, directory, names)
    write_stage_json(directory, "motion.json", {"p90": 1.0})
    write_stage_json(directory, "analysis-proxy.json", {"keyframeCount": 4})
    write_stage_json(directory, "manifest.json", {
        "schemaVersion": 1,
        "algorithmVersion": 999,
        "mediaType": "video",
        "sourceReferenceMediaId": preprocessing.sourceReferenceMediaId,
    })

    valid = validate_completed_stages(preprocessing, directory)

    assert valid.firstInvalidStage == "reproducibilityAssessment"
    assert valid.preprocessing.stages[-1].status == "pending"
    assert not (directory / "analysis-proxy.json").exists()
    assert not (directory / "manifest.json").exists()


def test_image_completed_stages_require_media_specific_artifacts_and_manifest(tmp_path, image_preprocessing):
    directory = preprocessing_directory(tmp_path, "project-001", image_preprocessing.id)
    directory.mkdir(parents=True)
    write_valid_image_artifacts(directory)
    write_image_manifest(directory, image_preprocessing.sourceReferenceMediaId)

    valid = validate_completed_stages(image_preprocessing, directory)

    assert valid.firstInvalidStage is None
    assert [stage.status for stage in valid.preprocessing.stages] == ["completed"] * 4


@pytest.mark.parametrize("invalid_proxy", ["truncated", "oversized"])
def test_invalid_image_proxy_rewinds_only_proxy_and_following_stages(
    tmp_path, image_preprocessing, invalid_proxy,
):
    directory = preprocessing_directory(tmp_path, "project-001", image_preprocessing.id)
    directory.mkdir(parents=True)
    write_valid_image_artifacts(directory)
    proxy = directory / "analysis-proxy.jpg"
    if invalid_proxy == "truncated":
        proxy.write_bytes(proxy.read_bytes()[:-20])
        with Image.open(proxy) as image:
            with pytest.raises(OSError):
                image.load()
    else:
        Image.new("RGB", (3000, 2000), (12, 34, 56)).save(proxy, format="JPEG")
    write_image_manifest(directory, image_preprocessing.sourceReferenceMediaId)

    valid = validate_completed_stages(image_preprocessing, directory)

    assert valid.firstInvalidStage == "proxyGeneration"
    assert [stage.status for stage in valid.preprocessing.stages] == [
        "completed", "completed", "pending", "pending",
    ]
    assert (directory / "normalized.png").is_file()
    assert not proxy.exists()
    assert not (directory / "manifest.json").exists()


@pytest.mark.parametrize(
    "manifest_update",
    [
        {"algorithmVersion": 999},
        {"mediaType": "video"},
        {"sourceReferenceMediaId": "image-other"},
    ],
)
def test_image_manifest_requires_matching_media_algorithm_and_source(
    tmp_path, image_preprocessing, manifest_update,
):
    directory = preprocessing_directory(tmp_path, "project-001", image_preprocessing.id)
    directory.mkdir(parents=True)
    write_valid_image_artifacts(directory)
    manifest = {
        "schemaVersion": 1,
        "algorithmVersion": 1,
        "mediaType": "image",
        "sourceReferenceMediaId": image_preprocessing.sourceReferenceMediaId,
    }
    manifest.update(manifest_update)
    write_stage_json(directory, "manifest.json", manifest)

    valid = validate_completed_stages(image_preprocessing, directory)

    assert valid.firstInvalidStage == "reproducibilityAssessment"


def test_discard_preprocessing_rejects_unsafe_id_without_deleting_sentinel(tmp_path):
    sentinel = tmp_path / "outside" / "sentinel"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(OSError, match="本地预处理路径超出数据目录"):
        discard_preprocessing(tmp_path, "project-001", "../outside")

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_reset_rejects_replaced_parent_symlink_without_deleting_external_sentinel(tmp_path):
    directory = storage_directory(tmp_path)
    directory.mkdir(parents=True)
    project_files = tmp_path / "project-files"
    original = tmp_path / "project-files-original"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    sentinel = outside / "project-001/local-preprocessing/prep-001/motion.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep", encoding="utf-8")
    project_files.rename(original)
    project_files.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="本地预处理路径超出数据目录"):
        reset_stage_artifacts(directory, "motionAnalysis", "video")

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_keyframe_recovery_rejects_external_frame_symlink(tmp_path, preprocessing):
    directory = storage_directory(tmp_path)
    write_stage_json(directory, "decode.json", {"durationSeconds": 2.0})
    write_stage_json(directory, "scene-changes.json", {"changes": []})
    (directory / "keyframes").mkdir()
    outside = tmp_path / "outside-frame.jpg"
    outside.write_bytes(b"outside")
    for index in range(1, 5):
        frame = directory / "keyframes" / f"frame-{index:04d}.jpg"
        if index == 2:
            frame.symlink_to(outside)
        else:
            frame.write_bytes(b"jpeg")
    (directory / "contact-sheet.jpg").write_bytes(b"sheet")

    valid = validate_completed_stages(preprocessing, directory)

    assert valid.firstInvalidStage == "keyframeExtraction"
    assert outside.read_bytes() == b"outside"


def test_keyframe_commit_rejects_external_frame_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "keyframes").mkdir(parents=True)
    outside = tmp_path / "outside-frame.jpg"
    outside.write_bytes(b"outside")
    names = [f"frame-{index:04d}.jpg" for index in range(1, 5)]
    for name in names:
        frame = workspace / "keyframes" / name
        if name == "frame-0002.jpg":
            frame.symlink_to(outside)
        else:
            frame.write_bytes(b"jpeg")
    (workspace / "contact-sheet.jpg").write_bytes(b"sheet")
    destination = storage_directory(tmp_path)

    with pytest.raises(ValueError, match="关键帧文件无效"):
        commit_keyframe_stage(workspace, destination, names)

    assert outside.read_bytes() == b"outside"
    assert not (destination / "keyframes").exists()


def test_keyframe_contact_sheet_replace_failure_cleans_only_current_stage(tmp_path):
    destination = storage_directory(tmp_path)
    write_stage_json(destination, "decode.json", {"durationSeconds": 2.0})
    workspace = tmp_path / "workspace"
    (workspace / "keyframes").mkdir(parents=True)
    names = [f"frame-{index:04d}.jpg" for index in range(1, 5)]
    for name in names:
        (workspace / "keyframes" / name).write_bytes(b"jpeg")
    (workspace / "contact-sheet.jpg").write_bytes(b"sheet")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-sentinel"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    import app.local_preprocessing_storage as storage
    original_replace = storage.os.replace
    calls = 0

    def fail_contact_sheet_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("contact sheet replace failed")
        original_replace(source, target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(storage.os, "replace", fail_contact_sheet_replace)
    try:
        with pytest.raises(OSError, match="contact sheet replace failed"):
            commit_keyframe_stage(workspace, destination, names)
    finally:
        monkeypatch.undo()

    assert not (destination / "keyframes").exists()
    assert not (destination / "contact-sheet.jpg").exists()
    assert json.loads((destination / "decode.json").read_text(encoding="utf-8")) == {
        "durationSeconds": 2.0,
    }
    assert sentinel.read_text(encoding="utf-8") == "keep"
