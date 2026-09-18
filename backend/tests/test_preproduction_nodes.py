import json
import subprocess
from pathlib import Path
from typing import Optional

import pytest
from PIL import Image

from app.preproduction_nodes import NodeExecutionError, execute_node


FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"


@pytest.fixture(scope="module", autouse=True)
def require_ffmpeg():
    if not Path(FFMPEG).is_file() or not Path(FFPROBE).is_file():
        pytest.skip("需要 Homebrew ffmpeg-full 进行真实媒体节点测试")


def make_video(path: Path) -> None:
    result = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=c=red:s=64x32:r=10:d=0.5", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=44100:duration=0.5", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def make_audio(path: Path) -> None:
    result = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=44100:duration=0.5", "-c:a", "aac", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def make_audio_with_cover(path: Path, cover: Path) -> None:
    result = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=44100:duration=0.5", "-i", str(cover),
            "-map", "0:a:0", "-map", "1:v:0", "-c:a", "libmp3lame", "-c:v", "mjpeg",
            "-disposition:v:0", "attached_pic", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def make_single_frame_video(path: Path) -> None:
    result = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=c=green:s=64x32:r=1:d=0.1", "-frames:v", "1", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def probe(path: Path) -> dict:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def media(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (80, 40), "blue").save(image)
    video = tmp_path / "source.mp4"
    audio = tmp_path / "source.m4a"
    make_video(video)
    make_audio(audio)
    return {"image": image, "video": video, "audio": audio}


def run(kind: str, source: Optional[Path], destination: Path, params: dict):
    return execute_node(
        kind, source, destination, params, ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE,
    )


def test_reference_copies_a_verified_media_file_with_its_extension(media, tmp_path):
    output = tmp_path / "reference"

    assert run("reference", media["image"], output, {}) == ["reference.png"]
    assert (output / "reference.png").read_bytes() == media["image"].read_bytes()


def test_prompt_writes_text_without_requiring_media(tmp_path):
    output = tmp_path / "prompt"

    assert run("prompt", None, output, {"text": "镜头提示\n保留主体"}) == ["prompt.txt"]
    assert (output / "prompt.txt").read_text(encoding="utf-8") == "镜头提示\n保留主体"


def test_trim_transcodes_video_and_audio_to_playable_type_matched_outputs(media, tmp_path):
    video_output = tmp_path / "video-trim"
    audio_output = tmp_path / "audio-trim"

    assert run("trim", media["video"], video_output, {"start": 0.1, "end": 0.4}) == ["output.mp4"]
    assert any(item["codec_type"] == "video" for item in probe(video_output / "output.mp4")["streams"])
    assert run("trim", media["audio"], audio_output, {"start": 0.1, "end": 0.4}) == ["output.m4a"]
    assert all(item["codec_type"] != "video" for item in probe(audio_output / "output.m4a")["streams"])


def test_trim_treats_audio_with_an_attached_cover_as_audio(media, tmp_path):
    source = tmp_path / "cover.mp3"
    output = tmp_path / "cover-trim"
    make_audio_with_cover(source, media["image"])

    assert run("trim", source, output, {"start": 0.1, "end": 0.4}) == ["output.m4a"]
    assert all(item["codec_type"] != "video" for item in probe(output / "output.m4a")["streams"])


@pytest.mark.parametrize("kind", ["first_frame", "last_frame"])
def test_frame_extraction_works_for_a_short_video(kind, media, tmp_path):
    output = tmp_path / kind

    assert run(kind, media["video"], output, {}) == ["output.png"]
    with Image.open(output / "output.png") as frame:
        assert frame.size == (64, 32)


def test_last_frame_extraction_handles_a_single_frame_low_fps_video(tmp_path):
    source = tmp_path / "single-frame.mp4"
    make_single_frame_video(source)

    first = tmp_path / "single-first"
    last = tmp_path / "single-last"
    assert run("first_frame", source, first, {}) == ["output.png"]
    assert run("last_frame", source, last, {}) == ["output.png"]
    assert (first / "output.png").read_bytes() == (last / "output.png").read_bytes()


def test_crop_and_resize_keep_image_or_video_output_types(media, tmp_path):
    crop_output = tmp_path / "crop"
    resize_output = tmp_path / "resize"

    assert run("crop", media["image"], crop_output, {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}) == ["output.png"]
    with Image.open(crop_output / "output.png") as image:
        assert image.size == (40, 20)
    assert run("resize", media["video"], resize_output, {"width": 100, "height": 100}) == ["output.mp4"]
    stream = next(item for item in probe(resize_output / "output.mp4")["streams"] if item["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == (100, 100)


@pytest.mark.parametrize(
    ("kind", "source", "params"),
    [
        ("trim", "image", {"start": 0, "end": 0.1}),
        ("first_frame", "audio", {}),
        ("crop", "audio", {"x": 0, "y": 0, "width": 1, "height": 1}),
        ("resize", "image", {"width": 5000, "height": 100}),
        ("trim", "audio", {"start": 0.4, "end": 0.1}),
    ],
)
def test_rejects_wrong_media_and_unsafe_parameters_after_cleaning_output(kind, source, params, media, tmp_path):
    output = tmp_path / f"bad-{kind}"

    with pytest.raises(NodeExecutionError):
        run(kind, media[source], output, params)

    assert not output.exists() or not list(output.iterdir())


def test_unknown_node_is_rejected(tmp_path):
    with pytest.raises(NodeExecutionError):
        run("generate_video", None, tmp_path / "unknown", {})


def test_refuses_a_nonempty_destination_without_deleting_existing_files(tmp_path):
    output = tmp_path / "occupied"
    output.mkdir()
    sentinel = output / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(NodeExecutionError):
        run("prompt", None, output, {"text": "new prompt"})

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_last_frame_handles_a_final_second_with_no_frame(tmp_path):
    source = tmp_path / 'sparse.mp4'
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=s=64x32:r=0.5:d=4', '-c:v', 'libx264', str(source)], check=True)
    expected = tmp_path / 'last.png'
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', str(source),
                    '-vf', 'select=eq(n\\,1)', '-frames:v', '1', str(expected)], check=True)
    destination = tmp_path / 'last-output'
    assert run('last_frame', source, destination, {}) == ['output.png']
    with Image.open(expected) as wanted, Image.open(destination / 'output.png') as actual:
        assert actual.tobytes() == wanted.tobytes()
