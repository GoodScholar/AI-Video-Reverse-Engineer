"""Public API behaviour for network product pictures; no real network in regressions."""
import io
from threading import RLock

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.preproduction import PreproductionStore

BASE = '/api/projects/p1/preproduction/product-images'

def png(color='orange'):
    data=io.BytesIO()
    Image.new('RGB',(400,600),color).save(data,format='PNG')
    return data.getvalue()

def setup(tmp_path, fetch=None, search=None):
    # Import here so the red assertion can report the missing public route.
    from app.product_images_api import create_product_images_router
    app=FastAPI()
    app.include_router(create_product_images_router(tmp_path, lambda pid: {} if pid in ('p1','p2') else None,
        source_lock=RLock(), search=search or (lambda name:[{'title':name,'url':'https://shop.example/product','snippet':'商品展示'}]),
        fetch=fetch or (lambda url, limit: (png() if url.endswith('.png') else '<title>大窑汽水</title><img src="/front.png" alt="大窑正面"><img src="/back.png" alt="大窑背面">'.encode(), 'image/png' if url.endswith('.png') else 'text/html','https://shop.example/product' if not url.endswith('.png') else url))))
    return TestClient(app)

def test_search_preview_import_preserves_sources_and_deduplicates(tmp_path):
    with setup(tmp_path) as client:
        found=client.post(BASE+'/search',json={'productName':'大窑汽水'})
        assert found.status_code==200,found.text
        body=found.json(); assert len(body['candidates'])==2
        assert client.get(body['candidates'][0]['previewUrl']).headers['content-type']=='image/png'
        request={'searchId':body['searchId'],'candidateIds':[x['id'] for x in body['candidates']], 'confirmed':True}
        result=client.post(BASE+'/import',json=request)
        assert result.status_code==200,result.text
        assets=result.json()['workspace']['assets']
        assert len(assets)==2 and all(a['kind']=='image' for a in assets)
        assert assets[0]['source']['pageUrl']=='https://shop.example/product'
        assert assets[0]['source']['productName']=='大窑汽水'
        assert assets[0]['source']['usage']=='user_confirmed'
        assert 'file' not in assets[0]
        assert client.post(BASE+'/import',json=request).json()['importedAssetIds']==result.json()['importedAssetIds']
        assert len(PreproductionStore(tmp_path).load('p1')['assets'])==2
        assert client.post(BASE.replace('/p1/','/p2/')+'/import',json=request).status_code==409

def test_import_requires_confirmation_and_current_search(tmp_path):
    with setup(tmp_path) as client:
        first=client.post(BASE+'/search',json={'productName':'大窑汽水'}).json()
        request={'searchId':first['searchId'],'candidateIds':[first['candidates'][0]['id']],'confirmed':False}
        assert client.post(BASE+'/import',json=request).status_code==422
        client.post(BASE+'/search',json={'productName':'其他汽水'})
        request['confirmed']=True
        assert client.post(BASE+'/import',json=request).status_code==409
        assert PreproductionStore(tmp_path).load('p1')['assets']==[]

def test_failed_image_import_adds_no_partial_assets(tmp_path):
    def fetch(url,limit):
        if url.endswith('/back.png'):return b'not an image','image/png',url
        return (png(),'image/png',url) if url.endswith('.png') else ('<title>大窑汽水</title><img src="/front.png" alt="大窑正面"><img src="/back.png" alt="大窑背面">'.encode(),'text/html',url)
    with setup(tmp_path,fetch=fetch) as client:
        found=client.post(BASE+'/search',json={'productName':'大窑汽水'}).json()
        result=client.post(BASE+'/import',json={'searchId':found['searchId'],'candidateIds':[x['id'] for x in found['candidates']],'confirmed':True})
        assert result.status_code==422
        assert PreproductionStore(tmp_path).load('p1')['assets']==[]
        assert not list((tmp_path/'project-files/p1/preproduction/assets').glob('*'))

