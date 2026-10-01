import json
import os
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.timeline_api import create_timeline_router
from app.timeline import TimelineStore


PROJECT_ID = "p1"
BASE = "/api/projects/p1/timeline"


class Queue:
    def __init__(self):
        self.jobs = []

    def submit(self, kind, project_id, handler):
        self.jobs.append((project_id, handler))
        return True

    def run(self):
        project_id, handler = self.jobs.pop(0)
        handler(project_id)


class TrackingLock:
    def __init__(self):
        self.entries = 0

    def __enter__(self):
        self.entries += 1

    def __exit__(self, *_args):
        return False


def _asset_root(tmp_path):
    root = tmp_path / "project-files" / PROJECT_ID / "preproduction"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    if not (root / "assets" / "video.mp4").exists():
        (root / "assets" / "video.mp4").write_bytes(b"video")
    if not (root / "assets" / "audio.wav").exists():
        (root / "assets" / "audio.wav").write_bytes(b"audio")
    (root / "state.json").write_text(json.dumps({
        "schemaVersion": 1, "revision": 1, "brief": {}, "shots": [],
        "assets": [
            {"id": "video", "name": "画面", "kind": "video", "file": "video.mp4", "duration": 10.0, "width": 64, "height": 48},
            {"id": "audio", "name": "声音", "kind": "audio", "file": "audio.wav", "duration": 10.0},
        ],
    }), encoding="utf-8")


def _workspace(revision=0, tracks=None):
    return {
        "revision": revision,
        "settings": {"width": 1280, "height": 720, "fps": 30},
        "tracks": tracks if tracks is not None else [{
            "id": "v1", "name": "视频", "kind": "video", "muted": False, "hidden": False,
            "clips": [{"id": "clip1", "assetId": "video", "start": 0, "inPoint": 0, "duration": 2, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}],
        }],
    }


def setup(tmp_path, renderer=None, source_lock=None):
    _asset_root(tmp_path)
    queue = Queue()
    app = FastAPI()
    app.include_router(create_timeline_router(tmp_path, lambda project_id: SimpleNamespace(id=project_id) if project_id == PROJECT_ID else None, queue, renderer=renderer, source_lock=source_lock))
    return TestClient(app), queue


def test_workspace_reads_preproduction_assets_and_uses_revision_cas(tmp_path):
    client, _ = setup(tmp_path)
    initial = client.get(BASE)
    assert initial.status_code == 200
    assert [asset["id"] for asset in initial.json()["assets"]] == ["video", "audio"]
    assert "file" not in initial.json()["assets"][0]

    saved = client.put(BASE, json=_workspace())
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert client.put(BASE, json=_workspace()).status_code == 409


