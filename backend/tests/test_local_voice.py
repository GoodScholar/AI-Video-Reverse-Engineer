import json
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import local_voice
from app.local_voice import LocalVoiceService
from app.main import create_app


def test_catalog_rejects_incompatible_model_without_starting_worker(tmp_path):
    model=tmp_path/'model';model.mkdir()
    model.joinpath('model.safetensors').write_bytes(b'weights')
    model.joinpath('config.json').write_text(json.dumps({'model_type':'qwen3_tts','tts_model_type':'custom_voice','tts_model_size':'1b7','quantization':{'bits':8}}))
    python=tmp_path/'python';python.write_text('python')
    service=LocalVoiceService(root=tmp_path,python=python)
    assert service.catalog()['available'] is False


def test_unverified_custom_directory_is_not_labelled_with_official_revision(tmp_path):
    model=tmp_path/'other-model';model.mkdir()
    model.joinpath('config.json').write_text(json.dumps({'model_type':'qwen3_tts','tts_model_type':'custom_voice','tts_model_size':'0b6','quantization':{'bits':8}}))
    model.joinpath('model.safetensors').write_bytes(b'weights')
    tmp_path.joinpath('model-manifest.json').write_text(json.dumps({'verified':True,'revision':'old-official-revision'}))
    python=tmp_path/'python';python.write_text('python')
    service=LocalVoiceService(root=tmp_path,model=model,python=python)
    assert service.catalog()['available'] is True
    assert service.catalog()['modelRevision']=='unverified-local-directory'


def test_project_voice_catalog_is_persisted_scoped_and_playable(tmp_path):
    assert hasattr(local_voice, 'ProjectVoiceStore')
    source = tmp_path / 'voice.wav'
    source.write_bytes(b'project voice')
    store = local_voice.ProjectVoiceStore(tmp_path / 'data')
    store.install(
        'project-1',
        'original-video-voice',
        source,
        name='原视频口播音色',
        description='从原视频分离的人声',
        use_cases='商品口播',
        reference_text='这是一段参考口播。',
    )
    clone_model = tmp_path / 'clone-model'
    clone_model.mkdir()
    clone_model.joinpath('config.json').write_text(json.dumps({
        'model_type': 'qwen3_tts', 'tts_model_type': 'base',
        'tts_model_size': '0b6', 'quantization': {'bits': 8},
    }))
    clone_model.joinpath('model.safetensors').write_bytes(b'weights')
    python = tmp_path / 'python'
    python.write_text('python')
    service = LocalVoiceService(
        root=tmp_path / 'preset', python=python,
        data_dir=tmp_path / 'data', clone_model=clone_model,
    )

    project_voice = next(voice for voice in service.catalog('project-1')['voices'] if voice['id'] == 'original-video-voice')

    assert project_voice == {
        'id': 'original-video-voice',
        'name': '原视频口播音色',
        'description': '从原视频分离的人声',
        'useCases': '商品口播',
        'group': 'project',
        'available': True,
        'sampleUrl': '/api/projects/project-1/voices/original-video-voice/sample',
    }
    assert service.catalog('project-1')['available'] is True
    assert next(voice for voice in service.catalog('project-1')['voices'] if voice['id'] == 'serena')['available'] is False
    assert all(voice['id'] != 'original-video-voice' for voice in service.catalog('project-2')['voices'])
    assert service.sample('original-video-voice', project_id='project-1').read_bytes() == b'project voice'


def test_project_voice_id_cannot_shadow_a_preset_voice(tmp_path):
    source = tmp_path / 'voice.wav'
    source.write_bytes(b'project voice')
    store = local_voice.ProjectVoiceStore(tmp_path / 'data')

    with pytest.raises(ValueError, match='预置音色'):
        store.install('project-1', 'serena', source, name='重复音色', description='分离人声',
                      use_cases='商品口播', reference_text='参考口播原文。')


def test_project_voice_copy_failure_does_not_leave_a_partial_install(tmp_path, monkeypatch):
    source = tmp_path / 'voice.wav'
    source.write_bytes(b'project voice')
    store = local_voice.ProjectVoiceStore(tmp_path / 'data')
    def fail_after_partial_copy(_, target):
        Path(target).write_bytes(b'partial')
        raise OSError('copy failed')
    monkeypatch.setattr(local_voice.shutil, 'copyfile', fail_after_partial_copy)

    with pytest.raises(OSError, match='copy failed'):
        store.install('project-1', 'new-voice', source, name='新音色', description='分离人声',
                      use_cases='商品口播', reference_text='参考口播原文。')

    assert not store.path('project-1', 'new-voice', 'reference.wav').exists()
    assert store.load('project-1')['voices'] == []


