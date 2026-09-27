import { HistoryCleanup } from "./HistoryCleanup";
import { ClipWaveform } from "./ClipWaveform";
import { snappedStart } from "./timelineSnapping";
import { createDraftCache } from "./draftStorage";
import { ShotClipReplacement, type ShotResult } from "./ShotClipReplacement";
import { type ChangeEvent, type KeyboardEvent, type PointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Copy, Download, Eye, EyeOff, LoaderCircle, Mic, MicOff, Pause, Plus, Save, Scissors, Trash2, Undo2, Redo2, Volume2, VolumeX } from "lucide-react";

import { preflightTimeline, type TimelinePreflight, cancelTimelineRun, getTimeline, importShotResults, saveTimeline, validateTimelineDraft, startTimelineRun, timelineOutputUrl, uploadTimelineAudio, type TimelineAsset, type TimelineClip, type TimelineFormat, type TimelineTrack, type TimelineTrackKind, type TimelineWorkspace } from "./timelineApi";
import "./timeline.css";

type Props = { onDraftChange?: (dirty: boolean) => void; projectId: string; preproductionRevision?: number; preparationDirty?: boolean; shotResults?: ShotResult[]; focusClip?: { trackId: string; clipId: string } | null };
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
async function readStoredDraft(cache: ReturnType<typeof createDraftCache>): Promise<Draft | null> {
  try {
    const saved = JSON.parse(await cache.read() ?? "null") as { revision?: number; draft?: Draft } | null;
    const draft = saved?.draft;
    return draft && Number.isInteger(draft.revision) && draft.revision >= 0 && draft.settings && Array.isArray(draft.tracks) ? draft : null;
  } catch { return null; }
}

function clipLabel(asset: TimelineAsset | undefined) { return asset?.name ?? "缺失素材"; }

