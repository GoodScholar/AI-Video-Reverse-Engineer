"""Bounded, local media preparation for character motion workflows."""
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4


def normalize_driver(source, target, settings, ffmpeg):
    """Use the first requested seconds; hold the final frame for short drivers."""
    temporary=target.with_name(f'.{uuid4()}.mp4')
    duration=settings.frames/settings.fps
    try:
        subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-i',str(source),
            '-map','0:v:0','-map','0:a:0?','-vf',f'fps={settings.fps},tpad=stop_mode=clone:stop_duration={duration}',
            '-frames:v',str(settings.frames),'-t',str(duration),'-c:v','libx264','-preset','fast',
            '-pix_fmt','yuv420p','-c:a','aac','-map_metadata','-1','-movflags','+faststart',str(temporary)],
            capture_output=True,timeout=180,check=True)
        os.replace(temporary,target)
    except (OSError,subprocess.SubprocessError) as error:
        raise ValueError('动作驱动视频预处理失败，请检查 FFmpeg 和素材编码。') from error
    finally:temporary.unlink(missing_ok=True)


def validate_video(path, ffprobe):
    try:
        data=json.loads(subprocess.run([ffprobe,'-v','error','-show_streams','-of','json',str(path)],
            capture_output=True,timeout=30,check=True).stdout)
        video=next(item for item in data['streams'] if item.get('codec_type')=='video')
        if int(video['width'])<=0 or int(video['height'])<=0:raise ValueError()
        return video
    except (OSError,subprocess.SubprocessError,ValueError,KeyError,StopIteration) as error:
        raise ValueError('生成服务返回的文件不是有效视频。') from error
