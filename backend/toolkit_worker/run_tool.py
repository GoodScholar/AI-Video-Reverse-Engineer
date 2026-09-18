#!/usr/bin/env python3
"""Isolated adapters; heavy model libraries are imported only inside their task."""
from __future__ import annotations
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.video_toolkit import ToolkitConfig, ToolkitError, validate_cuts
from app.video_upscale import UpscaleConfig, UpscaleError, execute_upscale, inspect_video


def command(args,timeout=3600):
    subprocess.run(args,check=True,stdout=subprocess.DEVNULL,timeout=timeout)


def scenes(source,directory,params,config,meta):
    from scenedetect import detect, AdaptiveDetector
    detector=AdaptiveDetector(adaptive_threshold=params['threshold'],min_scene_len=max(1,round(params['minSceneSeconds']*float(meta['fps']))))
    shots=detect(str(source),detector,show_progress=False)
    cuts=[start.get_seconds() for start,_ in shots if start.get_seconds()>0]
    data={'duration':meta['duration'],'cuts':validate_cuts(cuts,meta['duration']),'revision':0,'detector':'PySceneDetect AdaptiveDetector'}
    (directory/'scenes.json').write_text(json.dumps(data),encoding='utf-8')
    return ['scenes.json']


def subtitles(source,directory,params,config,meta):
    if not meta['audio']:raise ToolkitError('此视频没有音轨，无法提取字幕。')
    wav=directory/'audio.wav'
    command([config.ffmpeg,'-v','error','-nostdin','-y','-i',str(source),'-map','0:a:0','-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)])
    command([config.whisper,'-m',config.whisper_model,'-f',str(wav),'-l',params['language'],'-osrt','-oj','-of',str(directory/'subtitles')])
    srt=directory/'subtitles.srt'
    if not srt.is_file() or not (directory/'subtitles.json').is_file():raise ToolkitError('字幕工具没有生成预期的 SRT 与 JSON 文件。')
    if not srt.stat().st_size:srt.write_text('\n',encoding='utf-8')
    json.loads((directory/'subtitles.json').read_text())
    return ['subtitles.srt','subtitles.json']


def _budget(directory,bytes_needed):
    if bytes_needed>3_000_000_000 or shutil.disk_usage(directory).free<bytes_needed+512_000_000:
        raise ToolkitError('帧缓存预计超过 3 GB 或可用空间不足，请先裁短视频。')


def interpolate(source,directory,params,config,meta):
    from PIL import Image, ImageChops, ImageStat
    fps=params['fps'];source_fps=float(meta['fps'])
    if fps<=source_fps:raise ToolkitError('目标帧率必须高于素材帧率。')
    if meta['duration']>60 or max(meta['width'],meta['height'])>3840:
        raise ToolkitError('补帧首版支持最长 60 秒、最长边不超过 3840 的素材，请先裁短或缩小。')
    _budget(directory,math.ceil(meta['duration']*source_fps)*meta['width']*meta['height']*3)
    frames=directory/'rife-frames';frames.mkdir()
    command([config.ffmpeg,'-v','error','-nostdin','-y','-i',str(source),'-vf',f'fps={meta["fps"]},tpad=stop_mode=clone:stop_duration={max(0,meta["duration"]-meta["videoDuration"])}','-start_number','0',str(frames/'%06d.png')])
    files=sorted(frames.glob('*.png'))
    if not files:raise ToolkitError('视频没有可解码帧。')
    width,height=meta['width'],meta['height']
    if width%2 or height%2:raise ToolkitError('补帧输入尺寸必须为偶数，请先转码。')
    encoder=subprocess.Popen([config.ffmpeg,'-v','error','-nostdin','-y','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{width}x{height}','-framerate',str(fps),'-i','pipe:0','-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(directory/'silent.mp4')],stdin=subprocess.PIPE)
    previous=-1;cut=False
    try:
        for frame in range(math.ceil(meta['duration']*fps)):
            position=frame*source_fps/fps
            index=min(int(position),len(files)-1);next_index=min(index+1,len(files)-1)
            fraction=position-int(position)
            if previous!=index:
                with Image.open(files[index]) as a,Image.open(files[next_index]) as b:
                    difference=ImageChops.difference(a.convert('RGB').resize((48,48)),b.convert('RGB').resize((48,48)))
                    cut=sum(ImageStat.Stat(difference).mean)/3>65
                previous=index
            generated=frames/'intermediate.png'
            if fraction<1e-6 or index==next_index or cut:
                chosen=files[index]
            else:
                command([config.rife,'-0',str(files[index]),'-1',str(files[next_index]),'-o',str(generated),'-m',config.rife_models,'-s',str(fraction),'-j','1:1:1'],timeout=120)
                chosen=generated
            with Image.open(chosen) as image:
                if image.size!=(width,height):raise ToolkitError('补帧引擎返回的尺寸不正确。')
                encoder.stdin.write(image.convert('RGB').tobytes())
        encoder.stdin.close()
        if encoder.wait(timeout=120):raise ToolkitError('补帧输出编码失败。')
    finally:
        if encoder.poll() is None:encoder.kill();encoder.wait()
    output=directory/'output.mp4'
    command([config.ffmpeg,'-v','error','-nostdin','-y','-i',str(directory/'silent.mp4'),'-i',str(source),'-map','0:v:0','-map','1:a:0?','-c:v','copy','-c:a','aac','-t',str(meta['duration']),'-map_metadata','-1','-movflags','+faststart',str(output)])
    result=inspect_video(output,UpscaleConfig.local(config.ffmpeg,config.ffprobe))
    if abs(result['duration']-meta['duration'])>.1 or float(result['fps'])!=fps or result['audio']!=meta['audio']:
        raise ToolkitError('补帧结果的时长、帧率或音轨校验失败。')
    return ['output.mp4']


def mask(source,directory,params,config,meta):
    import numpy as np
    import torch
    from PIL import Image
    from sam2.build_sam import build_sam2_video_predictor
    if meta['duration']>30:raise ToolkitError('主体跟踪首版支持最长 30 秒，请先裁短视频。')
    fps=8
    ratio=min(1,640/max(meta['width'],meta['height']))
    width=max(2,round(meta['width']*ratio/2)*2);height=max(2,round(meta['height']*ratio/2)*2)
    frames=directory/'frames';frames.mkdir()
    command([config.ffmpeg,'-v','error','-nostdin','-y','-i',str(source),'-vf',f'fps={fps},scale={width}:{height}','-q:v','2','-start_number','0',str(frames/'%06d.jpg')])
    device='cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    predictor=build_sam2_video_predictor(config.sam_config,config.sam_checkpoint,device=device)
    points=np.array([[p[0]*width,p[1]*height] for p in params['points']],dtype=np.float32)
    labels=np.array([p[2] for p in params['points']],dtype=np.int32)
    encoders=[];records=[]
    try:
        for name in ('mask.mp4','overlay.mp4'):
            encoders.append(subprocess.Popen([config.ffmpeg,'-v','error','-nostdin','-y','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{width}x{height}','-framerate',str(fps),'-i','pipe:0','-an','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(directory/name)],stdin=subprocess.PIPE))
        with torch.inference_mode():
            state=predictor.init_state(str(frames),offload_video_to_cpu=True,offload_state_to_cpu=True)
            predictor.add_new_points_or_box(inference_state=state,frame_idx=0,obj_id=1,points=points,labels=labels)
            for index,_,logits in predictor.propagate_in_video(state):
                selected=(logits[0,0]>0).cpu().numpy()
                with Image.open(frames/f'{index:06d}.jpg') as frame:image=np.array(frame.convert('RGB'))
                monochrome=np.repeat((selected.astype(np.uint8)*255)[:,:,None],3,axis=2)
                overlay=image.copy();overlay[selected]=(image[selected]*.5+np.array([30,220,130])*.5).astype(np.uint8)
                encoders[0].stdin.write(monochrome.tobytes());encoders[1].stdin.write(overlay.tobytes())
                ys,xs=np.where(selected)
                records.append({'frame':int(index),'time':index/fps,'area':int(selected.sum()),'box':None if not len(xs) else [int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())]})
        if len(records)!=len(list(frames.glob('*.jpg'))):raise ToolkitError('主体跟踪结果缺少帧。')
        for encoder in encoders:
            encoder.stdin.close()
            if encoder.wait(timeout=120):raise ToolkitError('遮罩编码失败。')
    finally:
        for encoder in encoders:
            if encoder.poll() is None:encoder.kill();encoder.wait()
    (directory/'tracking.json').write_text(json.dumps({'fps':fps,'width':width,'height':height,'frames':records,'missingFrames':sum(r['area']==0 for r in records)}))
    return ['mask.mp4','overlay.mp4','tracking.json']


def execute(request):
    config=ToolkitConfig(**request['config'])
    source=Path(request['source']);directory=Path(request['directory']);params=request['params'];kind=request['kind']
    meta=inspect_video(source,UpscaleConfig.local(config.ffmpeg,config.ffprobe))
    if kind=='upscale':
        execute_upscale(source,directory,params['resolution'],UpscaleConfig.local(config.ffmpeg,config.ffprobe),lambda *_:None)
        artifacts=['output.mp4']
    else:
        artifacts={'scenes':scenes,'subtitles':subtitles,'mask':mask,'interpolate':interpolate}[kind](source,directory,params,config,meta)
    (directory/'result.json').write_text(json.dumps({'artifacts':artifacts}))


if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text())
    try:execute(request)
    except Exception as error:
        message=str(error) if isinstance(error,(ToolkitError,UpscaleError)) else '处理失败，请检查依赖、模型、显存或素材编码。'
        (Path(request['directory'])/'error.json').write_text(json.dumps({'message':message},ensure_ascii=False))
        import traceback
        traceback.print_exc()
        sys.exit(1)
