import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from .local_preprocessing import (
    ALGORITHM_VERSION,
    LocalPreprocessing,
    MediaType,
    StageName,
    stage_order_for,
)
from .reference_video import validate_storage_id


VIDEO_STAGE_FILES = {
    "decoding": ("decode.json",),
    "sceneDetection": ("scene-changes.json",),
    "keyframeExtraction": ("contact-sheet.jpg", "keyframes"),
    "motionAnalysis": ("motion.json",),
    "reproducibilityAssessment": ("analysis-proxy.json", "manifest.json"),
}
IMAGE_STAGE_FILES = {
    "imageDecoding": (),
    "imageNormalization": ("normalized.png",),
    "proxyGeneration": ("analysis-proxy.jpg",),
    "reproducibilityAssessment": ("manifest.json",),
}
_STAGE_JSON_FILES = frozenset({
    "decode.json",
    "scene-changes.json",
    "motion.json",
    "analysis-proxy.json",
    "manifest.json",
})
_FRAME_NAME = re.compile(r"frame-\d{4}\.jpg\Z")


class StageValidation(BaseModel):
    preprocessing: LocalPreprocessing
    firstInvalidStage: Optional[StageName]


def _stage_files_for(media_type: MediaType) -> dict[StageName, tuple[str, ...]]:
    return IMAGE_STAGE_FILES if media_type == "image" else VIDEO_STAGE_FILES


def preprocessing_directory(data_dir: Path, project_id: str, preprocessing_id: str) -> Path:
    try:
        validate_storage_id(project_id)
        validate_storage_id(preprocessing_id)
    except ValueError as error:
        raise OSError("本地预处理路径超出数据目录") from error
    root = data_dir.resolve()
    candidate = root / "project-files" / project_id / "local-preprocessing" / preprocessing_id
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise OSError("本地预处理路径超出数据目录") from error
    return candidate


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}-",
        suffix=".part",
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_stage_json(directory: Path, filename: str, payload: dict) -> None:
    if filename not in _STAGE_JSON_FILES:
        raise ValueError("不是允许的阶段 JSON 产物")
    atomic_write_json(_safe_preprocessing_directory(directory) / filename, payload)


def commit_keyframe_stage(workspace: Path, destination: Path, frame_names: list[str]) -> None:
    destination = _safe_preprocessing_directory(destination)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        _validate_keyframe_workspace(workspace, frame_names)
        _discard_stage_artifacts(destination, "keyframeExtraction", "video")
        os.replace(workspace / "keyframes", destination / "keyframes")
        os.replace(workspace / "contact-sheet.jpg", destination / "contact-sheet.jpg")
    except BaseException:
        _discard_stage_artifacts(destination, "keyframeExtraction", "video")
        raise


def inspect_completed_stages(
    preprocessing: LocalPreprocessing,
    directory: Path,
) -> StageValidation:
    directory = _safe_preprocessing_directory(directory)
    updated = preprocessing.model_copy(deep=True)
    first_invalid: Optional[StageName] = None
    for state in updated.stages:
        if state.status != "completed" or not stage_artifacts_are_valid(
            state.name, directory, updated,
        ):
            first_invalid = state.name
            break
    if first_invalid is not None:
        start = stage_order_for(updated.mediaType).index(first_invalid)
        for state in updated.stages[start:]:
            state.status = "pending"
            state.startedAt = None
            state.completedAt = None
    return StageValidation(preprocessing=updated, firstInvalidStage=first_invalid)


def validate_completed_stages(
    preprocessing: LocalPreprocessing,
    directory: Path,
) -> StageValidation:
    validated = inspect_completed_stages(preprocessing, directory)
    if validated.firstInvalidStage is not None:
        reset_stage_artifacts(
            directory, validated.firstInvalidStage, preprocessing.mediaType,
        )
    return validated


def stage_artifacts_are_valid(
    stage: StageName,
    directory: Path,
    preprocessing: LocalPreprocessing,
) -> bool:
    directory = _safe_preprocessing_directory(directory)
    stage_files = _stage_files_for(preprocessing.mediaType).get(stage)
    if stage_files is None:
        return False
    if stage == "keyframeExtraction":
        return _keyframe_artifacts_are_valid(directory)
    if stage == "imageNormalization":
        return _image_artifact_is_valid(directory / "normalized.png", "PNG")
    if stage == "proxyGeneration":
        return _image_artifact_is_valid(directory / "analysis-proxy.jpg", "JPEG")
    if stage == "reproducibilityAssessment":
        manifest = _read_json_object(directory, "manifest.json")
        return (
            _stage_files_are_valid(directory, stage_files)
            and manifest is not None
            and manifest.get("schemaVersion") == 1
            and manifest.get("algorithmVersion") == ALGORITHM_VERSION
            and manifest.get("mediaType") == preprocessing.mediaType
            and manifest.get("sourceReferenceMediaId") == preprocessing.sourceReferenceMediaId
        )
    return _stage_files_are_valid(directory, stage_files)


