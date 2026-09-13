import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .analysis_prompt import PROMPT_VERSION
from .semantic_analysis import SemanticAnalysis, SemanticAnalysisError
from .reference_video import validate_storage_id


SCHEMA_VERSION = 1


def semantic_analysis_checkpoint_path(
    data_dir: Path, project_id: str, analysis_id: str,
) -> Path:
    try:
        validate_storage_id(project_id)
        validate_storage_id(analysis_id)
    except ValueError as error:
        raise OSError("语义分析检查点路径无效") from error
    root = data_dir.resolve()
    candidate = root / "project-files" / project_id / "semantic-analysis" / f"{analysis_id}.json"
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise OSError("语义分析检查点路径无效") from error
    return candidate


def write_semantic_analysis_checkpoint(
    data_dir: Path, project_id: str, task: SemanticAnalysis,
) -> None:
    """Atomically save only a completed, fully bound analysis result."""

    if task.status != "completed" or task.result is None:
        raise ValueError("只能保存已完成的语义分析检查点")
    path = semantic_analysis_checkpoint_path(data_dir, project_id, task.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        dir=path.parent, prefix=f".{task.id}-", suffix=".part", text=True,
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(task.model_dump(mode="json"), file, ensure_ascii=False, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def completed_checkpoint_matches(
    data_dir: Path, project_id: str, task: SemanticAnalysis,
) -> bool:
    if task.status != "completed" or task.result is None:
        return False
    path = semantic_analysis_checkpoint_path(data_dir, project_id, task.id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        checkpoint = SemanticAnalysis.model_validate(payload)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    return checkpoint.model_dump(mode="json") == task.model_dump(mode="json")


def load_completed_checkpoint_for_recovery(
    data_dir: Path, project_id: str, task: SemanticAnalysis,
) -> Optional[SemanticAnalysis]:
    """Load a completed checkpoint only when it still names this exact task."""

    path = semantic_analysis_checkpoint_path(data_dir, project_id, task.id)
    try:
        checkpoint = SemanticAnalysis.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if checkpoint.status != "completed" or checkpoint.result is None:
        return None
    identity = (
        "id", "sourceReferenceMediaId", "sourcePreprocessingId", "provider", "model",
        "promptVersion", "schemaVersion",
    )
    if any(getattr(checkpoint, field) != getattr(task, field) for field in identity):
        return None
    return checkpoint


def discard_semantic_analysis_checkpoint(
    data_dir: Path, project_id: str, task: SemanticAnalysis,
) -> None:
    semantic_analysis_checkpoint_path(data_dir, project_id, task.id).unlink(missing_ok=True)


def new_semantic_analysis(
    *,
    reference_media_id: str,
    preprocessing_id: str,
    provider: str,
    model: str,
    now: datetime,
) -> SemanticAnalysis:
    timestamp = now.isoformat()
    return SemanticAnalysis(
        id=str(uuid4()),
        sourceReferenceMediaId=reference_media_id,
        sourcePreprocessingId=preprocessing_id,
        provider=provider,
        model=model,
        promptVersion=PROMPT_VERSION,
        schemaVersion=SCHEMA_VERSION,
        status="queued",
        createdAt=timestamp,
        updatedAt=timestamp,
    )


def interrupted_semantic_analysis(
    task: SemanticAnalysis,
    now: datetime,
) -> SemanticAnalysis:
    updated = task.model_copy(deep=True)
    updated.status = "failed"
    updated.updatedAt = now.isoformat()
    updated.completedAt = None
    updated.error = SemanticAnalysisError(
        code="semantic_analysis_interrupted",
        message="本地服务曾退出，语义分析已中断，请重新开始。",
        retryable=True,
    )
    return updated
