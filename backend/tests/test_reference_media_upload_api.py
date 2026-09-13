from io import BytesIO
import json
import shutil
import subprocess

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app import main
from app.main import create_app
from app.reference_media import ReferenceMediaError
from app.reference_video import ProbedReferenceVideo
from app.reference_media_storage import detect_reference_video_format, managed_reference_media_path


def valid_video_probe(path, original_name, size_bytes, *, ffprobe_path, timeout_seconds):
    assert path.exists()
    return ProbedReferenceVideo(
        format="mp4",
        duration_seconds=2.5,
        width=854,
        height=480,
        frame_rate=24.0,
    )


def create_project(client):
    response = client.post("/api/projects", json={"name": "统一上传"})
    assert response.status_code == 201
    return response.json()


def png_bytes(*, transparent=False):
    image = Image.new("RGBA", (256, 256), (255, 0, 0, 255))
    if transparent:
        image.putpixel((0, 0), (255, 0, 0, 0))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def png_with_corrupted_idat_crc():
    contents = bytearray(png_bytes())
    idat = contents.index(b"IDAT")
    crc_start = idat + 4 + int.from_bytes(contents[idat - 4:idat], "big")
    contents[crc_start] ^= 0x01
    return bytes(contents)


def upload_media(client, project_id, name, content, content_type="application/octet-stream"):
    return client.put(
        f"/api/projects/{project_id}/reference-media",
        files={"file": (name, content, content_type)},
    )


def test_video_format_hint_is_derived_from_iso_base_media_signature(tmp_path):
    mp4 = tmp_path / "mp4.bin"
    mp4.write_bytes(b"\x00\x00\x00\x14ftypisom\x00\x00\x00\x00isom")
    mov = tmp_path / "mov.bin"
    mov.write_bytes(b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ")

    assert detect_reference_video_format(mp4) == "mp4"
    assert detect_reference_video_format(mov) == "mov"


@pytest.mark.parametrize("brand", [b"3gp4", b"3g2a", b"M4A ", b"zzzz"])
def test_video_format_hint_rejects_non_mp4_mov_iso_bmff_major_brands(tmp_path, brand):
    path = tmp_path / f"{brand.decode('ascii').strip() or 'blank'}.bin"
    path.write_bytes(b"\x00\x00\x00\x14ftyp" + brand + b"\x00\x00\x00\x00isom")

    with pytest.raises(ReferenceMediaError) as captured:
        detect_reference_video_format(path)

    assert captured.value.code == "unsupported_video_format"


def test_video_format_hint_rejects_a_large_structurally_valid_3gp_ftyp(tmp_path):
    box_size = 64 * 1024 + 16
    path = tmp_path / "large-3gp.bin"
    path.write_bytes(
        box_size.to_bytes(4, "big")
        + b"ftyp3gp4\x00\x00\x00\x00"
        + b"isom" * ((box_size - 16) // 4)
    )

    with pytest.raises(ReferenceMediaError) as captured:
        detect_reference_video_format(path)

    assert captured.value.code == "unsupported_video_format"


def test_video_format_hint_rejects_a_truncated_box_after_a_valid_prefix(tmp_path):
    path = tmp_path / "truncated.bin"
    path.write_bytes(b"\x00\x00\x00\x08free\x00\x00\x00\x08")

    with pytest.raises(ReferenceMediaError) as captured:
        detect_reference_video_format(path)

    assert captured.value.code == "unsupported_video_format"


def test_unified_upload_rejects_a_nested_3gp_brand_reported_by_ffprobe(tmp_path, monkeypatch):
    nested_ftyp = b"\x00\x00\x00\x14ftyp3gp4\x00\x00\x00\x00isom"
    content = b"\x00\x00\x00\x08free" + (len(nested_ftyp) + 8).to_bytes(4, "big") + b"moov" + nested_ftyp
    probe_payload = {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "2.500000",
            "tags": {"major_brand": "3gp4"},
        },
        "streams": [{
            "codec_type": "video", "width": 854, "height": 480,
            "nb_read_frames": "60", "avg_frame_rate": "24/1", "r_frame_rate": "24/1",
            "disposition": {"attached_pic": 0}, "side_data_list": [],
        }],
    }
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["ffprobe"], returncode=0, stdout=json.dumps(probe_payload), stderr="",
        ),
    )
    client = TestClient(create_app(data_dir=tmp_path))
    project = create_project(client)

    response = upload_media(client, project["id"], "spoofed.mp4", content, "video/mp4")

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_video_format"
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] is None


