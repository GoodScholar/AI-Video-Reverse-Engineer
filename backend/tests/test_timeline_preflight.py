import subprocess
import pytest
from test_timeline_api import setup, BASE, _workspace


def prepare(tmp_path):
    client, queue = setup(tmp_path)
    client.put(BASE, json=_workspace())
    return client, queue


def test_missing_and_unreadable_sources_are_located_without_creating_run(tmp_path):
    client, queue = prepare(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/assets/video.mp4'
    path.write_bytes(b'broken-media')
    response = client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'})
    assert response.status_code == 200
    result = response.json()
    assert not result['ready']
    assert result['issues'][0]['clipId'] == 'clip1'
    assert result['issues'][0]['assetId'] == 'video'
    assert queue.jobs == []
    path.unlink()
    assert not client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'}).json()['ready']
    assert client.post(BASE + '/preflight', json={'revision': 0, 'format': 'mp4'}).status_code == 409


def test_actual_stream_duration_and_audio_are_checked(tmp_path):
    client, _ = prepare(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/assets/video.mp4'
    def video(duration):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=size=64x48:duration={duration}', '-c:v', 'libx264', str(path)], check=True)
    video(1)
    short = client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'}).json()
    assert not short['ready']
    assert any('尾部' in issue['message'] for issue in short['issues'])
    video(3)
    good = client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'}).json()
    assert good['ready']
    assert any(issue['level'] == 'warning' and '音频' in issue['message'] for issue in good['issues'])
    wav = client.post(BASE + '/preflight', json={'revision': 1, 'format': 'wav'}).json()
    assert not wav['ready']


def test_enabled_packaging_blocks_an_unanalysed_visible_clip_but_not_a_nonoverlap(tmp_path):
    from app.montage_packaging import default_packaging

    client, _ = setup(tmp_path)
    draft = _workspace()
    draft['packaging'] = default_packaging()
    draft['packaging']['elements'] = [{
        'id': 'title', 'kind': 'title', 'enabled': True, 'start': .2, 'end': .8,
        'text': '主体安全', 'position': 'top', 'style': 'plain',
    }]
    assert client.put(BASE, json=draft).status_code == 200
    blocked = client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'}).json()
    assert not blocked['ready']
    assert any('主体信息缺失需人工构图' in issue['message'] for issue in blocked['issues'])

    draft['revision'] = 1
    draft['packaging']['elements'][0]['start'] = 2.1
    draft['packaging']['elements'][0]['end'] = 2.5
    assert client.put(BASE, json=draft).status_code == 200
    assert client.post(BASE + '/preflight', json={'revision': 2, 'format': 'mp4'}).json()['ready']


@pytest.mark.parametrize('mode', ['center', 'manual', 'replace'])
def test_preflight_allows_explicit_risk_with_only_observed_partial_subject_samples(tmp_path, mode):
    from app.montage_packaging import default_packaging
    from app.montage_reframe import pending_reframe

    client, _ = setup(tmp_path)
    draft = _workspace()
    draft['packaging'] = default_packaging()
    draft['packaging']['elements'] = [{
        'id': 'title', 'kind': 'title', 'enabled': True, 'start': .2, 'end': .8,
        'text': '人工构图', 'position': 'auto', 'style': 'plain',
    }]
    assert client.put(BASE, json=draft).status_code == 200
    reframe = pending_reframe(target_width=1280, target_height=720, reason='历史分析中断')
    reframe.update(sourceWidth=64, sourceHeight=48, samples=[{
        'timeSeconds': .5, 'boxes': [{'x': .35, 'y': .2, 'width': .3, 'height': .6,
                                      'label': 'person', 'confidence': .9}],
        'center': {'x': .5, 'y': .5}, 'confidence': .9,
    }])
    reframe['override'] = {'mode': mode, 'keyframes': [] if mode == 'center' else [
        {'timeSeconds': 0, 'x': .5, 'y': .5},
    ]}
    # This is a persisted historical worker record: the public PUT correctly
    # refuses clients trying to invent detector facts.
    from app.timeline import TimelineStore
    store = TimelineStore(tmp_path)
    state = store.load('p1')
    state['tracks'][0]['clips'][0]['reframe'] = reframe
    store.save('p1', state)
    assert client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'}).json()['ready']


def test_audio_probe_is_cached_and_muted_wav_is_blocked(tmp_path, monkeypatch):
    from app import timeline_render
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/assets/audio.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=3', str(path)], check=True)
    draft = _workspace()
    track = draft['tracks'][0]
    track.update(kind='audio')
    track['clips'][0]['assetId'] = 'audio'
    track['clips'].append({**track['clips'][0], 'id': 'clip2', 'start': 2})
    assert client.put(BASE, json=draft).status_code == 200
    actual = timeline_render._probe_media
    calls = []
    def probe(*args):
        calls.append(args)
        return actual(*args)
    monkeypatch.setattr(timeline_render, '_probe_media', probe)
    assert client.post(BASE + '/preflight', json={'revision': 1, 'format': 'wav'}).json()['ready']
    assert len(calls) == 1
    draft['revision'] = 1
    track['muted'] = True
    client.put(BASE, json=draft)
    assert not client.post(BASE + '/preflight', json={'revision': 2, 'format': 'wav'}).json()['ready']


def test_changed_workspace_during_probe_is_not_certified(tmp_path, monkeypatch):
    from app import timeline_render
    from app.timeline import TimelineStore
    client, _ = prepare(tmp_path)
    def changed(*args):
        store = TimelineStore(tmp_path)
        state = store.load('p1')
        state['revision'] += 1
        store.save('p1', state)
        return []
    monkeypatch.setattr(timeline_render, 'inspect_timeline_media', changed)
    assert client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'}).status_code == 409


def test_unreadable_media_is_probed_once_for_multiple_clips(tmp_path, monkeypatch):
    from app import timeline_render
    client, _ = setup(tmp_path)
    draft = _workspace()
    draft['tracks'][0]['clips'].append({**draft['tracks'][0]['clips'][0], 'id': 'second', 'start': 3})
    client.put(BASE, json=draft)
    calls = []
    def broken(*args):
        calls.append(args)
        raise timeline_render.TimelineRenderError('无法读取时间线素材。')
    monkeypatch.setattr(timeline_render, '_probe_media', broken)
    result = client.post(BASE + '/preflight', json={'revision': 1, 'format': 'mp4'}).json()
    assert not result['ready']
    assert len([item for item in result['issues'] if item.get('clipId')]) == 2
    assert len(calls) == 1
