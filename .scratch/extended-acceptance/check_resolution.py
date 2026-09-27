"""Continue acceptance against the isolated data from run_acceptance.py."""
import json
import subprocess
import sys
import time
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

root=Path(sys.argv[1]);report=json.loads((root/'report.json').read_text());pid=report['projectId'];tl=f'/api/projects/{pid}/timeline'
with TestClient(create_app(data_dir=root/'data')) as client:
    state=client.get(tl).json()
    assert len(state['tracks'])==8 and len(state['runs'])==3
    track=state['tracks'][0];track['clips']=track['clips'][:1];track['clips'][0].update(start=0,duration=14.5)
    request={'revision':state['revision'],'tracks':[track],'settings':{'width':1080,'height':1920,'fps':30}}
    rejected=client.put(tl,json={**request,'settings':{**request['settings'],'height':1922}})
    assert rejected.status_code==422,rejected.text
    response=client.put(tl,json=request);assert response.status_code==200,response.text
    state=response.json();response=client.post(tl+'/runs',json={'revision':state['revision'],'format':'mp4'});assert response.status_code==202,response.text
    rid=response.json()['runs'][-1]['id'];start=time.monotonic()
    while time.monotonic()-start<180:
        state=client.get(tl).json();run=next(r for r in state['runs'] if r['id']==rid)
        if run['status'] not in ('queued','running'): break
        time.sleep(.2)
    assert run['status']=='completed',run
    output=root/'real-video-1080p.mp4';response=client.get(run['url']);assert response.status_code==200;output.write_bytes(response.content)
    facts=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration,size:stream=codec_type,width,height','-of','json',str(output)]))
    assert any(s.get('width')==1080 and s.get('height')==1920 for s in facts['streams'])
    assert abs(float(facts['format']['duration'])-14.5)<.05
    subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],check=True,capture_output=True,timeout=120)
    report['1080p']={'elapsedSeconds':round(time.monotonic()-start,2),'probe':facts}
# Verify a fresh app can read saved timeline and serve completed output.
with TestClient(create_app(data_dir=root/'data')) as client:
    assert len(client.get(tl).json()['runs'])==4
    assert client.get(run['url']).content==output.read_bytes()
report['restartVerified']=True
(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report['1080p'],ensure_ascii=False));print('RESTART_AND_LIMIT_CHECK_PASSED')
