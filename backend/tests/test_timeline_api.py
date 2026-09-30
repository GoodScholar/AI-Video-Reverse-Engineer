import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.timeline_api import create_timeline_router


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


def _asset_root(tmp_path):
    root = tmp_path / "project-files" / PROJECT_ID / "preproduction"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    if not (root / "assets" / "video.mp4").exists():
        subprocess.run([
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=red:size=64x48:rate=24:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(root / "assets" / "video.mp4"),
        ], check=True, capture_output=True, text=True)
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


def _add_image_asset(tmp_path):
    root = tmp_path / "project-files" / PROJECT_ID / "preproduction"
    image = root / "assets" / "image.png"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=64x48", "-frames:v", "1", str(image),
    ], check=True, capture_output=True, text=True)
    state_path = root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["assets"].append({"id": "image", "name": "静态图", "kind": "image", "file": "image.png", "width": 64, "height": 48})
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _image_workspace(revision=0):
    workspace = _workspace(revision)
    workspace["tracks"][0]["clips"][0]["assetId"] = "image"
    return workspace


def setup(tmp_path, renderer=None, reframe_analyzer=None):
    _asset_root(tmp_path)
    queue = Queue()
    app = FastAPI()
    app.include_router(create_timeline_router(tmp_path, lambda project_id: SimpleNamespace(id=project_id) if project_id == PROJECT_ID else None, queue, renderer=renderer, reframe_analyzer=reframe_analyzer))
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


def test_timeline_reframe_analyze_cancel_and_retry_are_durable(tmp_path):
    """A cancelled queued reframe must not write back; retry owns a new job."""
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    def analyzer(source, identity):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": {"width": 1280, "height": 720}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
                            {"timeSeconds": 2, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    client, queue = setup(tmp_path, reframe_analyzer=analyzer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    started = client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1})
    assert started.status_code == 202
    assert started.json()["reframeAnalysis"]["clip1"]["status"] == "queued"
    assert client.post(BASE + "/reframes/clip1/cancel", json={"revision": 1}).status_code == 200
    queue.run()
    assert client.get(BASE).json()["reframeAnalysis"]["clip1"]["status"] == "cancelled"
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    queue.run()
    completed = client.get(BASE).json()
    assert completed["reframeAnalysis"]["clip1"]["status"] == "completed"
    assert completed["tracks"][0]["clips"][0]["reframe"]["status"] == "review_required"
    forged = completed["tracks"]
    forged[0]["clips"][0]["reframe"]["status"] = "ready"
    assert client.put(BASE, json={"revision": completed["revision"], "settings": completed["settings"], "tracks": forged}).status_code == 422


def test_timeline_image_manual_override_rejects_nonzero_or_multiple_keyframes_and_persists_center(tmp_path):
    client, _ = setup(tmp_path)
    _add_image_asset(tmp_path)
    assert client.put(BASE, json=_image_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    queued = client.get(BASE).json()

    for keyframes in ([{"timeSeconds": .1, "x": .5, "y": .5}], [
        {"timeSeconds": 0, "x": .5, "y": .5}, {"timeSeconds": .1, "x": .6, "y": .5},
    ]):
        attempted = json.loads(json.dumps(queued))
        attempted["tracks"][0]["clips"][0]["reframe"]["override"] = {"mode": "manual", "keyframes": keyframes}
        assert client.put(BASE, json={"revision": queued["revision"], "settings": queued["settings"], "tracks": attempted["tracks"]}).status_code == 422

    queued["tracks"][0]["clips"][0]["reframe"]["override"] = {"mode": "center", "keyframes": []}
    saved = client.put(BASE, json={"revision": queued["revision"], "settings": queued["settings"], "tracks": queued["tracks"]})
    assert saved.status_code == 200
    assert client.get(BASE).json()["tracks"][0]["clips"][0]["reframe"]["override"] == {"mode": "center", "keyframes": []}


def test_timeline_empty_fingerprint_center_survives_explicit_analysis(tmp_path):
    client, queue = setup(tmp_path)
    _add_image_asset(tmp_path)
    assert client.put(BASE, json=_image_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    pending = client.get(BASE).json()
    assert pending["tracks"][0]["clips"][0]["reframe"]["sourceFingerprint"] == ""
    pending["tracks"][0]["clips"][0]["reframe"]["override"] = {"mode": "center", "keyframes": []}
    assert client.put(BASE, json={"revision": pending["revision"], "settings": pending["settings"],
                                  "tracks": pending["tracks"]}).status_code == 200
    assert client.post(BASE + "/reframes/clip1/cancel", json={"revision": 2}).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 2}).status_code == 202
    queue.run(); queue.run()
    assert client.get(BASE).json()["tracks"][0]["clips"][0]["reframe"]["override"] == {"mode": "center", "keyframes": []}


