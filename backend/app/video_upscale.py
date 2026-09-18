"""Bounded-batch local Real-ESRGAN video processing (SDR, CFR output)."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from PIL import Image

MODEL = 'realesrgan-x4plus'
WORKER_ROOT = Path(__file__).resolve().parents[1] / 'upscale_worker'


class UpscaleError(Exception):
    pass


@dataclass(frozen=True)
class UpscaleConfig:
    binary: str
    models: Path
    ffmpeg: str = 'ffmpeg'
    ffprobe: str = 'ffprobe'

    @classmethod
    def local(cls, ffmpeg='ffmpeg', ffprobe='ffprobe'):
        vendor = WORKER_ROOT / 'vendor' / 'realesrgan'
        return cls(os.environ.get('REALESRGAN_BINARY', str(vendor / 'realesrgan-ncnn-vulkan')),
                   Path(os.environ.get('REALESRGAN_MODELS', str(vendor / 'models'))), ffmpeg, ffprobe)

    def environment(self):
        engine = shutil.which(self.binary)
        models = all((self.models / f'{MODEL}.{suffix}').is_file() for suffix in ('param', 'bin'))
        ready = bool(engine and models and shutil.which(self.ffmpeg) and shutil.which(self.ffprobe))
        return {'available': ready, 'message': '本地 Real-ESRGAN 已就绪，运行时检查 GPU。' if ready else
                '未安装本地超分引擎或模型。请按 README 安装 Real-ESRGAN，配置 REALESRGAN_BINARY 与 REALESRGAN_MODELS，并确认 FFmpeg 可用。'}


def _command(args, timeout=120):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise UpscaleError('视频处理失败，请检查本地引擎、GPU、磁盘空间及媒体文件。') from error


def inspect_video(path: Path, config: UpscaleConfig):
    try:
        info = json.loads(_command([config.ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
        stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
        fps = Fraction(stream.get('avg_frame_rate') or stream['r_frame_rate'])
        video_duration = float(stream.get('duration') or info['format']['duration'])
        duration = max(video_duration, float(info['format']['duration']))
        audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
        width, height = int(stream['width']), int(stream['height'])
        rotation = next((float(s['rotation']) for s in stream.get('side_data_list', []) if 'rotation' in s),
                        float(stream.get('tags', {}).get('rotate', 0)))
        if round(rotation) % 180:
            width, height = height, width
        if not math.isfinite(duration) or not 0 < duration <= 301 or not 0 < fps <= 120 or min(width, height) <= 0:
            raise ValueError('invalid metadata')
        if stream.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
            raise UpscaleError('首版仅支持 SDR 视频，请先将 HDR 转换为 SDR。')
        return {'width': width, 'height': height, 'fps': fps, 'duration': duration,
                'videoDuration': video_duration, 'audio': audio is not None,
                'audioDuration': float(audio.get('duration') or duration) if audio else 0}
    except (ValueError, KeyError, StopIteration, ZeroDivisionError) as error:
        raise UpscaleError('无法读取有效的视频尺寸、时长或帧率。') from error


def _read_frame(pipe, size):
    data = bytearray()
    while len(data) < size:
        chunk = pipe.read(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    if data and len(data) != size:
        raise UpscaleError('视频解码得到不完整帧。')
    return bytes(data)


def target_dimensions(width: int, height: int, target: str | int):
    if target in (2, 4):
        # Existing stored jobs keep their original multiplier semantics.
        result = (width * target, height * target)
    elif target in ('1080p', '2k'):
        short_side = 1080 if target == '1080p' else 1440
        if min(width, height) >= short_side:
            raise UpscaleError('原片已达到此清晰度，请选择更高的输出清晰度。')
        ratio = short_side / min(width, height)
        result = tuple(int(math.floor(side * ratio / 2 + .5)) * 2 for side in (width, height))
    else:
        raise UpscaleError('请选择 1080P 或 2K 清晰度。')
    if max(result) > 7680 or min(result) > 4320:
        raise UpscaleError('输出尺寸超过 8K 上限，请选择较低的输出清晰度。')
    return result


def execute_upscale(source: Path, directory: Path, target: str | int, config: UpscaleConfig,
                    progress: Callable[[str, int], None]):
    if not config.environment()['available']:
        raise UpscaleError(config.environment()['message'])
    progress('preparing', 0)
    meta = inspect_video(source, config)
    width, height = meta['width'], meta['height']
    ow, oh = target_dimensions(width, height, target)
    fps = str(meta['fps'])
    expected = round(meta['duration'] * float(meta['fps']))
    # Keep at most a small batch of full-resolution neural results on disk.
    batch_size = max(1, min(8, 8_000_000 // (width * height)))
    scratch = directory / 'frames'
    inputs, outputs = scratch / 'input', scratch / 'output'
    inputs.mkdir(parents=True); outputs.mkdir()
    silent = directory / 'silent.mp4'
    partial = directory / 'output.part.mp4'
    final = directory / 'output.mp4'
    decoder = encoder = None
    frames = 0
    try:
        with (scratch / 'ffmpeg.log').open('wb') as log:
            decoder = subprocess.Popen([config.ffmpeg, '-nostdin', '-v', 'error', '-i', str(source),
                '-map', '0:v:0', '-vf', f'fps={fps},tpad=stop_mode=clone:stop_duration={max(0, meta["duration"] - meta["videoDuration"])}',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                stdout=subprocess.PIPE, stderr=log)
            encoder = subprocess.Popen([config.ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo',
                '-pixel_format', 'rgb24', '-video_size', f'{ow}x{oh}', '-framerate', fps, '-i', 'pipe:0',
                '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                str(silent)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            while True:
                batch = []
                for index in range(batch_size):
                    raw = _read_frame(decoder.stdout, width * height * 3)
                    if not raw:
                        break
                    name = f'{index:04d}.png'
                    Image.frombytes('RGB', (width, height), raw).save(inputs / name)
                    batch.append(name)
                if not batch:
                    break
                _command([config.binary, '-i', str(inputs), '-o', str(outputs), '-m', str(config.models),
                          '-n', MODEL, '-s', '4', '-t', '128', '-f', 'png', '-j', '1:1:1'], timeout=1800)
                for name in batch:
                    with Image.open(outputs / name) as im:
                        if im.size != (width * 4, height * 4):
                            raise UpscaleError('超分引擎输出尺寸不符合模型契约。')
                        im = im.convert('RGB')
                        if im.size != (ow, oh):
                            im = im.resize((ow, oh), Image.Resampling.LANCZOS)
                        encoder.stdin.write(im.tobytes())
                    (inputs / name).unlink(); (outputs / name).unlink()
                    frames += 1
                progress('upscaling', min(95, int(frames * 95 / max(expected, 1))))
            decoder.stdout.close()
            if decoder.wait(timeout=60) != 0 or not frames:
                raise UpscaleError('视频解码失败或没有可用帧。')
            encoder.stdin.close()
            if encoder.wait(timeout=120) != 0:
                raise UpscaleError('超分视频编码失败。')
        progress('encoding', 97)
        _command([config.ffmpeg, '-nostdin', '-v', 'error', '-y', '-i', str(silent), '-i', str(source),
                  '-map', '0:v:0', '-map', '1:a:0?', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                  '-t', str(frames / float(meta['fps'])), '-map_metadata', '-1', '-movflags', '+faststart', str(partial)])
        result = inspect_video(partial, config)
        if (result['width'], result['height']) != (ow, oh) or abs(result['duration'] - meta['duration']) > 1 / float(meta['fps']) + .05 or result['audio'] != meta['audio']:
            raise UpscaleError('输出的视频尺寸、时间线或音轨检查未通过。')
        if abs(result['audioDuration'] - meta['audioDuration']) > 1 / float(meta['fps']) + .05:
            raise UpscaleError('输出音轨时长检查未通过。')
        os.replace(partial, final)
        progress('completed', 100)
        return {'width': ow, 'height': oh, 'frameRate': float(result['fps']),
                'durationSeconds': result['duration']}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise UpscaleError('超分处理失败，请检查 GPU、模型和磁盘空间。') from error
    finally:
        for process in (decoder, encoder):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                for pipe in (process.stdin, process.stdout):
                    if pipe and not pipe.closed:
                        pipe.close()
        shutil.rmtree(scratch, ignore_errors=True)
        silent.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
