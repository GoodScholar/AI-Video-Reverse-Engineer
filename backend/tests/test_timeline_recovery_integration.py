"""Real FFmpeg cancellation and recovery, isolated from user projects."""
import subprocess
import time

from fastapi.testclient import TestClient
from app.main import create_app


def test_real_cancel_corrupt_source_and_retry(tmp_path):
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24:d=5', '-c:v', 'libx264', str(source)], check=True)
    with TestClient(create_app(data_dir=tmp_path / 'data')) as client:
        def call(method, path, **kwargs):
            response = getattr(client, method)(path, **kwargs)
            assert response.is_success, response.text
            return response
        pid = call('post', '/api/projects', json={'name': '取消与损坏恢复'}).json()['id']
        base = f'/api/projects/{pid}'
        prep = call('post', base + '/preproduction/assets?role=motion', files={'file': ('source.mp4', source.read_bytes(), 'video/mp4')}).json()
        aid = prep['assets'][0]['id']
        tl = base + '/timeline'
        clips = [{'id': f'c{i}', 'assetId': aid, 'start': i * 5, 'inPoint': 0, 'duration': 5, 'speed': 1, 'volume': 1, 'fadeIn': 0, 'fadeOut': 0} for i in range(60)]
        state = call('put', tl, json={'revision': 0, 'settings': {'width': 1920, 'height': 1080, 'fps': 30}, 'tracks': [{'id': 'v', 'name': '长画面', 'kind': 'video', 'muted': False, 'hidden': False, 'clips': clips}]}).json()
        run = call('post', tl + '/runs', json={'revision': state['revision'], 'format': 'mp4'}).json()['runs'][-1]
        project_root = tmp_path / 'data' / 'project-files' / pid
        partial = project_root / 'timeline' / 'runs' / run['id'] / 'output.mp4'
        try:
            deadline = time.monotonic() + 20
            while not list(partial.parent.glob(".output.*.mp4")):
                assert time.monotonic() < deadline, 'FFmpeg did not start writing'
                time.sleep(.05)
        finally:
            cancelled = call('post', tl + '/runs/' + run['id'] + '/cancel', json={}).json()
        assert cancelled['runs'][-1]['status'] == 'cancelled'
        assert client.get(tl + '/runs/' + run['id'] + '/output').status_code == 409
        deadline = time.monotonic() + 20
        while True:
            backup = client.post(base + '/backup', json={})
            if backup.status_code != 409:
                break
            assert time.monotonic() < deadline, 'cancelled worker did not exit'
            time.sleep(.05)
        assert backup.status_code == 200, backup.text
        assert not partial.exists()
        assert not list(partial.parent.glob(".output.*.mp4"))
        restored = call('post', '/api/project-backups/restore', content=backup.content, headers={'Content-Type': 'application/zip'}).json()
        assert call('get', f'/api/projects/{restored["id"]}/timeline').json()['runs'][-1]['status'] == 'cancelled'
        # Corrupt only isolated source; preflight must reject and recover when repaired.
        asset_file = project_root / 'preproduction' / 'assets' / prep['assets'][0]['file'] if 'file' in prep['assets'][0] else next((project_root / 'preproduction' / 'assets').iterdir())
        original = asset_file.read_bytes()
        asset_file.write_bytes(b'broken media')
        check = call('post', tl + '/preflight', json={'revision': state['revision'], 'format': 'mp4'}).json()
        assert not check['ready'] and any(i['level'] == 'error' for i in check['issues'])
        asset_file.write_bytes(original)
        state = call('put', tl, json={'revision': state['revision'], 'settings': {'width': 96, 'height': 64, 'fps': 24}, 'tracks': [{**state['tracks'][0], 'clips': clips[:1]}]}).json()
        assert call('post', tl + '/preflight', json={'revision': state['revision'], 'format': 'mp4'}).json()['ready']
        retry = call('post', tl + '/runs', json={'revision': state['revision'], 'format': 'mp4'}).json()['runs'][-1]
        deadline = time.monotonic() + 20
        while True:
            latest = call('get', tl).json()['runs'][-1]
            if latest['status'] not in ('queued', 'running'): break
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert latest['id'] == retry['id'] and latest['status'] == 'completed', latest
        assert call('get', latest['url']).content
