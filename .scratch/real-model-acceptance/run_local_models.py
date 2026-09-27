"""Existing offline models only; no network calls or installations."""
import json,subprocess,tempfile,time
from pathlib import Path
from dataclasses import asdict
from app.depth_capture_runner import DepthCaptureRequest,run_depth_capture
from app.video_upscale import UpscaleConfig,execute_upscale
root=Path(tempfile.mkdtemp(prefix='aivre-real-models-'));repo=Path.cwd();data=root/'data';source=data/'project-files/acceptance/reference-media/source.mp4';source.parent.mkdir(parents=True)
subprocess.run(['ffmpeg','-v','error','-y','-i','/Users/shen/Downloads/微信视频2026-09-12_132435_269.mp4','-t','2','-vf','fps=8,scale=160:284','-c:v','libx264','-c:a','aac',str(source)],check=True)
report={'directory':str(root),'input':'2-second 160x284 8fps excerpt of user cat video','results':{}}
print('DIRECTORY='+str(root),flush=True)
def save():Path('.scratch/real-model-acceptance/evidence/results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
def stage(name,fn):
 start=time.monotonic()
 try: report['results'][name]={'status':'executed','seconds':round(time.monotonic()-start,2),'result':fn()}
 except Exception as exc: report['results'][name]={'status':'failed','error':str(exc),'seconds':round(time.monotonic()-start,2)}
 report['results'][name]['seconds']=round(time.monotonic()-start,2);save();print(name+'='+json.dumps(report['results'][name],ensure_ascii=False,default=str),flush=True)
def person():
 out=root/'person';out.mkdir()
 command=[str(repo/'backend/person_worker/.venv/bin/python'),str(repo/'backend/person_worker/run_person.py'),'--input',str(source),'--output',str(out),'--model',str(repo/'backend/person_worker/models/pose_landmarker_full.task')]
 result=subprocess.run(command,capture_output=True,text=True,timeout=180)
 (out/'worker.log').write_text(result.stdout+'\n'+result.stderr)
 assert result.returncode==0,(result.returncode,result.stdout[-1000:],result.stderr[-1000:])
 return {'quality':json.loads((out/'quality.json').read_text()),'files':[p.name for p in out.iterdir()]}
stage('person-cat-negative',person)
for profile in ('480p','720p'):
 def depth(profile=profile):
  request=DepthCaptureRequest(data,'acceptance','depth-'+profile,'source',source,repo/'backend/depth_worker/checkpoints/video_depth_anything_vits.pth',repo/'backend/depth_worker/vendor/Video-Depth-Anything','cpu',2,profile)
  result=run_depth_capture(request,str(repo/'backend/depth_worker/.venv/bin/python'),repo/'backend/depth_worker/run_depth.py','ffmpeg',on_stage_started=lambda name:print(profile+':'+name,flush=True))
  return asdict(result)
 stage('depth-'+profile,depth)
for target in ('1080p','2k'):
 def upscale(target=target):
  out=root/('upscale-'+target);out.mkdir()
  result=execute_upscale(source,out,target,UpscaleConfig.local(),lambda name,count:None)
  return result
 stage('upscale-'+target,upscale)
print('FINISHED',flush=True)
