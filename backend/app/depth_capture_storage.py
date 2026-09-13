import json
import os
import re
import secrets
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from .depth_capture import DEPTH_ALGORITHM_VERSION
from .reference_video import validate_storage_id


DEPTH_ARTIFACTS = (
    "depth-control.mp4",
    "depth-preview.mp4",
    "depth-metadata.json",
    "depth-quality.json",
    "manifest.json",
)
_MODEL_IDENTITY = {
    "modelId": "video-depth-anything-small-relative",
    "upstreamCommit": "4f5ae23172ba60fd7bc11ef671cca678842c7072",
    "checkpointSha256": "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
}
_NORMALIZATION_DIRECTION = "near_white_far_black"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_COMMIT_LOCK = threading.Lock()


@dataclass(frozen=True)
class DepthArtifactInspection:
    valid: bool


class DepthPreviewUnavailableError(OSError):
    """The fixed preview asset is missing, unsafe, or fails artifact validation."""


def open_validated_depth_preview(
    *,
    data_dir: Path,
    project_id: str,
    capture_id: str,
    source_reference_video_id: str,
    algorithm: int = DEPTH_ALGORITHM_VERSION,
) -> tuple[int, int]:
    """Return an open descriptor for the validated preview's immutable inode.

    Unlike the read projection, this strict path preserves genuine I/O failures so
    the API can distinguish unavailable storage from an absent or unsafe asset.
    """
    try:
        _validate_ids(project_id, capture_id)
        _validate_source_and_algorithm(source_reference_video_id, algorithm)
    except (OSError, ValueError) as error:
        raise DepthPreviewUnavailableError("深度预览不可用") from error
    capture_fd = _open_depth_capture_directory_strict(data_dir, project_id, capture_id)
    descriptor: Optional[int] = None
    try:
        try:
            _validate_artifacts_strict(
                capture_fd,
                source_reference_video_id=source_reference_video_id,
                algorithm=algorithm,
            )
            expected = os.stat("depth-preview.mp4", dir_fd=capture_fd, follow_symlinks=False)
        except ValueError as error:
            raise DepthPreviewUnavailableError("深度预览不可用") from error
        descriptor = os.open("depth-preview.mp4", _FILE_FLAGS, dir_fd=capture_fd)
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_size <= 0
            or actual.st_dev != expected.st_dev
            or actual.st_ino != expected.st_ino
        ):
            raise DepthPreviewUnavailableError("深度预览不可用")
        return descriptor, actual.st_size
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(capture_fd)


def depth_capture_directory(data_dir: Path, project_id: str, capture_id: str) -> Path:
    root = _data_root(data_dir)
    _validate_ids(project_id, capture_id)
    directory = root / "project-files" / project_id / "depth-captures" / capture_id
    _reject_symlink_ancestors(root, directory)
    return directory


def create_depth_capture_workspace(data_dir: Path, project_id: str, capture_id: str) -> Path:
    with _depth_captures_parent(data_dir, project_id, capture_id, create=True) as (parent, parent_fd):
        for _ in range(32):
            name = _workspace_name(capture_id)
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            return parent / name
    raise OSError("无法创建深度捕捉工作目录")


