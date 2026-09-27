"""Saved subtitles belong to one variant and one content version."""
import json
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.batch_editing_api import create_batch_editing_router
from app.main import create_app


INTENT = {"x-aivre-intent": "semantic-analysis"}


def prepare(client, tmp_path):
    project_id = client.post("/api/projects", json={"name": "字幕交付"}).json()["id"]
    asset_root = tmp_path / "project-files" / project_id / "preproduction"
    (asset_root / "assets").mkdir(parents=True)
    (asset_root / "assets" / "shot.mp4").write_bytes(b"video")
    (asset_root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [],
        "assets": [{"id": "shot", "name": "演示", "kind": "video", "file": "shot.mp4", "duration": 3}]}), encoding="utf-8")
    base = f"/api/projects/{project_id}/batch-edits"
    first = client.post(base, json={"sellingPoint": "省时", "script": "演示操作"}, headers=INTENT).json()["task"]
    second = client.post(base, json={"sellingPoint": "易用", "script": "展示界面"}, headers=INTENT).json()["task"]
    tracks = first["variant"]["tracks"]
    tracks[0]["clips"] = [{"id": "clip-a", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 3,
                           "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
    saved = client.put(f"{base}/{first['id']}/variant", json={"revision": 0, "tracks": tracks}, headers=INTENT)
    assert saved.status_code == 200, saved.text
    return base, first, second


def test_subtitle_edit_persists_and_rejects_invalid_time_without_touching_other_variant(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        base, first, second = prepare(client, tmp_path)
        path = f"{base}/{first['id']}/subtitles"
        cues = [{"id": "line-a", "start": 0.2, "end": 1.5, "text": "人工修订字幕"}]

        saved = client.put(path, json={"revision": 0, "timelineRevision": 1, "cues": cues}, headers=INTENT)

        assert saved.status_code == 200, saved.text
        assert saved.json()["subtitles"]["revision"] == 1
        reopened = client.get(base).json()["tasks"]
        assert reopened[0]["variant"]["subtitles"]["cues"] == cues
        assert reopened[1]["id"] == second["id"]
        assert reopened[1]["variant"]["subtitles"]["cues"] == []
        assert client.put(path, json={"revision": 0, "timelineRevision": 1, "cues": cues}, headers=INTENT).status_code == 409
        invalid = [{**cues[0], "end": 3.5}]
        assert client.put(path, json={"revision": 1, "timelineRevision": 1, "cues": invalid}, headers=INTENT).status_code == 422
        lengthy = [{**cues[0], "text": "很长的字幕" * 30}]
        assert client.put(path, json={"revision": 1, "timelineRevision": 1, "cues": lengthy}, headers=INTENT).status_code == 422
        too_short = [{**cues[0], "start": 0.2001, "end": 0.2004}]
        assert client.put(path, json={"revision": 1, "timelineRevision": 1, "cues": too_short}, headers=INTENT).status_code == 422
        assert client.get(base).json()["tasks"][0]["variant"]["subtitles"]["cues"] == cues
        tracks = client.get(base).json()["tasks"][0]["variant"]["tracks"]
        tracks[0]["clips"][0]["duration"] = 1
        shorter = client.put(f"{base}/{first['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT)
        assert shorter.status_code == 422
        assert "字幕" in shorter.json()["detail"]["message"]
        assert client.get(base).json()["tasks"][0]["variant"]["revision"] == 1
        tracks[0]["clips"][0]["duration"] = 2.8
        assert client.put(f"{base}/{first['id']}/variant", json={"revision": 1, "tracks": tracks}, headers=INTENT).status_code == 200
        assert client.put(path, json={"revision": 1, "timelineRevision": 1, "cues": cues}, headers=INTENT).status_code == 409


def test_missing_speech_model_reports_dependency_without_replacing_saved_subtitles(tmp_path, monkeypatch):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        base, first, _ = prepare(client, tmp_path)
        path = f"{base}/{first['id']}/subtitles"
        cues = [{"id": "line-a", "start": 0, "end": 1, "text": "已确认文字"}]
        assert client.put(path, json={"revision": 0, "timelineRevision": 1, "cues": cues}, headers=INTENT).status_code == 200

        response = client.post(f"{path}/recognitions", json={"language": "zh"}, headers=INTENT)

        assert response.status_code == 503
        assert "WHISPER_MODEL" in response.json()["detail"]["message"]
        assert client.get(base).json()["tasks"][0]["variant"]["subtitles"]["cues"] == cues


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="需要本地 FFmpeg")
def test_recognition_keeps_existing_edit_as_candidate_then_burns_confirmed_subtitle(tmp_path, monkeypatch):
    project_id = "p1"
    root = tmp_path / "project-files" / project_id / "preproduction"
    (root / "assets").mkdir(parents=True)
    video = root / "assets" / "shot.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=128x96:r=24:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video)], check=True)
    (root / "state.json").write_text(json.dumps({"schemaVersion": 1, "revision": 0, "brief": {}, "shots": [],
        "assets": [{"id": "shot", "name": "演示", "kind": "video", "file": "shot.mp4", "duration": 2}]}), encoding="utf-8")
    whisper = tmp_path / "fake-whisper"
    whisper.write_text("#!/bin/sh\nfor arg in \"$@\"; do last=\"$arg\"; done\nprintf '1\\n00:00:00,200 --> 00:00:02,100\\n识别候选\\n' > \"${last}.srt\"\nprintf '{}' > \"${last}.json\"\n", encoding="utf-8")
    whisper.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    monkeypatch.setenv("WHISPER_BINARY", str(whisper))
    monkeypatch.setenv("WHISPER_MODEL", str(model))
    jobs = []

    class Queue:
        def submit(self, kind, pid, handler):
            jobs.append((pid, handler))
            return True

    app = FastAPI()
    app.include_router(create_batch_editing_router(tmp_path, lambda _: object(), Queue()))
    with TestClient(app) as client:
        base = f"/api/projects/{project_id}/batch-edits"
        first = client.post(base, json={"sellingPoint": "省时", "script": "演示"}).json()["task"]
        second = client.post(base, json={"sellingPoint": "易用", "script": "演示"}).json()["task"]
        tracks = first["variant"]["tracks"]
        tracks[0]["clips"] = [{"id": "clip", "assetId": "shot", "start": 0, "inPoint": 0, "duration": 2,
                               "speed": 1, "volume": 1, "fadeIn": 0, "fadeOut": 0}]
        assert client.put(f"{base}/{first['id']}/variant", json={"revision": 0, "tracks": tracks}).status_code == 200
        subtitles = f"{base}/{first['id']}/subtitles"
        saved = [{"id": "manual", "start": 0.2, "end": 1.5, "text": "人工原稿"}]
        assert client.put(subtitles, json={"revision": 0, "timelineRevision": 1, "cues": saved}).status_code == 200

        submitted = client.post(f"{subtitles}/recognitions", json={"language": "zh"})
        assert submitted.status_code == 202, submitted.text
        pid, handler = jobs.pop()
        handler(pid)
        state = client.get(base).json()["tasks"]
        recognition = state[0]["variant"]["subtitles"]["recognitions"][-1]
        assert recognition["status"] == "completed", recognition
        assert recognition["cues"][0]["text"] == "识别候选"
        assert recognition["cues"][0]["end"] == 2
        assert "sources" not in recognition and "snapshot" not in recognition
        assert state[0]["variant"]["subtitles"]["cues"] == saved
        assert state[1]["id"] == second["id"] and state[1]["variant"]["subtitles"]["cues"] == []

        corrected = [{**recognition["cues"][0], "text": "人工修订字幕"}]
        saved_correction = client.put(subtitles, json={"revision": 1, "timelineRevision": 1, "cues": corrected})
        assert saved_correction.status_code == 200
        assert "sources" not in saved_correction.json()["subtitles"]["recognitions"][-1]
        preview = client.post(f"{base}/{first['id']}/variant/previews", json={"revision": 1})
        assert preview.status_code == 202, preview.text
        pid, handler = jobs.pop()
        handler(pid)
        run = client.get(base).json()["tasks"][0]["variant"]["runs"][-1]
        assert run["status"] == "completed", run
        assert run["revision"] == 1 and run["subtitleRevision"] == 2
        srt = tmp_path / "project-files" / project_id / "batch-edits" / "runs" / run["id"] / "subtitles.srt"
        assert "人工修订字幕" in srt.read_text(encoding="utf-8")
        burned = client.get(f"{base}/{first['id']}/variant/previews/{run['id']}/output").content
        assert len(burned) > 1000
        output = tmp_path / "burned.mp4"
        output.write_bytes(burned)
        frame = subprocess.check_output(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", "0.8",
                                         "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        assert max(frame) > 30
        before = subprocess.check_output(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", "0.05",
                                          "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        assert max(before) < max(frame)
        assert client.get(base).json()["tasks"][1]["variant"]["runs"] == []

        whisper.write_text("#!/bin/sh\nfor arg in \"$@\"; do last=\"$arg\"; done\nprintf '\\n' > \"${last}.srt\"\nprintf '{}' > \"${last}.json\"\n", encoding="utf-8")
        retry = client.post(f"{subtitles}/recognitions", json={"language": "zh"})
        assert retry.status_code == 202, retry.text
        pid, handler = jobs.pop()
        handler(pid)
        after_retry = client.get(base).json()["tasks"][0]["variant"]
        assert after_retry["subtitles"]["recognitions"][-1]["status"] == "failed"
        assert "没有识别到" in after_retry["subtitles"]["recognitions"][-1]["error"]
        assert after_retry["subtitles"]["cues"] == corrected
        assert after_retry["runs"][-1]["status"] == "completed"
