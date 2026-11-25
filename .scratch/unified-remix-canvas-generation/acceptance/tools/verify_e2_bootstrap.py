#!/usr/bin/env python3
"""Fail-closed verifier for the audited E2 bootstrap inputs."""

from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile


UV_ARCHIVES = {
    "darwin-arm64": (
        "uv-aarch64-apple-darwin-0.11.15.tar.gz",
        "sha256:7e5b336108f8576eda1939920ca0a805b4a9a3c3d3eb2f6140e38b7092fbe4f3",
    ),
    "linux-x86_64-gnu": (
        "uv-x86_64-unknown-linux-gnu-0.11.15.tar.gz",
        "sha256:b03e572f010bea94a4a52d42671ba72981e12894f71576181a1d26ff68546da7",
    ),
}
WHEELS = {
    "darwin-arm64": (
        "zstandard-0.23.0-cp313-cp313-macosx_11_0_arm64.whl",
        "sha256:38302b78a850ff82656beaddeb0bb989a0322a8bbb1bf1ab10c17506681d772a",
    ),
    "linux-x86_64-gnu": (
        "zstandard-0.23.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "sha256:6f77fa49079891a4aab203d0b1744acc85577ed16d767b52fc089d83faf8d8ed",
    ),
}
EMPTY_CONFIG_SHA256 = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
PROXY_VARIABLES = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"})


@dataclass(frozen=True)
class ArchiveLimits:
    compressed_bytes: int
    members: int
    member_bytes: int
    expanded_bytes: int
    ratio: int