def commit_depth_artifacts(
    workspace: Path,
    *,
    data_dir: Path,
    project_id: str,
    capture_id: str,
    source_reference_video_id: str,
    algorithm: int = DEPTH_ALGORITHM_VERSION,
) -> Path:
    _validate_source_and_algorithm(source_reference_video_id, algorithm)
    with _depth_captures_parent(data_dir, project_id, capture_id, create=True) as (parent, parent_fd):
        workspace_name = _validated_workspace_name(workspace, parent, capture_id)
        workspace_fd = _open_directory(workspace_name, parent_fd)
        try:
            _validate_artifacts(
                workspace_fd,
                source_reference_video_id=source_reference_video_id,
                algorithm=algorithm,
            )
            workspace_stat = os.fstat(workspace_fd)
            with _COMMIT_LOCK:
                _assert_workspace_binding(parent_fd, workspace_name, workspace_stat)
                _validate_artifacts(
                    workspace_fd,
                    source_reference_video_id=source_reference_video_id,
                    algorithm=algorithm,
                )
                if _entry_exists(parent_fd, capture_id):
                    raise OSError("深度捕捉目标已存在")
                os.replace(
                    workspace_name,
                    capture_id,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
        finally:
            os.close(workspace_fd)
    return depth_capture_directory(data_dir, project_id, capture_id)


def inspect_depth_artifacts(
    *,
    data_dir: Path,
    project_id: str,
    capture_id: str,
    source_reference_video_id: str,
    algorithm: int = DEPTH_ALGORITHM_VERSION,
) -> DepthArtifactInspection:
    try:
        _validate_source_and_algorithm(source_reference_video_id, algorithm)
        with _depth_captures_parent(data_dir, project_id, capture_id, create=False) as (_, parent_fd):
            capture_fd = _open_directory(capture_id, parent_fd)
            try:
                _validate_artifacts(
                    capture_fd,
                    source_reference_video_id=source_reference_video_id,
                    algorithm=algorithm,
                )
            finally:
                os.close(capture_fd)
    except (OSError, ValueError):
        return DepthArtifactInspection(valid=False)
    return DepthArtifactInspection(valid=True)


def discard_depth_capture(data_dir: Path, project_id: str, capture_id: str) -> None:
    try:
        with _depth_captures_parent(data_dir, project_id, capture_id, create=False) as (_, parent_fd):
            capture_fd = _open_directory(capture_id, parent_fd)
            try:
                _validate_discardable_artifacts(capture_fd)
                capture_stat = os.fstat(capture_fd)
                for filename in DEPTH_ARTIFACTS:
                    os.unlink(filename, dir_fd=capture_fd)
                _assert_workspace_binding(parent_fd, capture_id, capture_stat)
                os.rmdir(capture_id, dir_fd=parent_fd)
            finally:
                os.close(capture_fd)
    except FileNotFoundError:
        return
    except OSError as error:
        raise OSError("深度捕捉路径超出数据目录") from error


def _validate_artifacts(
    directory_fd: int,
    *,
    source_reference_video_id: str,
    algorithm: int,
) -> None:
    names = set(os.listdir(directory_fd))
    expected = set(DEPTH_ARTIFACTS)
    if not expected.issubset(names):
        raise ValueError("深度捕捉产物不完整")
    if names != expected or not all(_nonempty_regular_file(directory_fd, name) for name in DEPTH_ARTIFACTS):
        raise ValueError("深度捕捉产物无效")
    manifest = _read_manifest(directory_fd)
    if manifest is None or not _manifest_is_valid(
        manifest,
        source_reference_video_id=source_reference_video_id,
        algorithm=algorithm,
    ):
        raise ValueError("深度捕捉清单无效")


def _validate_artifacts_strict(
    directory_fd: int,
    *,
    source_reference_video_id: str,
    algorithm: int,
) -> None:
    names = set(os.listdir(directory_fd))
    expected = set(DEPTH_ARTIFACTS)
    if not expected.issubset(names):
        raise ValueError("深度捕捉产物不完整")
    if names != expected or not all(_nonempty_regular_file_strict(directory_fd, name) for name in DEPTH_ARTIFACTS):
        raise ValueError("深度捕捉产物无效")
    manifest = _read_manifest_strict(directory_fd)
    if manifest is None or not _manifest_is_valid(
        manifest,
        source_reference_video_id=source_reference_video_id,
        algorithm=algorithm,
    ):
        raise ValueError("深度捕捉清单无效")


def _validate_discardable_artifacts(directory_fd: int) -> None:
    if set(os.listdir(directory_fd)) != set(DEPTH_ARTIFACTS) or not all(
        _regular_file(directory_fd, name) for name in DEPTH_ARTIFACTS
    ):
        raise OSError("深度捕捉目录无效")


def _manifest_is_valid(
    manifest: dict[str, Any],
    *,
    source_reference_video_id: str,
    algorithm: int,
) -> bool:
    return (
        _is_version(manifest.get("schemaVersion"), 1)
        and _is_version(manifest.get("algorithmVersion"), algorithm)
        and manifest.get("sourceReferenceVideoId") == source_reference_video_id
        and manifest.get("modelIdentity") == _MODEL_IDENTITY
        and manifest.get("normalizationDirection") == _NORMALIZATION_DIRECTION
        and manifest.get("files") == list(DEPTH_ARTIFACTS)
    )


def _is_version(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _read_manifest(directory_fd: int) -> Optional[dict[str, Any]]:
    try:
        descriptor = os.open("manifest.json", _FILE_FLAGS, dir_fd=directory_fd)
        with os.fdopen(descriptor, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_manifest_strict(directory_fd: int) -> Optional[dict[str, Any]]:
    try:
        descriptor = os.open("manifest.json", _FILE_FLAGS, dir_fd=directory_fd)
        with os.fdopen(descriptor, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _nonempty_regular_file(directory_fd: int, name: str) -> bool:
    try:
        return _regular_file(directory_fd, name) and os.stat(
            name, dir_fd=directory_fd, follow_symlinks=False,
        ).st_size > 0
    except OSError:
        return False


def _nonempty_regular_file_strict(directory_fd: int, name: str) -> bool:
    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    return stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0


def _regular_file(directory_fd: int, name: str) -> bool:
    try:
        return stat.S_ISREG(os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


@contextmanager
def _depth_captures_parent(
    data_dir: Path,
    project_id: str,
    capture_id: str,
    *,
    create: bool,
) -> Iterator[tuple[Path, int]]:
    root = _data_root(data_dir)
    _validate_ids(project_id, capture_id)
    root_fd = _open_directory(root)
    descriptors = [root_fd]
    try:
        project_files_fd = _open_child_directory(root_fd, "project-files", create)
        descriptors.append(project_files_fd)
        project_fd = _open_child_directory(project_files_fd, project_id, create)
        descriptors.append(project_fd)
        captures_fd = _open_child_directory(project_fd, "depth-captures", create)
        descriptors.append(captures_fd)
    except OSError as error:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise OSError("深度捕捉路径超出数据目录") from error
    try:
        yield root / "project-files" / project_id / "depth-captures", captures_fd
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_child_directory(parent_fd: int, name: str, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    return _open_directory(name, parent_fd)


def _open_directory(path: Union[Path, str], parent_fd: Optional[int] = None) -> int:
    if parent_fd is None:
        return os.open(path, _DIRECTORY_FLAGS)
    return os.open(path, _DIRECTORY_FLAGS, dir_fd=parent_fd)


def _open_depth_capture_directory_strict(data_dir: Path, project_id: str, capture_id: str) -> int:
    root = Path(os.path.abspath(data_dir))
    descriptors: list[int] = []
    try:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
        descriptors.append(descriptor)
        for name in ("project-files", project_id, "depth-captures"):
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
        return os.open(capture_id, _DIRECTORY_FLAGS, dir_fd=descriptors[-1])
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _data_root(data_dir: Path) -> Path:
    raw_root = Path(os.path.abspath(data_dir))
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise OSError("深度捕捉路径超出数据目录")
    return raw_root.resolve()


def _validate_ids(project_id: str, capture_id: str) -> None:
    try:
        validate_storage_id(project_id)
        validate_storage_id(capture_id)
    except ValueError as error:
        raise OSError("深度捕捉路径超出数据目录") from error


def _validate_source_and_algorithm(source_reference_video_id: str, algorithm: int) -> None:
    try:
        validate_storage_id(source_reference_video_id)
    except ValueError as error:
        raise ValueError("深度捕捉清单无效") from error
    if not _is_version(algorithm, DEPTH_ALGORITHM_VERSION):
        raise ValueError("深度捕捉清单无效")


def _reject_symlink_ancestors(root: Path, directory: Path) -> None:
    try:
        directory.relative_to(root)
        current = root
        for name in directory.relative_to(root).parts:
            current = current / name
            if current.is_symlink():
                raise ValueError
    except ValueError as error:
        raise OSError("深度捕捉路径超出数据目录") from error


def _workspace_name(capture_id: str) -> str:
    return f".{capture_id}-{secrets.token_hex(16)}"


def _validated_workspace_name(workspace: Path, parent: Path, capture_id: str) -> str:
    raw_workspace = Path(os.path.abspath(workspace))
    pattern = rf"\.{re.escape(capture_id)}-[0-9a-f]{{32}}"
    if raw_workspace.parent != parent or not re.fullmatch(pattern, raw_workspace.name):
        raise OSError("深度捕捉工作目录无效")
    return raw_workspace.name


def _assert_workspace_binding(parent_fd: int, name: str, expected: os.stat_result) -> None:
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_dev != expected.st_dev
        or current.st_ino != expected.st_ino
    ):
        raise OSError("深度捕捉工作目录无效")


def _entry_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True
