from copy import deepcopy

import pytest

from app.shot_organization import ShotOrganization, ShotOrganizationError


def workspace():
    return {
        "schemaVersion": 2,
        "revision": 4,
        "brief": {},
        "assets": [{"id": "asset-1"}],
        "scenes": [
            {"id": "scene-a", "title": "相遇", "rank": "00000001", "description": ""},
            {"id": "scene-b", "title": "追逐", "rank": "00000002", "description": ""},
        ],
        "shots": [
            {
                "id": "shot-a", "sceneId": "scene-a", "rank": "00000001", "title": "镜头 A",
                "duration": 2, "prompt": "", "negativePrompt": "", "assetIds": ["asset-1"],
                "resultAssetId": "result-1", "_resultVersions": [{"assetId": "result-1", "reviewed": True}],
                "nodes": [{"id": "prompt", "kind": "prompt", "input": "", "params": {}, "status": "completed", "artifacts": [{"name": "prompt.txt"}]}],
            },
            {
                "id": "shot-b", "sceneId": "scene-b", "rank": "00000002", "title": "镜头 B",
                "duration": 3, "prompt": "", "negativePrompt": "", "assetIds": [],
                "resultAssetId": None, "_resultVersions": [], "nodes": [],
            },
        ],
    }


def test_reorder_changes_only_rank_and_move_changes_only_scene_ownership():
    state = workspace()
    original = deepcopy(state)
    organization = ShotOrganization(state)

    organization.reorder_shots(["shot-b", "shot-a"])

    assert [(shot["id"], shot["rank"]) for shot in state["shots"]] == [
        ("shot-a", "00000002"), ("shot-b", "00000001"),
    ]
    assert state["shots"][0] == {**original["shots"][0], "rank": "00000002"}
    assert state["shots"][1] == {**original["shots"][1], "rank": "00000001"}

    before_move = deepcopy(state["shots"][0])
    organization.move_shots_to_scene(["shot-a"], "scene-b")
    assert state["shots"][0] == {**before_move, "sceneId": "scene-b"}


def test_batch_move_is_atomic_when_any_shot_is_missing():
    state = workspace()
    before = deepcopy(state)

    with pytest.raises(ShotOrganizationError) as error:
        ShotOrganization(state).move_shots_to_scene(["shot-a", "missing"], "scene-b")

    assert error.value.code == "preproduction_shot_missing"
    assert state == before


def test_delete_nonempty_scene_requires_explicit_migration_or_deletion():
    state = workspace()
    organization = ShotOrganization(state)

    with pytest.raises(ShotOrganizationError) as error:
        organization.delete_scene("scene-a")
    assert error.value.code == "preproduction_scene_impact_required"

    organization.delete_scene("scene-a", migrate_to_scene_id="scene-b")
    assert [scene["id"] for scene in state["scenes"]] == ["scene-b"]
    assert all(shot["sceneId"] == "scene-b" for shot in state["shots"])


def test_create_shot_validates_everything_before_mutating_state():
    state = workspace()
    before = deepcopy(state)
    organization = ShotOrganization(state)

    with pytest.raises(ShotOrganizationError):
        organization.create_shot({"id": "shot-new", "sceneId": "missing", "rank": "00000003"})
    assert state == before

    shot = {
        "id": "shot-new", "sceneId": "scene-a", "rank": "00000003", "title": "新镜头",
        "duration": 3, "prompt": "", "negativePrompt": "", "assetIds": [], "nodes": [],
        "resultAssetId": None, "_resultVersions": [],
    }
    organization.create_shot(shot)
    assert state["shots"][-1] == shot
    assert state["revision"] == before["revision"] + 1
