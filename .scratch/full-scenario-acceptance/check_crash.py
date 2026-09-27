"""Abruptly stop only processes spawned by this isolated acceptance run."""
import io,json,os,signal,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from app.main import create_app
root=Path(tempfile.mkdtemp(prefix='aivre-crash-'));data=root/'data'
with TestClient(create_app(data_dir=data)) as c:
    pid=c.post('/api/projects',json={'name':'强制中断隔离验收'}).json()['id'];base=f'/api/projects/{pid}';tl=base+'/timeline'
    image=io.BytesIO();Image.new('RGB',(96,64),'blue').save(image,format='PNG')
    uploaded=c.post(base+'/preproduction/assets?role=reference',files={'file':('test.png',image.getvalue(),'image/png')});assert uploaded.status_code==200,uploaded.text
    aid=uploaded.json()['assets'][0]['id']
    body={'revision':0,'settings':{'width':1920,'height':1080,'fps':30},'tracks':[{'id':'v','name':'压力画面','kind':'video','muted':False,'hidden':False,'clips':[{'id':'c','assetId':aid,'start':0,'inPoint':0,'duration':300,'speed':1,'volume':1,'fadeIn':0,'fadeOut':0}]}]}
    saved=c.put(tl,json=body);assert saved.status_code==200,saved.text
with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
env={**os.environ,'PYTHONPATH':str(Path('backend').resolve()),'AI_VIDEO_REVERSE_ENGINEER_DATA_DIR':str(data)}
log=(root/'server.log').open('w');server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=log)
children=[]
try:
    def request(path,body=None):
        raw=None if body is None else json.dumps(body).encode()
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=raw,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
    for _ in range(100):
        try: request('/api/projects');break
        except OSError:time.sleep(.05)
    run=request(tl+'/runs',{'revision':1,'format':'mp4'})['runs'][-1]
    folder=data/'project-files'/pid/'timeline'/'runs'/run['id']
    deadline=time.monotonic()+20
    while not list(folder.glob('.output.*.mp4')):
        assert time.monotonic()<deadline,'No encoder output'
        time.sleep(.05)
    rows=[tuple(map(int,line.split())) for line in subprocess.check_output(['ps','-axo','pid=,ppid='],text=True).splitlines()]
    parents={server.pid}
    while True:
        found={child for child,parent in rows if parent in parents}-parents
        if not found:break
        children.extend(found);parents.update(found)
    assert children,'Expected an owned FFmpeg child'
    server.kill();server.wait(timeout=5)
finally:
    if server.poll() is None:server.kill();server.wait(timeout=5)
    for child in reversed(children):
        try:os.kill(child,signal.SIGKILL)
        except ProcessLookupError:pass
    log.close()
with TestClient(create_app(data_dir=data)) as c:
    state=c.get(tl).json();assert state['runs'][-1]['status']=='failed',state['runs']
    assert c.get(tl+'/runs/'+run['id']+'/output').status_code==409
    body.update(revision=state['revision'],settings={'width':96,'height':64,'fps':24});body['tracks'][0]['clips'][0]['duration']=2
    state=c.put(tl,json=body).json();queued=c.post(tl+'/runs',json={'revision':state['revision'],'format':'mp4'});assert queued.status_code==202,queued.text
    deadline=time.monotonic()+20
    while True:
        current=c.get(tl).json()['runs'][-1]
        if current['status'] not in ('queued','running'):break
        assert time.monotonic()<deadline
        time.sleep(.05)
    assert current['status']=='completed',current
    assert c.get(current['url']).status_code==200
    backup=c.post(base+'/backup',json={});assert backup.status_code==200,backup.text
    restored=c.post('/api/project-backups/restore',content=backup.content,headers={'Content-Type':'application/zip'});assert restored.status_code==201,restored.text
result={'directory':str(root),'interruptedRunStatus':'failed','partialDownloadStatus':409,'retry':'completed','backupRestore':'passed','remainingPartialFiles':len(list(folder.glob('.output.*.mp4')))}
print(json.dumps(result,ensure_ascii=False,indent=2))
Path('.scratch/full-scenario-acceptance/evidence/crash.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
