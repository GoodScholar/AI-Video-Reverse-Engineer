import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.video_toolkit import ToolkitConfig, safe_child, validate_cuts, list_assets
from app.video_toolkit_api import create_toolkit_router

class Queue:
    def __init__(self): self.jobs=[]
    def submit(self, kind, pid, fn): self.jobs.append((pid,fn)); return True
    def run(self):
        pid,fn=self.jobs.pop(0); fn(pid)

def setup(tmp_path, runner=None):
    source=tmp_path/'project-files/p1/reference-media/v1.mp4'
    source.parent.mkdir(parents=True)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=64x48:rate=6:duration=1','-c:v','libx264',str(source)],check=True)
    media=SimpleNamespace(id='v1',type='video',format='mp4',originalName='clip.mp4')
    project=SimpleNamespace(id='p1',referenceMedia=media)
    queue=Queue()
    config=ToolkitConfig.local()
    app=FastAPI()
    app.include_router(create_toolkit_router(tmp_path,lambda _:project,queue,config=config,runner=runner,environment=lambda:{k:{'available':True,'message':'test'} for k in ('scenes','subtitles','mask','interpolate','upscale')}))
    return TestClient(app),queue,project

def scenes_runner(source,directory,kind,params,config,cancel,progress):
    assert source.is_file()
    (directory/'scenes.json').write_text(json.dumps({'duration':1,'cuts':[.5]}))
    return ['scenes.json']

def test_assets_only_current_and_completed_safe_outputs(tmp_path):
    client,_,project=setup(tmp_path)
    state=client.get('/api/projects/p1/toolkit').json()
    assert [a['id'] for a in state['assets']]==['reference:v1']
    root=tmp_path/'project-files/p1/upscale/r1';root.mkdir(parents=True)
    (root/'state.json').write_text(json.dumps({'id':'r1','status':'running'}))
    (root/'output.mp4').write_bytes(b'partial')
    assert len(list_assets(tmp_path,project))==1
    (root/'state.json').write_text(json.dumps({'id':'r1','status':'completed'}))
    assert len(list_assets(tmp_path,project))==2
    (root/'output.mp4').unlink();(root/'output.mp4').symlink_to(tmp_path/'project-files/p1/reference-media/v1.mp4')
    assert len(list_assets(tmp_path,project))==1

@pytest.mark.parametrize('cuts',[[0],[1],[.7,.3],[.3,.3],[float('nan')],[-1]])
def test_invalid_cuts_rejected(cuts):
    with pytest.raises(ValueError): validate_cuts(cuts,1)

def test_task_snapshot_scene_edits_and_revision(tmp_path):
    client,queue,project=setup(tmp_path,scenes_runner);base='/api/projects/p1/toolkit'
    response=client.post(base+'/runs',json={'assetIds':['reference:v1'],'kind':'scenes','params':{}})
    assert response.status_code==202,response.text
    rid=response.json()['runs'][0]['id']
    project.referenceMedia.id='v2'
    queue.run()
    state=client.get(base).json();run=state['runs'][0]
    assert run['status']=='completed',run
    r=client.get(base+f'/runs/{rid}/artifacts/scenes.json');assert r.json()['cuts']==[.5]
    assert client.put(base+f'/runs/{rid}/cuts',json={'revision':0,'cuts':[.25,.75]}).status_code==200
    assert client.put(base+f'/runs/{rid}/cuts',json={'revision':0,'cuts':[.4]}).status_code==409
    assert client.get(base+f'/runs/{rid}/artifacts/scenes.json').json()['cuts']==[.25,.75]

def test_cancel_queued_and_retry(tmp_path):
    client,queue,_=setup(tmp_path,scenes_runner);base='/api/projects/p1/toolkit'
    rid=client.post(base+'/runs',json={'assetIds':['reference:v1'],'kind':'scenes'}).json()['runs'][0]['id']
    assert client.post(base+f'/runs/{rid}/cancel',json={}).status_code==200
    queue.run()
    assert client.get(base).json()['runs'][0]['status']=='cancelled'
    assert client.post(base+f'/runs/{rid}/retry',json={}).status_code==202
    queue.run()
    assert client.get(base).json()['runs'][0]['status']=='completed'

def test_batch_validation_is_atomic(tmp_path):
    client,queue,_=setup(tmp_path,scenes_runner)
    response=client.post('/api/projects/p1/toolkit/runs',json={'assetIds':['reference:v1','reference:missing'],'kind':'scenes'})
    assert response.status_code==409
    assert not queue.jobs
    assert not client.get('/api/projects/p1/toolkit').json()['runs']

