"""Non-project configuration boundary for the Bilibili session stored in OS keyring."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .analysis_service_secrets import SecureStorageUnavailable
from .bilibili_import import SystemBilibiliCookieStore


class BilibiliSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookie: str = Field(min_length=1, max_length=1_000_000)


def create_bilibili_session_router(store=None) -> APIRouter:
    """Expose only configured state; cookie content never leaves request handling."""
    store = store or SystemBilibiliCookieStore()
    router = APIRouter(prefix="/api/source-sessions/bilibili")

    def unavailable(error: Exception):
        raise HTTPException(status_code=503, detail={"code": "bilibili_session_storage_unavailable", "message": "B站会话安全存储不可用。"}) from error

    @router.get("")
    def state():
        try:
            return {"configured": bool(store.get())}
        except (SecureStorageUnavailable, OSError):
            unavailable(SecureStorageUnavailable())

    @router.put("", status_code=204)
    def save(body: BilibiliSessionUpdate):
        try:
            store.set(body.cookie)
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": "bilibili_session_invalid", "message": "B站会话格式无效。"}) from error
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
