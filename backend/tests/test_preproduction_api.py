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

from app.preproduction_api import create_preproduction_router


PROJECT_ID = "project-001"
BASE = f"/api/projects/{PROJECT_ID}/preproduction"


class Queue:
    def __init__(self):
        self.jobs = []

    def submit(self, kind, project_id, handler):
        self.jobs.append((project_id, handler))
        return True

    def run(self):
        project_id, handler = self.jobs.pop(0)
        handler(project_id)


def make_video(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=6:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def setup(tmp_path, *, runner=None):
    source = tmp_path / f"project-files/{PROJECT_ID}/reference-media/ref-001.mp4"
    make_video(source)
    project = SimpleNamespace(
        id=PROJECT_ID,
        referenceMedia=SimpleNamespace(
            id="ref-001", type="video", format="mp4", originalName="reference.mp4",
            durationSeconds=2.0, width=64, height=48,
        ),
        localPreprocessing=SimpleNamespace(
            id="prep-001", sourceReferenceMediaId="ref-001", mediaType="video", status="completed",
        ),
    )
    preparation = tmp_path / f"project-files/{PROJECT_ID}/local-preprocessing/prep-001"
    preparation.mkdir(parents=True)
    (preparation / "scene-changes.json").write_text(json.dumps({"sceneChanges": []}))
    queue = Queue()
    app = FastAPI()
    app.include_router(create_preproduction_router(
        tmp_path, lambda _: project, queue, runner=runner,
    ))
    return TestClient(app), queue, project


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要本地 FFmpeg")


def test_save_uses_cas_and_marks_downstream_node_stale(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-reference", json={"revision": state["revision"]}).json()
    reference_id = imported["assets"][0]["id"]
    saved = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
            "shots": [{"id": "shot-a", "title": "镜头 A", "duration": 2, "prompt": "晴天", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "trim", "kind": "trim", "input": f"asset:{reference_id}", "params": {"start": 0, "end": 1}},
            {"id": "frame", "kind": "first_frame", "input": "node:trim", "params": {}},
        ]}],
    })
    assert saved.status_code == 200, saved.text
    current = saved.json()
    stale = client.put(BASE, json={"revision": 0, "brief": current["brief"], "shots": current["shots"]})
    assert stale.status_code == 409

    changed = current["shots"]
    changed[0]["nodes"][0]["params"]["end"] = 1.5
    updated = client.put(BASE, json={"revision": current["revision"], "brief": current["brief"], "shots": changed})
    assert updated.status_code == 200
    workspace = updated.json()
    assert workspace["shots"][0]["nodes"][1]["status"] == "stale"
    assert client.post(BASE + "/package", json={"revision": workspace["revision"]}).status_code == 409


def test_rejects_invalid_node_dependency_and_does_not_mutate_reference_media(tmp_path):
    client, _, project = setup(tmp_path)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-reference", json={"revision": state["revision"]}).json()
    reference_id = imported["assets"][0]["id"]
    bad = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
        "shots": [{"id": "shot-a", "title": "A", "duration": 1, "prompt": "", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "late", "kind": "first_frame", "input": "node:early", "params": {}},
            {"id": "early", "kind": "reference", "input": f"asset:{reference_id}", "params": {}},
        ]}],
    })
    assert bad.status_code == 422
    self_cycle = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
        "shots": [{"id": "shot-b", "title": "B", "duration": 1, "prompt": "", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "self", "kind": "first_frame", "input": "node:self", "params": {}},
        ]}],
    })
    assert self_cycle.status_code == 422
    repeated = client.post(BASE + "/import-reference", json={"revision": imported["revision"]})
    assert repeated.status_code == 200
    assert project.referenceMedia.id == "ref-001"
    assert repeated.json()["assets"][0]["id"].startswith("reference-")


