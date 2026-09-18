import asyncio
import errno
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.depth_capture import DepthQualityAssessment, DepthQualityCheck, new_depth_capture
from app import depth_capture_storage
from app.depth_capture_storage import DEPTH_ARTIFACTS
from app import main
from app.main import create_app
from app.reference_video import ReferenceVideo


def _assessment() -> DepthQualityAssessment:
    return DepthQualityAssessment(status="passed", checks=[
        DepthQualityCheck(
            criterion=criterion,
            status="passed",
            message="通过",
            evidence="测试数据",
            metric=0.0,
            threshold=1.0,
            sampleTimestamps=[],
        )
        for criterion in (
            "completeness", "dynamicRange", "temporalFlicker",
            "directionStability", "edgeContinuity", "timelineAlignment",
        )
    ])


def _write_artifacts(data_dir: Path, project_id: str, capture, *, source: str) -> Path:
    directory = data_dir / "project-files" / project_id / "depth-captures" / capture.id
    directory.mkdir(parents=True)
    for name in DEPTH_ARTIFACTS:
        path = directory / name
        if name == "manifest.json":
            path.write_text(json.dumps({
                "schemaVersion": 1,
                "algorithmVersion": 1,
                "sourceReferenceVideoId": source,
                "modelIdentity": capture.modelIdentity.model_dump(),
                "normalizationDirection": "near_white_far_black",
                "files": list(DEPTH_ARTIFACTS),
            }), encoding="utf-8")
        elif name == "depth-preview.mp4":
            path.write_bytes(b"abcdef")
        else:
            path.write_bytes(b"artifact")
    return directory


def _persist_project(data_dir: Path, project_id: str = "project-001", *, include_capture=True):
    now = datetime.now(timezone.utc).isoformat()
    reference = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=6,
        durationSeconds=2, width=640, height=360, frameRate=24,
    )
    capture = new_depth_capture(reference.id, "auto", now)
    capture.status = "completed"
    capture.executionDevice = "cpu"
    capture.qualityAssessment = _assessment()
    capture.completedAt = now
    capture.updatedAt = now
    for stage in capture.stages:
        stage.status = "completed"
        stage.startedAt = now
        stage.completedAt = now
    payload = {
        "id": project_id,
        "name": "媒体测试",
        "createdAt": now,
        "updatedAt": now,
        "referenceVideo": reference.model_dump(),
        "depthCaptures": [capture.model_dump()] if include_capture else [],
        "activeDepthCaptureId": capture.id if include_capture else None,
    }
    return payload, reference, capture


def _client_with_media(tmp_path):
    project, reference, capture = _persist_project(tmp_path)
    (tmp_path / "projects.json").write_text(json.dumps([project]), encoding="utf-8")
    reference_path = tmp_path / "project-files/project-001/reference-videos/video-001.mp4"
    reference_path.parent.mkdir(parents=True)
    reference_path.write_bytes(b"abcdef")
    artifacts = _write_artifacts(tmp_path, "project-001", capture, source=reference.id)
    return TestClient(create_app(data_dir=tmp_path)), capture, reference_path, artifacts


def _assert_media_headers(response, *, size: int, content_range: Optional[str] = None):
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(size)
    if content_range is not None:
        assert response.headers["content-range"] == content_range


def test_reference_content_streams_full_video_with_private_headers(tmp_path):
    client, _, _, _ = _client_with_media(tmp_path)

    response = client.get("/api/projects/project-001/reference-video/content")

    assert response.status_code == 200
    assert response.content == b"abcdef"
    _assert_media_headers(response, size=6)


@pytest.mark.parametrize(("range_header", "expected", "content_range"), [
    ("bytes=1-3", b"bcd", "bytes 1-3/6"),
    ("bytes=3-", b"def", "bytes 3-5/6"),
    ("bytes=-2", b"ef", "bytes 4-5/6"),
])
def test_reference_content_supports_each_single_range_form(tmp_path, range_header, expected, content_range):
    client, _, _, _ = _client_with_media(tmp_path)

    response = client.get(
        "/api/projects/project-001/reference-video/content",
        headers={"Range": range_header},
    )

    assert response.status_code == 206
    assert response.content == expected
    _assert_media_headers(response, size=len(expected), content_range=content_range)


def test_depth_preview_streams_only_a_valid_completed_current_capture(tmp_path):
    client, capture, _, _ = _client_with_media(tmp_path)

    response = client.get(
        f"/api/projects/project-001/depth-captures/{capture.id}/preview",
        headers={"Range": "bytes=0-0"},
    )

    assert response.status_code == 206
    assert response.content == b"a"
    _assert_media_headers(response, size=1, content_range="bytes 0-0/6")


