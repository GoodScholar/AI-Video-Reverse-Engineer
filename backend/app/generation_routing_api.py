"""Application-level APIs for user-saved GenerationService routing policies."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .generation_routing import GenerationRoutingConflict, GenerationRoutingPolicyError
from .generation_service import GenerationError, GenerationService


class _Route(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=256)


class _Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    modality: str
    routes: list[_Route] = Field(min_length=1, max_length=16)


class _Save(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    policy: _Policy


def create_generation_routing_router(service: GenerationService) -> APIRouter:
    router = APIRouter(prefix="/api/generation-routing-policies")

    @router.get("")
    def list_policies(modality: str | None = None):
        if modality is not None and modality not in {"text", "image", "video"}:
            raise HTTPException(422, detail={"code": "generation_routing_policy_invalid", "message": "生成路由策略模态无效。"})
        return service.routing_policies.list(modality=modality)

    @router.put("/{policy_id}")
    def save_policy(policy_id: str, body: _Save):
        if body.policy.id != policy_id:
            raise HTTPException(422, detail={"code": "generation_routing_policy_invalid", "message": "路由策略标识不匹配。"})
        policy = body.policy.model_dump()
        try:
            # A route is never accepted merely because a provider connected: its
            # exact modality catalog must be complete and versioned right now.
            for route in policy["routes"]:
                service.capabilities(route["provider"], route["model"], modality=policy["modality"])
            saved = service.routing_policies.save(policy, expected_revision=body.expectedRevision)
            return {"policy": saved}
        except GenerationRoutingConflict:
            raise HTTPException(409, detail={"code": "generation_routing_policy_revision_conflict", "message": "路由策略已更新，请刷新后再保存。"}) from None
        except (GenerationRoutingPolicyError, GenerationError, ValidationError):
            raise HTTPException(422, detail={"code": "generation_routing_policy_invalid", "message": "候选 Provider/模型未完成对应生成能力验证。"}) from None

    return router