def test_project_voice_synthesis_uses_clone_model_reference(tmp_path, monkeypatch):
    source = tmp_path / 'voice.wav'
    source.write_bytes(b'project voice')
    data_dir = tmp_path / 'data'
    store = local_voice.ProjectVoiceStore(data_dir)
    store.install('project-1', 'original-video-voice', source, name='原视频口播音色',
                  description='分离人声', use_cases='商品口播', reference_text='参考口播原文。')
    clone_model = tmp_path / 'clone-model'
    clone_model.mkdir()
    clone_model.joinpath('config.json').write_text(json.dumps({
        'model_type': 'qwen3_tts', 'tts_model_type': 'base',
        'tts_model_size': '0b6', 'quantization': {'bits': 8},
    }))
    clone_model.joinpath('model.safetensors').write_bytes(b'weights')
    python = tmp_path / 'python'
    python.write_text('python')

    class Stream:
        def __init__(self, response=''):
            self.writes = []
            self.response = response
        def write(self, value): self.writes.append(value)
        def flush(self): pass
        def readline(self): return self.response
        def close(self): pass
    class Process:
        def __init__(self, command):
            self.command = command
            self.stdin = Stream()
            self.stdout = Stream('{"status":"completed"}\n')
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0
    processes = []
    def popen(command, **kwargs):
        process = Process(command); processes.append(process); return process
    class Selector:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def register(self, *args): pass
        def select(self, timeout): return [object()]
    monkeypatch.setattr(local_voice.subprocess, 'Popen', popen)
    monkeypatch.setattr(local_voice.selectors, 'DefaultSelector', Selector)
    service = LocalVoiceService(root=tmp_path / 'preset', python=python, data_dir=data_dir, clone_model=clone_model)

    outputs = service.synthesize(['新生成的广告文案。'], 'original-video-voice', tmp_path / 'output', project_id='project-1')

    request = json.loads(processes[0].stdin.writes[0])
    assert Path(processes[0].command[-1]) == clone_model
    assert request == {
        'texts': ['新生成的广告文案。'],
        'refAudio': str(store.sample('project-1', 'original-video-voice').resolve()),
        'refText': '参考口播原文。',
        'directory': str((tmp_path / 'output').resolve()),
    }
    assert outputs == [tmp_path / 'output/0.wav']


def test_project_voice_routes_return_scoped_catalog_and_sample(tmp_path):
    source = tmp_path / 'voice.wav'
    source.write_bytes(b'project voice')
    store = local_voice.ProjectVoiceStore(tmp_path / 'data')
    store.install('project-1', 'original-video-voice', source, name='原视频口播音色',
                  description='分离人声', use_cases='商品口播', reference_text='参考口播原文。')
    service = LocalVoiceService(root=tmp_path / 'preset', data_dir=tmp_path / 'data')
    app = FastAPI()
    app.include_router(local_voice.create_voice_router(service))
    client = TestClient(app)

    catalog = client.get('/api/projects/project-1/voices')

    assert catalog.status_code == 200
    assert any(voice['id'] == 'original-video-voice' for voice in catalog.json()['voices'])
    assert client.get('/api/projects/project-2/voices').json()['voices'] == service.catalog('project-2')['voices']
    sample = client.get('/api/projects/project-1/voices/original-video-voice/sample')
    assert sample.status_code == 200
    assert sample.content == b'project voice'


def test_application_catalog_reads_project_voice_directory(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert client.get('/api/projects/missing-project/voices').status_code == 404
        project_id = client.post('/api/projects', json={'name': '项目音色验收'}).json()['id']
        source = tmp_path / 'voice.wav'
        source.write_bytes(b'project voice')
        local_voice.ProjectVoiceStore(tmp_path).install(
            project_id, 'original-video-voice', source, name='原视频口播音色',
            description='分离人声', use_cases='商品口播', reference_text='参考口播原文。',
        )

        catalog = client.get(f'/api/projects/{project_id}/voices')

        assert catalog.status_code == 200
        assert any(voice['id'] == 'original-video-voice' for voice in catalog.json()['voices'])
        assert client.get(f'/api/projects/{project_id}/voices/original-video-voice/sample').content == b'project voice'
