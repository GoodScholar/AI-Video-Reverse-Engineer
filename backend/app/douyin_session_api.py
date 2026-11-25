"""Non-project configuration boundary for a Douyin session in the OS keyring."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .analysis_service_secrets import SecureStorageUnavailable
from .douyin_import import SystemDouyinCookieStore


class DouyinSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookie: str = Field(min_length=1, max_length=1_000_000)


def create_douyin_session_router(store=None) -> APIRouter:
    """Only configured state crosses this boundary; the cookie never does."""
    store = store or SystemDouyinCookieStore()
    router = APIRouter(prefix="/api/source-sessions/douyin")

    def unavailable(error: Exception):
        raise HTTPException(status_code=503, detail={"code": "douyin_session_storage_unavailable", "message": "抖音会话安全存储不可用。"}) from error

    @router.get("")
    def state():
        try:
            return {"configured": bool(store.get())}
        except (SecureStorageUnavailable, OSError):
            unavailable(SecureStorageUnavailable())

    @router.put("", status_code=204)
    def save(body: DouyinSessionUpdate):
        try:
            store.set(body.cookie)
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": "douyin_session_invalid", "message": "抖音会话格式无效。"}) from error
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
