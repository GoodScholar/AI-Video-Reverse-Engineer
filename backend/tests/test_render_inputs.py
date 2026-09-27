import pytest

import app.render_inputs as render_inputs
from app.project_assets import ProjectAssets
from app.render_inputs import RenderInputError, RenderInputPreparation


def project_assets(tmp_path):
    root = tmp_path / "project-files" / "p1" / "preproduction" / "assets"
    root.mkdir(parents=True)
    (root / "video.mp4").write_bytes(b"video-v1")
    (root / "audio.wav").write_bytes(b"audio-v1")
    assets = {
        "video": {"id": "video", "kind": "video", "file": "video.mp4"},
        "audio": {"id": "audio", "kind": "audio", "file": "audio.wav"},
    }
    return RenderInputPreparation(ProjectAssets(tmp_path)), assets, root


def test_freeze_current_copies_each_used_project_asset_once_and_resolves_the_snapshot(tmp_path):
    preparation, assets, asset_root = project_assets(tmp_path)
    destination = tmp_path / "timeline" / "runs" / "run-1"
    tracks = [
        {"clips": [{"assetId": "video"}, {"assetId": "video"}]},
        {"clips": [{"assetId": "audio"}]},
    ]

    sources = preparation.freeze_current("p1", destination, tracks, assets)
    (asset_root / "video.mp4").write_bytes(b"video-v2")
    resolved = preparation.resolve(destination, sources)

    assert sources == {"audio": "audio.wav", "video": "video.mp4"}
    assert resolved["audio"].read_bytes() == b"audio-v1"
    assert resolved["video"].read_bytes() == b"video-v1"


@pytest.mark.parametrize("failure", ["missing", "symlink", "directory", "traversal"])
def test_freeze_current_rejects_unsafe_sources_and_removes_the_partial_snapshot(tmp_path, failure):
    preparation, _, asset_root = project_assets(tmp_path)
    (asset_root / "a-good.mp4").write_bytes(b"good")
    bad_id = "../escape" if failure == "traversal" else "z-bad"
    bad_file = asset_root / "z-bad.mp4"
    if failure == "symlink":
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"outside")
        bad_file.symlink_to(outside)
    elif failure == "directory":
        bad_file.mkdir()
    assets = {
        "a-good": {"id": "a-good", "kind": "video", "file": "a-good.mp4"},
        bad_id: {"id": bad_id, "kind": "video", "file": "z-bad.mp4"},
    }
    destination = tmp_path / "runs" / "failed"
    tracks = [{"clips": [{"assetId": "a-good"}, {"assetId": bad_id}]}]

    with pytest.raises(RenderInputError):
        preparation.freeze_current("p1", destination, tracks, assets)

    assert not destination.exists()


def test_freeze_current_removes_a_snapshot_after_copy_is_interrupted(tmp_path, monkeypatch):
    preparation, assets, _ = project_assets(tmp_path)
    destination = tmp_path / "runs" / "interrupted"
    tracks = [{"clips": [{"assetId": "video"}]}]

    def interrupt_copy(source, target):
        target.write(source.read(2))
        raise OSError("copy interrupted")

    monkeypatch.setattr(render_inputs.shutil, "copyfileobj", interrupt_copy)

    with pytest.raises(RenderInputError) as raised:
        preparation.freeze_current("p1", destination, tracks, assets)

    assert str(raised.value) == ""
    assert not destination.exists()


def test_freeze_approved_copies_the_specific_preview_sources_when_current_contents_match(tmp_path):
    preparation, assets, _ = project_assets(tmp_path)
    tracks = [{"clips": [{"assetId": "video"}]}]
    preview = tmp_path / "batch" / "runs" / "preview-1"
    preview_sources = preparation.freeze_current("p1", preview, tracks, assets)
    destination = tmp_path / "batch" / "exports" / "delivery-1"

    sources = preparation.freeze_approved("p1", destination, preview, preview_sources, assets)

    assert sources == {"video": "video.mp4"}
    assert preparation.resolve(destination, sources)["video"].read_bytes() == b"video-v1"