def test_timeline_image_reframe_analysis_cas_rejects_an_old_output_aspect(tmp_path):
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    def analyzer(source, identity):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": identity["targetAspect"], "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [{"x": .2, "y": .1, "width": .3, "height": .7,
                                                               "label": "person", "confidence": .9}],
                             "center": {"x": .35, "y": .45}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    client, queue = setup(tmp_path, reframe_analyzer=analyzer)
    _add_image_asset(tmp_path)
    assert client.put(BASE, json=_image_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    queued = client.get(BASE).json()
    assert queued["reframeAnalysis"]["clip1"]["identity"]["targetAspect"] == {"width": 1280, "height": 720}
    queued["settings"] = {"width": 720, "height": 1280, "fps": 30}
    assert client.put(BASE, json={"revision": queued["revision"], "settings": queued["settings"], "tracks": queued["tracks"]}).status_code == 200
    queue.run()

    record = client.get(BASE).json()["reframeAnalysis"]["clip1"]
    assert record["status"] == "failed"
    assert "输出比例" in record["error"]
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 2}).status_code == 202
    queue.run()
    retried = client.get(BASE).json()
    assert retried["reframeAnalysis"]["clip1"]["status"] == "completed"
    assert retried["tracks"][0]["clips"][0]["reframe"]["targetAspect"] == {"width": 720, "height": 1280}


