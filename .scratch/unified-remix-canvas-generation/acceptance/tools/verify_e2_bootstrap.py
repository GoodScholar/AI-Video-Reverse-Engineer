#!/usr/bin/env python3
"""Fail-closed verifier for the audited E2 bootstrap inputs."""

from __future__ import annotations

import argparse
import base64
import csv
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


class BootstrapError(RuntimeError):
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


def _host_platform() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "darwin-arm64"
    if sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "linux-x86_64-gnu"
    raise BootstrapError(f"unsupported bootstrap platform: {sys.platform}/{machine}")


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
    root: Path, manifest: dict[str, object], key: str, approved: dict[str, tuple[str, str]], *, kind: str
) -> dict[str, tuple[dict[str, str], bytes]]:
    checked: dict[str, tuple[dict[str, str], bytes]] = {}
    for selected, identity in approved.items():
        entry = _entry(manifest, key, selected)
        _approved_entry(entry, identity, kind=kind)
        checked[selected] = (entry, _artifact(root, entry, kind=kind))
    return checked


def _artifact(root: Path, entry: dict[str, str], *, kind: str) -> bytes:
    filename = entry["filename"]
    path = root / "artifacts" / filename
    try:
        path.resolve(strict=False).relative_to((root / "artifacts").resolve())
    except ValueError as error:
        raise BootstrapError(f"{kind} filename escapes artifacts directory") from error
    data = _regular_bytes(path, label="artifact")
    if _sha256(data) != entry["sha256"]:
        raise BootstrapError(f"{kind} digest mismatch: {filename}")
    return data


def _wheel_is_valid(data: bytes, entry: dict[str, str]) -> None:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as wheel:
            names = wheel.namelist()
            if len(names) != len(set(names)):
                raise BootstrapError("wheel has duplicate members")
            prefix = "zstandard-0.23.0.dist-info/"
            metadata = wheel.read(prefix + "METADATA").decode("utf-8")
            wheel_metadata = wheel.read(prefix + "WHEEL").decode("utf-8")
            record = wheel.read(prefix + "RECORD").decode("utf-8")
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
            if {row[0] for row in records if row} != {name for name in names if not name.endswith("/")}:
                raise BootstrapError("wheel RECORD does not list every member")
            for row in records:
                if len(row) != 3 or row[0] == prefix + "RECORD":
                    continue
                algorithm, encoded = row[1].split("=", 1)
                if algorithm != "sha256" or not row[2].isdigit():
                    raise BootstrapError("wheel RECORD entry is invalid")
                content = wheel.read(row[0])
                actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
                if actual != encoded or len(content) != int(row[2]):
                    raise BootstrapError("wheel RECORD digest mismatch")
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        raise BootstrapError("wheel metadata is invalid") from error


def _extract_uv(data: bytes) -> Path:
    try:
        with tarfile.open(fileobj=__import__("io").BytesIO(data), mode="r:gz") as archive:
            candidates = [member for member in archive.getmembers() if member.isfile() and Path(member.name).name == "uv"]
            if len(candidates) != 1 or archive.getmembers() != [member for member in archive.getmembers() if not member.issym() and not member.islnk()]:
                raise BootstrapError("uv archive must contain one regular uv executable and no links")
            source = archive.extractfile(candidates[0])
            if source is None:
                raise BootstrapError("uv archive executable is unavailable")
            destination = Path(tempfile.mkdtemp(prefix="e2-bootstrap-")) / "uv"
            destination.write_bytes(source.read())
            destination.chmod(0o555)
            return destination
    except (OSError, tarfile.TarError) as error:
        raise BootstrapError("uv archive is invalid") from error


def _verify_version(executable: Path, expected: str) -> None:
    environment = {key: value for key, value in os.environ.items() if key.upper() not in PROXY_VARIABLES}
    environment.update({"UV_OFFLINE": "1", "UV_CONFIG_FILE": os.devnull})
    result = subprocess.run([executable, "--version"], env=environment, capture_output=True, text=True)
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
    selected = _host_platform()
    checked_uv = _all_entries(root, manifest, "uvArchives", UV_ARCHIVES, kind="uv")
    checked_wheels = _all_entries(root, manifest, "zstandardWheels", WHEELS, kind="zstandard")
    uv, uv_bytes = checked_uv[selected]
    config = manifest.get("emptyConfig")
    if not isinstance(config, dict) or config.get("filename") != "uv-empty.toml" or config.get("sha256") != EMPTY_CONFIG_SHA256:
        raise BootstrapError("empty config identity is invalid")
    config_bytes = _regular_bytes(root / "uv-empty.toml", label="empty config")
    if config_bytes:
        raise BootstrapError("empty config must be zero bytes")
    if _sha256(config_bytes) != EMPTY_CONFIG_SHA256:
        raise BootstrapError("empty config digest mismatch")
    for entry, wheel_data in checked_wheels.values():
        _wheel_is_valid(wheel_data, entry)
    executable = _extract_uv(uv_bytes)
    _verify_version(executable, uv.get("versionOutput", ""))
    return executable, _sha256(raw_manifest)


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
