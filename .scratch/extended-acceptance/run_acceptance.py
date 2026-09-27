"""Opt-in local acceptance; creates isolated data, never calls model providers."""
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

source = Path('/Users/shen/Downloads/微信视频2026-09-12_132435_269.mp4')
root = Path(tempfile.mkdtemp(prefix='aivre-extended-'))
report = {'directory': str(root), 'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest()}
print('EVIDENCE_DIRECTORY=' + str(root), flush=True)

def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration,size:stream=codec_type,codec_name,width,height', '-of', 'json', str(path)]))

with TestClient(create_app(data_dir=root/'data')) as client:
    def call(method, path, **kwargs):
        response = getattr(client, method)(path, **kwargs)
        assert response.is_success, (path, response.status_code, response.text[:1000])
        return response
    pid = call('post', '/api/projects', json={'name': '真实微信视频延伸验收'}).json()['id']
    base = '/api/projects/' + pid
    call('put', base+'/reference-media', files={'file': (source.name, source.read_bytes(), 'video/mp4')})
    call('post', base+'/local-preprocessing')
    deadline=time.monotonic()+180
    while True:
        project=call('get', base).json()
        task=project['localPreprocessing']
        if task['status'] not in ('queued','running'): break
        assert time.monotonic()<deadline, task
        time.sleep(.2)
    assert task['status']=='completed', task
    prep=call('post', base+'/preproduction/import-shots', json={'revision':0}).json()
    assert prep['shots'], prep
    report['source']=probe(source)
    report['detectedShots']=len(prep['shots'])
    print('PREPROCESSING_OK shots='+str(len(prep['shots'])), flush=True)
    asset=next(a for a in prep['assets'] if a['kind']=='video')
    tl=base+'/timeline'
    state=call('get',tl).json()
    def clip(cid, aid, start, duration, **overrides):
        return dict(id=cid,assetId=aid,start=start,inPoint=0,duration=duration,speed=1,volume=1,fadeIn=0,fadeOut=0,**overrides)
    def save(tracks):
        global state
        state=call('put',tl,json={'revision':state['revision'],'settings':{'width':160,'height':284,'fps':24},'tracks':tracks}).json()
    def render(fmt,label,expected):
        global state
        check=call('post',tl+'/preflight',json={'revision':state['revision'],'format':fmt}).json()
        assert check['ready'],check
        started=time.monotonic()
        queued=call('post',tl+'/runs',json={'revision':state['revision'],'format':fmt}).json()
        rid=queued['runs'][-1]['id'];deadline=started+300
        while True:
            state=call('get',tl).json();run=next(r for r in state['runs'] if r['id']==rid)
            if run['status'] not in ('queued','running'): break
            assert time.monotonic()<deadline,run
            time.sleep(.3)
        assert run['status']=='completed',run
        output=root/(label+'.'+fmt);output.write_bytes(call('get',run['url']).content)
        facts=probe(output);assert abs(float(facts['format']['duration'])-expected)<.15,facts
        subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],check=True,capture_output=True,timeout=120)
        result={'seconds':round(time.monotonic()-started,2),'probe':facts,'output':str(output)}
        report[label]=result;print(label+'='+json.dumps(result),flush=True)
    save([{'id':'video','name':'真实视频','kind':'video','muted':False,'hidden':False,'clips':[clip('full',asset['id'],0,14.5)]}])
    render('mp4','real-video',14.5)
    # Maximum supported duration/track/clip counts, with real input and local audio.
    audio=root/'tone.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=220:duration=10','-y',str(audio)],check=True)
    prep=call('post',base+'/preproduction/assets?role=audio',files={'file':('tone.wav',audio.read_bytes(),'audio/wav')}).json()
    aid=prep['assets'][-1]['id']
    tracks=[]
    for t in range(8):
        clips=[clip(f'c{t}-{i}',asset['id'] if t==0 else aid, i*(290/9),10) for i in range(10)]
        if t:
            for c in clips: c.update(volume=.1,fadeIn=.2,fadeOut=.2)
        tracks.append({'id':f't{t}','name':f'压力轨 {t}','kind':'video' if t==0 else 'audio','muted':False,'hidden':False,'clips':clips})
    save(tracks)
    render('mp4','300s-8tracks-80clips',300)
    render('wav','300s-mix',300)
    backup=call('post',base+'/backup',json={}).content
    restored=call('post','/api/project-backups/restore',content=backup,headers={'Content-Type':'application/zip'}).json()
    restored_state=call('get','/api/projects/'+restored['id']+'/timeline').json()
    assert restored_state['tracks']==state['tracks']
    assert len(restored_state['runs'])==3
    report['backupBytes']=len(backup)
    report['originalUnchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==report['sourceSha256']
    assert report['originalUnchanged']
    report['projectId']=pid
(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('ALL_PASSED '+str(root/'report.json'),flush=True)