def test_upload_validates_real_media_and_serves_only_owned_asset(tmp_path):
    client, _, _ = setup(tmp_path)
    invalid = client.post(BASE + "/assets?role=character", files={"file": ("bad.mp4", b"not-a-video", "video/mp4")})
    assert invalid.status_code == 422
    valid = tmp_path / "input.mp4"
    make_video(valid)
    faststart = tmp_path / "faststart.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(valid), "-c", "copy", "-movflags", "+faststart", str(faststart)], check=True)
    corrupted = bytearray(faststart.read_bytes())
    marker = corrupted.index(b"mdat")
    box_start = marker - 4
    box_size = int.from_bytes(corrupted[box_start:marker], "big")
    corrupted[marker + 4:box_start + box_size] = b"\xff" * (box_size - 8)
    unreadable = client.post(BASE + "/assets?role=motion", files={"file": ("broken.mp4", bytes(corrupted), "video/mp4")})
    assert unreadable.status_code == 422
    uploaded = client.post(BASE + "/assets?role=motion", files={"file": ("motion.mp4", valid.read_bytes(), "video/mp4")})
    assert uploaded.status_code == 200, uploaded.text
    asset = uploaded.json()["assets"][0]
    assert asset["url"] == BASE + f"/assets/{asset['id']}/file"
    assert client.get(BASE + f"/assets/{asset['id']}/file").status_code == 200
    assert client.get(BASE + "/assets/../state/file").status_code == 404


def test_run_waits_for_input_then_publishes_current_artifact_and_restart_fails_inflight(tmp_path):
    def runner(kind, source, destination, params, **_):
        assert source.is_file()
        (destination / "output.png").write_bytes(b"image")
        return ["output.png"]

    client, queue, project = setup(tmp_path, runner=runner)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-reference", json={"revision": state["revision"]}).json()
    reference_id = imported["assets"][0]["id"]
    saved = client.put(BASE, json={
        "revision": imported["revision"], "brief": imported["brief"],
        "shots": [{"id": "shot-a", "title": "A", "duration": 1, "prompt": "", "negativePrompt": "", "assetIds": [reference_id], "nodes": [
            {"id": "source", "kind": "reference", "input": f"asset:{reference_id}", "params": {}},
            {"id": "frame", "kind": "first_frame", "input": "node:source", "params": {}},
        ]}],
    }).json()
    blocked = client.post(BASE + "/shots/shot-a/nodes/frame/run", json={"revision": saved["revision"]})
    assert blocked.status_code == 409
    started = client.post(BASE + "/shots/shot-a/nodes/source/run", json={"revision": saved["revision"]})
    assert started.status_code == 202
    assert client.post(BASE + "/shots/shot-a/nodes/frame/run", json={"revision": started.json()["revision"]}).status_code == 409
    queue.run()
    after_source = client.get(BASE).json()
    started_frame = client.post(BASE + "/shots/shot-a/nodes/frame/run", json={"revision": after_source["revision"]})
    assert started_frame.status_code == 202
    queue.run()
    done = client.get(BASE).json()
    node = done["shots"][0]["nodes"][1]
    assert node["status"] == "completed"
    assert client.get(BASE + "/artifacts/shot-a/frame/output.png").content == b"image"

    # Simulate persisted work from a process that was interrupted before completion.
    path = tmp_path / f"project-files/{PROJECT_ID}/preproduction/state.json"
    payload = json.loads(path.read_text())
    payload["shots"][0]["nodes"][1]["status"] = "running"
    path.write_text(json.dumps(payload))
    app = FastAPI()
    app.include_router(create_preproduction_router(tmp_path, lambda _: project, Queue(), runner=runner))
    restarted = TestClient(app).get(BASE).json()
    assert restarted["shots"][0]["nodes"][1]["status"] == "failed"


def test_prompt_node_receives_the_complete_brief_and_shot_prompts(tmp_path):
    captured = []
    def runner(kind, source, destination, params, **_):
        assert kind == "prompt" and source is None
        captured.append(params["text"])
        (destination / "prompt.txt").write_text(params["text"])
        return ["prompt.txt"]

    client, queue, _ = setup(tmp_path, runner=runner)
    state = client.get(BASE).json()
    state["brief"].update({"theme": "主题", "purpose": "用途", "style": "风格", "duration": 12, "aspect": "16:9", "mustPreserve": "保留红伞"})
    saved = client.put(BASE, json={"revision": state["revision"], "brief": state["brief"], "shots": [{
        "id": "shot-a", "title": "A", "duration": 1, "prompt": "镜头提示", "negativePrompt": "不要模糊", "assetIds": [],
        "nodes": [{"id": "prompt", "kind": "prompt", "input": "", "params": {"text": "节点文字"}}],
    }]}).json()
    assert client.post(BASE + "/shots/shot-a/nodes/prompt/run", json={"revision": saved["revision"]}).status_code == 202
    queue.run()
    assert "保留红伞" in captured[0] and "16:9" in captured[0] and "12" in captured[0]
    assert "不要模糊" in captured[0] and "节点文字" in captured[0]


