from fastapi.testclient import TestClient
from app.main import create_app


def test_final_generation_is_excluded_but_preparation_is_available(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = client.post('/api/projects', json={'name': '前置工作台'}).json()['id']
        for module in ('reproduction', 'character-motion'):
            response = client.post(f'/api/projects/{pid}/{module}/runs', json={'revision': 0})
            assert response.status_code == 410
            assert response.json()['detail']['code'] == 'final_generation_disabled'
            assert client.get(f'/api/projects/{pid}/{module}').status_code == 200


def test_preparation_mutations_reject_untrusted_browser_origins(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = client.post('/api/projects', json={'name': '前置工作台'}).json()['id']
        base = f'/api/projects/{pid}/preproduction'
        assert client.put(base, json={}, headers={'Origin': 'https://untrusted.example'}).status_code == 403
        assert client.post(base + '/assets', files={'file': ('sample.png', b'invalid', 'image/png')},
                           headers={'Origin': 'https://untrusted.example'}).status_code == 403
        assert client.post(base + '/package', json={}, headers={'Origin': 'http://localhost:5173'}).status_code == 403


def test_timeline_composition_is_available_but_protected(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = client.post('/api/projects', json={'name': '剪辑项目'}).json()['id']
        base = f'/api/projects/{pid}/timeline'
        state = client.get(base)
        assert state.status_code == 200
        for suffix in ('', '/runs', '/runs/run-1/cancel'):
            method = client.put if not suffix else client.post
            assert method(base + suffix, json={}, headers={'Origin': 'https://untrusted.example'}).status_code == 403
            assert method(base + suffix, json={}, headers={'Origin': 'http://localhost:5173'}).status_code == 403
        assert client.post(f'/api/projects/{pid}/reproduction/runs', json={}).status_code == 410
