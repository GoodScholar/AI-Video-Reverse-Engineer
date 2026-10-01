"""Project-level ownership for durable media assets and their files."""
from __future__ import annotations

import os
import shutil
import stat
import hashlib
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Iterator, Mapping
from uuid import uuid4

from .durable_runs import freeze_run_input
from .preproduction import PreproductionStore
from .reference_video import validate_storage_id


ReferenceFacts = Callable[[str, str], list[dict[str, str]]]
_RESULT_INSTALL_LOCKS: dict[str, RLock] = {}
_RESULT_INSTALL_LOCKS_GUARD = RLock()


class AssetInUseError(Exception):
    def __init__(self, references: list[dict[str, str]]):
        super().__init__("项目素材仍被引用")
        self.references = references


class AssetFileUnavailableError(OSError):
    pass


class AssetReferencesUnavailableError(Exception):
    pass


class AssetStateConflict(Exception):
    """The caller's workspace revision is no longer current."""


class ResultInstallationConflict(ValueError):
    """A run/result identity already names different immutable media."""


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
            if "sha256" in record and _sha256(path) != record["sha256"]:
                raise OSError("项目素材内容哈希不匹配")
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
        project_id: str,
        state: dict[str, Any],
        asset_id: str,
        *,
        name: str,
        notes: str,
        save: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        with _result_install_lock(self._store.root):
            latest = self._store.load(project_id)
            if state.get("revision") != latest.get("revision"):
                raise AssetStateConflict("项目素材已更新")
            asset = self.find_in(latest, asset_id)
            previous_name = asset.get("name")
            previous_notes = asset.get("notes")
            previous_revision = latest["revision"]
            try:
                asset.update(name=name, notes=notes)
                latest["revision"] += 1
                save(latest)
            except BaseException:
                asset["name"] = previous_name
                if previous_notes is None:
                    asset.pop("notes", None)
                else:
                    asset["notes"] = previous_notes
                latest["revision"] = previous_revision
                raise
            _sync_state(state, latest)
            return self.find_in(state, asset_id)

    def add_many(
        self,
        project_id: str,
        entries: Iterable[tuple[Path, dict[str, Any]]],
    ) -> dict[str, Any]:
        with _result_install_lock(self._store.root):
            state = self._store.load(project_id)
            with ExitStack() as stack:
                for source, record in entries:
                    stack.enter_context(self.install(project_id, state, source, record))
                state["revision"] += 1
                self._store.save(project_id, state)
            return state

    def install_run_result(
        self,
        project_id: str,
        *,
        source: Path,
        run_id: str,
        result_id: str,
        kind: str,
        name: str,
        input_snapshot: Any,
        provider: str | None = None,
        processing_method: str | None = None,
        common_run_id: str | None = None,
        domain_kind: str | None = None,
        domain_run_id: str | None = None,
        media: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Install one controlled media result once, preserving its run identity."""
        validate_storage_id(run_id)
        validate_storage_id(result_id)
        if common_run_id is None and (domain_kind is None or domain_run_id is None):
            raise ValueError("运行结果必须指定公共运行或领域运行身份")
        if domain_kind is not None:
            validate_storage_id(domain_kind)
        if domain_run_id is not None:
            domain_run_id = validate_storage_id(domain_run_id)
        if common_run_id is not None:
            common_run_id = validate_storage_id(common_run_id)
        if kind not in {"image", "video", "audio"}:
            raise ValueError("运行结果媒体类型无效")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("运行结果名称无效")
        _regular_file(Path(source))
        content_hash = _sha256(Path(source))
        frozen = freeze_run_input(input_snapshot)
        media_metadata = _media_metadata(media)
        with _result_install_lock(self._store.root):
            return self._install_run_result_locked(
                project_id, Path(source), run_id, result_id, kind, name.strip(), frozen, content_hash,
                provider, processing_method, common_run_id, domain_kind, domain_run_id, media_metadata,
            )

    def _install_run_result_locked(
        self, project_id: str, source: Path, run_id: str, result_id: str, kind: str, name: str,
        frozen: Any, content_hash: str, provider: str | None, processing_method: str | None,
        common_run_id: str | None, domain_kind: str | None, domain_run_id: str | None, media_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._store.load(project_id)
        if common_run_id is not None:
            candidates = [asset for asset in self.records_from(state)
                          if asset.get("sourceCommonRunId") == common_run_id and asset.get("sourceResultId") == result_id]
        else:
            candidates = [asset for asset in self.records_from(state)
                          if asset.get("sourceResultId") == result_id
                          and asset.get("sourceDomainKind", "legacy") == domain_kind
                          and asset.get("sourceDomainRunId", asset.get("sourceRunId")) == domain_run_id]
        if len(candidates) > 1:
            raise ResultInstallationConflict("运行结果身份对应多个项目素材")
        existing = candidates[0] if candidates else None
        if existing is not None:
            try:
                installed_hash = _sha256(self.file(project_id, existing))
            except (OSError, ValueError, KeyError):
                raise ResultInstallationConflict("已安装的运行结果文件不可用") from None
            expected_facts = {
                "kind": kind, "provider": provider or None, "processingMethod": processing_method or None,
                "sourceCommonRunId": common_run_id, "duration": media_metadata.get("duration"),
                "width": media_metadata.get("width"), "height": media_metadata.get("height"),
                "sourceDomainKind": domain_kind, "sourceDomainRunId": domain_run_id,
            }
            actual_facts = {key: existing.get(key) for key in expected_facts}
            actual_facts["sourceDomainKind"] = existing.get("sourceDomainKind", "legacy")
            actual_facts["sourceDomainRunId"] = existing.get("sourceDomainRunId", existing.get("sourceRunId"))
            if (existing.get("sha256") != content_hash or installed_hash != existing.get("sha256")
                    or existing.get("inputFingerprint") != frozen.fingerprint or actual_facts != expected_facts):
                raise ResultInstallationConflict("同一运行结果的内容或输入已变化")
            return existing
        asset_id = self.new_id("result")
        suffix = Path(source).suffix.lower()
        filename = asset_id + (suffix if suffix and suffix[1:].isalnum() else ".bin")
        record = {
            "id": asset_id,
            "assetVersionId": asset_id + "-v1",
            "name": name,
            "kind": kind,
            "role": "reference",
            "file": filename,
            "sha256": content_hash,
            "sourceRunId": run_id,
            "sourceResultId": result_id,
            "inputSnapshot": deepcopy(frozen.snapshot),
            "inputFingerprint": frozen.fingerprint,
            **media_metadata,
        }
        if domain_kind is not None:
            record["sourceDomainKind"] = domain_kind
            record["sourceDomainRunId"] = domain_run_id
        if common_run_id is not None:
            record["sourceCommonRunId"] = common_run_id
        if isinstance(provider, str) and provider:
            record["provider"] = provider
        if isinstance(processing_method, str) and processing_method:
            record["processingMethod"] = processing_method
        with self.install(project_id, state, source, record):
            if _sha256(self._store.path(project_id, "assets", filename)) != content_hash:
                raise ResultInstallationConflict("运行结果安装后内容校验失败")
            state["revision"] += 1
            self._store.save(project_id, state)
        return record

    def delete(
        self,
        project_id: str,
        state: dict[str, Any],
        asset_id: str,
        *,
        save: Callable[[dict[str, Any]], None],
    ) -> None:
        with _result_install_lock(self._store.root):
            latest = self._store.load(project_id)
            if state.get("revision") != latest.get("revision"):
                raise AssetStateConflict("项目素材已更新")
            asset = self.find_in(latest, asset_id)
            references = self.references(project_id, asset_id)
            if references:
                raise AssetInUseError(references)
            path = self.file(project_id, asset)
            staged = path.with_name(f".delete-{uuid4().hex}.part")
            previous_assets = latest["assets"]
            previous_revision = latest["revision"]
            path.rename(staged)
            try:
                latest["assets"] = [item for item in previous_assets if item is not asset]
                latest["revision"] += 1
                save(latest)
            except BaseException:
                latest["assets"] = previous_assets
                latest["revision"] = previous_revision
                if staged.exists():
                    staged.rename(path)
                raise
            staged.unlink()
            _sync_state(state, latest)

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


def _result_install_lock(root: Path) -> RLock:
    key = str(Path(root).resolve())
    with _RESULT_INSTALL_LOCKS_GUARD:
        return _RESULT_INSTALL_LOCKS.setdefault(key, RLock())


def _sync_state(destination: dict[str, Any], source: dict[str, Any]) -> None:
    destination.clear()
    destination.update(deepcopy(source))


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


def _sha256(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _media_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - {"duration", "width", "height"}:
        raise ValueError("运行结果媒体信息无效")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "duration":
            if not isinstance(item, (int, float)) or isinstance(item, bool) or item <= 0:
                raise ValueError("运行结果时长无效")
        elif not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            raise ValueError("运行结果尺寸无效")
        result[key] = item
    return result
