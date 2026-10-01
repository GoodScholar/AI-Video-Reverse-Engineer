import hashlib, shutil, subprocess
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.kuaishou_import import KuaishouProbe, KuaishouImportError, SystemKuaishouCookieStore
from app.preproduction_api import create_preproduction_router

PROJECT_ID="project-kuaishou-source"; BASE=f"/api/projects/{PROJECT_ID}/preproduction"; CANONICAL="https://www.kuaishou.com/short-video/3x9abcDEF"
class Queue:
 def submit(self,_kind,run_id,handler,**_kwargs): handler(run_id); return True
class Adapter:
 version="1.0.0"; is_controlled=True
 def __init__(self,media=None,error=None): self.media,self.error,self.probe_calls,self.download_calls,self.cookies=media,error,0,0,[]
 def probe(self,url,*,cookie=None,cancelled=None):
  self.probe_calls+=1; self.cookies.append(cookie)
  if self.error: raise self.error
  return KuaishouProbe(CANONICAL,"3x9abcDEF","授权快手样片",1.0,"owner",media_url="https://js2.a.yximgs.com/video.mp4")
 def download(self,probe,destination,*,cookie=None,cancelled=None): self.download_calls+=1; self.cookies.append(cookie); shutil.copyfile(self.media,destination); return destination
