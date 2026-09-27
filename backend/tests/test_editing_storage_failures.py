"""Fault injection at the actual atomic-save boundary; never fills the disk."""
import errno
import os

import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.mark.parametrize('module', ['timeline', 'preproduction'])
@pytest.mark.parametrize('operation', ['fsync', 'replace'])
def test_failed_atomic_save_preserves_last_saved_state_and_can_retry(tmp_path, monkeypatch, module, operation):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = client.post('/api/projects', json={'name': '存储失败验收'}).json()['id']
        url = f'/api/projects/{pid}/{module}'
        state = client.get(url).json()
        keys = ('revision', 'settings', 'tracks') if module == 'timeline' else ('revision', 'brief', 'shots')
        response = client.put(url, json={k: state[k] for k in keys})
        assert response.status_code == 200, response.text
        state = response.json()
        path = tmp_path / 'project-files' / pid / module / 'state.json'
        original = path.read_bytes()
        body = {k: state[k] for k in keys}
        if module == 'timeline': body['settings']['width'] = 640
        else: body['brief']['theme'] = '恢复后应保存的新内容'
        def fail(*args, **kwargs):
            raise OSError(errno.ENOSPC, 'simulated disk full')
        with monkeypatch.context() as patch:
            patch.setattr(os, operation, fail)
            rejected = client.put(url, json=body)
        assert rejected.status_code == 503, rejected.text
        assert path.read_bytes() == original
        assert not list(path.parent.glob('*.part'))
        assert client.get(url).json()['revision'] == state['revision']
        saved = client.put(url, json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()['revision'] == state['revision'] + 1
        assert path.read_bytes() != original
