"""Build bounded, shot-local visual evidence without sending the source video."""
import io
import json
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .analysis_input import VideoAnalysisInput, VideoProxy
from .analysis_prompt import build_analysis_prompt
from .analysis_providers.base import ProviderRequest
from .analysis_response import validate_or_repair
from .prompt_generation import generate_prompts
from .reference_media_storage import managed_reference_media_is_safe, resolve_reference_media_path


def build_shot_analysis_input(data_dir, project, shot, ffmpeg_path='ffmpeg', ffprobe_path='ffprobe'):
    media = project.referenceMedia
    start, end = shot['startSeconds'], shot['endSeconds']
    if (media is None or media.type != 'video' or not math.isfinite(start) or not math.isfinite(end)
            or not 0 <= start < end <= media.durationSeconds):
        raise ValueError('镜头时间范围无效。')
    if not managed_reference_media_is_safe(data_dir, project.id, media):
        raise ValueError('参考视频不可用。')
    source = resolve_reference_media_path(data_dir, project.id, media)
    duration = end - start
    probe = subprocess.run([
        ffprobe_path, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
        '-show_entries', 'format=start_time:frame=best_effort_timestamp_time', '-of', 'json', str(source),
    ], capture_output=True, text=True, timeout=120, check=False)
    if probe.returncode != 0:
        raise ValueError('镜头帧时间戳无法读取。')
    payload = json.loads(probe.stdout)
    origin = float(payload.get('format', {}).get('start_time', 0))
    pts = sorted(float(frame['best_effort_timestamp_time']) - origin for frame in payload.get('frames', [])
                 if 'best_effort_timestamp_time' in frame)
    candidates = [time for time in pts if math.isfinite(time) and start <= time < end]
    if not candidates:
        raise ValueError('镜头区间内没有可用视频帧。')
    times = [min(candidates, key=lambda time: abs(time - (start + duration * fraction))) - start
             for fraction in (0.125, 0.375, 0.625, 0.875)]
    sheet = Image.new('RGB', (640, 408), 'black')
    draw = ImageDraw.Draw(sheet)
    with tempfile.TemporaryDirectory(prefix='shot-analysis-') as directory:
        for index, relative in enumerate(times):
            target = Path(directory) / f'{index}.jpg'
            result = subprocess.run([
                ffmpeg_path, '-v', 'error', '-nostdin', '-ss', f'{start:.9f}', '-i', str(source),
                '-map', '0:V:0', '-frames:v', '1', '-an', '-vf',
                rf'trim=end={duration:.9f},select=gte(t\,{max(0, relative - 0.000001):.9f}),scale=320:180:force_original_aspect_ratio=decrease', str(target),
            ], capture_output=True, timeout=60, check=False)
            if result.returncode != 0 or not target.is_file():
                raise ValueError('镜头采样帧提取失败，请检查本地视频与 FFmpeg。')
            with Image.open(target) as frame:
                tile = ImageOps.pad(frame.convert('RGB'), (320, 180), color='black')
                x, y = (index % 2) * 320, (index // 2) * 204
                sheet.paste(tile, (x, y))
                draw.text((x + 8, y + 184), f'{index + 1}: sample +{relative:.3f}s', fill='white')
    image = io.BytesIO()
    sheet.save(image, format='JPEG', quality=85)
    proxy = VideoProxy.model_validate({
        'schemaVersion': 1,
        'source': {'durationSeconds': duration, 'width': media.width, 'height': media.height, 'frameRate': media.frameRate},
        'keyframes': [{'index': i + 1, 'timeSeconds': time} for i, time in enumerate(times)],
        'scene': {'changeCount': 0, 'changeTimesSeconds': []},
        'motion': {'samples': [], 'p50': None, 'p90': None, 'peak': None, 'level': 'unavailable'},
        'contactSheetFile': 'contact-sheet.jpg',
    })
    return VideoAnalysisInput(contactSheetBytes=image.getvalue(), analysisProxy=proxy)


def analyze_preparation_shot(data_dir, project, shot, provider, model, ffmpeg_path='ffmpeg', ffprobe_path='ffprobe'):
    evidence = build_shot_analysis_input(data_dir, project, shot, ffmpeg_path, ffprobe_path)
    request = ProviderRequest(analysisInput=evidence, model=model, prompt=build_analysis_prompt(evidence)
        + '\n仅分析这一个镜头的四张采样帧，时间戳相对镜头起点。静态帧无法证明的动作、运镜或声音必须标记无法确认；不虚构镜头外事件。')
    analysis = validate_or_repair(provider, provider.analyze(request), request)
    prompts = generate_prompts(analysis, provider, model)
    facts = analysis.observedFacts
    notes = [f"镜头 {shot['startSeconds']:.2f}–{shot['endSeconds']:.2f} 秒，依据四张采样帧分析（不是逐帧动作捕捉）。",
             f'主体：{facts.staticVisual.subject}', f'场景：{facts.staticVisual.scene}',
             f'构图：{facts.staticVisual.composition}']
    if facts.temporal:
        notes.extend([f'动作：{facts.temporal.subjectMotion}', f'运镜：{facts.temporal.cameraMotion}',
                      f'节奏：{facts.temporal.rhythm}'])
    return {'notes': '\n'.join(notes), 'prompts': prompts.model_dump()}
