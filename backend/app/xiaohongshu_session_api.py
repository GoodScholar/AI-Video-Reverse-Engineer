"""Non-project configuration boundary for a Xiaohongshu session in the OS keyring."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .analysis_service_secrets import SecureStorageUnavailable
from .xiaohongshu_import import SystemXiaohongshuCookieStore


class XiaohongshuSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookie: str = Field(min_length=1, max_length=1_000_000)


def create_xiaohongshu_session_router(store=None) -> APIRouter:
    """Only configured state crosses this boundary; the cookie never does."""
    store = store or SystemXiaohongshuCookieStore()
    router = APIRouter(prefix="/api/source-sessions/xiaohongshu")

    def unavailable(error: Exception):
        raise HTTPException(status_code=503, detail={"code": "xiaohongshu_session_storage_unavailable", "message": "小红书会话安全存储不可用。"}) from error

    @router.get("")
    def state():
        try:
            return {"configured": bool(store.get())}
        except (SecureStorageUnavailable, OSError):
            unavailable(SecureStorageUnavailable())

    @router.put("", status_code=204)
    def save(body: XiaohongshuSessionUpdate):
        try:
            store.set(body.cookie)
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": "xiaohongshu_session_invalid", "message": "小红书会话格式无效。"}) from error
        except (SecureStorageUnavailable, OSError) as error:
            unavailable(error)
        return Response(status_code=204)

    @router.delete("", status_code=204)
    def delete():
        try:
            store.delete()
        except (SecureStorageUnavailable, OSError) as error:
            unavailable(error)
        return Response(status_code=204)

    return router
