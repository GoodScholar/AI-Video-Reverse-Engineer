import json
import re
import stat

import pytest

from app.depth_capture_storage import (
    DEPTH_ARTIFACTS,
    commit_depth_artifacts,
    create_depth_capture_workspace,
    depth_capture_directory,
    discard_depth_capture,
    inspect_depth_artifacts,
)


PROJECT_ID = "project-1"
CAPTURE_ID = "capture-1"
SOURCE_ID = "video-1"
MODEL_IDENTITY = {
    "modelId": "video-depth-anything-small-relative",
    "upstreamCommit": "4f5ae23172ba60fd7bc11ef671cca678842c7072",
    "checkpointSha256": "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
}


def canonical_directory(data_dir):
    return depth_capture_directory(data_dir, PROJECT_ID, CAPTURE_ID)


def create_valid_depth_artifacts(directory, *, source=SOURCE_ID, algorithm=1):
    for filename in DEPTH_ARTIFACTS:
        (directory / filename).write_bytes(b"artifact")
    (directory / "manifest.json").write_text(json.dumps({
        "schemaVersion": 1,
        "algorithmVersion": algorithm,
        "sourceReferenceVideoId": source,
        "modelIdentity": MODEL_IDENTITY,
        "normalizationDirection": "near_white_far_black",
        "files": [
            "depth-control.mp4",
            "depth-preview.mp4",
            "depth-metadata.json",
            "depth-quality.json",
            "manifest.json",
        ],
    }), encoding="utf-8")


def inspect(data_dir, *, source=SOURCE_ID, algorithm=1):
    return inspect_depth_artifacts(
        data_dir=data_dir,
        project_id=PROJECT_ID,
        capture_id=CAPTURE_ID,
        source_reference_video_id=source,
        algorithm=algorithm,
    )


def commit(workspace, data_dir, *, source=SOURCE_ID, algorithm=1):
    return commit_depth_artifacts(
        workspace,
        data_dir=data_dir,
        project_id=PROJECT_ID,
        capture_id=CAPTURE_ID,
        source_reference_video_id=source,
        algorithm=algorithm,
    )


def test_depth_capture_directory_rejects_traversal(tmp_path):
    with pytest.raises(OSError, match="深度捕捉路径超出数据目录"):
        depth_capture_directory(tmp_path, "../escape", CAPTURE_ID)


def test_create_workspace_is_random_canonical_and_private(tmp_path):
    workspace = create_depth_capture_workspace(tmp_path, PROJECT_ID, CAPTURE_ID)

    assert workspace.parent == canonical_directory(tmp_path).parent
    assert re.fullmatch(r"\.capture-1-[0-9a-f]{32}", workspace.name)
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700


def test_create_workspace_rejects_symlinked_canonical_parent(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "project-files").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="深度捕捉路径超出数据目录"):
        create_depth_capture_workspace(tmp_path, PROJECT_ID, CAPTURE_ID)


def test_commit_promotes_complete_workspace_to_trusted_canonical_directory(tmp_path):
    workspace = create_depth_capture_workspace(tmp_path, PROJECT_ID, CAPTURE_ID)
    create_valid_depth_artifacts(workspace)

    committed = commit(workspace, tmp_path)

    assert committed == canonical_directory(tmp_path)
    assert not workspace.exists()
    assert {path.name for path in committed.iterdir()} == set(DEPTH_ARTIFACTS)


def test_commit_requires_all_five_artifacts_without_creating_capture(tmp_path):
    workspace = create_depth_capture_workspace(tmp_path, PROJECT_ID, CAPTURE_ID)
    (workspace / "depth-control.mp4").write_bytes(b"video")

    with pytest.raises(ValueError, match="深度捕捉产物不完整"):
        commit(workspace, tmp_path)

    assert not canonical_directory(tmp_path).exists()


def test_commit_rejects_workspace_not_created_by_the_trusted_helper(tmp_path):
    parent = canonical_directory(tmp_path).parent
    parent.mkdir(parents=True)
    workspace = parent / ".capture-1-manual"
    workspace.mkdir(mode=0o700)
    create_valid_depth_artifacts(workspace)

    with pytest.raises(OSError, match="深度捕捉工作目录无效"):
        commit(workspace, tmp_path)

    assert not canonical_directory(tmp_path).exists()


