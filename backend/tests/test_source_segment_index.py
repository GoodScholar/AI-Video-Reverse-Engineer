import hashlib
import json
from pathlib import Path
import subprocess
from threading import Event, Thread

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.source_segment_index as source_index
from app.preproduction import PreproductionStore
from app.main import create_app
from app.source_segment_index import (
    KEYFRAME_MODEL_VERSION,
    SEGMENT_ALGORITHM_VERSION,
    SegmentAnalysis,
    SegmentKeyframe,
    build_segment_intervals,
    create_source_segment_router,
    run_source_segment_analysis,
)
from app.video_toolkit import ToolkitConfig


PROJECT_ID = "project-1"
INTENT = {"x-aivre-intent": "semantic-analysis"}


class ManualQueue:
    def __init__(self):
        self.jobs = []

    def submit(self, run_kind, owner_id, handler):
        self.jobs.append((run_kind, owner_id, handler))
        return True

    def run(self, index=0):
        _, owner_id, handler = self.jobs.pop(index)
        handler(owner_id)

    def is_project_active(self, _owner_id):
        return False

    def shutdown(self):
        pass


def install_video(tmp_path: Path, asset_id: str, contents: bytes = b"source-video", duration: float = 10.0):
    store = PreproductionStore(tmp_path)
    state = store.load(PROJECT_ID)
    filename = f"{asset_id}.mp4"
    state["assets"].append({
        "id": asset_id,
        "name": f"素材 {asset_id}",
        "kind": "video",
        "role": "motion",
        "file": filename,
        "duration": duration,
    })
    store.save(PROJECT_ID, state)
    path = store.path(PROJECT_ID, "assets", filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path


def client_for(tmp_path, queue, runner, *, algorithm_version="scene-v1", keyframe_model_version="frame-v1"):
    app = FastAPI()
    app.include_router(create_source_segment_router(
        tmp_path,
        lambda project_id: {"id": project_id} if project_id == PROJECT_ID else None,
        queue,
        runner=runner,
        algorithm_version=algorithm_version,
        keyframe_model_version=keyframe_model_version,
    ))
    return TestClient(app)


def analysis_with_cuts(cuts=(2.0, 7.0), duration=10.0):
    def run(_source, output, cancelled):
        assert not cancelled()
        keyframes = []
        boundaries = [0.0, *cuts, duration]
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
            filename = f"keyframe-{index}.jpg"
            (output / filename).write_bytes(f"frame-{index}".encode())
            keyframes.append(SegmentKeyframe(time_seconds=(start + end) / 2, filename=filename))
        return SegmentAnalysis(duration_seconds=duration, cuts=list(cuts), keyframes=keyframes)
    return run


def test_scene_boundaries_form_stable_nonempty_source_intervals():
    assert build_segment_intervals(10.0, [7.0, 2.0, 2.0, -1.0, 12.0]) == [
        (0.0, 2.0),
        (2.0, 7.0),
        (7.0, 10.0),
    ]
    assert build_segment_intervals(0.0, []) == []


def test_same_video_keeps_multiple_candidates_with_source_evidence_and_bounded_previews(tmp_path):
    source = install_video(tmp_path, "asset-a")
    queue = ManualQueue()
    client = client_for(tmp_path, queue, analysis_with_cuts())

    started = client.post(
        f"/api/projects/{PROJECT_ID}/source-segments",
        json={"assetIds": ["asset-a"]},
        headers=INTENT,
    )

    assert started.status_code == 202, started.text
    assert started.json()["indexes"][0]["status"] == "queued"
    queue.run()
    completed = client.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]
    assert completed["status"] == "completed"
    assert completed["validity"] == {"status": "current", "reason": None}
    assert completed["sourceFingerprint"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert [(item["inPointSeconds"], item["outPointSeconds"]) for item in completed["candidates"]] == [
        (0.0, 2.0), (2.0, 7.0), (7.0, 10.0),
    ]
    assert len({item["id"] for item in completed["candidates"]}) == 3
    assert {item["assetId"] for item in completed["candidates"]} == {"asset-a"}
    assert all(item["sourceFingerprint"] == completed["sourceFingerprint"] for item in completed["candidates"])
    assert all(item["algorithmVersion"] == "scene-v1" for item in completed["candidates"])
    assert all(item["keyframe"]["timeSeconds"] >= item["inPointSeconds"] for item in completed["candidates"])
    assert all(item["keyframe"]["timeSeconds"] < item["outPointSeconds"] for item in completed["candidates"])
    assert completed["candidates"][-1]["preview"]["endSeconds"] == 10.0
    assert "#t=7.000,10.000" in completed["candidates"][-1]["preview"]["url"]
    assert str(tmp_path) not in json.dumps(completed, ensure_ascii=False)

    keyframe = client.get(completed["candidates"][0]["keyframe"]["url"])
    assert keyframe.status_code == 200
    assert keyframe.content == b"frame-1"


def test_multiple_video_assets_can_be_queued_cancelled_and_retried(tmp_path):
    install_video(tmp_path, "asset-a")
    install_video(tmp_path, "asset-b")
    queue = ManualQueue()
    client = client_for(tmp_path, queue, analysis_with_cuts(cuts=()))

    response = client.post(
        f"/api/projects/{PROJECT_ID}/source-segments",
        json={"assetIds": ["asset-a", "asset-b"]},
        headers=INTENT,
    )
    assert response.status_code == 202
    assert {item["assetId"] for item in response.json()["indexes"]} == {"asset-a", "asset-b"}

    cancelled = client.post(
        f"/api/projects/{PROJECT_ID}/source-segments/assets/asset-a/cancel",
        headers=INTENT,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    first_run_id = cancelled.json()["runId"]

    retried = client.post(
        f"/api/projects/{PROJECT_ID}/source-segments/assets/asset-a/retry",
        headers=INTENT,
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    assert retried.json()["runId"] != first_run_id

    queue.run(0)  # The cancelled run finishes late and must not replace the retry.
    queued = client.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"]
    current_a = next(item for item in queued if item["assetId"] == "asset-a")
    assert current_a["runId"] == retried.json()["runId"] and current_a["status"] == "queued"


def test_restart_resumes_active_run_from_checkpoint(tmp_path):
    install_video(tmp_path, "asset-a")
    first_queue = ManualQueue()
    first = client_for(tmp_path, first_queue, analysis_with_cuts())
    started = first.post(
        f"/api/projects/{PROJECT_ID}/source-segments",
        json={"assetIds": ["asset-a"]},
        headers=INTENT,
    ).json()["indexes"][0]

    restarted_queue = ManualQueue()
    restarted = client_for(tmp_path, restarted_queue, analysis_with_cuts())
    recovered = restarted.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]

    assert recovered["runId"] == started["runId"]
    assert recovered["status"] == "queued"
    assert recovered["error"] is None
    assert len(restarted_queue.jobs) == 1
    restarted_queue.run()
    completed = restarted.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]
    assert completed["status"] == "completed"


def test_late_completion_after_restart_does_not_overwrite_resumed_attempt(tmp_path):
    install_video(tmp_path, "asset-a")
    entered = Event()
    release = Event()

    def blocked_runner(_source, output, _cancelled):
        (output / "keyframe-1.jpg").write_bytes(b"frame")
        entered.set()
        assert release.wait(2)
        return SegmentAnalysis(
            duration_seconds=10,
            cuts=[],
            keyframes=[SegmentKeyframe(time_seconds=5, filename="keyframe-1.jpg")],
        )

    first_queue = ManualQueue()
    first = client_for(tmp_path, first_queue, blocked_runner)
    first.post(
        f"/api/projects/{PROJECT_ID}/source-segments",
        json={"assetIds": ["asset-a"]},
        headers=INTENT,
    )
    old_worker = Thread(target=first_queue.run)
    old_worker.start()
    assert entered.wait(2)

    restarted_queue = ManualQueue()
    restarted = client_for(tmp_path, restarted_queue, analysis_with_cuts())
    assert restarted.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]["status"] == "queued"
    release.set()
    old_worker.join(timeout=2)

    still_queued = restarted.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]
    assert still_queued["status"] == "queued" and still_queued["candidates"] == []
    restarted_queue.run()
    assert restarted.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]["status"] == "completed"