def test_depth_video_streams_valid_grayscale_control_with_range_and_optional_download(tmp_path):
    client, capture, _, _ = _client_with_media(tmp_path)

    inline = client.get(
        f"/api/projects/project-001/depth-captures/{capture.id}/video",
        headers={"Range": "bytes=1-3"},
    )
    download = client.get(f"/api/projects/project-001/depth-captures/{capture.id}/video?download=true")

    assert inline.status_code == 206
    assert inline.content == b"rti"
    _assert_media_headers(inline, size=3, content_range="bytes 1-3/8")
    assert "content-disposition" not in inline.headers
    assert download.status_code == 200
    assert download.headers["content-disposition"] == f'attachment; filename="depth-{capture.id}.mp4"'


@pytest.mark.parametrize("range_header", [
    "bytes=", "bytes=-", "bytes=3-1", "bytes=6-", "bytes=0-0,2-3", "items=0-1",
])
def test_reference_content_rejects_invalid_or_unsatisfiable_ranges(tmp_path, range_header):
    client, _, _, _ = _client_with_media(tmp_path)

    response = client.get(
        "/api/projects/project-001/reference-video/content",
        headers={"Range": range_header},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */6"


def test_zero_and_one_byte_media_handle_range_boundaries(tmp_path):
    client, _, reference_path, _ = _client_with_media(tmp_path)
    reference_path.write_bytes(b"")

    full = client.get("/api/projects/project-001/reference-video/content")
    empty_range = client.get(
        "/api/projects/project-001/reference-video/content", headers={"Range": "bytes=0-0"},
    )

    assert full.status_code == 200
    assert full.content == b""
    _assert_media_headers(full, size=0)
    assert empty_range.status_code == 416
    assert empty_range.headers["content-range"] == "bytes */0"

    reference_path.write_bytes(b"x")
    one = client.get(
        "/api/projects/project-001/reference-video/content", headers={"Range": "bytes=-1"},
    )
    assert one.status_code == 206
    assert one.content == b"x"
    _assert_media_headers(one, size=1, content_range="bytes 0-0/1")


def test_capture_from_another_project_is_not_exposed(tmp_path):
    project_one, _, _ = _persist_project(tmp_path, "project-001", include_capture=False)
    project_two, reference_two, capture_two = _persist_project(tmp_path, "project-002")
    (tmp_path / "projects.json").write_text(json.dumps([project_one, project_two]), encoding="utf-8")
    _write_artifacts(tmp_path, "project-002", capture_two, source=reference_two.id)
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get(f"/api/projects/project-001/depth-captures/{capture_two.id}/preview")

    assert response.status_code == 404


@pytest.mark.parametrize("mutation", ["stale", "not_completed", "invalid_manifest"])
def test_depth_preview_hides_stale_incomplete_or_invalid_capture(tmp_path, mutation):
    client, capture, _, artifacts = _client_with_media(tmp_path)
    payload = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    if mutation == "stale":
        payload[0]["depthCaptures"][0]["sourceReferenceVideoId"] = "video-other"
    elif mutation == "not_completed":
        payload[0]["depthCaptures"][0]["status"] = "running"
    else:
        (artifacts / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "projects.json").write_text(json.dumps(payload), encoding="utf-8")

    response = client.get(f"/api/projects/project-001/depth-captures/{capture.id}/preview")

    assert response.status_code == 404


@pytest.mark.parametrize("target", ["reference_file", "reference_parent", "preview_file", "preview_parent"])
def test_media_hides_symlinked_file_or_managed_ancestor(tmp_path, target):
    client, capture, reference_path, artifacts = _client_with_media(tmp_path)
    if target == "reference_file":
        reference_path.unlink()
        reference_path.symlink_to(tmp_path / "elsewhere.mp4")
        url = "/api/projects/project-001/reference-video/content"
    elif target == "reference_parent":
        directory = reference_path.parent
        replacement = tmp_path / "reference-videos-real"
        directory.rename(replacement)
        directory.symlink_to(replacement, target_is_directory=True)
        url = "/api/projects/project-001/reference-video/content"
    elif target == "preview_file":
        preview = artifacts / "depth-preview.mp4"
        preview.unlink()
        preview.symlink_to(tmp_path / "elsewhere.mp4")
        url = f"/api/projects/project-001/depth-captures/{capture.id}/preview"
    else:
        directory = artifacts.parent
        replacement = tmp_path / "depth-captures-real"
        directory.rename(replacement)
        directory.symlink_to(replacement, target_is_directory=True)
        url = f"/api/projects/project-001/depth-captures/{capture.id}/preview"

    response = client.get(url)

    assert response.status_code == 404
    assert str(tmp_path) not in response.text


def test_missing_media_and_media_errors_do_not_expose_absolute_paths(tmp_path):
    client, capture, reference_path, _ = _client_with_media(tmp_path)
    reference_path.unlink()

    reference = client.get("/api/projects/project-001/reference-video/content")
    missing_capture = client.get("/api/projects/project-001/depth-captures/not-found/preview")
    missing_project = client.get(f"/api/projects/not-found/depth-captures/{capture.id}/preview")

    for response in (reference, missing_capture, missing_project):
        assert response.status_code == 404
        assert str(tmp_path) not in response.text


def test_media_streaming_never_uses_path_read_bytes(tmp_path, monkeypatch):
    client, _, _, _ = _client_with_media(tmp_path)

    def whole_file_read_is_forbidden(_self):
        raise AssertionError("媒体路由不得整文件读入内存")

    monkeypatch.setattr(Path, "read_bytes", whole_file_read_is_forbidden)
    response = client.get("/api/projects/project-001/reference-video/content")

    assert response.status_code == 200
    assert response.content == b"abcdef"


def test_depth_preview_keeps_the_validated_inode_when_path_is_replaced(tmp_path, monkeypatch):
    client, capture, _, artifacts = _client_with_media(tmp_path)
    original_open = main.open_validated_depth_preview

    def open_then_replace(**kwargs):
        descriptor, size = original_open(**kwargs)
        replacement = artifacts / "replacement.mp4"
        replacement.write_bytes(b"untrusted")
        os.replace(replacement, artifacts / "depth-preview.mp4")
        return descriptor, size

    monkeypatch.setattr(main, "open_validated_depth_preview", open_then_replace, raising=False)

    response = client.get(f"/api/projects/project-001/depth-captures/{capture.id}/preview")

    assert response.status_code == 200
    assert response.content == b"abcdef"


@pytest.mark.parametrize("failure", [
    PermissionError(errno.EACCES, "denied"),
    OSError(errno.EIO, "io failure"),
    OSError(errno.EMFILE, "descriptor limit"),
])
def test_depth_preview_maps_genuine_storage_open_failure_to_503_without_paths(tmp_path, monkeypatch, failure):
    client, capture, _, _ = _client_with_media(tmp_path)
    original_open = depth_capture_storage.os.open

    def unavailable(path, *args, **kwargs):
        if path == "depth-preview.mp4":
            raise failure
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(depth_capture_storage.os, "open", unavailable)
    response = client.get(f"/api/projects/project-001/depth-captures/{capture.id}/preview")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert str(tmp_path) not in response.text


def test_managed_media_stream_closes_before_first_read_and_after_exhaustion(tmp_path):
    path = tmp_path / "stream.mp4"
    path.write_bytes(b"abc")
    unopened_descriptor = os.open(path, os.O_RDONLY)
    unopened = main.ManagedMediaStream(unopened_descriptor, 0, 3)
    asyncio.run(unopened.aclose())
    asyncio.run(unopened.aclose())
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(unopened_descriptor)

    descriptor = os.open(path, os.O_RDONLY)
    stream = main.ManagedMediaStream(descriptor, 0, 3)

    async def consume():
        return b"".join([chunk async for chunk in stream])

    assert asyncio.run(consume()) == b"abc"
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptor)


