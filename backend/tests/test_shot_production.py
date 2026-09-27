import pytest
from copy import deepcopy

from app.shot_production import ShotProduction, ShotProductionError


def workspace(*, nodes=None):
    return {
        "schemaVersion": 1,
        "revision": 3,
        "brief": {"theme": "短片", "purpose": "", "style": "", "duration": 2, "aspect": "16:9", "mustPreserve": ""},
        "assets": [
            {"id": "source", "name": "源视频", "kind": "video", "role": "motion", "file": "source.mp4", "duration": 2},
            {"id": "result", "name": "结果", "kind": "video", "role": "motion", "file": "result.mp4", "duration": 2},
        ],
        "shots": [{
            "id": "shot-a", "title": "镜头", "duration": 2, "prompt": "原提示", "negativePrompt": "",
            "assetIds": ["source"], "resultAssetId": None, "_resultVersions": [],
            "nodes": nodes if nodes is not None else [],
        }],
    }


def node(identifier, kind, input_value, params=None, *, status="completed"):
    return {
        "id": identifier, "kind": kind, "input": input_value, "params": params or {},
        "status": status, "error": None, "artifacts": [{"name": identifier + ".bin"}] if status == "completed" else [],
    }


def editable_shot(state):
    shot = state["shots"][0]
    return deepcopy({key: shot[key] for key in ("id", "title", "duration", "prompt", "negativePrompt", "assetIds", "resultAssetId", "nodes")})


def test_edit_validates_dependencies_and_invalidates_the_changed_node_and_all_descendants():
    state = workspace(nodes=[
        node("trim", "trim", "asset:source", {"start": 0, "end": 1}),
        node("frame", "first_frame", "node:trim"),
        node("resize", "resize", "node:frame", {"width": 640, "height": 480}),
    ])
    production = ShotProduction(state)
    incoming = editable_shot(state)
    incoming["nodes"][0]["params"] = {"start": 0, "end": 1.5}

    production.edit(state["brief"], [incoming])

    assert state["revision"] == 4
    assert [item["status"] for item in state["shots"][0]["nodes"]] == ["stale", "stale", "stale"]
    assert all(item["artifacts"] == [] for item in state["shots"][0]["nodes"])

    invalid = editable_shot(state)
    invalid["nodes"][0]["input"] = "node:resize"
    with pytest.raises(ShotProductionError) as error:
        production.edit(state["brief"], [invalid])
    assert (error.value.code, error.value.status) == ("preproduction_input_invalid", 422)


def test_edit_rejects_an_inflight_run_without_losing_its_identity():
    state = workspace(nodes=[node("source", "reference", "asset:source", status="pending")])
    production = ShotProduction(state)
    production.prepare_run("shot-a", "source", "run-1")
    production.start_run("shot-a", "source", "run-1")

    with pytest.raises(ShotProductionError) as error:
        production.edit(state["brief"], [editable_shot(state)])

    step = state["shots"][0]["nodes"][0]
    assert error.value.code == "preproduction_node_running"
    assert (state["revision"], step["status"], step["runId"]) == (4, "running", "run-1")


def test_prompt_changes_use_the_same_dependency_propagation_rule():
    state = workspace(nodes=[
        node("prompt", "prompt", "", {"text": "补充"}),
        node("frame", "first_frame", "node:prompt"),
        node("independent", "reference", "asset:source"),
    ])
    production = ShotProduction(state)
    incoming = editable_shot(state)
    incoming["prompt"] = "新提示"

    production.edit(state["brief"], [incoming])

    assert [item["status"] for item in state["shots"][0]["nodes"]] == ["stale", "stale", "completed"]


def test_candidate_lifecycle_preserves_history_and_derives_review_freshness():
    state = workspace()
    production = ShotProduction(state)

    production.attach_result("shot-a", "result")
    version = production.public_result_versions("shot-a")[0]
    assert state["shots"][0]["resultAssetId"] == "result"
    assert version["reviewed"] is False and version["planChanged"] is False
    assert version["association"]["revision"] == 4

    production.review_result("shot-a", "result")
    production.set_result_note("shot-a", "result", "外部制作")
    production.set_adoption_reason("shot-a", "result", "动作更自然")
    reviewed = production.public_result_versions("shot-a")[0]
    assert reviewed["reviewed"] is True and reviewed["externalNote"] == "外部制作"

    incoming = editable_shot(state)
    incoming["prompt"] = "改变后的方案"
    production.edit(state["brief"], [incoming])
    changed = production.public_result_versions("shot-a")[0]
    assert changed["planChanged"] is True and changed["reviewed"] is False
    assert changed["adoptionReason"] == "动作更自然"

    with pytest.raises(ShotProductionError) as error:
        production.remove_result("shot-a", "result")
    assert error.value.code == "preproduction_result_adopted"

    incoming = editable_shot(state)
    incoming["resultAssetId"] = None
    production.edit(state["brief"], [incoming])
    production.remove_result("shot-a", "result")
    assert production.public_result_versions("shot-a") == []

    with pytest.raises(ShotProductionError) as error:
        production.set_result_note("missing-shot", "result", "备注")
    assert (error.value.code, error.value.status) == ("preproduction_result_missing", 404)