def test_commit_rejects_valid_but_wrong_source_video_id(tmp_path):
    workspace = create_depth_capture_workspace(tmp_path, PROJECT_ID, CAPTURE_ID)
    create_valid_depth_artifacts(workspace, source="video-2")

    with pytest.raises(ValueError, match="深度捕捉清单无效"):
        commit(workspace, tmp_path, source=SOURCE_ID)

    assert not canonical_directory(tmp_path).exists()


def test_commit_rejects_existing_destination_without_replacing_it(tmp_path):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    workspace = create_depth_capture_workspace(tmp_path, PROJECT_ID, CAPTURE_ID)
    create_valid_depth_artifacts(workspace)

    with pytest.raises(OSError, match="深度捕捉目标已存在"):
        commit(workspace, tmp_path)

    assert (destination / "depth-control.mp4").read_bytes() == b"artifact"
    assert workspace.exists()


def test_commit_rejects_symlink_artifact_without_exposing_destination(tmp_path):
    workspace = create_depth_capture_workspace(tmp_path, PROJECT_ID, CAPTURE_ID)
    create_valid_depth_artifacts(workspace)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    (workspace / "depth-control.mp4").unlink()
    (workspace / "depth-control.mp4").symlink_to(outside)

    with pytest.raises(ValueError, match="深度捕捉产物无效"):
        commit(workspace, tmp_path)

    assert outside.read_bytes() == b"outside"
    assert not canonical_directory(tmp_path).exists()


@pytest.mark.parametrize("field,value", [
    ("modelId", "wrong-model"),
    ("upstreamCommit", "0" * 40),
    ("checkpointSha256", "0" * 64),
])
def test_inspect_rejects_each_wrong_fixed_model_identity_field(tmp_path, field, value):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["modelIdentity"][field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert inspect(tmp_path).valid is False


def test_inspect_rejects_wrong_normalization_direction(tmp_path):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["normalizationDirection"] = "near_black_far_white"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert inspect(tmp_path).valid is False


def test_inspect_rejects_non_relative_artifact_filename_in_manifest(tmp_path):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0] = "../depth-control.mp4"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert inspect(tmp_path).valid is False


@pytest.mark.parametrize("field", ["schemaVersion", "algorithmVersion"])
def test_inspect_rejects_boolean_versions(tmp_path, field):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert inspect(tmp_path).valid is False


def test_inspect_rejects_invalid_utf8_manifest(tmp_path):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    (destination / "manifest.json").write_bytes(b"\xff\xfe")

    assert inspect(tmp_path).valid is False


def test_inspect_rejects_symlinked_ancestor(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    destination = outside / f"project-files/{PROJECT_ID}/depth-captures/{CAPTURE_ID}"
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    (tmp_path / "project-files").symlink_to(outside / "project-files", target_is_directory=True)

    assert inspect(tmp_path).valid is False


def test_inspect_requires_matching_source_and_algorithm(tmp_path):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination, source="video-2")

    assert inspect(tmp_path, source="video-1").valid is False
    assert inspect(tmp_path, source="video-2", algorithm=2).valid is False


def test_discard_depth_capture_rejects_symlink_without_deleting_external_files(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    sentinel = outside / "sentinel"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")
    parent = canonical_directory(tmp_path).parent
    parent.mkdir(parents=True)
    canonical_directory(tmp_path).symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="深度捕捉路径超出数据目录"):
        discard_depth_capture(tmp_path, PROJECT_ID, CAPTURE_ID)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_discard_depth_capture_removes_only_five_expected_files(tmp_path):
    destination = canonical_directory(tmp_path)
    destination.mkdir(parents=True)
    create_valid_depth_artifacts(destination)
    sentinel = destination.parent / "other-capture" / "sentinel"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")

    discard_depth_capture(tmp_path, PROJECT_ID, CAPTURE_ID)

    assert not destination.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"