def test_import_shots_and_package_is_consistent_and_path_safe(tmp_path):
    client, queue, _ = setup(tmp_path)
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-shots", json={"revision": state["revision"]})
    assert imported.status_code == 200
    workspace = imported.json()
    shot = workspace["shots"][0]
    assert shot["assetIds"] and shot["nodes"][0]["params"] == {"start": 0.0, "end": 2.0}
    assert client.post(BASE + f"/shots/{shot['id']}/nodes/trim/run", json={"revision": workspace["revision"]}).status_code == 202
    queue.run()
    completed = client.get(BASE).json()
    repeated = client.post(BASE + "/import-shots", json={"revision": completed["revision"]})
    assert len(repeated.json()["shots"]) == 1
    package = client.post(BASE + "/package", json={"revision": repeated.json()["revision"]})
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = archive.namelist()
        workspace = json.loads(archive.read("workspace.json"))
        assert "workspace.json" in names
        assert any(name.startswith("assets/reference-") for name in names)
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        assert workspace["assets"][0]["url"] in names
        assert workspace["shots"][0]["nodes"][0]["artifacts"][0]["url"] in names


def test_import_toolkit_copies_only_completed_video_outputs_once(tmp_path):
    client, _, _ = setup(tmp_path)
    directory = tmp_path / f"project-files/{PROJECT_ID}/toolkit/run-001"
    directory.mkdir(parents=True)
    make_video(directory / "output.mp4")
    (directory / "state.json").write_text(json.dumps({
        "id": "run-001", "status": "completed", "kind": "interpolate", "artifacts": ["output.mp4"],
    }))
    state = client.get(BASE).json()
    imported = client.post(BASE + "/import-toolkit", json={"revision": state["revision"]})
    assert imported.status_code == 200, imported.text
    asset = imported.json()["assets"][0]
    assert asset["role"] == "motion" and asset["kind"] == "video"
    repeated = client.post(BASE + "/import-toolkit", json={"revision": imported.json()["revision"]})
    assert repeated.status_code == 200
    assert len(repeated.json()["assets"]) == 1


