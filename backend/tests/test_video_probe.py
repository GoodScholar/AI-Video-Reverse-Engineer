import dataclasses
import json
import shutil
import subprocess

import pytest

from app.reference_video import (
    MAX_REFERENCE_VIDEO_BYTES,
    ProbedReferenceVideo,
    ReferenceVideo,
    ReferenceVideoError,
    probe_reference_video,
    reference_video_format_from_name,
)


def test_reference_video_serializes_the_video_discriminator():
    video = ReferenceVideo(
        id="video-001", originalName="clip.mp4", format="mp4", sizeBytes=10,
        durationSeconds=2.5, width=854, height=480, frameRate=24,
    )

    assert video.model_dump()["type"] == "video"


def completed_probe(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )


def valid_probe_payload(
    *,
    width: int = 854,
    height: int = 480,
    duration: str = "2.500000",
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
    frames: str = "60",
    avg_frame_rate: str = "24/1",
    r_frame_rate: str = "24/1",
) -> dict:
    return {
        "format": {"format_name": format_name, "duration": duration},
        "streams": [
            {
                "codec_type": "video",
                "width": width,
                "height": height,
                "duration": duration,
                "avg_frame_rate": avg_frame_rate,
                "r_frame_rate": r_frame_rate,
                "nb_read_frames": frames,
                "disposition": {"attached_pic": 0},
                "side_data_list": [],
            }
        ],
    }


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="真实媒体集成测试需要 ffmpeg 和 ffprobe",
)
def test_real_ffprobe_accepts_a_two_second_480p_mp4(tmp_path):
    sample = tmp_path / "valid-2s-480p.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x343938:s=854x480:r=24",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(sample),
        ],
        check=True,
        capture_output=True,
    )

    facts = probe_reference_video(
        sample,
        sample.name,
        sample.stat().st_size,
        ffprobe_path="ffprobe",
        timeout_seconds=30.0,
    )

    assert facts.format == "mp4"
    assert facts.duration_seconds == pytest.approx(2.0, abs=0.01)
    assert (facts.width, facts.height) == (854, 480)
    assert facts.frame_rate == pytest.approx(24.0)


def test_probe_keeps_filename_hint_when_ffprobe_has_no_major_brand(tmp_path, monkeypatch):
    path = tmp_path / "portrait.mov"
    path.write_bytes(b"video")
    payload = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.500000"},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30/1",
                "nb_read_frames": "75",
                "disposition": {"attached_pic": 0},
                "side_data_list": [{"rotation": -90}],
            }
        ],
    }
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(
        path,
        "portrait.MOV",
        5,
        ffprobe_path="ffprobe",
        timeout_seconds=30.0,
    )

    assert facts.format == "mov"
    assert (facts.width, facts.height) == (1080, 1920)
    assert facts.duration_seconds == 2.5
    assert facts.frame_rate == pytest.approx(29.97002997)


