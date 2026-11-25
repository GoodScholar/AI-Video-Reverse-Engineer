/* React adaptation of Toonflow d86cce6 nodeScaffold/components/videoPlayer.vue (MIT). */
import * as React from "react";
import { Camera, Pause, Play, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react";
import "./creativeCanvasMediaPlayer.css";

export type VideoFrameKind = "current" | "first" | "last";
export type CreativeCanvasMediaPlayerHandle = { enterFullscreen: () => Promise<void> };
type Props = { src: string; label: string; onCaptureFrame?: (file: File, kind: VideoFrameKind) => void | Promise<void> };

function formatTime(value: number) {
  const seconds = Math.floor(Number.isFinite(value) ? Math.max(0, value) : 0);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function stopCanvas(event: React.SyntheticEvent) { event.stopPropagation(); }

export const CreativeCanvasMediaPlayer = React.forwardRef<CreativeCanvasMediaPlayerHandle, Props>(function CreativeCanvasMediaPlayer({ src, label, onCaptureFrame }, ref) {
  const video = React.useRef<HTMLVideoElement>(null);
  const captureController = React.useRef<AbortController>();
  const alive = React.useRef(true);
  const lastVolume = React.useRef(100);
  const [ready, setReady] = React.useState(false);
  const [playing, setPlaying] = React.useState(false);
  const [currentTime, setCurrentTime] = React.useState(0);
  const [duration, setDuration] = React.useState(0);
  const [volume, setVolumeState] = React.useState(100);
  const [muted, setMuted] = React.useState(false);
  const [captureOpen, setCaptureOpen] = React.useState(false);
  const [capturing, setCapturing] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    captureController.current?.abort();
    setReady(false); setPlaying(false); setCurrentTime(0); setDuration(0); setCaptureOpen(false); setError("");
  }, [src]);
  React.useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; captureController.current?.abort(); };
  }, []);

  const enterFullscreen = React.useCallback(async () => {
    if (!video.current) return;
    try { await video.current.requestFullscreen(); }
    catch { if (alive.current) setError("无法进入视频全屏"); }
  }, []);
  React.useImperativeHandle(ref, () => ({ enterFullscreen }), [enterFullscreen]);

  const readVolume = () => {
    const current = video.current; if (!current) return;
    const next = Math.round(current.volume * 100);
    setVolumeState(next); setMuted(current.muted);
    if (next > 0) lastVolume.current = next;
  };
  const seek = (value: number) => {
    const current = video.current; if (!current || !Number.isFinite(value)) return;
    current.currentTime = Math.min(duration, Math.max(0, value)); setCurrentTime(current.currentTime);
  };
  const togglePlayback = async () => {
    const current = video.current; if (!current) return;
    if (playing) { current.pause(); return; }
    try { await current.play(); }
    catch (reason) { if (!(reason instanceof DOMException && reason.name === "AbortError") && alive.current) setError("视频播放失败"); }
  };
  const toggleMute = () => {
    const current = video.current; if (!current) return;
    const silent = current.muted || current.volume === 0;
    if (current.volume === 0) current.volume = lastVolume.current / 100;
    current.muted = !silent; readVolume();
  };
  const adjustVolume = (value: number) => {
    const current = video.current; if (!current || !Number.isFinite(value)) return;
    current.volume = Math.min(100, Math.max(0, value)) / 100; current.muted = current.volume === 0; readVolume();
  };

  const loadFrame = (source: HTMLVideoElement, time: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => finish(new Error("视频帧读取超时")), 15_000);
    const abort = () => finish(signal.reason ?? new DOMException("已取消", "AbortError"));
    const finish = (reason?: unknown) => {
      clearTimeout(timer); source.onloadeddata = null; source.onseeked = null; source.onerror = null; signal.removeEventListener("abort", abort);
      reason ? reject(reason) : resolve();
    };
    source.onloadeddata = () => { if (time === 0) finish(); else source.currentTime = time; };
    source.onseeked = () => finish(); source.onerror = () => finish(new Error("无法读取视频帧"));
    signal.addEventListener("abort", abort, { once: true }); source.src = src; source.load(); if (signal.aborted) abort();
  });
  const downloadFrame = (file: File) => {
    const url = URL.createObjectURL(file); const link = document.createElement("a");
    link.href = url; link.download = file.name; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const captureFrame = async (kind: VideoFrameKind) => {
    const current = video.current; if (!current || !ready || capturing) return;
    captureController.current?.abort(); const controller = new AbortController(); captureController.current = controller;
    setCapturing(true); setCaptureOpen(false); setError(""); let frameVideo: HTMLVideoElement | undefined;
    try {
      let source = current;
      if (kind !== "current") {
        frameVideo = document.createElement("video"); frameVideo.muted = true; frameVideo.preload = "auto";
        await loadFrame(frameVideo, kind === "first" ? 0 : Math.max(0, duration - 0.001), controller.signal); source = frameVideo;
      }
      if (controller.signal.aborted) return;
      const canvas = document.createElement("canvas"); canvas.width = source.videoWidth; canvas.height = source.videoHeight;
      const context = canvas.getContext("2d"); if (!context || !canvas.width || !canvas.height) throw new Error("视频帧尚未就绪");
      context.drawImage(source, 0, 0);
      const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error("截图生成失败")), "image/png"));
      if (controller.signal.aborted) return;
      const labelByKind = { current: "当前帧", first: "首帧", last: "尾帧" } as const;
      const file = new File([blob], `${label} · ${labelByKind[kind]}.png`, { type: "image/png" });
      if (onCaptureFrame) await onCaptureFrame(file, kind); else downloadFrame(file);
    } catch (reason) {
      if (!controller.signal.aborted && alive.current) setError(reason instanceof Error ? reason.message : "截帧失败");
    } finally {
      if (frameVideo) { frameVideo.removeAttribute("src"); frameVideo.load(); }
      if (captureController.current === controller) captureController.current = undefined;
      if (alive.current) setCapturing(false);
    }
  };

  return <div className="creative-media-player" onPointerDown={stopCanvas} onMouseDown={stopCanvas} onDoubleClick={stopCanvas} onKeyDown={stopCanvas}>
    <video ref={video} className="creative-media-player__video" role="application" src={src} playsInline preload="auto" draggable={false} aria-label={label}
      onLoadedMetadata={event => { const next = event.currentTarget.duration; setDuration(Number.isFinite(next) ? next : 0); }} onLoadedData={() => setReady(true)}
      onTimeUpdate={event => setCurrentTime(event.currentTarget.currentTime || 0)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onVolumeChange={readVolume}
      onError={() => { setReady(false); setPlaying(false); setError("无法预览该视频"); }} />
    <div className="creative-media-player__controls" aria-label="视频播放控制">
      <button type="button" className="creative-media-player__play" disabled={!ready} aria-label={playing ? "暂停视频" : "播放视频"} title={playing ? "暂停" : "播放"} onClick={togglePlayback}>{playing ? <Pause size={15} /> : <Play size={15} />}</button>
      <span className="creative-media-player__time">{formatTime(currentTime)}</span>
      <input className="creative-media-player__progress" type="range" min="0" max={duration || 1} step="0.01" value={Math.min(currentTime, duration || 1)} disabled={!ready || !duration} aria-label="视频播放进度" onChange={event => seek(Number(event.target.value))} />
      <span className="creative-media-player__duration">{formatTime(duration)}</span>
      <label className="creative-media-player__volume"><button type="button" disabled={!ready} aria-pressed={muted || !volume} aria-label={muted || !volume ? "取消静音" : "静音视频"} title="音量" onClick={toggleMute}>{muted || !volume ? <VolumeX size={15} /> : <Volume2 size={15} />}</button><input type="range" min="0" max="100" value={muted ? 0 : volume} disabled={!ready} aria-label="视频音量" onChange={event => adjustVolume(Number(event.target.value))} /></label>
      <div className="creative-media-player__capture"><button type="button" disabled={!ready || capturing} aria-expanded={captureOpen} aria-label="截取视频帧" title="截取视频帧" onClick={() => setCaptureOpen(value => !value)}><Camera size={15} /></button>{captureOpen && <div role="menu" aria-label="视频帧导出"><button role="menuitem" type="button" onClick={() => void captureFrame("current")}><Camera size={14} />截取当前帧</button><button role="menuitem" type="button" onClick={() => void captureFrame("first")}><SkipBack size={14} />截取首帧</button><button role="menuitem" type="button" onClick={() => void captureFrame("last")}><SkipForward size={14} />截取尾帧</button></div>}</div>
    </div>
    {error && <p role="alert" className="creative-media-player__error">{error}</p>}
  </div>;
});
