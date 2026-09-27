from fastapi.testclient import TestClient
from app.main import create_app


def test_voice_requests_from_current_workbench_port_keep_intent_boundary(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = client.post('/api/projects', json={'name': '本地配音验收'}).json()['id']
        base = f'/api/projects/{pid}/batch-edits'
        for origin in ('http://127.0.0.1:5188', 'http://localhost:5188'):
            headers = {'Origin': origin, 'X-AIVRE-Intent': 'semantic-analysis'}
            response = client.post(base, json={'sellingPoint': '咖啡制作', 'script': '倒入咖啡豆。'}, headers=headers)
            assert response.status_code == 201
            task_id = response.json()['task']['id']
            voice_url = f'{base}/{task_id}/voiceovers'
            # 正确意图应进入配音参数校验；缺少意图仍被中间件拦截。
            assert client.post(voice_url, json={}, headers=headers).status_code == 422
            assert client.post(voice_url, json={}, headers={'Origin': origin}).status_code == 403
        assert client.post(base, json={}, headers={'Origin': 'https://untrusted.example', 'X-AIVRE-Intent': 'semantic-analysis'}).status_code == 403


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
        for suffix in ('', '/runs', '/runs/run-1/cancel', '/import-shot-results'):
            method = client.put if not suffix else client.post
            assert method(base + suffix, json={}, headers={'Origin': 'https://untrusted.example'}).status_code == 403
            assert method(base + suffix, json={}, headers={'Origin': 'http://localhost:5173'}).status_code == 403
        assert client.post(f'/api/projects/{pid}/reproduction/runs', json={}).status_code == 410


def test_product_image_routes_share_browser_mutation_boundary(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid=client.post('/api/projects',json={'name':'找图边界'}).json()['id']
        base=f'/api/projects/{pid}/preproduction/product-images'
        for suffix in ('/search','/import'):
            assert client.post(base+suffix,json={},headers={'Origin':'https://untrusted.example','X-AIVRE-Intent':'semantic-analysis'}).status_code==403
            assert client.post(base+suffix,json={},headers={'Origin':'http://127.0.0.1:5188'}).status_code==403
            assert client.post(base+suffix,json={},headers={'Origin':'http://127.0.0.1:5188','X-AIVRE-Intent':'semantic-analysis'}).status_code==422


def test_production_preproduction_routes_use_reentrant_shared_lock(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid=client.post('/api/projects',json={'name':'共享锁验收'}).json()['id']
        base=f'/api/projects/{pid}/preproduction'
        state=client.get(base).json()
        result=client.put(base,json={'revision':state['revision'],'brief':{**state['brief'],'theme':'商品制作'},'shots':[]})
        assert result.status_code==200,result.text
        assert result.json()['brief']['theme']=='商品制作'
