import io
import zipfile
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def _project(video=True):
    media = SimpleNamespace(id="driver-1", type="video" if video else "image", width=512, height=512,
                            originalName="driver.mp4", format="mp4")
    return SimpleNamespace(id="project-1", referenceMedia=media)


def _png_bytes():
    body = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(body, format="PNG")
    return body.getvalue()


def _setup(tmp_path, client_factory=None):
    from app.character_motion_api import create_character_motion_router

    project = _project()
    driver = tmp_path / "project-files/project-1/reference-media/driver-1.mp4"
    driver.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","testsrc2=size=64x48:rate=30:duration=1","-c:v","libx264",str(driver)],check=True)
    app = FastAPI()
    app.include_router(create_character_motion_router(tmp_path, lambda _: project, client_factory=client_factory))
    return TestClient(app), project


BASE = "/api/projects/project-1/character-motion"


def test_motion_plan_is_independent_of_semantic_analysis_and_rejects_stale_revisions(tmp_path):
    client, _ = _setup(tmp_path)
    initial = client.get(BASE).json()
    assert initial["prompt"] == ""
    assert initial["driver"] is not None
    saved = client.put(BASE, json={"revision": 0, "prompt": "a dancer", "settings": initial["settings"], "comfyUrl": initial["comfyUrl"]})
    assert saved.status_code == 200
    assert client.put(BASE, json={"revision": 0, "prompt": "old", "settings": initial["settings"], "comfyUrl": initial["comfyUrl"]}).status_code == 409


