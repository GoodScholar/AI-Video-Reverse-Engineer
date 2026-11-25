"""Current boundary for final AI video generation submissions."""

from fastapi import HTTPException


FINAL_GENERATION_DISABLED = {
    "code": "final_generation_disabled",
    "message": "当前工作台只准备素材与方案，请导出后在外部工具执行视频生成。",
}


def reject_final_generation() -> None:
    raise HTTPException(status_code=410, detail=FINAL_GENERATION_DISABLED)