def reset_stage_artifacts(
    directory: Path, from_stage: StageName, media_type: MediaType,
) -> None:
    directory = _safe_preprocessing_directory(directory)
    stages = stage_order_for(media_type)
    start = stages.index(from_stage)
    for stage in stages[start:]:
        _discard_stage_artifacts(directory, stage, media_type)


def discard_preprocessing(data_dir: Path, project_id: str, preprocessing_id: str) -> None:
    discard_path(_safe_preprocessing_directory(
        preprocessing_directory(data_dir, project_id, preprocessing_id),
    ))


def discard_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _validate_keyframe_workspace(workspace: Path, frame_names: list[str]) -> None:
    if not 4 <= len(frame_names) <= 12:
        raise ValueError("关键帧数量必须在 4 到 12 之间")
    expected = [f"frame-{index:04d}.jpg" for index in range(1, len(frame_names) + 1)]
    if frame_names != expected or any(not _FRAME_NAME.fullmatch(name) for name in frame_names):
        raise ValueError("关键帧文件名无效")
    keyframes = workspace / "keyframes"
    if not _nonempty_regular_file(workspace / "contact-sheet.jpg"):
        raise ValueError("联系表文件无效")
    if keyframes.is_symlink() or not keyframes.is_dir() or any(
        not _nonempty_regular_file(keyframes / name) for name in frame_names
    ):
        raise ValueError("关键帧文件无效")


def _keyframe_artifacts_are_valid(directory: Path) -> bool:
    contact_sheet = directory / "contact-sheet.jpg"
    keyframes = directory / "keyframes"
    if not _path_is_inside(directory, contact_sheet) or not _path_is_inside(directory, keyframes):
        return False
    if (
        not _nonempty_regular_file(contact_sheet)
        or keyframes.is_symlink()
        or not keyframes.is_dir()
    ):
        return False
    names = sorted(path.name for path in keyframes.iterdir())
    expected = [f"frame-{index:04d}.jpg" for index in range(1, len(names) + 1)]
    return (
        4 <= len(names) <= 12
        and names == expected
        and all(
            _path_is_inside(directory, keyframes / name)
            and _nonempty_regular_file(keyframes / name)
            for name in names
        )
    )


def _read_json_object(directory: Path, filename: str) -> Optional[dict[str, Any]]:
    path = directory / filename
    if not _path_is_inside(directory, path) or not _nonempty_regular_file(path):
        return None
    try:
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload else None


def _stage_files_are_valid(directory: Path, files: tuple[str, ...]) -> bool:
    return all(
        _read_json_object(directory, filename) is not None
        if filename in _STAGE_JSON_FILES
        else _path_is_inside(directory, directory / filename)
        and _nonempty_regular_file(directory / filename)
        for filename in files
    )


def _image_artifact_is_valid(path: Path, image_format: str) -> bool:
    try:
        if not _nonempty_regular_file(path):
            return False
        with Image.open(path) as image:
            valid = image.format == image_format and image.mode == "RGB" and image.size[0] > 0 and image.size[1] > 0
            if valid:
                image.verify()
            return valid
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError):
        return False


def _discard_stage_artifacts(
    directory: Path, stage: StageName, media_type: MediaType,
) -> None:
    directory = _safe_preprocessing_directory(directory)
    for relative in _stage_files_for(media_type).get(stage, ()):
        path = directory / relative
        if path.is_symlink() or _path_is_inside(directory, path):
            discard_path(path)


def _path_is_inside(directory: Path, path: Path) -> bool:
    try:
        directory = _safe_preprocessing_directory(directory)
        raw_path = Path(os.path.abspath(path))
        raw_path.relative_to(directory)
        path.resolve(strict=False).relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _safe_preprocessing_directory(directory: Path) -> Path:
    raw_directory = Path(os.path.abspath(directory))
    local_preprocessing = raw_directory.parent
    project = local_preprocessing.parent
    project_files = project.parent
    data_root = project_files.parent
    try:
        if (
            local_preprocessing.name != "local-preprocessing"
            or project_files.name != "project-files"
        ):
            raise ValueError
        validate_storage_id(project.name)
        validate_storage_id(raw_directory.name)
        root = data_root.resolve()
        canonical_directory = (
            root / "project-files" / project.name / "local-preprocessing" / raw_directory.name
        )
        if raw_directory != canonical_directory:
            raise ValueError
        canonical_directory.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise OSError("本地预处理路径超出数据目录") from error
    return canonical_directory


def _nonempty_regular_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file() and path.stat().st_size > 0