def test_character_upload_decodes_image_and_never_replaces_project_reference_media(tmp_path):
    client, project = _setup(tmp_path)
    invalid = client.post(BASE + "/character", files={"file": ("role.png", b"not image", "image/png")})
    assert invalid.status_code == 422
    uploaded = client.post(BASE + "/character", files={"file": ("role.png", _png_bytes(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    state = uploaded.json()
    assert state["character"]["originalName"] == "role.png"
    assert project.referenceMedia.id == "driver-1"
    assert client.get(BASE).json()["character"]["id"] == state["character"]["id"]


def test_missing_or_unsafe_driver_blocks_export(tmp_path):
    client, project = _setup(tmp_path)
    client.post(BASE + "/character", files={"file": ("role.png", _png_bytes(), "image/png")})
    state = client.get(BASE).json()
    assert client.put(BASE, json={"revision": state["revision"], "prompt": "dancer", "settings": state["settings"], "comfyUrl": state["comfyUrl"]}).status_code == 200
    (tmp_path / "project-files/project-1/reference-media/driver-1.mp4").unlink()
    assert client.get(BASE + "/package").status_code == 409
    project.referenceMedia = SimpleNamespace(id="driver-2", type="image", width=512, height=512, originalName="still.png", format="png")
    assert client.get(BASE + "/package").status_code == 409


def test_export_includes_offline_assets_and_official_preprocess_snapshot(tmp_path):
    client, _ = _setup(tmp_path)
    assert client.post(BASE + "/character", files={"file": ("role.png", _png_bytes(), "image/png")}).status_code == 200
    state = client.get(BASE).json()
    assert client.put(BASE, json={"revision": state["revision"], "prompt": "a full-body dancer", "settings": state["settings"], "comfyUrl": state["comfyUrl"]}).status_code == 200
    package = client.get(BASE + "/package")
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = set(archive.namelist())
        assert {"workflow-api.json", "official-template.json", "manifest.json", "input/character.png", "input/driver.mp4"} <= names
        manifest = archive.read("manifest.json").decode()
        assert "video_wan2_2_14B_animate.json" in manifest
        graph = archive.read("workflow-api.json").decode()
        assert "DWPreprocessor" in graph and "WanAnimateToVideo" in graph
        assert "Sam2Segmentation" not in graph and "KSampler" in graph and "CreateVideo" in graph


class _Comfy:
    instances = 0
    def __init__(self, url):
        type(self).instances += 1
        self.url = url
    def close(self):
        pass
    def check(self, workflow):
        return {"connected": True, "ready": True, "version": "test", "missingNodes": [], "missingModels": [], "message": "就绪"}
    def upload_image(self, path, name):
        assert path.is_file()
        if path.suffix == ".mp4": _Comfy.video_bytes = path.read_bytes()
        return name
    def submit(self, workflow):
        return "prompt-1"
    def poll(self, prompt_id):
        return {"status": "completed", "outputs": [{"filename": "motion.mp4", "subfolder": "", "type": "output"}], "error": None}
    def fetch_output(self, output):
        return _Comfy.video_bytes


def test_final_generation_write_routes_are_disabled_without_side_effects(tmp_path):
    _Comfy.instances = 0
    client, _ = _setup(tmp_path, _Comfy)
    responses = [
        client.post(BASE + '/runs'),
        client.post(BASE + '/runs', json={'revision': 0, 'disclosureAccepted': True, 'preprocessorConfirmed': True}),
        client.post(BASE + '/runs/old-run/refresh', json={}),
        client.post(BASE + '/runs/old-run/resolve', json={'confirmedNotQueued': True}),
    ]
    assert [response.status_code for response in responses] == [410, 410, 410, 410]
    assert {response.json()['detail']['code'] for response in responses} == {'final_generation_disabled'}
    assert {response.json()['detail']['message'] for response in responses} == {'当前工作台只准备素材与方案，请导出后在外部工具执行视频生成。'}
    assert client.get(BASE).json()['runs'] == []
    assert _Comfy.instances == 0


def test_historical_generation_records_and_outputs_remain_read_only(tmp_path):
    from app.character_motion import CharacterMotionStore, MotionRun

    client, project = _setup(tmp_path)
    store = CharacterMotionStore(tmp_path)
    state = store.load(project)
    state.runs.append(MotionRun(id='old-run', status='completed', createdAt='2026-09-14T00:00:00Z',
        revision=1, sourceHash='old-hash', comfyUrl='http://127.0.0.1:8188', workflow={},
        outputs=[{'filename': 'output-0.mp4'}]))
    output = store.path(project.id, 'runs', 'old-run', 'outputs', 'output-0.mp4')
    output.parent.mkdir(parents=True)
    output.write_bytes(b'historical-video')
    store.save(project.id, state)

    history = client.get(BASE).json()['runs']
    assert history[0]['id'] == 'old-run'
    assert history[0]['status'] == 'completed'
    assert client.get(BASE + '/runs/old-run/output/0').content == b'historical-video'


def test_final_generation_disablement_precedes_character_motion_origin_checks(tmp_path):
    from app.main import create_app

    client = TestClient(create_app(data_dir=tmp_path))
    response = client.post("/api/projects/unknown/character-motion/runs", json={"revision": 0, "disclosureAccepted": True, "preprocessorConfirmed": True}, headers={"origin": "https://untrusted.example", "x-aivre-intent": "semantic-analysis"})
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "final_generation_disabled"
    upload = client.post("/api/projects/unknown/character-motion/character", files={"file": ("role.png", _png_bytes(), "image/png")}, headers={"origin": "https://untrusted.example"})
    assert upload.status_code == 403

def _ready_plan(client):
    client.post(BASE+'/character',files={'file':('role.png',_png_bytes(),'image/png')})
    state=client.get(BASE).json()
    return client.put(BASE,json={'revision':state['revision'],'prompt':'dancer','settings':state['settings'],'comfyUrl':state['comfyUrl']}).json()


def test_package_symlink_does_not_overwrite_outside_file(tmp_path):
    client,_=_setup(tmp_path);state=_ready_plan(client)
    assert client.get(BASE+'/package').status_code==200
    outside=tmp_path/'outside';outside.write_bytes(b'unchanged')
    target=tmp_path/f'project-files/project-1/character-motion/inputs-{state["revision"]}/character.png'
    target.unlink();target.symlink_to(outside)
    assert client.get(BASE+'/package').status_code==422
    assert outside.read_bytes()==b'unchanged'

def test_replacing_character_marks_plan_stale_until_saved(tmp_path):
    client,_=_setup(tmp_path);_ready_plan(client)
    replaced=client.post(BASE+'/character',files={'file':('other.png',_png_bytes(),'image/png')}).json()
    assert replaced['sourceHash'] is None and replaced['stale'] is True
    saved=client.put(BASE,json={'revision':replaced['revision'],'prompt':replaced['prompt'],'settings':replaced['settings'],'comfyUrl':replaced['comfyUrl']}).json()
    assert saved['stale'] is False and saved['sourceHash']