def test_result_attachment_preflight_preserves_business_error_order():
    state = workspace()
    production = ShotProduction(state)

    with pytest.raises(ShotProductionError) as error:
        production.prepare_result_attachment("missing", state["revision"] - 1)
    assert error.value.code == "preproduction_conflict"

    with pytest.raises(ShotProductionError) as error:
        production.prepare_result_attachment("missing", state["revision"])
    assert (error.value.code, error.value.status) == ("preproduction_shot_missing", 404)

    state["shots"][0]["_resultVersions"] = [
        {"assetId": f"result-{index}", "signature": None, "reviewed": False}
        for index in range(100)
    ]
    with pytest.raises(ShotProductionError) as error:
        production.prepare_result_attachment("shot-a", state["revision"])
    assert (error.value.code, error.value.status) == ("preproduction_result_limit", 422)


@pytest.mark.parametrize("operation", [
    lambda production: production.attach_result("shot-a", "source"),
    lambda production: production.review_result("shot-a", "result"),
    lambda production: production.set_result_note("shot-a", "result", "备注"),
    lambda production: production.set_adoption_reason("shot-a", "result", "理由"),
    lambda production: production.remove_result("shot-a", "result"),
])
def test_candidate_mutations_reject_an_inflight_step(operation):
    state = workspace(nodes=[node("source-step", "reference", "asset:source", status="pending")])
    production = ShotProduction(state)
    production.attach_result("shot-a", "result")
    state["shots"][0]["resultAssetId"] = None
    production.prepare_run("shot-a", "source-step", "run-1")

    with pytest.raises(ShotProductionError) as error:
        operation(production)

    assert error.value.code == "preproduction_node_running"


def test_delivery_checks_consume_availability_facts_without_reading_files():
    state = workspace(nodes=[node("trim", "trim", "asset:source", {"start": 0, "end": 1})])
    production = ShotProduction(
        state,
        asset_available=lambda asset: asset["id"] != "source",
        artifact_available=lambda shot, step, artifact: False,
    )
    production.attach_result("shot-a", "result")

    checks = production.delivery_checks()

    assert {item["code"] for item in checks} == {
        "shot_asset_unavailable", "node_output_unavailable", "result_review_required",
    }
    assert all(item.get("shotId") == "shot-a" for item in checks)


def test_rerunning_a_step_invalidates_only_its_downstream_dependants():
    state = workspace(nodes=[
        node("source", "reference", "asset:source"),
        node("frame", "first_frame", "node:source"),
        node("independent", "reference", "asset:source"),
    ])
    production = ShotProduction(state)

    production.invalidate_descendants("shot-a", "source")

    assert [item["status"] for item in state["shots"][0]["nodes"]] == ["completed", "stale", "completed"]


def test_prepare_run_owns_readiness_queueing_and_source_selection():
    state = workspace(nodes=[
        node("source", "reference", "asset:source", status="pending"),
        node("frame", "first_frame", "node:source", status="pending"),
    ])
    production = ShotProduction(state)

    with pytest.raises(ShotProductionError) as error:
        production.prepare_run("shot-a", "frame", "run-1")
    assert error.value.code == "preproduction_input_not_ready"
    assert state["revision"] == 3

    source_run = production.prepare_run("shot-a", "source", "run-2")
    assert source_run.source.kind == "asset" and source_run.source.asset_id == "source"
    assert state["shots"][0]["nodes"][0]["status"] == "queued"
    assert state["revision"] == 4


def test_run_completion_updates_only_the_step_and_never_adopts_a_candidate():
    state = workspace(nodes=[node("source", "reference", "asset:source", status="pending")])
    production = ShotProduction(state)
    production.prepare_run("shot-a", "source", "run-1")

    assert production.start_run("shot-a", "source", "run-1") is not None
    assert production.complete_run("shot-a", "source", "run-1", [{"name": "reference.mp4", "url": "/artifact"}])

    step = state["shots"][0]["nodes"][0]
    assert step["status"] == "completed" and step["artifacts"] == [{"name": "reference.mp4", "url": "/artifact"}]
    assert state["shots"][0]["resultAssetId"] is None
    assert production.complete_run("shot-a", "source", "old-run", []) is False


def test_prepare_run_materializes_prompt_context_and_failure_state():
    state = workspace(nodes=[node("prompt", "prompt", "", {"text": "补充约束"}, status="pending")])
    production = ShotProduction(state)

    prepared = production.prepare_run("shot-a", "prompt", "run-1")

    assert prepared.params["text"] == "\n".join((
        "主题: 短片", "时长: 2", "画幅: 16:9", "镜头提示词: 原提示", "节点文本: 补充约束",
    ))
    assert production.start_run("shot-a", "prompt", "run-1") is not None
    assert production.fail_run("shot-a", "prompt", "run-1", "执行失败") is True
    step = state["shots"][0]["nodes"][0]
    assert (step["status"], step["error"], step["artifacts"]) == ("failed", "执行失败", [])
    assert production.fail_run("shot-a", "prompt", "old-run", "忽略") is False
