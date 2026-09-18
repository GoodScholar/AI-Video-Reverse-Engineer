"""真实媒体验证：规范化必须使用显示方向与 PTS，而不是帧序号。"""
import json
import shutil
import subprocess

import pytest
from PIL import Image

from app.depth_capture_runner import _normalize_input_timeline


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="需要本地 FFmpeg 和 FFprobe",
)


def encode(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def video_stream(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=width,height,avg_frame_rate,nb_read_frames,duration:stream_side_data=rotation",
        "-of", "json", str(path),
    ], text=True))["streams"][0]


def pixel_at(path, seconds):
    data = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(path),
        "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    assert len(data) == 3
    return tuple(data)


def test_depth_normalization_preserves_vfr_event_times(tmp_path):
    for color in ("red", "green", "blue"):
        Image.new("RGB", (160, 120), color).save(tmp_path / f"{color}.png")
    playlist = tmp_path / "frames.txt"
    playlist.write_text("file 'red.png'\nduration 0.25\nfile 'green.png'\nduration 0.75\nfile 'blue.png'\nduration 1.0\nfile 'blue.png'\n")
    source, output = tmp_path / "vfr.mp4", tmp_path / "cfr.mp4"
    encode("-f", "concat", "-safe", "0", "-i", playlist, "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", source)
    _normalize_input_timeline(source, output, 8, 640, "ffmpeg", subprocess.run)
    stream = video_stream(output)
    assert stream["avg_frame_rate"] == "8/1"
    assert int(stream["nb_read_frames"]) == 16
    assert float(stream["duration"]) == pytest.approx(2.0)
    for timestamp, channel in ((0.125, 0), (0.625, 1), (1.5, 2)):
        pixel = pixel_at(output, timestamp)
        assert pixel[channel] > max(pixel[index] for index in range(3) if index != channel) + 60


def test_depth_normalization_applies_rotation_and_removes_rotation_metadata(tmp_path):
    source, rotated, output = (tmp_path / name for name in ("source.mp4", "rotated.mp4", "cfr.mp4"))
    encode("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=8:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", source)
    encode("-display_rotation", "90", "-i", source, "-c", "copy", rotated)
    assert any(abs(item.get("rotation", 0)) == 90 for item in video_stream(rotated).get("side_data_list", []))
    _normalize_input_timeline(rotated, output, 8, 640, "ffmpeg", subprocess.run)
    stream = video_stream(output)
    assert (stream["width"], stream["height"]) == (480, 640)
    assert int(stream["nb_read_frames"]) == 16
    assert all(item.get("rotation", 0) == 0 for item in stream.get("side_data_list", []))
