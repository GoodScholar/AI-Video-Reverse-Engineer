"""Real media pipeline tests; the neural CLI is replaced only to avoid GPU requirements."""
import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.video_upscale import UpscaleConfig, execute_upscale
from app.video_upscale_api import create_upscale_router


@pytest.fixture
def config(tmp_path):
    models = tmp_path / 'models'
    models.mkdir()
    for suffix in ('bin', 'param'):
        (models / f'realesrgan-x4plus.{suffix}').write_bytes(b'test')
    cli = tmp_path / 'fake-esrgan'
    script = tmp_path / 'fake_engine.py'
    cli.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(script)) + ' "$@"\n')
    script.write_text('''import sys
from pathlib import Path
from PIL import Image
args=dict(zip(sys.argv[1::2],sys.argv[2::2]))
for src in Path(args['-i']).glob('*.png'):
 with Image.open(src) as im:
  im.resize((im.width*4,im.height*4)).save(Path(args['-o'])/src.name)
''')
    cli.chmod(0o755)
    return UpscaleConfig(str(cli), models)


def make_video(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=32x24:rate=6:duration=2','-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)


@pytest.mark.parametrize('scale, dimensions', [(2,(64,48)),(4,(128,96))])
def test_pipeline_preserves_full_timeline_audio_and_requested_dimensions(tmp_path, config, scale, dimensions):
    source=tmp_path/'source.mp4';make_video(source)
    events=[]
    out=tmp_path/'run';out.mkdir()
    result=execute_upscale(source,out,scale,config,lambda stage,progress: events.append((stage,progress)))
    assert (result['width'],result['height'])==dimensions
    assert result['frameRate']==6
    assert abs(result['durationSeconds']-2)<1/6
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(out/'output.mp4')]))
    assert any(s['codec_type']=='audio' for s in info['streams'])
    assert len([s for s in info['streams'] if s['codec_type']=='video'])==1
    assert events[-1][1]==100
    assert not (out/'frames').exists()


class Queue:
    def __init__(self): self.jobs=[]
    def submit(self,kind,pid,handler): self.jobs.append((pid,handler));return True
    def run(self):
        pid,fn=self.jobs.pop(0);fn(pid)


def setup(tmp_path,config,queue=None):
    source=tmp_path/'project-files/p1/reference-media/v1.mp4';make_video(source)
    project=SimpleNamespace(id='p1',referenceMedia=SimpleNamespace(id='v1',type='video',format='mp4',width=32,height=24))
    queue=queue or Queue()
    app=FastAPI();app.include_router(create_upscale_router(tmp_path,lambda _: project,queue,config=config))
    return TestClient(app),project,queue


def test_api_binds_source_blocks_duplicates_and_downloads_completed_snapshot(tmp_path,config):
    client,project,queue=setup(tmp_path,config)
    base='/api/projects/p1/upscale'
    assert client.post(base,json={'sourceId':'stale','scale':2}).status_code==409
    r=client.post(base,json={'sourceId':'v1','scale':2});assert r.status_code==202,r.text
    rid=r.json()['id']
    assert client.post(base,json={'sourceId':'v1','scale':2}).status_code==409
    assert client.get(f'{base}/{rid}/video').status_code==409
    project.referenceMedia.id='v2'
    (tmp_path/'project-files/p1/reference-media/v1.mp4').unlink()
    queue.run()
    run=client.get(base).json()['runs'][0]
    assert run['status']=='completed',run
    assert run['sourceId']=='v1'
    assert run['output']['width']==64
    r=client.get(f'{base}/{rid}/video?download=true');assert r.status_code==200
    assert 'attachment' in r.headers['content-disposition']
    r=client.get(f'{base}/{rid}/video',headers={'Range':'bytes=0-15'})
    assert r.status_code==206 and len(r.content)==16
    assert not (tmp_path/f'project-files/p1/upscale/{rid}/input.mp4').exists()


def test_missing_engine_rejects_before_queuing(tmp_path):
    cfg=UpscaleConfig('/missing-engine',tmp_path/'missing-models')
    client,_,queue=setup(tmp_path,cfg)
    assert client.get('/api/projects/p1/upscale').json()['environment']['available'] is False
    assert client.post('/api/projects/p1/upscale',json={'sourceId':'v1','scale':2}).status_code==409
    assert not queue.jobs


def test_restart_marks_pending_failed_and_cleans_snapshot(tmp_path,config):
    client,project,queue=setup(tmp_path,config)
    run=client.post('/api/projects/p1/upscale',json={'sourceId':'v1','scale':2}).json()
    app=FastAPI();app.include_router(create_upscale_router(tmp_path,lambda _:project,Queue(),config=config))
    state=TestClient(app).get('/api/projects/p1/upscale').json()['runs'][0]
    assert state['status']=='failed' and '中断' in state['error']
    assert not (tmp_path/f'project-files/p1/upscale/{run["id"]}/input.mp4').exists()


