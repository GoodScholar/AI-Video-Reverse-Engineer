import io
import json
import zipfile

from fastapi.testclient import TestClient
from app.main import create_app


def create(client):
    return client.post('/api/projects', json={'name': '备份验证'}).json()['id']


def test_roundtrip_project_files_and_new_identity(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        base = tmp_path / 'project-files' / pid
        (base / 'notes').mkdir(parents=True)
        (base / 'notes' / 'test.txt').write_text('完整素材')
        state = client.get(f'/api/projects/{pid}/preproduction').json()
        state['brief']['theme'] = '恢复测试'
        assert client.put(f'/api/projects/{pid}/preproduction', json={k: state[k] for k in ('revision','brief','shots')}).status_code == 200
        exported = client.post(f'/api/projects/{pid}/backup', json={})
        assert exported.status_code == 200
        restored = client.post('/api/project-backups/restore', content=exported.content, headers={'Content-Type':'application/zip'})
        assert restored.status_code == 201, restored.text
        new_id = restored.json()['id']
        assert new_id != pid
        assert (tmp_path / 'project-files' / new_id / 'notes' / 'test.txt').read_text() == '完整素材'
        assert client.get(f'/api/projects/{new_id}/preproduction').json()['brief']['theme'] == '恢复测试'
        assert len(client.get('/api/projects').json()) == 2


def test_tampered_and_traversal_archives_create_no_project(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        base = tmp_path / 'project-files' / pid
        base.mkdir(parents=True)
        (base / 'sample.txt').write_text('original')
        exported = client.post(f'/api/projects/{pid}/backup', json={})
        assert exported.status_code == 200
        for name in ['files/sample.txt', '../escape']:
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(exported.content)) as source, zipfile.ZipFile(output,'w') as dest:
                for info in source.infolist():
                    dest.writestr(info, b'corrupt' if info.filename == name else source.read(info))
                if name.startswith('..'): dest.writestr(name,b'bad')
            r = client.post('/api/project-backups/restore', content=output.getvalue(), headers={'Content-Type':'application/zip'})
            assert r.status_code == 422, r.text
            assert len(client.get('/api/projects').json()) == 1
        assert not (tmp_path / 'escape').exists()


def test_export_refuses_active_state_and_symlink(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        root = tmp_path / 'project-files' / pid
        root.mkdir(parents=True)
        state = root / 'job.json'
        state.write_text(json.dumps({'status':'running'}))
        assert client.post(f'/api/projects/{pid}/backup', json={}).status_code == 409
        state.unlink()
        (root/'unsafe').symlink_to(tmp_path/'projects.json')
        assert client.post(f'/api/projects/{pid}/backup', json={}).status_code == 422


def test_backup_routes_require_trusted_browser_intent(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        for path in [f'/api/projects/{pid}/backup','/api/project-backups/restore']:
            assert client.post(path,headers={'Origin':'https://untrusted.example'}).status_code == 403


def test_user_json_and_prompts_are_not_rewritten(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        source = tmp_path / 'project-files' / pid
        source.mkdir(parents=True)
        raw = json.dumps({'url': f'/api/projects/{pid}/example', 'text': pid}, indent=4).encode()
        (source/'user.json').write_bytes(raw)
        state = client.get(f'/api/projects/{pid}/preproduction').json()
        state['brief']['theme'] = pid
        client.put(f'/api/projects/{pid}/preproduction', json={k: state[k] for k in ('revision','brief','shots')})
        exported = client.post(f'/api/projects/{pid}/backup', json={})
        restored = client.post('/api/project-backups/restore', content=exported.content, headers={'Content-Type':'application/zip'})
        assert restored.status_code == 201, restored.text
        new_id = restored.json()['id']
        assert client.get(f'/api/projects/{new_id}/preproduction').json()['brief']['theme'] == pid
        assert (tmp_path/'project-files'/new_id/'user.json').read_bytes() == raw


def test_export_refuses_submitting_generation(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        path = tmp_path/'project-files'/pid/'character-motion'
        path.mkdir(parents=True)
        (path/'state.json').write_text(json.dumps({'runs':[{'status':'submitting'}]}))
        assert client.post(f'/api/projects/{pid}/backup', json={}).status_code == 409


def test_media_candidates_timeline_and_artifact_urls_roundtrip(tmp_path):
    import subprocess
    import time
    media = tmp_path/'sample.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=64x64:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(media)], check=True)
    with TestClient(create_app(data_dir=tmp_path/'data')) as client:
        pid = create(client)
        prep_url = f'/api/projects/{pid}/preproduction'
        uploaded = client.put(f'/api/projects/{pid}/reference-media', files={'file':('sample.mp4',media.read_bytes(),'video/mp4')})
        assert uploaded.status_code == 200, uploaded.text
        state = client.get(prep_url).json()
        state = client.put(prep_url,json={'revision':state['revision'],'brief':state['brief'],'shots':[{'id':'s1','title':'保留镜头','duration':1,'prompt':'不改动提示词','assetIds':[],'nodes':[]}]}).json()
        for _ in range(2):
            r = client.post(prep_url+f"/assets?role=motion&resultForShot=s1&revision={state['revision']}",files={'file':('result.mp4',media.read_bytes(),'video/mp4')})
            assert r.status_code == 200, r.text
            state = r.json()
        timeline_url = f'/api/projects/{pid}/timeline'
        result = client.post(timeline_url+'/import-shot-results',json={'revision':0,'preproductionRevision':state['revision']})
        assert result.status_code == 200, result.text
        timeline = result.json()
        # Include a real rendered output to verify restored history and downloads.
        rendered = client.post(timeline_url+'/runs',json={'revision':timeline['revision'],'format':'mp4'})
        assert rendered.status_code == 202, rendered.text
        for _ in range(200):
            timeline = client.get(timeline_url).json()
            if timeline['runs'][-1]['status'] not in ('queued','running'): break
            time.sleep(.03)
        assert timeline['runs'][-1]['status'] == 'completed', timeline['runs'][-1]
        exported = client.post(f'/api/projects/{pid}/backup',json={})
        assert exported.status_code == 200, exported.text
        restored = client.post('/api/project-backups/restore',content=exported.content,headers={'Content-Type':'application/zip'})
        assert restored.status_code == 201, restored.text
        new_id = restored.json()['id']
        restored_prep = client.get(f'/api/projects/{new_id}/preproduction').json()
        assert restored_prep['shots'] == state['shots']
        assert len(restored_prep['shots'][0]['resultVersions']) == 2
        for asset in restored_prep['assets']:
            assert client.get(asset['url']).content == media.read_bytes()
        restored_timeline = client.get(f'/api/projects/{new_id}/timeline').json()
        assert restored_timeline['tracks'] == timeline['tracks']
        assert restored_timeline['settings'] == timeline['settings']
        assert client.get(restored_timeline['runs'][0]['url']).content == client.get(timeline['runs'][0]['url']).content
        assert client.get(f'/api/projects/{new_id}/reference-video/content').content == media.read_bytes()


def test_managed_artifact_reference_rebound_without_rewriting_node_text(tmp_path):
    from app.project_backup import rebind_managed_json
    value = {'shots':[{'nodes':[{'params':{'text':'/api/projects/old/example'},'artifacts':[{'url':'/api/projects/old/preproduction/artifacts/a/b/x.png'}]}]}]}
    assert rebind_managed_json('preproduction/state.json',value,'old','new','/tmp/project-files/old',tmp_path)
    node = value['shots'][0]['nodes'][0]
    assert node['params']['text'] == '/api/projects/old/example'
    assert node['artifacts'][0]['url'] == '/api/projects/new/preproduction/artifacts/a/b/x.png'


def test_duplicate_symlink_and_unknown_version_rejected(tmp_path):
    import stat
    import warnings
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        exported = client.post(f'/api/projects/{pid}/backup',json={})
        assert exported.status_code == 200
        for kind in ['duplicate','symlink','version']:
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(exported.content)) as source, zipfile.ZipFile(output,'w') as target:
                manifest = json.loads(source.read('manifest.json'))
                if kind == 'version': manifest['version'] = 999
                target.writestr('manifest.json',json.dumps(manifest))
                if kind == 'duplicate':
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        target.writestr('manifest.json',json.dumps(manifest))
                if kind == 'symlink':
                    info = zipfile.ZipInfo('files/link'); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    target.writestr(info,'/etc/passwd')
            assert client.post('/api/project-backups/restore',content=output.getvalue(),headers={'Content-Type':'application/zip'}).status_code == 422
            assert len(client.get('/api/projects').json()) == 1


def test_failed_registration_rolls_back_files(tmp_path, monkeypatch):
    import app.main as main
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        base = tmp_path/'project-files'/pid
        base.mkdir(parents=True)
        (base/'asset.txt').write_text('preserved')
        exported = client.post(f'/api/projects/{pid}/backup',json={})
        def fail(*args): raise OSError('disk full')
        monkeypatch.setattr(main,'_write_projects',fail)
        response = client.post('/api/project-backups/restore',content=exported.content,headers={'Content-Type':'application/zip'})
        assert response.status_code == 422
        assert [p.name for p in (tmp_path/'project-files').iterdir()] == [pid]
        assert not list(tmp_path.glob('.restore-*'))
        assert len(client.get('/api/projects').json()) == 1


def test_cancelled_render_worker_must_exit_before_backup(tmp_path, monkeypatch):
    from threading import Event
    from app import timeline_render
    from test_preproduction_api import make_video
    entered, release = Event(), Event()
    def rendering(snapshot, sources, output, **kwargs):
        output.write_bytes(b'partial')
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(timeline_render, 'render_timeline', rendering)
    media = tmp_path / 'input.mp4'; make_video(media)
    with TestClient(create_app(data_dir=tmp_path / 'data')) as client:
        pid = create(client)
        prep = f'/api/projects/{pid}/preproduction'
        asset = client.post(prep + '/assets?role=motion', files={'file': ('input.mp4', media.read_bytes(), 'video/mp4')}).json()['assets'][0]
        from test_timeline_api import _workspace
        draft = _workspace(); draft['tracks'][0]['clips'][0]['assetId'] = asset['id']
        timeline = f'/api/projects/{pid}/timeline'
        client.put(timeline, json=draft)
        run = client.post(timeline + '/runs', json={'revision': 1, 'format': 'mp4'}).json()['runs'][0]
        try:
            assert entered.wait(3)
            client.post(timeline + '/runs/' + run['id'] + '/cancel', json={})
            response = client.post(f'/api/projects/{pid}/backup', json={})
            assert response.status_code == 409, response.text[:200]
        finally:
            release.set()


def test_restore_rejects_invalid_history_before_registering(tmp_path):
    import hashlib
    with TestClient(create_app(data_dir=tmp_path)) as client:
        pid = create(client)
        client.get(f'/api/projects/{pid}/timeline')
        from app.timeline import TimelineStore, default_workspace
        TimelineStore(tmp_path).save(pid, default_workspace())
        archive = client.post(f'/api/projects/{pid}/backup', json={}).content
        for runs in ([None], [{'id': 'r', 'status': 'completed', 'format': 'mp4', 'revision': 0, 'snapshot': {'settings': {'width': 1280, 'height': 720, 'fps': 30}, 'tracks': []}, 'sources': {}, 'output': 'missing.mp4'}]):
            changed = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(changed, 'w') as dest:
                manifest = json.loads(source.read('manifest.json'))
                for name in source.namelist():
                    if name == 'manifest.json': continue
                    raw = source.read(name)
                    if name == 'files/timeline/state.json':
                        value = json.loads(raw); value['runs'] = runs
                        raw = json.dumps(value).encode()
                        manifest['files']['timeline/state.json'] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                    dest.writestr(name, raw)
                dest.writestr('manifest.json', json.dumps(manifest))
            result = client.post('/api/project-backups/restore', content=changed.getvalue(), headers={'Content-Type': 'application/zip'})
            assert result.status_code == 422, result.text
            assert len(client.get('/api/projects').json()) == 1


def test_damaged_compression_is_rejected_without_creating_a_project(tmp_path):
    """Bad compressed streams must be input errors, not unhandled server errors."""
    import struct

    with TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False) as client:
        create(client)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps({'format': 'aivre-project', 'version': 1}))
        original = output.getvalue()
        unsupported = bytearray(original)
        struct.pack_into('<H', unsupported, 8, 99)
        central = unsupported.index(b'PK\x01\x02')
        struct.pack_into('<H', unsupported, central + 10, 99)
        broken = bytearray(original)
        name_length, extra_length = struct.unpack_from('<HH', broken, 26)
        broken[30 + name_length + extra_length] = 7  # Invalid DEFLATE block type.
        for payload in (unsupported, broken):
            response = client.post('/api/project-backups/restore', content=bytes(payload), headers={'Content-Type': 'application/zip'})
            assert response.status_code == 422, response.text
            assert len(client.get('/api/projects').json()) == 1
            assert not list(tmp_path.glob('.restore-*'))
