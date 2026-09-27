"""Project-scoped discovery and all-or-nothing image import into existing assets."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .preproduction import PreproductionStore
from .preproduction_api import _public_state, _stored_file_available
from .product_images import IMAGE_LIMIT, discover_images, safe_fetch, search_products


class Search(BaseModel):
    model_config = ConfigDict(extra='forbid')
    productName: str = Field(min_length=1, max_length=200)


class Import(BaseModel):
    model_config = ConfigDict(extra='forbid')
    searchId: str
    candidateIds: list[str] = Field(min_length=1, max_length=4)
    confirmed: bool


def image_info(data):
    with Image.open(io.BytesIO(data)) as image:
        if min(image.size) < 160 or image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 40_000_000 or max(image.size) > 12000:
            raise ValueError('图片格式或尺寸不支持')
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.width, image.height, {'JPEG': ('jpg', 'image/jpeg'), 'PNG': ('png', 'image/png'), 'WEBP': ('webp', 'image/webp')}[image.format]


def create_product_images_router(data_dir, get_project, *, source_lock=None, search=search_products, fetch=safe_fetch):
    router = APIRouter(prefix='/api/projects/{project_id}/preproduction/product-images')
    store = PreproductionStore(data_dir)
    lock = source_lock or RLock()
    searches = {}  # Ephemeral searches are deliberately invalidated on service restart.

    def project(project_id):
        if get_project(project_id) is None:
            raise HTTPException(404, '项目不存在')

    def selection(project_id, search_id, ids):
        current = searches.get(project_id)
        if not current or current['searchId'] != search_id:
            raise HTTPException(409, '检索已更新，请重新选择商品图片')
        choices = {item['id']: item for item in current['candidates']}
        if any(item not in choices for item in ids):
            raise HTTPException(422, '图片不属于当前检索')
        return current, [choices[item] for item in dict.fromkeys(ids)]

    @router.post('/search')
    def find(project_id: str, request: Search):
        project(project_id)
        name = request.productName.strip()
        if not name:
            raise HTTPException(422, '请填写商品名称')
        search_id = str(uuid4())
        # Register before network work so older concurrent searches cannot supersede newer ones.
        with lock:
            searches[project_id] = {'searchId': search_id, 'productName': name, 'candidates': []}
        try:
            candidates = discover_images(search(name), fetch, name)
        except Exception as error:
            raise HTTPException(503, '网络检索暂时不可用，请重试或上传商品图片') from error
        for item in candidates:
            item['id'] = str(uuid4())
            item['previewUrl'] = f'/api/projects/{project_id}/preproduction/product-images/preview/{item["id"]}'
        result = {'searchId': search_id, 'productName': name, 'candidates': candidates}
        with lock:
            if searches[project_id]['searchId'] != search_id:
                raise HTTPException(409, '检索已更新，请使用最新结果')
            searches[project_id] = result
        return result

    @router.get('/preview/{candidate_id}')
    def preview(project_id: str, candidate_id: str):
        project(project_id)
        with lock:
            current = searches.get(project_id)
            if not current:
                raise HTTPException(404, '图片检索已失效')
            _, candidates = selection(project_id, current['searchId'], [candidate_id])
        try:
            data, _, _ = fetch(candidates[0]['imageUrl'], IMAGE_LIMIT)
            _, _, (_, mime) = image_info(data)
        except Exception as error:
            raise HTTPException(422, '图片暂时无法预览') from error
        return Response(data, media_type=mime, headers={'Cache-Control': 'private, max-age=300'})

    @router.post('/import')
    def import_images(project_id: str, request: Import):
        project(project_id)
        if not request.confirmed:
            raise HTTPException(422, '请先核对图片与来源')
        with lock:
            current, candidates = selection(project_id, request.searchId, request.candidateIds)
        downloaded = []
        try:
            for candidate in candidates:
                data, _, final_url = fetch(candidate['imageUrl'], IMAGE_LIMIT)
                width, height, (suffix, _) = image_info(data)
                downloaded.append((candidate, data, final_url, width, height, suffix))
        except Exception as error:
            raise HTTPException(422, '商品图片下载或校验失败，未导入任何素材') from error
        created = []
        ids = []
        with lock:
            selection(project_id, request.searchId, request.candidateIds)
            state = store.load(project_id)
            try:
                for candidate, data, final_url, width, height, suffix in downloaded:
                    existing = next((item for item in state['assets'] if item.get('source', {}).get('imageUrl') == candidate['imageUrl']), None)
                    if existing and _stored_file_available(store, project_id, "assets", existing.get("file")):
                        ids.append(existing['id'])
                        continue
                    asset_id = existing["id"] if existing else str(uuid4())
                    filename = (str(uuid4()) if existing else asset_id) + '.' + suffix
                    path = store.path(project_id, 'assets', filename)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    created.append(path)
                    path.write_bytes(data)
                    asset = {'id': asset_id, 'name': candidate['title'], 'kind': 'image', 'role': 'reference',
                        'file': filename, 'width': width, 'height': height, 'available': True,
                        'notes': '网络商品图片；请核对规格与使用范围', 'source': {'pageUrl': candidate['pageUrl'],
                        'imageUrl': candidate['imageUrl'], 'downloadUrl': final_url, 'productName': current['productName'],
                        'retrievedAt': datetime.now(timezone.utc).isoformat(), 'usage': 'user_confirmed'}}
                    if existing:
                        asset.update({key: existing[key] for key in ('name', 'notes', 'role') if key in existing})
                        existing.update(asset)
                    else:
                        state['assets'].append(asset)
                    ids.append(asset_id)
                if created:
                    state['revision'] += 1
                    store.save(project_id, state)
            except Exception:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
        return {'workspace': _public_state(project_id, state), 'importedAssetIds': ids}

    return router
