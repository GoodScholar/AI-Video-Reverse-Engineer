import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Optional, Union

from fastapi import UploadFile

from .reference_media import ReferenceImage, ReferenceMediaError
from .reference_video import ReferenceVideo, reference_video_format_from_major_brand, validate_storage_id


ReferenceMediaValue = Union[ReferenceImage, ReferenceVideo, Mapping[str, object]]


class UnsafeManagedMediaPathError(OSError):
    pass


@dataclass(frozen=True)
class StagedReferenceMedia:
    path: Path
    size_bytes: int


async def stage_reference_media(
    upload: UploadFile,
    data_dir: Path,
    *,
    max_bytes: int,
    chunk_bytes: int = 1024 * 1024,
) -> StagedReferenceMedia:
    data_dir.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(dir=data_dir, prefix=".reference-", suffix=".part")
    path = Path(raw_path)
    descriptor_open = True
    total = 0
    try:
        target = os.fdopen(descriptor, "wb")
        descriptor_open = False
        with target:
            while True:
                chunk = await upload.read(chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ReferenceMediaError(
                        413,
                        "video_too_large",
                        f"参考素材实测为 {total:,} 字节，最大允许 {max_bytes:,} 字节。",
                    )
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        return StagedReferenceMedia(path=path, size_bytes=total)
    except BaseException:
        if descriptor_open:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def detect_reference_media_type(path: Path) -> Literal["image", "video"]:
    with path.open("rb") as source:
        signature = source.read(16)
    if (
        signature.startswith(b"\xff\xd8\xff")
        or signature.startswith(b"\x89PNG\r\n\x1a\n")
        or (signature.startswith(b"RIFF") and signature[8:12] == b"WEBP")
    ):
        return "image"
    return "video"


def detect_reference_video_format(path: Path) -> Optional[Literal["mp4", "mov"]]:
    major_brand = _iso_bmff_major_brand(path)
    try:
        return reference_video_format_from_major_brand(major_brand)
    except ValueError as error:
        raise _unsupported_video_format() from error


def _iso_bmff_major_brand(path: Path) -> Optional[bytes]:
    """Read an ISO-BMFF `ftyp` brand, distinguishing no box from an unsafe partial scan."""
    file_size = path.stat().st_size
    header_read_budget = 64 * 1024
    header_bytes_read = 0
    offset = 0
    saw_valid_box = False
    with path.open("rb") as source:
        while offset + 8 <= file_size and header_bytes_read + 8 <= header_read_budget:
            source.seek(offset)
            header = source.read(8)
            header_bytes_read += len(header)
            if len(header) != 8:
                return _reject_or_defer_iso_bmff(saw_valid_box, b"")
            box_size = int.from_bytes(header[:4], "big")
            box_type = header[4:8]
            header_size = 8
            if box_size == 1:
                if header_bytes_read + 8 > header_read_budget:
                    return None
                extended_size = source.read(8)
                header_bytes_read += len(extended_size)
                if len(extended_size) != 8:
                    return _reject_or_defer_iso_bmff(saw_valid_box, box_type)
                box_size = int.from_bytes(extended_size, "big")
                header_size = 16
            elif box_size == 0:
                if box_type == b"ftyp":
                    raise _unsupported_video_format()
                return None
            if box_size < header_size or offset + box_size > file_size:
                return _reject_or_defer_iso_bmff(saw_valid_box, box_type)
            if box_type == b"ftyp":
                if header_bytes_read + 4 > header_read_budget:
                    return None
                payload = source.read(4)
                header_bytes_read += len(payload)
                if box_size < header_size + 8 or len(payload) != 4:
                    raise _unsupported_video_format()
                return payload
            saw_valid_box = True
            offset += box_size
    if offset < file_size and header_bytes_read < header_read_budget:
        return _reject_or_defer_iso_bmff(saw_valid_box, b"")
    return None


def _reject_or_defer_iso_bmff(saw_valid_box: bool, box_type: bytes) -> Optional[bytes]:
    if saw_valid_box or box_type == b"ftyp":
        raise _unsupported_video_format()
    return None


def _unsupported_video_format() -> ReferenceMediaError:
    return ReferenceMediaError(415, "unsupported_video_format", "仅支持 MP4 或 MOV 视频容器。")


def reference_media_basename(original_name: str) -> str:
    basename = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename:
        raise ReferenceMediaError(415, "unsupported_media_format", "参考素材文件名不能为空。")
    return basename


def managed_reference_media_path(data_dir: Path, project_id: str, media: ReferenceMediaValue) -> Path:
    media_id, media_format = _media_id_and_format(media)
    validate_storage_id(project_id)
    validate_storage_id(media_id)
    return _safe_managed_path(
        data_dir,
        "project-files",
        project_id,
        "reference-media",
        f"{media_id}.{media_format}",
    )


def resolve_reference_media_path(data_dir: Path, project_id: str, media: ReferenceMediaValue) -> Path:
    canonical = managed_reference_media_path(data_dir, project_id, media)
    if canonical.exists() or _media_type(media) != "video":
        return canonical
    media_id, media_format = _media_id_and_format(media)
    return _safe_managed_path(
        data_dir,
        "project-files",
        project_id,
        "reference-videos",
        f"{media_id}.{media_format}",
    )


def managed_reference_media_is_safe(data_dir: Path, project_id: str, media: ReferenceMediaValue) -> bool:
    try:
        root = Path(os.path.abspath(data_dir))
        if root.is_symlink() or not root.is_dir():
            return False
        candidate = resolve_reference_media_path(root, project_id, media)
        relative = candidate.relative_to(root.resolve())
        current = root.resolve()
        for name in relative.parts:
            current = current / name
            if current.is_symlink():
                return False
        metadata = os.stat(candidate, follow_symlinks=False)
        return stat.S_ISREG(metadata.st_mode) and not candidate.is_symlink()
    except (OSError, ValueError):
        return False


def promote_staged_reference_media(staged_path: Path, final_path: Path) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_path, final_path)


def discard_managed_file(path: Optional[Path]) -> None:
    if path is not None:
        path.unlink(missing_ok=True)


def _safe_managed_path(data_dir: Path, *parts: str) -> Path:
    root = data_dir.resolve()
    candidate = root.joinpath(*parts)
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise UnsafeManagedMediaPathError("参考素材托管路径超出数据目录") from error
    return candidate


def _media_type(media: ReferenceMediaValue) -> str:
    return media["type"] if isinstance(media, Mapping) else media.type


def _media_id_and_format(media: ReferenceMediaValue) -> tuple[str, str]:
    if isinstance(media, Mapping):
        media_id = media.get("id")
        media_format = media.get("format")
    else:
        media_id = media.id
        media_format = media.format
    if not isinstance(media_id, str) or not isinstance(media_format, str):
        raise ValueError("参考素材元数据无效")
    return media_id, media_format
