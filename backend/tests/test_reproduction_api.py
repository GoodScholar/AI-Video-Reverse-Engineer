from types import SimpleNamespace
from pathlib import Path
import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.reproduction_api import create_reproduction_router
from app.reproduction import SavedPrompts
from app.semantic_analysis import StructuredVisualAnalysis


def setup(tmp_path, client_factory=None):
    visual = {key: '人物' for key in ['subject', 'scene', 'composition', 'viewpoint', 'lighting', 'color', 'visualStyle']}
    analysis = StructuredVisualAnalysis(observedFacts={'staticVisual': visual, 'temporal': None}, generationSuggestions={
        'subjectMotion': '缓慢移动', 'environmentalMotion': '无', 'cameraMotion': '固定', 'rhythm': '缓慢', 'suggestedDuration': 5, 'audio': '无',
    })
    p = SimpleNamespace(id='project-1', referenceMedia=SimpleNamespace(id='image-1',type='image',width=512,height=512),
        semanticAnalysis=SimpleNamespace(id='analysis-1',status='completed',result=analysis,sourceReferenceMediaId='image-1',sourcePreprocessingId='pre-1'),
        localPreprocessing=SimpleNamespace(id='pre-1',status='completed',reproducibilityAssessment=None),
        activeDepthCaptureId=None,depthCaptures=[])
    source=tmp_path/'project-files/project-1/local-preprocessing/pre-1/normalized.png'
    source.parent.mkdir(parents=True)
    Image.new('RGB',(512,512)).save(source)
    prompts=SavedPrompts(positiveZh='人物',negativeZh='模糊',positiveEn='A person',negativeEn='blurry')
    app=FastAPI()
    app.include_router(create_reproduction_router(tmp_path,lambda _:p,lambda _:prompts,client_factory=client_factory))
    return TestClient(app),p


BASE='/api/projects/project-1/reproduction'


def test_plan_generates_edits_exports_and_becomes_stale(tmp_path):
    client,p=setup(tmp_path)
    state=client.get(BASE).json()
    assert state['prompts'] is None and state['canGeneratePrompts']
    assert client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':False}).status_code==422
    generated=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True})
    assert generated.status_code==200, generated.text
    state=generated.json()
    state['prompts']['positiveEn']='A person walking'
    saved=client.put(BASE,json={key:state[key] for key in ['revision','prompts','settings','comfyUrl']})
    assert saved.status_code==200, saved.text
    assert client.get(BASE).json()['prompts']['positiveEn']=='A person walking'
    package=client.get(BASE+'/package')
    assert package.status_code==200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as z:
        assert {'workflow-api.json','manifest.json','prompts.json','input/reference.png'} <= set(z.namelist())
        assert b'A person walking' in z.read('workflow-api.json')
    p.semanticAnalysis.result=p.semanticAnalysis.result.model_copy(update={'version':2})
    assert client.get(BASE).json()['stale']
    assert client.get(BASE+'/package').status_code==409
    assert client.get(BASE).json()['prompts']['positiveEn']=='A person walking'


def test_revision_conflict_does_not_overwrite_edits(tmp_path):
    client,_=setup(tmp_path)
    state=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True}).json()
    body={key:state[key] for key in ['revision','prompts','settings','comfyUrl']}
    assert client.put(BASE,json=body).status_code==200
    assert client.put(BASE,json=body).status_code==409


def test_no_analysis_rejects_generation(tmp_path):
    client,p=setup(tmp_path)
    p.semanticAnalysis=None
    assert not client.get(BASE).json()['canGeneratePrompts']
    assert client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True}).status_code==409


def test_stale_plan_keeps_user_edits_but_remains_stale(tmp_path):
    client,p=setup(tmp_path)
    state=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True}).json()
    p.semanticAnalysis.result=p.semanticAnalysis.result.model_copy(update={'version':2})
    body={key:state[key] for key in ['revision','prompts','settings','comfyUrl']}
    body['prompts']['positiveEn']='My correction'
    response=client.put(BASE,json=body)
    assert response.status_code==200
    assert response.json()['stale']
    assert response.json()['prompts']['positiveEn']=='My correction'


def test_prompt_failure_preserves_last_success(tmp_path):
    client,p=setup(tmp_path)
    first=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True}).json()
    p.semanticAnalysis=None
    assert client.post(BASE+'/prompts',json={'revision':1,'disclosureAccepted':True}).status_code==409
    assert client.get(BASE).json()['prompts']==first['prompts']


