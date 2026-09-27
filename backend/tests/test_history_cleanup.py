from test_timeline_api import setup, BASE, _workspace
from test_preproduction_api import setup as setup_pre, BASE as PRE_BASE, PROJECT_ID
from app.preproduction import PreproductionStore


def test_candidate_removal_keeps_assets_and_protects_adopted(tmp_path):
    client, _, _ = setup_pre(tmp_path)
    state = client.post(PRE_BASE + '/import-reference', json={'revision': 0}).json()
    aid = state['assets'][0]['id']
    store = PreproductionStore(tmp_path)
    saved = store.load(PROJECT_ID)
    saved['shots'] = [{'id': 's1', 'title': '镜头', 'duration': 2, 'prompt': '', 'negativePrompt': '', 'assetIds': [], 'nodes': [], 'resultAssetId': aid,
        '_resultVersions': [{'assetId': aid, 'signature': None, 'reviewed': False}, {'assetId': 'old', 'signature': None, 'reviewed': False}]}]
    saved['assets'].append({**saved['assets'][0], 'id': 'old'})
    store.save(PROJECT_ID, saved)
    root = PRE_BASE + '/shots/s1/results/'
    assert client.get(root + aid + '/cleanup-preview').status_code == 409
    preview = client.get(root + 'old/cleanup-preview').json()
    assert preview['bytes'] == 0
    assert client.post(root + 'old/remove', json={'revision': 0}).status_code == 409
    result = client.post(root + 'old/remove', json={'revision': 1})
    assert result.status_code == 200, result.text
    assert len(result.json()['shots'][0]['resultVersions']) == 1
    assert len(result.json()['assets']) == 2
    assert client.get(state['assets'][0]['url']).status_code == 200


def test_completed_history_preview_and_delete_do_not_touch_assets_or_tracks(tmp_path):
    def renderer(timeline, sources, output, **kwargs):
        output.write_bytes(b'output')
    client, queue = setup(tmp_path, renderer=renderer)
    client.put(BASE, json=_workspace())
    run = client.post(BASE + '/runs', json={'revision': 1, 'format': 'mp4'}).json()['runs'][0]
    url = BASE + '/runs/' + run['id']
    assert client.get(url + '/cleanup-preview').status_code == 409
    queue.run()
    preview = client.get(url + '/cleanup-preview').json()
    assert preview['bytes'] == 11 and preview['fileCount'] == 2
    assert client.post(url + '/delete', json={'revision': 0}).status_code == 409
    result = client.post(url + '/delete', json={'revision': 1})
    assert result.status_code == 200, result.text
    assert result.json()['runs'] == []
    assert result.json()['tracks'] == _workspace()['tracks']
    assert (tmp_path / 'project-files/p1/preproduction/assets/video.mp4').read_bytes() == b'video'
    assert not (tmp_path / 'project-files/p1/timeline/runs' / run['id']).exists()
    assert client.get(url + '/output').status_code == 404


def test_cancelled_queued_job_can_be_cleaned_and_worker_does_not_revive_it(tmp_path):
    client, queue = setup(tmp_path)
    client.put(BASE, json=_workspace())
    run = client.post(BASE + '/runs', json={'revision': 1, 'format': 'mp4'}).json()['runs'][0]
    url = BASE + '/runs/' + run['id']
    client.post(url + '/cancel', json={})
    assert client.post(url + '/delete', json={'revision': 1}).status_code == 200
    queue.run()
    assert client.get(BASE).json()['runs'] == []


def test_cancelled_running_worker_is_protected_until_exit(tmp_path):
    from threading import Event, Thread
    entered, release = Event(), Event()
    def renderer(timeline, sources, output, **kwargs):
        entered.set()
        assert release.wait(3)
        output.write_bytes(b'output')
    client, queue = setup(tmp_path, renderer=renderer)
    client.put(BASE, json=_workspace())
    run = client.post(BASE + '/runs', json={'revision': 1, 'format': 'mp4'}).json()['runs'][0]
    url = BASE + '/runs/' + run['id']
    worker = Thread(target=queue.run)
    worker.start()
    try:
        assert entered.wait(3)
        client.post(url + '/cancel', json={})
        assert client.post(url + '/delete', json={'revision': 1}).status_code == 409
    finally:
        release.set(); worker.join(3)
    assert not worker.is_alive()
    assert client.post(url + '/delete', json={'revision': 1}).status_code == 200


def test_failed_state_write_rolls_back_history_files(tmp_path, monkeypatch):
    from app.timeline import TimelineStore
    client, queue = setup(tmp_path, renderer=lambda timeline, sources, output, **kw: output.write_bytes(b'output'))
    client.put(BASE, json=_workspace())
    run = client.post(BASE + '/runs', json={'revision': 1, 'format': 'mp4'}).json()['runs'][0]
    queue.run()
    url = BASE + '/runs/' + run['id']
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(TimelineStore, 'save', fail)
    assert client.post(url + '/delete', json={'revision': 1}).status_code == 503
    assert client.get(url + '/output').content == b'output'
    assert (tmp_path / 'project-files/p1/timeline/runs' / run['id'] / 'sources/video.mp4').read_bytes() == b'video'


def test_symlink_in_history_cannot_delete_outside_project(tmp_path):
    client, _ = setup(tmp_path)
    client.put(BASE, json=_workspace())
    run = client.post(BASE + '/runs', json={'revision': 1, 'format': 'mp4'}).json()['runs'][0]
    url = BASE + '/runs/' + run['id']
    client.post(url + '/cancel', json={})
    other = tmp_path / 'keep.txt'; other.write_text('keep')
    (tmp_path / 'project-files/p1/timeline/runs' / run['id'] / 'unsafe').symlink_to(other)
    assert client.get(url + '/cleanup-preview').status_code == 409
    assert client.post(url + '/delete', json={'revision': 1}).status_code == 503
    assert other.read_text() == 'keep'


def test_physical_cleanup_failure_reports_retained_files(tmp_path, monkeypatch):
    from app import history_cleanup
    client, queue = setup(tmp_path, renderer=lambda timeline, sources, output, **kw: output.write_bytes(b'output'))
    client.put(BASE, json=_workspace())
    run = client.post(BASE + '/runs', json={'revision': 1, 'format': 'mp4'}).json()['runs'][0]
    queue.run()
    def fail(*args, **kwargs):
        raise OSError('permission denied')
    monkeypatch.setattr(history_cleanup.shutil, 'rmtree', fail)
    result = client.post(BASE + '/runs/' + run['id'] + '/delete', json={'revision': 1})
    assert result.status_code == 200
    assert result.json()['runs'] == []
    assert '未能释放' in result.json()['cleanupWarning']
    assert list((tmp_path / 'project-files/p1/timeline').glob('cleanup-*'))
