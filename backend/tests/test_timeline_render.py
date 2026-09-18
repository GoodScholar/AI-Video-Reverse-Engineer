import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app.timeline_render import TimelineRenderError, render_timeline


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="真实时间线渲染测试需要本地 ffmpeg 和 ffprobe",
)


def _run(*command):
    subprocess.run(command, check=True, capture_output=True, text=True)


def _video(path: Path, color: str, frequency: int, duration: float = 2.0) -> Path:
    _run(
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=%s:size=64x48:rate=24:duration=%s" % (color, duration),
        "-f", "lavfi", "-i", "sine=frequency=%s:sample_rate=48000:duration=%s" % (frequency, duration),
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


def _audio(path: Path, frequency: int, duration: float = 2.0) -> Path:
    _run(
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=%s:sample_rate=48000:duration=%s" % (frequency, duration),
        "-c:a", "pcm_s16le", str(path),
    )
    return path


def _video_with_short_video_stream(path: Path) -> Path:
    _run(
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=64x48:rate=24:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


def _image(path: Path, color: str) -> Path:
    _run(
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=%s:size=64x48:rate=1" % color,
        "-frames:v", "1", str(path),
    )
    return path


def _clip(asset_id: str, start: float, duration: float, **values):
    clip = {
        "id": asset_id + "-clip", "assetId": asset_id, "start": start,
        "inPoint": 0, "duration": duration, "speed": 1, "volume": 1,
        "fadeIn": 0, "fadeOut": 0,
    }
    clip.update(values)
    return clip


def _timeline(width=64, height=48, fps=24, tracks=()):
    return {"settings": {"width": width, "height": height, "fps": fps}, "tracks": list(tracks)}


def _track(kind: str, clips, **values):
    track = {"id": kind + "-track", "name": kind, "kind": kind, "muted": False, "hidden": False, "clips": clips}
    track.update(values)
    return track


def _probe(path: Path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _rgb_at(path: Path, second: float):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", str(second), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True,
    )
    data = result.stdout
    middle = ((48 // 2) * 64 + (64 // 2)) * 3
    return tuple(data[middle:middle + 3])


def _tone_amplitude(path: Path, frequency: int, start: float, seconds: float) -> float:
    with wave.open(str(path), "rb") as source:
        source.setpos(int(start * source.getframerate()))
        frames = source.readframes(int(seconds * source.getframerate()))
        sample_width = source.getsampwidth()
        channels = source.getnchannels()
        assert sample_width == 2
        samples = [int.from_bytes(frames[index:index + 2], "little", signed=True)
                   for index in range(0, len(frames), sample_width * channels)]
        rate = source.getframerate()
    sine = sum(sample * math.sin(2 * math.pi * frequency * index / rate) for index, sample in enumerate(samples))
    cosine = sum(sample * math.cos(2 * math.pi * frequency * index / rate) for index, sample in enumerate(samples))
    return math.hypot(sine, cosine) / max(len(samples), 1)


def test_mp4_renders_black_gaps_and_later_video_tracks_cover_earlier_tracks(tmp_path):
    red = _video(tmp_path / "red.mp4", "red", 440)
    blue = _video(tmp_path / "blue.mp4", "blue", 880)
    output = tmp_path / "timeline.mp4"

    render_timeline(
        _timeline(tracks=[
            _track("video", [_clip("red", 0.25, 0.75)]),
            _track("video", [_clip("blue", 0.65, 0.35)]),
        ]),
        {"red": red, "blue": blue}, output, format="mp4",
    )

    assert output.is_file()
    assert max(_rgb_at(output, 0.10)) < 12
    red_pixel = _rgb_at(output, 0.40)
    blue_pixel = _rgb_at(output, 0.80)
    assert red_pixel[0] > 180 and red_pixel[2] < 60
    assert blue_pixel[2] > 150 and blue_pixel[0] < 80
    streams = _probe(output)["streams"]
    assert {stream["codec_type"] for stream in streams} == {"video", "audio"}


def test_hidden_video_is_not_shown_but_its_audio_is_kept_when_not_muted(tmp_path):
    red = _video(tmp_path / "red.mp4", "red", 440)
    blue = _video(tmp_path / "blue.mp4", "blue", 880)
    output = tmp_path / "hidden.mp4"

    render_timeline(
        _timeline(tracks=[
            _track("video", [_clip("red", 0, 0.75)], muted=True),
            _track("video", [_clip("blue", 0, 0.75)], hidden=True),
        ]),
        {"red": red, "blue": blue}, output, format="mp4",
    )

    pixel = _rgb_at(output, 0.35)
    assert pixel[0] > 180 and pixel[2] < 60
    wav = tmp_path / "hidden.wav"
    _run("ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(output), "-map", "0:a:0", str(wav))
    assert _tone_amplitude(wav, 880, 0.20, 0.30) > 300
    assert _tone_amplitude(wav, 440, 0.20, 0.30) < 80


def test_wav_applies_trim_speed_volume_and_output_relative_fades(tmp_path):
    tone = _audio(tmp_path / "tone.wav", 440)
    output = tmp_path / "timeline.wav"

    render_timeline(
        _timeline(tracks=[_track("audio", [
            _clip("tone", 0.25, 0.5, inPoint=0.5, speed=2, volume=0.5, fadeIn=0.1, fadeOut=0.1),
        ])]),
        {"tone": tone}, output, format="wav",
    )

    with wave.open(str(output), "rb") as source:
        assert source.getframerate() == 48000
        assert source.getnchannels() == 2
        assert source.getsampwidth() == 2
        assert source.getnframes() / source.getframerate() == pytest.approx(0.75, abs=0.03)
    assert _tone_amplitude(output, 440, 0.03, 0.10) < 10
    assert _tone_amplitude(output, 440, 0.30, 0.10) > 300
    assert _tone_amplitude(output, 440, 0.71, 0.03) < 250


def test_preview_scales_to_at_most_640_pixels_wide_and_keeps_aspect_ratio(tmp_path):
    image = _image(tmp_path / "green.png", "green")
    output = tmp_path / "preview.mp4"

    render_timeline(
        _timeline(width=800, height=400, tracks=[_track("video", [_clip("green", 0, 0.5)])]),
        {"green": image}, output, format="preview",
    )

    video = next(stream for stream in _probe(output)["streams"] if stream["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (640, 320)


def test_png_with_faster_speed_remains_visible_for_the_whole_output_clip(tmp_path):
    image = _image(tmp_path / "red.png", "red")
    output = tmp_path / "fast-image.mp4"

    render_timeline(
        _timeline(tracks=[_track("video", [_clip("red", 0, 2, speed=2)])]),
        {"red": image}, output, format="mp4",
    )

    pixel = _rgb_at(output, 1.5)
    assert pixel[0] > 180 and pixel[2] < 60


def test_rejects_video_clip_past_the_end_of_its_video_stream_even_if_audio_is_longer(tmp_path):
    source = _video_with_short_video_stream(tmp_path / "uneven.mp4")

    with pytest.raises(TimelineRenderError, match="视频流"):
        render_timeline(
            _timeline(tracks=[_track("video", [_clip("uneven", 0, 1.5)])]),
            {"uneven": source}, tmp_path / "bad.mp4", format="mp4",
        )


def test_rejects_clip_that_requires_media_past_its_source_end(tmp_path):
    tone = _audio(tmp_path / "tone.wav", 440, duration=1.0)

    with pytest.raises(TimelineRenderError, match="源媒体"):
        render_timeline(
            _timeline(tracks=[_track("audio", [_clip("tone", 0, 0.75, inPoint=0.5, speed=1)])]),
            {"tone": tone}, tmp_path / "bad.wav", format="wav",
        )


def test_cancelled_render_does_not_publish_output(tmp_path):
    tone = _audio(tmp_path / "tone.wav", 440)
    output = tmp_path / "cancelled.wav"

    with pytest.raises(TimelineRenderError, match="取消"):
        render_timeline(
            _timeline(tracks=[_track("audio", [_clip("tone", 0, 1)])]),
            {"tone": tone}, output, format="wav", cancelled=lambda: True,
        )

    assert not output.exists()