def test_candidate_replacement_persists_pending_reframe_and_manual_render(tmp_path):
    """Saving a replacement must never resurrect old facts on read or render."""
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    def analyzer(source, _identity):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": {"width": 1280, "height": 720}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
                            {"timeSeconds": 2, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    client, queue = setup(tmp_path, reframe_analyzer=analyzer)
    assets = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets"
    (assets / "replacement.mp4").write_bytes((assets / "video.mp4").read_bytes())
    state_path = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "state.json"
    preproduction = json.loads(state_path.read_text())
    preproduction["assets"].append({"id": "replacement", "name": "替换画面", "kind": "video", "file": "replacement.mp4", "duration": 10.0, "width": 64, "height": 48})
    state_path.write_text(json.dumps(preproduction))
    assert client.put(BASE, json=_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    queue.run()
    analyzed = client.get(BASE).json()
    replacement = analyzed["tracks"]
    replacement[0]["clips"][0]["assetId"] = "replacement"
    replacement[0]["clips"][0].pop("reframe")
    saved = client.put(BASE, json={"revision": analyzed["revision"], "settings": analyzed["settings"], "tracks": replacement}).json()
    pending = saved["tracks"][0]["clips"][0]["reframe"]
    assert pending["sourceFingerprint"] == "" and pending["samples"] == []
    assert pending["override"] == {"mode": "auto", "keyframes": []}
    assert client.get(BASE).json()["tracks"][0]["clips"][0]["reframe"] == pending
    pending["override"] = {"mode": "center", "keyframes": []}
    rendered = client.put(BASE, json={"revision": saved["revision"], "settings": saved["settings"], "tracks": saved["tracks"]}).json()
    queued = client.post(BASE + "/runs", json={"revision": rendered["revision"], "format": "preview"})
    assert queued.status_code == 202
    queue.run()
    assert client.get(BASE).json()["runs"][-1]["status"] == "completed"


def test_timeline_reframe_invalidates_when_source_changes_while_queued(tmp_path):
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    initial = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "video.mp4"
    _asset_root(tmp_path)
    old_hash = source_fingerprint(initial)
    def analyzer(_source, _identity):
        return {"schemaVersion": 1, "sourceFingerprint": old_hash, "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": {"width": 1280, "height": 720}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
                            {"timeSeconds": 2, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    client, queue = setup(tmp_path, reframe_analyzer=analyzer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    path = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "video.mp4"
    path.write_bytes(b"changed")
    queue.run()
    record = client.get(BASE).json()["reframeAnalysis"]["clip1"]
    assert record["status"] == "failed"


def test_timeline_reframe_source_deleted_while_queued_becomes_retryable_failure(tmp_path):
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    _asset_root(tmp_path)
    source = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "video.mp4"
    initial_hash = source_fingerprint(source)
    def analyzer(_source, _identity):
        return {"schemaVersion": 1, "sourceFingerprint": initial_hash, "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": {"width": 1280, "height": 720}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9},
                            {"timeSeconds": 2, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}
    client, queue = setup(tmp_path, reframe_analyzer=analyzer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    source.unlink()
    queue.run()
    record = client.get(BASE).json()["reframeAnalysis"]["clip1"]
    assert record["status"] == "failed"
    assert "素材" in record["error"]


def test_timeline_reframe_worker_rejects_asset_identity_change_with_same_file(tmp_path):
    client, queue = setup(tmp_path)
    assert client.put(BASE, json=_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    asset_path = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "state.json"
    assets = json.loads(asset_path.read_text(encoding="utf-8"))
    assets["assets"][0]["sourceReferenceId"] = "replacement-reference"
    asset_path.write_text(json.dumps(assets), encoding="utf-8")
    queue.run()

    assert client.get(BASE).json()["reframeAnalysis"]["clip1"]["status"] == "failed"


def test_timeline_reframe_never_accepts_worker_dimensions_when_the_source_probe_has_none(tmp_path, monkeypatch):
    """Only ffprobe dimensions may authorize detector facts for a timeline clip."""
    from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint
    from app import timeline_render
    monkeypatch.setattr(timeline_render, "_probe_media", lambda *_: SimpleNamespace(width=None, height=None))

    def analyzer(source, _identity):
        return {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
                "targetAspect": {"width": 1280, "height": 720}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
                "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
                "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []}}

    client, queue = setup(tmp_path, reframe_analyzer=analyzer)
    assert client.put(BASE, json=_workspace()).status_code == 200
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    queue.run()

    record = client.get(BASE).json()["reframeAnalysis"]["clip1"]
    assert record["status"] == "failed"
    assert "可信源尺寸" in record["error"]


def test_timeline_reframe_restart_recovers_queued_analysis_as_retryable(tmp_path):
    client, queue = setup(tmp_path)
    assert client.put(BASE, json=_workspace()).status_code == 200
    # The detector need not execute: recovery must not leave the durable
    # queued record permanently active after a new router is constructed.
    assert client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}).status_code == 202
    restarted, _queue = setup(tmp_path)
    record = restarted.get(BASE).json()["reframeAnalysis"]["clip1"]
    assert record["status"] == "failed"
    assert "重启" in record["error"]
    assert queue.jobs


def test_timeline_reframe_rejects_stale_revision_before_it_can_enqueue(tmp_path):
    client, queue = setup(tmp_path)
    assert client.put(BASE, json=_workspace()).status_code == 200
    assert client.put(BASE, json=_workspace(revision=1)).status_code == 200

    stale = client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1})

    assert stale.status_code == 409
    assert queue.jobs == []


def test_timeline_reframe_proves_source_outside_lock_and_aborts_stale_submission(tmp_path, monkeypatch):
    """A slow source proof must not block workspace edits or leave a stale queued record."""
    from app import montage_reframe

    client, queue = setup(tmp_path)
    assert client.put(BASE, json=_workspace()).status_code == 200
    entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
    original = montage_reframe.source_fingerprint

    def blocked_fingerprint(path):
        entered.set()
        assert release.wait(5), "测试未释放主体分析来源证明"
        return original(path)

    monkeypatch.setattr(montage_reframe, "source_fingerprint", blocked_fingerprint)
    result = {}
    submitted = threading.Thread(target=lambda: result.setdefault(
        "response", client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}),
    ))
    submitted.start()
    assert entered.wait(5)
    reader = threading.Thread(target=lambda: (result.setdefault("read", client.get(BASE)), read_done.set()))
    reader.start()
    try:
        assert read_done.wait(1), "慢来源证明不应占用时间线锁"
        current = result["read"].json()
        # There is no record until the proof CAS succeeds, so cancel stays a
        # quick, truthful conflict instead of waiting behind the hash.
        assert client.post(BASE + "/reframes/clip1/cancel", json={"revision": 1}).status_code == 409
        changed = client.put(BASE, json={
            "revision": current["revision"],
            "settings": {"width": 720, "height": 1280, "fps": 30},
            "tracks": current["tracks"],
        })
        assert changed.status_code == 200, changed.text
    finally:
        release.set()
        submitted.join(5)
        reader.join(5)

    assert result["response"].status_code == 409
    assert result["response"].json()["detail"]["code"] == "timeline_conflict"
    current = client.get(BASE).json()
    assert current["reframeAnalysis"] == {}
    assert queue.jobs == []


def test_timeline_reframe_probe_media_proof_does_not_block_get(tmp_path, monkeypatch):
    from app import timeline_render

    client, queue = setup(tmp_path)
    assert client.put(BASE, json=_workspace()).status_code == 200
    entered, release = threading.Event(), threading.Event()
    original = timeline_render._probe_media
    def blocked(path, probe):
        entered.set(); assert release.wait(5); return original(path, probe)
    monkeypatch.setattr(timeline_render, "_probe_media", blocked)
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("response", client.post(
        BASE + "/reframes/clip1/analyze", json={"revision": 1})))
    thread.start(); assert entered.wait(5)
    assert client.get(BASE).status_code == 200
    release.set(); thread.join(5)
    assert result["response"].status_code == 202
    assert queue.jobs


def test_timeline_reframe_source_replaced_during_proof_never_creates_a_job(tmp_path, monkeypatch):
    from app import montage_reframe

    client, queue = setup(tmp_path)
    assert client.put(BASE, json=_workspace()).status_code == 200
    source = tmp_path / "project-files" / PROJECT_ID / "preproduction" / "assets" / "video.mp4"
    entered, release = threading.Event(), threading.Event()
    original = montage_reframe.source_fingerprint

    def blocked_fingerprint(path):
        entered.set()
        assert release.wait(5), "测试未释放替换来源证明"
        return original(path)

    monkeypatch.setattr(montage_reframe, "source_fingerprint", blocked_fingerprint)
    result = {}
    submitted = threading.Thread(target=lambda: result.setdefault(
        "response", client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}),
    ))
    submitted.start()
    assert entered.wait(5)
    try:
        source.write_bytes(b"replaced while proving")
    finally:
        release.set()
        submitted.join(5)

    assert result["response"].status_code == 409
    assert result["response"].json()["detail"]["code"] == "reframe_source_unavailable"
    assert client.get(BASE).json()["reframeAnalysis"] == {}
    assert queue.jobs == []


