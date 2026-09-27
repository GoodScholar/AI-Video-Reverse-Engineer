"""Versioned local project archives; no code or jobs are executed on restore."""
import hashlib
import json
import lzma
import math
import os
import shutil
import stat
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .preproduction import PreproductionStore, safe_child
from .project_assets import ProjectAssets
from .timeline import TimelineStore, validate_workspace

MAX_BYTES = 4 * 1024**3
MAX_FILES = 10000
MAX_JSON = 16 * 1024**2


def fail(message, status=422):
    raise HTTPException(status_code=status, detail=message)


def read_json(raw):
    if len(raw) > MAX_JSON:
        fail("备份中的 JSON 文件过大。")
    try:
        return json.loads(raw, parse_constant=lambda _: fail("备份包含无效数值。"))
    except (ValueError, UnicodeError, RecursionError):
        fail("备份中的 JSON 无法读取。")


def check_idle(value):
    if isinstance(value, dict):
        if value.get("status") in ("queued", "running", "cancelling", "cancel_requested", "submitting", "unknown"):
            fail("项目仍有活动任务，请完成或取消后再备份。", 409)
        for item in value.values():
            check_idle(item)
    elif isinstance(value, list):
        for item in value:
            check_idle(item)


def safe_name(name):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or '\\' in name or '\x00' in name or ':' in name or any(p in ('', '.', '..') for p in name.split('/')):
        fail("备份包含不安全路径。")
    return parts


def inventory(root):
    found = {}
    if not root.exists():
        return found
    if root.is_symlink():
        fail("项目目录不能是符号链接。")
    for path in root.rglob('*'):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            fail("项目中存在符号链接或非常规文件，无法备份。")
        name = path.relative_to(root).as_posix()
        safe_name(name)
        found[name] = (info.st_size, info.st_mtime_ns, info.st_ino)
        if len(found) > MAX_FILES:
            fail("项目文件数量超过备份上限。")
    if sum(v[0] for v in found.values()) > MAX_BYTES:
        fail("项目文件总量超过 4 GiB。", 413)
    return found


def rebind_managed_json(name, value, old_id, new_id, old_root, new_root):
    # Only fields owned by these schemas are references. Prompts and arbitrary
    # JSON files must remain byte-for-byte intact.
    changed = False
    if name == 'preproduction/state.json':
        for shot in value.get('shots', []):
            for node in shot.get('nodes', []):
                for artifact in node.get('artifacts', []):
                    url = artifact.get('url', '')
                    prefix = f'/api/projects/{old_id}/'
                    if isinstance(url, str) and url.startswith(prefix):
                        artifact['url'] = f'/api/projects/{new_id}/' + url[len(prefix):]
                        changed = True
    parts = PurePosixPath(name).parts
    if len(parts) == 3 and parts[0] == 'toolkit' and parts[2] == 'request.json':
        for key in ('source', 'directory'):
            path = value.get(key)
            prefix = old_root.rstrip('/') + '/'
            if isinstance(path, str) and path.startswith(prefix):
                value[key] = str(new_root) + '/' + path[len(prefix):]
                changed = True
    return changed