def test_managed_media_stream_closes_after_read_error(tmp_path, monkeypatch):
    path = tmp_path / "stream.mp4"
    path.write_bytes(b"abc")
    descriptor = os.open(path, os.O_RDONLY)
    stream = main.ManagedMediaStream(descriptor, 0, 3)

    def broken_read(*_args):
        raise OSError(errno.EIO, "read failed")

    monkeypatch.setattr(main.os, "read", broken_read)

    async def fail_once():
        with pytest.raises(OSError, match="read failed"):
            await stream.__anext__()

    asyncio.run(fail_once())
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptor)


@pytest.mark.parametrize("route", ["reference", "depth"])
def test_media_lseek_storage_error_is_a_safe_503_and_closes_descriptor(tmp_path, monkeypatch, route):
    client, capture, _, _ = _client_with_media(tmp_path)
    observed_descriptors = []

    def broken_seek(descriptor, *_args):
        observed_descriptors.append(descriptor)
        raise OSError(errno.EIO, "seek failed")

    monkeypatch.setattr(main.os, "lseek", broken_seek)
    url = (
        "/api/projects/project-001/reference-video/content"
        if route == "reference"
        else f"/api/projects/project-001/depth-captures/{capture.id}/preview"
    )
    safe_client = TestClient(client.app, raise_server_exceptions=False)

    response = safe_client.get(url)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert str(tmp_path) not in response.text
    assert len(observed_descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(observed_descriptors[0])
