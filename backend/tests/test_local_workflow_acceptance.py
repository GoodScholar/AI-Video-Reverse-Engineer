"""Cross-feature local workflow, using real media and an isolated project store."""
import json
import subprocess
import time
from fastapi.testclient import TestClient
from app.main import create_app


def test_local_workflow_from_upload_to_restore_and_cleanup(tmp_path):
    video = tmp_path / 'reference.mp4'
    audio = tmp_path / 'voice.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24:d=3', '-c:v', 'libx264', str(video)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', str(audio)], check=True)
    data = tmp_path / 'data'
    with TestClient(create_app(data_dir=data)) as client:
        def call(method, path, **kwargs):
            response = getattr(client, method)(path, **kwargs)
            assert response.is_success, response.text
            return response
        project = call('post', '/api/projects', json={'name': '完整本地流程验收'}).json()
        pid = project['id']
        prep = f'/api/projects/{pid}/preproduction'
        timeline = f'/api/projects/{pid}/timeline'
        call('put', f'/api/projects/{pid}/reference-media', files={'file': ('reference.mp4', video.read_bytes(), 'video/mp4')})
        state = call('get', prep).json()
        state = call('put', prep, json={'revision': state['revision'], 'brief': state['brief'], 'shots': [{'id': 's1', 'title': '验收镜头', 'duration': 2, 'prompt': '', 'assetIds': [], 'nodes': []}]}).json()
        for _ in range(2):
            state = call('post', prep + f'/assets?role=motion&resultForShot=s1&revision={state["revision"]}', files={'file': ('candidate.mp4', video.read_bytes(), 'video/mp4')}).json()
        old, adopted = [item['assetId'] for item in state['shots'][0]['resultVersions']]
        state = call('put', prep + '/assets/' + adopted, json={'revision': state['revision'], 'name': '采用结果', 'notes': '保留节奏'}).json()
        state = call('post', prep + '/assets?role=audio', files={'file': ('voice.wav', audio.read_bytes(), 'audio/wav')}).json()
        voice = state['assets'][-1]['id']
        edit = call('post', timeline + '/import-shot-results', json={'revision': 0, 'preproductionRevision': state['revision']}).json()
        edit['settings'] = {'width': 96, 'height': 64, 'fps': 24}
        edit['tracks'].append({'id': 'audio', 'name': '本地音频', 'kind': 'audio', 'muted': False, 'hidden': False, 'clips': [{'id': 'voice', 'assetId': voice, 'start': 0, 'inPoint': .5, 'duration': 1.5, 'speed': 1.5, 'volume': .6, 'fadeIn': .2, 'fadeOut': .2}]})
        edit = call('put', timeline, json={key: edit[key] for key in ('revision', 'settings', 'tracks')}).json()
        assert client.put(timeline, json={**{key: edit[key] for key in ('settings', 'tracks')}, 'revision': 0}).status_code == 409
        wave = call('post', timeline + '/assets/' + voice + '/waveform', json={}).json()
        assert wave['status'] == 'ready' and max(wave['peaks']) > 0
        outputs = []
        for format in ('mp4', 'wav'):
            assert call('post', timeline + '/preflight', json={'revision': edit['revision'], 'format': format}).json()['ready']
            queued = call('post', timeline + '/runs', json={'revision': edit['revision'], 'format': format}).json()
            run_id = queued['runs'][-1]['id']
            for _ in range(300):
                edit = call('get', timeline).json()
                run = next(item for item in edit['runs'] if item['id'] == run_id)
                if run['status'] not in ('queued', 'running'): break
                time.sleep(.02)
            assert run['status'] == 'completed', run
            outputs.append(call('get', run['url']).content)
        backup = call('post', f'/api/projects/{pid}/backup', json={}).content
        restored = call('post', '/api/project-backups/restore', content=backup, headers={'Content-Type': 'application/zip'}).json()
        rid = restored['id']
        restored_timeline = f'/api/projects/{rid}/timeline'
        restored_prep = f'/api/projects/{rid}/preproduction'
        restored_edit = call('get', restored_timeline).json()
        assert restored_edit['tracks'] == edit['tracks']
        assert [call('get', run['url']).content for run in restored_edit['runs']] == outputs
        rstate = call('get', restored_prep).json()
        assert next(item for item in rstate['assets'] if item['id'] == adopted)['notes'] == '保留节奏'
        call('get', restored_prep + f'/shots/s1/results/{old}/cleanup-preview')
        rstate = call('post', restored_prep + f'/shots/s1/results/{old}/remove', json={'revision': rstate['revision']}).json()
        assert not call('get', restored_prep + f'/assets/{old}/references').json()['references']
        rstate = call('post', restored_prep + f'/assets/{old}/delete', json={'revision': rstate['revision']}).json()
        assert client.post(restored_prep + f'/assets/{adopted}/delete', json={'revision': rstate['revision']}).status_code == 409
        first = restored_edit['runs'][0]['id']
        plan = call('get', restored_timeline + f'/runs/{first}/cleanup-preview').json()
        assert plan['bytes'] > 0
        cleaned = call('post', restored_timeline + f'/runs/{first}/delete', json={'revision': plan['revision']}).json()
        assert cleaned['tracks'] == edit['tracks'] and len(cleaned['runs']) == 1
        assert call('get', cleaned['runs'][0]['url']).content == outputs[1]
        assert len(call('get', timeline).json()['runs']) == 2
        post_cleanup = call('post', f'/api/projects/{rid}/backup', json={}).content
        call('post', '/api/project-backups/restore', content=post_cleanup, headers={'Content-Type': 'application/zip'})
        (tmp_path / 'acceptance.json').write_text(json.dumps({'dataDir': str(data), 'original': pid, 'restored': rid, 'outputBytes': [len(value) for value in outputs]}, ensure_ascii=False))
        print('ACCEPTANCE_EVIDENCE=' + str(tmp_path / 'acceptance.json'))
