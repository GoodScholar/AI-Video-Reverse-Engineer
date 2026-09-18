import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shot_preparation_api import create_shot_preparation_router
from app.person_controls import PersonControlStore, append_queued_run


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要本地 FFmpeg")


PROJECT_ID = "project-001"
PREPROCESSING_ID = "prep-001"
SOURCE_ID = "video-001"
BASE = f"/api/projects/{PROJECT_ID}/preparation"


def _make_video(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=64x48:d=1",
        "-f", "lavfi", "-i", "color=c=blue:s=64x48:d=1",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-r", "12",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def _project(source_id=SOURCE_ID, preprocessing_id=PREPROCESSING_ID):
    return SimpleNamespace(
        id=PROJECT_ID,
        referenceMedia=SimpleNamespace(
            id=source_id, type="video", format="mp4", durationSeconds=2.0,
        ),
        localPreprocessing=SimpleNamespace(
            id=preprocessing_id, sourceReferenceMediaId=source_id,
            mediaType="video", status="completed",
        ),
        depthCaptures=[], activeDepthCaptureId=None, semanticAnalysis=None,
    )


def _setup(tmp_path):
    media = tmp_path / f"project-files/{PROJECT_ID}/reference-media/{SOURCE_ID}.mp4"
    media.parent.mkdir(parents=True)
    _make_video(media)
    artifacts = tmp_path / f"project-files/{PROJECT_ID}/local-preprocessing/{PREPROCESSING_ID}"
    artifacts.mkdir(parents=True)
    (artifacts / "scene-changes.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sceneChanges": [{"timeSeconds": 1.0, "score": 18.0}],
    }), encoding="utf-8")
    project = _project()
    app = FastAPI()
    app.include_router(create_shot_preparation_router(tmp_path, lambda _: project))
    return TestClient(app), project


def _toolkit_scenes(tmp_path: Path, *, run_id="scene-run-001", cuts=None, revision=0, asset_id=f"reference:{SOURCE_ID}"):
    directory = tmp_path / f"project-files/{PROJECT_ID}/toolkit/{run_id}"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps({
        "id": run_id, "assetId": asset_id, "kind": "scenes", "status": "completed",
        "artifacts": ["scenes.json"], "createdAt": "2026-09-18T00:00:00+00:00",
    }), encoding="utf-8")
    (directory / "scenes.json").write_text(json.dumps({
        "duration": 2.0, "cuts": [0.5, 1.0] if cuts is None else cuts, "revision": revision,
    }), encoding="utf-8")
    return run_id


def _pipeline_scenes(tmp_path: Path, *, pipeline_id="pipeline-001", step_id="step-scenes", cuts=None):
    directory = tmp_path / f"project-files/{PROJECT_ID}/toolkit/pipelines/{pipeline_id}/00-scenes"
    directory.mkdir(parents=True)
    (directory.parent / "state.json").write_text(json.dumps({
        "id": pipeline_id, "assetId": f"reference:{SOURCE_ID}", "status": "running", "createdAt": "2026-09-18T00:00:00+00:00",
        "steps": [{"id": step_id, "kind": "scenes", "directory": "00-scenes", "status": "completed", "artifacts": ["scenes.json"]}],
    }), encoding="utf-8")
    (directory / "scenes.json").write_text(json.dumps({"duration": 2.0, "cuts": [0.75] if cuts is None else cuts}), encoding="utf-8")
    return f"pipeline:{pipeline_id}:{step_id}"


