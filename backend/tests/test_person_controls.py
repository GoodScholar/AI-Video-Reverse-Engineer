import json
import shutil
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shot_preparation_api import create_shot_preparation_router


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要本地 FFmpeg")

PROJECT_ID = "project-001"
SOURCE_ID = "video-001"
PREPROCESSING_ID = "prep-001"
BASE = f"/api/projects/{PROJECT_ID}/preparation"


class ManualQueue:
    def __init__(self):
        self.jobs = []

    def submit(self, kind, project_id, handler):
        self.jobs.append((kind, project_id, handler))
        return True

    def run_next(self):
        _, project_id, handler = self.jobs.pop(0)
        handler(project_id)


def _video(path):
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "color=c=red:s=64x48:d=1", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def _worker(path, *, broken=False):
    path.write_text("""\
import argparse, json, shutil
from pathlib import Path
p = argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--output'); p.add_argument('--model'); p.add_argument('--ffmpeg'); p.add_argument('--check', action='store_true'); a = p.parse_args()
if a.check: print(json.dumps({'status':'passed'})); raise SystemExit(0)
o = Path(a.output); o.mkdir(parents=True, exist_ok=True)
if %s: raise SystemExit(2)
for n in ('pose.mp4','mask.mp4','overlay.mp4'): shutil.copyfile(a.input, o/n)
point = {'x': .5, 'y': .5, 'z': 0, 'presence': .9, 'visibility': .9}
(o/'landmarks.jsonl').write_text(''.join(json.dumps({'timeSeconds': i / 8, 'personCount': 1, 'landmarks33': [point] * 33, 'presence': [.9] * 33, 'visibility': [.9] * 33}) + '\\n' for i in range(8)))
(o/'quality.json').write_text(json.dumps({'status':'passed','frameCount':8,'detectedFrameCount':8,'missingTimesSeconds':[],'multiplePersonTimesSeconds':[],'lowConfidenceTimesSeconds':[],'invalidLandmarkTimesSeconds':[],'unusableMaskTimesSeconds':[],'message':'ok'}))
(o/'metadata.json').write_text(json.dumps({'schemaVersion':1,'model':'fake','modelSha256':'0' * 64,'scope':'single_person','poseFormat':'mediapipe33','frameRate':8,'width':64,'height':48,'frameCount':8}))
""" % repr(broken), encoding="utf-8")


def _setup(tmp_path, *, broken=False, queue=None):
    media = tmp_path / f"project-files/{PROJECT_ID}/reference-media/{SOURCE_ID}.mp4"
    media.parent.mkdir(parents=True, exist_ok=True); _video(media)
    prep = tmp_path / f"project-files/{PROJECT_ID}/local-preprocessing/{PREPROCESSING_ID}"
    prep.mkdir(parents=True, exist_ok=True)
    (prep / "scene-changes.json").write_text(json.dumps({'schemaVersion': 1, 'sceneChanges': []}))
    model = tmp_path / "model.task"; model.write_bytes(b"model")
    worker = tmp_path / "worker.py"; _worker(worker, broken=broken)
    project = SimpleNamespace(id=PROJECT_ID, referenceMedia=SimpleNamespace(id=SOURCE_ID, type="video", format="mp4", durationSeconds=1.0), localPreprocessing=SimpleNamespace(id=PREPROCESSING_ID, sourceReferenceMediaId=SOURCE_ID, mediaType="video", status="completed"), depthCaptures=[], activeDepthCaptureId=None, semanticAnalysis=None)
    app = FastAPI(); queue = queue or ManualQueue()
    app.include_router(create_shot_preparation_router(tmp_path, lambda _: project, compute_queue=queue, person_worker_python=sys.executable, person_worker_script=worker, person_model=model))
    return TestClient(app), project, queue, worker


def _start(client):
    body = client.get(BASE).json()
    return client.post(f"{BASE}/shots/shot-001/person-control", json={'sourceId': SOURCE_ID, 'preprocessingId': PREPROCESSING_ID, 'revision': body['revision']})


def test_person_control_queues_and_exposes_completed_artifacts(tmp_path):
    client, _, queue, _ = _setup(tmp_path)
    assert client.get(BASE).json()['shots'][0]['personControl']['status'] == 'not_started'
    queued = _start(client)
    assert queued.status_code == 202, queued.text
    assert queued.json()['shots'][0]['personControl']['status'] == 'queued'
    queue.run_next()
    completed = client.get(BASE).json()['shots'][0]
    assert completed['personControl']['status'] == 'completed'
    assert completed['controls']['pose'] == 'available'
    preview = client.get(f"{BASE}/shots/shot-001/person-control/pose", params={'runId': completed['personControl']['runId']})
    assert preview.status_code == 200 and len(preview.content) > 0
    partial = client.get(f"{BASE}/shots/shot-001/person-control/pose", params={'runId': completed['personControl']['runId']}, headers={'Range': 'bytes=0-0'})
    assert partial.status_code == 206 and len(partial.content) == 1
    assert partial.headers['content-range'] == f"bytes 0-0/{len(preview.content)}"
    invalid_range = client.get(f"{BASE}/shots/shot-001/person-control/pose", params={'runId': completed['personControl']['runId']}, headers={'Range': 'bytes=999999999-'})
    assert invalid_range.status_code == 416
    assert invalid_range.headers['content-range'] == f"bytes */{len(preview.content)}"
    package = client.get(f"{BASE}/package")
    with zipfile.ZipFile(BytesIO(package.content)) as archive:
        assert {
            'controls/shot-001/pose.mp4', 'controls/shot-001/mask.mp4',
            'controls/shot-001/overlay.mp4', 'controls/shot-001/quality.json',
        } <= set(archive.namelist())


