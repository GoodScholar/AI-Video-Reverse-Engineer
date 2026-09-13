import json
import os
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.reference_media import ReferenceImage
from app.reference_media_storage import managed_reference_media_path
from app.reference_video import ReferenceVideo


def write_project_with_media(tmp_path, media, contents):
    now = datetime.now(timezone.utc).isoformat()
    project = {
        "id": "project-001",
        "name": "内容读取",
        "createdAt": now,
        "updatedAt": now,
        "referenceMedia": media.model_dump(),
    }
    (tmp_path / "projects.json").write_text(json.dumps([project]), encoding="utf-8")
    path = managed_reference_media_path(tmp_path, project["id"], media)
    path.parent.mkdir(parents=True)
    path.write_bytes(contents)
    return TestClient(create_app(data_dir=tmp_path)), path


def image_reference(*, image_format="png"):
    return ReferenceImage(
        id="image-001", originalName=f"reference.{image_format}", format=image_format,
        sizeBytes=4, width=256, height=256, hasTransparency=False,
    )


def video_reference(*, video_format="mp4"):
    return ReferenceVideo(
        id="video-001", originalName=f"reference.{video_format}", format=video_format,
        sizeBytes=6, durationSeconds=2.5, width=854, height=480, frameRate=24,
    )


def actual_png_bytes():
    output = BytesIO()
    Image.new("RGB", (1, 1), "red").save(output, format="PNG")
    return output.getvalue()


def test_unified_content_reads_png_with_original_bytes_and_image_mime(tmp_path):
    contents = actual_png_bytes()
    client, _ = write_project_with_media(tmp_path, image_reference(), contents)

    response = client.get("/api/projects/project-001/reference-media/content")

    assert response.status_code == 200
    assert response.content == contents
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"


def test_unified_content_keeps_mp4_range_and_unsatisfiable_range_semantics(tmp_path):
    client, _ = write_project_with_media(tmp_path, video_reference(), b"abcdef")

    partial = client.get(
        "/api/projects/project-001/reference-media/content", headers={"Range": "bytes=1-3"},
    )
    invalid = client.get(
        "/api/projects/project-001/reference-media/content", headers={"Range": "bytes=99-100"},
    )

    assert partial.status_code == 206
    assert partial.content == b"bcd"
    assert partial.headers["content-type"] == "video/mp4"
    assert partial.headers["accept-ranges"] == "bytes"
    assert partial.headers["cache-control"] == "private, no-store"
    assert partial.headers["content-range"] == "bytes 1-3/6"
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */6"


def test_unified_content_uses_quicktime_mime_for_mov(tmp_path):
    client, _ = write_project_with_media(tmp_path, video_reference(video_format="mov"), b"mov")

    response = client.get("/api/projects/project-001/reference-media/content")

    assert response.status_code == 200
    assert response.content == b"mov"
    assert response.headers["content-type"] == "video/quicktime"


def test_legacy_video_content_route_delegates_to_current_video(tmp_path):
    client, _ = write_project_with_media(tmp_path, video_reference(), b"legacy-video")

    response = client.get("/api/projects/project-001/reference-video/content")

    assert response.status_code == 200
    assert response.content == b"legacy-video"
    assert response.headers["content-type"] == "video/mp4"


def test_legacy_video_content_route_does_not_serve_a_current_image(tmp_path):
    client, _ = write_project_with_media(tmp_path, image_reference(), b"image")

    response = client.get("/api/projects/project-001/reference-video/content")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "media_not_found"


def test_unified_content_hides_an_absent_reference_media(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    (tmp_path / "projects.json").write_text(json.dumps([{
        "id": "project-001", "name": "无素材", "createdAt": now, "updatedAt": now,
        "referenceMedia": None,
    }]), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/projects/project-001/reference-media/content")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "media_not_found"


@pytest.mark.parametrize("target", ["missing", "unsafe"])
def test_unified_content_hides_missing_and_unsafe_media_files(tmp_path, target):
    client, path = write_project_with_media(tmp_path, image_reference(), b"image")
    if target == "missing":
        path.unlink()
    else:
        path.unlink()
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"outside")
        path.symlink_to(outside)

    response = client.get("/api/projects/project-001/reference-media/content")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "media_not_found"
    assert str(tmp_path) not in response.text


@pytest.mark.parametrize("target", ["external_file", "external_ancestor"])
def test_unified_content_hides_links_that_escape_the_data_directory(tmp_path, target):
    if not hasattr(os, "symlink"):
        pytest.skip("当前平台不支持符号链接")
    client, path = write_project_with_media(tmp_path, image_reference(), b"image")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    if target == "external_file":
        path.unlink()
        outside.write_bytes(b"outside")
        path.symlink_to(outside)
    else:
        directory = path.parent
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)

    response = TestClient(client.app, raise_server_exceptions=False).get(
        "/api/projects/project-001/reference-media/content",
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "media_not_found"
    assert str(tmp_path) not in response.text