class FakeComfy:
    def __init__(self,url): pass
    def close(self): pass
    def check(self,workflow): return {'connected':True,'ready':True,'version':'test','missingNodes':[],'missingModels':[],'message':'就绪'}
    def upload_image(self,path,name):
        assert path.is_file()
        return name
    def submit(self,workflow): return 'prompt-1'
    def poll(self,prompt_id): return {'status':'completed','outputs':[{'filename':'result.mp4','subfolder':'','type':'output'}],'error':None}
    def fetch_output(self,item): return b'video-result'


def test_explicit_run_persists_outputs_and_disallows_duplicate_submission(tmp_path):
    client,_=setup(tmp_path,FakeComfy)
    client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True})
    assert client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':False}).status_code==422
    queued=client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':True})
    assert queued.status_code==200, queued.text
    run=queued.json()['runs'][0]
    assert run['status']=='queued'
    assert client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':True}).status_code==409
    completed=client.post(BASE+f"/runs/{run['id']}/refresh",json={}).json()
    assert completed['runs'][0]['status']=='completed'
    url=completed['runs'][0]['outputs'][0]['url']
    assert client.get(url).content==b'video-result'
    assert client.get(BASE).json()['runs'][0]['status']=='completed'


def test_unknown_submission_requires_explicit_resolution(tmp_path):
    class TimeoutComfy(FakeComfy):
        def submit(self,workflow): raise TimeoutError('uncertain')
    client,_=setup(tmp_path,TimeoutComfy)
    client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True})
    assert client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':True}).status_code==502
    run=client.get(BASE).json()['runs'][0]
    assert run['status']=='unknown'
    endpoint=BASE+f"/runs/{run['id']}/resolve"
    assert client.post(endpoint,json={}).status_code==422
    assert client.post(endpoint,json={'confirmedNotQueued':True}).json()['runs'][0]['status']=='failed'


def test_late_running_response_does_not_erase_completed_output(tmp_path):
    from threading import Event, Thread
    entered,release=Event(),Event()
    class RacingComfy(FakeComfy):
        calls=0
        def poll(self,prompt_id):
            RacingComfy.calls+=1
            if RacingComfy.calls==1:
                entered.set()
                assert release.wait(5)
                return {'status':'running','outputs':[],'error':None}
            return super().poll(prompt_id)
    client,_=setup(tmp_path,RacingComfy)
    client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True})
    run=client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':True}).json()['runs'][0]
    endpoint=BASE+f"/runs/{run['id']}/refresh"
    first=Thread(target=lambda:client.post(endpoint,json={}))
    first.start()
    try:
        assert entered.wait(5)
        assert client.post(endpoint,json={}).json()['runs'][0]['status']=='completed'
    finally:
        release.set()
        first.join(5)
    final=client.get(BASE).json()['runs'][0]
    assert final['status']=='completed' and len(final['outputs'])==1


def test_explicit_comfy_rejection_is_retryable_not_unknown(tmp_path):
    from app.comfyui_client import ComfyUIClientError
    class RejectedComfy(FakeComfy):
        def submit(self,workflow): raise ComfyUIClientError('Rejected',outcome_unknown=False,status_code=400)
    client,_=setup(tmp_path,RejectedComfy)
    client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True})
    assert client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':True}).status_code==502
    assert client.get(BASE).json()['runs'][0]['status']=='failed'
    assert client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':True}).status_code==502
    assert len(client.get(BASE).json()['runs'])==2


def test_interrupted_submitting_record_is_recoverable_after_restart(tmp_path):
    from app.reproduction import ReproductionStore, GenerationRun
    client,p=setup(tmp_path)
    client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True})
    store=ReproductionStore(tmp_path)
    state=store.load(p)
    state.runs.append(GenerationRun(id='run-1',status='submitting',createdAt='2026-09-14T00:00:00Z',
        revision=1,sourceHash=state.sourceHash,comfyUrl=state.comfyUrl,workflow={}))
    store.save(p.id,state)
    assert client.get(BASE).json()['runs'][0]['status']=='unknown'
    response=client.post(BASE+'/runs/run-1/resolve',json={'promptId':'remote-1'})
    assert response.status_code==200
    assert response.json()['runs'][0]['status']=='queued'


def test_sensitive_reproduction_request_rejects_foreign_origin(tmp_path):
    from app.main import create_app
    client=TestClient(create_app(data_dir=tmp_path))
    response=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True},
        headers={'origin':'https://untrusted.example','x-aivre-intent':'semantic-analysis'})
    assert response.status_code==403