def test_empty_search_and_search_failure_are_distinguishable(tmp_path):
    with setup(tmp_path,search=lambda name:[]) as client:
        result=client.post(BASE+'/search',json={'productName':'找不到的商品'})
        assert result.status_code==200 and result.json()['candidates']==[]
    def fail(name):raise OSError('offline')
    with setup(tmp_path,search=fail) as client:
        assert client.post(BASE+'/search',json={'productName':'商品'}).status_code==503


def test_import_and_background_node_completion_preserve_both_updates(tmp_path, monkeypatch):
    from pathlib import Path
    from threading import Event, Thread
    from app.preproduction_api import create_preproduction_router
    from app.product_images_api import create_product_images_router
    state=PreproductionStore(tmp_path).load('p1')
    state['shots']=[{'id':'shot','title':'镜头','duration':3,'prompt':'商品','negativePrompt':'','assetIds':[],
                     'nodes':[{'id':'node','kind':'prompt','input':'','params':{},'status':'pending','artifacts':[]}]}]
    PreproductionStore(tmp_path).save('p1',state)
    started, finish, runner_finished, writing, release = (Event() for _ in range(5))
    class Queue:
        handler=None
        def submit(self,kind,pid,handler):self.handler=handler;return True
    queue=Queue()
    def runner(kind,source,path,params,**kwargs):
        started.set();assert finish.wait(5)
        (path/'prompt.txt').write_text('商品镜头')
        runner_finished.set();return ['prompt.txt']
    original=Path.write_bytes
    def write(path,data):
        if path.parent.name=='assets':
            writing.set();assert release.wait(5)
        return original(path,data)
    monkeypatch.setattr(Path,'write_bytes',write)
    app=FastAPI(); lock=RLock(); project=lambda pid:{}
    app.include_router(create_preproduction_router(tmp_path,project,queue,source_lock=lock,runner=runner))
    app.include_router(create_product_images_router(tmp_path,project,source_lock=lock,
        search=lambda name:[{'url':'https://shop.example/product'}],
        fetch=lambda url,limit:(png(),'image/png',url) if url.endswith('.png') else (b'<img src="/front.png" alt="product">','text/html',url)))
    with TestClient(app) as client:
        found=client.post(BASE+'/search',json={'productName':'商品'}).json()
        run=client.post('/api/projects/p1/preproduction/shots/shot/nodes/node/run',json={'revision':0})
        assert run.status_code==202,run.text
        worker=Thread(target=lambda:queue.handler('p1'));worker.start();assert started.wait(3)
        responses=[]
        importer=Thread(target=lambda:responses.append(client.post(BASE+'/import',json={'searchId':found['searchId'],'candidateIds':[found['candidates'][0]['id']],'confirmed':True})))
        importer.start();assert writing.wait(3);finish.set();assert runner_finished.wait(3)
        worker.join(timeout=1)
        release.set();importer.join(timeout=3);worker.join(timeout=3)
        assert responses[0].status_code==200
        latest=PreproductionStore(tmp_path).load('p1')
        assert len(latest['assets'])==1
        assert latest['shots'][0]['nodes'][0]['status']=='completed'
        assert latest['shots'][0]['nodes'][0]['artifacts']


def test_reimport_repairs_missing_file_without_changing_asset_id(tmp_path):
    with setup(tmp_path) as client:
        found=client.post(BASE+'/search',json={'productName':'商品'}).json()
        request={'searchId':found['searchId'],'candidateIds':[found['candidates'][0]['id']],'confirmed':True}
        first=client.post(BASE+'/import',json=request).json()
        store=PreproductionStore(tmp_path);state=store.load('p1');asset=state['assets'][0]
        store.path('p1','assets',asset['file']).unlink()
        state['assets'][0]['name']='客户修改名称';store.save('p1',state)
        second=client.post(BASE+'/import',json=request)
        assert second.status_code==200
        latest=store.load('p1')['assets']
        assert len(latest)==1 and latest[0]['id']==first['importedAssetIds'][0]
        assert latest[0]['name']=='客户修改名称'
        assert store.path('p1','assets',latest[0]['file']).is_file()
