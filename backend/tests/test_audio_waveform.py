import subprocess
from test_timeline_api import setup, BASE


def test_waveform_real_audio_silence_and_cache(tmp_path, monkeypatch):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/assets/audio.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', r'aevalsrc=if(lt(t\,1)\,0\,0.5*sin(2*PI*440*t)):s=8000:d=2', str(path)], check=True)
    url = BASE + '/assets/audio/waveform'
    result = client.post(url, json={})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['status'] == 'ready'
    assert abs(data['duration'] - 2) < 0.02
    assert max(data['peaks'][:90]) == 0
    assert max(data['peaks'][110:]) > 0.4
    def forbidden(*args, **kwargs):
        raise AssertionError('cached media must not decode again')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    assert client.post(url, json={}).json() == data
    path.unlink()
    assert client.post(url, json={}).status_code == 409


def test_no_audio_differs_from_broken_media(tmp_path):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/assets/video.mp4'
    assert client.post(BASE + '/assets/video/waveform', json={}).status_code == 422
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=size=64x48:duration=1', '-c:v', 'libx264', str(path)], check=True)
    assert client.post(BASE + '/assets/video/waveform', json={}).json()['status'] == 'no_audio'
    assert client.post(BASE + '/assets/unknown/waveform', json={}).status_code == 404


def test_opposite_stereo_channels_keep_waveform_and_cache_invalidates(tmp_path):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/assets/audio.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'aevalsrc=0.5*sin(2*PI*440*t)|-0.5*sin(2*PI*440*t):s=8000:d=1', str(path)], check=True)
    url = BASE + '/assets/audio/waveform'
    assert max(client.post(url, json={}).json()['peaks']) > .4
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '1', str(path)], check=True)
    assert max(client.post(url, json={}).json()['peaks']) == 0


def test_waveform_timeout_returns_retryable_error(tmp_path, monkeypatch):
    client, _ = setup(tmp_path)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('ffprobe', 15)
    monkeypatch.setattr(subprocess, 'run', timeout)
    response = client.post(BASE + '/assets/audio/waveform', json={})
    assert response.status_code == 422
    assert '重试' in response.json()['detail']['message']


def test_busy_decoders_do_not_block_cache_hits_or_queue_new_work(tmp_path, monkeypatch):
    import pytest
    from app import audio_waveform
    cache = audio_waveform.WaveformCache()
    source = tmp_path / 'a.wav'
    source.write_bytes(b'a')
    other = tmp_path / 'b.wav'
    other.write_bytes(b'b')
    monkeypatch.setattr(audio_waveform, 'extract_waveform', lambda *args: {'status': 'no_audio'})
    assert cache.get(source, 'ffmpeg', 'ffprobe') == {'status': 'no_audio'}
    cache.decoders.acquire(); cache.decoders.acquire()
    try:
        assert cache.get(source, 'ffmpeg', 'ffprobe') == {'status': 'no_audio'}
        with pytest.raises(ValueError, match='繁忙'):
            cache.get(other, 'ffmpeg', 'ffprobe')
    finally:
        cache.decoders.release(); cache.decoders.release()


def test_video_audio_start_offset_is_preserved_in_waveform(tmp_path):
    client, _ = setup(tmp_path)
    path = tmp_path / 'project-files/p1/preproduction/assets/video.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=size=64x48:duration=2',
        '-itsoffset', '0.5', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264', '-c:a', 'aac', str(path)], check=True)
    data = client.post(BASE + '/assets/video/waveform', json={}).json()
    assert data['status'] == 'ready'
    assert max(data['peaks'][:40]) == 0
    assert max(data['peaks'][60:100]) > .05
