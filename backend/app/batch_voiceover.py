"""Retiming uses the measured duration of each generated sentence, never ASR estimates."""
from __future__ import annotations
import math
import re
import wave
from copy import deepcopy
from uuid import uuid4
from .timeline import validate_workspace


def plan_voiceover(task, assets):
    texts = [part.strip() for part in re.split(r'[。！？；;!?\n]|\.(?!\d)', task['script']) if part.strip()]
    visuals = [track for track in task['variant']['tracks'] if track['kind'] == 'video' and not track['hidden'] and track['clips']]
    if len(visuals) != 1 or not texts or len(texts) > 30 or any(len(text)>120 for text in texts):
        raise ValueError('请使用一个画面轨道，脚本用句号或换行分段，每段最多 120 字、最多 30 段。')
    clips = sorted(visuals[0]['clips'], key=lambda clip: clip['start'])
    if len(clips) != len(texts):
        raise ValueError('每段脚本需要对应一个画面片段，请先按脚本推荐并保存镜头。')
    validate_workspace({'revision': task['variant']['revision'], 'settings': task['variant']['settings'], 'tracks': task['variant']['tracks']}, assets)
    return {'texts': texts, 'videoTrackId': visuals[0]['id'], 'clips': deepcopy(clips)}


def audio_duration(path):
    with wave.open(str(path), 'rb') as source:
        if source.getnchannels()!=1 or source.getsampwidth()!=2 or source.getframerate()!=24000:
            raise ValueError('生成的配音格式无效。')
        duration = source.getnframes()/source.getframerate()
    if not math.isfinite(duration) or not .05 < duration <=300:
        raise ValueError('生成的配音时长无效。')
    return duration


def arrange_voiceover(task, plan, audio_assets, assets):
    tracks = deepcopy(task['variant']['tracks'])
    video = next(track for track in tracks if track['id']==plan['videoTrackId'])
    video['clips'] = []
    voice_clips = []
    cues = []
    position = 0.0
    for text, original, audio in zip(plan['texts'], plan['clips'], audio_assets):
        duration = audio['duration']
        clip = {**original, 'start': position, 'duration': duration, 'volume': 0,
                'fadeIn': min(original['fadeIn'], duration), 'fadeOut': min(original['fadeOut'], duration)}
        video['clips'].append(clip)
        voice_clips.append({'id':uuid4().hex,'assetId':audio['id'],'start':position,'inPoint':0,'duration':duration,
                            'speed':1,'volume':1,'fadeIn':0,'fadeOut':0})
        cues.append({'id':uuid4().hex,'start':position,'end':position+duration,'text':text})
        position += duration
    tracks = [track for track in tracks if track['id']!='voiceover']
    # Retiming pictures must also bound existing music, or FFmpeg produces a black tail.
    for track in tracks:
        if track['kind'] == 'audio':
            trimmed = []
            for clip in track['clips']:
                if clip['start'] >= position:
                    continue
                duration = min(clip['duration'], position-clip['start'])
                trimmed.append({**clip, 'duration': duration, 'fadeIn': min(clip['fadeIn'], duration),
                                'fadeOut': min(clip['fadeOut'], duration)})
            track['clips'] = trimmed
    tracks.append({'id':'voiceover','name':'AI 配音','kind':'audio','muted':False,'hidden':False,'clips':voice_clips})
    try:
        validate_workspace({'revision':task['variant']['revision'],'settings':task['variant']['settings'],'tracks':tracks},
                           {**assets, **{a['id']:a for a in audio_assets}})
    except ValueError as error:
        raise ValueError(f'画面无法覆盖实际配音时长，请补充更长素材或手动调整镜头速度后重试：{error}') from None
    return tracks, cues
