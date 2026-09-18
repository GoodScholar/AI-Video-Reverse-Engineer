import importlib.util
import json
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
import pytest
from app.video_toolkit import ToolkitConfig, execute_tool, ToolkitCancelled, ToolkitError
from app.video_toolkit_api import ToolParams


def source(path, audio=True):
    cmd=['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=32x24:rate=6:duration=1']
    if audio:cmd+=['-f','lavfi','-i','sine=duration=1','-c:a','aac']
    subprocess.run(cmd+['-c:v','libx264',str(path)],check=True)


def test_rife_pipeline_real_decode_encode_and_audio(tmp_path):
    clip=tmp_path/'clip.mp4';source(clip)
    engine=tmp_path/'rife';script=tmp_path/'rife.py'
    script.write_text('''from PIL import Image
import sys
p=dict(zip(sys.argv[1::2],sys.argv[2::2]))
with Image.open(p['-0']) as a, Image.open(p['-1']) as b:
 Image.blend(a,b,float(p['-s'])).save(p['-o'])
''')
    engine.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' "$@"\n');engine.chmod(0o755)
    config=replace(ToolkitConfig.local(),rife=str(engine),rife_models=str(tmp_path))
    directory=tmp_path/'run';directory.mkdir()
    names=execute_tool(clip,directory,'interpolate',ToolParams(fps=30).model_dump(),config,lambda:False,lambda *_:None)
    assert names==['output.mp4']
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(directory/'output.mp4')]))
    video=next(s for s in data['streams'] if s['codec_type']=='video')
    assert video['avg_frame_rate']=='30/1'
    assert any(s['codec_type']=='audio' for s in data['streams'])
    assert abs(float(video['duration'])-1)<.04


def test_no_audio_is_clear_error_before_whisper(tmp_path):
    clip=tmp_path/'clip.mp4';source(clip,False)
    directory=tmp_path/'run';directory.mkdir()
    with pytest.raises(ToolkitError,match='没有音轨'):
        execute_tool(clip,directory,'subtitles',ToolParams().model_dump(),ToolkitConfig.local(),lambda:False,lambda *_:None)


def test_cancellation_stops_worker(tmp_path):
    clip=tmp_path/'clip.mp4';source(clip)
    directory=tmp_path/'run';directory.mkdir()
    with pytest.raises(ToolkitCancelled):
        execute_tool(clip,directory,'interpolate',ToolParams().model_dump(),ToolkitConfig.local(),lambda:True,lambda *_:None)

def test_cancel_terminates_external_tool_and_its_child(tmp_path):
    import os
    import time
    clip=tmp_path/'clip.mp4';source(clip)
    directory=tmp_path/'run';directory.mkdir()
    fake=tmp_path/'whisper';script=tmp_path/'whisper.py'
    script.write_text('''import json,subprocess,sys,time,os
from pathlib import Path
p=dict(zip(sys.argv[1:7:2],sys.argv[2:8:2]))
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
Path(sys.argv[-1]+'.pids').write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(120)
''')
    fake.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' "$@"\n');fake.chmod(0o755)
    config=replace(ToolkitConfig.local(),whisper=str(fake))
    marker=directory/'subtitles.pids';start=__import__('time').monotonic()
    with pytest.raises(ToolkitCancelled):
        execute_tool(clip,directory,'subtitles',ToolParams().model_dump(),config,lambda:marker.exists() or __import__('time').monotonic()-start>10,lambda *_:None)
    assert marker.exists()
    for pid in json.loads(marker.read_text()):
        for _ in range(30):
            info=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
            if not info or info.startswith('Z'):break
            time.sleep(.1)
        assert not info or info.startswith('Z')


def test_real_scene_detector_in_isolated_environment(tmp_path):
    config=ToolkitConfig.local()
    if not config.environment()['scenes']['available']:pytest.skip('optional scene worker is not installed')
    clip=tmp_path/'cut.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=red:size=160x120:rate=24:duration=1','-f','lavfi','-i','color=blue:size=160x120:rate=24:duration=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264',str(clip)],check=True)
    directory=tmp_path/'run';directory.mkdir()
    execute_tool(clip,directory,'scenes',ToolParams().model_dump(),config,lambda:False,lambda *_:None)
    data=json.loads((directory/'scenes.json').read_text())
    assert data['cuts']==[1.0] and data['duration']==2