def validate_core(data_root, project):
    pid = project.id
    prep_store = PreproductionStore(data_root)
    project_assets = ProjectAssets(data_root)
    prep = prep_store.load(pid)
    from .preproduction_api import SaveWorkspace, ShotUpdate, NodeUpdate
    shots = []
    for shot in prep['shots']:
        editable = {k: v for k, v in shot.items() if k in ShotUpdate.model_fields}
        editable['nodes'] = [{k: v for k, v in node.items() if k in NodeUpdate.model_fields} for node in shot['nodes']]
        shots.append(editable)
    SaveWorkspace.model_validate({'revision': prep['revision'], 'brief': prep['brief'], 'shots': shots})
    records = project_assets.records(pid)
    assets = project_assets.index(pid)
    if len(assets) != len(records):
        fail('项目素材标识重复。')
    for asset in assets.values():
        if any(not isinstance(asset.get(k), str) or not asset[k] for k in ('id', 'name', 'file', 'role')) or asset.get('kind') not in ('video', 'audio', 'image'):
            fail('项目素材元数据无效。')
        if asset['kind'] in ('video', 'audio') and (not isinstance(asset.get('duration'), (int, float)) or not math.isfinite(asset['duration']) or asset['duration'] <= 0):
            fail('项目素材时长无效。')
        if not project_assets.available(pid, asset):
            fail("备份缺少项目素材文件。")
    for shot in prep['shots']:
        if not isinstance(shot, dict) or not isinstance(shot.get('nodes'), list):
            fail("镜头方案结构无效。")
        ids = list(shot.get('assetIds', [])) + [v['assetId'] for v in shot.get('_resultVersions', [])]
        if shot.get('resultAssetId'):
            ids.append(shot['resultAssetId'])
        if any(asset_id not in assets for asset_id in ids):
            fail("镜头方案引用了缺失素材。")
        for node in shot['nodes']:
            for artifact in node.get('artifacts', []):
                if not prep_store.path(pid, 'artifacts', shot['id'], node['id'], artifact['name']).is_file():
                    fail("备份缺少镜头步骤产物。")
    timeline = TimelineStore(data_root).load(pid)
    validate_workspace({k: timeline[k] for k in ('revision', 'settings', 'tracks')}, assets)
    validate_timeline_history(TimelineStore(data_root), pid, timeline, assets)
    if project.referenceMedia:
        from .reference_media_storage import resolve_reference_media_path
        if not resolve_reference_media_path(data_root, pid, project.referenceMedia).is_file():
            fail("备份缺少参考素材。")


def validate_timeline_history(store, project_id, timeline, assets):
    from .reference_video import validate_storage_id
    seen = set()
    for run in timeline['runs']:
        if not isinstance(run, dict):
            fail('备份中的渲染历史结构无效。')
        run_id = validate_storage_id(run.get('id'))
        if run_id in seen or run.get('status') not in ('completed', 'failed', 'cancelled') or run.get('format') not in ('preview', 'mp4', 'wav'):
            fail('备份中的渲染历史标识或状态无效。')
        seen.add(run_id)
        if type(run.get('revision')) is not int or not 0 <= run['revision'] <= timeline['revision']:
            fail('备份中的渲染历史版本无效。')
        if run.get('error') is not None and not isinstance(run['error'], str):
            fail('备份中的渲染错误信息无效。')
        snapshot = run.get('snapshot')
        if not isinstance(snapshot, dict) or set(snapshot) != {'settings', 'tracks'}:
            fail('备份中的渲染快照无效。')
        _, tracks = validate_workspace({'revision': run['revision'], **snapshot}, assets)
        used = {clip['assetId'] for track in tracks for clip in track['clips']}
        sources = run.get('sources')
        if not isinstance(sources, dict) or set(sources) != used:
            fail('备份中的渲染源文件关联无效。')
        for filename in sources.values():
            if not store.path(project_id, 'runs', run_id, 'sources', filename).is_file():
                fail('备份缺少渲染历史源文件。')
        if run['status'] == 'completed':
            expected = run_id + ('.wav' if run['format'] == 'wav' else '.mp4')
            if run.get('output') != expected or not store.path(project_id, 'outputs', expected).is_file():
                fail('备份缺少渲染历史输出文件或关联无效。')


