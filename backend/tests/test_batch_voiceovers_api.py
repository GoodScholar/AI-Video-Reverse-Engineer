"""Voice generation must bind saved scripts and produce editable, audible timelines."""
import json
import math
import struct
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.batch_editing_api import create_batch_editing_router


def write_audio(path, seconds):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(24000)
        out.writeframes(b''.join(struct.pack('<h', int(4000 * math.sin(i / 20))) for i in range(int(seconds * 24000))))


class VoiceService:
    def catalog(self):
        return {'available': True, 'voices': [{'id': 'serena', 'name': 'Serena', 'description': '温暖女声', 'useCases': '生活分享', 'sampleUrl': None}]}

    def synthesize(self, texts, voice, directory):
        assert voice == 'serena'
        outputs = []
        for i, text in enumerate(texts):
            path = directory / f'{i}.wav'
            write_audio(path, 1.5 + i)
            outputs.append(path)
        return outputs


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / 'project-files/p1/preproduction'
    (root / 'assets').mkdir(parents=True)
    (root / 'assets/first.png').write_bytes(b'image')
    (root / 'assets/second.png').write_bytes(b'image')
    (root / 'state.json').write_text(json.dumps({'schemaVersion': 1, 'revision': 0, 'brief': {}, 'shots': [], 'assets': [
        {'id': 'first', 'file': 'first.png', 'name': '倒豆', 'kind': 'image'},
        {'id': 'second', 'file': 'second.png', 'name': '注水', 'kind': 'image'}]}))
    return tmp_path


def client_for(root, service=None):
    jobs = []
    class Queue:
        def submit(self, kind, project, handler):
            jobs.append((project, handler)); return True
    app = FastAPI()
    app.include_router(create_batch_editing_router(root, lambda _: object(), Queue(), voice_service=service or VoiceService(),
        renderer=lambda timeline, sources, output, **kw: output.write_bytes(b'preview')))
    return TestClient(app), jobs


def arranged(api, point='咖啡制作'):
    base = '/api/projects/p1/batch-edits'
    task = api.post(base, json={'sellingPoint': point, 'script': '倒入咖啡豆。缓缓注入热水。'}).json()['task']
    tracks = task['variant']['tracks']
    tracks[0]['clips'] = [dict(id=f'clip{i}', assetId=asset, start=i*3, inPoint=0, duration=3, speed=1, volume=1, fadeIn=0, fadeOut=0)
                          for i, asset in enumerate(('first', 'second'))]
    api.put(f"{base}/{task['id']}/variant", json={'revision': 0, 'tracks': tracks})
    return next(t for t in api.get(base).json()['tasks'] if t['id'] == task['id'])


def request_for(task):
    return {'voiceId': 'serena', 'confirmScript': True, 'targets': [dict(taskId=task['id'], contentRevision=task.get('contentRevision', 0),
        variantRevision=task['variant']['revision'], subtitleRevision=task['variant'].get('subtitles', {}).get('revision', 0))]}


def test_voiceover_timing_tracks_and_review_invalidation(workspace):
    api, jobs = client_for(workspace)
    with api:
        task = arranged(api)
        base = f"/api/projects/p1/batch-edits/{task['id']}"
        preview = api.post(base+'/variant/previews', json={'revision': 1}).json()['variant']['runs'][-1]
        pid, handler = jobs.pop(); handler(pid)
        assert api.post(base+'/reviews', json={'runId': preview['id'], 'decision': 'approved', 'reason': '确认'}).status_code == 200
        submitted = api.post(base+'/voiceovers', json=request_for(task))
        assert submitted.status_code == 202, submitted.text
        assert submitted.json()['tasks'][0]['reviewStatus'] == 'stale'
        assert api.post(base+'/variant/previews', json={'revision': 2}).status_code == 409
        pid, handler = jobs.pop(); handler(pid)
        current = api.get('/api/projects/p1/batch-edits').json()['tasks'][0]
        assert current['voiceover']['status'] == 'completed', current
        assert current['voiceover']['voiceId'] == 'serena'
        assert 'plan' not in current['voiceover']
        cues = current['variant']['subtitles']['cues']
        assert [(c['start'], c['end'], c['text']) for c in cues] == [(0, 1.5, '倒入咖啡豆'), (1.5, 4, '缓缓注入热水')]
        voice_track = next(t for t in current['variant']['tracks'] if t['id'] == 'voiceover')
        assert [(c['start'], c['duration']) for c in voice_track['clips']] == [(0, 1.5), (1.5, 2.5)]
        assert all(c['volume'] == 0 for c in current['variant']['tracks'][0]['clips'])
        audio = [a for a in api.get('/api/projects/p1/batch-edits').json()['assets'] if a['kind'] == 'audio']
        assert len(audio) == 2
        assert api.post(base+'/reviews', json={'runId': preview['id'], 'decision': 'approved', 'reason': '旧预览'}).status_code == 409
        assert api.put(base+'/content', json={'revision': 0, 'sellingPoint': '咖啡', 'script': '新的文案。'}).status_code == 200
        assert api.post(base+'/variant/previews', json={'revision': current['variant']['revision']}).status_code == 409


