import { useEffect, useRef, useState } from "react";
import { cancelBatchExport, cancelBatchPreview, createBatchTask, createBulkVariants, getBatchWorkspace, recognizeBatchSubtitles, recommendBatchVariant, reviewBatchVariant, saveBatchContent, saveBatchSubtitles, saveBatchVariant, startBatchPreview, startGenerationPreviews, submitBatchExports, type BatchAsset, type BatchCue, type BatchReview, type BatchTask, type BatchVariant } from "./batchEditingApi";
import type { TimelineAsset, TimelineClip, TimelineTrack } from "./timelineApi";
import { AigcCreator } from "./AigcCreator";
import { AspectRatioPicker } from "./AspectRatioPicker";
import "./batch.css";
import { VoiceSelector } from "./VoiceSelector";
import { ratioLabel, resolveProductAspect, type AspectMode } from "./videoAspect";

function clipName(clip: TimelineClip, assets: TimelineAsset[]) { return assets.find((asset) => asset.id === clip.assetId)?.name ?? "缺失素材"; }
function isCurrentPreview(task: BatchTask, run: BatchVariant["runs"][number] | undefined): boolean {
  return Boolean((!task.voiceover || (task.voiceover.status === "completed" && task.voiceover.contentRevision === (task.contentRevision ?? 0))) && run?.status === "completed" && run.revision === task.variant.revision &&
    (run.contentRevision ?? 0) === (task.contentRevision ?? 0) &&
    (run.subtitleRevision ?? 0) === (task.variant.subtitles?.revision ?? 0));
}
function previewCategory(task: BatchTask): "completed" | "queued" | "failed" | "cancelled" | "pending" {
  const run = task.variant.runs[task.variant.runs.length - 1];
  if (run?.status === "queued" || run?.status === "running") return "queued";
  if (run?.status === "failed" || task.generation?.status === "failed") return "failed";
  if (run?.status === "cancelled") return "cancelled";
  if (isCurrentPreview(task, run)) return "completed";
  return "pending";
}
function nextId() { return `clip-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
const reviewLabel = { pending: "待审核", approved: "已通过", rejected: "已退回", stale: "旧审核已失效" };
const exportLabel = { queued: "排队中", running: "导出中", completed: "已完成", failed: "失败", cancelled: "已取消" };
function invalidatedReviewStatus(task: BatchTask): BatchTask["reviewStatus"] {
  return task.reviewStatus === "approved" || task.reviewStatus === "rejected" ? "stale" : task.reviewStatus ?? "pending";
}
type BulkItem = { sellingPoint: string; script: string };
type VariantAspectDraft = { aspectMode: AspectMode; settings: BatchVariant["settings"]; resolvedAspect: string; aspectReason: string };
function reorder(clips: TimelineClip[], index: number, direction: -1 | 1, kind: TimelineTrack["kind"]): TimelineClip[] {
  const gaps = clips.map((clip, position) => position === 0 ? clip.start : Math.max(0, clip.start - clips[position - 1].start - clips[position - 1].duration));
  const next = [...clips];
  [next[index], next[index + direction]] = [next[index + direction], next[index]];
  if (kind === "audio") return next.map((clip, position) => ({ ...clip, start: clips[position].start }));
  let position = 0;
  return next.map((clip, index) => { position += gaps[index]; const moved = { ...clip, start: position }; position += clip.duration; return moved; });
}

export function BatchEditor({ projectId, onDraftChange, onPersistedChange, presentation, focusTaskId, onProgress, onOpenEdit, projectAssets, visible = true }: { projectId: string; onDraftChange?: (dirty: boolean) => void; onPersistedChange?: () => void;
  presentation?: "voice" | "review" | "edit"; focusTaskId?: string; onOpenEdit?: () => void;
  onProgress?: (progress: { hasTasks: boolean; hasPreview: boolean }) => void; projectAssets?: BatchAsset[]; visible?:boolean }) {
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  const [tasks, setTasks] = useState<BatchTask[]>([]);
  const [loadedAssets, setAssets] = useState<BatchAsset[]>([]);
  const assets = projectAssets ?? loadedAssets;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, TimelineTrack[]>>({});
  const [aspectDrafts, setAspectDrafts] = useState<Record<string, VariantAspectDraft>>({});
  const [contentDrafts, setContentDrafts] = useState<Record<string, BulkItem>>({});
  const [reviewReasons, setReviewReasons] = useState<Record<string, string>>({});
  const [selectedExportIds, setSelectedExportIds] = useState<string[]>([]);
  const [subtitleDrafts, setSubtitleDrafts] = useState<Record<string, BatchCue[]>>({});
  const [recommendationSelections, setRecommendationSelections] = useState<Record<string, string[]>>({});
  const [bulkCount, setBulkCount] = useState(5);
  const [bulkInputs, setBulkInputs] = useState<Record<string, BulkItem[]>>({});
  const [sellingPoint, setSellingPoint] = useState("");
  const [script, setScript] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showAigc, setShowAigc] = useState(false);
  const [aigcOpened, setAigcOpened] = useState(false);
  const [aigcDirty, setAigcDirty] = useState(false);
  const [voiceSelections, setVoiceSelections] = useState<Record<string, string>>({});
  const [voicePendingOwners, setVoicePendingOwners] = useState<Record<string, string[]>>({});
  const [voiceSubmittingOwners, setVoiceSubmittingOwners] = useState<Record<string, string[]>>({});
  const voicePending = Object.values(voicePendingOwners).some(ids => ids.length > 0);
  const [reviewFilter, setReviewFilter] = useState<"all" | "pending" | "approved" | "changes">("all");
  useEffect(() => { setVoiceSelections({}); setVoicePendingOwners({}); setVoiceSubmittingOwners({}); }, [projectId]);

  useEffect(() => {
    let active = true;
    setTasks([]); setAssets([]); setSelectedId(null); setDrafts({}); setAspectDrafts({}); setContentDrafts({}); setReviewReasons({}); setSelectedExportIds([]); setSubtitleDrafts({}); setRecommendationSelections({}); setBulkCount(5); setBulkInputs({}); setReviewFilter("all"); setLoading(true); setBusy(false); setError("");
    void getBatchWorkspace(projectId).then((result) => { if (active) { setTasks(result.tasks); setAssets(result.assets); if(presentation) setSelectedId(focusTaskId ?? [...result.tasks].reverse().find(task => !task.batchId)?.id ?? null); } })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取批量任务。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId]);
  useEffect(() => {
    if (!focusTaskId) return;
    let active = true;
    void getBatchWorkspace(projectId).then(result => { if(active && projectRef.current === projectId){setTasks(result.tasks);setAssets(result.assets);setSelectedId(focusTaskId);} })
      .catch(reason => {if(active)setError(reason instanceof Error?reason.message:"无法读取交接任务。");});
    return () => {active=false;};
  }, [projectId, focusTaskId]);
  const hasPreview = tasks.some(task => isCurrentPreview(task, task.variant.runs[task.variant.runs.length - 1]));
  useEffect(() => { onProgress?.({hasTasks:tasks.length>0,hasPreview}); }, [tasks.length,hasPreview,onProgress]);

  async function createTask() {
    setBusy(true); setError("");
    try {
      const result = await createBatchTask(projectId, sellingPoint, script);
      if (projectRef.current !== projectId) return;
      setTasks((current) => [...current, result.task]);
      setSelectedId(result.task.id);
      setSellingPoint(""); setScript("");
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法创建批量任务。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  const selected = tasks.find((task) => task.id === selectedId);
  useEffect(() => {
    if (presentation === "voice" && selected?.batchId) setSelectedId(selected.batchId);
  }, [presentation, selected?.id, selected?.batchId]);
  const draftTracks = selected ? drafts[selected.id] ?? selected.variant.tracks : null;
  const savedAspect = selected ? {
    aspectMode: selected.variant.aspectMode ?? "9:16",
    settings: selected.variant.settings,
    resolvedAspect: selected.variant.resolvedAspect ?? ratioLabel(selected.variant.settings.width, selected.variant.settings.height),
    aspectReason: selected.variant.aspectReason ?? "沿用已保存的视频比例。",
  } : null;
  const smartResolution = selected && draftTracks && savedAspect?.aspectMode === "smart" && !aspectDrafts[selected.id]
    ? resolveProductAspect("smart", assets.filter((asset) => draftTracks.some((track) => track.kind === "video" && !track.hidden &&
      track.clips.some((clip) => clip.assetId === asset.id)))) : null;
  const selectedAspect = selected ? aspectDrafts[selected.id] ?? (smartResolution ? {
    aspectMode: "smart" as const, settings: { width: smartResolution.width, height: smartResolution.height, fps: 30 },
    resolvedAspect: smartResolution.resolvedAspect, aspectReason: smartResolution.reason,
  } : savedAspect) : null;
  const contentDraft = selected ? contentDrafts[selected.id] ?? { sellingPoint: selected.sellingPoint, script: selected.script } : null;
  const contentDirty = Boolean(selected && contentDraft && (contentDraft.sellingPoint !== selected.sellingPoint || contentDraft.script !== selected.script));
  const tracksDirty = Boolean(selected && draftTracks && JSON.stringify(draftTracks) !== JSON.stringify(selected.variant.tracks));
  const aspectDirty = Boolean(selected && selectedAspect && (selectedAspect.aspectMode !== (selected.variant.aspectMode ?? "9:16") ||
    JSON.stringify(selectedAspect.settings) !== JSON.stringify(selected.variant.settings)));
  const dirty = tracksDirty || aspectDirty;
  const subtitles = selected?.variant.subtitles ?? { revision: 0, cues: [], recognitions: [] };
  const subtitleDraft = selected ? subtitleDrafts[selected.id] ?? subtitles.cues : [];
  const subtitleDirty = JSON.stringify(subtitleDraft) !== JSON.stringify(subtitles.cues);
  const subtitleDuration = Math.max(0, ...(draftTracks ?? []).filter((track) => track.kind === "video" && !track.hidden)
    .flatMap((track) => track.clips.map((clip) => clip.start + clip.duration)));
  const subtitlesValid = subtitleDraft.every((cue) => cue.text.trim() && cue.text.length <= 120 && Number.isFinite(cue.start) && Number.isFinite(cue.end) &&
    cue.start >= 0 && cue.end > cue.start && Math.round(cue.end * 1000) > Math.round(cue.start * 1000) && cue.end <= subtitleDuration + 0.002);
  const anyDirty = tasks.some((task) => (contentDrafts[task.id] && (contentDrafts[task.id].sellingPoint !== task.sellingPoint || contentDrafts[task.id].script !== task.script)) ||
    (drafts[task.id] && JSON.stringify(drafts[task.id]) !== JSON.stringify(task.variant.tracks)) ||
    (aspectDrafts[task.id] && (aspectDrafts[task.id].aspectMode !== (task.variant.aspectMode ?? "9:16") ||
      JSON.stringify(aspectDrafts[task.id].settings) !== JSON.stringify(task.variant.settings))) ||
    (subtitleDrafts[task.id] && JSON.stringify(subtitleDrafts[task.id]) !== JSON.stringify(task.variant.subtitles?.cues ?? [])));
  useEffect(() => { onDraftChange?.(anyDirty || aigcDirty || voicePending); }, [anyDirty, aigcDirty, voicePending, onDraftChange]);
  const latestRun = selected?.variant.runs[selected.variant.runs.length - 1];
  const latestExport = selected?.variant.exports?.[selected.variant.exports.length - 1];
  const latestCompleted = [...(selected?.variant.runs ?? [])].reverse().find((run) => run.status === "completed");
  const currentPreview = selected && isCurrentPreview(selected, latestRun) ? latestRun : null;
  const reviewReady = Boolean(currentPreview && selected && !hasPendingVoice(selected) && !dirty && !contentDirty && !subtitleDirty && (reviewReasons[selected?.id ?? ""] ?? "").trim());
  const latestRecognition = subtitles.recognitions[subtitles.recognitions.length - 1];
  const selectedAssets = selected ? recommendationSelections[selected.id] ?? selected.proposal?.assetIds ?? [] : [];
  const selectedChildren = selected && !selected.batchId ? tasks.filter((task) => task.batchId === selected.id) : [];
  const generationIds = [...new Set(selectedChildren.map((task) => task.generationId).filter((id): id is string => Boolean(id)))].reverse();
  const voiceTargets = selectedChildren.length ? selectedChildren.filter(task => task.generationId === generationIds[0]) : selected ? [selected] : [];
  const currentBulkItems = selected ? bulkInputs[selected.id] ?? [] : [];
  const bulkReady = Number.isInteger(bulkCount) && bulkCount >= 5 && bulkCount <= 20 && selectedAssets.length >= 2 &&
    Array.from({ length: bulkCount }, (_, index) => currentBulkItems[index]).every((item) => item?.sellingPoint.trim() && item.script.trim());
  const selectionChanged = Boolean(selected?.proposal && JSON.stringify(selectedAssets) !== JSON.stringify(selected.proposal.assetIds));
  const savedIncludesRecommendation = Boolean(selected?.proposal?.clips.every((clip) =>
    selected.variant.tracks.some((track) => track.kind === "video" && track.clips.some((item) => item.id === clip.id))));

  function hasPendingVoice(task: BatchTask): boolean {
    return Object.values(voicePendingOwners).some(ids => ids.includes(task.id));
  }

  function exportable(task: BatchTask): boolean {
    const run = task.variant.runs[task.variant.runs.length - 1];
    return !hasPendingVoice(task) && task.reviewStatus === "approved" && isCurrentPreview(task, run) &&
      task.variant.settings.fps === 30 && !aspectDrafts[task.id] &&
      (!contentDrafts[task.id] || (contentDrafts[task.id].sellingPoint === task.sellingPoint && contentDrafts[task.id].script === task.script)) &&
      (!drafts[task.id] || JSON.stringify(drafts[task.id]) === JSON.stringify(task.variant.tracks)) &&
      (!subtitleDrafts[task.id] || JSON.stringify(subtitleDrafts[task.id]) === JSON.stringify(task.variant.subtitles?.cues ?? []));
  }

  const childTasks = tasks.filter(task => task.batchId);
  const reviewTasks = childTasks.length ? childTasks : tasks;
  function reviewState(task: BatchTask) {
    const changed = (contentDrafts[task.id] && (contentDrafts[task.id].sellingPoint !== task.sellingPoint || contentDrafts[task.id].script !== task.script)) ||
      (drafts[task.id] && JSON.stringify(drafts[task.id]) !== JSON.stringify(task.variant.tracks)) ||
      (aspectDrafts[task.id] && (aspectDrafts[task.id].aspectMode !== (task.variant.aspectMode ?? "9:16") ||
        JSON.stringify(aspectDrafts[task.id].settings) !== JSON.stringify(task.variant.settings))) ||
      (subtitleDrafts[task.id] && JSON.stringify(subtitleDrafts[task.id]) !== JSON.stringify(task.variant.subtitles?.cues ?? []));
    const status = task.reviewStatus ?? "pending";
    return (status === "approved" || status === "rejected") && (changed || hasPendingVoice(task) || !isCurrentPreview(task, task.variant.runs[task.variant.runs.length - 1])) ? "stale" : status;
  }
  const matchesReview = (task: BatchTask) => reviewFilter === "all" || (reviewFilter === "changes"
    ? reviewState(task) === "rejected" || reviewState(task) === "stale" : reviewState(task) === reviewFilter);
  const filteredReviewTasks = reviewTasks.filter(matchesReview);
  useEffect(() => {
    if (presentation === "review" && !filteredReviewTasks.some(task => task.id === selectedId)) {
      setSelectedId(filteredReviewTasks[0]?.id ?? null);
    }
  }, [presentation, reviewFilter, selectedId, tasks, contentDrafts, drafts, aspectDrafts, subtitleDrafts, voicePendingOwners]);

  function changeBulkItem(index: number, key: keyof BulkItem, value: string) {
    if (!selected) return;
    setBulkInputs((current) => {
      const items = [...(current[selected.id] ?? [])];
      items[index] = { sellingPoint: items[index]?.sellingPoint ?? "", script: items[index]?.script ?? "", [key]: value };
      return { ...current, [selected.id]: items };
    });
  }

  async function createBulk() {
    if (!selected || selected.batchId || !bulkReady) return;
    setBusy(true); setError("");
    try {
      const items = currentBulkItems.slice(0, bulkCount).map((item) => ({ sellingPoint: item.sellingPoint.trim(), script: item.script.trim() }));
      const result = await createBulkVariants(projectId, selected.id, items, selectedAssets);
      if (projectRef.current !== projectId) return;
      setTasks((current) => [...current, ...result.tasks]);
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法批量创建变体。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function previewGeneration(generationId: string) {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      await startGenerationPreviews(projectId, selected.id, generationId);
      if (projectRef.current === projectId) { onPersistedChange?.(); await refresh(false); }
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法批量生成预览。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function exportChildren(taskIds: string[]) {
    if (!taskIds.length) return;
    setBusy(true); setError("");
    try {
      const groups = new Map<string, string[]>();
      for (const task of tasks.filter(task => taskIds.includes(task.id) && task.batchId && exportable(task))) {
        groups.set(task.batchId!, [...(groups.get(task.batchId!) ?? []), task.id]);
      }
      for (const [parentId, ids] of groups) {
        for (let offset = 0; offset < ids.length; offset += 20) {
          await submitBatchExports(projectId, parentId, ids.slice(offset, offset + 20));
          if (projectRef.current !== projectId) return;
        }
      }
      if (projectRef.current !== projectId) return;
      setSelectedExportIds([]);
      onPersistedChange?.();
      await refresh(false);
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法提交成片导出。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function cancelExport(task: BatchTask, runId: string) {
    setBusy(true); setError("");
    try {
      const result = await cancelBatchExport(projectId, task.id, runId);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((item) => item.id === task.id ? result.task : item));
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法取消成片导出。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function updateChildPreview(task: BatchTask, cancel: boolean) {
    const run = task.variant.runs[task.variant.runs.length - 1];
    setBusy(true); setError("");
    try {
      const result = cancel && run
        ? await cancelBatchPreview(projectId, task.id, run.id)
        : await startBatchPreview(projectId, task.id, task.variant.revision);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((item) => item.id === task.id ? { ...item, variant: result.variant } : item));
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法更新预览状态。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  function toggleRecommendationAsset(assetId: string) {
    if (!selected) return;
    setRecommendationSelections((current) => {
      const previous = current[selected.id] ?? selected.proposal?.assetIds ?? [];
      return { ...current, [selected.id]: previous.includes(assetId) ? previous.filter((id) => id !== assetId) : [...previous, assetId] };
    });
  }

  async function recommendVariant() {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const result = await recommendBatchVariant(projectId, selected.id, selected.variant.revision, selectedAssets);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((task) => task.id === selected.id ? { ...task, proposal: result.proposal } : task));
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法依据脚本推荐镜头。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  function applyRecommendation() {
    if (!selected?.proposal || selected.proposal.baseRevision !== selected.variant.revision || dirty || selectionChanged) return;
    const firstVideo = selected.variant.tracks.find((track) => track.kind === "video")?.id;
    setDrafts((current) => ({ ...current, [selected.id]: (current[selected.id] ?? selected.variant.tracks).map((track) =>
      track.id === firstVideo ? { ...track, clips: selected.proposal!.clips } : track) }));
  }

  async function refresh(notify = true) {
    try {
      const result = await getBatchWorkspace(projectId);
      if (projectRef.current !== projectId) return;
      setTasks(result.tasks); setAssets(result.assets);
      setError("");
      if (notify) onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法刷新预览状态。"); }
  }

  async function recognizeSubtitles() {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const result = await recognizeBatchSubtitles(projectId, selected.id, "auto");
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((task) => task.id === selected.id ? { ...task,
        variant: { ...task.variant, subtitles: result.subtitles } } : task));
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法识别语音字幕。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function saveSubtitles() {
    if (!selected || !subtitleDirty) return;
    setBusy(true); setError("");
    try {
      const result = await saveBatchSubtitles(projectId, selected.id, subtitles.revision, selected.variant.revision, subtitleDraft);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((task) => task.id === selected.id ? { ...task,
        reviewStatus: invalidatedReviewStatus(task), variant: { ...task.variant, subtitles: result.subtitles } } : task));
      setSubtitleDrafts((current) => {
        if (JSON.stringify(current[selected.id]) !== JSON.stringify(subtitleDraft)) return current;
        const next = { ...current }; delete next[selected.id]; return next;
      });
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法保存字幕。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  function changeSubtitle(index: number, update: Partial<BatchCue>) {
    if (!selected) return;
    setSubtitleDrafts((current) => ({ ...current, [selected.id]: subtitleDraft.map((cue, position) =>
      position === index ? { ...cue, ...update } : cue) }));
  }

  const childrenActive = selectedChildren.some((task) => {
    const run = task.variant.runs[task.variant.runs.length - 1];
    const delivery = task.variant.exports?.[task.variant.exports.length - 1];
    return task.voiceover?.status === "queued" || task.voiceover?.status === "running" || run?.status === "queued" || run?.status === "running" || delivery?.status === "queued" || delivery?.status === "running";
  });
  useEffect(() => {
    if (!childrenActive && selected?.voiceover?.status !== "queued" && selected?.voiceover?.status !== "running" && latestRun?.status !== "queued" && latestRun?.status !== "running" &&
        latestRecognition?.status !== "queued" && latestRecognition?.status !== "running" &&
        latestExport?.status !== "queued" && latestExport?.status !== "running") return undefined;
    const timer = setInterval(() => { void refresh(); }, 1500);
    return () => clearInterval(timer);
  }, [projectId, selectedId, childrenActive, selected?.voiceover?.status, latestRun?.id, latestRun?.status, latestRecognition?.id, latestRecognition?.status, latestExport?.id, latestExport?.status]);

  function changeClips(trackId: string, update: (clips: TimelineClip[]) => TimelineClip[]) {
    if (!selected) return;
    setDrafts((current) => ({ ...current, [selected.id]: (current[selected.id] ?? selected.variant.tracks)
      .map((track) => track.id === trackId ? { ...track, clips: update(track.clips) } : track) }));
  }

  function addAsset(asset: TimelineAsset) {
    const trackId = asset.kind === "audio" ? "audio" : "video";
    changeClips(trackId, (clips) => {
      const duration = Math.min(3, asset.duration ?? 3);
      const start = clips.reduce((end, clip) => Math.max(end, clip.start + clip.duration), 0);
      return [...clips, { id: nextId(), assetId: asset.id, start, inPoint: 0, duration, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 }];
    });
  }

  function changeAspectMode(aspectMode: AspectMode) {
    if (!selected || !draftTracks) return;
    const usedAssetIds = new Set(draftTracks.filter((track) => track.kind === "video" && !track.hidden)
      .flatMap((track) => track.clips.map((clip) => clip.assetId)));
    const resolution = resolveProductAspect(aspectMode, assets.filter((asset) => usedAssetIds.has(asset.id)));
    const next: VariantAspectDraft = { aspectMode, settings: { width: resolution.width, height: resolution.height, fps: 30 },
      resolvedAspect: resolution.resolvedAspect, aspectReason: resolution.reason };
    setAspectDrafts((current) => {
      const updated = { ...current };
      if (aspectMode === (selected.variant.aspectMode ?? "9:16") && JSON.stringify(next.settings) === JSON.stringify(selected.variant.settings)) delete updated[selected.id];
      else updated[selected.id] = next;
      return updated;
    });
  }

  async function saveVariant() {
    if (!selected || !draftTracks || !selectedAspect) return;
    setBusy(true); setError("");
    try {
      const result = await saveBatchVariant(projectId, selected.id, selected.variant.revision, draftTracks, selectedAspect.settings, selectedAspect.aspectMode);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((task) => task.id === selected.id ? { ...task, variant: result.variant,
        reviewStatus: invalidatedReviewStatus(task),
        generation: task.generation?.status === "failed" && result.variant.tracks.some((track) => track.kind === "video" && !track.hidden && track.clips.length > 0)
          ? { status: "ready", error: null } : task.generation } : task));
      setDrafts((current) => {
        if (JSON.stringify(current[selected.id]) !== JSON.stringify(draftTracks)) return current;
        const next = { ...current }; delete next[selected.id]; return next;
      });
      setAspectDrafts((current) => {
        if (JSON.stringify(current[selected.id]) !== JSON.stringify(selectedAspect)) return current;
        const next = { ...current }; delete next[selected.id]; return next;
      });
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法保存变体。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function saveContent() {
    if (!selected || !contentDraft || !contentDirty) return;
    setBusy(true); setError("");
    try {
      const result = await saveBatchContent(projectId, selected.id, selected.contentRevision ?? 0, contentDraft.sellingPoint, contentDraft.script);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((task) => task.id === selected.id ? result.task : task));
      setContentDrafts((current) => {
        if (JSON.stringify(current[selected.id]) !== JSON.stringify(contentDraft)) return current;
        const next = { ...current }; delete next[selected.id]; return next;
      });
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法保存卖点与脚本。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function review(decision: BatchReview["decision"]) {
    if (!selected || !currentPreview || !reviewReady) return;
    setBusy(true); setError("");
    try {
      const result = await reviewBatchVariant(projectId, selected.id, currentPreview.id, decision, reviewReasons[selected.id]);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((task) => task.id === selected.id ? result.task : task));
      setReviewReasons((current) => ({ ...current, [selected.id]: "" }));
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法保存审核结果。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function preview() {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const result = await startBatchPreview(projectId, selected.id, selected.variant.revision);
      if (projectRef.current !== projectId) return;
      setTasks((current) => current.map((task) => task.id === selected.id ? { ...task, variant: result.variant } : task));
      onPersistedChange?.();
    } catch (reason) { if (projectRef.current === projectId) setError(reason instanceof Error ? reason.message : "无法生成预览。"); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  return <section className={`preproduction-panel preproduction-native-form ${presentation ? `studio-batch studio-batch-${presentation}` : ""}`} aria-labelledby="batch-editor-title">
    <h3 id="batch-editor-title">{presentation === "voice" ? "选择声音并制作" : presentation === "review" ? "成片与审片" : presentation === "edit" ? "单条精修" : "批量混剪"}</h3>
    <p>为每批素材确认卖点与脚本，再编排独立短视频变体。</p>
    {!presentation && <>
    <button type="button" className="secondary-action" onClick={() => { setAigcOpened(true); setShowAigc((current) => !current); }}>AI 商品内容创作</button>
    {aigcOpened && <div hidden={!showAigc}><AigcCreator projectId={projectId} onDraftChange={setAigcDirty} onPersistedChange={onPersistedChange}
      onBatchCreated={(taskId) => { void refresh(false).then(() => setSelectedId(taskId)); }} /></div>}
    {error && <p role="alert" className="preproduction-error">{error}</p>}
    <div className="preproduction-form-grid">
      <label>客户卖点<input value={sellingPoint} onChange={(event) => setSellingPoint(event.target.value)} /></label>
      <label>已确认脚本<textarea value={script} onChange={(event) => setScript(event.target.value)} /></label>
    </div>
    <button type="button" className="primary-action" disabled={loading || busy || !sellingPoint.trim() || !script.trim()} onClick={() => void createTask()}>创建批量任务</button>
    </>}
    {presentation && error && <p role="alert" className="preproduction-error">{error}</p>}
    {presentation && loading && <p role="status">正在读取制作任务…</p>}
    {presentation && !loading && !tasks.length && <p>尚无制作任务，请先确认五条脚本并继续选声音。</p>}
    {presentation !== "review" && <ul className="batch-editor-tasks">{tasks.filter((task) => !task.batchId).map((task) => <li key={task.id}><strong>{task.sellingPoint}</strong><p>{task.script}</p><button type="button" className="secondary-action" onClick={() => setSelectedId(task.id)}>编辑 {task.sellingPoint}</button></li>)}</ul>}
    {presentation === "review" && <div className="sp-list-toolbar">
      <div className="sp-tabs" role="group" aria-label="审片状态筛选">{([ ["all", "全部"], ["pending", "待审核"], ["approved", "已通过"], ["changes", "需修改"] ] as const).map(([value, label]) =>
        <button key={value} type="button" aria-pressed={reviewFilter === value} onClick={() => setReviewFilter(value)}>{label} {reviewTasks.filter(task => value === "all" || (value === "changes" ? ["rejected", "stale"].includes(reviewState(task)) : reviewState(task) === value)).length}</button>)}</div>
      <button type="button" className="primary-action" disabled={busy || !reviewTasks.some(task => task.batchId && exportable(task))}
        onClick={() => void exportChildren(reviewTasks.filter(task => task.batchId && exportable(task)).map(task => task.id))}>导出已通过项</button>
    </div>}
    <div className={presentation === "review" ? "studio-review-layout" : ""}>
    {presentation === "review" && <div className="studio-review-results">
      {!filteredReviewTasks.length && !loading && <div className="sp-empty"><h4>当前筛选没有作品</h4><p>切换审核状态查看其他作品。</p></div>}
      <div className="sp-result-grid">{filteredReviewTasks.map(task => {
        const complete = [...task.variant.runs].reverse().find(run => run.status === "completed");
        const firstClip = task.variant.tracks.find(track => track.kind === "video" && !track.hidden)?.clips[0];
        const cover = assets.find(asset => asset.id === firstClip?.assetId);
        const state = reviewState(task);
        return <button type="button" key={task.id} className={`sp-result-card ${selectedId === task.id ? "is-selected" : ""}`} aria-pressed={selectedId === task.id} aria-label={`查看作品 ${task.sellingPoint}`} onClick={() => setSelectedId(task.id)}>
          <div className="studio-result-cover" style={{ aspectRatio: `${complete?.settings?.width ?? task.variant.settings.width} / ${complete?.settings?.height ?? task.variant.settings.height}`, height: "auto" }}>{cover?.kind === "image" ? <img src={cover.url} alt="" /> : complete?.url || cover?.url ? <video src={complete?.url ?? cover?.url} muted playsInline preload="metadata" aria-hidden="true" /> : <span>画面尚未准备</span>}</div>
          <h4>{task.sellingPoint}</h4><p>{task.script}</p><span className={`sp-status sp-status-${state}`}>{reviewLabel[state]}</span>
        </button>;
      })}</div>
    </div>}
    {selected && draftTracks && <div className="batch-editor-workspace">
      {selected.batchId && presentation !== "review" && <button type="button" className="secondary-action" onClick={() => setSelectedId(selected.batchId!)}>返回批量任务</button>}
      <h4>{selected.sellingPoint} · 短视频变体</h4>
      {selectedAspect && <p>比例 {selectedAspect.resolvedAspect} · {selectedAspect.settings.width}×{selectedAspect.settings.height} · {selectedAspect.settings.fps} 帧/秒</p>}
      <p>{selected.script}</p>
      {hasPendingVoice(selected) && <p role="status">此作品已选择新音色，生成配音并重新预览后再审核。</p>}
      {presentation && latestCompleted?.url && <div className="studio-result-preview"><p>预览对应保存版本 {latestCompleted.revision}{latestCompleted.subtitleRevision !== undefined || subtitles.revision > 0 ? `／字幕版本 ${latestCompleted.subtitleRevision ?? 0}` : ""}
        {latestCompleted.revision !== selected.variant.revision || (latestCompleted.contentRevision ?? 0) !== (selected.contentRevision ?? 0) || (latestCompleted.subtitleRevision ?? 0) !== subtitles.revision || dirty || contentDirty || subtitleDirty ? "（当前编辑已变化）" : ""}</p>
        <p>预览脚本版本 {latestCompleted.contentRevision ?? 0}</p>
        <video controls preload="metadata" aria-label="短视频变体预览" src={latestCompleted.url}
          style={latestCompleted.settings ? { aspectRatio: `${latestCompleted.settings.width} / ${latestCompleted.settings.height}` } : undefined} /></div>}
      {contentDraft && (!presentation || presentation === "edit") && <div className="batch-editor-content">
        <h5>卖点与脚本</h5>
        <label>当前卖点<input value={contentDraft.sellingPoint} onChange={(event) => setContentDrafts((current) => ({ ...current,
          [selected.id]: { ...contentDraft, sellingPoint: event.target.value } }))} /></label>
        <label>当前脚本<textarea value={contentDraft.script} onChange={(event) => setContentDrafts((current) => ({ ...current,
          [selected.id]: { ...contentDraft, script: event.target.value } }))} /></label>
        <button type="button" className="primary-action" disabled={busy || !contentDirty || !contentDraft.sellingPoint.trim() || !contentDraft.script.trim()}
          onClick={() => void saveContent()}>保存卖点与脚本</button>
      </div>}
      {selectedAspect && (!presentation || presentation === "edit") && <AspectRatioPicker name={`batch-aspect-${selected.id}`}
        value={selectedAspect.aspectMode} onChange={changeAspectMode}
        resolution={{ resolvedAspect: selectedAspect.resolvedAspect, width: selectedAspect.settings.width,
          height: selectedAspect.settings.height, reason: selectedAspect.aspectReason }} disabled={busy} />}
      <div hidden={presentation === "review"}><VoiceSelector autoLoad={presentation === "voice"} visible={visible && presentation !== "review"} key={`${projectId}:${selected.id}`} projectId={projectId} ownerId={selected.id}
        initialVoiceId={voiceSelections[selected.id] ?? ""}
        onVoiceChange={voiceId => { if (projectRef.current === projectId) setVoiceSelections(current => ({ ...current, [selected.id]: voiceId })); }}
        pendingRequest={Object.values(voiceSubmittingOwners).some(ids => voiceTargets.some(task => ids.includes(task.id)))}
        onSubmitting={submitting => { if (projectRef.current === projectId) setVoiceSubmittingOwners(current => ({ ...current, [selected.id]: submitting ? voiceTargets.map(task => task.id) : [] })); }}
        targets={voiceTargets}
        disabled={busy || anyDirty} onSelectionPending={pending => { if (projectRef.current === projectId) setVoicePendingOwners(current => ({ ...current, [selected.id]: pending ? voiceTargets.map(task => task.id) : [] })); }}
        onError={message => { if (projectRef.current === projectId) setError(message); }}
        onGenerated={(updated) => {
          if (projectRef.current !== projectId) return;
          setTasks(current => current.map(task => updated.find(item => item.id === task.id) ?? task));
          setVoicePendingOwners(current => Object.fromEntries(Object.entries(current).map(([owner, ids]) => [owner, ids.filter(id => !updated.some(task => task.id === id))])));
          onPersistedChange?.();
        }} /></div>
      {(!presentation || presentation === "edit") && <div className="batch-editor-assets"><h5>项目素材</h5>{assets.length ? assets.map((asset) => <div key={asset.id} className="batch-editor-asset">
        <button type="button" className="secondary-action" onClick={() => addAsset(asset)}>添加素材 {asset.name}</button>
        {(asset.kind === "video" || asset.kind === "image") && <label><input type="checkbox" checked={selectedAssets.includes(asset.id)} onChange={() => toggleRecommendationAsset(asset.id)} />选择推荐素材 {asset.name}</label>}
      </div>) : <p>请先在素材页上传或导入素材。</p>}</div>}
      {!selected.batchId && presentation !== "review" && <div className="batch-editor-bulk">
        {!presentation && <>
        <h5>批量创建变体</h5>
        <p>先在上方选取画面素材，再填写每条已确认的卖点和脚本。每次提交 5–20 条；新批次保留旧变体。</p>
        <label>目标数量 <input type="number" min="5" max="20" step="1" value={bulkCount} onChange={(event) => setBulkCount(Number(event.target.value))} /></label>
        {Number.isInteger(bulkCount) && bulkCount >= 5 && bulkCount <= 20 && Array.from({ length: bulkCount }, (_, index) => <div key={index} className="batch-editor-bulk-row">
          <label>第 {index + 1} 条卖点<input value={currentBulkItems[index]?.sellingPoint ?? ""} onChange={(event) => changeBulkItem(index, "sellingPoint", event.target.value)} /></label>
          <label>第 {index + 1} 条脚本<textarea value={currentBulkItems[index]?.script ?? ""} onChange={(event) => changeBulkItem(index, "script", event.target.value)} /></label>
        </div>)}
        <button type="button" className="primary-action" disabled={busy || !bulkReady} onClick={() => void createBulk()}>创建 {bulkCount} 条变体</button>
        {(!Number.isInteger(bulkCount) || bulkCount < 5 || bulkCount > 20) && <p>目标数量须为 5–20 的整数。</p>}
        </>}
        {selectedChildren.length > 0 && presentation !== "voice" && <div className="batch-editor-exports">
          <h5>批量导出成片</h5>
          <p>按各作品已审核的视频比例与实际尺寸导出，统一为 30 帧/秒。仅可选择当前预览已完成且审核通过的变体。</p>
          <button type="button" className="primary-action" disabled={busy || !selectedChildren.some((task) => selectedExportIds.includes(task.id) && exportable(task))}
            onClick={() => void exportChildren(selectedChildren.filter((task) => selectedExportIds.includes(task.id) && exportable(task)).map((task) => task.id))}>导出已选变体</button>
        </div>}
        {generationIds.map((generationId) => {
          const children = selectedChildren.filter((task) => task.generationId === generationId);
          const categories = children.map(previewCategory);
          const queued = categories.filter((status) => status === "queued").length;
          const completed = categories.filter((status) => status === "completed").length;
          const failed = categories.filter((status) => status === "failed").length;
          const cancelled = categories.filter((status) => status === "cancelled").length;
          const pending = categories.filter((status) => status === "pending").length;
          return <section key={generationId} className="batch-editor-generation" aria-label={`批次 ${generationId}`}>
            <h5>批次 {generationId}</h5>
            <p>{completed} 条预览完成 · {queued} 条排队中 · {failed} 条失败 · {cancelled} 条已取消 · {pending} 条待预览</p>
            <button type="button" className="secondary-action" disabled={busy || tasks.some(task => task.generationId === generationId && hasPendingVoice(task))} onClick={() => void previewGeneration(generationId)}>生成本批预览</button>
            {presentation !== "voice" && <div className="batch-editor-variants">{children.map((child) => {
              const run = child.variant.runs[child.variant.runs.length - 1];
              const complete = [...child.variant.runs].reverse().find((item) => item.status === "completed");
              const subtitleRevision = child.variant.subtitles?.revision ?? 0;
              const currentClips = child.variant.tracks.filter((track) => track.kind === "video").flatMap((track) => track.clips)
                .sort((left, right) => left.start - right.start);
              const delivery = child.variant.exports?.[child.variant.exports.length - 1];
              return <article key={child.id} className="batch-editor-variant-card">
                <h6>{child.sellingPoint}</h6><p>{child.script}</p>
                <p>审核状态：{reviewLabel[child.reviewStatus ?? "pending"]}</p>
                <label><input type="checkbox" checked={selectedExportIds.includes(child.id)} disabled={!exportable(child)}
                  onChange={() => setSelectedExportIds((current) => current.includes(child.id) ? current.filter((id) => id !== child.id) : [...current, child.id])} />选择导出 {child.sellingPoint}</label>
                <p>当前镜头：{currentClips.length ? currentClips.map((clip) => clipName(clip, assets)).join(" → ") : "尚未编排"}</p>
                <p>已保存字幕：{child.variant.subtitles?.cues.length ?? 0} 条</p>
                {child.proposal?.matches.map((match) => <p key={match.clipId}>初始推荐依据：{match.scriptSegment} → {match.assetName}（{match.matchedTerms.join("、")}）</p>)}
                {child.generation?.status === "failed" && <p role="status">编排失败：{child.generation.error}</p>}
                {run && <p>预览{run.status === "queued" ? "排队中" : run.status === "running" ? "渲染中" : run.status === "completed" ? "已完成" : run.status === "cancelled" ? "已取消" : `失败：${run.error ?? "请重试"}`}</p>}
                <button type="button" className="secondary-action" onClick={() => setSelectedId(child.id)}>编辑变体 {child.sellingPoint}</button>
                {(run?.status === "failed" || run?.status === "cancelled") && <button type="button" className="secondary-action" disabled={busy} onClick={() => void updateChildPreview(child, false)}>重试预览 {child.sellingPoint}</button>}
                {(run?.status === "queued" || run?.status === "running") && <button type="button" className="secondary-action" disabled={busy} onClick={() => void updateChildPreview(child, true)}>取消预览 {child.sellingPoint}</button>}
                {complete?.url && <div><p>预览对应保存版本 {complete.revision}{complete.subtitleRevision !== undefined || subtitleRevision > 0 ? `／字幕版本 ${complete.subtitleRevision ?? 0}` : ""}
                  {complete.revision !== child.variant.revision || (complete.contentRevision ?? 0) !== (child.contentRevision ?? 0) || (complete.subtitleRevision ?? 0) !== subtitleRevision ? "（当前版本已变化）" : ""}</p>
                  <p>预览脚本版本 {complete.contentRevision ?? 0}</p>
                  <video controls preload="metadata" aria-label={`${child.sellingPoint} 预览`} src={complete.url}
                    style={complete.settings ? { aspectRatio: `${complete.settings.width} / ${complete.settings.height}` } : undefined} /></div>}
                {delivery && <p>成片导出：{exportLabel[delivery.status]}{delivery.status === "failed" ? `：${delivery.error ?? "请重试"}` : ""}</p>}
                {delivery?.url && <a href={delivery.url} download>下载成片 {child.sellingPoint}</a>}
                {(delivery?.status === "failed" || delivery?.status === "cancelled") && exportable(child) &&
                  <button type="button" className="secondary-action" disabled={busy} onClick={() => void exportChildren([child.id])}>重试导出 {child.sellingPoint}</button>}
                {(delivery?.status === "queued" || delivery?.status === "running") &&
                  <button type="button" className="secondary-action" disabled={busy} onClick={() => void cancelExport(child, delivery.id)}>取消导出 {child.sellingPoint}</button>}
                {(child.variant.exports?.length ?? 0) > 0 && <details><summary>交付历史</summary><ol>{child.variant.exports!.map((item) =>
                  <li key={item.id}>
                    <p>{exportLabel[item.status]} · 卖点：{item.sellingPoint} · 脚本：{item.script}</p>
                    <p>素材：{item.assets.map((asset) => asset.name).join("、")} · 镜头：{item.snapshot.tracks.filter((track) => track.kind === "video")
                      .flatMap((track) => track.clips).map((clip) => clip.assetId).join(" → ")}</p>
                    <p>字幕：{item.subtitles.map((cue) => cue.text).join("／") || "无"} · 脚本版本 {item.contentRevision}／变体版本 {item.variantRevision}／字幕版本 {item.subtitleRevision} · 审核 {item.review.id}（{item.review.reason}）</p>
                    {item.url && <a href={item.url} download>下载历史成片</a>}
                  </li>)}</ol></details>}
              </article>;
            })}</div>}
          </section>;
        })}
      </div>}
      {(!presentation || presentation === "edit") && <>
      <div className="batch-editor-recommendation">
        <h5>按脚本推荐镜头</h5>
        <p>依据素材名称和备注的词语匹配脚本段落；当前不分析画面内容。请在素材页补充准确备注，脚本用句号或换行分段。</p>
        <button type="button" className="secondary-action" disabled={busy || selectedAssets.length < 2} onClick={() => void recommendVariant()}>按脚本推荐镜头</button>
        {selected.proposal && <div>
          <p>推荐依据（生成时变体版本 {selected.proposal.baseRevision}）</p>
          <ol>{selected.proposal.matches.map((match) => <li key={match.clipId}>{match.scriptSegment} → {match.assetName}；匹配词：{match.matchedTerms.join("、")}{match.matchedSellingPointTerms.length > 0 && <>；卖点匹配：{match.matchedSellingPointTerms.join("、")}</>}</li>)}</ol>
          <button type="button" className="secondary-action" disabled={busy || dirty || selectionChanged || selected.proposal.baseRevision !== selected.variant.revision} onClick={applyRecommendation}>应用推荐到草稿</button>
          {savedIncludesRecommendation && selected.proposal.baseRevision !== selected.variant.revision && <p>已保存版本包含此推荐镜头。</p>}
          {selected.proposal.baseRevision !== selected.variant.revision && <p>此推荐基于旧版本，重新推荐后才能再次应用。</p>}
          {selectionChanged && <p>素材选择已变化，请重新推荐后再应用。</p>}
          {dirty && <p>先保存或撤销当前草稿，再应用推荐。</p>}
        </div>}
      </div>
      {draftTracks.map((track) => <div key={track.id} className="batch-editor-track">
        <h5>{track.name}</h5>
        {track.clips.map((clip, index) => <div key={clip.id} className="batch-editor-clip">
          <strong>{clipName(clip, assets)}</strong>
          <button type="button" className="secondary-action" disabled={index === 0} onClick={() => changeClips(track.id, (clips) => reorder(clips, index, -1, track.kind))}>上移 {clipName(clip, assets)}</button>
          <button type="button" className="secondary-action" disabled={index === track.clips.length - 1} onClick={() => changeClips(track.id, (clips) => reorder(clips, index, 1, track.kind))}>下移 {clipName(clip, assets)}</button>
          <button type="button" className="secondary-action" onClick={() => changeClips(track.id, (clips) => clips.filter((item) => item.id !== clip.id))}>移除 {clipName(clip, assets)}</button>
          {([ ["start", "起点（秒）"], ["inPoint", "入点（秒）"], ["duration", "时长（秒）"], ["speed", "速度"], ["volume", "音量"], ["fadeIn", "淡入（秒）"], ["fadeOut", "淡出（秒）"] ] as const).map(([key, label]) =>
            <label key={key}>{clipName(clip, assets)} {label}<input type="number" min="0" step="0.1" value={clip[key]} onChange={(event) => changeClips(track.id, (clips) => clips.map((item) => item.id === clip.id ? { ...item, [key]: Number(event.target.value) } : item))} /></label>)}
        </div>)}
      </div>)}
      <div className="batch-editor-subtitles">
        <h5>逐句字幕</h5>
        <p>{selected.aigcSource?.screenCopySource === "script"
          ? "屏幕文案来自已确认脚本，可在此逐句修改。保存后会烧录进下一次预览。"
          : "识别结果先作为候选；确认并保存的字幕会烧录进下一次预览。"}</p>
        <button type="button" className="secondary-action" disabled={busy || dirty || subtitleDirty || subtitleDuration <= 0 || latestRecognition?.status === "queued" || latestRecognition?.status === "running"}
          onClick={() => void recognizeSubtitles()}>识别语音字幕</button>
        {latestRecognition && <p role="status">识别{latestRecognition.status === "queued" ? "排队中" : latestRecognition.status === "running" ? "运行中" : latestRecognition.status === "completed" ? "已完成" : `失败：${latestRecognition.error ?? "请重试"}`}</p>}
        {latestRecognition?.status === "completed" && <button type="button" className="secondary-action"
          disabled={busy || latestRecognition.timelineRevision !== selected.variant.revision}
          onClick={() => setSubtitleDrafts((current) => ({ ...current, [selected.id]: latestRecognition.cues }))}>采用识别候选</button>}
        {latestRecognition?.status === "completed" && latestRecognition.timelineRevision !== selected.variant.revision && <p>识别候选对应旧时间线，请重新识别。</p>}
        <button type="button" className="secondary-action" disabled={busy || subtitleDuration <= 0}
          onClick={() => setSubtitleDrafts((current) => ({ ...current, [selected.id]: [...subtitleDraft,
            { id: nextId(), start: 0, end: Math.min(2, subtitleDuration), text: "" }] }))}>添加字幕</button>
        {subtitleDraft.map((cue, index) => <div key={cue.id} className="batch-editor-subtitle-row">
          <label>第 {index + 1} 条字幕开始<input type="number" min="0" step="0.1" value={cue.start} onChange={(event) => changeSubtitle(index, { start: Number(event.target.value) })} /></label>
          <label>第 {index + 1} 条字幕结束<input type="number" min="0" step="0.1" value={cue.end} onChange={(event) => changeSubtitle(index, { end: Number(event.target.value) })} /></label>
          <label>第 {index + 1} 条字幕文字<input value={cue.text} onChange={(event) => changeSubtitle(index, { text: event.target.value })} /></label>
          <button type="button" className="secondary-action" onClick={() => setSubtitleDrafts((current) => ({ ...current,
            [selected.id]: subtitleDraft.filter((_, position) => position !== index) }))}>删除第 {index + 1} 条字幕</button>
        </div>)}
        {subtitleDirty && !subtitlesValid && <p role="status">每条字幕需填写不超过 120 字；时间须在当前画面范围内且至少相隔 1 毫秒。</p>}
        <button type="button" className="primary-action" disabled={busy || dirty || !subtitleDirty || !subtitlesValid} onClick={() => void saveSubtitles()}>保存字幕</button>
      </div>
      <button type="button" className="primary-action" disabled={busy || !dirty} onClick={() => void saveVariant()}>保存变体</button>
      </>}
      <button type="button" className="secondary-action" disabled={busy || hasPendingVoice(selected) || dirty || contentDirty || subtitleDirty || !draftTracks.some((track) => track.kind === "video" && track.clips.length > 0) || latestRun?.status === "queued" || latestRun?.status === "running"} onClick={() => void preview()}>生成预览</button>
      <button type="button" className="secondary-action" onClick={() => void refresh()}>刷新预览状态</button>
      {dirty && <p>先保存变体，才能预览当前编辑。</p>}
      {contentDirty && <p>先保存卖点与脚本，才能预览当前编辑。</p>}
      {subtitleDirty && <p>先保存字幕，才能预览当前编辑。</p>}
      {latestRun && <p>{latestRun.status === "queued" ? "排队中" : latestRun.status === "running" ? "渲染中" : latestRun.status === "completed" ? "预览已完成" : latestRun.status === "cancelled" ? "预览已取消" : `预览失败：${latestRun.error ?? "请重试"}`}</p>}
      {!presentation && latestCompleted?.url && <div><p>预览对应保存版本 {latestCompleted.revision}{latestCompleted.subtitleRevision !== undefined || subtitles.revision > 0 ? `／字幕版本 ${latestCompleted.subtitleRevision ?? 0}` : ""}
        {latestCompleted.revision !== selected.variant.revision || (latestCompleted.contentRevision ?? 0) !== (selected.contentRevision ?? 0) || (latestCompleted.subtitleRevision ?? 0) !== subtitles.revision || dirty || contentDirty || subtitleDirty ? "（当前编辑已变化）" : ""}</p>
        <p>预览脚本版本 {latestCompleted.contentRevision ?? 0}</p>
        <video controls preload="metadata" aria-label="短视频变体预览" src={latestCompleted.url}
          style={latestCompleted.settings ? { aspectRatio: `${latestCompleted.settings.width} / ${latestCompleted.settings.height}` } : undefined} /></div>}
      {presentation === "review" && onOpenEdit && <button type="button" className="secondary-action" onClick={onOpenEdit}>修改这条</button>}
      {presentation === "review" && latestExport && <section className="studio-delivery" aria-label="所选作品交付">
        <h5>成片交付</h5>
        <p>成片导出：{exportLabel[latestExport.status]}{latestExport.error ? `：${latestExport.error}` : ""}</p>
        <p>交付版本：脚本 {latestExport.contentRevision}／变体 {latestExport.variantRevision}／字幕 {latestExport.subtitleRevision}</p>
        {(latestExport.contentRevision !== (selected.contentRevision ?? 0) || latestExport.variantRevision !== selected.variant.revision || latestExport.subtitleRevision !== subtitles.revision || dirty || contentDirty || subtitleDirty) && <p role="status">这是旧版本交付，当前修改需重新制作与审核。</p>}
        {latestExport.url && <a className="sp-text-button" href={latestExport.url} download>下载成片 {selected.sellingPoint}</a>}
        {(latestExport.status === "failed" || latestExport.status === "cancelled") && selected.batchId && <button type="button" className="secondary-action" disabled={busy || !exportable(selected)} onClick={() => void exportChildren([selected.id])}>重试导出 {selected.sellingPoint}</button>}
        {(latestExport.status === "queued" || latestExport.status === "running") && <button type="button" className="secondary-action" disabled={busy} onClick={() => void cancelExport(selected, latestExport.id)}>取消导出 {selected.sellingPoint}</button>}
        {(selected.variant.exports?.length ?? 0) > 1 && <details><summary>交付历史</summary>{selected.variant.exports!.map(item => <p key={item.id}>{exportLabel[item.status]} · 脚本 {item.contentRevision}／变体 {item.variantRevision}／字幕 {item.subtitleRevision}{item.url && <a className="sp-text-button" href={item.url} download>下载历史成片</a>}</p>)}</details>}
      </section>}
      {presentation !== "voice" && <div className="batch-editor-review">
        <h5>逐条审核</h5>
        {selected.aigcSource && <details>
          <summary>创作来源与交接快照</summary>
          <p>候选 {selected.aigcSource.candidateId} · 第 {selected.aigcSource.candidateRevision} 版；简报第 {selected.aigcSource.briefRevision} 版</p>
          <p>以下内容记录交接时的来源，后续编辑不会改写它。</p>
          {selected.aigcSource.brief && <>
            <p>商品名称：{selected.aigcSource.brief.productName}</p>
            <p>目标受众：{selected.aigcSource.brief.audience}</p>
            <p>简报卖点：{selected.aigcSource.brief.sellingPoints.join("、") || "无"}</p>
            <p>行动引导：{selected.aigcSource.brief.callToAction || "无"}</p>
            <p>禁用表达：{selected.aigcSource.brief.forbiddenPhrases.join("、") || "无"}</p>
          </>}
          <p>交接时卖点：{selected.aigcSource.sellingPoint ?? selected.sellingPoint}</p>
          <p>引用事实：{selected.aigcSource.facts.map((fact) => fact.text).join("、") || "无"}</p>
          <p>引用素材：{selected.aigcSource.assets?.map((asset) => asset.name).join("、") || "无"}</p>
          {selected.aigcSource.beats && <ol>{selected.aigcSource.beats.map((beat, index) => <li key={index}>
            镜头 {index + 1}：{beat.text}；素材 {selected.aigcSource?.assets?.find((asset) => asset.id === beat.assetId)?.name ?? beat.assetId}；
            事实 {beat.factIds.map((id) => selected.aigcSource?.facts.find((fact) => fact.id === id)?.text ?? id).join("、")}
          </li>)}</ol>}
        </details>}
        <p>审核状态：{reviewLabel[selected.reviewStatus ?? "pending"]}</p>
        <p>审核依据：脚本版本 {selected.contentRevision ?? 0}／变体版本 {selected.variant.revision}／字幕版本 {subtitles.revision}</p>
        <p>当前素材：{selected.variant.tracks.filter((track) => track.kind === "video").flatMap((track) => track.clips)
          .map((clip) => clipName(clip, assets)).join(" → ") || "尚未编排"}</p>
        <p>当前字幕：{subtitles.cues.length ? subtitles.cues.map((cue) => cue.text).join("／") : "无"}</p>
        {!currentPreview && <p>当前版本尚无可审核的完成预览，请先生成预览。</p>}
        <label>审核原因<textarea value={reviewReasons[selected.id] ?? ""} onChange={(event) => setReviewReasons((current) => ({ ...current,
          [selected.id]: event.target.value }))} /></label>
        <button type="button" className="primary-action" disabled={busy || !reviewReady} onClick={() => void review("approved")}>通过变体</button>
        <button type="button" className="secondary-action" disabled={busy || !reviewReady} onClick={() => void review("rejected")}>退回变体</button>
        {(selected.reviews ?? []).length > 0 && <div><h6>审核历史</h6><ol>{selected.reviews!.map((item) => <li key={item.id}>
          {item.decision === "approved" ? "通过" : "退回"}：{item.reason}（脚本 {item.contentRevision}／变体 {item.variantRevision}／字幕 {item.subtitleRevision}）
        </li>)}</ol></div>}
      </div>}
    </div>}
    </div>
  </section>;
}
