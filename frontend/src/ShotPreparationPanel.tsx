import { useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, Download, LoaderCircle, Play, Save, Sparkles } from "lucide-react";

import type { Project } from "./models";
import {
  applyToolkitTimeline,
  analyzePreparationShot,
  downloadShotPreparationPackage,
  extractShotPersonControl,
  getShotPreparation,
  restoreDetectedTimeline,
  saveShotPreparation,
  shotRepresentativeFrameUrl,
  type PreparationShot,
  type PersonControl,
  type ShotPreparationSave,
  type ShotPreparationState,
  type ShotPrompts,
} from "./shotPreparationApi";
import "./shotPreparation.css";

const DESKTOP_QUERY = "(min-width: 1024px)";
const noopMutationPendingChange = () => undefined;

type Props = { project: Project; onMutationPendingChange?: (pending: boolean) => void };
type Draft = Pick<ShotPreparationState, "sourceId" | "preprocessingId" | "revision" | "shots">;
type Action = "save" | "package" | "analyze" | "timeline" | null;

function useDesktop() {
  const [desktop, setDesktop] = useState(() => typeof window !== "undefined" && window.matchMedia?.(DESKTOP_QUERY).matches);
  useEffect(() => {
    const query = window.matchMedia?.(DESKTOP_QUERY);
    if (!query) return undefined;
    const update = () => setDesktop(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return desktop;
}

function sourceIdentity(project: Project) {
  const media = project.referenceMedia;
  const preprocessing = project.localPreprocessing;
  if (media?.type !== "video" || preprocessing?.status !== "completed" || preprocessing.mediaType !== "video" || preprocessing.sourceReferenceMediaId !== media.id) return null;
  return JSON.stringify({ projectId: project.id, sourceId: media.id, preprocessingId: preprocessing.id });
}

function controlsIdentity(project: Project) {
  return JSON.stringify({
    activeDepthCaptureId: project.activeDepthCaptureId ?? null,
    depthCaptures: project.depthCaptures?.map((capture) => ({
      id: capture.id,
      status: capture.status,
      updatedAt: capture.updatedAt,
      qualityStatus: capture.qualityAssessment?.status ?? null,
    })) ?? [],
  });
}

function analysisIdentity(project: Project) {
  const analysis = project.semanticAnalysis;
  return JSON.stringify({
    id: analysis?.id ?? null,
    status: analysis?.status ?? null,
    updatedAt: analysis?.updatedAt ?? null,
  });
}

function draftFrom(state: ShotPreparationState): Draft {
  return { sourceId: state.sourceId, preprocessingId: state.preprocessingId, revision: state.revision, shots: state.shots };
}

function sameDraft(left: Draft | null, right: Draft | null) {
  return left === null || right === null ? left === right : JSON.stringify(editableState(left)) === JSON.stringify(editableState(right));
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function timecode(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remaining.toFixed(2).padStart(5, "0")}`;
}

function controlsLabel(shot: PreparationShot) {
  const depth = { available: "可用", review_required: "需要人工确认", failed: "处理失败", missing: "未生成" }[shot.controls.depth];
  return `深度：${depth}`;
}

function editableState(draft: Draft): ShotPreparationSave {
  return {
    revision: draft.revision,
    sourceId: draft.sourceId,
    preprocessingId: draft.preprocessingId,
    shots: draft.shots.map(({ id, notes, prompts }) => ({ id, notes, prompts })),
  };
}

const personControlLabels = {
  missing: "未生成", running: "正在提取", available: "可用", review_required: "需要人工确认", failed: "处理失败", unavailable: "不可用",
} as const;

function PersonControlPreview({ shot, referenceVideoUrl }: { shot: PreparationShot; referenceVideoUrl: string }) {
  const referenceRef = useRef<HTMLVideoElement>(null);
  const outputRefs = useRef<Record<string, HTMLVideoElement | null>>({});
  const outputs = shot.personControl?.outputs ?? {};
  const outputEntries = ([
    ["pose", "33 点身体骨架"], ["mask", "人体遮罩"], ["overlay", "叠加预览"],
  ] as const).filter(([key]) => Boolean(outputs[key]));

  useEffect(() => {
    const reference = referenceRef.current;
    if (!reference) return;
    try { reference.currentTime = shot.startSeconds; } catch { /* 媒体元数据尚未就绪。 */ }
  }, [shot.id, shot.startSeconds]);

  function setOutputTimes(relativeSeconds: number, except?: string) {
    for (const [key] of outputEntries) {
      const video = outputRefs.current[key];
      if (!video || key === except) continue;
      try { if (Math.abs(video.currentTime - relativeSeconds) > 0.06) video.currentTime = relativeSeconds; } catch { /* 媒体元数据尚未就绪。 */ }
    }
  }

  function syncFromReference(play = false) {
    const reference = referenceRef.current;
    if (!reference) return;
    const relative = Math.max(0, reference.currentTime - shot.startSeconds);
    setOutputTimes(relative);
    if (play) for (const [key] of outputEntries) void outputRefs.current[key]?.play().catch(() => undefined);
  }

  function syncFromOutput(key: string, play = false) {
    const output = outputRefs.current[key];
    const reference = referenceRef.current;
    if (!output || !reference) return;
    try { if (Math.abs(reference.currentTime - shot.startSeconds - output.currentTime) > 0.06) reference.currentTime = shot.startSeconds + output.currentTime; } catch { /* 媒体元数据尚未就绪。 */ }
    setOutputTimes(output.currentTime, key);
    if (play) {
      void reference.play().catch(() => undefined);
      for (const [otherKey] of outputEntries) if (otherKey !== key) void outputRefs.current[otherKey]?.play().catch(() => undefined);
    }
  }

  function pauseAll() {
    const reference = referenceRef.current;
    if (reference && !reference.paused) reference.pause();
    for (const [key] of outputEntries) {
      const video = outputRefs.current[key];
      if (video && !video.paused) video.pause();
    }
  }

  function locateQualityTime(relativeSeconds: number) {
    const reference = referenceRef.current;
    if (!reference) return;
    try { reference.currentTime = shot.startSeconds + relativeSeconds; } catch { /* 媒体元数据尚未就绪。 */ }
    setOutputTimes(relativeSeconds);
  }

  const quality = shot.personControl?.quality;

  return <div className="shot-preparation-person-preview" aria-label="人物控制素材预览">
    <p className="shot-preparation-video-note">播放秒数相对于当前镜头；定位或播放任一视频时，会尝试同步其余已加载视频。</p>
    <div className="shot-preparation-person-videos">
      <label>参考视频（全片时间）<video ref={referenceRef} controls preload="metadata" src={referenceVideoUrl} aria-label="参考视频定位" onSeeking={() => syncFromReference()} onPlay={() => syncFromReference(true)} onPause={pauseAll} onTimeUpdate={() => { const video = referenceRef.current; if (video && video.currentTime >= shot.endSeconds) pauseAll(); }} /></label>
      {outputEntries.map(([key, label]) => <label key={key}>{label}（镜头内时间）<video ref={(node) => { outputRefs.current[key] = node; }} controls preload="metadata" src={outputs[key]} aria-label={label} onSeeking={() => syncFromOutput(key)} onPlay={() => syncFromOutput(key, true)} onPause={pauseAll} /></label>)}
    </div>
    <button type="button" className="secondary-action shot-preparation-locate" onClick={() => { const reference = referenceRef.current; if (!reference) return; try { reference.currentTime = shot.startSeconds; } catch { /* 媒体元数据尚未就绪。 */ } setOutputTimes(0); }}>定位到当前镜头起点</button>
    {quality && (quality.missingTimesSeconds.length > 0 || quality.multiplePersonTimesSeconds.length > 0) && <div className="shot-preparation-quality-locator">
      {quality.missingTimesSeconds.length > 0 && <p>缺检定位：{quality.missingTimesSeconds.map((seconds) => <button key={`missing-${seconds}`} type="button" onClick={() => locateQualityTime(seconds)}>{timecode(seconds)}</button>)}</p>}
      {quality.multiplePersonTimesSeconds.length > 0 && <p>多人定位：{quality.multiplePersonTimesSeconds.map((seconds) => <button key={`multiple-${seconds}`} type="button" onClick={() => locateQualityTime(seconds)}>{timecode(seconds)}</button>)}</p>}
    </div>}
  </div>;
}

function personQualityText(personControl: PersonControl | undefined) {
  const quality = personControl?.quality;
  if (!quality) return null;
  const parts = [`${quality.detectedFrameCount}/${quality.frameCount} 帧检测到人物`];
  if (quality.missingTimesSeconds.length) parts.push(`缺检：${quality.missingTimesSeconds.map(timecode).join("、")}`);
  if (quality.multiplePersonTimesSeconds.length) parts.push(`多人：${quality.multiplePersonTimesSeconds.map(timecode).join("、")}`);
  return `${quality.status === "passed" ? "质量通过" : quality.status === "failed" ? "质量失败" : "需要复核"} · ${parts.join("；")}。${quality.message}`;
}

export function ShotPreparationPanel({ project, onMutationPendingChange = noopMutationPendingChange }: Props) {
  const desktop = useDesktop();
  const identity = sourceIdentity(project);
  const depthIdentity = controlsIdentity(project);
  const semanticIdentity = analysisIdentity(project);
  const [state, setState] = useState<ShotPreparationState | null>(null);
  const [savedDraft, setSavedDraft] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const [selectedToolkitRunId, setSelectedToolkitRunId] = useState("");
  const [loading, setLoading] = useState(Boolean(identity));
  const [loadingError, setLoadingError] = useState("");
  const [actionError, setActionError] = useState("");
  const [action, setAction] = useState<Action>(null);
  const mounted = useRef(false);
  const requestId = useRef(0);
  const currentIdentity = useRef(identity);
  const previousIdentity = useRef<string | null>(null);
  const refreshAfterAction = useRef(false);
  const personRequestId = useRef(0);
  const pollRequestId = useRef(0);
  const [personAction, setPersonAction] = useState(false);
  currentIdentity.current = identity;

  const draftStateRef = useRef({ draft, savedDraft });
  draftStateRef.current = { draft, savedDraft };

  const applyState = (next: ShotPreparationState, preserveDraft = false) => {
    const normalized = {
      ...next,
      timelineOverride: next.timelineOverride ?? null,
      toolkitScenes: Array.isArray(next.toolkitScenes) ? next.toolkitScenes : [],
    };
    const nextDraft = draftFrom(normalized);
    const preserveDirtyDraft = preserveDraft && !sameDraft(draftStateRef.current.draft, draftStateRef.current.savedDraft);
    setState(normalized);
    setDraft((current) => {
      if (!preserveDirtyDraft || !current) return nextDraft;
      const localShots = new Map(current.shots.map((shot) => [shot.id, shot]));
      return {
        ...(preserveDirtyDraft ? current : nextDraft),
        shots: nextDraft.shots.map((shot) => {
          const local = localShots.get(shot.id);
          return local && local.startSeconds === shot.startSeconds && local.endSeconds === shot.endSeconds
            ? { ...shot, notes: local.notes, prompts: local.prompts }
            : shot;
        }),
      };
    });
    if (!preserveDirtyDraft) setSavedDraft(nextDraft);
    setSelectedShotId((current) => normalized.shots.some((shot) => shot.id === current) ? current : normalized.shots[0]?.id ?? null);
    setSelectedToolkitRunId((current) => normalized.toolkitScenes.some((run) => run.toolkitRunId === current) ? current : normalized.toolkitScenes[0]?.toolkitRunId ?? "");
  };

  const isCurrent = (request: number, expected: string | null) => mounted.current && requestId.current === request && currentIdentity.current === expected;

  const load = async (expected = identity, preserveDraft = false) => {
    if (!expected) return;
    const request = ++requestId.current;
    setLoading(true);
    setLoadingError("");
    try {
      const next = await getShotPreparation(project.id);
      if (isCurrent(request, expected)) applyState(next, preserveDraft);
    } catch (error) {
      if (isCurrent(request, expected)) setLoadingError(errorMessage(error, "无法读取逐镜头准备数据。"));
    } finally {
      if (isCurrent(request, expected)) setLoading(false);
    }
  };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestId.current += 1; };
  }, []);

  useEffect(() => {
    const sourceChanged = previousIdentity.current !== identity;
    previousIdentity.current = identity;
    if (!identity) {
      requestId.current += 1;
      personRequestId.current += 1; pollRequestId.current += 1;
      setAction(null); setPersonAction(false); setActionError(""); setLoadingError("");
      setState(null); setDraft(null); setSavedDraft(null); setSelectedShotId(null); setLoading(false);
      setSelectedToolkitRunId("");
      return;
    }
    if (sourceChanged) {
      requestId.current += 1;
      personRequestId.current += 1; pollRequestId.current += 1;
      setAction(null); setPersonAction(false); setActionError(""); setLoadingError("");
      setState(null); setDraft(null); setSavedDraft(null); setSelectedShotId(null); setSelectedToolkitRunId("");
      void load(identity);
      return;
    }
    if (action !== null) {
      refreshAfterAction.current = true;
      return;
    }
    void load(identity, true);
    // Source changes clear drafts. Depth and semantic state changes refresh controls without replacing local text edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, depthIdentity, semanticIdentity]);

  useEffect(() => {
    if (action !== null || !refreshAfterAction.current || !identity) return;
    refreshAfterAction.current = false;
    void load(identity, true);
    // A depth update during a mutation is deferred so it cannot invalidate that mutation's response.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, identity]);

  const personControlActive = Boolean(state?.shots.some((shot) => shot.personControl?.status === "queued" || shot.personControl?.status === "running"));

  useEffect(() => {
    onMutationPendingChange(personAction || personControlActive);
    return () => onMutationPendingChange(false);
  }, [onMutationPendingChange, personAction, personControlActive]);

  useEffect(() => {
    if (!identity || !personControlActive || action !== null) return undefined;
    const expected = identity;
    const refresh = async () => {
      const request = ++pollRequestId.current;
      try {
        const next = await getShotPreparation(project.id);
        if (mounted.current && pollRequestId.current === request && currentIdentity.current === expected) applyState(next, true);
      } catch {
        // 轮询失败不覆盖当前界面；用户仍可通过“重新读取”查看错误。
      }
    };
    const timer = window.setInterval(() => { void refresh(); }, 1000);
    return () => { window.clearInterval(timer); pollRequestId.current += 1; };
    // 轮询使用独立令牌，不会使保存或分析请求的响应失效。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, identity, personControlActive, project.id]);

  const selectedShot = useMemo(() => draft?.shots.find((shot) => shot.id === selectedShotId) ?? draft?.shots[0] ?? null, [draft?.shots, selectedShotId]);
  const dirty = !sameDraft(draft, savedDraft);
  const editingLocked = !desktop || action !== null;
  const canAnalyze = Boolean(state?.canAnalyze && selectedShot && desktop && action === null);
  const canExtractPersonControl = Boolean(desktop && state?.personControlEnvironment?.ready && selectedShot && !dirty && action === null && !personAction && !personControlActive);
  const selectedToolkitRun = state?.toolkitScenes.find((run) => run.toolkitRunId === selectedToolkitRunId) ?? null;
  const appliedTimelineLabel = state?.timelineOverride
    ? state.toolkitScenes.find((run) => run.toolkitRunId === state.timelineOverride?.toolkitRunId)?.label ?? `${state.timelineOverride.toolkitRunId.slice(0, 16)}…`
    : null;

  function updateShot(update: (shot: PreparationShot) => PreparationShot) {
    if (!selectedShot || editingLocked) return;
    setDraft((current) => current ? { ...current, shots: current.shots.map((shot) => shot.id === selectedShot.id ? update(shot) : shot) } : current);
  }

  function selectShot(shot: PreparationShot) {
    setSelectedShotId(shot.id);
  }

  async function save() {
    if (!draft || !dirty || action) return;
    const request = ++requestId.current;
    const expected = identity;
    const projectId = project.id;
    setAction("save"); setActionError("");
    try {
      const next = await saveShotPreparation(projectId, editableState(draft));
      if (isCurrent(request, expected)) applyState(next);
    } catch (error) {
      if (isCurrent(request, expected)) setActionError(errorMessage(error, "无法保存逐镜头准备。"));
    } finally {
      if (isCurrent(request, expected)) setAction(null);
    }
  }

  async function downloadPackage() {
    if (!draft || dirty || action) return;
    const request = ++requestId.current;
    const expected = identity;
    setAction("package"); setActionError("");
    try {
      await downloadShotPreparationPackage(project.id, draft);
    } catch (error) {
      if (isCurrent(request, expected)) setActionError(errorMessage(error, "无法导出逐镜头准备包。"));
    } finally {
      if (isCurrent(request, expected)) setAction(null);
    }
  }

  async function analyze() {
    if (!draft || !selectedShot || !canAnalyze) return;
    const request = ++requestId.current;
    const expected = identity;
    const projectId = project.id;
    setAction("analyze"); setActionError("");
    try {
      const prepared = dirty ? await saveShotPreparation(projectId, editableState(draft)) : state;
      if (!prepared) return;
      if (!isCurrent(request, expected)) return;
      if (dirty) applyState(prepared);
      const next = await analyzePreparationShot(projectId, selectedShot.id, {
        revision: prepared.revision, sourceId: prepared.sourceId, preprocessingId: prepared.preprocessingId, disclosureAccepted: true,
      });
      if (isCurrent(request, expected)) applyState(next);
    } catch (error) {
      if (isCurrent(request, expected)) setActionError(errorMessage(error, "无法分析当前镜头。"));
    } finally {
      if (isCurrent(request, expected)) setAction(null);
    }
  }

  async function extractPersonControl() {
    if (!state || !selectedShot || !canExtractPersonControl) return;
    const expected = identity;
    const request = ++personRequestId.current;
    setPersonAction(true); setActionError("");
    try {
      const next = await extractShotPersonControl(project.id, selectedShot.id, {
        revision: state.revision, sourceId: state.sourceId, preprocessingId: state.preprocessingId,
      });
      if (mounted.current && personRequestId.current === request && currentIdentity.current === expected) applyState(next, true);
    } catch (error) {
      if (mounted.current && personRequestId.current === request && currentIdentity.current === expected) setActionError(errorMessage(error, "无法提取当前镜头的人物控制素材。"));
    } finally {
      if (mounted.current && personRequestId.current === request && currentIdentity.current === expected) setPersonAction(false);
    }
  }

  async function applyTimeline() {
    if (!state || !selectedToolkitRun || dirty || action !== null || personAction || personControlActive) return;
    const request = ++requestId.current;
    const expected = identity;
    personRequestId.current += 1; pollRequestId.current += 1;
    setAction("timeline"); setActionError("");
    try {
      const next = await applyToolkitTimeline(project.id, {
        revision: state.revision, sourceId: state.sourceId, preprocessingId: state.preprocessingId,
        toolkitRunId: selectedToolkitRun.toolkitRunId, cutRevision: selectedToolkitRun.cutRevision,
      });
      if (isCurrent(request, expected)) applyState(next);
    } catch (error) {
      if (isCurrent(request, expected)) setActionError(errorMessage(error, "无法应用视频工具切点。"));
    } finally {
      if (isCurrent(request, expected)) setAction(null);
    }
  }

  async function restoreTimeline() {
    if (!state || !state.timelineOverride || dirty || action !== null || personAction || personControlActive) return;
    const request = ++requestId.current;
    const expected = identity;
    personRequestId.current += 1; pollRequestId.current += 1;
    setAction("timeline"); setActionError("");
    try {
      const next = await restoreDetectedTimeline(project.id, {
        revision: state.revision, sourceId: state.sourceId, preprocessingId: state.preprocessingId,
      });
      if (isCurrent(request, expected)) applyState(next);
    } catch (error) {
      if (isCurrent(request, expected)) setActionError(errorMessage(error, "无法恢复本地检测切点。"));
    } finally {
      if (isCurrent(request, expected)) setAction(null);
    }
  }

  if (!identity) return null;

  const referenceVideoUrl = `/api/projects/${encodeURIComponent(project.id)}/reference-media/content?version=${encodeURIComponent(project.referenceMedia!.id)}`;
  return <section className={`shot-preparation-panel${!desktop ? " shot-preparation-panel--readonly" : ""}`} aria-labelledby="shot-preparation-title">
    <div className="shot-preparation-heading">
      <div><p className="shot-preparation-kicker">SHOT PREPARATION</p><h2 id="shot-preparation-title">逐镜头前置准备</h2><p>时间线和代表帧来自本地预处理；备注与四组提示词均从空白草稿开始，需要你手动填写，并非自动分析。</p></div>
      {!desktop && <span className="shot-preparation-readonly">窄屏仅可查看</span>}
    </div>

    {loading && <p className="shot-preparation-loading" role="status"><LoaderCircle className="loading-spinner" size={17} aria-hidden="true" />正在读取逐镜头准备数据…</p>}
    {loadingError && <div className="shot-preparation-error" role="alert"><CircleAlert size={17} aria-hidden="true" /><span>{loadingError}</span><button className="secondary-action" type="button" onClick={() => void load()}>重新读取</button></div>}

    {!loading && !loadingError && state && draft && selectedShot && <>
      {dirty && <p className="shot-preparation-unsaved" role="status">有未保存修改。保存后才可导出独立准备包。</p>}
      {desktop && <div className="shot-preparation-actions" role="group" aria-label="视频工具分镜联动">
        <label>视频工具分镜
          <select aria-label="视频工具分镜" value={selectedToolkitRunId} disabled={dirty || action !== null || personAction || personControlActive || state.toolkitScenes.length === 0} onChange={(event) => setSelectedToolkitRunId(event.target.value)}>
            {state.toolkitScenes.length === 0 ? <option value="">当前参考视频没有已完成的视频工具分镜任务</option> : state.toolkitScenes.map((run) => <option key={run.toolkitRunId} value={run.toolkitRunId}>{run.label} · 切点版本 {run.cutRevision}</option>)}
          </select>
        </label>
        <button className="secondary-action" type="button" disabled={!selectedToolkitRun || dirty || action !== null || personAction || personControlActive} onClick={() => void applyTimeline()}>{action === "timeline" ? "正在应用切点…" : "应用选中切点"}</button>
        <button className="secondary-action" type="button" disabled={dirty || action !== null || personAction || personControlActive} onClick={() => void load(identity, true)}>刷新分镜结果</button>
        {state.timelineOverride && <><span>已应用视频工具分镜：{appliedTimelineLabel} · 切点版本 {state.timelineOverride.cutRevision}</span><button className="secondary-action" type="button" disabled={dirty || action !== null || personAction || personControlActive} onClick={() => void restoreTimeline()}>恢复本地检测切点</button></>}
        {dirty && <small>请先保存草稿后再切换时间线。</small>}
      </div>}
      <div className="shot-preparation-workbench">
        <nav className="shot-preparation-timeline" aria-label="镜头时间线">
          <h3>镜头时间线</h3>
          <ol>{draft.shots.map((shot, index) => <li key={shot.id}><button type="button" className={shot.id === selectedShot.id ? "is-selected" : ""} aria-current={shot.id === selectedShot.id ? "true" : undefined} onClick={() => selectShot(shot)}><span>镜头 {String(index + 1).padStart(2, "0")}</span><small>{timecode(shot.startSeconds)} — {timecode(shot.endSeconds)}</small></button></li>)}</ol>
        </nav>
        <div className="shot-preparation-preview">
          <figure><img src={shotRepresentativeFrameUrl(project.id, selectedShot.id, state.sourceId, state.preprocessingId, state.revision)} alt={`镜头代表帧 ${timecode(selectedShot.representativeSeconds)}`} /><figcaption>代表帧 · {timecode(selectedShot.representativeSeconds)}</figcaption></figure>
          <PersonControlPreview shot={selectedShot} referenceVideoUrl={referenceVideoUrl} />
        </div>
        <div className="shot-preparation-editor">
          <div className="shot-preparation-editor-heading"><h3>镜头 {String(draft.shots.findIndex((shot) => shot.id === selectedShot.id) + 1).padStart(2, "0")}</h3><span>{timecode(selectedShot.startSeconds)} — {timecode(selectedShot.endSeconds)}</span></div>
          <label>镜头备注<textarea aria-label="镜头备注" readOnly={editingLocked} value={selectedShot.notes} placeholder="需要手动填写：镜头内容、运动和制作注意事项" onChange={(event) => updateShot((shot) => ({ ...shot, notes: event.target.value }))} /></label>
          <div className="shot-preparation-prompt-grid">
            {([ ["positiveZh", "中文正向提示词"], ["negativeZh", "中文负向提示词"], ["positiveEn", "English positive prompt"], ["negativeEn", "English negative prompt"] ] as Array<[keyof ShotPrompts, string]>).map(([key, label]) => <label key={key}>{label}<textarea readOnly={editingLocked} value={selectedShot.prompts[key]} placeholder="需要手动填写，不是自动分析" onChange={(event) => updateShot((shot) => ({ ...shot, prompts: { ...shot.prompts, [key]: event.target.value } }))} /></label>)}
          </div>
          <div className="shot-preparation-controls" aria-label="控制素材状态">
            <h3>控制素材检查</h3>
            <p className={`shot-preparation-control shot-preparation-control--${selectedShot.controls.depth}`}>{controlsLabel(selectedShot)}</p>
            <p className={`shot-preparation-control shot-preparation-control--${selectedShot.controls.pose}`}>姿态：{personControlLabels[selectedShot.controls.pose]}</p>
            <p className={`shot-preparation-control shot-preparation-control--${selectedShot.controls.mask}`}>遮罩：{personControlLabels[selectedShot.controls.mask]}</p>
            {selectedShot.personControl?.status === "failed" && <p className="shot-preparation-control shot-preparation-control--failed">人物控制提取失败：{selectedShot.personControl.error || "本地处理未完成。"}</p>}
            {personQualityText(selectedShot.personControl) && <p className={`shot-preparation-control shot-preparation-control--${selectedShot.personControl?.quality?.status}`}>{personQualityText(selectedShot.personControl)}</p>}
            {desktop && <div className="shot-preparation-person-action">
              <p>仅提取单人身体 33 点骨架与人体遮罩，不承诺 OpenPose 兼容，也不用于任意物体分割。</p>
              {!state.personControlEnvironment?.ready && <small>{state.personControlEnvironment?.message || "本地人物控制模型不可用；不会自动安装 ComfyUI。"}</small>}
              {dirty && <small>请先保存草稿后再提取，避免以旧版本运行。</small>}
              <button className="secondary-action" type="button" disabled={!canExtractPersonControl} onClick={() => void extractPersonControl()}>{personAction ? <LoaderCircle className="loading-spinner" size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}{personAction ? "正在提交人物控制提取…" : "提取当前镜头的人物控制素材"}</button>
            </div>}
          </div>
          {desktop && state.canAnalyze && <div className="shot-preparation-analysis"><p>仅在你点击后，才会将该镜头 4 张采样帧拼图和时间信息发送至当前分析服务，可能产生费用；不会上传完整视频，也不会自动调用付费模型。</p><button className="secondary-action" type="button" disabled={!canAnalyze} onClick={() => void analyze()}>{action === "analyze" ? <LoaderCircle className="loading-spinner" size={16} aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}{action === "analyze" ? "正在分析当前镜头…" : "分析当前镜头并生成提示词"}</button>{dirty && <small>分析前会先保存当前修改。</small>}</div>}
        </div>
      </div>
      {desktop && <div className="shot-preparation-actions"><button className="primary-action" type="button" disabled={!dirty || action !== null} onClick={() => void save()}>{action === "save" ? <LoaderCircle className="loading-spinner" size={17} aria-hidden="true" /> : <Save size={17} aria-hidden="true" />}{action === "save" ? "正在保存…" : "保存逐镜头准备"}</button><button className="secondary-action" type="button" disabled={dirty || action !== null} onClick={() => void downloadPackage()}>{action === "package" ? <LoaderCircle className="loading-spinner" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}{action === "package" ? "正在导出…" : "导出逐镜头准备包"}</button></div>}
      {actionError && <div className="shot-preparation-error" role="alert"><CircleAlert size={17} aria-hidden="true" /><span>{actionError}</span></div>}
    </>}
  </section>;
}