@pytest.mark.parametrize(
    ("major_brand", "filename", "expected_format"),
    [("isom", "clip.mov", "mp4"), ("qt  ", "clip.mp4", "mov")],
)
def test_probe_uses_ffprobe_major_brand_as_authoritative_container_identity(
    tmp_path, monkeypatch, major_brand, filename, expected_format,
):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    payload = valid_probe_payload()
    payload["format"]["tags"] = {"major_brand": major_brand}
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(path, filename, 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert facts.format == expected_format


@pytest.mark.parametrize(
    ("width", "height", "duration"),
    [
        (854, 480, "2.000000"),
        (3840, 2160, "10.000000"),
        (2160, 3840, "2.000000"),
        (2160, 2160, "2.000000"),
    ],
)
def test_probe_accepts_inclusive_duration_and_resolution_edges(
    tmp_path, monkeypatch, width, height, duration
):
    path = tmp_path / "edge.mp4"
    path.write_bytes(b"video")
    payload = valid_probe_payload(width=width, height=height, duration=duration)
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(
        path,
        "edge.mp4",
        5,
        ffprobe_path="ffprobe",
        timeout_seconds=30.0,
    )

    assert (facts.width, facts.height) == (width, height)
    assert facts.duration_seconds == float(duration)


@pytest.mark.parametrize(
    ("filename", "payload", "expected_status", "expected_code", "measured"),
    [
        ("clip.avi", valid_probe_payload(), 415, "unsupported_video_format", ".avi"),
        (
            "clip.mp4",
            valid_probe_payload(format_name="matroska,webm"),
            415,
            "unsupported_video_format",
            "matroska",
        ),
        ("clip.mp4", valid_probe_payload(duration="1.99"), 422, "video_too_short", "1.99"),
        ("clip.mp4", valid_probe_payload(duration="10.01"), 422, "video_too_long", "10.01"),
        (
            "clip.mp4",
            valid_probe_payload(width=854, height=479),
            422,
            "video_resolution_too_low",
            "854×479",
        ),
        (
            "clip.mp4",
            valid_probe_payload(width=3841, height=2160),
            422,
            "video_resolution_too_high",
            "3841×2160",
        ),
        (
            "clip.mp4",
            valid_probe_payload(width=2160, height=3841),
            422,
            "video_resolution_too_high",
            "2160×3841",
        ),
        (
            "clip.mp4",
            valid_probe_payload(width=2161, height=2161),
            422,
            "video_resolution_too_high",
            "2161×2161",
        ),
        (
            "clip.mp4",
            valid_probe_payload(frames="0"),
            422,
            "video_unreadable",
            "有效视频帧",
        ),
        (
            "clip.mp4",
            valid_probe_payload(avg_frame_rate="0/0", r_frame_rate="0/0"),
            422,
            "video_frame_rate_invalid",
            "帧率",
        ),
    ],
)
def test_probe_rejects_each_hard_limit(
    tmp_path, monkeypatch, filename, payload, expected_status, expected_code, measured
):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(
            path,
            filename,
            5,
            ffprobe_path="ffprobe",
            timeout_seconds=30.0,
        )

    assert captured.value.status_code == expected_status
    assert captured.value.code == expected_code
    assert measured in captured.value.message


def test_probe_rejects_a_file_larger_than_the_measured_byte_limit(tmp_path, monkeypatch):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: pytest.fail("超限文件不应启动 ffprobe"),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(
            path,
            "clip.mp4",
            MAX_REFERENCE_VIDEO_BYTES + 1,
            ffprobe_path="ffprobe",
            timeout_seconds=30.0,
        )

    assert captured.value.status_code == 413
    assert captured.value.code == "video_too_large"
    assert "200,000,001" in captured.value.message


@pytest.mark.parametrize(
    "payload",
    [
        {"format": {"format_name": "mov,mp4"}, "streams": []},
        {"format": {"format_name": "mov,mp4", "duration": "2"}, "streams": [{}]},
    ],
)
def test_probe_rejects_missing_required_probe_fields(tmp_path, monkeypatch, payload):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert captured.value.status_code == 422
    assert captured.value.code == "video_unreadable"


def test_probe_rejects_nonzero_ffprobe_exit(tmp_path, monkeypatch):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["ffprobe"], returncode=1, stdout="", stderr="invalid media"
        ),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert captured.value.status_code == 422
    assert captured.value.code == "video_unreadable"


def test_probe_rejects_invalid_json(tmp_path, monkeypatch):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["ffprobe"], returncode=0, stdout="not json", stderr=""
        ),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert captured.value.status_code == 422
    assert captured.value.code == "video_unreadable"


def test_probe_rejects_only_attached_picture_streams(tmp_path, monkeypatch):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    payload = valid_probe_payload()
    payload["streams"][0]["disposition"]["attached_pic"] = 1
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert captured.value.status_code == 422
    assert captured.value.code == "video_unreadable"
    assert "有效视频帧" in captured.value.message


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (FileNotFoundError(), 503, "ffprobe_unavailable"),
        (PermissionError(), 503, "ffprobe_unavailable"),
        (subprocess.TimeoutExpired("ffprobe", 2.5), 422, "video_unreadable"),
    ],
)
def test_probe_maps_ffprobe_launch_and_timeout_errors(
    tmp_path, monkeypatch, error, expected_status, expected_code
):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")

    def raise_error(*args, **kwargs):
        raise error

    monkeypatch.setattr("app.reference_video.subprocess.run", raise_error)

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=2.5)

    assert captured.value.status_code == expected_status
    assert captured.value.code == expected_code


def test_probe_falls_back_to_r_frame_rate_when_average_rate_is_invalid(tmp_path, monkeypatch):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    payload = valid_probe_payload(avg_frame_rate="N/A", r_frame_rate="30000/1001")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert facts.frame_rate == pytest.approx(29.97002997)


def test_probe_prioritizes_duration_before_resolution_and_frame_rate(tmp_path, monkeypatch):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    payload = valid_probe_payload(
        width=854,
        height=479,
        duration="1.99",
        avg_frame_rate="0/0",
        r_frame_rate="0/0",
    )
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert captured.value.code == "video_too_short"


def test_probe_prioritizes_rotated_resolution_before_frame_rate(tmp_path, monkeypatch):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    payload = valid_probe_payload(
        width=2160,
        height=3841,
        avg_frame_rate="0/0",
        r_frame_rate="0/0",
    )
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "clip.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert captured.value.code == "video_resolution_too_high"