def _save_drafts(client, body, notes_by_shot):
    response = client.put(BASE, json={
        "revision": body["revision"], "sourceId": body["sourceId"], "preprocessingId": body["preprocessingId"],
        "shots": [{"id": shot["id"], "notes": notes_by_shot.get(shot["id"], ""), "prompts": {
            "positiveZh": notes_by_shot.get(shot["id"], ""), "negativeZh": "", "positiveEn": "", "negativeEn": "",
        }} for shot in body["shots"]],
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_toolkit_cuts_replace_only_preparation_timeline_and_restore_detected_boundaries(tmp_path):
    client, _ = _setup(tmp_path)
    original = _save_drafts(client, client.get(BASE).json(), {"shot-002": "保持的镜头"})
    run_id = _toolkit_scenes(tmp_path)

    applied = client.post(f"{BASE}/timeline/apply", json={
        "revision": original["revision"], "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
        "toolkitRunId": run_id, "cutRevision": 0,
    })
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["timelineOverride"] == {"toolkitRunId": run_id, "cutRevision": 0}
    assert [(shot["startSeconds"], shot["endSeconds"]) for shot in body["shots"]] == [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0)]
    assert [shot["notes"] for shot in body["shots"]] == ["", "", "保持的镜头"]
    assert body["shots"][0]["prompts"] == {"positiveZh": "", "negativeZh": "", "positiveEn": "", "negativeEn": ""}
    frame = client.get(f"{BASE}/shots/shot-003/frame")
    assert frame.status_code == 200 and frame.content.startswith(b"\xff\xd8\xff")
    stale_frame = client.get(f"{BASE}/shots/shot-002/frame", params={"sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID, "revision": original["revision"]})
    assert stale_frame.status_code == 409
    package = client.get(f"{BASE}/package")
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        timeline = json.loads(archive.read("timeline.json"))
        assert [(shot["startSeconds"], shot["endSeconds"]) for shot in timeline["shots"]] == [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0)]
    stale_package = client.get(f"{BASE}/package", params={"sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID, "revision": original["revision"]})
    assert stale_package.status_code == 409

    restored = client.post(f"{BASE}/timeline/restore", json={
        "revision": body["revision"], "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["timelineOverride"] is None
    assert [(shot["startSeconds"], shot["endSeconds"]) for shot in restored.json()["shots"]] == [(0.0, 1.0), (1.0, 2.0)]
    assert restored.json()["shots"][1]["notes"] == "保持的镜头"


def test_toolkit_timeline_rejects_wrong_source_and_late_cut_revision(tmp_path):
    client, _ = _setup(tmp_path)
    state = client.get(BASE).json()
    wrong_source_run = _toolkit_scenes(tmp_path, run_id="other-source", asset_id="reference:video-other")
    wrong_source = client.post(f"{BASE}/timeline/apply", json={
        "revision": state["revision"], "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
        "toolkitRunId": wrong_source_run, "cutRevision": 0,
    })
    assert wrong_source.status_code == 409
    assert wrong_source.json()["detail"]["code"] == "shot_preparation_toolkit_source_mismatch"

    run_id = _toolkit_scenes(tmp_path, run_id="newer-cuts", revision=1)
    stale = client.post(f"{BASE}/timeline/apply", json={
        "revision": state["revision"], "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
        "toolkitRunId": run_id, "cutRevision": 0,
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "shot_preparation_toolkit_cuts_stale"


def test_timeline_epoch_hides_person_controls_from_the_previous_shot_layout(tmp_path):
    client, _ = _setup(tmp_path)
    person_store = PersonControlStore(tmp_path)
    old = person_store.new_run(SOURCE_ID, PREPROCESSING_ID, "shot-002")
    append_queued_run(old)
    person_store.save(PROJECT_ID, SOURCE_ID, PREPROCESSING_ID, "shot-002", old)
    run_id = _toolkit_scenes(tmp_path)
    state = client.get(BASE).json()

    applied = client.post(f"{BASE}/timeline/apply", json={
        "revision": state["revision"], "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
        "toolkitRunId": run_id, "cutRevision": 0,
    })
    assert applied.status_code == 200, applied.text
    unchanged_range = applied.json()["shots"][2]
    assert unchanged_range["id"] == "shot-003"
    assert unchanged_range["personControl"]["status"] == "not_started"


def test_completed_pipeline_scene_step_is_available_before_later_steps_finish(tmp_path):
    client, _ = _setup(tmp_path)
    virtual_run_id = _pipeline_scenes(tmp_path)
    state = client.get(BASE).json()
    assert state["toolkitScenes"] == [{"toolkitRunId": virtual_run_id, "cutRevision": 0, "cuts": [0.75], "createdAt": "2026-09-18T00:00:00+00:00", "label": "流水线分镜 · pipeline · 2 镜头"}]

    applied = client.post(f"{BASE}/timeline/apply", json={
        "revision": state["revision"], "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
        "toolkitRunId": virtual_run_id, "cutRevision": 0,
    })
    assert applied.status_code == 200, applied.text
    assert [(shot["startSeconds"], shot["endSeconds"]) for shot in applied.json()["shots"]] == [(0.0, 0.75), (0.75, 2.0)]


def test_preparation_covers_the_entire_video_exports_real_representative_frames_and_keeps_empty_drafts(tmp_path):
    client, _ = _setup(tmp_path)

    preparation = client.get(BASE)
    assert preparation.status_code == 200, preparation.text
    body = preparation.json()
    assert body["sourceId"] == SOURCE_ID
    assert body["preprocessingId"] == PREPROCESSING_ID
    assert [(shot["startSeconds"], shot["endSeconds"]) for shot in body["shots"]] == [(0.0, 1.0), (1.0, 2.0)]
    assert [shot["representativeSeconds"] for shot in body["shots"]] == [0.5, 1.5]
    assert all(shot["notes"] == "" for shot in body["shots"])
    assert all(shot["prompts"] == {
        "positiveZh": "", "negativeZh": "", "positiveEn": "", "negativeEn": "",
    } for shot in body["shots"])

    frame = client.get(f"{BASE}/shots/{body['shots'][1]['id']}/frame")
    assert frame.status_code == 200, frame.text
    assert frame.headers["content-type"] == "image/jpeg"
    assert frame.content.startswith(b"\xff\xd8\xff")

    package = client.get(f"{BASE}/package")
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        timeline = json.loads(archive.read("timeline.json"))
        assert [(shot["startSeconds"], shot["endSeconds"]) for shot in timeline["shots"]] == [(0.0, 1.0), (1.0, 2.0)]
        assert {"frames/shot-001.jpg", "frames/shot-002.jpg", "manifest.json"} <= set(archive.namelist())


def test_save_uses_source_and_revision_cas_and_rejects_replaced_source(tmp_path):
    client, project = _setup(tmp_path)
    first = client.get(BASE).json()
    saved = client.put(BASE, json={
        "revision": 0, "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
        "shots": [{
            "id": "shot-001", "notes": "第一镜", "prompts": {
                "positiveZh": "红色", "negativeZh": "", "positiveEn": "red", "negativeEn": "",
            },
        }, {
            "id": "shot-002", "notes": "", "prompts": {
                "positiveZh": "", "negativeZh": "", "positiveEn": "", "negativeEn": "",
            },
        }],
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert saved.json()["shots"][0]["notes"] == "第一镜"

    stale = client.put(BASE, json={
        "revision": 0, "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID, "shots": [],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "shot_preparation_conflict"

    project.referenceMedia = SimpleNamespace(id="video-002", type="video", format="mp4", durationSeconds=2.0)
    project.localPreprocessing = SimpleNamespace(
        id="prep-002", sourceReferenceMediaId="video-002", mediaType="video", status="completed",
    )
    replaced = client.get(BASE)
    assert replaced.status_code == 409
    assert replaced.json()["detail"]["code"] == "shot_preparation_source_unavailable"


def test_save_rejects_a_request_bound_to_the_previous_source_after_replacement(tmp_path):
    client, project = _setup(tmp_path)
    original = client.get(BASE).json()
    replacement = tmp_path / f"project-files/{PROJECT_ID}/reference-media/video-002.mp4"
    _make_video(replacement)
    replacement_artifacts = tmp_path / f"project-files/{PROJECT_ID}/local-preprocessing/prep-002"
    replacement_artifacts.mkdir(parents=True)
    (replacement_artifacts / "scene-changes.json").write_text(json.dumps({"sceneChanges": []}), encoding="utf-8")
    project.referenceMedia = SimpleNamespace(id="video-002", type="video", format="mp4", durationSeconds=2.0)
    project.localPreprocessing = SimpleNamespace(
        id="prep-002", sourceReferenceMediaId="video-002", mediaType="video", status="completed",
    )

    stale = client.put(BASE, json={
        "revision": original["revision"], "sourceId": original["sourceId"],
        "preprocessingId": original["preprocessingId"],
        "shots": [{"id": shot["id"], "notes": "旧源", "prompts": shot["prompts"]} for shot in original["shots"]],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "shot_preparation_stale"


def test_package_does_not_mark_a_shot_ready_when_only_one_prompt_field_is_filled(tmp_path):
    client, _ = _setup(tmp_path)
    preparation = client.get(BASE).json()
    saved = client.put(BASE, json={
        "revision": preparation["revision"], "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID,
        "shots": [{
            "id": shot["id"], "notes": shot["notes"],
            "prompts": {
                "positiveZh": "仅这一项" if shot["id"] == "shot-001" else "完整正向",
                "negativeZh": "" if shot["id"] == "shot-001" else "完整负向",
                "positiveEn": "" if shot["id"] == "shot-001" else "complete positive",
                "negativeEn": "" if shot["id"] == "shot-001" else "complete negative",
            },
        } for shot in preparation["shots"]],
    })
    assert saved.status_code == 200
    package = client.get(f"{BASE}/package")
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["promptReadiness"] == {"allShotsPrepared": False, "preparedShotCount": 1}
        readme = archive.read("README.txt").decode()
        assert "原始参考视频" in readme and "pose" in readme and "mask" in readme


def test_analyze_requires_disclosure_and_commits_only_when_its_revision_is_current(tmp_path):
    client, project = _setup(tmp_path)
    project.semanticAnalysis = SimpleNamespace(id="analysis-001")
    calls = []

    def analyze_shot(received_project, shot):
        calls.append((received_project.id, shot["id"]))
        return {"notes": "模型结果", "prompts": {"positiveZh": "蓝色", "negativeZh": "", "positiveEn": "blue", "negativeEn": ""}}

    app = FastAPI()
    app.include_router(create_shot_preparation_router(tmp_path, lambda _: project, analyze_shot=analyze_shot))
    client = TestClient(app)
    preparation = client.get(BASE).json()
    assert preparation["canAnalyze"] is True
    blocked = client.post(f"{BASE}/shots/shot-001/analyze", json={
        "revision": 0, "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID, "disclosureAccepted": False,
    })
    assert blocked.status_code == 422
    completed = client.post(f"{BASE}/shots/shot-001/analyze", json={
        "revision": 0, "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID, "disclosureAccepted": True,
    })
    assert completed.status_code == 200, completed.text
    assert calls == [(PROJECT_ID, "shot-001")]
    assert completed.json()["shots"][0]["notes"] == "模型结果"


def test_analyze_failure_releases_the_shot_and_late_result_cannot_overwrite_a_saved_draft(tmp_path):
    client, project = _setup(tmp_path)
    project.semanticAnalysis = SimpleNamespace(id="analysis-001")
    calls = 0

    def analyze_shot(_, shot):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider unavailable")
        current = client.get(BASE).json()
        saved = client.put(BASE, json={
            "revision": current["revision"], "sourceId": current["sourceId"],
            "preprocessingId": current["preprocessingId"],
            "shots": [{
                "id": item["id"], "notes": "用户保存" if item["id"] == shot["id"] else item["notes"],
                "prompts": item["prompts"],
            } for item in current["shots"]],
        })
        assert saved.status_code == 200
        return {"notes": "迟到的模型结果", "prompts": {"positiveZh": "", "negativeZh": "", "positiveEn": "late", "negativeEn": ""}}

    app = FastAPI()
    app.include_router(create_shot_preparation_router(tmp_path, lambda _: project, analyze_shot=analyze_shot))
    client = TestClient(app)
    body = client.get(BASE).json()
    request = {"revision": 0, "sourceId": SOURCE_ID, "preprocessingId": PREPROCESSING_ID, "disclosureAccepted": True}
    assert client.post(f"{BASE}/shots/shot-001/analyze", json=request).status_code == 502
    late = client.post(f"{BASE}/shots/shot-001/analyze", json=request)
    assert late.status_code == 409
    assert late.json()["detail"]["code"] == "shot_preparation_stale"
    assert client.get(BASE).json()["shots"][0]["notes"] == "用户保存"