def video(path): subprocess.run(["ffmpeg","-hide_banner","-nostdin","-v","error","-y","-f","lavfi","-i","testsrc2=size=64x48:rate=6:duration=1","-f","lavfi","-i","anullsrc=r=48000:cl=mono:d=1","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(path)],check=True)
def client(tmp,adapter,queue=None,**kw):
 app=FastAPI(); project=SimpleNamespace(id=PROJECT_ID,referenceMedia=None,localPreprocessing=None); app.include_router(create_preproduction_router(tmp,lambda _:project,queue or Queue(),kuaishou_adapter=adapter,**kw)); return TestClient(app)
def test_kuaishou_e2_rights_zero_request_and_atomic_asset_idempotency(tmp_path):
 source=tmp_path/"authorized.mp4";video(source);adapter=Adapter(source);api=client(tmp_path,adapter);body={"url":CANONICAL,"role":"reference","rightsConfirmed":True,"externalRequestAccepted":True}
 denied=api.post(BASE+"/kuaishou/import",json={**body,"rightsConfirmed":False});assert denied.status_code==422 and adapter.probe_calls==0
 first=api.post(BASE+"/kuaishou/import",json=body);assert first.status_code==202,first.text;run=api.get(BASE+f"/kuaishou/imports/{first.json()['run']['id']}").json()["run"];assert run["status"]=="completed" and run["uploadedAssetId"]
 asset=next(x for x in api.get(BASE).json()["assets"] if x["id"]==run["uploadedAssetId"]);assert asset["sha256"]==hashlib.sha256(source.read_bytes()).hexdigest() and asset["videoSource"]["sourceType"]=="kuaishou" and CANONICAL not in str(asset["videoSource"]["sourceFacts"])
 assert api.post(BASE+"/kuaishou/import",json=body).json()["run"]["id"]==run["id"] and adapter.download_calls==1 and not list(tmp_path.rglob(".kuaishou-*"))
def test_kuaishou_e2_session_is_secret_and_four_state_errors(tmp_path):
 class Backend:
  values={}
  def get_password(s,a,b):return s.values.get((a,b))
  def set_password(s,a,b,c):s.values[(a,b)]=c
  def delete_password(s,a,b):s.values.pop((a,b),None)
 Backend.__module__="keyring.backends.macOS";store=SystemKuaishouCookieStore(Backend());store.set("secret")
 for status,code in [("requires_login",409),("temporarily_unavailable",503),("unsupported_link",422)]:
  api=client(tmp_path,Adapter(error=KuaishouImportError(status)),kuaishou_session_store=store);response=api.post(BASE+"/kuaishou/probe",json={"url":CANONICAL,"rightsConfirmed":True,"externalRequestAccepted":True,"useSession":True});assert response.status_code==code and "secret" not in response.text

def test_kuaishou_e2_rejects_adapter_identity_changes_and_oversized_duration(tmp_path):
    class InvalidAdapter(Adapter):
        def __init__(self, probe):
            super().__init__()
            self.result = probe

        def probe(self, *_args, **_kwargs):
            return self.result

    for probe in [
        KuaishouProbe("https://www.kuaishou.com/short-video/anotherId", "anotherId", "样片", 1, "owner"),
        KuaishouProbe(CANONICAL, "3x9abcDEF", "样片", 43201, "owner"),
    ]:
        response = client(tmp_path, InvalidAdapter(probe)).post(
            BASE + "/kuaishou/probe",
            json={"url": CANONICAL, "rightsConfirmed": True, "externalRequestAccepted": True},
        )
        assert response.status_code in {422, 503}

def test_kuaishou_e2_preserves_known_adapter_states(tmp_path):
 class StateAdapter(Adapter):
  def __init__(s,status):super().__init__();s.status=status
  def probe(s,*_args,**_kwargs):return KuaishouProbe(CANONICAL,"3x9abcDEF","样片",1,"owner",status=s.status,media_url="https://js2.a.yximgs.com/video.mp4")
 for status,code in [("requires_login",409),("unsupported_link",422),("temporarily_unavailable",503)]:
  response=client(tmp_path,StateAdapter(status)).post(BASE+"/kuaishou/probe",json={"url":CANONICAL,"rightsConfirmed":True,"externalRequestAccepted":True})
  assert response.status_code==code

def test_kuaishou_e2_cancel_then_retry_executes_only_the_retry(tmp_path):
 class HeldQueue:
  def __init__(s):s.jobs=[]
  def submit(s,_kind,run_id,handler,**_):s.jobs.append((run_id,handler));return True
  def run(s):run,handler=s.jobs.pop(0);handler(run)
 source=tmp_path/"authorized.mp4";video(source);adapter=Adapter(source);queue=HeldQueue();api=client(tmp_path,adapter,queue);body={"url":CANONICAL,"role":"reference","rightsConfirmed":True,"externalRequestAccepted":True}
 run=api.post(BASE+"/kuaishou/import",json=body).json()["run"];assert api.post(BASE+f"/kuaishou/imports/{run['id']}/cancel").status_code==202;queue.run();assert adapter.download_calls==0
 retry=api.post(BASE+f"/kuaishou/imports/{run['id']}/retry").json()["run"];queue.run();assert retry["retryOf"]==run["id"] and adapter.download_calls==1

def test_kuaishou_e2_restart_reconciles_install_before_run_transition(tmp_path,monkeypatch):
 from app.platform_video_import import PlatformVideoImportRuns
 source=tmp_path/"authorized.mp4";video(source);adapter=Adapter(source);original=PlatformVideoImportRuns.transition;failed={"once":False}
 def crash(s,project_id,run_id,**changes):
  if s._platform=="kuaishou" and changes.get("status")=="completed" and not failed["once"]:failed["once"]=True;raise OSError("crash")
  return original(s,project_id,run_id,**changes)
 monkeypatch.setattr(PlatformVideoImportRuns,"transition",crash);api=client(tmp_path,adapter);body={"url":CANONICAL,"role":"reference","rightsConfirmed":True,"externalRequestAccepted":True};run=api.post(BASE+"/kuaishou/import",json=body).json()["run"];monkeypatch.setattr(PlatformVideoImportRuns,"transition",original)
 recovered=client(tmp_path,adapter).get(BASE+f"/kuaishou/imports/{run['id']}").json()["run"];assert recovered["status"]=="completed" and recovered["uploadedAssetId"] and adapter.download_calls==1