def test_reference_video_format_from_name_rejects_missing_extension():
    with pytest.raises(ReferenceVideoError) as captured:
        reference_video_format_from_name("clip")

    assert captured.value.status_code == 415
    assert captured.value.code == "unsupported_video_format"
    assert "无扩展名" in captured.value.message


@pytest.mark.parametrize(
    ("side_data_list", "tags_rotate", "expected_size"),
    [
        ([], "90", (480, 854)),
        ([{"rotation": "0"}], "90", (854, 480)),
        ([{"rotation": "N/A"}], "90", (480, 854)),
        ([{"rotation": "N/A"}, {"rotation": "-90"}], "0", (480, 854)),
    ],
)
def test_probe_resolves_rotation_from_side_data_then_tags(
    tmp_path, monkeypatch, side_data_list, tags_rotate, expected_size
):
    path = tmp_path / "rotation.mp4"
    path.write_bytes(b"video")
    payload = valid_probe_payload()
    stream = payload["streams"][0]
    stream["side_data_list"] = side_data_list
    stream["tags"] = {"rotate": tags_rotate}
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(path, "rotation.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert (facts.width, facts.height) == expected_size


@pytest.mark.parametrize(
    ("width", "height", "rotation", "expected_code", "measured"),
    [
        (479, 854, "90", "video_resolution_too_low", "854×479"),
        (3841, 2160, "90", "video_resolution_too_high", "2160×3841"),
    ],
)
def test_probe_applies_rotation_before_resolution_rejection(
    tmp_path, monkeypatch, width, height, rotation, expected_code, measured
):
    path = tmp_path / "rotation-limit.mp4"
    path.write_bytes(b"video")
    payload = valid_probe_payload(width=width, height=height)
    payload["streams"][0]["side_data_list"] = [{"rotation": rotation}]
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(
            path,
            "rotation-limit.mp4",
            5,
            ffprobe_path="ffprobe",
            timeout_seconds=30.0,
        )

    assert captured.value.code == expected_code
    assert measured in captured.value.message


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            valid_probe_payload(format_name="matroska", duration="1.99"),
            "unsupported_video_format",
        ),
        (valid_probe_payload(duration="1.99", frames="0"), "video_unreadable"),
    ],
)
def test_probe_prioritizes_container_and_readability_before_duration(
    tmp_path, monkeypatch, payload, expected_code
):
    path = tmp_path / "priority.mp4"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(path, "priority.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("format_duration", "stream_duration", "expected_duration"),
    [("2.50", "1.00", 2.5), (None, "2.50", 2.5)],
)
def test_probe_uses_format_duration_before_falling_back_to_stream_duration(
    tmp_path, monkeypatch, format_duration, stream_duration, expected_duration
):
    path = tmp_path / "duration.mp4"
    path.write_bytes(b"video")
    payload = valid_probe_payload(duration=stream_duration)
    if format_duration is None:
        del payload["format"]["duration"]
    else:
        payload["format"]["duration"] = format_duration
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(path, "duration.mp4", 5, ffprobe_path="ffprobe", timeout_seconds=30.0)

    assert facts.duration_seconds == expected_duration


def test_probed_reference_video_is_frozen_with_exact_public_fields():
    assert [field.name for field in dataclasses.fields(ProbedReferenceVideo)] == [
        "format",
        "duration_seconds",
        "width",
        "height",
        "frame_rate",
    ]
    facts = ProbedReferenceVideo("mp4", 2.5, 854, 480, 24.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.width = 480


def test_probe_invokes_ffprobe_with_the_full_safe_command_and_options(tmp_path, monkeypatch):
    path = tmp_path / "safe name.mp4"
    path.write_bytes(b"video")
    captured = {}

    def capture_probe(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return completed_probe(valid_probe_payload())

    monkeypatch.setattr("app.reference_video.subprocess.run", capture_probe)

    probe_reference_video(
        path,
        "safe name.mp4",
        5,
        ffprobe_path="/custom/ffprobe",
        timeout_seconds=12.5,
    )

    assert captured["args"] == (
        [
            "/custom/ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
    )
    assert captured["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": 12.5,
        "check": False,
    }


def test_probe_accepts_file_at_the_exact_measured_byte_limit(tmp_path, monkeypatch):
    path = tmp_path / "max.mp4"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(valid_probe_payload()),
    )

    facts = probe_reference_video(
        path,
        "max.mp4",
        MAX_REFERENCE_VIDEO_BYTES,
        ffprobe_path="ffprobe",
        timeout_seconds=30.0,
    )

    assert facts.format == "mp4"
