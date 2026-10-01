import hashlib
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from app.preproduction import PreproductionStore
from app.durable_runs import freeze_run_input
from app.project_assets import AssetFileUnavailableError, AssetInUseError, AssetReferencesUnavailableError, AssetStateConflict, ProjectAssets, ResultInstallationConflict


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
    store = PreproductionStore(tmp_path)
    store.save(PROJECT_ID, _state(asset))
    state = store.load(PROJECT_ID)
    assets = ProjectAssets(tmp_path)

    with pytest.raises(OSError):
        assets.update_metadata(
            PROJECT_ID, state, "asset-1", name="新名称", notes="新备注",
            save=lambda value: (_ for _ in ()).throw(OSError("disk full")),
        )

    assert state["revision"] == 0
    assert asset["name"] == "旧名称" and asset["notes"] == "旧备注"


def test_metadata_update_rejects_a_stale_caller_snapshot_without_losing_installed_result(tmp_path):
    """A stale metadata request must conflict instead of silently rebasing onto a new result."""
    source, generated = tmp_path / "source.mp4", tmp_path / "generated.mp4"
    source.write_bytes(b"source")
    generated.write_bytes(b"generated")
    assets = ProjectAssets(tmp_path)
    assets.add_many(PROJECT_ID, [(source, {
        "id": "source", "file": "source.mp4", "kind": "video", "role": "reference", "name": "旧素材",
    })])
    stale = PreproductionStore(tmp_path).load(PROJECT_ID)
    original_stale = deepcopy(stale)
    installed = assets.install_run_result(
        PROJECT_ID, source=generated, run_id="run-1", result_id="result-1", kind="video", name="生成结果",
        input_snapshot={}, domain_kind="test", domain_run_id="run-1",
    )

    with pytest.raises(AssetStateConflict):
        assets.update_metadata(
            PROJECT_ID, stale, "source", name="新名称", notes="已编辑",
            save=lambda value: PreproductionStore(tmp_path).save(PROJECT_ID, value),
        )

    saved = PreproductionStore(tmp_path).load(PROJECT_ID)
    assert {item["id"] for item in saved["assets"]} == {"source", installed["id"]}
    assert next(item for item in saved["assets"] if item["id"] == "source")["name"] == "旧素材"
    assert stale == original_stale
    assert assets.file(PROJECT_ID, installed).read_bytes() == b"generated"


def test_delete_rejects_a_stale_caller_snapshot_without_losing_any_file(tmp_path):
    """A stale delete must not remove a file or overwrite a result installed later."""
    source, generated = tmp_path / "source.mp4", tmp_path / "generated.mp4"
    source.write_bytes(b"source")
    generated.write_bytes(b"generated")
    assets = ProjectAssets(tmp_path)
    assets.add_many(PROJECT_ID, [(source, {
        "id": "source", "file": "source.mp4", "kind": "video", "role": "reference", "name": "旧素材",
    })])
    stale = PreproductionStore(tmp_path).load(PROJECT_ID)
    original_stale = deepcopy(stale)
    installed = assets.install_run_result(
        PROJECT_ID, source=generated, run_id="run-1", result_id="result-1", kind="video", name="生成结果",
        input_snapshot={}, domain_kind="test", domain_run_id="run-1",
    )

    with pytest.raises(AssetStateConflict):
        assets.delete(PROJECT_ID, stale, "source", save=lambda value: PreproductionStore(tmp_path).save(PROJECT_ID, value))

    saved = PreproductionStore(tmp_path).load(PROJECT_ID)
    assert {item["id"] for item in saved["assets"]} == {"source", installed["id"]}
    assert stale == original_stale
    assert PreproductionStore(tmp_path).path(PROJECT_ID, "assets", "source.mp4").read_bytes() == b"source"
    assert assets.file(PROJECT_ID, installed).read_bytes() == b"generated"