def test_source_and_version_changes_or_missing_files_mark_completed_index_stale(tmp_path):
    source = install_video(tmp_path, "asset-a")
    queue = ManualQueue()
    client = client_for(tmp_path, queue, analysis_with_cuts())
    client.post(
        f"/api/projects/{PROJECT_ID}/source-segments",
        json={"assetIds": ["asset-a"]},
        headers=INTENT,
    )
    queue.run()

    source.write_bytes(b"replacement-video")
    changed = client.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]
    assert changed["validity"] == {"status": "stale", "reason": "source_changed"}
    assert changed["candidates"] == []
    assert changed["candidateCount"] == 3

    version_changed = client_for(
        tmp_path, ManualQueue(), analysis_with_cuts(), algorithm_version="scene-v2",
    ).get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]
    assert version_changed["validity"] == {"status": "stale", "reason": "algorithm_changed"}

    source.unlink()
    missing = client.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]
    assert missing["validity"] == {"status": "stale", "reason": "source_missing"}


def test_source_change_during_analysis_prevents_late_result_from_becoming_current(tmp_path):
    source = install_video(tmp_path, "asset-a")
    queue = ManualQueue()

    def changing_runner(_source, output, cancelled):
        (output / "keyframe-1.jpg").write_bytes(b"frame")
        source.write_bytes(b"new-source")
        return SegmentAnalysis(
            duration_seconds=10,
            cuts=[],
            keyframes=[SegmentKeyframe(time_seconds=5, filename="keyframe-1.jpg")],
        )

    client = client_for(tmp_path, queue, changing_runner)
    client.post(
        f"/api/projects/{PROJECT_ID}/source-segments",
        json={"assetIds": ["asset-a"]},
        headers=INTENT,
    )
    queue.run()

    result = client.get(f"/api/projects/{PROJECT_ID}/source-segments").json()["indexes"][0]
    assert result["status"] == "failed"
    assert result["error"]["code"] == "source_changed"
    assert result["candidates"] == []