def test_failed_runner_does_not_publish_artifacts(tmp_path):
    def bad(source,directory,*args):
        (directory/'output.mp4').write_bytes(b'partial');raise RuntimeError('/private/secret')
    client,queue,_=setup(tmp_path,bad);base='/api/projects/p1/toolkit'
    rid=client.post(base+'/runs',json={'assetIds':['reference:v1'],'kind':'interpolate'}).json()['runs'][0]['id'];queue.run()
    state=client.get(base).json()
    assert state['runs'][0]['status']=='failed'
    assert '/private' not in state['runs'][0]['error']
    assert client.get(base+f'/runs/{rid}/artifacts/output.mp4').status_code==409
    assert len(state['assets'])==1

def test_safe_path_blocks_parent_and_symlink(tmp_path):
    with pytest.raises(ValueError):safe_child(tmp_path,'..','escape')
    (tmp_path/'link').symlink_to('/tmp',target_is_directory=True)
    with pytest.raises(ValueError):safe_child(tmp_path,'link','x')

def test_restart_marks_pending_interrupted(tmp_path):
    client,_,project=setup(tmp_path,scenes_runner);base='/api/projects/p1/toolkit'
    client.post(base+'/runs',json={'assetIds':['reference:v1'],'kind':'scenes'})
    app=FastAPI();app.include_router(create_toolkit_router(tmp_path,lambda _:project,Queue()))
    assert TestClient(app).get(base).json()['runs'][0]['status']=='failed'

def test_frame_preview_range_and_mask_validation(tmp_path):
    client,_,_=setup(tmp_path)
    base='/api/projects/p1/toolkit'
    r=client.get(base+'/assets/reference:v1/frame')
    assert r.status_code==200 and r.headers['content-type']=='image/png'
    r=client.get(base+'/assets/reference:v1/video',headers={'Range':'bytes=0-15'})
    assert r.status_code==206 and len(r.content)==16
    assert client.post(base+'/runs',json={'assetIds':['reference:v1'],'kind':'mask'}).status_code==409
    assert client.post(base+'/runs',json={'assetIds':['reference:v1'],'kind':'mask','params':{'points':[[2,.5,1]]}}).status_code==422


def test_shutdown_cancels_pending_before_wait(tmp_path):
    client,queue,_=setup(tmp_path)
    with client:
        client.post('/api/projects/p1/toolkit/runs',json={'assetIds':['reference:v1'],'kind':'scenes'})
    assert client.get('/api/projects/p1/toolkit').json()['runs'][0]['status']=='cancelled'
    queue.run()
    assert client.get('/api/projects/p1/toolkit').json()['runs'][0]['status']=='cancelled'


def test_pipeline_chains_only_video_steps_and_preserves_analysis_input(tmp_path):
    calls=[]
    def pipeline_runner(source,directory,kind,*args):
        calls.append((kind,source.name,source.parent.name))
        if kind=='scenes':
            (directory/'scenes.json').write_text(json.dumps({'duration':1,'cuts':[]}))
            return ['scenes.json']
        (directory/'output.mp4').write_bytes(b'video')
        return ['output.mp4']
    client,queue,_=setup(tmp_path,pipeline_runner);base='/api/projects/p1/toolkit'
    response=client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'scenes'},{'kind':'interpolate','params':{'fps':60}},{'kind':'upscale','params':{'resolution':'2k'}}]})
    assert response.status_code==202,response.text
    pipeline=response.json()['pipeline']
    while queue.jobs: queue.run()
    saved=client.get(base).json()['pipelines'][0]
    assert saved['status']=='completed'
    assert [step['status'] for step in saved['steps']]==['completed','completed','completed']
    assert calls[0][1:] == ('input.mp4',pipeline['id'])
    assert calls[1][1:] == ('input.mp4',pipeline['id'])
    assert calls[2][1:] == ('output.mp4','01-interpolate')
    assert f'pipeline:{pipeline["id"]}' in [asset['id'] for asset in client.get(base).json()['assets']]
    assert client.get(base+f'/pipelines/{pipeline["id"]}/steps/{saved["steps"][-1]["id"]}/artifacts/output.mp4').content == b'video'