def test_person_control_failed_run_can_retry(tmp_path):
    client, _, queue, worker = _setup(tmp_path, broken=True)
    assert _start(client).status_code == 202
    queue.run_next()
    assert client.get(BASE).json()['shots'][0]['personControl']['status'] == 'failed'
    _worker(worker, broken=False)
    assert _start(client).status_code == 202
    queue.run_next()
    assert client.get(BASE).json()['shots'][0]['personControl']['status'] == 'completed'


def test_person_control_hides_result_when_source_is_replaced(tmp_path):
    client, project, queue, _ = _setup(tmp_path)
    assert _start(client).status_code == 202
    project.referenceMedia.id = 'video-002'
    project.localPreprocessing.sourceReferenceMediaId = 'video-002'
    queue.run_next()
    # The old run is never projected onto the new source.
    assert client.get(BASE).status_code == 409


def test_person_control_environment_reports_missing_paths_without_loading_model(tmp_path):
    client, _, _, _ = _setup(tmp_path)
    # The configured files are present and the endpoint is purely a lightweight check.
    environment = client.get(BASE).json()['personControlEnvironment']
    assert environment['ready'] is True
    assert '未' in environment['message']


def test_person_control_marks_environment_missing_as_unavailable(tmp_path):
    client, _, _, _ = _setup(tmp_path)
    (tmp_path / 'model.task').unlink()
    shot = client.get(BASE).json()['shots'][0]
    assert shot['controls']['pose'] == 'unavailable'
    assert shot['personControl']['status'] == 'not_started'


def test_restart_marks_queued_person_control_failed_for_retry(tmp_path):
    client, _, _, _ = _setup(tmp_path)
    assert _start(client).status_code == 202
    restarted, _, _, _ = _setup(tmp_path)
    assert restarted.get(BASE).json()['shots'][0]['personControl']['status'] == 'failed'


def test_corrupt_completed_artifacts_are_not_ready_or_exported(tmp_path):
    client, _, queue, _ = _setup(tmp_path)
    assert _start(client).status_code == 202
    queue.run_next()
    run_id = client.get(BASE).json()['shots'][0]['personControl']['runId']
    (tmp_path / f'project-files/{PROJECT_ID}/person-controls/{SOURCE_ID}/{PREPROCESSING_ID}/shot-001/{run_id}/pose.mp4').write_bytes(b'broken')
    shot = client.get(BASE).json()['shots'][0]
    assert shot['controls']['pose'] == 'failed'
    assert client.get(f'{BASE}/shots/shot-001/person-control/pose', params={'runId': run_id}).status_code == 404
    package = client.get(f'{BASE}/package')
    with zipfile.ZipFile(BytesIO(package.content)) as archive:
        assert not any(name.startswith('controls/shot-001/') for name in archive.namelist())


@pytest.mark.parametrize('mutation', ['multiple_people', 'low_confidence'])
def test_passed_quality_must_match_person_and_confidence_rows(tmp_path, mutation):
    client, _, queue, _ = _setup(tmp_path)
    assert _start(client).status_code == 202
    queue.run_next()
    run_id = client.get(BASE).json()['shots'][0]['personControl']['runId']
    records = tmp_path / f'project-files/{PROJECT_ID}/person-controls/{SOURCE_ID}/{PREPROCESSING_ID}/shot-001/{run_id}/landmarks.jsonl'
    rows = [json.loads(line) for line in records.read_text().splitlines()]
    if mutation == 'multiple_people':
        rows[0]['personCount'] = 2
    else:
        rows[0]['landmarks33'][11]['presence'] = .1
        rows[0]['landmarks33'][11]['visibility'] = .1
        rows[0]['presence'][11] = .1
        rows[0]['visibility'][11] = .1
    records.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    shot = client.get(BASE).json()['shots'][0]
    assert shot['personControl']['status'] == 'failed'
    assert shot['controls']['pose'] == 'failed'


def test_invalid_persisted_status_is_rejected_without_server_error(tmp_path):
    client, _, queue, _ = _setup(tmp_path)
    assert _start(client).status_code == 202
    queue.run_next()
    run_id = client.get(BASE).json()['shots'][0]['personControl']['runId']
    state_path = tmp_path / f'project-files/{PROJECT_ID}/person-controls/{SOURCE_ID}/{PREPROCESSING_ID}/shot-001/state.json'
    state = json.loads(state_path.read_text())
    state['runs'][-1]['status'] = []
    state_path.write_text(json.dumps(state))
    response = client.get(BASE)
    assert response.status_code == 200
    assert response.json()['shots'][0]['personControl']['status'] == 'failed'