def test_concurrent_metadata_writes_use_one_workspace_revision_cas(tmp_path):
    """Two router instances holding revision 1 cannot both overwrite assets.json."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    assets = ProjectAssets(tmp_path)
    assets.add_many(PROJECT_ID, [(source, {
        "id": "source", "file": "source.mp4", "kind": "video", "role": "reference", "name": "原名称",
    })])
    first_state = PreproductionStore(tmp_path).load(PROJECT_ID)
    second_state = PreproductionStore(tmp_path).load(PROJECT_ID)

    def update(instance, state, name):
        return instance.update_metadata(
            PROJECT_ID, state, "source", name=name, notes="",
            save=lambda value: PreproductionStore(tmp_path).save(PROJECT_ID, value),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(update, ProjectAssets(tmp_path), first_state, "版本一"),
            pool.submit(update, ProjectAssets(tmp_path), second_state, "版本二"),
        ]
        outcomes = [future.exception() for future in futures]

    assert sum(error is None for error in outcomes) == 1
    assert sum(isinstance(error, AssetStateConflict) for error in outcomes) == 1
    saved = PreproductionStore(tmp_path).load(PROJECT_ID)
    assert saved["revision"] == 2
    assert saved["assets"][0]["name"] in {"版本一", "版本二"}


def test_install_run_result_is_idempotent_and_keeps_immutable_provenance(tmp_path):
    """Removing the run/result identity or input fingerprint must break this test."""
    source = tmp_path / "rendered.mp4"
    source.write_bytes(b"real-media-result")
    assets = ProjectAssets(tmp_path)

    installed = assets.install_run_result(
        PROJECT_ID,
        source=source,
        run_id="run-1",
        result_id="result-1",
        kind="video",
        name="生成片段",
        provider="local-renderer",
        processing_method="ffmpeg",
        input_snapshot={"script": "第一版", "model": "local"},
        domain_kind="test", domain_run_id="run-1",
        media={"duration": 2},
    )
    repeated = assets.install_run_result(
        PROJECT_ID,
        source=source,
        run_id="run-1",
        result_id="result-1",
        kind="video",
        name="另一个显示名不能覆盖",
        provider="local-renderer",
        processing_method="ffmpeg",
        input_snapshot={"script": "第一版", "model": "local"},
        domain_kind="test", domain_run_id="run-1",
        media={"duration": 2},
    )

    assert repeated == installed
    assert installed["id"].startswith("result-")
    assert installed["assetVersionId"] == installed["id"] + "-v1"
    assert installed["sourceRunId"] == "run-1"
    assert installed["sourceResultId"] == "result-1"
    assert installed["provider"] == "local-renderer"
    assert installed["processingMethod"] == "ffmpeg"
    assert installed["inputSnapshot"] == {"model": "local", "script": "第一版"}
    assert installed["duration"] == 2
    assert len(installed["inputFingerprint"]) == 64
    assert len(installed["sha256"]) == 64
    assert installed["name"] == "生成片段"
    assert assets.file(PROJECT_ID, installed).read_bytes() == b"real-media-result"
    assert len(assets.records(PROJECT_ID)) == 1


def test_install_run_result_rejects_a_tampered_installed_version(tmp_path):
    """Ignoring the installed file's hash would silently reuse corrupted media."""
    source = tmp_path / "rendered.mp4"
    source.write_bytes(b"original-media")
    assets = ProjectAssets(tmp_path)
    installed = assets.install_run_result(
        PROJECT_ID, source=source, run_id="run-1", result_id="result-1", kind="video", name="生成片段",
        input_snapshot={"script": "第一版"}, domain_kind="test", domain_run_id="run-1", media={"duration": 2},
    )
    assets.file(PROJECT_ID, installed).write_bytes(b"tampered-media")

    with pytest.raises(AssetFileUnavailableError):
        assets.file(PROJECT_ID, installed)

    with pytest.raises(ResultInstallationConflict):
        assets.install_run_result(
            PROJECT_ID, source=source, run_id="run-1", result_id="result-1", kind="video", name="生成片段",
            input_snapshot={"script": "第一版"}, domain_kind="test", domain_run_id="run-1", media={"duration": 2},
        )


def test_install_run_result_rejects_conflicting_immutable_provider_facts(tmp_path):
    source = tmp_path / "rendered.mp4"
    source.write_bytes(b"media")
    assets = ProjectAssets(tmp_path)
    assets.install_run_result(
        PROJECT_ID, source=source, run_id="run-1", result_id="result-1", kind="video", name="结果",
        provider="provider-a", processing_method="ffmpeg", input_snapshot={"input": 1}, domain_kind="test", domain_run_id="run-1", media={"duration": 2},
    )

    with pytest.raises(ResultInstallationConflict):
        assets.install_run_result(
            PROJECT_ID, source=source, run_id="run-1", result_id="result-1", kind="video", name="结果",
            provider="provider-b", processing_method="ffmpeg", input_snapshot={"input": 1}, domain_kind="test", domain_run_id="run-1", media={"duration": 2},
        )


