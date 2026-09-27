"""Prepare immutable project-asset inputs for local rendering."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Any

from .project_assets import ProjectAssets
from .reference_video import validate_storage_id


class RenderInputError(OSError):
    pass


class RenderInputPreparation:
    def __init__(self, project_assets: ProjectAssets):
        self._project_assets = project_assets

    def freeze_current(
        self,
        project_id: str,
        destination: Path,
        tracks: list[dict[str, Any]],
        assets: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        destination = Path(destination)
        created = False
        try:
            source_directory = _create_destination(destination)
            created = True
            sources = {}
            for asset_id in sorted(_used_asset_ids(tracks)):
                validate_storage_id(asset_id)
                source = self._project_assets.file(project_id, assets[asset_id])
                filename = validate_storage_id(asset_id + source.suffix.lower())
                _copy_regular(source, source_directory / filename)
                sources[asset_id] = filename
            return sources
        except (KeyError, OSError, TypeError, ValueError) as error:
            if created:
                shutil.rmtree(destination, ignore_errors=True)
            raise RenderInputError() from error

    def resolve(self, directory: Path, sources: dict[str, str]) -> dict[str, Path]:
        try:
            source_directory = _source_directory(Path(directory))
            result = {}
            for asset_id, filename in sources.items():
                validate_storage_id(asset_id)
                validate_storage_id(filename)
                path = source_directory / filename
                _regular_file(path)
                result[asset_id] = path
            return result
        except (OSError, TypeError, ValueError) as error:
            raise RenderInputError(_safe_resolve_message(error)) from error

    def freeze_approved(
        self,
        project_id: str,
        destination: Path,
        approved_directory: Path,
        approved_sources: dict[str, str],
        assets: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        destination = Path(destination)
        created = False
        try:
            source_directory = _create_destination(destination)
            created = True
            approved_source_directory = _source_directory(Path(approved_directory))
            sources = {}
            for asset_id in sorted(approved_sources):
                filename = approved_sources[asset_id]
                validate_storage_id(asset_id)
                validate_storage_id(filename)
                current = self._project_assets.file(project_id, assets[asset_id])
                approved = approved_source_directory / filename
                _copy_matching(current, approved, source_directory / filename)
                sources[asset_id] = filename
            return sources
        except (KeyError, OSError, TypeError, ValueError) as error:
            if created:
                shutil.rmtree(destination, ignore_errors=True)
            raise RenderInputError() from error


def _used_asset_ids(tracks):
    return {clip["assetId"] for track in tracks for clip in track["clips"]}


def _create_destination(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        source_directory = destination / "sources"
        source_directory.mkdir()
        return source_directory
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _source_directory(directory: Path) -> Path:
    if directory.is_symlink():
        raise OSError("前置工作台存储路径无效")
    if not directory.exists():
        raise FileNotFoundError(str(directory))
    if not directory.is_dir():
        raise NotADirectoryError(str(directory))
    source_directory = directory / "sources"
    if source_directory.is_symlink():
        raise OSError("前置工作台存储路径无效")
    if not source_directory.exists():
        raise FileNotFoundError(str(source_directory))
    if not source_directory.is_dir():
        raise NotADirectoryError(str(source_directory))
    return source_directory


def _safe_resolve_message(error: BaseException) -> str:
    message = str(error)
    if message and len(message) <= 300 and "/" not in message and "\\" not in message:
        return message
    return ""


def _regular_file(path: Path) -> None:
    with _open_regular(path):
        pass


def _copy_regular(source: Path, target: Path) -> None:
    try:
        with _open_regular(source) as stream, target.open("xb") as output:
            shutil.copyfileobj(stream, output)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _copy_matching(current: Path, approved: Path, target: Path) -> None:
    try:
        with _open_regular(current) as original, _open_regular(approved) as frozen:
            while True:
                original_bytes = original.read(1024 * 1024)
                frozen_bytes = frozen.read(1024 * 1024)
                if original_bytes != frozen_bytes:
                    raise ValueError("项目素材内容已变化")
                if not original_bytes:
                    break
            frozen.seek(0)
            with target.open("xb") as output:
                shutil.copyfileobj(frozen, output)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _open_regular(path: Path):
    if path.is_symlink():
        raise OSError("前置工作台存储路径无效")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError("不是普通文件")
    return os.fdopen(descriptor, "rb")