def test_refresh_from_old_prompt_cannot_overwrite_resolved_prompt(tmp_path):
    from threading import Event, Thread
    from app.reproduction import ReproductionStore
    entered,release=Event(),Event()
    class SlowComfy(FakeComfy):
        def poll(self,prompt_id):
            entered.set()
            assert release.wait(5)
            return {'status':'failed','outputs':[],'error':'old prompt failed'}
    client,p=setup(tmp_path,SlowComfy)
    client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True})
    run=client.post(BASE+'/runs',json={'revision':1,'disclosureAccepted':True}).json()['runs'][0]
    endpoint=BASE+f"/runs/{run['id']}"
    store=ReproductionStore(tmp_path)
    state=store.load(p)
    state.runs[0].status='unknown'
    store.save(p.id,state)
    first=Thread(target=lambda:client.post(endpoint+'/refresh',json={}))
    first.start()
    try:
        assert entered.wait(5)
        assert client.post(endpoint+'/resolve',json={'promptId':'new-prompt'}).status_code==200
    finally:
        release.set()
        first.join(5)
    final=client.get(BASE).json()['runs'][0]
    assert final['status']=='queued' and final['promptId']=='new-prompt'


def test_main_app_reuses_original_provider_for_text_only_prompts(tmp_path):
    import json
    from app.main import create_app
    from app.analysis_providers.base import ProviderResult
    from test_semantic_analysis_api import InMemoryCredentials, ManualQueue, ready_project

    holder={}
    observed=[]
    class Provider:
        def analyze(self,request):
            observed.append(request)
            return ProviderResult(rawText=json.dumps({'positiveZh':'人物','negativeZh':'模糊',
                'positiveEn':'A person','negativeEn':'blurry'}))
    def queue_factory(handler):
        holder['queue']=ManualQueue(handler)
        return holder['queue']
    _,sample=setup(tmp_path/'sample')
    client=TestClient(create_app(data_dir=tmp_path,credential_store=InMemoryCredentials(),
        provider_registry=lambda **kwargs:Provider(),semantic_analysis_queue_factory=queue_factory,
        semantic_analysis_runner=lambda **kwargs:sample.semanticAnalysis.result))
    project_id=ready_project(client,tmp_path)
    assert client.put('/api/analysis-providers/bailian/configuration',json={'model':'qwen3.7-flash'}).status_code==200
    assert client.post(f'/api/projects/{project_id}/semantic-analysis',json={
        'provider':'bailian','model':'qwen3.7-flash','disclosureAccepted':True}).status_code==202
    holder['queue'].run_next()
    base=f'/api/projects/{project_id}/reproduction'
    response=client.post(base+'/prompts',json={'revision':0,'disclosureAccepted':True})
    assert response.status_code==200,response.text
    assert response.json()['prompts']['positiveEn']=='A person'
    assert len(observed)==1 and observed[0].task=='prompt_generation'
    assert observed[0].analysisInput is None and not observed[0].isRepair
    assert client.get(base+'/package').status_code==200


def test_depth_package_contains_time_matched_control_video(tmp_path):
    import shutil
    import subprocess
    import json
    import pytest
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('需要 FFmpeg')
    client,p=setup(tmp_path)
    p.referenceMedia.type='video'
    p.referenceMedia.durationSeconds=2.0
    pre=tmp_path/'project-files/project-1/local-preprocessing/pre-1'
    (pre/'keyframes').mkdir()
    Image.new('RGB',(64,64)).save(pre/'keyframes/frame-0001.jpg')
    p.activeDepthCaptureId='depth-1'
    p.depthCaptures=[SimpleNamespace(id='depth-1',sourceReferenceVideoId='image-1',status='completed',
        qualityAssessment=SimpleNamespace(status='passed'),reviewConfirmedAt=None)]
    source=tmp_path/'project-files/project-1/depth-captures/depth-1/depth-control.mp4'
    source.parent.mkdir(parents=True)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=gray:s=64x64:r=8',
        '-t','2','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True)
    state=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True}).json()
    assert state['settings']['strategy']=='wan22_fun_control'
    result=client.get(BASE+'/package')
    assert result.status_code==200,result.text
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        target=tmp_path/'exported-depth.mp4'
        target.write_bytes(archive.read('input/depth-control.mp4'))
    probed=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(target)],
        check=True,capture_output=True,text=True)
    stream=json.loads(probed.stdout)['streams'][0]
    assert int(stream['nb_frames'])==81
    assert stream['r_frame_rate']=='16/1'