def test_common_run_result_identity_rejects_conflicting_domain_provenance(tmp_path):
    source = tmp_path / "result.mp4"
    source.write_bytes(b"media")
    assets = ProjectAssets(tmp_path)
    first = assets.install_run_result(
        PROJECT_ID, source=source, run_id="canvas-run", common_run_id="common-1", result_id="result-1",
        domain_kind="canvas", domain_run_id="canvas-run", kind="video", name="结果", input_snapshot={"input": 1},
    )
    repeated = assets.install_run_result(
        PROJECT_ID, source=source, run_id="canvas-run", common_run_id="common-1", result_id="result-1",
        domain_kind="canvas", domain_run_id="canvas-run", kind="video", name="结果", input_snapshot={"input": 1},
    )
    with pytest.raises(ResultInstallationConflict):
        assets.install_run_result(
            PROJECT_ID, source=source, run_id="other-run", common_run_id="common-1", result_id="result-1",
            domain_kind="remix", domain_run_id="other-run", kind="video", name="结果", input_snapshot={"input": 1},
        )

    assert repeated == first
    assert first["sourceCommonRunId"] == "common-1"
    assert first["sourceDomainKind"] == "canvas"
    assert len(assets.records(PROJECT_ID)) == 1


def test_different_results_with_the_same_display_name_keep_distinct_files_and_versions(tmp_path):
    """Using a display name as an installation key would overwrite the first result."""
    first_file, second_file = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first_file.write_bytes(b"first-result")
    second_file.write_bytes(b"second-result")
    assets = ProjectAssets(tmp_path)

    first = assets.install_run_result(
        PROJECT_ID, source=first_file, run_id="run-1", result_id="result-1", kind="video", name="同名结果",
        input_snapshot={"input": 1}, domain_kind="test", domain_run_id="run-1", media={"duration": 2},
    )
    second = assets.install_run_result(
        PROJECT_ID, source=second_file, run_id="run-2", result_id="result-2", kind="video", name="同名结果",
        input_snapshot={"input": 2}, domain_kind="test", domain_run_id="run-2", media={"duration": 2},
    )

    assert first["name"] == second["name"] == "同名结果"
    assert first["id"] != second["id"] and first["file"] != second["file"]
    assert first["assetVersionId"] != second["assetVersionId"]
    assert assets.file(PROJECT_ID, first).read_bytes() == b"first-result"
    assert assets.file(PROJECT_ID, second).read_bytes() == b"second-result"


def test_legacy_asset_without_result_truth_fields_stays_readable_and_public_unchanged(tmp_path):
    """Adding result provenance must not rewrite or hide old project assets."""
    legacy = {"id": "legacy-1", "name": "旧素材", "kind": "video", "role": "reference", "file": "legacy-1.mp4"}
    store = PreproductionStore(tmp_path)
    store.save(PROJECT_ID, _state(legacy))
    path = store.path(PROJECT_ID, "assets", "legacy-1.mp4")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"legacy-media")

    assets = ProjectAssets(tmp_path)

    assert assets.records(PROJECT_ID) == [legacy]
    assert assets.public(PROJECT_ID) == [{
        "id": "legacy-1", "name": "旧素材", "kind": "video", "role": "reference",
        "url": "/api/projects/p1/preproduction/assets/legacy-1/file",
    }]
    assert assets.file(PROJECT_ID, "legacy-1").read_bytes() == b"legacy-media"


def test_legacy_image_result_with_run_fields_but_without_hash_or_version_stays_readable(tmp_path):
    legacy = {"id": "old-image", "name": "旧图片结果", "kind": "image", "role": "reference", "file": "old-image.png",
              "sourceRunId": "image-run", "sourceResultId": "image-result"}
    store = PreproductionStore(tmp_path)
    store.save(PROJECT_ID, _state(legacy))
    path = store.path(PROJECT_ID, "assets", "old-image.png")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"legacy-image")

    asset = ProjectAssets(tmp_path)

    assert asset.public(PROJECT_ID)[0]["sourceRunId"] == "image-run"
    assert asset.file(PROJECT_ID, "old-image").read_bytes() == b"legacy-image"


def test_legacy_result_without_domain_fields_reinstalls_idempotently(tmp_path):
    source = tmp_path / "legacy.mp4"
    source.write_bytes(b"legacy-result")
    frozen = freeze_run_input({"input": 1})
    legacy = {"id": "old-result", "name": "旧结果", "kind": "video", "role": "reference", "file": "old-result.mp4",
              "sha256": hashlib.sha256(b"legacy-result").hexdigest(), "sourceRunId": "run-1", "sourceResultId": "result-1",
              "inputSnapshot": frozen.snapshot, "inputFingerprint": frozen.fingerprint, "assetVersionId": "old-result-v1"}
    store = PreproductionStore(tmp_path)
    store.save(PROJECT_ID, _state(legacy))
    target = store.path(PROJECT_ID, "assets", "old-result.mp4")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"legacy-result")

    repeated = ProjectAssets(tmp_path).install_run_result(
        PROJECT_ID, source=source, run_id="run-1", result_id="result-1", kind="video", name="新名称不覆盖",
        input_snapshot={"input": 1}, domain_kind="legacy", domain_run_id="run-1",
    )

    assert repeated == legacy