def test_timeline_reframe_proof_rejects_clip_identity_change_without_revision(tmp_path, monkeypatch):
    """The second lock compares the clip, not only its public revision token."""
    from app import montage_reframe

    client, queue = setup(tmp_path)
    assert client.put(BASE, json=_workspace()).status_code == 200
    entered, release = threading.Event(), threading.Event()
    original = montage_reframe.source_fingerprint

    def blocked_fingerprint(path):
        entered.set()
        assert release.wait(5), "测试未释放片段身份证明"
        return original(path)

    monkeypatch.setattr(montage_reframe, "source_fingerprint", blocked_fingerprint)
    result = {}
    submitted = threading.Thread(target=lambda: result.setdefault(
        "response", client.post(BASE + "/reframes/clip1/analyze", json={"revision": 1}),
    ))
    submitted.start()
    assert entered.wait(5)
    try:
        state_path = tmp_path / "project-files" / PROJECT_ID / "timeline" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tracks"][0]["clips"][0]["inPoint"] = 1
        # This white-box race intentionally keeps revision=1: the proof CAS
        # must still reject a changed clip identity before it creates state.
        state_path.write_text(json.dumps(state), encoding="utf-8")
    finally:
        release.set()
        submitted.join(5)

    assert result["response"].status_code == 409
    assert result["response"].json()["detail"]["code"] == "timeline_conflict"
    assert client.get(BASE).json()["reframeAnalysis"] == {}
    assert queue.jobs == []


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
        assert sources["video"].read_bytes() != b"changed"
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


def test_transition_boundary_is_persisted_and_frozen_into_the_render_run(tmp_path):
    received = []
    def renderer(timeline, sources, output, **kwargs):
        received.append(timeline)
        output.write_bytes(b"rendered")
    client, queue = setup(tmp_path, renderer)
    value = _workspace(tracks=[{
        "id": "v1", "name": "视频", "kind": "video", "muted": False, "hidden": False,
        "clips": [
            {"id": "one", "assetId": "video", "start": 0, "inPoint": 0, "duration": 2, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
            {"id": "two", "assetId": "video", "start": 1.6, "inPoint": 2, "duration": 2, "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0},
        ],
    }])
    value["transitions"] = [{
        "id": "boundary", "leftClipId": "one", "rightClipId": "two", "template": "dissolve",
        "enabled": True, "duration": .4, "overlap": .4, "audioCrossfade": True, "reason": "场景变化",
    }]

    saved = client.put(BASE, json=value)
    assert saved.status_code == 200
    assert saved.json()["transitions"] == value["transitions"]
    assert client.post(BASE + "/runs", json={"revision": 1, "format": "preview"}).status_code == 202
    queue.run()

    assert received[0]["transitions"] == value["transitions"]


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


def test_restart_marks_unfinished_run_failed(tmp_path):
    client, _, = setup(tmp_path)
    client.put(BASE, json=_workspace())
    run_id = client.post(BASE + "/runs", json={"revision": 1, "format": "mp4"}).json()["runs"][-1]["id"]
    restarted, _ = setup(tmp_path)
    run = restarted.get(BASE).json()["runs"][0]
    assert run["id"] == run_id and run["status"] == "failed"


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