def test_legacy_timeline_run_is_upgraded_once_with_recoverable_backup(tmp_path):
    store = TimelineStore(tmp_path)
    path = store.path(PROJECT_ID, "state.json")
    path.parent.mkdir(parents=True)
    legacy = {
        "schemaVersion": 1,
        "revision": 1,
        "settings": {"width": 1280, "height": 720, "fps": 30},
        "tracks": [],
        "runs": [{
            "id": "legacy-run",
            "revision": 1,
            "format": "preview",
            "status": "completed",
            "error": None,
            "snapshot": {"settings": {"width": 1280, "height": 720, "fps": 30}, "tracks": []},
            "sources": {},
            "output": "legacy-run.mp4",
        }],
    }
    original = json.dumps(legacy, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(original)

    migrated = store.load(PROJECT_ID)

    run = migrated["runs"][0]
    assert migrated["runContractVersion"] == 1
    assert run["commonRunId"] == "timeline:legacy-run"
    assert run["domainKind"] == "timeline.render"
    assert run["inputSnapshot"] == legacy["runs"][0]["snapshot"]
    assert len(run["inputFingerprint"]) == 64
    assert run["idempotencyKey"] is None
    assert run["retryOf"] is None
    assert run["attempt"] == 1
    assert run["logs"] == []
    backup = store.path(PROJECT_ID, "state.json.run-contract-v1.bak")
    assert backup.read_bytes() == original
    persisted = path.read_bytes()
    assert store.load(PROJECT_ID) == migrated
    assert path.read_bytes() == persisted


def test_legacy_run_migration_keeps_original_state_when_atomic_replacement_fails(tmp_path):
    initial_store = TimelineStore(tmp_path)
    path = initial_store.path(PROJECT_ID, "state.json")
    path.parent.mkdir(parents=True)
    legacy = {
        "schemaVersion": 1, "revision": 0,
        "settings": {"width": 1280, "height": 720, "fps": 30}, "tracks": [],
        "runs": [{"id": "legacy-run", "revision": 0, "format": "preview", "status": "queued", "error": None,
                  "snapshot": {"settings": {"width": 1280, "height": 720, "fps": 30}, "tracks": []}, "sources": {}}],
    }
    original = json.dumps(legacy, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(original)

    def fail_state_replacement(source, destination):
        if destination == str(path):
            raise OSError("simulated replacement failure")
        os.replace(source, destination)

    with pytest.raises(OSError, match="simulated replacement failure"):
        TimelineStore(tmp_path, replace=fail_state_replacement).load(PROJECT_ID)

    backup = initial_store.path(PROJECT_ID, "state.json.run-contract-v1.bak")
    assert path.read_bytes() == original
    assert backup.read_bytes() == original
    legacy["revision"] = 1
    updated = json.dumps(legacy, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(updated)
    assert TimelineStore(tmp_path).load(PROJECT_ID)["runs"][0]["commonRunId"] == "timeline:legacy-run"
    assert backup.read_bytes() == updated


def test_rejects_overlapping_video_clips_and_wrong_asset_track_kind(tmp_path):
    client, _ = setup(tmp_path)
    overlapping = _workspace(tracks=[{
        "id": "v1", "name": "视频", "kind": "video", "muted": False, "hidden": False,
        "clips": [
            {"id": "one", "assetId": "video", "start": 0, "inPoint": 0, "duration": 2, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
            {"id": "two", "assetId": "video", "start": 1, "inPoint": 0, "duration": 2, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
        ],
    }])
    assert client.put(BASE, json=overlapping).status_code == 422
    wrong_kind = _workspace(tracks=[{
        "id": "v1", "name": "视频", "kind": "video", "muted": False, "hidden": False,
        "clips": [{"id": "one", "assetId": "audio", "start": 0, "inPoint": 0, "duration": 1, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}],
    }])
    assert client.put(BASE, json=wrong_kind).status_code == 422


def test_run_uses_saved_snapshot_and_publishes_only_completed_output(tmp_path):
    received = []

    def renderer(timeline, sources, output, **kwargs):
        received.append((timeline, sources))
        assert sources["video"].read_bytes() == b"video"
        output.write_bytes(b"rendered")

    client, queue = setup(tmp_path, renderer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    submitted = client.post(BASE + "/runs", json={"revision": 1, "format": "preview"})
    assert submitted.status_code == 202
    assert submitted.json()["revision"] == 1
    run_id = submitted.json()["runs"][0]["id"]
    assert client.get(BASE + "/runs/" + run_id + "/output").status_code == 409
    (tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "video.mp4").write_bytes(b"changed")
    queue.run()
    completed = client.get(BASE).json()["runs"][0]
    assert completed["status"] == "completed"
    assert completed["revision"] == 1
    assert received[0][0]["tracks"][0]["clips"][0]["assetId"] == "video"
    assert client.get(BASE + "/runs/" + run_id + "/output").content == b"rendered"


def test_run_contract_exposes_common_identity_and_reuses_matching_idempotency_key(tmp_path):
    client, queue = setup(tmp_path, lambda *_args, **_kwargs: None)
    assert client.put(BASE, json=_workspace()).status_code == 200

    first = client.post(BASE + "/runs", json={"revision": 1, "format": "preview", "idempotencyKey": "preview-v1"})
    assert first.status_code == 202, first.text
    run = first.json()["runs"][0]
    assert run["commonRunId"]
    assert run["domainKind"] == "timeline.render"
    assert run["inputSnapshot"]["tracks"][0]["id"] == "v1"
    assert len(run["inputFingerprint"]) == 64
    assert run["idempotencyKey"] == "preview-v1"
    assert run["retryOf"] is None
    assert client.get(BASE + "/runs/" + run["id"]).json()["commonRunId"] == run["commonRunId"]

    repeated = client.post(BASE + "/runs", json={"revision": 1, "format": "preview", "idempotencyKey": "preview-v1"})
    assert repeated.status_code == 202
    assert [item["id"] for item in repeated.json()["runs"]] == [run["id"]]
    assert len(queue.jobs) == 1
    conflict = client.post(BASE + "/runs", json={"revision": 1, "format": "mp4", "idempotencyKey": "preview-v1"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "timeline_idempotency_conflict"


def test_retry_creates_linked_domain_run_and_is_idempotent(tmp_path):
    client, queue = setup(tmp_path, lambda *_args, **_kwargs: None)
    assert client.put(BASE, json=_workspace()).status_code == 200
    original = client.post(BASE + "/runs", json={"revision": 1, "format": "preview"}).json()["runs"][0]
    assert client.get(BASE + "/runs/" + original["commonRunId"]).json()["id"] == original["id"]
    assert client.post(BASE + "/runs/" + original["commonRunId"] + "/cancel", json={}).status_code == 200

    retried = client.post(BASE + "/runs/" + original["commonRunId"] + "/retry", json={"idempotencyKey": "retry-original-1"})
    assert retried.status_code == 202, retried.text
    retry = retried.json()["runs"][-1]
    assert retry["id"] != original["id"]
    assert retry["commonRunId"] != original["commonRunId"]
    assert retry["retryOf"] == original["id"]
    assert retry["inputFingerprint"] == original["inputFingerprint"]
    assert retry["idempotencyKey"] == "retry-original-1"
    repeated = client.post(BASE + "/runs/" + original["id"] + "/retry", json={"idempotencyKey": "retry-original-1"})
    assert repeated.status_code == 202
    assert [item["id"] for item in repeated.json()["runs"]] == [original["id"], retry["id"]]
    assert len(queue.jobs) == 2
    blocked = client.post(BASE + "/runs/" + original["id"] + "/retry", json={"idempotencyKey": "retry-original-2"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "timeline_run_active"
    assert len(queue.jobs) == 2


def test_retry_holds_the_project_source_lock_while_copying_and_queueing(tmp_path):
    source_lock = TrackingLock()
    client, _ = setup(tmp_path, lambda *_args, **_kwargs: None, source_lock)
    assert client.put(BASE, json=_workspace()).status_code == 200
    original = client.post(BASE + "/runs", json={"revision": 1, "format": "preview"}).json()["runs"][0]
    assert client.post(BASE + "/runs/" + original["id"] + "/cancel", json={}).status_code == 200
    source_lock.entries = 0

    retried = client.post(BASE + "/runs/" + original["id"] + "/retry", json={"idempotencyKey": "retry-with-lock"})

    assert retried.status_code == 202
    assert source_lock.entries == 1


def test_common_run_id_cleanup_removes_files_and_the_domain_record(tmp_path):
    def renderer(_timeline, _sources, output, **_kwargs):
        output.write_bytes(b"rendered")

    client, queue = setup(tmp_path, renderer)
    state = client.put(BASE, json=_workspace()).json()
    run = client.post(BASE + "/runs", json={"revision": state["revision"], "format": "preview"}).json()["runs"][0]
    queue.run()
    completed = client.get(BASE + "/runs/" + run["commonRunId"]).json()
    assert completed["status"] == "completed"
    assert client.get(BASE + "/runs/" + run["commonRunId"] + "/cleanup-preview").status_code == 200

    cleaned = client.post(BASE + "/runs/" + run["commonRunId"] + "/delete", json={"revision": state["revision"]})

    assert cleaned.status_code == 200
    assert cleaned.json()["runs"] == []
    assert not (tmp_path / "project-files" / PROJECT_ID / "timeline" / "runs" / run["id"]).exists()
    assert not (tmp_path / "project-files" / PROJECT_ID / "timeline" / "outputs" / (run["id"] + ".mp4")).exists()


def test_cancelled_or_failed_run_never_publishes_partial_output(tmp_path):
    def broken(timeline, sources, output, **kwargs):
        output.write_bytes(b"partial")
        raise RuntimeError("/private/path")

    client, queue = setup(tmp_path, broken)
    client.put(BASE, json=_workspace())
    run_id = client.post(BASE + "/runs", json={"revision": 1, "format": "mp4"}).json()["runs"][-1]["id"]
    assert client.post(BASE + "/runs/" + run_id + "/cancel", json={}).status_code == 200
    queue.run()
    assert client.get(BASE).json()["runs"][0]["status"] == "cancelled"
    assert client.get(BASE + "/runs/" + run_id + "/output").status_code == 409

    run_id = client.post(BASE + "/runs", json={"revision": 1, "format": "mp4"}).json()["runs"][-1]["id"]
    queue.run()
    failed = client.get(BASE).json()["runs"][-1]
    assert failed["status"] == "failed"
    assert "/private" not in failed["error"]
    assert client.get(BASE + "/runs/" + run_id + "/output").status_code == 409


def test_running_cancel_is_acknowledged_by_worker_and_retains_late_output_privately(tmp_path):
    client = None

    def renderer(_timeline, _sources, output, **_kwargs):
        cancelled = client.post(BASE + "/runs/" + run_id + "/cancel", json={})
        assert cancelled.status_code == 200
        assert cancelled.json()["runs"][0]["status"] == "cancel_requested"
        output.write_bytes(b"late-output")

    client, queue = setup(tmp_path, renderer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    run_id = client.post(BASE + "/runs", json={"revision": 1, "format": "preview"}).json()["runs"][0]["id"]
    queue.run()

    run = client.get(BASE + "/runs/" + run_id).json()
    assert run["status"] == "cancelled"
    assert run["logs"][-1]["event"] == "retained_not_adopted"
    assert client.get(BASE + "/runs/" + run_id + "/output").status_code == 409
    assert (tmp_path / "project-files" / PROJECT_ID / "timeline" / "runs" / run_id / "attempt-1.mp4").read_bytes() == b"late-output"


def test_running_cancel_retains_partial_output_under_attempt_name_when_renderer_raises(tmp_path):
    client = None

    def renderer(_timeline, _sources, output, **_kwargs):
        cancelled = client.post(BASE + "/runs/" + run_id + "/cancel", json={})
        assert cancelled.status_code == 200
        output.write_bytes(b"partial-late-output")
        raise RuntimeError("renderer acknowledged cancellation")

    client, queue = setup(tmp_path, renderer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    run_id = client.post(BASE + "/runs", json={"revision": 1, "format": "preview"}).json()["runs"][0]["id"]
    queue.run()

    run = client.get(BASE + "/runs/" + run_id).json()
    assert run["status"] == "cancelled"
    assert run["logs"][-1] == {"event": "retained_not_adopted", "attempt": 1, "reason": "cancelled"}
    retained = tmp_path / "project-files" / PROJECT_ID / "timeline" / "runs" / run_id / "attempt-1.mp4"
    assert retained.read_bytes() == b"partial-late-output"
    assert not (retained.parent / "output.mp4").exists()


def test_late_failed_attempt_cannot_fail_or_overwrite_the_recovered_attempt(tmp_path):
    started, release = Event(), Event()

    def renderer(_timeline, _sources, output, **_kwargs):
        output.write_bytes(b"attempt-one")
        started.set()
        assert release.wait(3)
        raise RuntimeError("late attempt failed")

    client, queue = setup(tmp_path, renderer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    run_id = client.post(BASE + "/runs", json={"revision": 1, "format": "preview"}).json()["runs"][0]["id"]
    worker = Thread(target=queue.run)
    worker.start()
    try:
        assert started.wait(3)
        store = TimelineStore(tmp_path)
        state = store.load(PROJECT_ID)
        state["runs"][0].update(status="running", attempt=2)
        store.save(PROJECT_ID, state)
    finally:
        release.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    run = client.get(BASE + "/runs/" + run_id).json()
    assert run["status"] == "running"
    assert run["attempt"] == 2
    assert run["logs"][-1] == {"event": "retained_not_adopted", "attempt": 1, "reason": "superseded_attempt"}
    run_dir = tmp_path / "project-files" / PROJECT_ID / "timeline" / "runs" / run_id
    assert (run_dir / "attempt-1.mp4").read_bytes() == b"attempt-one"
    assert not (run_dir / "attempt-2.staging.mp4").exists()

def test_missing_frozen_source_keeps_the_existing_safe_render_error(tmp_path):
    client, queue = setup(tmp_path, lambda *_args, **_kwargs: None)
    assert client.put(BASE, json=_workspace()).status_code == 200
    submitted = client.post(BASE + "/runs", json={"revision": 1, "format": "preview"})
    run_id = submitted.json()["runs"][-1]["id"]
    frozen_source = tmp_path / "project-files" / PROJECT_ID / "timeline" / "runs" / run_id / "sources" / "video.mp4"
    frozen_source.unlink()

    queue.run()

    run = client.get(BASE).json()["runs"][-1]
    assert run["status"] == "failed"
    assert run["error"] == "渲染失败，请检查时间线和素材。"


def test_rejects_symlinked_preproduction_source_and_allows_audible_audio_only_wav(tmp_path):
    client, _ = setup(tmp_path)
    source = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "video.mp4"
    source.unlink()
    source.symlink_to(tmp_path / "outside.mp4")
    assert client.put(BASE, json=_workspace()).status_code == 200
    assert client.post(BASE + "/runs", json={"revision": 1, "format": "mp4"}).status_code == 409

    audio_only = _workspace(revision=1, tracks=[{
        "id": "a1", "name": "声音", "kind": "audio", "muted": False, "hidden": False,
        "clips": [{"id": "a", "assetId": "audio", "start": 0, "inPoint": 0, "duration": 1, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}],
    }])
    assert client.put(BASE, json=audio_only).status_code == 200
    accepted = client.post(BASE + "/runs", json={"revision": 2, "format": "wav"})
    assert accepted.status_code == 202
    assert accepted.json()["runs"][-1]["format"] == "wav"
    assert client.post(BASE + "/runs/" + accepted.json()["runs"][-1]["id"] + "/cancel", json={}).status_code == 200

    muted_audio = _workspace(revision=2, tracks=[{
        "id": "a1", "name": "声音", "kind": "audio", "muted": True, "hidden": False,
        "clips": [{"id": "a", "assetId": "audio", "start": 0, "inPoint": 0, "duration": 1, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}],
    }])
    assert client.put(BASE, json=muted_audio).status_code == 200
    assert client.post(BASE + "/runs", json={"revision": 3, "format": "wav"}).status_code == 422


def test_restart_resumes_unfinished_run_with_same_identity_and_new_attempt(tmp_path):
    client, _, = setup(tmp_path)
    client.put(BASE, json=_workspace())
    run_id = client.post(BASE + "/runs", json={"revision": 1, "format": "mp4"}).json()["runs"][-1]["id"]
    original = client.get(BASE + "/runs/" + run_id).json()
    restarted, recovery_queue = setup(tmp_path)
    run = restarted.get(BASE).json()["runs"][0]
    assert run["id"] == run_id and run["commonRunId"] == original["commonRunId"]
    assert run["status"] == "queued"
    assert run["attempt"] == 2
    assert run["recovery"]["reason"] == "restart_resumed"
    assert run["logs"][-1] == {"event": "recovered", "attempt": 2, "reason": "restart_resumed"}
    assert len(recovery_queue.jobs) == 1


def test_adjacent_decimal_clips_are_accepted_but_real_overlap_is_rejected(tmp_path):
    client, _ = setup(tmp_path)
    body = _workspace()
    clip = body['tracks'][0]['clips'][0]
    clip.update(start=.1, duration=.2)
    body['tracks'][0]['clips'].append({**clip, 'id': 'clip2', 'start': .3})
    result = client.put(BASE, json=body)
    assert result.status_code == 200, result.text
    body['revision'] = result.json()['revision']
    body['tracks'][0]['clips'][1]['start'] = .29
    assert client.put(BASE, json=body).status_code == 422


def test_import_shot_results_appends_in_order_without_overwriting_or_duplication(tmp_path):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/state.json'
    preparation = json.loads(path.read_text())
    preparation['shots'] = [{"id": "s1", "duration": 1, "resultAssetId": "video"}, {"id": "s2", "duration": 2, "resultAssetId": "video"}]
    path.write_text(json.dumps(preparation))
    original = client.put(BASE, json=_workspace()).json()
    result = client.post(BASE + '/import-shot-results', json={"revision": original['revision'], "preproductionRevision": 1})
    assert result.status_code == 200, result.text
    state = result.json()
    assert state['tracks'][0] == original['tracks'][0]
    assert [c['start'] for c in state['tracks'][1]['clips']] == [2, 3]
    assert [c['duration'] for c in state['tracks'][1]['clips']] == [1, 2]
    repeated = client.post(BASE + '/import-shot-results', json={"revision": state['revision'], "preproductionRevision": 1})
    assert repeated.json() == state
    assert client.post(BASE + '/import-shot-results', json={"revision": 0, "preproductionRevision": 1}).status_code == 409
    assert client.post(BASE + '/import-shot-results', json={"revision": state['revision'], "preproductionRevision": 0}).status_code == 409


def test_import_requires_video_results_long_enough_for_every_shot(tmp_path):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/state.json'
    preparation = json.loads(path.read_text())
    for result_id, duration in [(None, 1), ('audio', 1), ('video', 11)]:
        preparation['shots'] = [{"id": "s1", "duration": duration, "resultAssetId": result_id}]
        path.write_text(json.dumps(preparation))
        result = client.post(BASE + '/import-shot-results', json={"revision": 0, "preproductionRevision": 1})
        assert result.status_code == 409
        assert client.get(BASE).json()['tracks'] == []


def test_shot_result_dedup_ignores_brief_revision_and_survives_timeline_edits(tmp_path):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/state.json'
    prep = json.loads(path.read_text())
    prep['shots'] = [{"id": "s1", "duration": 1, "resultAssetId": "video"}, {"id": "s2", "duration": 2, "resultAssetId": "video"}]
    path.write_text(json.dumps(prep))
    state = client.post(BASE + '/import-shot-results', json={"revision": 0, "preproductionRevision": 1}).json()
    state['tracks'][0]['clips'][0]['volume'] = .5
    saved = client.put(BASE, json={key: state[key] for key in ('revision', 'settings', 'tracks')}).json()
    prep['revision'] = 2
    prep['brief']['theme'] = '文字修改'
    path.write_text(json.dumps(prep))
    repeated = client.post(BASE + '/import-shot-results', json={"revision": saved['revision'], "preproductionRevision": 2})
    assert repeated.json() == saved
    prep['shots'].reverse()
    prep['revision'] = 3
    path.write_text(json.dumps(prep))
    changed = client.post(BASE + '/import-shot-results', json={"revision": saved['revision'], "preproductionRevision": 3}).json()
    assert len(changed['tracks']) == 2
    assert changed['tracks'][0] == saved['tracks'][0]
    assert [clip['duration'] for clip in changed['tracks'][1]['clips']] == [2, 1]


def test_import_legacy_track_and_changed_result_content(tmp_path):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/state.json'
    prep = json.loads(path.read_text())
    prep['revision'] = 2
    prep['shots'] = [{"id": "s1", "duration": 2, "resultAssetId": "video"}]
    path.write_text(json.dumps(prep))
    legacy = _workspace()
    legacy['tracks'][0]['id'] = 'shot-results-1'
    old = client.put(BASE, json=legacy).json()
    assert client.post(BASE + '/import-shot-results', json={"revision": old['revision'], "preproductionRevision": 2}).json() == old
    prep['shots'][0]['duration'] = 3
    prep['revision'] = 3
    path.write_text(json.dumps(prep))
    changed = client.post(BASE + '/import-shot-results', json={"revision": old['revision'], "preproductionRevision": 3}).json()
    assert len(changed['tracks']) == 2
    # Delete the new imported track: importing the same content should work again.
    changed['tracks'].pop()
    saved = client.put(BASE, json={key: changed[key] for key in ('revision', 'settings', 'tracks')}).json()
    again = client.post(BASE + '/import-shot-results', json={"revision": saved['revision'], "preproductionRevision": 3}).json()
    assert len(again['tracks']) == 2
    prep['assets'].append({**prep['assets'][0], 'id': 'other-video'})
    prep['shots'][0]['resultAssetId'] = 'other-video'
    prep['revision'] = 4
    path.write_text(json.dumps(prep))
    replaced = client.post(BASE + '/import-shot-results', json={"revision": again['revision'], "preproductionRevision": 4}).json()
    assert len(replaced['tracks']) == 3
    assert replaced['tracks'][-1]['clips'][0]['assetId'] == 'other-video'


def test_validate_draft_checks_assets_without_writing_or_requiring_current_revision(tmp_path):
    client, queue = setup(tmp_path)
    before = client.get(BASE).json()
    draft = _workspace(8)
    checked = client.post(BASE + "/validate-draft", json=draft)
    assert checked.status_code == 200
    assert checked.json() == draft
    assert client.get(BASE).json() == before
    assert not queue.jobs
    draft["tracks"][0]["clips"][0]["assetId"] = "missing"
    assert client.post(BASE + "/validate-draft", json=draft).status_code == 422
    draft["tracks"][0]["clips"][0]["assetId"] = "video"
    (tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "video.mp4").unlink()
    assert client.post(BASE + "/validate-draft", json=draft).status_code == 409
    assert client.post(BASE.replace("p1", "p2") + "/validate-draft", json=draft).status_code == 404
