from pathlib import Path

import pytest

from app.preproduction import PreproductionStore
from app.project_assets import AssetInUseError, AssetReferencesUnavailableError, ProjectAssets


PROJECT_ID = "p1"


def _state(asset=None):
    return {
        "schemaVersion": 1,
        "revision": 0,
        "brief": {},
        "shots": [],
        "assets": [asset] if asset else [],
    }


def test_projects_assets_own_public_projection_and_file_resolution(tmp_path):
    asset = {"id": "asset-1", "name": "画面", "kind": "video", "role": "motion",
             "file": "asset-1.mp4", "sourceReferenceId": "private", "_private": True}
    store = PreproductionStore(tmp_path)
    store.save(PROJECT_ID, _state(asset))
    path = store.path(PROJECT_ID, "assets", asset["file"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")

    assets = ProjectAssets(tmp_path)

    assert assets.index(PROJECT_ID) == {asset["id"]: asset}
    assert assets.public(PROJECT_ID) == [{
        "id": "asset-1", "name": "画面", "kind": "video", "role": "motion",
        "url": "/api/projects/p1/preproduction/assets/asset-1/file",
    }]
    assert assets.public(PROJECT_ID, include_source_reference=True)[0]["sourceReferenceId"] == "private"
    assert assets.file(PROJECT_ID, "asset-1") == path
    assert assets.available(PROJECT_ID, asset)


def test_install_rolls_back_file_and_state_when_workspace_save_fails(tmp_path):
    store = PreproductionStore(tmp_path)
    state = _state()
    source = tmp_path / "upload.mp4"
    source.write_bytes(b"video")
    record = {"id": "asset-1", "name": "上传", "kind": "video", "role": "motion", "file": "asset-1.mp4"}
    assets = ProjectAssets(tmp_path)

    with pytest.raises(OSError):
        with assets.install(PROJECT_ID, state, source, record):
            raise OSError("disk full")

    assert state["assets"] == []
    assert source.read_bytes() == b"video"
    assert not store.path(PROJECT_ID, "assets", "asset-1.mp4").exists()


def test_reference_facts_are_contributed_without_project_assets_interpreting_them(tmp_path):
    asset = {"id": "asset-1", "name": "画面", "kind": "video", "role": "motion", "file": "asset-1.mp4"}
    PreproductionStore(tmp_path).save(PROJECT_ID, _state(asset))
    calls = []

    def shots(project_id, asset_id):
        calls.append(("shots", project_id, asset_id))
        return [{"kind": "shot_binding", "label": "镜头绑定"}]

    def timeline(project_id, asset_id):
        calls.append(("timeline", project_id, asset_id))
        return [{"kind": "timeline", "label": "时间线片段"}]

    assets = ProjectAssets(tmp_path, reference_facts=(shots, timeline))

    assert assets.references(PROJECT_ID, "asset-1") == [
        {"kind": "shot_binding", "label": "镜头绑定"},
        {"kind": "timeline", "label": "时间线片段"},
    ]
    assert calls == [("shots", PROJECT_ID, "asset-1"), ("timeline", PROJECT_ID, "asset-1")]


def test_reference_fact_failures_are_reported_separately_from_delete_failures(tmp_path):
    asset = {"id": "asset-1", "name": "画面", "kind": "video", "role": "motion", "file": "asset-1.mp4"}
    PreproductionStore(tmp_path).save(PROJECT_ID, _state(asset))

    def unavailable(project_id, asset_id):
        raise OSError("broken timeline")

    assets = ProjectAssets(tmp_path, reference_facts=(unavailable,))

    with pytest.raises(AssetReferencesUnavailableError):
        assets.references(PROJECT_ID, "asset-1")


def test_delete_restores_file_when_state_save_fails(tmp_path):
    asset = {"id": "asset-1", "name": "画面", "kind": "video", "role": "motion", "file": "asset-1.mp4"}
    store = PreproductionStore(tmp_path)
    state = _state(asset)
    store.save(PROJECT_ID, state)
    path = store.path(PROJECT_ID, "assets", asset["file"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    assets = ProjectAssets(tmp_path)

    with pytest.raises(OSError):
        assets.delete(PROJECT_ID, state, "asset-1", save=lambda value: (_ for _ in ()).throw(OSError("disk full")))

    assert path.read_bytes() == b"video"
    assert state["assets"] == [asset]


def test_delete_is_blocked_by_contributed_reference_facts(tmp_path):
    asset = {"id": "asset-1", "name": "画面", "kind": "video", "role": "motion", "file": "asset-1.mp4"}
    store = PreproductionStore(tmp_path)
    state = _state(asset)
    store.save(PROJECT_ID, state)
    path = store.path(PROJECT_ID, "assets", asset["file"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    assets = ProjectAssets(tmp_path, reference_facts=(
        lambda project_id, asset_id: [{"kind": "timeline", "label": "时间线片段"}],
    ))

    with pytest.raises(AssetInUseError) as error:
        assets.delete(PROJECT_ID, state, "asset-1", save=lambda value: store.save(PROJECT_ID, value))

    assert error.value.references == [{"kind": "timeline", "label": "时间线片段"}]
    assert path.read_bytes() == b"video"
    assert state["assets"] == [asset]


def test_metadata_update_restores_record_when_state_save_fails(tmp_path):
    asset = {"id": "asset-1", "name": "旧名称", "notes": "旧备注", "kind": "video", "role": "motion", "file": "asset-1.mp4"}
    state = _state(asset)
    assets = ProjectAssets(tmp_path)

    with pytest.raises(OSError):
        assets.update_metadata(
            state, "asset-1", name="新名称", notes="新备注",
            save=lambda value: (_ for _ in ()).throw(OSError("disk full")),
        )

    assert state["revision"] == 0
    assert asset["name"] == "旧名称" and asset["notes"] == "旧备注"
