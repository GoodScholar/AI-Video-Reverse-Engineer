import json
import math
import re
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image

from app.batch_subtitles import write_srt
from app import timeline_render
from app.timeline_render import TimelineRenderError, _output_size, render_timeline
from app.montage_reframe import DETECTOR_VERSION, MODEL_SHA256, source_fingerprint


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


def test_collect_clips_probes_reframe_timing_once_per_source(monkeypatch, tmp_path):
    """A dict cache must not evaluate its default probe on a cache hit."""
    source = tmp_path / "shared.mp4"
    source.write_bytes(b"shared")
    calls = []
    monkeypatch.setattr(timeline_render, "_probe_media", lambda *_: timeline_render._Media(
        4, 4, None, True, False, False, 64, 48, 24, "1:1", 0,
    ))
    from app import montage_reframe
    timing = montage_reframe.SourceTiming((0, 1), (1, 1), True, None)
    monkeypatch.setattr(montage_reframe, "probe_source_timing", lambda path, probe: calls.append((path, probe)) or timing)
    reframe = {"schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 64, "sourceHeight": 48,
               "targetAspect": {"width": 64, "height": 48}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
               "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
               "uncertaintyReasons": ["人工构图"], "status": "review_required", "override": {"mode": "center", "keyframes": []}}
    tracks = [_track("video", [_clip("shared", 0, 1, id="one", reframe=reframe),
                                _clip("shared", 1, 1, id="two", reframe=reframe)])]

    timeline_render._collect_clips(tracks, {"shared": source}, "ffprobe", "mp4")

    assert calls == [(source, "ffprobe")]


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


