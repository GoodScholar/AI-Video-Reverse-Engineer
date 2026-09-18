import json
import subprocess
from pathlib import Path
import pytest
from app.character_motion_media import normalize_driver, validate_video
from app.character_motion import MotionSettings

def test_normalize_driver_preserves_motion_timing_and_bounds_clip(tmp_path):
    source=tmp_path/'source.mp4';target=tmp_path/'driver.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=64x48:rate=30:duration=3','-c:v','libx264',str(source)],check=True)
    normalize_driver(source,target,MotionSettings(frames=17,fps=8),'ffmpeg')
    data=validate_video(target,'ffprobe')
    assert data['avg_frame_rate']=='8/1'
    assert int(data['nb_frames'])==17
    assert abs(float(data['duration'])-17/8)<.02

def test_short_driver_holds_last_frame(tmp_path):
    source=tmp_path/'source.mp4';target=tmp_path/'driver.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=red:size=64x48:rate=8:duration=1','-c:v','libx264',str(source)],check=True)
    normalize_driver(source,target,MotionSettings(frames=17,fps=8),'ffmpeg')
    assert int(validate_video(target,'ffprobe')['nb_frames'])==17

def test_non_video_output_is_rejected(tmp_path):
    file=tmp_path/'invalid.mp4';file.write_bytes(b'not a video')
    with pytest.raises(ValueError):validate_video(file,'ffprobe')