def test_freeze_approved_rejects_changed_current_contents_and_removes_partial_delivery(tmp_path):
    preparation, assets, asset_root = project_assets(tmp_path)
    tracks = [{"clips": [{"assetId": "audio"}, {"assetId": "video"}]}]
    preview = tmp_path / "batch" / "runs" / "preview-1"
    preview_sources = preparation.freeze_current("p1", preview, tracks, assets)
    (asset_root / "video.mp4").write_bytes(b"video-v2")
    destination = tmp_path / "batch" / "exports" / "delivery-1"

    with pytest.raises(RenderInputError):
        preparation.freeze_approved("p1", destination, preview, preview_sources, assets)

    assert not destination.exists()


@pytest.mark.parametrize("failure", ["asset-traversal", "file-traversal", "symlink", "directory", "missing"])
def test_resolve_rejects_unsafe_or_missing_frozen_sources(tmp_path, failure):
    preparation, _, _ = project_assets(tmp_path)
    directory = tmp_path / "runs" / "run-1"
    source_directory = directory / "sources"
    source_directory.mkdir(parents=True)
    asset_id, filename = "video", "video.mp4"
    if failure == "asset-traversal":
        asset_id = "../video"
        (source_directory / filename).write_bytes(b"video")
    elif failure == "file-traversal":
        filename = "../outside.mp4"
        (directory / "outside.mp4").write_bytes(b"outside")
    elif failure == "symlink":
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"outside")
        (source_directory / filename).symlink_to(outside)
    elif failure == "directory":
        (source_directory / filename).mkdir()

    with pytest.raises(RenderInputError) as raised:
        preparation.resolve(directory, {asset_id: filename})

    expected = {
        "asset-traversal": "存储标识符必须是安全的单段名称",
        "file-traversal": "存储标识符必须是安全的单段名称",
        "symlink": "前置工作台存储路径无效",
        "directory": "不是普通文件",
        "missing": "",
    }
    assert str(raised.value) == expected[failure]


def test_resolve_rejects_a_symlinked_frozen_source_directory(tmp_path):
    preparation, _, _ = project_assets(tmp_path)
    directory = tmp_path / "runs" / "run-1"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "video.mp4").write_bytes(b"outside")
    (directory / "sources").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RenderInputError) as raised:
        preparation.resolve(directory, {"video": "video.mp4"})

    assert str(raised.value) == "前置工作台存储路径无效"


@pytest.mark.parametrize("failure", ["missing", "file"])
def test_resolve_uses_the_existing_fallback_for_an_unusable_source_directory(tmp_path, failure):
    preparation, _, _ = project_assets(tmp_path)
    directory = tmp_path / "runs" / "run-1"
    directory.mkdir(parents=True)
    if failure == "file":
        (directory / "sources").write_bytes(b"not-a-directory")

    with pytest.raises(RenderInputError) as raised:
        preparation.resolve(directory, {"video": "video.mp4"})

    assert str(raised.value) == ""


def test_freeze_approved_rejects_a_symlinked_preview_source_directory(tmp_path):
    preparation, assets, _ = project_assets(tmp_path)
    preview = tmp_path / "runs" / "preview"
    preview.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "video.mp4").write_bytes(b"video-v1")
    (preview / "sources").symlink_to(outside, target_is_directory=True)
    destination = tmp_path / "exports" / "delivery"

    with pytest.raises(RenderInputError):
        preparation.freeze_approved(
            "p1",
            destination,
            preview,
            {"video": "video.mp4"},
            assets,
        )

    assert not destination.exists()


@pytest.mark.parametrize("operation", ["current", "approved"])
def test_failed_preparation_never_removes_a_preexisting_destination(tmp_path, operation):
    preparation, assets, _ = project_assets(tmp_path)
    tracks = [{"clips": [{"assetId": "video"}]}]
    preview = tmp_path / "runs" / "preview"
    preview_sources = preparation.freeze_current("p1", preview, tracks, assets)
    destination = tmp_path / "runs" / "existing"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(RenderInputError):
        if operation == "current":
            preparation.freeze_current("p1", destination, tracks, assets)
        else:
            preparation.freeze_approved("p1", destination, preview, preview_sources, assets)

    assert marker.read_text(encoding="utf-8") == "keep"
