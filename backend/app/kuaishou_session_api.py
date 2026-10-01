"""Non-project OS-keyring configuration boundary for Kuaishou sessions."""
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from .analysis_service_secrets import SecureStorageUnavailable
from .kuaishou_import import SystemKuaishouCookieStore

class KuaishouSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookie: str = Field(min_length=1, max_length=1_000_000)

def create_kuaishou_session_router(store=None) -> APIRouter:
    store, router = store or SystemKuaishouCookieStore(), APIRouter(prefix="/api/source-sessions/kuaishou")
    def unavailable(error):
        raise HTTPException(503, detail={"code": "kuaishou_session_storage_unavailable", "message": "快手会话安全存储不可用。"}) from error
    @router.get("")
    def state():
        try: return {"configured": bool(store.get())}
        except (SecureStorageUnavailable, OSError) as error: unavailable(error)
    @router.put("", status_code=204)
    def save(body: KuaishouSessionUpdate):
        try: store.set(body.cookie)
        except ValueError as error: raise HTTPException(422, detail={"code": "kuaishou_session_invalid", "message": "快手会话格式无效。"}) from error
        except (SecureStorageUnavailable, OSError) as error: unavailable(error)
        return Response(status_code=204)
    @router.delete("", status_code=204)
    def delete():
        try: store.delete()
        except (SecureStorageUnavailable, OSError) as error: unavailable(error)
        return Response(status_code=204)
    return router