def test_packaging_uses_one_ass_subtitle_pass_and_keeps_preview_export_coordinates(tmp_path, monkeypatch):
    """Packaging owns cues even with no user elements; legacy PNG is forbidden."""
    from app.montage_packaging import default_packaging
    image = _image(tmp_path / "green.png", "green")
    subtitles = tmp_path / "subtitles.srt"
    write_srt(subtitles, [{"id": "cue", "start": .1, "end": .3, "text": "唯一烧录"}])
    captured, real_build = [], timeline_render._build_command
    def capture(*args, **kwargs):
        captured.append((args[8], Path(kwargs["ass_path"]).read_text(encoding="utf-8")))
        return real_build(*args, **kwargs)
    monkeypatch.setattr(timeline_render, "_build_command", capture)
    monkeypatch.setattr("app.batch_subtitles.caption_images", lambda *_: pytest.fail("包装不得走 PNG 字幕"))
    timeline = _timeline(width=720, height=1280, tracks=[_track("video", [_clip("green", 0, .5)])])
    cues = [{"id": "cue", "start": .1, "end": .3, "text": "唯一烧录"}]
    preview, export = tmp_path / "pack-preview.mp4", tmp_path / "pack-export.mp4"

    ffmpeg = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    ffprobe = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
    if not Path(ffmpeg).is_file():
        pytest.skip("包装真实渲染需要含 libass 的 FFmpeg")
    preview_media = render_timeline(timeline, {"green": image}, preview, format="preview", subtitles_path=subtitles,
                                    packaging=default_packaging(), packaging_cues=cues, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    export_media = render_timeline(timeline, {"green": image}, export, format="mp4", subtitles_path=subtitles,
                                   packaging=default_packaging(), packaging_cues=cues, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)

    assert preview_media["width"] == 630 and export_media["width"] == 720
    assert [items for items, _ass in captured] == [[], []]
    assert all(ass.count("唯一烧录") == 1 and "\\pos(360," in ass for _items, ass in captured)
    assert all(not path.exists() for path in tmp_path.glob(".packaging.ass"))

    def text_bbox(video, second):
        frame = tmp_path / (video.stem + f"-{second}.png")
        _run(ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(video), "-ss", str(second),
             "-frames:v", "1", str(frame))
        with Image.open(frame).convert("RGB") as pixels:
            points = [(x, y) for y in range(pixels.height) for x in range(pixels.width)
                      if all(channel > 210 for channel in pixels.getpixel((x, y)))]
            if not points:
                return None, (pixels.width, pixels.height)
            return (min(x for x, _y in points), min(y for _x, y in points),
                    max(x for x, _y in points) + 1, max(y for _x, y in points) + 1), (pixels.width, pixels.height)

    preview_box, preview_size = text_bbox(preview, .2)
    export_box, export_size = text_bbox(export, .2)
    assert preview_box and export_box
    preview_normalized = [preview_box[0] / preview_size[0], preview_box[1] / preview_size[1],
                          preview_box[2] / preview_size[0], preview_box[3] / preview_size[1]]
    export_normalized = [export_box[0] / export_size[0], export_box[1] / export_size[1],
                         export_box[2] / export_size[0], export_box[3] / export_size[1]]
    assert max(abs(left - right) for left, right in zip(preview_normalized, export_normalized)) < .012
    assert text_bbox(preview, .4)[0] is None and text_bbox(export, .4)[0] is None


def test_production_multiline_outline_pixels_stay_inside_the_compiler_bbox(tmp_path):
    """The real libass fill *and* semi-transparent outline fit the production prediction."""
    from app.montage_packaging import _element_events, compile_ass, default_packaging, private_font_path

    ffmpeg = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if not Path(ffmpeg).is_file():
        pytest.skip("包装真实描边证据需要含 libass 的 FFmpeg")
    background = _video(tmp_path / "outline-background.mp4", "0x123456", 440, duration=.5)
    timeline = _timeline(width=720, height=1280, tracks=[_track("video", [_clip("background", 0, .5)])])
    packaging = default_packaging()
    packaging["elements"] = [{
        "id": "outline-title", "kind": "title", "enabled": True, "start": .1, "end": .3,
        "text": "多行描边\n完整边界", "position": "top", "style": "emphasis",
    }]
    ass = compile_ass(packaging, [], [], 720, 1280, private_font_path())
    ass_path = tmp_path / "production-multiline-outline.ass"
    ass_path.write_text(ass, encoding="utf-8")
    rendered, control = tmp_path / "production-multiline-outline.mp4", tmp_path / "outline-control.mp4"
    render_timeline(timeline, {"background": background}, rendered, format="mp4", packaging=packaging,
                    packaging_cues=[], ffmpeg_path=ffmpeg, ffprobe_path="/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    render_timeline(timeline, {"background": background}, control, format="mp4", ffmpeg_path=ffmpeg,
                    ffprobe_path="/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    frame, base_frame = tmp_path / "production-multiline-outline.png", tmp_path / "outline-control.png"
    for video, image in ((rendered, frame), (control, base_frame)):
        _run(ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", "0.2", "-i", str(video),
             "-frames:v", "1", str(image))
    with Image.open(frame).convert("RGB") as actual, Image.open(base_frame).convert("RGB") as baseline:
        points = [(x, y) for y in range(actual.height) for x in range(actual.width)
                  if sum(abs(left - right) for left, right in zip(actual.getpixel((x, y)), baseline.getpixel((x, y)))) > 8]
    assert points  # compare against a real no-ASS render, not a yellow-fill colour threshold
    actual_box = [min(x for x, _y in points), min(y for _x, y in points),
                  max(x for x, _y in points) + 1, max(y for _x, y in points) + 1]
    event = _element_events(packaging, [], 720, 1280)[0]
    predicted_box = [360 - event["width"] / 2, 64, 360 + event["width"] / 2, 64 + event["height"]]
    predicted_json = tmp_path / "production-multiline-outline-predicted.json"
    actual_json = tmp_path / "production-multiline-outline-actual.json"
    predicted_json.write_text(json.dumps(predicted_box), encoding="utf-8")
    actual_json.write_text(json.dumps(actual_box), encoding="utf-8")
    fontselect = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-v", "verbose", "-i", str(background),
         "-vf", "ass=filename=%s:fontsdir=%s" % (ass_path, private_font_path().parent),
         "-ss", "0.2", "-frames:v", "1", "-f", "null", "-"], check=True, capture_output=True, text=True,
    ).stderr
    fontselect_log = tmp_path / "production-multiline-outline-fontselect.log"
    fontselect_log.write_text(fontselect, encoding="utf-8")
    requested_face = "NotoSansCJKsc-Regular"
    assert re.search(
        r"fontselect: \(" + re.escape(requested_face) + r", \d+, 0\) -> "
        + re.escape(requested_face) + r", 0, " + re.escape(requested_face) + r"(?:\r?$)",
        fontselect,
        flags=re.MULTILINE,
    )
    assert sorted(path.name for path in private_font_path().parent.iterdir()) == [private_font_path().name]
    evidence = Path("/tmp/aivre-ticket05-evidence/task21-round5")
    evidence.mkdir(parents=True, exist_ok=True)
    for artifact in (ass_path, rendered, control, frame, base_frame, predicted_json, actual_json, fontselect_log):
        shutil.copy2(artifact, evidence / artifact.name)
    expected = [
        "production-multiline-outline.ass", "production-multiline-outline.mp4", "outline-control.mp4",
        "production-multiline-outline.png", "outline-control.png",
        "production-multiline-outline-predicted.json", "production-multiline-outline-actual.json",
        "production-multiline-outline-fontselect.log",
    ]
    assert all((evidence / name).is_file() for name in expected)
    assert predicted_box[0] <= actual_box[0] and predicted_box[1] <= actual_box[1]
    assert predicted_box[2] >= actual_box[2] and predicted_box[3] >= actual_box[3]