def create_backup_router(data_dir, get_project, parse_project, register_project, lock, *, is_project_active=None):
    root = Path(data_dir).absolute()
    router = APIRouter()

    @router.post('/api/projects/{project_id}/backup')
    def export_project(project_id: str):
        folder = Path(tempfile.mkdtemp(prefix='aivre-backup-'))
        try:
            with lock:
                project = get_project(project_id)
                if is_project_active is not None and is_project_active(project_id):
                    fail("项目处理进程尚未退出，请等待完全停止后再备份。", 409)
                payload = project.model_dump(mode='json')
                check_idle(payload)
                source = safe_child(root, 'project-files', project.id)
                before = inventory(source)
                validate_core(root, project)
                manifest = {'format': 'aivre-project', 'version': 1, 'project': payload,
                            'sourceRoot': str(source), 'files': {}}
                target = folder / 'project.zip'
                with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for name in sorted(before):
                        path = source / name
                        if path.suffix == '.json':
                            if before[name][0] > MAX_JSON:
                                fail('项目中的 JSON 文件过大。')
                            check_idle(read_json(path.read_bytes()))
                        digest = hashlib.sha256()
                        with path.open('rb') as stream, archive.open('files/'+name, 'w', force_zip64=True) as out:
                            while chunk := stream.read(1024**2):
                                digest.update(chunk)
                                out.write(chunk)
                        manifest['files'][name] = {'size': before[name][0], 'sha256': digest.hexdigest()}
                    archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, allow_nan=False))
                if before != inventory(source):
                    fail("项目在备份期间发生变化，请重试。", 409)
                if target.stat().st_size > MAX_BYTES:
                    fail("备份 ZIP 超过 4 GiB。", 413)
            return FileResponse(target, media_type='application/zip', filename=f'project-{project.id}.zip',
                                background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            shutil.rmtree(folder, ignore_errors=True)
            fail("项目文件或状态无效，无法完整备份。")

    def restore_file(archive_path, staging):
        new_id = str(uuid4())
        destination = safe_child(root, 'project-files', new_id)
        staged = staging / 'project-files' / new_id
        staged.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if len(entries) > MAX_FILES + 1 or len(set(names)) != len(names):
                    fail("备份文件重复或数量超限。")
                if sum(entry.file_size for entry in entries) > MAX_BYTES:
                    fail("备份解包总量超过 4 GiB。")
                for entry in entries:
                    safe_name(entry.filename)
                    mode = entry.external_attr >> 16
                    if entry.is_dir() or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)) or entry.flag_bits & 1:
                        fail("备份包含不支持的文件类型。")
                if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > MAX_JSON:
                    fail("缺少有效的项目备份清单。")
                manifest = read_json(archive.read('manifest.json'))
                if manifest.get('format') != 'aivre-project' or manifest.get('version') != 1:
                    fail("不支持此项目备份格式或版本。")
                files = manifest['files']
                if not isinstance(files, dict) or set(names) != {'manifest.json', *('files/'+n for n in files)}:
                    fail("备份文件与清单不一致。")
                original = parse_project(manifest['project'])
                check_idle(manifest['project'])
                old_root = manifest['sourceRoot']
                if not isinstance(old_root, str) or not old_root.endswith('/project-files/'+original.id):
                    fail("备份来源路径无效。")
                for name, expected in files.items():
                    safe_name(name)
                    entry = archive.getinfo('files/'+name)
                    if entry.file_size != expected['size']:
                        fail("备份文件大小不匹配。")
                    target = staged / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.open(entry) as source, target.open('xb') as out:
                        while chunk := source.read(1024**2):
                            digest.update(chunk)
                            out.write(chunk)
                    if digest.hexdigest() != expected['sha256']:
                        fail("备份文件校验失败，文件可能损坏。")
                    if target.suffix == '.json':
                        if entry.file_size > MAX_JSON:
                            fail('备份中的 JSON 文件过大。')
                        value = read_json(target.read_bytes())
                        check_idle(value)
                        if rebind_managed_json(name, value, original.id, new_id, old_root, destination):
                            target.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
                payload = original.model_dump(mode='json')
                payload.update(id=new_id, name=original.name[:94]+'（恢复）', createdAt=datetime.now(timezone.utc).isoformat(), updatedAt=datetime.now(timezone.utc).isoformat())
                restored = parse_project(payload)
                validate_core(staging, restored)
            with lock:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.rename(staged, destination)
                try:
                    register_project(restored)
                except BaseException:
                    shutil.rmtree(destination, ignore_errors=True)
                    raise
            return restored
        except HTTPException:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, EOFError, NotImplementedError, zipfile.BadZipFile, zlib.error, lzma.LZMAError):
            fail("项目备份损坏或内容无效，未创建新项目。")

    @router.post('/api/project-backups/restore', status_code=201)
    async def restore_project(request: Request):
        # Raw ZIP stream is bounded before it reaches any multipart parser.
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=root) as temp:
            staging = Path(temp)
            archive_path = staging / 'upload.zip'
            total = 0
            with archive_path.open('wb') as out:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        fail("备份 ZIP 超过 4 GiB。", 413)
                    out.write(chunk)
            return await run_in_threadpool(restore_file, archive_path, staging)

    return router