def test_unified_route_detects_real_png_ignoring_name_and_mime(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    project = create_project(client)

    response = upload_media(client, project["id"], "misleading.mp4", png_bytes(transparent=True), "video/mp4")

    assert response.status_code == 200
    media = response.json()["referenceMedia"]
    assert media == {
        "type": "image",
        "id": media["id"],
        "originalName": "misleading.mp4",
        "format": "png",
        "sizeBytes": len(png_bytes(transparent=True)),
        "width": 256,
        "height": 256,
        "hasTransparency": True,
    }
    assert managed_reference_media_path(tmp_path, project["id"], media).read_bytes() == png_bytes(transparent=True)


def test_image_limit_is_independent_from_a_test_configured_video_limit(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=8))
    project = create_project(client)

    response = upload_media(client, project["id"], "image.png", png_bytes())

    assert response.status_code == 200
    assert response.json()["referenceMedia"]["type"] == "image"


def test_unified_route_detects_video_without_trusting_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_video_probe)
    client = TestClient(create_app(data_dir=tmp_path))
    project = create_project(client)

    response = upload_media(client, project["id"], "misleading.png", b"not-an-image", "image/png")

    assert response.status_code == 200
    media = response.json()["referenceMedia"]
    assert media["type"] == "video"
    assert media["format"] == "mp4"
    assert media["originalName"] == "misleading.png"
    assert managed_reference_media_path(tmp_path, project["id"], media).read_bytes() == b"not-an-image"


def test_legacy_video_route_is_a_video_only_thin_compatibility_layer(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    project = create_project(client)

    response = client.put(
        f"/api/projects/{project['id']}/reference-video",
        files={"file": ("image.mp4", png_bytes(), "video/mp4")},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "reference_video_required"
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] is None


def test_metadata_write_failure_keeps_old_media_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_video_probe)
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    project = create_project(client)
    first = upload_media(client, project["id"], "first.mp4", b"first")
    assert first.status_code == 200
    old_media = first.json()["referenceMedia"]
    old_path = managed_reference_media_path(tmp_path, project["id"], old_media)

    def fail_write(*args, **kwargs):
        raise OSError("metadata unavailable")

    monkeypatch.setattr(main, "_write_projects", fail_write)
    replacement = upload_media(client, project["id"], "replacement.png", png_bytes())

    assert replacement.status_code == 503
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] == old_media
    assert old_path.read_bytes() == b"first"
    assert list(tmp_path.rglob(".reference-*.part")) == []


def test_unreadable_png_replacement_keeps_old_media_and_cleans_staging(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    project = create_project(client)
    first = upload_media(client, project["id"], "first.png", png_bytes())
    assert first.status_code == 200
    old_media = first.json()["referenceMedia"]
    old_path = managed_reference_media_path(tmp_path, project["id"], old_media)

    replacement = upload_media(client, project["id"], "corrupted.png", png_with_corrupted_idat_crc())

    assert replacement.status_code == 422
    assert replacement.json()["detail"]["code"] == "image_unreadable"
    assert client.get(f"/api/projects/{project['id']}").json()["referenceMedia"] == old_media
    assert old_path.read_bytes() == png_bytes()
    assert list(tmp_path.rglob(".reference-*.part")) == []


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="真实媒体集成测试需要 ffmpeg 和 ffprobe",
)
def test_unified_upload_accepts_mov_without_ftyp_before_a_large_mdat(tmp_path):
    source = tmp_path / "without-ftyp.mov"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=854x480:r=24",
            "-t", "2.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )
    contents = bytearray(source.read_bytes())
    first_ftyp = contents.index(b"ftyp")
    contents[first_ftyp:first_ftyp + 4] = b"free"
    source.write_bytes(contents)
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    project = create_project(client)

    response = upload_media(client, project["id"], source.name, source.read_bytes(), "video/quicktime")

    assert response.status_code == 200
    media = response.json()["referenceMedia"]
    assert media["type"] == "video"
    assert media["format"] == "mov"
    assert media["durationSeconds"] == pytest.approx(2.5, abs=0.01)
    assert (media["width"], media["height"], media["frameRate"]) == (854, 480, 24.0)
