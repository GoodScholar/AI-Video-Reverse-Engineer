from test_preproduction_api import setup, BASE, PROJECT_ID
from app.preproduction import PreproductionStore
from app.timeline import TimelineStore, default_workspace


def imported(tmp_path):
    client, _, _ = setup(tmp_path)
    state = client.post(BASE + '/import-reference', json={'revision': 0}).json()
    return client, state, state['assets'][0]['id']


def test_metadata_cas_and_delete_unreferenced(tmp_path):
    client, state, aid = imported(tmp_path)
    url = BASE + '/assets/' + aid
    assert client.put(url, json={'revision': 0, 'name': '新版', 'notes': ''}).status_code == 409
    assert client.put(url, json={'revision': 1, 'name': '  ', 'notes': ''}).status_code == 422
    result = client.put(url, json={'revision': 1, 'name': '  新版参考  ', 'notes': '动作参考'} )
    assert result.status_code == 200, result.text
    asset = result.json()['assets'][0]
    assert asset['name'] == '新版参考' and asset['notes'] == '动作参考'
    assert client.get(asset['url']).status_code == 200
    assert client.get(url + '/references').json()['references'] == []
    result = client.post(url + '/delete', json={'revision': 2})
    assert result.status_code == 200 and result.json()['assets'] == []
    assert client.get(asset['url']).status_code == 404
    assert not list((tmp_path / 'project-files' / PROJECT_ID / 'preproduction' / 'assets').glob('*.mp4'))


def test_all_persisted_references_block_deletion(tmp_path):
    client, state, aid = imported(tmp_path)
    store = PreproductionStore(tmp_path)
    stored = store.load(PROJECT_ID)
    stored['shots'] = [{'id': 's1', 'title': '镜头一', 'duration': 2, 'prompt': '', 'negativePrompt': '',
        'assetIds': [aid], 'resultAssetId': aid, '_resultVersions': [{'assetId': aid, 'signature': None, 'reviewed': False}],
        'nodes': [{'id': 'n1', 'kind': 'reference', 'input': 'asset:' + aid, 'params': {}, 'status': 'pending', 'artifacts': []}]}]
    store.save(PROJECT_ID, stored)
    timeline = default_workspace()
    track = {'id': 't1', 'name': '视频轨', 'clips': [{'id': 'c1', 'assetId': aid}]}
    timeline['tracks'] = [track]
    timeline['runs'] = [{'id': 'r1', 'snapshot': {'tracks': [track]}, 'sources': {aid: 'copy.mp4'}}]
    TimelineStore(tmp_path).save(PROJECT_ID, timeline)
    url = BASE + '/assets/' + aid
    refs = client.get(url + '/references').json()['references']
    assert {r['kind'] for r in refs} == {'shot_binding', 'shot_result', 'candidate', 'node', 'timeline', 'timeline_history'}
    assert {'trackId': 't1', 'clipId': 'c1'}.items() <= next(r for r in refs if r['kind'] == 'timeline').items()
    assert next(r for r in refs if r['kind'] == 'timeline_history')['runId'] == 'r1'
    assert client.post(url + '/delete', json={'revision': 1}).status_code == 409
    assert client.get(state['assets'][0]['url']).status_code == 200


def test_unreadable_timeline_fails_closed(tmp_path):
    client, _, aid = imported(tmp_path)
    path = TimelineStore(tmp_path).path(PROJECT_ID, 'state.json')
    path.parent.mkdir(parents=True)
    path.write_text('broken')
    result = client.post(BASE + '/assets/' + aid + '/delete', json={'revision': 1})
    assert result.status_code == 503
    assert result.json()['detail']['code'] == 'asset_references_unavailable'


def test_failed_state_write_restores_asset_file(tmp_path, monkeypatch):
    client, state, aid = imported(tmp_path)
    def unavailable(*args):
        raise OSError('disk full')
    monkeypatch.setattr(PreproductionStore, 'save', unavailable)
    result = client.post(BASE + '/assets/' + aid + '/delete', json={'revision': 1})
    assert result.status_code == 503
    assert client.get(state['assets'][0]['url']).status_code == 200
    assert PreproductionStore(tmp_path).load(PROJECT_ID)['assets'][0]['id'] == aid


def test_metadata_and_delete_preserve_original_reference(tmp_path):
    client, state, aid = imported(tmp_path)
    original = tmp_path / 'project-files' / PROJECT_ID / 'reference-media' / 'ref-001.mp4'
    before = original.read_bytes()
    client.put(BASE + '/assets/' + aid, json={'revision': 1, 'name': '参考改名', 'notes': '备注'})
    result = client.post(BASE + '/assets/' + aid + '/delete', json={'revision': 2})
    assert result.status_code == 200
    assert original.read_bytes() == before


def test_delete_keeps_unavailable_file_error_contract(tmp_path):
    client, state, aid = imported(tmp_path)
    asset = PreproductionStore(tmp_path).load(PROJECT_ID)['assets'][0]
    (tmp_path / 'project-files' / PROJECT_ID / 'preproduction' / 'assets' / asset['file']).unlink()

    result = client.post(BASE + '/assets/' + aid + '/delete', json={'revision': 1})

    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'preproduction_asset_unavailable'
