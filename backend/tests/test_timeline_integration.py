"""Real-media checks through the application boundary and shared job queue."""
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from app.main import create_app


class Queue:
    def __init__(self): self.jobs = []
    def submit(self, kind, project_id, handler):
        self.jobs.append((project_id, handler))
        return True
    def run(self):
        project_id, handler = self.jobs.pop(0)
        handler(project_id)
    def shutdown(self): pass


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='需要 FFmpeg')
def test_real_timeline_preview_audio_export_and_revision_snapshot(tmp_path):
    from PIL import Image
    red, blue = tmp_path / 'red.png', tmp_path / 'blue.png'
    Image.new('RGB', (64, 64), 'red').save(red)
    Image.new('RGB', (64, 64), 'blue').save(blue)
    tone = tmp_path / 'tone.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', str(tone)], check=True)
    queue = Queue()
    with TestClient(create_app(data_dir=tmp_path / 'data', local_compute_queue=queue)) as client:
        pid = client.post('/api/projects', json={'name': '多轨真实验收'}).json()['id']
        base = f'/api/projects/{pid}/timeline'
        assets = []
        for path, role in ((red, 'scene'), (blue, 'scene'), (tone, 'audio')):
            response = client.post(f'/api/projects/{pid}/preproduction/assets?role={role}',
                                   files={'file': (path.name, path.read_bytes())})
            assert response.status_code == 200, response.text
            assets.append(response.json()['assets'][-1]['id'])
        state = client.get(base).json()
        def clip(id, asset, start, duration):
            return dict(id=id, assetId=asset, start=start, inPoint=0, duration=duration,
                        speed=1, volume=.5, fadeIn=0, fadeOut=0)
        tracks = [dict(id='visual', name='画面', kind='video', muted=False, hidden=False,
                       clips=[clip('red', assets[0], 0, .5), clip('blue', assets[1], .5, .5)]),
                  dict(id='sound', name='配乐', kind='audio', muted=False, hidden=False,
                       clips=[clip('tone', assets[2], .25, .5)])]
        response = client.put(base, json={'revision': state['revision'], 'settings': {'width': 64, 'height': 64, 'fps': 24}, 'tracks': tracks})
        assert response.status_code == 200, response.text
        state = response.json()
        rendered_revision = state['revision']
        queued = client.post(base + '/runs', json={'revision': rendered_revision, 'format': 'preview'})
        assert queued.status_code == 202, queued.text
        # Edit while the render is queued: the queued snapshot must remain valid.
        tracks[0]['clips'][0]['assetId'] = assets[1]
        changed = client.put(base, json={'revision': rendered_revision, 'settings': state['settings'], 'tracks': tracks})
        assert changed.status_code == 200, changed.text
        queue.run()
        state = client.get(base).json()
        run = next(r for r in state['runs'] if r['format'] == 'preview')
        assert run['status'] == 'completed', run
        assert run['revision'] == rendered_revision < state['revision']
        result = client.get(run['url'])
        assert result.status_code == 200
        output = tmp_path / 'preview.mp4'; output.write_bytes(result.content)
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(output)]))
        assert abs(float(probe['format']['duration']) - 1) < .1
        assert {s['codec_type'] for s in probe['streams']} == {'video', 'audio'}
        for at, channel in (('0.1', 0), ('0.7', 2)):
            pixel = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', at, '-i', str(output),
                                             '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
            assert pixel[channel] > 200 and sum(pixel) - pixel[channel] < 80
        queued = client.post(base + '/runs', json={'revision': state['revision'], 'format': 'wav'})
        assert queued.status_code == 202, queued.text
        queue.run()
        run = next(r for r in client.get(base).json()['runs'] if r['format'] == 'wav')
        assert run['status'] == 'completed', run
        wav = client.get(run['url']).content
        assert wav[:4] == b'RIFF' and b'WAVE' in wav[:16]
