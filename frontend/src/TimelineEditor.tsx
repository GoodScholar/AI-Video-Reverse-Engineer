import { type ChangeEvent, type PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { Copy, Download, Eye, EyeOff, LoaderCircle, Mic, MicOff, Pause, Plus, Save, Scissors, Trash2, Undo2, Redo2, Volume2, VolumeX } from "lucide-react";

import { cancelTimelineRun, getTimeline, saveTimeline, startTimelineRun, timelineOutputUrl, uploadTimelineAudio, type TimelineAsset, type TimelineClip, type TimelineFormat, type TimelineTrack, type TimelineTrackKind, type TimelineWorkspace } from "./timelineApi";
import "./timeline.css";

type Props = { projectId: string };
type Draft = Pick<TimelineWorkspace, "revision" | "settings" | "tracks">;
type SelectedClip = { trackId: string; clipId: string } | null;
type RecorderCtor = typeof MediaRecorder;

const scaleOptions = [40, 70, 100, 140, 180];
const activeStatuses = new Set(["queued", "running"]);
const formatLabels: Record<TimelineFormat, string> = { preview: "预览 MP4", mp4: "成片 MP4", wav: "音轨 WAV" };
const runStatusLabels = { queued: "排队中", running: "渲染中", completed: "已完成", failed: "失败", cancelled: "已取消" } as const;

function errorMessage(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback; }
function draftOf(workspace: TimelineWorkspace): Draft { return { revision: workspace.revision, settings: workspace.settings, tracks: workspace.tracks }; }
function snapshot(workspace: Draft | null) { return workspace ? JSON.stringify(workspace) : ""; }
function numberInput(value: string, fallback: number) { const next = Number(value); return Number.isFinite(next) ? next : fallback; }
function clamp(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)); }
function id(prefix: string) { return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`; }
function mediaRecorderMime(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((mime) => MediaRecorder.isTypeSupported(mime));
}
function extensionFor(mime: string) { return mime.includes("mp4") ? "m4a" : "webm"; }
function draftStorageKey(projectId: string) { return `aivre:timeline-draft:${projectId}`; }
function readStoredDraft(projectId: string, revision: number): Draft | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(draftStorageKey(projectId)) ?? "null") as { revision?: number; draft?: Draft } | null;
    return saved?.revision === revision && saved.draft ? saved.draft : null;
  } catch { return null; }
}

function clipLabel(asset: TimelineAsset | undefined) { return asset?.name ?? "缺失素材"; }

export function TimelineEditor({ projectId }: Props) {
  const [workspace, setWorkspace] = useState<TimelineWorkspace | null>(null);
  const [savedSnapshot, setSavedSnapshot] = useState("");
  const [history, setHistory] = useState<Draft[]>([]);
  const [future, setFuture] = useState<Draft[]>([]);
  const [selectedClip, setSelectedClip] = useState<SelectedClip>(null);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [pixelsPerSecond, setPixelsPerSecond] = useState(100);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [recording, setRecording] = useState(false);
  const mounted = useRef(true);
  const projectRef = useRef(projectId);
  const workspaceRef = useRef<TimelineWorkspace | null>(null);
  const savedSnapshotRef = useRef(savedSnapshot);
  const busyRef = useRef<string | null>(busy);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const dragRef = useRef<{ trackId: string; clipId: string; pointerX: number; start: number } | null>(null);
  const dragSnapshotRef = useRef<Draft | null>(null);
  workspaceRef.current = workspace;
  savedSnapshotRef.current = savedSnapshot;
  busyRef.current = busy;

  const dirty = Boolean(workspace && snapshot(draftOf(workspace)) !== savedSnapshot);
  const selected = useMemo(() => {
    if (!workspace || !selectedClip) return null;
    const track = workspace.tracks.find((item) => item.id === selectedClip.trackId);
    const clip = track?.clips.find((item) => item.id === selectedClip.clipId);
    return track && clip ? { track, clip, asset: workspace.assets.find((item) => item.id === clip.assetId) } : null;
  }, [workspace, selectedClip]);
  const selectedTrack = workspace?.tracks.find((track) => track.id === selectedTrackId) ?? workspace?.tracks[0] ?? null;
  const previews = workspace?.runs.filter((run) => run.format === "preview" && run.status === "completed") ?? [];
  const latestPreview = previews[previews.length - 1] ?? null;
  const hasActiveRun = Boolean(workspace?.runs.some((run) => activeStatuses.has(run.status)));

  function accept(next: TimelineWorkspace, submitted?: string) {
    if (!mounted.current || projectRef.current !== projectId) return;
    const current = workspaceRef.current;
    const newerDraft = Boolean(submitted && current && snapshot(draftOf(current)) !== submitted);
    const accepted = newerDraft && current ? { ...next, settings: current.settings, tracks: current.tracks } : next;
    setWorkspace(accepted);
    setSavedSnapshot(snapshot(draftOf(next)));
    setSelectedTrackId((currentId) => currentId && accepted.tracks.some((track) => track.id === currentId) ? currentId : accepted.tracks[0]?.id ?? null);
    setSelectedClip((currentSelection) => currentSelection && accepted.tracks.some((track) => track.id === currentSelection.trackId && track.clips.some((clip) => clip.id === currentSelection.clipId)) ? currentSelection : null);
  }

  useEffect(() => {
    mounted.current = true; projectRef.current = projectId;
    setLoading(true); setError(""); setWorkspace(null); setSavedSnapshot(""); setHistory([]); setFuture([]); setSelectedClip(null); setSelectedTrackId(null);
    void getTimeline(projectId).then((next) => {
      const draft = readStoredDraft(projectId, next.revision);
      if (!draft) { accept(next); return; }
      if (!mounted.current || projectRef.current !== projectId) return;
      setWorkspace({ ...next, settings: draft.settings, tracks: draft.tracks });
      setSavedSnapshot(snapshot(draftOf(next)));
      setSelectedTrackId(draft.tracks[0]?.id ?? null);
    }).catch((reason) => {
      if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法读取剪辑时间线。"));
    }).finally(() => { if (mounted.current && projectRef.current === projectId) setLoading(false); });
    return () => { mounted.current = false; recorderRef.current?.stop(); streamRef.current?.getTracks().forEach((track) => track.stop()); recorderRef.current = null; streamRef.current = null; };
  }, [projectId]);

  useEffect(() => {
    if (!workspace) return;
    try {
      const key = draftStorageKey(projectId);
      if (dirty) sessionStorage.setItem(key, JSON.stringify({ revision: workspace.revision, draft: draftOf(workspace) }));
      else sessionStorage.removeItem(key);
    } catch { /* sessionStorage 不可用时仍保持内存草稿 */ }
  }, [dirty, projectId, workspace]);

  useEffect(() => {
    if (!hasActiveRun || dirty) return undefined;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending || busyRef.current) return;
      pending = true;
      void getTimeline(projectId).then((next) => {
        if (mounted.current && projectRef.current === projectId && !busyRef.current && !dirty) accept(next);
      }).catch(() => undefined).finally(() => { pending = false; });
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [dirty, hasActiveRun, projectId]);

  function edit(change: (current: TimelineWorkspace) => TimelineWorkspace) {
    setWorkspace((current) => {
      if (!current) return current;
      setHistory((items) => [...items.slice(-39), draftOf(current)]);
      setFuture([]);
      return change(current);
    });
  }

  function restore(next: Draft, direction: "undo" | "redo") {
    setWorkspace((current) => {
      if (!current) return current;
      const currentDraft = draftOf(current);
      if (direction === "undo") { setHistory((items) => items.slice(0, -1)); setFuture((items) => [...items, currentDraft]); }
      else { setFuture((items) => items.slice(0, -1)); setHistory((items) => [...items, currentDraft]); }
      return { ...current, ...next };
    });
  }

  function updateTrack(trackId: string, change: (track: TimelineTrack) => TimelineTrack) {
    edit((current) => ({ ...current, tracks: current.tracks.map((track) => track.id === trackId ? change(track) : track) }));
  }
  function updateClip(trackId: string, clipId: string, change: (clip: TimelineClip) => TimelineClip) {
    updateTrack(trackId, (track) => ({ ...track, clips: track.clips.map((clip) => clip.id === clipId ? change(clip) : clip) }));
  }
  function addTrack(kind: TimelineTrackKind) {
    const track = { id: id("track"), name: kind === "video" ? "视频轨道" : "音频轨道", kind, muted: false, hidden: false, clips: [] };
    edit((current) => ({ ...current, tracks: [...current.tracks, track] })); setSelectedTrackId(track.id);
  }
  function reorderTrack(trackId: string, offset: number) {
    edit((current) => {
      const index = current.tracks.findIndex((track) => track.id === trackId); const target = index + offset;
      if (index < 0 || target < 0 || target >= current.tracks.length) return current;
      const tracks = [...current.tracks]; [tracks[index], tracks[target]] = [tracks[target], tracks[index]]; return { ...current, tracks };
    });
  }
  function addAsset(asset: TimelineAsset) {
    if (!workspace || !selectedTrack || workspace.tracks.reduce((total, track) => total + track.clips.length, 0) >= 80 || (selectedTrack.kind === "video" && asset.kind === "audio")) return;
    const clip = { id: id("clip"), assetId: asset.id, start: playhead, inPoint: 0, duration: asset.duration && asset.duration > 0 ? Math.min(asset.duration, 10) : 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 };
    updateTrack(selectedTrack.id, (track) => ({ ...track, clips: [...track.clips, clip] })); setSelectedClip({ trackId: selectedTrack.id, clipId: clip.id });
  }
  function splitClip() {
    if (!selected || playhead <= selected.clip.start || playhead >= selected.clip.start + selected.clip.duration) return;
    const firstDuration = playhead - selected.clip.start;
    const second = { ...selected.clip, id: id("clip"), start: playhead, inPoint: selected.clip.inPoint + firstDuration * selected.clip.speed, duration: selected.clip.duration - firstDuration, fadeIn: 0 };
    updateTrack(selected.track.id, (track) => ({ ...track, clips: track.clips.flatMap((clip) => clip.id === selected.clip.id ? [{ ...clip, duration: firstDuration, fadeOut: 0 }, second] : [clip]) }));
    setSelectedClip({ trackId: selected.track.id, clipId: second.id });
  }
  function copyClip() {
    if (!selected) return;
    const clone = { ...selected.clip, id: id("clip"), start: selected.clip.start + selected.clip.duration };
    updateTrack(selected.track.id, (track) => ({ ...track, clips: [...track.clips, clone] })); setSelectedClip({ trackId: selected.track.id, clipId: clone.id });
  }
  function deleteClip() {
    if (!selected) return;
    updateTrack(selected.track.id, (track) => ({ ...track, clips: track.clips.filter((clip) => clip.id !== selected.clip.id) })); setSelectedClip(null);
  }
  async function save() {
    if (!workspace || busy) return;
    setBusy("save"); setError(""); const submitted = snapshot(draftOf(workspace));
    try { accept(await saveTimeline(projectId, draftOf(workspace)), submitted); sessionStorage.removeItem(draftStorageKey(projectId)); }
    catch (reason) { setError(errorMessage(reason, "无法保存时间线。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  async function submitRun(format: TimelineFormat) {
    if (!workspace || dirty || busy) return;
    setBusy(`run-${format}`); setError("");
    try { accept(await startTimelineRun(projectId, workspace.revision, format)); }
    catch (reason) { setError(errorMessage(reason, "无法提交渲染任务。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  async function cancel(runId: string) {
    if (busy) return;
    setBusy(`cancel-${runId}`); setError("");
    try { accept(await cancelTimelineRun(projectId, runId)); }
    catch (reason) { setError(errorMessage(reason, "无法取消渲染任务。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  async function uploadAudio(file: File) {
    if (busy) return;
    setBusy("upload-audio"); setError("");
    try { await uploadTimelineAudio(projectId, file); accept(await getTimeline(projectId)); }
    catch (reason) { setError(errorMessage(reason, "无法上传音频素材。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  function onAudioUpload(event: ChangeEvent<HTMLInputElement>) { const file = event.target.files?.[0]; event.target.value = ""; if (file) void uploadAudio(file); }
  async function beginRecording() {
    if (recording || busy) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { setError("当前浏览器不支持本地录音。请上传音频文件。"); return; }
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current || projectRef.current !== projectId) { stream.getTracks().forEach((track) => track.stop()); return; }
      const mime = mediaRecorderMime(); const chunks: BlobPart[] = [];
      const recorder = mime ? new (MediaRecorder as RecorderCtor)(stream, { mimeType: mime }) : new (MediaRecorder as RecorderCtor)(stream);
      streamRef.current = stream; recorderRef.current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        const usedMime = recorder.mimeType || mime || "audio/webm";
        stream.getTracks().forEach((track) => track.stop()); streamRef.current = null; recorderRef.current = null;
        if (mounted.current && projectRef.current === projectId) { setRecording(false); if (chunks.length) void uploadAudio(new File([new Blob(chunks, { type: usedMime })], `本地录音-${Date.now()}.${extensionFor(usedMime)}`, { type: usedMime })); }
      };
      recorder.start(); setRecording(true);
    } catch (reason) { setError(errorMessage(reason, "无法启用麦克风。请检查浏览器权限后重试。")); }
  }
  function stopRecording() { recorderRef.current?.stop(); }
  function onClipPointerDown(event: PointerEvent<HTMLButtonElement>, trackId: string, clip: TimelineClip) {
    event.currentTarget.setPointerCapture?.(event.pointerId); dragRef.current = { trackId, clipId: clip.id, pointerX: event.clientX, start: clip.start }; dragSnapshotRef.current = workspace ? draftOf(workspace) : null;
    setSelectedTrackId(trackId); setSelectedClip({ trackId, clipId: clip.id });
  }
  function onClipPointerMove(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current; if (!drag) return;
    const proposed = clamp(drag.start + (event.clientX - drag.pointerX) / pixelsPerSecond, 0, 300);
    setWorkspace((current) => current ? { ...current, tracks: current.tracks.map((track) => {
      if (track.id !== drag.trackId) return track;
      const moving = track.clips.find((clip) => clip.id === drag.clipId);
      if (!moving) return track;
      const start = track.kind === "audio" ? proposed : clamp(proposed, 0, 300);
      const overlaps = track.kind === "video" && track.clips.some((clip) => clip.id !== moving.id && start < clip.start + clip.duration && start + moving.duration > clip.start);
      return { ...track, clips: track.clips.map((clip) => clip.id === drag.clipId ? { ...clip, start: Math.round((overlaps ? moving.start : start) * 100) / 100 } : clip) };
    }) } : current);
  }
  function onClipPointerUp() { if (dragRef.current) { dragRef.current = null; if (dragSnapshotRef.current) { setHistory((items) => [...items.slice(-39), dragSnapshotRef.current!]); setFuture([]); } dragSnapshotRef.current = null; } }

  if (loading) return <section className="timeline-editor"><p className="timeline-empty">正在读取复刻剪辑时间线…</p></section>;
  if (!workspace) return <section className="timeline-editor"><p className="timeline-empty">{error || "无法读取复刻剪辑时间线。"}</p><button type="button" className="secondary-action" onClick={() => { setLoading(true); void getTimeline(projectId).then(accept).catch((reason) => setError(errorMessage(reason, "无法读取剪辑时间线。"))).finally(() => setLoading(false)); }}>重新读取</button></section>;

  const maxDuration = Math.max(30, ...workspace.tracks.flatMap((track) => track.clips.map((clip) => clip.start + clip.duration)), ...workspace.assets.map((asset) => asset.duration ?? 0));
  return <section className="timeline-editor" aria-label="复刻剪辑时间线">
    <header className="timeline-header"><div><p className="timeline-kicker">复刻辅助收尾</p><h2>拼接原片节奏与本地音轨</h2><p>预览和导出来自已保存版本；当前草稿不会实时合成。</p></div><div className="timeline-header-actions"><button type="button" className="secondary-action" disabled={!history.length || Boolean(busy)} onClick={() => history.length && restore(history[history.length - 1], "undo")}><Undo2 size={16} />撤销</button><button type="button" className="secondary-action" disabled={!future.length || Boolean(busy)} onClick={() => future.length && restore(future[future.length - 1], "redo")}><Redo2 size={16} />重做</button><button type="button" className="primary-action" disabled={!dirty || Boolean(busy)} onClick={() => void save()}>{busy === "save" ? <LoaderCircle className="loading-spinner" size={16} /> : <Save size={16} />}保存时间线</button></div></header>
    {error && <p role="alert" className="timeline-error">{error}</p>}
    <div className="timeline-toolbar"><label>缩放<select aria-label="缩放" value={pixelsPerSecond} onChange={(event) => setPixelsPerSecond(Number(event.target.value))}>{scaleOptions.map((scale) => <option key={scale} value={scale}>{scale}%</option>)}</select></label><label>播放头（秒）<input aria-label="播放头（秒）" type="number" min="0" max="300" step="0.1" value={playhead} onChange={(event) => setPlayhead(clamp(numberInput(event.target.value, playhead), 0, 300))} /></label><button type="button" className="secondary-action" disabled={!selected || Boolean(busy)} onClick={splitClip}><Scissors size={16} />按播放头分割</button><button type="button" className="secondary-action" disabled={!selected || Boolean(busy)} onClick={copyClip}><Copy size={16} />复制片段</button><button type="button" className="secondary-action" disabled={!selected || Boolean(busy)} onClick={deleteClip}><Trash2 size={16} />删除片段</button></div>
    <div className="timeline-layout"><aside className="timeline-assets"><h3>可用素材</h3><p>导入原片片段、白模画面或本地音轨，添加到选中轨道。</p><label className="secondary-action"><Plus size={16} />导入音频<input hidden aria-label="导入音频" type="file" accept="audio/*,video/*" disabled={Boolean(busy)} onChange={onAudioUpload} /></label><button type="button" className="secondary-action" disabled={recording || Boolean(busy)} onClick={() => void beginRecording()}><Mic size={16} />开始本地录音</button><button type="button" className="secondary-action" disabled={!recording} onClick={stopRecording}><MicOff size={16} />停止并上传</button><ul>{workspace.assets.map((asset) => <li key={asset.id}><strong>{asset.name}</strong><small>{asset.kind}{asset.duration ? ` · ${asset.duration} 秒` : ""}</small><button type="button" disabled={!selectedTrack || (selectedTrack.kind === "video" && asset.kind === "audio") || Boolean(busy)} onClick={() => addAsset(asset)}>添加到选中轨道</button></li>)}</ul></aside>
      <div className="timeline-workarea"><div className="timeline-ruler" style={{ width: `${maxDuration * pixelsPerSecond + 150}px` }}>{Array.from({ length: Math.ceil(maxDuration / 5) + 1 }, (_, index) => <span key={index} style={{ left: `${index * 5 * pixelsPerSecond}px` }}>{index * 5}s</span>)}<i style={{ left: `${playhead * pixelsPerSecond}px` }} aria-hidden="true" /></div><div className="timeline-tracks">{workspace.tracks.map((track, index) => <div key={track.id} className={track.id === selectedTrack?.id ? "timeline-track is-selected" : "timeline-track"}><div className="timeline-track-label"><button type="button" onClick={() => setSelectedTrackId(track.id)}>{track.name}<small>{track.kind === "video" ? "画面" : "音频"}</small></button><div><button type="button" aria-label={`${track.muted ? "取消静音" : "静音"}${track.name}`} onClick={() => updateTrack(track.id, (item) => ({ ...item, muted: !item.muted }))}>{track.muted ? <VolumeX size={15} /> : <Volume2 size={15} />}</button>{track.kind === "video" && <button type="button" aria-label={`${track.hidden ? "显示" : "隐藏"}${track.name}`} onClick={() => updateTrack(track.id, (item) => ({ ...item, hidden: !item.hidden }))}>{track.hidden ? <EyeOff size={15} /> : <Eye size={15} />}</button>}<button type="button" aria-label={`上移${track.name}`} disabled={index === 0} onClick={() => reorderTrack(track.id, -1)}>↑</button><button type="button" aria-label={`下移${track.name}`} disabled={index === workspace.tracks.length - 1} onClick={() => reorderTrack(track.id, 1)}>↓</button></div></div><div className="timeline-track-canvas" style={{ width: `${maxDuration * pixelsPerSecond + 150}px` }} onPointerDown={(event) => { if (event.target === event.currentTarget) setPlayhead(clamp(event.nativeEvent.offsetX / pixelsPerSecond, 0, 300)); }}>{track.clips.map((clip) => <button key={clip.id} type="button" aria-label={clipLabel(workspace.assets.find((asset) => asset.id === clip.assetId))} className={selectedClip?.clipId === clip.id && selectedClip.trackId === track.id ? "timeline-clip is-selected" : "timeline-clip"} style={{ left: `${clip.start * pixelsPerSecond}px`, width: `${Math.max(32, clip.duration * pixelsPerSecond)}px` }} onPointerDown={(event) => onClipPointerDown(event, track.id, clip)} onPointerMove={onClipPointerMove} onPointerUp={onClipPointerUp}>{clipLabel(workspace.assets.find((asset) => asset.id === clip.assetId))}</button>)}</div></div>)}</div><div className="timeline-track-actions"><button type="button" className="secondary-action" disabled={workspace.tracks.length >= 8 || Boolean(busy)} onClick={() => addTrack("video")}><Plus size={16} />添加画面轨</button><button type="button" className="secondary-action" disabled={workspace.tracks.length >= 8 || Boolean(busy)} onClick={() => addTrack("audio")}><Plus size={16} />添加音频轨</button>{selectedTrack && <button type="button" className="secondary-action" disabled={workspace.tracks.length <= 1 || Boolean(busy)} onClick={() => { edit((current) => ({ ...current, tracks: current.tracks.filter((track) => track.id !== selectedTrack.id) })); setSelectedTrackId(workspace.tracks.find((track) => track.id !== selectedTrack.id)?.id ?? null); }}>删除选中轨道</button>}</div></div>
      <aside className="timeline-inspector"><h3>片段参数</h3>{selected ? <><p>{clipLabel(selected.asset)}</p><div className="timeline-form-grid">{([ ["start", "时间线起点", 0, 300, 0.01], ["inPoint", "源入点", 0, selected.asset?.duration ?? 300, 0.01], ["duration", "输出时长", 0.01, 300, 0.01], ["speed", "速度", 0.25, 4, 0.01], ["volume", "音量", 0, 2, 0.01], ["fadeIn", "淡入（秒）", 0, selected.clip.duration, 0.01], ["fadeOut", "淡出（秒）", 0, selected.clip.duration, 0.01] ] as const).map(([field, label, min, max, step]) => <label key={field}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={selected.clip[field]} onChange={(event) => updateClip(selected.track.id, selected.clip.id, (clip) => ({ ...clip, [field]: clamp(numberInput(event.target.value, clip[field]), min, max) }))} /></label>)}</div></> : <p className="timeline-empty">选择一个片段后精确调整切入、时长、速度和音量。</p>}<h3>输出设置</h3><div className="timeline-form-grid"><label>宽度<input aria-label="宽度" type="number" min="64" max="1920" step="2" value={workspace.settings.width} onChange={(event) => edit((current) => ({ ...current, settings: { ...current.settings, width: clamp(numberInput(event.target.value, current.settings.width), 64, 1920) } }))} /></label><label>高度<input aria-label="高度" type="number" min="64" max="1920" step="2" value={workspace.settings.height} onChange={(event) => edit((current) => ({ ...current, settings: { ...current.settings, height: clamp(numberInput(event.target.value, current.settings.height), 64, 1920) } }))} /></label><label>帧率<select aria-label="帧率" value={workspace.settings.fps} onChange={(event) => edit((current) => ({ ...current, settings: { ...current.settings, fps: Number(event.target.value) as 24 | 25 | 30 } }))}><option value="24">24 fps</option><option value="25">25 fps</option><option value="30">30 fps</option></select></label></div></aside></div>
    <section className="timeline-render"><div><h3>已保存版本渲染</h3><p>{dirty ? "当前草稿尚未保存；下方结果仍对应较早版本。" : `当前已保存版本：${workspace.revision}`}</p></div><div className="timeline-render-actions"><button type="button" className="primary-action" disabled={dirty || Boolean(busy)} onClick={() => void submitRun("preview")}>{busy === "run-preview" ? <LoaderCircle className="loading-spinner" size={16} /> : <Pause size={16} />}渲染预览 MP4</button><button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => void submitRun("mp4")}><Download size={16} />导出 MP4</button><button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => void submitRun("wav")}><Download size={16} />导出 WAV</button></div>{latestPreview && <figure className="timeline-preview"><video controls preload="metadata" src={latestPreview.url ?? timelineOutputUrl(projectId, latestPreview.id)} /><figcaption>{latestPreview.revision === workspace.revision && !dirty ? "已保存版本预览" : `旧预览：版本 ${latestPreview.revision}，当前版本 ${workspace.revision}`}</figcaption></figure>}<ul className="timeline-runs">{workspace.runs.map((run) => <li key={run.id}><span>{formatLabels[run.format]} · 版本 {run.revision}</span><strong className={`timeline-run--${run.status}`}>{runStatusLabels[run.status]}</strong>{run.error && <span>{run.error}</span>}{activeStatuses.has(run.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void cancel(run.id)}>取消</button>}{(run.status === "failed" || run.status === "cancelled") && <button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => void submitRun(run.format)}>重新提交</button>}{run.status === "completed" && run.format !== "preview" && <a className="secondary-action" href={run.url ?? timelineOutputUrl(projectId, run.id)} download>下载 {run.format.toUpperCase()}</a>}</li>)}</ul></section>
  </section>;
}
