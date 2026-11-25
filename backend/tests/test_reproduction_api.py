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


def test_package_contains_reference_fitted_to_selected_canvas(tmp_path):
    client,_=setup(tmp_path)
    source=tmp_path/'project-files/project-1/local-preprocessing/pre-1/normalized.png'
    Image.new('RGB',(100,100),(0,255,0)).save(source)
    state=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True}).json()
    state['settings'].update({'aspectMode':'16:9','width':1024,'height':576})
    saved=client.put(BASE,json={key:state[key] for key in ['revision','prompts','settings','comfyUrl']})
    assert saved.status_code==200,saved.text

    package=client.get(BASE+'/package')
    assert package.status_code==200,package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        with Image.open(io.BytesIO(archive.read('input/reference.png'))) as reference:
            assert reference.size==(1024,576)
            assert reference.getpixel((0,288))==(0,0,0)
            assert reference.getpixel((512,288))==(0,255,0)


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
    instances = 0
    def __init__(self,url):
        type(self).instances += 1
    def close(self): pass
    def check(self,workflow): return {'connected':True,'ready':True,'version':'test','missingNodes':[],'missingModels':[],'message':'就绪'}
    def upload_image(self,path,name):
        assert path.is_file()
        return name
    def submit(self,workflow): return 'prompt-1'
    def poll(self,prompt_id): return {'status':'completed','outputs':[{'filename':'result.mp4','subfolder':'','type':'output'}],'error':None}
    def fetch_output(self,item): return b'video-result'


def test_final_generation_write_routes_are_disabled_without_side_effects(tmp_path):
    FakeComfy.instances = 0
    client, _ = setup(tmp_path, FakeComfy)
    responses = [
        client.post(BASE + '/runs'),
        client.post(BASE + '/runs', json={'revision': 0, 'disclosureAccepted': True}),
        client.post(BASE + '/runs/old-run/refresh', json={}),
        client.post(BASE + '/runs/old-run/resolve', json={'confirmedNotQueued': True}),
    ]
    assert [response.status_code for response in responses] == [410, 410, 410, 410]
    assert {response.json()['detail']['code'] for response in responses} == {'final_generation_disabled'}
    assert {response.json()['detail']['message'] for response in responses} == {'当前工作台只准备素材与方案，请导出后在外部工具执行视频生成。'}
    assert client.get(BASE).json()['runs'] == []
    assert FakeComfy.instances == 0


def test_historical_generation_records_and_outputs_remain_read_only(tmp_path):
    from app.reproduction import GenerationRun, ReproductionStore

    client, project = setup(tmp_path)
    store = ReproductionStore(tmp_path)
    state = store.load(project)
    state.runs.append(GenerationRun(id='old-run', status='completed', createdAt='2026-09-14T00:00:00Z',
        revision=1, sourceHash='old-hash', comfyUrl='http://127.0.0.1:8188', workflow={},
        outputs=[{'filename': 'output-0.mp4'}]))
    output = store.path(project.id, 'old-run', 'output-0.mp4')
    output.parent.mkdir(parents=True)
    output.write_bytes(b'historical-video')
    store.save(project.id, state)

    history = client.get(BASE).json()['runs']
    assert history[0]['id'] == 'old-run'
    assert history[0]['status'] == 'completed'
    assert client.get(BASE + '/runs/old-run/output/0').content == b'historical-video'


def test_sensitive_reproduction_request_rejects_foreign_origin(tmp_path):
    from app.main import create_app
    client=TestClient(create_app(data_dir=tmp_path))
    response=client.post(BASE+'/prompts',json={'revision':0,'disclosureAccepted':True},
        headers={'origin':'https://untrusted.example','x-aivre-intent':'semantic-analysis'})
    assert response.status_code==403


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
    p.referenceMedia.width=640
    p.referenceMedia.height=360
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
    assert (int(stream['width']),int(stream['height']))==(848,480)
    assert int(stream['nb_frames'])==81
    assert stream['r_frame_rate']=='16/1'
    frame=subprocess.check_output(['ffmpeg','-v','error','-i',str(target),'-frames:v','1',
        '-f','rawvideo','-pix_fmt','gray','-'])
    assert frame[240*848] < 20
    assert 80 < frame[240*848+424] < 180