def test_pipeline_failure_resumes_at_failed_step_without_repeating_success(tmp_path):
    calls=[]
    def flaky(source,directory,kind,*args):
        calls.append(kind)
        if kind=='upscale' and calls.count('upscale')==1: raise RuntimeError('broken')
        (directory/'output.mp4').write_bytes(b'video')
        return ['output.mp4']
    client,queue,_=setup(tmp_path,flaky);base='/api/projects/p1/toolkit'
    pid=client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'interpolate'},{'kind':'upscale'}]}).json()['pipeline']['id']
    while queue.jobs: queue.run()
    failed=client.get(base).json()['pipelines'][0]
    assert failed['status']=='failed'
    assert [step['status'] for step in failed['steps']]==['completed','failed']
    assert client.post(base+f'/pipelines/{pid}/retry',json={}).status_code==202
    while queue.jobs: queue.run()
    assert client.get(base).json()['pipelines'][0]['status']=='completed'
    assert calls==['interpolate','upscale','upscale']


def test_pipeline_cancel_blocks_downstream_and_restart_marks_retryable(tmp_path):
    client,queue,project=setup(tmp_path,scenes_runner);base='/api/projects/p1/toolkit'
    pid=client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'scenes'},{'kind':'interpolate'}]}).json()['pipeline']['id']
    assert client.post(base+f'/pipelines/{pid}/cancel',json={}).status_code==200
    queue.run()
    cancelled=client.get(base).json()['pipelines'][0]
    assert cancelled['status']=='cancelled'
    assert cancelled['steps'][0]['status']=='cancelled'
    assert cancelled['steps'][1]['status']=='blocked'
    app=FastAPI();app.include_router(create_toolkit_router(tmp_path,lambda _:project,Queue(),runner=scenes_runner,environment=lambda:{k:{'available':True,'message':'test'} for k in ('scenes','subtitles','mask','interpolate','upscale')}))
    assert TestClient(app).get(base).json()['pipelines'][0]['status']=='cancelled'


def test_restart_clears_cancelled_pipeline_in_flight_marker(tmp_path):
    client,_,project=setup(tmp_path,scenes_runner);base='/api/projects/p1/toolkit'
    pid=client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'scenes'},{'kind':'interpolate'}]}).json()['pipeline']['id']
    client.post(base+f'/pipelines/{pid}/cancel',json={})
    app=FastAPI();app.include_router(create_toolkit_router(tmp_path,lambda _:project,Queue(),runner=scenes_runner,environment=lambda:{k:{'available':True,'message':'test'} for k in ('scenes','subtitles','mask','interpolate','upscale')}))
    recovered=TestClient(app)
    assert recovered.post(base+f'/pipelines/{pid}/retry',json={}).status_code==202


def test_pipeline_rejects_invalid_order_missing_dependency_and_active_duplicate(tmp_path):
    client,_,_=setup(tmp_path,scenes_runner);base='/api/projects/p1/toolkit'
    assert client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'upscale'}]}).status_code==422
    assert client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'upscale'},{'kind':'interpolate'}]}).status_code==422
    assert client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'mask'},{'kind':'interpolate'}]}).status_code==422
    created=client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'scenes'},{'kind':'interpolate'}]})
    assert created.status_code==202
    assert client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'scenes'},{'kind':'interpolate'}]}).status_code==409


def test_pipeline_retry_rejects_another_active_pipeline_for_the_same_asset(tmp_path):
    def broken(*args): raise RuntimeError('broken')
    client,queue,_=setup(tmp_path,broken);base='/api/projects/p1/toolkit'
    failed=client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'scenes'},{'kind':'interpolate'}]}).json()['pipeline']['id']
    queue.run()
    created=client.post(base+'/pipelines',json={'assetId':'reference:v1','steps':[{'kind':'scenes'},{'kind':'interpolate'}]})
    assert created.status_code==202
    assert client.post(base+f'/pipelines/{failed}/retry',json={}).status_code==409


def test_untrusted_toolkit_mutation_is_rejected(tmp_path):
    from app.main import create_app
    with TestClient(create_app(tmp_path)) as client:
        project=client.post('/api/projects',json={'name':'工具'}).json()
        url=f'/api/projects/{project["id"]}/toolkit/runs'
        assert client.post(url,json={'assetIds':['x'],'kind':'scenes'},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post(url,json={'assetIds':['x'],'kind':'scenes'},headers={'Origin':'http://localhost:5173'}).status_code==403