def test_default_runner_reuses_scene_and_keyframe_checkpoints(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "index"
    output.mkdir()
    (output / "scenes.json").write_text(json.dumps({
        "schemaVersion": 1,
        "algorithmVersion": SEGMENT_ALGORITHM_VERSION,
        "duration": 2.0,
        "cuts": [1.0],
    }), encoding="utf-8")
    (output / "keyframe-1.jpg").write_bytes(b"existing-frame")
    monkeypatch.setattr(source_index, "_probe_video_duration", lambda *_: 2.0)
    monkeypatch.setattr(source_index, "execute_tool", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("scene detection reran")))
    commands = []

    def extract(command, _cancelled):
        commands.append(command)
        Path(command[-1]).write_bytes(b"new-frame")

    monkeypatch.setattr(source_index, "_run_cancellable", extract)

    result = run_source_segment_analysis(
        source, output, lambda: False, config=ToolkitConfig.local(),
    )

    assert result.duration_seconds == 2.0 and result.cuts == [1.0]
    assert [item.filename for item in result.keyframes] == ["keyframe-1.jpg", "keyframe-2.jpg"]
    assert len(commands) == 1 and commands[0][-1].endswith("keyframe-2.jpg")


def test_real_scene_index_uses_video_stream_tail_when_audio_is_longer(tmp_path):
    config = ToolkitConfig.local()
    if not config.environment()["scenes"]["available"]:
        pytest.skip("optional scene worker is not installed")
    source = tmp_path / "two-scenes-with-long-audio.mp4"
    subprocess.run([
        config.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=160x120:rate=24:duration=1",
        "-f", "lavfi", "-i", "color=blue:size=160x120:rate=24:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(source),
    ], check=True)

    result = run_source_segment_analysis(
        source, tmp_path / "output", lambda: False, config=config,
    )

    assert result.duration_seconds == pytest.approx(2.0)
    assert result.cuts == [1.0]
    assert [item.time_seconds for item in result.keyframes] == [0.5, 1.5]
    assert all((tmp_path / "output" / item.filename).stat().st_size > 0 for item in result.keyframes)


def test_video_duration_uses_webm_stream_tag_instead_of_longer_audio_container(tmp_path):
    config = ToolkitConfig.local()
    source = tmp_path / "short-video-long-audio.webm"
    subprocess.run([
        config.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=160x120:rate=24:duration=1",
        "-f", "lavfi", "-i", "color=blue:size=160x120:rate=24:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libvpx-vp9", "-deadline", "realtime",
        "-cpu-used", "8", "-c:a", "libopus", str(source),
    ], check=True)

    assert source_index._probe_video_duration(source, config.ffprobe) == pytest.approx(2.0)


def test_main_app_mounts_index_api_and_protects_local_mutations(tmp_path):
    queue = ManualQueue()
    app = create_app(
        tmp_path,
        local_compute_queue=queue,
        source_segment_runner=analysis_with_cuts(cuts=()),
    )
    client = TestClient(app)
    project_id = client.post("/api/projects", json={"name": "商品项目"}).json()["id"]
    store = PreproductionStore(tmp_path)
    state = store.load(project_id)
    state["assets"].append({
        "id": "asset-a", "name": "视频", "kind": "video", "role": "motion",
        "file": "asset-a.mp4", "duration": 10,
    })
    store.save(project_id, state)
    source = store.path(project_id, "assets", "asset-a.mp4")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video")
    url = f"/api/projects/{project_id}/source-segments"

    denied = client.post(
        url,
        json={"assetIds": ["asset-a"]},
        headers={"origin": "http://127.0.0.1:5173"},
    )
    allowed = client.post(
        url,
        json={"assetIds": ["asset-a"]},
        headers={"origin": "http://127.0.0.1:5173", **INTENT},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 202
    assert client.get(url).json()["indexes"][0]["assetId"] == "asset-a"