@pytest.mark.parametrize(("kind", "suffix"), [("image", ".png"), ("video", ".mp4"), ("audio", ".wav")])
def test_install_run_result_accepts_each_controlled_media_kind(tmp_path, kind, suffix):
    """Narrowing installation to video would strand image or audio results outside project assets."""
    source = tmp_path / ("result" + suffix)
    source.write_bytes(b"media")

    installed = ProjectAssets(tmp_path).install_run_result(
        PROJECT_ID, source=source, run_id="run-" + kind, result_id="result-1", kind=kind,
        name=kind, input_snapshot={"kind": kind}, domain_kind="test", domain_run_id="run-" + kind,
    )

    assert installed["kind"] == kind
    assert installed["file"].endswith(suffix)


def test_two_project_asset_instances_install_results_without_losing_a_concurrent_record(tmp_path, monkeypatch):
    """Separate router instances used to load the same state then overwrite one another."""
    first_file, second_file = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first_file.write_bytes(b"first")
    second_file.write_bytes(b"second")
    first_loaded, second_started, guard = Event(), Event(), Lock()
    original_load = PreproductionStore.load
    loads = 0

    def synchronized_load(store, project_id):
        nonlocal loads
        state = original_load(store, project_id)
        with guard:
            loads += 1
            position = loads
        if position == 1:
            first_loaded.set()
            second_started.wait(timeout=0.15)
        elif position == 2:
            second_started.set()
        return state

    monkeypatch.setattr(PreproductionStore, "load", synchronized_load)
    first, second = ProjectAssets(tmp_path), ProjectAssets(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(first.install_run_result, PROJECT_ID, source=first_file, run_id="run-1", result_id="result-1",
                        kind="video", name="first", input_snapshot={"value": 1}, domain_kind="test", domain_run_id="run-1"),
            pool.submit(second.install_run_result, PROJECT_ID, source=second_file, run_id="run-2", result_id="result-2",
                        kind="video", name="second", input_snapshot={"value": 2}, domain_kind="test", domain_run_id="run-2"),
        ]
        [future.result() for future in futures]

    assert {asset["name"] for asset in ProjectAssets(tmp_path).records(PROJECT_ID)} == {"first", "second"}


def test_add_many_and_result_install_share_the_project_asset_write_lock(tmp_path, monkeypatch):
    """A voiceover batch and a generated result must not overwrite each other's state save."""
    generated_file, voice_file = tmp_path / "generated.mp4", tmp_path / "voice.wav"
    generated_file.write_bytes(b"generated")
    voice_file.write_bytes(b"voice")
    first_loaded, second_started, guard = Event(), Event(), Lock()
    original_load = PreproductionStore.load
    loads = 0

    def synchronized_load(store, project_id):
        nonlocal loads
        state = original_load(store, project_id)
        with guard:
            loads += 1
            position = loads
        if position == 1:
            first_loaded.set(); second_started.wait(timeout=0.15)
        elif position == 2:
            second_started.set()
        return state

    monkeypatch.setattr(PreproductionStore, "load", synchronized_load)
    generated, voices = ProjectAssets(tmp_path), ProjectAssets(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(generated.install_run_result, PROJECT_ID, source=generated_file, run_id="run-1", result_id="result-1",
                            kind="video", name="生成", input_snapshot={}, domain_kind="test", domain_run_id="run-1")
        second = pool.submit(voices.add_many, PROJECT_ID, [(voice_file, {
            "id": "voice-1", "file": "voice-1.wav", "kind": "audio", "role": "audio", "name": "配音",
        })])
        first.result(); second.result()

    assert {asset["name"] for asset in ProjectAssets(tmp_path).records(PROJECT_ID)} == {"生成", "配音"}


def test_new_result_requires_explicit_domain_or_common_identity(tmp_path):
    source = tmp_path / "result.mp4"
    source.write_bytes(b"media")
    assets = ProjectAssets(tmp_path)
    with pytest.raises(ValueError):
        assets.install_run_result(PROJECT_ID, source=source, run_id="run", result_id="result", kind="video", name="结果", input_snapshot={})
    first = assets.install_run_result(PROJECT_ID, source=source, run_id="run", result_id="result", kind="video", name="一", input_snapshot={}, domain_kind="canvas", domain_run_id="run")
    second = assets.install_run_result(PROJECT_ID, source=source, run_id="run", result_id="result", kind="video", name="二", input_snapshot={}, domain_kind="remix", domain_run_id="run")
    assert first["id"] != second["id"]