def test_real_delivery_handoff_preserves_other_shot_after_local_revision(tmp_path):
    from PIL import Image
    from urllib.parse import unquote
    import re

    client, queue, project = setup(tmp_path)
    image = io.BytesIO()
    Image.new('RGB', (64, 64), 'orange').save(image, format='PNG')
    uploaded = client.post(BASE + '/assets?role=character', files={
        'file': ('角色 [橙色].png', image.getvalue(), 'image/png'),
    })
    assert uploaded.status_code == 200, uploaded.text
    state = uploaded.json()
    character = state['assets'][0]['id']
    state = client.post(BASE + '/import-reference', json={'revision': state['revision']}).json()
    motion = state['assets'][1]['id']
    state['brief'].update(theme='露营角色演示', mustPreserve='橙色外观', duration=2, aspect='4:3')
    state['shots'] = [
        {'id': shot_id, 'title': title, 'duration': 1, 'prompt': prompt, 'negativePrompt': '避免形变',
         'assetIds': [character, motion], 'nodes': [
             {'id': 'trim', 'kind': 'trim', 'input': f'asset:{motion}', 'params': {'start': 0, 'end': 1}},
             {'id': 'frame', 'kind': 'first_frame', 'input': 'node:trim', 'params': {}},
         ]}
        for shot_id, title, prompt in [('shot-a', '建立环境', '暖色露营'), ('shot-b', '角色收尾', '角色挥手')]
    ]
    def save(state):
        response = client.put(BASE, json={key: state[key] for key in ('revision', 'brief', 'shots')})
        assert response.status_code == 200, response.text
        return response.json()

    def run(shot_id, node_id):
        state = client.get(BASE).json()
        response = client.post(BASE + f'/shots/{shot_id}/nodes/{node_id}/run', json={'revision': state['revision']})
        assert response.status_code == 202, response.text
        queue.run()
        state = client.get(BASE).json()
        node = next(n for s in state['shots'] if s['id'] == shot_id for n in s['nodes'] if n['id'] == node_id)
        assert node['status'] == 'completed', node
        return state

    state = save(state)
    for shot_id in ('shot-a', 'shot-b'):
        for node_id in ('trim', 'frame'):
            state = run(shot_id, node_id)
    other = state['shots'][1]
    other_url = other['nodes'][1]['artifacts'][0]['url']
    other_bytes = client.get(other_url).content
    state['shots'][0]['nodes'][0]['params']['end'] = 0.5
    state = save(state)
    assert [n['status'] for n in state['shots'][0]['nodes']] == ['stale', 'stale']
    assert state['shots'][1] == other
    assert client.get(other_url).content == other_bytes
    assert client.post(BASE + '/package', json={'revision': state['revision']}).status_code == 409
    run('shot-a', 'trim')
    state = run('shot-a', 'frame')
    response = client.post(BASE + '/package', json={'revision': state['revision']})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert 'HANDOFF.md' in archive.namelist()
        handoff = archive.read('HANDOFF.md').decode()
        assert handoff.index('建立环境') < handoff.index('角色收尾')
        assert '暖色露营' in handoff and '角色挥手' in handoff and '橙色外观' in handoff
        assert '角色' in handoff and '0.5' in handoff
        links = re.findall(r'\]\(([^)]+)\)', handoff)
        assert links
        assert all(unquote(link) in archive.namelist() for link in links)
        assert str(tmp_path) not in handoff and '/api/projects/' not in handoff
        assert len([name for name in archive.namelist() if name.startswith('assets/')]) == 2
        exported = json.loads(archive.read('workspace.json'))
        second_frame = exported['shots'][1]['nodes'][1]['artifacts'][0]['url']
        assert archive.read(second_frame) == other_bytes
    assert project.referenceMedia.id == 'ref-001'


def test_delivery_warnings_are_actionable_and_do_not_block_export(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    state['brief']['duration'] = 5
    shot = {'id': 'shot-a', 'title': '文字镜头', 'duration': 2, 'prompt': '云海',
            'negativePrompt': '', 'assetIds': [], 'nodes': []}
    saved = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [shot]})
    assert saved.status_code == 200
    state = saved.json()
    by_code = {check.get('code'): check for check in state['checks']}
    assert by_code['brief_incomplete']['level'] == 'warning'
    assert by_code['duration_mismatch']['level'] == 'warning'
    assert by_code['shot_assets_missing']['shotId'] == 'shot-a'
    assert client.post(BASE + '/package', json={'revision': state['revision']}).status_code == 200
    state['brief'].update(theme='云海', aspect='16:9', duration=2)
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [shot]}).json()
    assert not any(c.get('code') in ('brief_incomplete', 'duration_mismatch') for c in state['checks'])


def test_delivery_duration_ignores_floating_point_noise(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.get(BASE).json()
    state['brief'].update(theme='短片', aspect='16:9', duration=.3)
    shots = [{'id': f'shot-{i}', 'title': '', 'duration': duration, 'prompt': '天空',
              'negativePrompt': '', 'assetIds': [], 'nodes': []} for i, duration in enumerate((.1, .2))]
    state = client.put(BASE, json={'revision': state['revision'], 'brief': state['brief'], 'shots': shots}).json()
    assert not any(c.get('code') == 'duration_mismatch' for c in state['checks'])


def test_browser_style_audio_recording_without_container_duration_is_imported(tmp_path):
    client, _, _ = setup(tmp_path)
    recording = tmp_path / 'recording.webm'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.5', '-c:a', 'libopus', '-live', '1', str(recording)], check=True)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(recording)]))
    assert 'duration' not in probe['format']
    response = client.post(BASE + '/assets?role=audio', files={'file': ('录音.webm', recording.read_bytes(), 'audio/webm')})
    assert response.status_code == 200, response.text
    asset = response.json()['assets'][0]
    assert asset['kind'] == 'audio' and .45 <= asset['duration'] <= .6
    assert client.get(asset['url']).status_code == 200