def test_packaging_rejects_default_missing_glyph_and_cleans_ass_on_renderer_failure(tmp_path, monkeypatch):
    from app.montage_packaging import default_packaging
    image, output = _image(tmp_path / "green.png", "green"), tmp_path / "bad-packaging.mp4"
    timeline = _timeline(width=720, height=1280, tracks=[_track("video", [_clip("green", 0, .5)])])
    with pytest.raises(TimelineRenderError, match="缺少"):
        render_timeline(timeline, {"green": image}, output, format="preview", packaging=default_packaging(),
                        packaging_cues=[{"id": "bad", "start": 0, "end": .4, "text": "\U00020000"}])
    assert not output.exists() and not list(tmp_path.glob(".packaging.ass"))

    monkeypatch.setattr(timeline_render, "_run_ffmpeg", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimelineRenderError("中断")))
    with pytest.raises(TimelineRenderError, match="中断"):
        render_timeline(timeline, {"green": image}, output, format="preview", packaging=default_packaging(),
                        packaging_cues=[{"id": "ok", "start": 0, "end": .4, "text": "清理"}])
    assert not output.exists() and not list(tmp_path.glob(".packaging.ass"))


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


def test_reframed_still_image_is_full_bleed_without_black_padding(tmp_path):
    image = _image(tmp_path / "red.png", "red")
    reframe = {
        "schemaVersion": 1, "sourceFingerprint": source_fingerprint(image), "sourceWidth": 64, "sourceHeight": 48,
        "targetAspect": {"width": 24, "height": 48}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
        "samples": [{"timeSeconds": 0, "boxes": [], "center": {"x": .5, "y": .5}, "confidence": .9}],
        "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []},
    }
    output = tmp_path / "still-reframe.mp4"

    render_timeline(_timeline(width=24, height=48, tracks=[_track("video", [_clip("red", 0, 1, reframe=reframe)])]),
                    {"red": image}, output, format="mp4")

    pixel = _rgb_at(output, .5)
    assert pixel[0] > 180 and pixel[1] < 70 and pixel[2] < 70


def test_reframe_moves_a_full_bleed_crop_in_source_time_without_black_padding(tmp_path):
    """A crop applied after speed conversion would not reach the blue source side at 2x."""
    source = tmp_path / "split.mp4"
    _run("ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=red:size=64x48:rate=24:duration=3",
         "-f", "lavfi", "-i", "color=blue:size=64x48:rate=24:duration=3",
         "-filter_complex", "[0:v][1:v]hstack=inputs=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source))
    analysis = {
        "schemaVersion": 1, "sourceFingerprint": source_fingerprint(source), "sourceWidth": 128, "sourceHeight": 48,
        "targetAspect": {"width": 1, "height": 2}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
            "samples": [
                {"timeSeconds": point, "boxes": [{"x": .1, "y": .1, "width": .1, "height": .8, "label": "person", "confidence": .9}],
                 "center": {"x": .2 + .6 * point / 2.958333, "y": .5}, "confidence": .9}
                for point in (0, .5, 1, 1.5, 2, 2.5, 2.958333)
            ], "uncertaintyReasons": [], "status": "ready", "override": {"mode": "auto", "keyframes": []},
    }
    output = tmp_path / "reframe.mp4"
    render_timeline(_timeline(width=24, height=48, tracks=[_track("video", [
        _clip("split", 0, 1.5, speed=2, reframe=analysis),
    ])]), {"split": source}, output, format="mp4")

    first, last = _rgb_at(output, .15), _rgb_at(output, 1.3)
    assert first[0] > 150 and first[2] < 70
    assert last[2] > 130 and last[0] < 80
    assert min(first) >= 0 and min(last) >= 0


@pytest.mark.parametrize("sar,rotation", [("2:1", 0), ("1:1", 90)])
def test_reframe_preflight_rejects_the_same_sar_or_rotation_sources_as_render(tmp_path, monkeypatch, sar, rotation):
    import app.timeline_render as module
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    reframe = {
        "schemaVersion": 1, "sourceFingerprint": "", "sourceWidth": 0, "sourceHeight": 0,
        "targetAspect": {"width": 1, "height": 1}, "detectorVersion": DETECTOR_VERSION, "modelSha256": MODEL_SHA256,
        "samples": [], "uncertaintyReasons": ["等待人工"], "status": "review_required",
        "override": {"mode": "center", "keyframes": []},
    }
    state = _timeline(width=64, height=64, tracks=[_track("video", [_clip("source", 0, 1, reframe=reframe)])])
    state["revision"] = 0
    media = module._Media(1, 1, None, True, False, False, 64, 48, 24, sar, rotation)
    collected = {**state["tracks"][0]["clips"][0], "media": media}
    monkeypatch.setattr(module, "_collect_clips", lambda *_args, **_kwargs: [collected])

    issues = module.inspect_timeline_media(state, {"source": {"id": "source", "kind": "video", "duration": 1}},
                                           lambda _asset: source, "mp4", "ffprobe")

    assert any("旋转或非方形像素" in issue["message"] for issue in issues)
    with pytest.raises(TimelineRenderError, match="旋转或非方形像素"):
        module._video_filter(0, collected, 64, 64, 24, "out")


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
