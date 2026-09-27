"""Read-only HTTP projection for the content-production workflow."""

from contextlib import nullcontext
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .aigc_content_api import load_aigc_content_state
from .batch_editing_api import BatchStore
from .content_workflow import project_content_workflow
from .project_assets import ProjectAssets


def create_content_workflow_router(data_dir, get_project, source_lock=None) -> APIRouter:
    root = Path(data_dir)
    batches = BatchStore(root)
    project_assets = ProjectAssets(root)
    router = APIRouter(prefix="/api/projects/{project_id}/content-workflow")

    @router.get("")
    def workflow(project_id: str):
        get_project(project_id)
        try:
            with source_lock or nullcontext():
                aigc_state = load_aigc_content_state(root, project_id)
                batch_state = batches.load(project_id)
                visual_asset_ids = {
                    asset["id"]
                    for asset in project_assets.records(project_id)
                    if asset.get("kind") in ("image", "video")
                }
                return project_content_workflow(aigc_state, batch_state, visual_asset_ids)
        except HTTPException:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(status_code=503, detail={
                "code": "content_workflow_storage_invalid",
                "message": "内容制作进度无法读取，请检查项目数据。",
            }) from None

    return router