export function TimelineEditor({ projectId, onDraftChange, preproductionRevision, preparationDirty = false, shotResults = [], focusClip }: Props) {
  const draftCache = useMemo(() => createDraftCache(draftStorageKey(projectId)), [projectId]);
  const [workspace, setWorkspace] = useState<TimelineWorkspace | null>(null);
  const [pendingImport, setPendingImport] = useState<{ draft: Draft; baseline: string } | null>(null);
  const draftInputRef = useRef<HTMLInputElement>(null);
  const [conflictDraft, setConflictDraft] = useState<Draft | null>(null);
  const [savedSnapshot, setSavedSnapshot] = useState("");
  const [history, setHistory] = useState<Draft[]>([]);
  const [future, setFuture] = useState<Draft[]>([]);
  const preparationVersionRef = useRef(preproductionRevision);
  preparationVersionRef.current = preproductionRevision;
  const [preflight, setPreflight] = useState<TimelinePreflight | null>(null);
  const [checkFormat, setCheckFormat] = useState<TimelineFormat>("mp4");
  useEffect(() => { setPreflight(null); }, [projectId, preproductionRevision]);
  const [selectedClip, setSelectedClip] = useState<SelectedClip>(null);
  const appliedFocus = useRef<Props["focusClip"]>(null);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [waveformErrors, setWaveformErrors] = useState<Record<string, string>>({});
  const onWaveformError = useCallback((assetId: string, message: string) => setWaveformErrors((current) => current[assetId] === message ? current : { ...current, [assetId]: message }), []);
  useEffect(() => setWaveformErrors({}), [projectId]);
  const [waveformRefresh, setWaveformRefresh] = useState(0);
  const [snapping, setSnapping] = useState(true);
  const [pixelsPerSecond, setPixelsPerSecond] = useState(100);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [storageError, setStorageError] = useState("");
  const [importNotice, setImportNotice] = useState("");
  const [importFocus, setImportFocus] = useState<{ trackId: string; clipId: string; start: number } | null>(null);
  const workareaRef = useRef<HTMLDivElement>(null);
  const [recording, setRecording] = useState(false);
  const mounted = useRef(true);
  const projectRef = useRef(projectId);
  const workspaceRef = useRef<TimelineWorkspace | null>(null);
  const savedSnapshotRef = useRef(savedSnapshot);
  const busyRef = useRef<string | null>(busy);
  const recordingPendingRef = useRef(false);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const previewRef = useRef<HTMLVideoElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const dragRef = useRef<{ trackId: string; clipId: string; pointerX: number; start: number } | null>(null);
  const dragSnapshotRef = useRef<Draft | null>(null);
  workspaceRef.current = workspace;
  savedSnapshotRef.current = savedSnapshot;
  busyRef.current = busy;

  const dirty = Boolean(workspace && snapshot(draftOf(workspace)) !== savedSnapshot);
  useEffect(() => {
    if (!workspace || !focusClip || appliedFocus.current === focusClip) return;
    const track = workspace.tracks.find((item) => item.id === focusClip.trackId);
    const clip = track?.clips.find((item) => item.id === focusClip.clipId);
    if (!track || !clip) return;
    appliedFocus.current = focusClip;
    setSelectedTrackId(track.id); setSelectedClip({ trackId: track.id, clipId: clip.id }); setPlayhead(clip.start);
    setImportFocus({ trackId: track.id, clipId: clip.id, start: clip.start });
  }, [focusClip, workspace]);
  useEffect(() => { if (!loading) onDraftChange?.(dirty || Boolean(conflictDraft)); }, [dirty, conflictDraft, loading, onDraftChange]);
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
    if (current && next.revision < current.revision) return;
    const preserveDraft = Boolean(current && snapshot(draftOf(current)) !== (submitted ?? savedSnapshotRef.current));
    const accepted = preserveDraft && current
      ? { ...next, revision: submitted ? next.revision : current.revision, settings: current.settings, tracks: current.tracks }
      : next;
    workspaceRef.current = accepted;
    setWorkspace(accepted);
    if (submitted || !preserveDraft) {
      savedSnapshotRef.current = snapshot(draftOf(next));
      setSavedSnapshot(savedSnapshotRef.current);
    }
    setSelectedTrackId((currentId) => currentId && accepted.tracks.some((track) => track.id === currentId) ? currentId : accepted.tracks[0]?.id ?? null);
    setSelectedClip((currentSelection) => currentSelection && accepted.tracks.some((track) => track.id === currentSelection.trackId && track.clips.some((clip) => clip.id === currentSelection.clipId)) ? currentSelection : null);
  }

  function loadDraft(next: TimelineWorkspace, draft: Draft | null) {
    if (!mounted.current || projectRef.current !== projectId) return;
    const conflict = draft && draft.revision !== next.revision ? draft : null;
    const current = draft && !conflict ? { ...next, settings: draft.settings, tracks: draft.tracks } : next;
    workspaceRef.current = current;
    savedSnapshotRef.current = snapshot(draftOf(next));
    setConflictDraft(conflict); setWorkspace(current); setSavedSnapshot(savedSnapshotRef.current);
    setHistory([]); setFuture([]); setSelectedClip(null); setSelectedTrackId(current.tracks[0]?.id ?? null);
  }

  async function reloadWorkspace() {
    if (busy) return;
    setLoading(true); setError("");
    try {
      const next = await getTimeline(projectId);
      const current = workspaceRef.current;
      const draft = current && snapshot(draftOf(current)) !== savedSnapshotRef.current ? draftOf(current) : await readStoredDraft(draftCache);
      loadDraft(next, draft);
    } catch (reason) { if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法读取剪辑时间线。")); }
    finally { if (mounted.current && projectRef.current === projectId) setLoading(false); }
  }

  function resolveDraft(restoreDraft: boolean) {
    if (!workspace || !conflictDraft) return;
    if (restoreDraft) {
      const next = { ...workspace, settings: conflictDraft.settings, tracks: conflictDraft.tracks };
      workspaceRef.current = next; setWorkspace(next); setSelectedTrackId(next.tracks[0]?.id ?? null);
    }
    setConflictDraft(null);
  }

  function exportConflictDraft() {
    const draft = conflictDraft ?? (workspace && draftOf(workspace));
    if (!draft) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({ projectId, ...draft }, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = `timeline-draft-${projectId}-v${draft.revision}.json`;
    document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
  }

  async function importDraft(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file || !workspace || busyRef.current) return;
    const baseline = snapshot(draftOf(workspace));
    setBusy("import-draft"); setError(""); setPendingImport(null);
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error("草稿 JSON 不能超过 2 MB。");
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("无法读取草稿文件。"));
        reader.readAsText(file);
      });
      const value = JSON.parse(text);
      if (!value || value.projectId !== projectId) throw new Error("草稿不属于当前项目，无法恢复素材引用。");
      const checked = await validateTimelineDraft(projectId, { revision: value.revision, settings: value.settings, tracks: value.tracks });
      if (!mounted.current || projectRef.current !== projectId) return;
      if (snapshot(workspaceRef.current && draftOf(workspaceRef.current)) !== baseline) throw new Error("校验期间编辑已变化，请重新导入草稿。");
      setPendingImport({ draft: checked, baseline });
    } catch (reason) {
      if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法读取草稿 JSON。"));
    } finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }

  function restoreImportedDraft() {
    if (!pendingImport || !workspace || busy) return;
    if (snapshot(draftOf(workspace)) !== pendingImport.baseline) {
      setError("预览后编辑已变化，请重新导入草稿。"); setPendingImport(null); return;
    }
    edit((current) => ({ ...current, settings: pendingImport.draft.settings, tracks: pendingImport.draft.tracks }));
    setSelectedClip(null); setSelectedTrackId(pendingImport.draft.tracks[0]?.id ?? null);
    setPendingImport(null); setImportNotice("已恢复为可撤销的编辑草稿，请检查后保存时间线。");
  }

  useEffect(() => {
    mounted.current = true; projectRef.current = projectId;
    workspaceRef.current = null; savedSnapshotRef.current = ""; setBusy(null); setRecording(false); setConflictDraft(null);
    setPendingImport(null); setLoading(true); setError(""); setImportNotice(""); setImportFocus(null); setWorkspace(null); setSavedSnapshot(""); setHistory([]); setFuture([]); setSelectedClip(null); setSelectedTrackId(null);
    void getTimeline(projectId).then(async (next) => {
      if (!mounted.current || projectRef.current !== projectId) return;
      loadDraft(next, await readStoredDraft(draftCache));
    }).catch((reason) => {
      if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法读取剪辑时间线。"));
    }).finally(() => { if (mounted.current && projectRef.current === projectId) setLoading(false); });
    return () => { mounted.current = false; if (recorderRef.current?.state === "recording") recorderRef.current.stop(); streamRef.current?.getTracks().forEach((track) => track.stop()); recorderRef.current = null; streamRef.current = null; };
  }, [projectId]);

  useEffect(() => {
    if (!workspace || loading || conflictDraft || workspaceRef.current !== workspace) return;
    let active = true;
    void draftCache.write(dirty ? JSON.stringify({ revision: workspace.revision, draft: draftOf(workspace) }) : null)
      .then(() => { if (active) setStorageError(""); })
      .catch((reason) => { if (active) setStorageError(errorMessage(reason, "浏览器无法长期保存草稿，请保存时间线或导出草稿 JSON。")); });
    return () => { active = false; };
  }, [dirty, projectId, workspace, loading, conflictDraft, draftCache]);

  useEffect(() => {
    if (!hasActiveRun || conflictDraft || loading) return undefined;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending || busyRef.current) return;
      pending = true;
      void getTimeline(projectId).then((next) => {
        if (mounted.current && projectRef.current === projectId && !busyRef.current) accept(next);
      }).catch(() => undefined).finally(() => { pending = false; });
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [hasActiveRun, projectId, conflictDraft, loading]);

  useEffect(() => {
    if (!importFocus) return;
    const area = workareaRef.current;
    const clip = area && Array.from(area.querySelectorAll<HTMLButtonElement>("[data-clip-id]")).find((element) => element.dataset.clipId === importFocus.clipId && element.dataset.trackId === importFocus.trackId);
    if (area && clip) {
      clip.focus({ preventScroll: true });
      clip.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      area.scrollLeft = Math.max(0, importFocus.start * pixelsPerSecond - 24);
    }
    setImportFocus(null);
  }, [importFocus, pixelsPerSecond]);

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
      return { ...current, settings: next.settings, tracks: next.tracks };
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
    if (!workspace || !selectedTrack || workspace.tracks.reduce((total, track) => total + track.clips.length, 0) >= 80 || (selectedTrack.kind === "video" && asset.kind === "audio") || (selectedTrack.kind === "audio" && asset.kind === "image")) return;
    const clip = { id: id("clip"), assetId: asset.id, start: playhead, inPoint: 0, duration: asset.duration && asset.duration > 0 ? Math.min(asset.duration, 10) : 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 };
    updateTrack(selectedTrack.id, (track) => ({ ...track, clips: [...track.clips, clip] })); setSelectedClip({ trackId: selectedTrack.id, clipId: clip.id });
  }
  function splitClip() {
    if (!selected || workspace!.tracks.reduce((count, track) => count + track.clips.length, 0) >= 80 || playhead <= selected.clip.start || playhead >= selected.clip.start + selected.clip.duration) return;
    const firstDuration = playhead - selected.clip.start;
    const second = { ...selected.clip, id: id("clip"), start: playhead, inPoint: selected.clip.inPoint + firstDuration * selected.clip.speed, duration: selected.clip.duration - firstDuration, fadeOut: Math.min(selected.clip.fadeOut, selected.clip.duration - firstDuration), fadeIn: 0 };
    updateTrack(selected.track.id, (track) => ({ ...track, clips: track.clips.flatMap((clip) => clip.id === selected.clip.id ? [{ ...clip, duration: firstDuration, fadeIn: Math.min(clip.fadeIn, firstDuration), fadeOut: 0 }, second] : [clip]) }));
    setSelectedClip({ trackId: selected.track.id, clipId: second.id });
  }
  function copyClip() {
    if (!selected || workspace!.tracks.reduce((count, track) => count + track.clips.length, 0) >= 80) return;
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
    try { accept(await saveTimeline(projectId, draftOf(workspace)), submitted); }
    catch (reason) { if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法保存时间线。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  async function importResults() {
    if (!workspace || dirty || preparationDirty || busy || preproductionRevision === undefined) return;
    setBusy("import-results"); setError(""); setImportNotice("");
    const submitted = snapshot(draftOf(workspace));
    const previousIds = new Set(workspace.tracks.map((track) => track.id));
    try {
      const next = await importShotResults(projectId, workspace.revision, preproductionRevision);
      if (!mounted.current || projectRef.current !== projectId || (workspaceRef.current && next.revision < workspaceRef.current.revision)) return;
      const hasNewDraft = workspaceRef.current && snapshot(draftOf(workspaceRef.current)) !== submitted;
      accept(next);
      if (hasNewDraft) {
        setImportNotice("导入请求已完成，当前草稿已保留；请先处理草稿与已保存版本的差异。");
        return;
      }
      const added = next.tracks.filter((track) => !previousIds.has(track.id));
      const clips = added.flatMap((track) => track.clips);
      if (!clips.length) { setImportNotice("这批镜头结果已在时间线中，未重复添加。"); return; }
      const start = Math.min(...clips.map((clip) => clip.start));
      const end = Math.max(...clips.map((clip) => clip.start + clip.duration));
      setImportNotice(`已导入 ${clips.length} 个片段，时间范围 ${Number(start.toFixed(3))}–${Number(end.toFixed(3))} 秒；已追加到新轨道并保存。`);
      const track = added.find((item) => item.clips.length)!;
      const first = [...track.clips].sort((a, b) => a.start - b.start)[0];
      setSelectedTrackId(track.id); setSelectedClip({ trackId: track.id, clipId: first.id }); setPlayhead(first.start);
      setImportFocus({ trackId: track.id, clipId: first.id, start: first.start });
    }
    catch (reason) { if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法导入镜头结果。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  async function submitRun(format: TimelineFormat, checkOnly = false) {
    if (!workspace || dirty || busy) return;
    setBusy(`run-${format}`); setError("");
    const submitted = snapshot(draftOf(workspace));
    const submittedPreparation = preproductionRevision;
    const submittedAssets = JSON.stringify(workspace.assets);
    setPreflight(null);
    try {
      const result = await preflightTimeline(projectId, workspace.revision, format);
      if (!mounted.current || projectRef.current !== projectId) return;
      if (!workspaceRef.current || snapshot(draftOf(workspaceRef.current)) !== submitted || preparationVersionRef.current !== submittedPreparation || JSON.stringify(workspaceRef.current.assets) !== submittedAssets) {
        setError("预检期间素材或时间线已变化，请保存后重新检查。"); return;
      }
      setPreflight(result);
      if (checkOnly || !result.ready) return;
      accept(await startTimelineRun(projectId, workspace.revision, format));
    }
    catch (reason) { if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法提交渲染任务。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  async function cancel(runId: string) {
    if (busy) return;
    setBusy(`cancel-${runId}`); setError("");
    try { accept(await cancelTimelineRun(projectId, runId)); }
    catch (reason) { if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法取消渲染任务。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  async function uploadAudio(file: File) {
    if (busy) return;
    setBusy("upload-audio"); setError("");
    try { await uploadTimelineAudio(projectId, file); accept(await getTimeline(projectId)); }
    catch (reason) { if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法上传音频素材。")); }
    finally { if (mounted.current && projectRef.current === projectId) setBusy(null); }
  }
  function onAudioUpload(event: ChangeEvent<HTMLInputElement>) { const file = event.target.files?.[0]; event.target.value = ""; if (file) void uploadAudio(file); }
  async function beginRecording() {
    if (recording || busy || recordingPendingRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { setError("当前浏览器不支持本地录音。请上传音频文件。"); return; }
    setError(""); recordingPendingRef.current = true;
    let acquiredStream: MediaStream | null = null;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      acquiredStream = stream;
      if (!mounted.current || projectRef.current !== projectId) { stream.getTracks().forEach((track) => track.stop()); return; }
      const mime = mediaRecorderMime(); const chunks: BlobPart[] = [];
      const recorder = mime ? new (MediaRecorder as RecorderCtor)(stream, { mimeType: mime }) : new (MediaRecorder as RecorderCtor)(stream);
      streamRef.current = stream; recorderRef.current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      const stopTimer = window.setTimeout(() => { if (recorder.state === "recording") recorder.stop(); }, 299_000);
      recorder.onstop = () => {
        window.clearTimeout(stopTimer);
        const usedMime = recorder.mimeType || mime || "audio/webm";
        stream.getTracks().forEach((track) => track.stop()); streamRef.current = null; recorderRef.current = null;
        if (mounted.current && projectRef.current === projectId) { setRecording(false); if (chunks.length) void uploadAudio(new File([new Blob(chunks, { type: usedMime })], `本地录音-${Date.now()}.${extensionFor(usedMime)}`, { type: usedMime })); }
      };
      recorder.start(); setRecording(true);
    } catch (reason) { acquiredStream?.getTracks().forEach((track) => track.stop()); if (mounted.current && projectRef.current === projectId) setError(errorMessage(reason, "无法启用麦克风。请检查浏览器权限后重试。")); }
    finally { recordingPendingRef.current = false; }
  }
  function stopRecording() { if (recorderRef.current?.state === "recording") recorderRef.current.stop(); }
  function seek(value: number) {
    setPlayhead(value);
    if (previewRef.current && Number.isFinite(previewRef.current.duration)) previewRef.current.currentTime = Math.min(value, previewRef.current.duration);
  }
  function onClipPointerDown(event: PointerEvent<HTMLButtonElement>, trackId: string, clip: TimelineClip) {
    if (busy || event.button !== 0) return;
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture?.(event.pointerId); dragRef.current = { trackId, clipId: clip.id, pointerX: event.clientX, start: clip.start }; dragSnapshotRef.current = workspace ? draftOf(workspace) : null;
    setSelectedTrackId(trackId); setSelectedClip({ trackId, clipId: clip.id });
  }
  function onClipPointerMove(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current; if (!drag || busy) return;
    const proposed = clamp(drag.start + (event.clientX - drag.pointerX) / pixelsPerSecond, 0, 300);
    setWorkspace((current) => current ? { ...current, tracks: current.tracks.map((track) => {
      if (track.id !== drag.trackId) return track;
      const moving = track.clips.find((clip) => clip.id === drag.clipId);
      if (!moving) return track;
      const start = snappedStart(proposed, moving, track.id, current.tracks, playhead, pixelsPerSecond, snapping && !event.altKey);
      return { ...track, clips: track.clips.map((clip) => clip.id === drag.clipId ? { ...clip, start } : clip) };
    }) } : current);
  }
  function onClipPointerUp() {
    if (!dragRef.current) return;
    dragRef.current = null;
    const before = dragSnapshotRef.current;
    dragSnapshotRef.current = null;
    if (before && snapshot(before) !== snapshot(workspaceRef.current && draftOf(workspaceRef.current))) {
      setHistory((items) => [...items.slice(-39), before]);
      setFuture([]);
    }
  }

  function onTimelineKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!workspace || busy || dragRef.current || event.defaultPrevented || event.nativeEvent.isComposing || event.altKey) return;
    const target = event.target as HTMLElement;
    if (target.closest("input,textarea,select,video,audio,[contenteditable]:not([contenteditable='false']),[role='textbox']")) return;
    const key = event.key.toLowerCase();
    const command = event.metaKey || event.ctrlKey;
    if (command && key === "z") {
      event.preventDefault();
      const items = event.shiftKey ? future : history;
      if (items.length) restore(items[items.length - 1], event.shiftKey ? "redo" : "undo");
    } else if (event.ctrlKey && !event.metaKey && key === "y" && !event.shiftKey) {
      event.preventDefault(); if (future.length) restore(future[future.length - 1], "redo");
    } else if (command && key === "s") {
      event.preventDefault(); if (dirty && !event.repeat) void save();
    } else if (command && key === "d") {
      event.preventDefault(); if (!event.repeat) copyClip();
    } else if (!command && !event.shiftKey && key === "s") {
      event.preventDefault(); if (!event.repeat) splitClip();
    } else if (!command && !event.shiftKey && (key === "delete" || key === "backspace")) {
      event.preventDefault(); if (!event.repeat) deleteClip();
    } else if (!command && (key === "arrowleft" || key === "arrowright")) {
      event.preventDefault(); seek(clamp(playhead + (key === "arrowright" ? 1 : -1) * (event.shiftKey ? 10 : 1) / workspace.settings.fps, 0, 300));
    }
  }

  if (loading) return <section className="timeline-editor"><p className="timeline-empty">正在读取复刻剪辑时间线…</p></section>;
  if (!workspace) return <section className="timeline-editor"><p className="timeline-empty">{error || "无法读取复刻剪辑时间线。"}</p><button type="button" className="secondary-action" onClick={() => void reloadWorkspace()}>重新读取</button></section>;

  if (conflictDraft) return <section className="timeline-editor timeline-draft-conflict" aria-labelledby="timeline-conflict-title">
    <h2 id="timeline-conflict-title">发现版本冲突草稿</h2>
    <p>本地草稿基于版本 {conflictDraft.revision}，已保存时间线为版本 {workspace.revision}。草稿仍已保留，请选择处理方式。</p>
    <p>草稿含 {conflictDraft.tracks.length} 条轨道、{conflictDraft.tracks.reduce((count, track) => count + track.clips.length, 0)} 个片段；已保存版本含 {workspace.tracks.length} 条轨道、{workspace.tracks.reduce((count, track) => count + track.clips.length, 0)} 个片段。</p>
    <p>恢复只进入编辑，不会自动保存。之后点击保存将用草稿编排替换当前已保存编排；期间若其他窗口再次更新，保存仍会拒绝覆盖。</p>
    <div className="timeline-header-actions"><button type="button" className="primary-action" onClick={() => resolveDraft(true)}>恢复草稿并继续编辑</button><button type="button" className="secondary-action" onClick={exportConflictDraft}>另存草稿 JSON</button><button type="button" className="secondary-action" onClick={() => resolveDraft(false)}>放弃草稿，使用已保存版本</button></div>
  </section>;

  const maxDuration = Math.max(30, ...workspace.tracks.flatMap((track) => track.clips.map((clip) => clip.start + clip.duration)), ...workspace.assets.map((asset) => asset.duration ?? 0));
  return <section className="timeline-editor" aria-label="复刻剪辑时间线" tabIndex={0} onKeyDown={onTimelineKeyDown}>
    <header className="timeline-header"><div><p className="timeline-kicker">复刻辅助收尾</p><h2>拼接原片节奏与本地音轨</h2><p>预览和导出来自已保存版本；当前草稿不会实时合成。</p></div><div className="timeline-header-actions"><button type="button" className="secondary-action" disabled={!history.length || Boolean(busy)} onClick={() => history.length && restore(history[history.length - 1], "undo")}><Undo2 size={16} />撤销</button><button type="button" className="secondary-action" disabled={!future.length || Boolean(busy)} onClick={() => future.length && restore(future[future.length - 1], "redo")}><Redo2 size={16} />重做</button><button type="button" className="primary-action" disabled={!dirty || Boolean(busy)} onClick={() => void save()}>{busy === "save" ? <LoaderCircle className="loading-spinner" size={16} /> : <Save size={16} />}保存时间线</button></div></header>
    {storageError && <p role="alert" className="timeline-error">{storageError}</p>}
    {error && <div className="timeline-error"><p role="alert">{error}</p><button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void reloadWorkspace()}>重新读取并检查草稿</button></div>}
    <div className="timeline-draft-actions"><button type="button" className="secondary-action" onClick={exportConflictDraft}>导出草稿 JSON</button><button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => draftInputRef.current?.click()}>导入草稿 JSON</button><input hidden ref={draftInputRef} type="file" accept=".json,application/json" aria-label="导入时间线草稿文件" disabled={Boolean(busy)} onChange={(event) => void importDraft(event)} /><p>草稿保存在此浏览器。JSON 仅包含剪辑编排，恢复需使用当前项目的原素材。</p></div>
    {pendingImport && <section className="timeline-import-notice" aria-label="草稿恢复预览"><p>待恢复草稿：版本 {pendingImport.draft.revision}，{pendingImport.draft.tracks.length} 条轨道，{pendingImport.draft.tracks.reduce((count, track) => count + track.clips.length, 0)} 个片段。恢复将替换当前编辑内容，可撤销，不会自动保存。</p><div className="timeline-header-actions"><button type="button" className="primary-action" disabled={Boolean(busy)} onClick={restoreImportedDraft}>恢复导入的草稿</button><button type="button" className="secondary-action" onClick={() => setPendingImport(null)}>取消恢复</button></div></section>}
    {preproductionRevision !== undefined && <div className="timeline-results-import"><button type="button" className="secondary-action" disabled={dirty || preparationDirty || Boolean(busy)} onClick={() => void importResults()}>按镜头导入结果</button><p>按已保存的镜头顺序和计划时长，追加到时间线末尾的新视频轨。镜头顺序、结果素材和时长未变化时不会重复导入。{(dirty || preparationDirty) && "请先保存镜头方案和时间线。"}</p></div>}
    {importNotice && <p role="status" className="timeline-import-notice">{importNotice}</p>}
    <details className="timeline-shortcuts"><summary>快捷键与吸附说明</summary><p>在时间线内使用：S 分割；⌘/Ctrl+D 复制；Delete/Backspace 删除；⌘/Ctrl+Z 撤销；⌘/Ctrl+Shift+Z 或 Ctrl+Y 重做；⌘/Ctrl+S 保存；←/→ 移动播放头一帧，Shift+←/→ 移动十帧。输入框和媒体播放器保留原有操作。</p><p>拖动片段时，首尾在 8 像素内吸附到播放头或其他片段边缘；按住 Alt 临时关闭。移动播放头不会实时合成草稿画面。</p></details>
    <p className="timeline-waveform-help">显示源音频波形，随入点、时长、速度和缩放变化，不代表混音后的音量。<button type="button" className="secondary-action" onClick={() => setWaveformRefresh((value) => value + 1)}>重新加载波形</button></p>
    {Object.entries(waveformErrors).some(([assetId, message]) => message && workspace.tracks.some((track) => track.clips.some((clip) => clip.assetId === assetId))) && <ul className="timeline-waveform-errors" role="status">{Object.entries(waveformErrors).filter(([assetId, message]) => message && workspace.tracks.some((track) => track.clips.some((clip) => clip.assetId === assetId))).map(([assetId, message]) => <li key={assetId}>{workspace.assets.find((asset) => asset.id === assetId)?.name ?? "素材"}：{message} 剪辑仍可继续。</li>)}</ul>}
    <div className="timeline-toolbar"><button type="button" className="secondary-action" aria-pressed={snapping} disabled={Boolean(busy)} onClick={() => setSnapping((current) => !current)}>吸附{snapping ? "：开" : "：关"}</button><label>缩放<select aria-label="缩放" value={pixelsPerSecond} onChange={(event) => setPixelsPerSecond(Number(event.target.value))}>{scaleOptions.map((scale) => <option key={scale} value={scale}>{scale}%</option>)}</select></label><label>播放头（秒）<input aria-label="播放头（秒）" type="number" min="0" max="300" step="0.1" value={playhead} onChange={(event) => seek(clamp(numberInput(event.target.value, playhead), 0, 300))} /></label><button type="button" className="secondary-action" disabled={!selected || Boolean(busy)} onClick={splitClip}><Scissors size={16} />按播放头分割</button><button type="button" className="secondary-action" disabled={!selected || Boolean(busy)} onClick={copyClip}><Copy size={16} />复制片段</button><button type="button" className="secondary-action" disabled={!selected || Boolean(busy)} onClick={deleteClip}><Trash2 size={16} />删除片段</button></div>
    <div className="timeline-layout"><aside className="timeline-assets"><h3>可用素材</h3><p>导入原片片段、白模画面或本地音轨，添加到选中轨道。</p><button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => audioInputRef.current?.click()}><Plus size={16} />导入音频</button><input ref={audioInputRef} hidden aria-label="导入音频" type="file" accept="audio/*,video/*" disabled={Boolean(busy)} onChange={onAudioUpload} /><button type="button" className="secondary-action" disabled={recording || Boolean(busy)} onClick={() => void beginRecording()}><Mic size={16} />开始本地录音</button><button type="button" className="secondary-action" disabled={!recording} onClick={stopRecording}><MicOff size={16} />停止并上传</button><ul>{workspace.assets.map((asset) => <li key={asset.id}><strong>{asset.name}</strong><small>{asset.kind}{asset.duration ? ` · ${asset.duration} 秒` : ""}</small><button type="button" disabled={!selectedTrack || (selectedTrack.kind === "video" && asset.kind === "audio") || (selectedTrack.kind === "audio" && asset.kind === "image") || Boolean(busy)} onClick={() => addAsset(asset)}>添加到选中轨道</button></li>)}</ul></aside>
      <div className="timeline-workarea" ref={workareaRef}><div className="timeline-ruler" style={{ width: `${maxDuration * pixelsPerSecond + 150}px` }}>{Array.from({ length: Math.ceil(maxDuration / 5) + 1 }, (_, index) => <span key={index} style={{ left: `${118 + index * 5 * pixelsPerSecond}px` }}>{index * 5}s</span>)}<i style={{ left: `${118 + playhead * pixelsPerSecond}px` }} aria-hidden="true" /></div><div className="timeline-tracks">{workspace.tracks.map((track, index) => <div key={track.id} className={track.id === selectedTrack?.id ? "timeline-track is-selected" : "timeline-track"}><div className="timeline-track-label"><button type="button" onClick={() => setSelectedTrackId(track.id)}>{track.name}<small>{track.kind === "video" ? "画面" : "音频"}</small></button><div><button type="button" aria-label={`${track.muted ? "取消静音" : "静音"}${track.name}`} onClick={() => updateTrack(track.id, (item) => ({ ...item, muted: !item.muted }))}>{track.muted ? <VolumeX size={15} /> : <Volume2 size={15} />}</button>{track.kind === "video" && <button type="button" aria-label={`${track.hidden ? "显示" : "隐藏"}${track.name}`} onClick={() => updateTrack(track.id, (item) => ({ ...item, hidden: !item.hidden }))}>{track.hidden ? <EyeOff size={15} /> : <Eye size={15} />}</button>}<button type="button" aria-label={`上移${track.name}`} disabled={index === 0} onClick={() => reorderTrack(track.id, -1)}>↑</button><button type="button" aria-label={`下移${track.name}`} disabled={index === workspace.tracks.length - 1} onClick={() => reorderTrack(track.id, 1)}>↓</button></div></div><div className="timeline-track-canvas" style={{ width: `${maxDuration * pixelsPerSecond + 150}px` }} onPointerDown={(event) => { if (event.target === event.currentTarget) seek(clamp(event.nativeEvent.offsetX / pixelsPerSecond, 0, 300)); }}>{track.clips.map((clip) => <button key={clip.id} type="button" data-clip-id={clip.id} data-track-id={track.id} aria-label={clipLabel(workspace.assets.find((asset) => asset.id === clip.assetId))} className={selectedClip?.clipId === clip.id && selectedClip.trackId === track.id ? "timeline-clip is-selected" : "timeline-clip"} style={{ left: `${clip.start * pixelsPerSecond}px`, width: `${Math.max(32, clip.duration * pixelsPerSecond)}px` }} onPointerDown={(event) => onClipPointerDown(event, track.id, clip)} onClick={() => { setSelectedTrackId(track.id); setSelectedClip({ trackId: track.id, clipId: clip.id }); }} onPointerMove={onClipPointerMove} onPointerUp={onClipPointerUp} onPointerCancel={onClipPointerUp}><span className="timeline-clip-name">{clipLabel(workspace.assets.find((asset) => asset.id === clip.assetId))}</span>{workspace.assets.find((asset) => asset.id === clip.assetId)?.kind !== "image" && <ClipWaveform projectId={projectId} clip={clip} pixelsPerSecond={pixelsPerSecond} refresh={waveformRefresh} onError={onWaveformError} />}</button>)}</div></div>)}</div><div className="timeline-track-actions"><button type="button" className="secondary-action" disabled={workspace.tracks.length >= 8 || Boolean(busy)} onClick={() => addTrack("video")}><Plus size={16} />添加画面轨</button><button type="button" className="secondary-action" disabled={workspace.tracks.length >= 8 || Boolean(busy)} onClick={() => addTrack("audio")}><Plus size={16} />添加音频轨</button>{selectedTrack && <button type="button" className="secondary-action" disabled={workspace.tracks.length <= 1 || Boolean(busy)} onClick={() => { edit((current) => ({ ...current, tracks: current.tracks.filter((track) => track.id !== selectedTrack.id) })); setSelectedTrackId(workspace.tracks.find((track) => track.id !== selectedTrack.id)?.id ?? null); }}>删除选中轨道</button>}</div></div>
      <aside className="timeline-inspector"><h3>片段参数</h3>{selected ? <><p>{clipLabel(selected.asset)}</p>{selected.track.kind === "video" && shotResults.length > 0 && <ShotClipReplacement key={`${selected.track.id}:${selected.clip.id}`} clip={selected.clip} assets={workspace.assets} shots={shotResults} disabled={Boolean(busy) || preparationDirty} onReplace={(assetId) => { updateClip(selected.track.id, selected.clip.id, (clip) => ({ ...clip, assetId })); setImportNotice("已替换选中片段，剪辑参数保持不变；请检查后保存。"); }} />}<div className="timeline-form-grid">{([ ["start", "时间线起点", 0, 300, 0.01], ["inPoint", "源入点", 0, selected.asset?.duration ?? 300, 0.01], ["duration", "输出时长", 0.01, 300, 0.01], ["speed", "速度", 0.25, 4, 0.01], ["volume", "音量", 0, 2, 0.01], ["fadeIn", "淡入（秒）", 0, selected.clip.duration, 0.01], ["fadeOut", "淡出（秒）", 0, selected.clip.duration, 0.01] ] as const).map(([field, label, min, max, step]) => <label key={field}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={selected.clip[field]} onChange={(event) => updateClip(selected.track.id, selected.clip.id, (clip) => ({ ...clip, [field]: clamp(numberInput(event.target.value, clip[field]), min, max) }))} /></label>)}</div></> : <p className="timeline-empty">选择一个片段后精确调整切入、时长、速度和音量。</p>}<h3>输出设置</h3><div className="timeline-form-grid"><label>宽度<input aria-label="宽度" type="number" min="64" max="1920" step="2" value={workspace.settings.width} onChange={(event) => edit((current) => ({ ...current, settings: { ...current.settings, width: clamp(numberInput(event.target.value, current.settings.width), 64, 1920) } }))} /></label><label>高度<input aria-label="高度" type="number" min="64" max="1920" step="2" value={workspace.settings.height} onChange={(event) => edit((current) => ({ ...current, settings: { ...current.settings, height: clamp(numberInput(event.target.value, current.settings.height), 64, 1920) } }))} /></label><label>帧率<select aria-label="帧率" value={workspace.settings.fps} onChange={(event) => edit((current) => ({ ...current, settings: { ...current.settings, fps: Number(event.target.value) as 24 | 25 | 30 } }))}><option value="24">24 fps</option><option value="25">25 fps</option><option value="30">30 fps</option></select></label></div></aside></div>
    <section className="timeline-preflight" aria-label="素材与导出预检">
      <h3>素材与导出预检</h3>
      <p>检查已保存版本的文件、音视频流与片段区间。每次导出都会重新检查；预检不代替最终渲染验证。</p>
      <label>预检格式<select value={checkFormat} disabled={Boolean(busy)} onChange={(event) => setCheckFormat(event.target.value as TimelineFormat)}><option value="mp4">MP4 视频</option><option value="wav">WAV 音频</option></select></label>
      <button type="button" className="secondary-action" disabled={dirty || Boolean(busy) || hasActiveRun} onClick={() => void submitRun(checkFormat, true)}>检查素材与导出</button>
      {busy?.startsWith("run-") && <p role="status">正在检查素材并处理导出请求…</p>}
      {dirty && <p>请先保存当前剪辑，再进行预检。</p>}
      {preflight && <div role="status"><p>{dirty || preflight.revision !== workspace.revision ? "检查结果已过期，请保存后重新检查。" : preflight.ready ? "预检通过，可提交导出。" : "预检发现阻断问题，请修复后重试。"} · {preflight.format.toUpperCase()} · 版本 {preflight.revision}</p>
        <ul>{preflight.issues.map((issue, index) => <li key={index}><strong>{issue.level === "error" ? "需修复" : "提醒"} · {issue.label}</strong><p>{issue.message}</p>{issue.trackId && issue.clipId && <button type="button" className="secondary-action" aria-label={`定位${issue.label}`} onClick={() => {
          const track = workspace.tracks.find((item) => item.id === issue.trackId);
          const clip = track?.clips.find((item) => item.id === issue.clipId);
          if (track && clip) { setSelectedTrackId(track.id); setSelectedClip({ trackId: track.id, clipId: clip.id }); setPlayhead(clip.start); setImportFocus({ trackId: track.id, clipId: clip.id, start: clip.start }); }
        }}>定位片段</button>}</li>)}</ul></div>}
    </section>
    <section className="timeline-render"><div><h3>已保存版本渲染</h3><p>{dirty ? "当前草稿尚未保存；下方结果仍对应较早版本。" : `当前已保存版本：${workspace.revision}`}</p></div><div className="timeline-render-actions"><button type="button" className="primary-action" disabled={dirty || hasActiveRun || Boolean(busy)} onClick={() => void submitRun("preview")}>{busy === "run-preview" ? <LoaderCircle className="loading-spinner" size={16} /> : <Pause size={16} />}渲染预览 MP4</button><button type="button" className="secondary-action" disabled={dirty || hasActiveRun || Boolean(busy)} onClick={() => void submitRun("mp4")}><Download size={16} />导出 MP4</button><button type="button" className="secondary-action" disabled={dirty || hasActiveRun || Boolean(busy)} onClick={() => void submitRun("wav")}><Download size={16} />导出 WAV</button></div>{latestPreview && <figure className="timeline-preview"><video ref={previewRef} onTimeUpdate={(event) => setPlayhead(event.currentTarget.currentTime)} controls preload="metadata" src={latestPreview.url ?? timelineOutputUrl(projectId, latestPreview.id)} /><figcaption>{latestPreview.revision === workspace.revision && !dirty ? "已保存版本预览" : `旧预览：版本 ${latestPreview.revision}，当前版本 ${workspace.revision}`}</figcaption></figure>}<ul className="timeline-runs">{workspace.runs.map((run) => <li key={run.id}><span>{formatLabels[run.format]} · 版本 {run.revision}</span><strong className={`timeline-run--${run.status}`}>{runStatusLabels[run.status]}</strong>{run.error && <span>{run.error}</span>}{activeStatuses.has(run.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void cancel(run.id)}>取消</button>}{(run.status === "failed" || run.status === "cancelled") && <button type="button" className="secondary-action" disabled={dirty || hasActiveRun || Boolean(busy)} onClick={() => void submitRun(run.format)}>重新提交</button>}{!activeStatuses.has(run.status) && <HistoryCleanup<TimelineWorkspace & { cleanupWarning?: string }> label="清理记录" revision={workspace.revision} disabled={dirty || Boolean(busy)}
      previewUrl={`/api/projects/${encodeURIComponent(projectId)}/timeline/runs/${encodeURIComponent(run.id)}/cleanup-preview`}
      deleteUrl={`/api/projects/${encodeURIComponent(projectId)}/timeline/runs/${encodeURIComponent(run.id)}/delete`}
      onBusy={(active) => setBusy(active ? "history-cleanup" : null)} onComplete={(next) => { accept(next); setImportNotice(next.cleanupWarning ?? "渲染历史已清理，原素材和当前时间线保留。"); }} />}
{run.status === "completed" && run.format !== "preview" && <a className="secondary-action" href={run.url ?? timelineOutputUrl(projectId, run.id)} download>下载 {run.format.toUpperCase()}</a>}</li>)}</ul></section>
  </section>;
}