DEFAULT_LIMITS = ArchiveLimits(
    compressed_bytes=1024**3,
    members=100_000,
    member_bytes=256 * 1024**2,
    expanded_bytes=4 * 1024**3,
    ratio=100,
)


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactSnapshot:
    directory: Path
    path: Path

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
            self.directory.rmdir()
        except OSError:
            pass


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _regular_bytes(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise BootstrapError(f"missing regular {label}: {path.name}")
            data = handle.read()
            after = os.fstat(handle.fileno())
    except OSError as error:
        raise BootstrapError(f"missing regular {label}: {path.name}") from error
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise BootstrapError(f"{label} changed while being read: {path.name}")
    return data


def _regular_size(path: Path, *, label: str) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise BootstrapError(f"missing regular {label}: {path.name}")
            return metadata.st_size
    except OSError as error:
        raise BootstrapError(f"missing regular {label}: {path.name}") from error


def _snapshot_file(path: Path, *, label: str, maximum: int) -> tuple[ArtifactSnapshot, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    snapshot: ArtifactSnapshot | None = None
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise BootstrapError(f"missing regular {label}: {path.name}")
            if before.st_size > maximum:
                raise BootstrapError(f"{label} compressed budget exceeded: {path.name}")
            directory = Path(tempfile.mkdtemp(prefix="e2-bootstrap-artifact-"))
            directory.chmod(0o700)
            target = directory / "artifact"
            snapshot = ArtifactSnapshot(directory, target)
            target_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            digest = hashlib.sha256()
            copied_bytes = 0
            with os.fdopen(target_descriptor, "wb") as copied:
                while chunk := handle.read(1024 * 1024):
                    copied_bytes += len(chunk)
                    if copied_bytes > maximum:
                        raise BootstrapError(f"{label} compressed budget exceeded: {path.name}")
                    digest.update(chunk)
                    copied.write(chunk)
            after = os.fstat(handle.fileno())
    except OSError as error:
        if snapshot is not None:
            snapshot.cleanup()
        raise BootstrapError(f"missing regular {label}: {path.name}") from error
    except BootstrapError:
        if snapshot is not None:
            snapshot.cleanup()
        raise
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        if snapshot is not None:
            snapshot.cleanup()
        raise BootstrapError(f"{label} changed while being read: {path.name}")
    if snapshot is None:
        raise BootstrapError(f"missing regular {label}: {path.name}")
    try:
        snapshot.path.chmod(0o400)
    except OSError as error:
        snapshot.cleanup()
        raise BootstrapError(f"unable to secure {label} snapshot") from error
    return snapshot, "sha256:" + digest.hexdigest()


def _classify_platform(system: str, machine: str, gnu_libc: str | None) -> str:
    system = system.lower()
    machine = machine.lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "darwin-arm64"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        if not gnu_libc or not gnu_libc.startswith("glibc "):
            raise BootstrapError("Linux x86_64 bootstrap requires proven GNU libc")
        return "linux-x86_64-gnu"
    raise BootstrapError(f"unsupported bootstrap platform: {system}/{machine}")


def _host_platform() -> str:
    gnu_libc = None
    if sys.platform.startswith("linux"):
        try:
            gnu_libc = os.confstr("CS_GNU_LIBC_VERSION")
        except (AttributeError, OSError, ValueError):
            pass
    return _classify_platform(sys.platform, platform.machine(), gnu_libc)


def _entry(manifest: dict[str, object], key: str, selected: str) -> dict[str, str]:
    values = manifest.get(key)
    if not isinstance(values, list):
        raise BootstrapError(f"{key} must be a list")
    entries = [value for value in values if isinstance(value, dict) and value.get("platform") == selected]
    if len(entries) != 1:
        noun = "uv" if key == "uvArchives" else "zstandard"
        raise BootstrapError(f"exactly one {noun} artifact is required for {selected}")
    return {str(field): str(value) for field, value in entries[0].items()}


def _approved_entry(entry: dict[str, str], approved: tuple[str, str], *, kind: str) -> None:
    filename, digest = approved
    if entry.get("filename") != filename:
        raise BootstrapError(f"{kind} filename does not match approved release identity")
    if entry.get("sha256") != digest:
        raise BootstrapError(f"{kind} digest does not match approved release identity")
    if entry.get("version") != ("0.11.15" if kind == "uv" else "0.23.0"):
        raise BootstrapError(f"{kind} version must be {'0.11.15' if kind == 'uv' else '0.23.0'}")


def _all_entries(
    root: Path,
    manifest: dict[str, object],
    key: str,
    approved: dict[str, tuple[str, str]],
    snapshots: list[ArtifactSnapshot],
    *,
    kind: str,
) -> dict[str, tuple[dict[str, str], ArtifactSnapshot]]:
    checked: dict[str, tuple[dict[str, str], ArtifactSnapshot]] = {}
    for selected, identity in approved.items():
        entry = _entry(manifest, key, selected)
        _approved_entry(entry, identity, kind=kind)
        snapshot = _artifact(root, entry, kind=kind)
        snapshots.append(snapshot)
        checked[selected] = (entry, snapshot)
    return checked


def _artifact(root: Path, entry: dict[str, str], *, kind: str) -> ArtifactSnapshot:
    filename = entry["filename"]
    path = root / "artifacts" / filename
    try:
        path.resolve(strict=False).relative_to((root / "artifacts").resolve())
    except ValueError as error:
        raise BootstrapError(f"{kind} filename escapes artifacts directory") from error
    snapshot, digest = _snapshot_file(path, label="artifact", maximum=DEFAULT_LIMITS.compressed_bytes)
    if digest != entry["sha256"]:
        snapshot.cleanup()
        raise BootstrapError(f"{kind} digest mismatch: {filename}")
    return snapshot


def _check_expanded(total: int, compressed: int, limits: ArchiveLimits, *, label: str) -> int:
    if total > limits.expanded_bytes:
        raise BootstrapError(f"{label} expanded budget exceeded")
    if compressed == 0 or total > compressed * limits.ratio:
        raise BootstrapError(f"{label} compression ratio exceeded")
    return total


def _inspect_zip(path: Path, limits: ArchiveLimits) -> list[zipfile.ZipInfo]:
    compressed = _regular_size(path, label="artifact")
    if compressed > limits.compressed_bytes:
        raise BootstrapError(f"wheel compressed budget exceeded: {path.name}")
    try:
        with zipfile.ZipFile(path) as wheel:
            infos = wheel.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise BootstrapError("wheel metadata is invalid") from error
    if len(infos) > limits.members:
        raise BootstrapError("wheel member budget exceeded")
    total = 0
    for info in infos:
        if info.file_size < 0 or info.file_size > limits.member_bytes:
            raise BootstrapError("wheel member budget exceeded")
        total = _check_expanded(total + info.file_size, compressed, limits, label="wheel")
    return infos


def _member_bytes(wheel: zipfile.ZipFile, name: str, infos: dict[str, zipfile.ZipInfo]) -> bytes:
    info = infos[name]
    if info.file_size > 1024 * 1024:
        raise BootstrapError("wheel metadata member is too large")
    with wheel.open(info) as handle:
        return handle.read()


def _member_digest(wheel: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with wheel.open(info) as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii"), size


def _wheel_is_valid(path: Path, entry: dict[str, str], limits: ArchiveLimits = DEFAULT_LIMITS) -> None:
    try:
        infos_list = _inspect_zip(path, limits)
        with zipfile.ZipFile(path) as wheel:
            names = [info.filename for info in infos_list]
            if len(names) != len(set(names)):
                raise BootstrapError("wheel has duplicate members")
            infos = {info.filename: info for info in infos_list}
            prefix = "zstandard-0.23.0.dist-info/"
            metadata = _member_bytes(wheel, prefix + "METADATA", infos).decode("utf-8")
            wheel_metadata = _member_bytes(wheel, prefix + "WHEEL", infos).decode("utf-8")
            record = _member_bytes(wheel, prefix + "RECORD", infos).decode("utf-8")
            if "Name: zstandard\n" not in metadata or "Version: 0.23.0\n" not in metadata:
                raise BootstrapError("wheel metadata name or version is invalid")
            _, _, python_tag, abi_tag, platform_tags = entry["filename"].removesuffix(".whl").split("-", 4)
            expected_tags = {f"{python_tag}-{abi_tag}-{platform_tag}" for platform_tag in platform_tags.split(".")}
            actual_tags = {
                line.removeprefix("Tag: ") for line in wheel_metadata.splitlines() if line.startswith("Tag: ")
            }
            if not expected_tags <= actual_tags:
                raise BootstrapError("wheel tag does not match filename")
            records = list(csv.reader(record.splitlines()))
            expected_names = {name for name in names if not name.endswith("/")}
            record_names: set[str] = set()
            for row in records:
                if len(row) != 3 or not row[0] or row[0] in record_names:
                    raise BootstrapError("wheel RECORD row is invalid")
                record_names.add(row[0])
            if record_names != expected_names:
                raise BootstrapError("wheel RECORD does not list every member")
            for row in records:
                filename, hash_value, expected_size = row
                if filename == prefix + "RECORD":
                    if hash_value or expected_size:
                        raise BootstrapError("wheel RECORD row is invalid")
                    continue
                try:
                    algorithm, encoded = hash_value.split("=", 1)
                except ValueError as error:
                    raise BootstrapError("wheel RECORD row is invalid") from error
                if algorithm != "sha256" or not encoded or not expected_size.isdigit():
                    raise BootstrapError("wheel RECORD entry is invalid")
                actual, actual_size = _member_digest(wheel, infos[filename])
                if actual != encoded or actual_size != int(expected_size):
                    raise BootstrapError("wheel RECORD digest mismatch")
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        raise BootstrapError("wheel metadata is invalid") from error


def _inspect_tar(path: Path, limits: ArchiveLimits) -> None:
    compressed = _regular_size(path, label="artifact")
    if compressed > limits.compressed_bytes:
        raise BootstrapError(f"uv compressed budget exceeded: {path.name}")
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = 0
            total = 0
            candidates = 0
            for member in archive:
                members += 1
                if members > limits.members or member.size < 0 or member.size > limits.member_bytes:
                    raise BootstrapError("uv member budget exceeded")
                total = _check_expanded(total + member.size, compressed, limits, label="uv")
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise BootstrapError("uv archive must contain regular files and directories only")
                if member.isfile() and Path(member.name).name == "uv":
                    candidates += 1
            if candidates != 1:
                raise BootstrapError("uv archive must contain one regular uv executable")
    except (OSError, tarfile.TarError) as error:
        raise BootstrapError("uv archive is invalid") from error


def _extract_uv(snapshot: ArtifactSnapshot, limits: ArchiveLimits = DEFAULT_LIMITS) -> Path:
    _inspect_tar(snapshot.path, limits)
    directory: Path | None = None
    destination: Path | None = None
    try:
        with tarfile.open(snapshot.path, mode="r:gz") as archive:
            candidate = next(member for member in archive if member.isfile() and Path(member.name).name == "uv")
            source = archive.extractfile(candidate)
            if source is None:
                raise BootstrapError("uv archive executable is unavailable")
            directory = Path(tempfile.mkdtemp(prefix="e2-bootstrap-executable-"))
            directory.chmod(0o700)
            destination = directory / "uv"
            with destination.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
            destination.chmod(0o555)
            metadata = destination.stat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o555 or metadata.st_nlink != 1:
                raise BootstrapError("extracted uv executable identity is invalid")
            return destination
    except (OSError, StopIteration, tarfile.TarError) as error:
        if destination is not None:
            destination.unlink(missing_ok=True)
        if directory is not None:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise BootstrapError("uv archive is invalid") from error
    except BootstrapError:
        if destination is not None:
            destination.unlink(missing_ok=True)
        if directory is not None:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _cleanup_executable(executable: Path) -> None:
    executable.unlink(missing_ok=True)
    try:
        executable.parent.rmdir()
    except OSError:
        pass


def _uv_environment() -> dict[str, str]:
    return {
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "UV_OFFLINE": "1",
        "UV_CONFIG_FILE": os.devnull,
    }


def _verify_version(executable: Path, expected: str) -> None:
    result = subprocess.run([executable, "--version"], env=_uv_environment(), capture_output=True, text=True)
    if result.returncode != 0 or result.stdout.strip() != expected:
        raise BootstrapError("uv version/build identity does not match manifest")


def verify(manifest_path: Path) -> tuple[Path, str]:
    root = manifest_path.parent
    raw_manifest = _regular_bytes(manifest_path, label="manifest")
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError as error:
        raise BootstrapError("manifest is not valid JSON") from error
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != "BootstrapArtifactsV1":
        raise BootstrapError("manifest schemaVersion must be BootstrapArtifactsV1")
    snapshots: list[ArtifactSnapshot] = []
    executable: Path | None = None
    try:
        selected = _host_platform()
        checked_uv = _all_entries(root, manifest, "uvArchives", UV_ARCHIVES, snapshots, kind="uv")
        checked_wheels = _all_entries(root, manifest, "zstandardWheels", WHEELS, snapshots, kind="zstandard")
        uv, uv_snapshot = checked_uv[selected]
        config = manifest.get("emptyConfig")
        if not isinstance(config, dict) or config.get("filename") != "uv-empty.toml" or config.get("sha256") != EMPTY_CONFIG_SHA256:
            raise BootstrapError("empty config identity is invalid")
        config_bytes = _regular_bytes(root / "uv-empty.toml", label="empty config")
        if config_bytes:
            raise BootstrapError("empty config must be zero bytes")
        if _sha256(config_bytes) != EMPTY_CONFIG_SHA256:
            raise BootstrapError("empty config digest mismatch")
        for entry, wheel_snapshot in checked_wheels.values():
            _wheel_is_valid(wheel_snapshot.path, entry)
        executable = _extract_uv(uv_snapshot)
        _verify_version(executable, uv.get("versionOutput", ""))
        return executable, _sha256(raw_manifest)
    except BaseException:
        if executable is not None:
            _cleanup_executable(executable)
        raise
    finally:
        for snapshot in snapshots:
            snapshot.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if not args.offline:
        raise BootstrapError("--offline is required")
    if any(value for key, value in os.environ.items() if key.upper() in PROXY_VARIABLES and value):
        raise BootstrapError("proxy environment is forbidden")
    executable, manifest_digest = verify(args.manifest)
    print(f"BOOTSTRAP_UV={executable}")
    print(f"MANIFEST_DIGEST={manifest_digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapError as error:
        print(f"bootstrap verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
