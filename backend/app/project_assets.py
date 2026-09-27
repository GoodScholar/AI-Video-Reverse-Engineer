"""Project-level ownership for durable media assets and their files."""
from __future__ import annotations

import os
import shutil
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from uuid import uuid4

from .preproduction import PreproductionStore
from .reference_video import validate_storage_id


ReferenceFacts = Callable[[str, str], list[dict[str, str]]]


class AssetInUseError(Exception):
    def __init__(self, references: list[dict[str, str]]):
        super().__init__("项目素材仍被引用")
        self.references = references


class AssetFileUnavailableError(OSError):
    pass


class AssetReferencesUnavailableError(Exception):
    pass


class ProjectAssets:
    """Hide the legacy workspace shape and asset directory behind one boundary."""

    def __init__(self, data_dir: Path, *, reference_facts: Iterable[ReferenceFacts] = ()):
        self._store = PreproductionStore(Path(data_dir))
        self._reference_facts = tuple(reference_facts)

    @staticmethod
    def new_id(prefix: str | None = "asset") -> str:
        if prefix is None:
            return uuid4().hex
        validate_storage_id(prefix)
        return f"{prefix}-{uuid4().hex}"

    def records(self, project_id: str) -> list[dict[str, Any]]:
        return self.records_from(self._store.load(project_id))

    @staticmethod
    def records_from(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [asset for asset in state["assets"] if isinstance(asset, dict)]

    def index(self, project_id: str) -> dict[str, dict[str, Any]]:
        return self.index_from(self._store.load(project_id))

    @staticmethod
    def index_from(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            asset["id"]: asset
            for asset in ProjectAssets.records_from(state)
            if isinstance(asset.get("id"), str)
        }

    def public(
        self,
        project_id: str,
        *,
        kinds: Iterable[str] | None = None,
        include_url: bool = True,
        include_source_reference: bool = False,
    ) -> list[dict[str, Any]]:
        allowed = set(kinds) if kinds is not None else None
        return [
            self.public_record(
                project_id, asset,
                include_url=include_url,
                include_source_reference=include_source_reference,
            )
            for asset in self.records(project_id)
            if allowed is None or asset.get("kind") in allowed
        ]

    @staticmethod
    def public_record(
        project_id: str,
        asset: dict[str, Any],
        *,
        include_url: bool = True,
        include_source_reference: bool = False,
    ) -> dict[str, Any]:
        public = {
            key: value
            for key, value in asset.items()
            if key != "file"
            and (include_source_reference or key != "sourceReferenceId")
            and not key.startswith("_")
        }
        if include_url:
            public["url"] = f"/api/projects/{project_id}/preproduction/assets/{asset['id']}/file"
        return public

    def get(self, project_id: str, asset_id: str) -> dict[str, Any]:
        validate_storage_id(asset_id)
        try:
            return self.index(project_id)[asset_id]
        except KeyError:
            raise KeyError("项目素材不存在") from None

    def file(self, project_id: str, asset: str | dict[str, Any]) -> Path:
        record = self.get(project_id, asset) if isinstance(asset, str) else asset
        try:
            filename = record["file"]
            validate_storage_id(filename)
            path = self._store.path(project_id, "assets", filename)
            _regular_file(path)
            return path
        except (KeyError, TypeError, OSError, ValueError):
            raise AssetFileUnavailableError("项目素材文件不可用") from None

    def available(self, project_id: str, asset: str | dict[str, Any]) -> bool:
        try:
            self.file(project_id, asset)
            return True
        except (KeyError, OSError, ValueError):
            return False

    def references(self, project_id: str, asset_id: str) -> list[dict[str, str]]:
        self.get(project_id, asset_id)
        references: list[dict[str, str]] = []
        try:
            for facts in self._reference_facts:
                references.extend(facts(project_id, asset_id))
        except (OSError, ValueError, KeyError, TypeError):
            raise AssetReferencesUnavailableError("无法完整读取项目素材引用") from None
        return references

    @contextmanager
    def install(
        self,
        project_id: str,
        state: dict[str, Any],
        source: Path,
        record: dict[str, Any],
        *,
        move: bool = False,
    ) -> Iterator[dict[str, Any]]:
        asset_id = validate_storage_id(record.get("id"))
        filename = validate_storage_id(record.get("file"))
        if asset_id in self.index_from(state):
            raise ValueError("项目素材标识重复")
        target = self._store.path(project_id, "assets", filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("项目素材文件已存在")
        _regular_file(Path(source))
        if move:
            os.replace(source, target)
        else:
            _copy_regular(Path(source), target)
        state["assets"].append(record)
        try:
            yield record
        except BaseException:
            state["assets"].remove(record)
            if move and target.exists():
                os.replace(target, source)
            else:
                target.unlink(missing_ok=True)
            raise

    def update_metadata(
        self,
        state: dict[str, Any],
        asset_id: str,
        *,
        name: str,
        notes: str,
        save: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        asset = self.find_in(state, asset_id)
        previous_name = asset.get("name")
        previous_notes = asset.get("notes")
        previous_revision = state["revision"]
        try:
            asset.update(name=name, notes=notes)
            state["revision"] += 1
            save(state)
        except BaseException:
            asset["name"] = previous_name
            if previous_notes is None:
                asset.pop("notes", None)
            else:
                asset["notes"] = previous_notes
            state["revision"] = previous_revision
            raise
        return asset

    def add_many(
        self,
        project_id: str,
        entries: Iterable[tuple[Path, dict[str, Any]]],
    ) -> dict[str, Any]:
        state = self._store.load(project_id)
        with ExitStack() as stack:
            for source, record in entries:
                stack.enter_context(self.install(project_id, state, source, record))
            state["revision"] += 1
            self._store.save(project_id, state)
        return state

    def delete(
        self,
        project_id: str,
        state: dict[str, Any],
        asset_id: str,
        *,
        save: Callable[[dict[str, Any]], None],
    ) -> None:
        asset = self.find_in(state, asset_id)
        references = self.references(project_id, asset_id)
        if references:
            raise AssetInUseError(references)
        path = self.file(project_id, asset)
        staged = path.with_name(f".delete-{uuid4().hex}.part")
        previous_assets = state["assets"]
        previous_revision = state["revision"]
        path.rename(staged)
        try:
            state["assets"] = [item for item in previous_assets if item is not asset]
            state["revision"] += 1
            save(state)
        except BaseException:
            state["assets"] = previous_assets
            state["revision"] = previous_revision
            if staged.exists():
                staged.rename(path)
            raise
        staged.unlink()

    @staticmethod
    def find_in(state: dict[str, Any], asset_id: str) -> dict[str, Any]:
        validate_storage_id(asset_id)
        asset = next((item for item in ProjectAssets.records_from(state) if item.get("id") == asset_id), None)
        if asset is None:
            raise KeyError("项目素材不存在")
        return asset


def _regular_file(path: Path) -> None:
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or path.is_symlink():
        raise OSError("项目素材文件不可用")


def _copy_regular(source: Path, target: Path) -> None:
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as stream, target.open("xb") as output:
            descriptor = -1
            shutil.copyfileobj(stream, output)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)