def test_generation_rejects_unconfirmed_and_stale_requests(workspace):
    api, jobs = client_for(workspace)
    with api:
        task = arranged(api); base=f"/api/projects/p1/batch-edits/{task['id']}/voiceovers"
        body=request_for(task); body['confirmScript']=False
        assert api.post(base,json=body).status_code == 422
        body=request_for(task); body['targets'][0]['contentRevision']=99
        assert api.post(base,json=body).status_code == 409
        body=request_for(task); body['voiceId']='unknown'
        assert api.post(base,json=body).status_code == 422
        assert not jobs


def test_edit_during_generation_does_not_overwrite_new_draft(workspace):
    api,jobs=client_for(workspace)
    with api:
        task=arranged(api);base=f"/api/projects/p1/batch-edits/{task['id']}"
        assert api.post(base+'/voiceovers',json=request_for(task)).status_code==202
        api.put(base+'/content',json={'revision':0,'sellingPoint':'新版','script':'重新写的脚本'})
        pid,handler=jobs.pop();handler(pid)
        current=api.get('/api/projects/p1/batch-edits').json()['tasks'][0]
        assert current['script']=='重新写的脚本'
        assert current['voiceover']['status']=='failed'
        assert current['variant']['tracks'][1]['clips']==[]


def test_short_visual_fails_without_publishing_audio_and_restart_marks_interrupted(workspace):
    root = workspace/'project-files/p1/preproduction'
    state=json.loads((root/'state.json').read_text())
    state['assets'][0].update(kind='video',duration=1)
    (root/'state.json').write_text(json.dumps(state))
    api,jobs=client_for(workspace)
    with api:
        # Arrange within the actual visual limit before generating a longer voice segment.
        base='/api/projects/p1/batch-edits'
        task=api.post(base,json={'sellingPoint':'备豆','script':'倒入咖啡豆。'}).json()['task']
        tracks=task['variant']['tracks'];tracks[0]['clips']=[dict(id='clip',assetId='first',start=0,inPoint=0,duration=1,speed=1,volume=1,fadeIn=0,fadeOut=0)]
        api.put(f"{base}/{task['id']}/variant",json={'revision':0,'tracks':tracks})
        task=api.get(base).json()['tasks'][0]
        api.post(f"{base}/{task['id']}/voiceovers",json=request_for(task));pid,handler=jobs.pop();handler(pid)
        failed=api.get(base).json()['tasks'][0]
        assert failed['voiceover']['status']=='failed'
        assert '画面无法覆盖' in failed['voiceover']['error']
        assert failed['variant']['tracks']==tracks
        assert len(api.get(base).json()['assets'])==2
        # A queued retry is not silently declared successful after restart.
        api.post(f"{base}/{task['id']}/voiceovers",json=request_for(failed))
    reopened,_=client_for(workspace)
    with reopened:
        assert reopened.get(base).json()['tasks'][0]['voiceover']['status']=='failed'


def test_unrelated_task_cannot_be_added_to_a_voice_batch(workspace):
    api,jobs=client_for(workspace)
    with api:
        first=arranged(api);second=arranged(api,'另一个客户任务')
        response=api.post(f"/api/projects/p1/batch-edits/{first['id']}/voiceovers",json=request_for(second))
        assert response.status_code==422
        assert not jobs


def test_preserved_music_is_trimmed_to_voice_picture_length(workspace):
    root=workspace/'project-files/p1/preproduction'
    state=json.loads((root/'state.json').read_text())
    write_audio(root/'assets/music.wav',8)
    state['assets'].append({'id':'music','name':'配乐','kind':'audio','file':'music.wav','duration':8})
    (root/'state.json').write_text(json.dumps(state))
    api,jobs=client_for(workspace)
    with api:
        task=arranged(api);base=f"/api/projects/p1/batch-edits/{task['id']}"
        tracks=task['variant']['tracks']
        tracks[1]['clips']=[dict(id='bgm',assetId='music',start=0,inPoint=0,duration=8,speed=1,volume=.2,fadeIn=0,fadeOut=5)]
        api.put(base+'/variant',json={'revision':1,'tracks':tracks})
        task=api.get('/api/projects/p1/batch-edits').json()['tasks'][0]
        api.post(base+'/voiceovers',json=request_for(task));pid,handler=jobs.pop();handler(pid)
        current=api.get('/api/projects/p1/batch-edits').json()['tasks'][0]
        music=current['variant']['tracks'][1]['clips'][0]
        assert music['duration']==4
        assert music['volume']==.2
        assert music['fadeOut']==4
