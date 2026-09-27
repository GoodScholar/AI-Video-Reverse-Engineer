"""Use an existing isolated acceptance project, never user project storage."""
import json,subprocess,time
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app
root=Path('/var/folders/jk/nlrws6j16tv20qd4p5ctf6700000gn/T/aivre-extended-5q_eteqv')
with TestClient(create_app(data_dir=root/'data')) as c:
    for project in c.get('/api/projects').json():
        tl=f'/api/projects/{project["id"]}/timeline';state=c.get(tl).json()
        if len(state['tracks'])==8 and sum(len(t['clips']) for t in state['tracks'])==80:break
    else:raise AssertionError('No isolated maximum-capacity fixture')
    saved=c.put(tl,json={'revision':state['revision'],'settings':{'width':1920,'height':1080,'fps':30},'tracks':state['tracks']});assert saved.status_code==200,saved.text
    state=saved.json();started=time.monotonic()
    queued=c.post(tl+'/runs',json={'revision':state['revision'],'format':'mp4'});assert queued.status_code==202,queued.text
    rid=queued.json()['runs'][-1]['id'];deadline=time.monotonic()+300
    while True:
        run=next(r for r in c.get(tl).json()['runs'] if r['id']==rid)
        if run['status'] not in ('queued','running'):break
        if time.monotonic()>deadline:
            c.post(tl+'/runs/'+rid+'/cancel',json={});raise AssertionError('HD export exceeded acceptance time limit')
        time.sleep(.5)
    assert run['status']=='completed',run
    elapsed=round(time.monotonic()-started,2)
    output=root/'300s-1080p-8tracks-80clips.mp4';response=c.get(run['url']);assert response.status_code==200;output.write_bytes(response.content)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration,size:stream=codec_type,width,height,r_frame_rate','-of','json',str(output)]))
    assert any(s.get('width')==1920 and s.get('height')==1080 and s.get('r_frame_rate')=='30/1' for s in info['streams'])
    assert any(s['codec_type']=='audio' for s in info['streams'])
    assert abs(float(info['format']['duration'])-300)<.1
    subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],check=True,capture_output=True,timeout=120)
    result={'seconds':elapsed,'probe':info,'output':str(output),'tracks':8,'clips':80,'note':'1 video track and 7 audio tracks; includes deliberate gaps'}
    Path('.scratch/storage-and-hd-acceptance/evidence/hd.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