def test_engine_failure_exposes_safe_error_and_allows_retry(tmp_path,config):
    Path(config.binary).write_text('#!/bin/sh\nexit 1\n')
    client,_,queue=setup(tmp_path,config)
    base='/api/projects/p1/upscale'
    run=client.post(base,json={'sourceId':'v1','scale':2}).json();queue.run()
    state=client.get(base).json()['runs'][0]
    assert state['status']=='failed' and state['error']
    assert client.get(f'{base}/{run["id"]}/video').status_code==409
    assert client.post(base,json={'sourceId':'v1','scale':2}).status_code==202


def test_symlink_output_not_served(tmp_path,config):
    client,_,queue=setup(tmp_path,config)
    base='/api/projects/p1/upscale'
    run=client.post(base,json={'sourceId':'v1','scale':2}).json();queue.run()
    target=tmp_path/f'project-files/p1/upscale/{run["id"]}/output.mp4'
    target.unlink();target.symlink_to(tmp_path/'project-files/p1/reference-media/v1.mp4')
    assert client.get(f'{base}/{run["id"]}/video').status_code==404


def test_untrusted_browser_cannot_start_compute(tmp_path):
    from app.main import create_app
    with TestClient(create_app(tmp_path)) as client:
        project=client.post('/api/projects',json={'name':'超分'}).json()
        url=f'/api/projects/{project["id"]}/upscale'
        rejected=client.post(url,json={'sourceId':'v1','scale':2},headers={'Origin':'https://evil.example'})
        assert rejected.status_code==403
        rejected=client.post(url,json={'sourceId':'v1','scale':2},headers={'Origin':'http://localhost:5173'})
        assert rejected.status_code==403
        valid=client.post(url,json={'sourceId':'v1','scale':2},headers={'Origin':'http://localhost:5173','X-AIVRE-Intent':'semantic-analysis'})
        assert valid.status_code==409
        assert client.get(url).status_code==200


def test_queue_rejection_is_durable_and_cleans_input(tmp_path,config):
    class RejectedQueue:
        def submit(self,*args): return False
    client,_,_=setup(tmp_path,config,RejectedQueue())
    assert client.post('/api/projects/p1/upscale',json={'sourceId':'v1','scale':2}).status_code==503
    run=client.get('/api/projects/p1/upscale').json()['runs'][0]
    assert run['status']=='failed'
    assert not (tmp_path/f'project-files/p1/upscale/{run["id"]}/input.mp4').exists()


def test_rejects_image_invalid_scale_and_oversized_output(tmp_path,config):
    client,project,queue=setup(tmp_path,config)
    base='/api/projects/p1/upscale'
    assert client.post(base,json={'sourceId':'v1','scale':3}).status_code==422
    project.referenceMedia.type='image'
    assert client.post(base,json={'sourceId':'v1','scale':2}).status_code==409
    project.referenceMedia.type='video';project.referenceMedia.width=3840;project.referenceMedia.height=2160
    assert client.post(base,json={'sourceId':'v1','scale':4}).status_code==409
    assert not queue.jobs


def test_audio_tail_is_not_truncated_when_longer_than_video(tmp_path,config):
    source=tmp_path/'tail.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=32x24:rate=6:duration=2','-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-c:a','aac',str(source)],check=True)
    out=tmp_path/'run';out.mkdir()
    result=execute_upscale(source,out,2,config,lambda *_:None)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(out/'output.mp4')]))
    audio=next(s for s in info['streams'] if s['codec_type']=='audio')
    assert float(audio['duration'])>=2.99
    assert abs(result['durationSeconds']-3)<1/6


@pytest.mark.parametrize('width,height,resolution,expected', [
    (1280,720,'1080p',(1920,1080)),
    (720,1280,'2k',(1440,2560)),
    (720,1410,'1080p',(1080,2116)),
    (720,1410,'2k',(1440,2820)),
    (640,640,'1080p',(1080,1080)),
])
def test_resolution_targets_preserve_orientation_and_even_dimensions(width,height,resolution,expected):
    from app.video_upscale import target_dimensions
    assert target_dimensions(width,height,resolution)==expected


@pytest.mark.parametrize("resolution,dimensions", [("1080p",(1440,1080)),("2k",(1920,1440))])
def test_resolution_api_persists_target_and_encodes_exact_dimensions(tmp_path,config,resolution,dimensions):
    client,project,queue=setup(tmp_path,config)
    base='/api/projects/p1/upscale'
    response=client.post(base,json={'sourceId':'v1','outputResolution':resolution})
    assert response.status_code==202,response.text
    assert response.json()['outputResolution']==resolution
    queue.run()
    run=client.get(base).json()['runs'][0]
    assert run['status']=='completed',run
    assert (run['output']['width'],run['output']['height'])==dimensions
    assert run['output']['durationSeconds']==2
    assert client.post(base,json={'sourceId':'v1','outputResolution':'4k'}).status_code==422


def test_resolution_rejects_redundant_or_ambiguous_upscale(tmp_path,config):
    client,project,queue=setup(tmp_path,config)
    base='/api/projects/p1/upscale'
    project.referenceMedia.width=1920;project.referenceMedia.height=1080
    assert client.post(base,json={'sourceId':'v1','outputResolution':'1080p'}).status_code==409
    assert client.post(base,json={'sourceId':'v1','outputResolution':'2k','scale':2}).status_code==422
    assert not queue.jobs
