import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image

from app.batch_subtitles import write_srt
from app import timeline_render
from app.timeline_render import TimelineRenderError, _output_size, render_timeline


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


def _timeline(width=64, height=48, fps=24, tracks=(), transitions=()):
    return {"settings": {"width": width, "height": height, "fps": fps}, "tracks": list(tracks),
            "transitions": list(transitions)}


def _track(kind: str, clips, **values):
    track = {"id": kind + "-track", "name": kind, "kind": kind, "muted": False, "hidden": False, "clips": clips}
    track.update(values)
    return track


def _transition(template, duration=.3, *, audio=True):
    return {
        "id": "boundary", "leftClipId": "red-clip", "rightClipId": "blue-clip",
        "template": template, "enabled": True, "duration": duration, "overlap": duration,
        "audioCrossfade": audio, "reason": "测试边界",
    }


def _probe(path: Path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _rgb_at(path: Path, second: float):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", str(second), "-i", str(path),
         "-frames:v", "1", "-vf", "scale=64:48", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
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

    media = render_timeline(
        _timeline(tracks=[
            _track("video", [_clip("red", 0.25, 0.75)]),
            _track("video", [_clip("blue", 0.65, 0.35)]),
        ]),
        {"red": red, "blue": blue}, output, format="mp4",
    )

    assert output.is_file()
    assert media == {"width": 64, "height": 48, "fps": 24.0, "duration": pytest.approx(1.0, abs=0.12)}
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


@pytest.mark.parametrize(("width", "height", "preview"), [
    (1680, 720, (630, 270)), (1280, 720, (640, 360)), (960, 720, (640, 480)),
    (720, 720, (640, 640)), (720, 960, (636, 848)), (720, 1280, (630, 1120)),
])
def test_preview_downscaling_preserves_supported_ratios_exactly(width, height, preview):
    assert _output_size(width, height, "preview") == preview


def test_long_horizontal_caption_fits_inside_preview_safe_area(tmp_path):
    image = _image(tmp_path / "green.png", "green")
    subtitles = tmp_path / "subtitles.srt"
    write_srt(subtitles, [{"id": "long", "start": 0, "end": 0.5, "text": "横屏字幕安全区" * 15}])
    output = tmp_path / "wide-preview.mp4"

    render_timeline(
        _timeline(width=1680, height=720, tracks=[_track("video", [_clip("green", 0, 0.5)])]),
        {"green": image}, output, format="preview", subtitles_path=subtitles,
    )

    video = next(stream for stream in _probe(output)["streams"] if stream["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (630, 270)
    with Image.open(tmp_path / "caption-1.png") as caption:
        assert caption.height <= round(270 * 0.45)


@pytest.mark.parametrize(('width', 'height'), [
    (1680, 720), (1280, 720), (960, 720), (720, 720), (720, 960), (720, 1280),
])
def test_mp4_preserves_each_supported_output_ratio(tmp_path, width, height):
    image = _image(tmp_path / "green.png", "green")
    output = tmp_path / f"ratio-{width}x{height}.mp4"

    render_timeline(
        _timeline(width=width, height=height, tracks=[_track("video", [_clip("green", 0, 0.5)])]),
        {"green": image}, output, format="mp4",
    )

    video = next(stream for stream in _probe(output)["streams"] if stream["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (width, height)


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


@pytest.mark.parametrize(("template", "ffmpeg_template"), [
    ("dissolve", "fade"), ("push", "slideleft"), ("wipe", "wipeleft"),
    ("flash-white", "fadewhite"), ("pixelize", "pixelize"),
])
@pytest.mark.parametrize("duration", [.15, .4])
def test_actual_ffmpeg_renders_every_supported_visual_transition(template, ffmpeg_template, duration, tmp_path, monkeypatch):
    red = _video(tmp_path / "red.mp4", "red", 440, duration=1.5)
    blue = _video(tmp_path / "blue.mp4", "blue", 880, duration=1.5)
    output = tmp_path / f"{template}-{duration}.mp4"

    commands = []
    real_run = timeline_render._run_ffmpeg
    def capture(command, cancelled, cwd=None):
        commands.append(command)
        return real_run(command, cancelled, cwd)
    monkeypatch.setattr(timeline_render, "_run_ffmpeg", capture)

    media = render_timeline(
        _timeline(
            tracks=[_track("video", [_clip("red", 0, 1.2), _clip("blue", 1.2 - duration, 1.2)])],
            transitions=[_transition(template, duration)],
        ),
        {"red": red, "blue": blue}, output, format="mp4",
    )

    assert media["duration"] == pytest.approx(2.4 - duration, abs=.12)
    assert output.is_file()
    filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert f"xfade=transition={ffmpeg_template}:duration={duration:g}:offset=" in filter_graph


def test_transition_audio_crossfade_blends_both_adjacent_clip_streams(tmp_path, monkeypatch):
    red = _video(tmp_path / "red.mp4", "red", 440, duration=1.5)
    blue = _video(tmp_path / "blue.mp4", "blue", 880, duration=1.5)
    output = tmp_path / "crossfade.mp4"
    commands = []
    real_run = timeline_render._run_ffmpeg
    def capture(command, cancelled, cwd=None):
        commands.append(command)
        return real_run(command, cancelled, cwd)
    monkeypatch.setattr(timeline_render, "_run_ffmpeg", capture)
    render_timeline(
        _timeline(
            tracks=[_track("video", [_clip("red", 0, 1.2), _clip("blue", .8, 1.2)])],
            transitions=[_transition("dissolve", .4)],
        ),
        {"red": red, "blue": blue}, output, format="mp4",
    )
    wav = tmp_path / "crossfade.wav"
    _run("ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(output), "-map", "0:a:0", str(wav))

    assert _tone_amplitude(wav, 440, .95, .1) > 100
    assert _tone_amplitude(wav, 880, .95, .1) > 100
    assert _tone_amplitude(wav, 440, .82, .06) > _tone_amplitude(wav, 440, 1.1, .06) * 2
    assert _tone_amplitude(wav, 880, 1.1, .06) > _tone_amplitude(wav, 880, .82, .06) * 2
    red_level, _, blue_level = _rgb_at(output, 1.0)
    assert 70 < red_level < 190 and 70 < blue_level < 190
    filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "acrossfade=d=0.4" in filter_graph


def test_disabling_audio_crossfade_uses_a_hard_audio_cut_at_the_same_boundary(tmp_path, monkeypatch):
    red = _video(tmp_path / "red.mp4", "red", 440, duration=1.5)
    blue = _video(tmp_path / "blue.mp4", "blue", 880, duration=1.5)
    output = tmp_path / "hard-audio-cut.mp4"
    commands = []
    real_run = timeline_render._run_ffmpeg
    def capture(command, cancelled, cwd=None):
        commands.append(command)
        return real_run(command, cancelled, cwd)
    monkeypatch.setattr(timeline_render, "_run_ffmpeg", capture)

    render_timeline(
        _timeline(
            tracks=[_track("video", [_clip("red", 0, 1.2), _clip("blue", .8, 1.2)])],
            transitions=[_transition("dissolve", .4, audio=False)],
        ),
        {"red": red, "blue": blue}, output, format="mp4",
    )

    filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "acrossfade=" not in filter_graph
    assert "concat=n=2:v=0:a=1" in filter_graph


def test_visual_transition_uses_silence_when_adjacent_images_have_no_audio(tmp_path):
    red = _image(tmp_path / "red.png", "red")
    blue = _image(tmp_path / "blue.png", "blue")
    output = tmp_path / "silent-transition.mp4"

    render_timeline(
        _timeline(
            tracks=[_track("video", [_clip("red", 0, .8), _clip("blue", .6, .8)])],
            transitions=[_transition("wipe", .2)],
        ),
        {"red": red, "blue": blue}, output, format="mp4",
    )

    assert {stream["codec_type"] for stream in _probe(output)["streams"]} == {"video", "audio"}


def test_preview_and_export_use_the_same_transition_boundary_timing(tmp_path):
    red = _video(tmp_path / "red.mp4", "red", 440, duration=1.5)
    blue = _video(tmp_path / "blue.mp4", "blue", 880, duration=1.5)
    timeline = _timeline(
        width=1280, height=720,
        tracks=[_track("video", [_clip("red", 0, 1.2), _clip("blue", .9, 1.2)])],
        transitions=[_transition("dissolve", .3)],
    )
    preview = tmp_path / "preview-transition.mp4"
    export = tmp_path / "export-transition.mp4"

    preview_media = render_timeline(timeline, {"red": red, "blue": blue}, preview, format="preview")
    export_media = render_timeline(timeline, {"red": red, "blue": blue}, export, format="mp4")

    assert preview_media["duration"] == pytest.approx(export_media["duration"], abs=.01)
    assert _rgb_at(preview, 1.05) == pytest.approx(_rgb_at(export, 1.05), abs=3)
    for video in (preview, export):
        wav = video.with_suffix('.wav')
        _run("ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(video), "-map", "0:a:0", str(wav))
    for frequency in (440, 880):
        assert _tone_amplitude(preview.with_suffix('.wav'), frequency, 1, .1) == pytest.approx(
            _tone_amplitude(export.with_suffix('.wav'), frequency, 1, .1), rel=.05)


def test_cancelled_transition_render_never_publishes_a_partial_output(tmp_path):
    red = _video(tmp_path / "red.mp4", "red", 440, duration=1.5)
    blue = _video(tmp_path / "blue.mp4", "blue", 880, duration=1.5)
    output = tmp_path / "cancelled-transition.mp4"
    checks = 0
    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(TimelineRenderError, match="取消"):
        render_timeline(
            _timeline(
                tracks=[_track("video", [_clip("red", 0, 1.2), _clip("blue", .8, 1.2)])],
                transitions=[_transition("dissolve", .4)],
            ),
            {"red": red, "blue": blue}, output, format="mp4", cancelled=cancelled,
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".cancelled-transition.*.mp4"))


@pytest.mark.parametrize(("template", "right_speed"), [("cut", 1), ("speed-change", 1.25)])
def test_actual_ffmpeg_renders_hard_cut_and_controlled_speed_change(template, right_speed, tmp_path, monkeypatch):
    red = _video(tmp_path / "red.mp4", "red", 440, duration=1.5)
    blue = _video(tmp_path / "blue.mp4", "blue", 880, duration=1.5)
    output = tmp_path / f"{template}.mp4"
    transition = _transition(template, 0, audio=False)
    commands = []
    real_run = timeline_render._run_ffmpeg
    def capture(command, cancelled, cwd=None):
        commands.append(command)
        return real_run(command, cancelled, cwd)
    monkeypatch.setattr(timeline_render, "_run_ffmpeg", capture)

    render_timeline(
        _timeline(
            tracks=[_track("video", [_clip("red", 0, 1), _clip("blue", 1, 1, speed=right_speed)])],
            transitions=[transition],
        ),
        {"red": red, "blue": blue}, output, format="mp4",
    )

    filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "xfade=" not in filter_graph
    if template == "speed-change":
        assert "setpts=(PTS-STARTPTS)/1.25" in filter_graph


def test_render_rejects_transition_longer_than_an_adjacent_short_clip(tmp_path):
    red = _video(tmp_path / "red.mp4", "red", 440, duration=1)
    blue = _video(tmp_path / "blue.mp4", "blue", 880, duration=1)

    with pytest.raises(TimelineRenderError, match="短于相邻片段"):
        render_timeline(
            _timeline(
                tracks=[_track("video", [_clip("red", 0, .2), _clip("blue", 0, .4)])],
                transitions=[_transition("dissolve", .2)],
            ),
            {"red": red, "blue": blue}, tmp_path / "bad-transition.mp4", format="mp4",
        )


def test_transition_normalizes_different_input_dimensions_and_frame_rates(tmp_path):
    red = _video(tmp_path / "red.mp4", "red", 440)
    blue = tmp_path / "blue.mp4"
    _run("ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=blue:size=120x80:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(blue))
    output = tmp_path / "normalized.mp4"
    render_timeline(_timeline(fps=25, tracks=[_track("video", [_clip("red", 0, 1.2), _clip("blue", .8, 1.2)])],
                              transitions=[_transition("dissolve", .4)]),
                    {"red": red, "blue": blue}, output, format="mp4")
    video = next(stream for stream in _probe(output)["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (64, 48, "25/1")
    red_level, _, blue_level = _rgb_at(output, 1)
    assert 70 < red_level < 190 and 70 < blue_level < 190
