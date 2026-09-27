import { useEffect, useMemo, useRef, useState } from "react";
import { Check, CircleAlert, Clipboard, Download, LoaderCircle, Play, RefreshCw, Save, ServerCog } from "lucide-react";

import { promptConfigurationIssue } from "./promptAvailability";
import { AspectRatioPicker } from "./AspectRatioPicker";
import type { AnalysisProviderConfiguration, Project } from "./models";
import { ratioLabel, resolveReproductionAspect } from "./videoAspect";
import {
  checkComfy,
  downloadReproductionPackage,
  generateReproductionPrompts,
  getReproduction,
  refreshReproductionRun,
  resolveReproductionRun,
  saveReproduction,
  startReproductionRun,
  type ComfyCheck,
  type ReproductionPrompts,
  type ReproductionRun,
  type ReproductionSettings,
  type ReproductionState,
} from "./reproductionApi";
import "./reproduction.css";

const DESKTOP_QUERY = "(min-width: 1024px)";
const POLL_INTERVAL_MS = 1_500;

type Props = { project: Project; analysisProviders?: AnalysisProviderConfiguration[]; preparationOnly?: boolean };
type Draft = Pick<ReproductionState, "prompts" | "settings" | "comfyUrl">;
type CheckedConnection = { result: ComfyCheck; revision: number; comfyUrl: string; fingerprint: string };

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

function draftFrom(state: ReproductionState): Draft {
  return { prompts: state.prompts, settings: state.settings, comfyUrl: state.comfyUrl };
}

function sameDraft(left: Draft | null, right: Draft | null) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function duration(settings: ReproductionSettings) {
  const seconds = settings.fps > 0 ? settings.frames / settings.fps : 0;
  return `${seconds.toFixed(seconds % 1 === 0 ? 0 : 2)} 秒`;
}

function statusLabel(status: ReproductionRun["status"]) {
  return ({ submitting: "正在提交", queued: "队列中", running: "生成中", completed: "已完成", failed: "失败", unknown: "状态未知" })[status];
}

function activeRun(run: ReproductionRun) {
  return run.status === "submitting" || run.status === "queued" || run.status === "running";
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function UnknownRunRecovery({
  value,
  disabled,
  onChange,
  onResolve,
}: {
  value: { promptId: string; confirmedNotQueued: boolean };
  disabled: boolean;
  onChange: (next: { promptId: string; confirmedNotQueued: boolean }) => void;
  onResolve: (confirmedNotQueued: boolean) => void;
}) {
  return <div className="reproduction-unknown-recovery">
    <p>无法确认这次请求是否进入了 ComfyUI 队列。系统不会自动将它视为未提交。</p>
    <label>ComfyUI prompt ID<input value={value.promptId} disabled={disabled} onChange={(event) => onChange({ ...value, promptId: event.target.value })} placeholder="粘贴 prompt ID 后继续跟踪" /></label>
    <div className="reproduction-recovery-actions"><button className="secondary-action" type="button" disabled={disabled || !value.promptId.trim()} onClick={() => onResolve(false)}>继续跟踪</button></div>
    <label className="reproduction-confirm-queue"><input type="checkbox" checked={value.confirmedNotQueued} disabled={disabled} onChange={(event) => onChange({ ...value, confirmedNotQueued: event.target.checked })} />我已在 ComfyUI 确认未排队，允许重新提交</label>
    <button className="secondary-action" type="button" disabled={disabled || !value.confirmedNotQueued} onClick={() => onResolve(true)}>确认未排队并恢复提交</button>
  </div>;
}

export function ReproductionPanel({ project, analysisProviders, preparationOnly = false }: Props) {
  const isDesktop = useDesktop();
  const projectAnalysisReady = project.semanticAnalysis?.status === "completed";
  const [state, setState] = useState<ReproductionState | null>(null);
  const [savedDraft, setSavedDraft] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingError, setLoadingError] = useState("");
  const [action, setAction] = useState<"prompts" | "save" | "package" | "check" | "run" | "resolve" | null>(null);
  const [actionError, setActionError] = useState("");
  const [check, setCheck] = useState<CheckedConnection | null>(null);
  const [copyFeedback, setCopyFeedback] = useState("");
  const [resolutionDrafts, setResolutionDrafts] = useState<Record<string, { promptId: string; confirmedNotQueued: boolean }>>({});
  const requestId = useRef(0);
  const mounted = useRef(false);
  const currentProjectId = useRef(project.id);
  const currentSourceIdentity = useRef("");
  const latestPlan = useRef<{ revision: number; comfyUrl: string; fingerprint: string } | null>(null);
  currentProjectId.current = project.id;
  const sourceIdentity = JSON.stringify({
    referenceMediaId: project.referenceMedia?.id ?? null,
    preprocessingId: project.localPreprocessing?.id ?? null,
    semanticAnalysisId: project.semanticAnalysis?.id ?? null,
    semanticAnalysisUpdatedAt: project.semanticAnalysis?.updatedAt ?? null,
    semanticResultVersion: project.semanticAnalysis?.result?.version ?? null,
    activeDepthCaptureId: project.activeDepthCaptureId ?? null,
    activeDepthCaptureUpdatedAt: project.depthCaptures?.find((capture) => capture.id === project.activeDepthCaptureId)?.updatedAt ?? null,
  });
  currentSourceIdentity.current = sourceIdentity;
  latestPlan.current = state && draft ? { revision: state.revision, comfyUrl: draft.comfyUrl, fingerprint: JSON.stringify({ revision: state.revision, draft }) } : null;

  const applyState = (next: ReproductionState, preserveDraft = false) => {
    setState(next);
    const nextDraft = draftFrom(next);
    setSavedDraft(nextDraft);
    setDraft((current) => preserveDraft && current ? current : nextDraft);
    setCheck(null);
  };

  const isCurrent = (sequence: number, projectId: string, identity: string) => (
    mounted.current
    && requestId.current === sequence
    && currentProjectId.current === projectId
    && currentSourceIdentity.current === identity
  );

  const load = async (projectId = project.id, identity = sourceIdentity, preserveDraft = false) => {
    const sequence = ++requestId.current;
    setLoading(true);
    setLoadingError("");
    try {
      const next = await getReproduction(projectId);
      if (!isCurrent(sequence, projectId, identity)) return;
      applyState(next, preserveDraft);
    } catch (error) {
      if (isCurrent(sequence, projectId, identity)) setLoadingError(errorMessage(error, "无法读取复刻方案。"));
    } finally {
      if (isCurrent(sequence, projectId, identity)) setLoading(false);
    }
  };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestId.current += 1; };
  }, []);

  const previousProjectId = useRef<string | null>(null);
  useEffect(() => {
    const switchingProject = previousProjectId.current !== null && previousProjectId.current !== project.id;
    previousProjectId.current = project.id;
    requestId.current += 1;
    setCheck(null); setAction(null); setActionError(""); setCopyFeedback(""); setResolutionDrafts({});
    if (switchingProject || previousProjectId.current === project.id && state === null) {
      setState(null); setDraft(null); setSavedDraft(null);
    }
    if (!projectAnalysisReady) {
      setState((current) => current ? { ...current, analysisReady: false, canGeneratePrompts: false, stale: true } : current);
      setLoading(false);
      return;
    }
    void load(project.id, sourceIdentity, !switchingProject);
    // Identity intentionally includes every source that can make the stored plan stale.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, projectAnalysisReady, sourceIdentity]);

  const dirty = !sameDraft(draft, savedDraft);
  const hasActiveRuns = Boolean(state?.runs?.some(activeRun));
  const currentRevision = state?.revision ?? 0;
  const mutationsAllowed = isDesktop && state !== null;
  const hasPrompts = Boolean(draft?.prompts);
  const canUseSavedPlan = mutationsAllowed && Boolean(state?.analysisReady) && hasPrompts && !dirty && !state.stale;
  const checkIsCurrent = check !== null && check.revision === currentRevision && check.comfyUrl === draft?.comfyUrl && check.fingerprint === latestPlan.current?.fingerprint;
  const canRun = canUseSavedPlan && checkIsCurrent && check.result.connected && check.result.ready;
  const controlUnavailable = !state?.hasDepth;
  const configurationIssue = promptConfigurationIssue(project, analysisProviders);
  const canGeneratePrompts = !configurationIssue && mutationsAllowed && Boolean(state?.canGeneratePrompts) && action === null;
  const editingLocked = action === "prompts" || action === "save";
  const selectedReproductionAspect = draft ? resolveReproductionAspect(draft.settings.aspectMode ?? "smart", draft.settings, project.referenceMedia) : null;
  const reproductionAspect = draft && selectedReproductionAspect ? {
    resolvedAspect: ratioLabel(draft.settings.width, draft.settings.height), width: draft.settings.width, height: draft.settings.height,
    reason: draft.settings.width === selectedReproductionAspect.width && draft.settings.height === selectedReproductionAspect.height
      ? selectedReproductionAspect.reason : "当前为手动调整后的实际输出尺寸。",
  } : null;

  useEffect(() => {
    if (preparationOnly || !state || !hasActiveRuns || action !== null) return undefined;
    const projectId = project.id;
    const identity = sourceIdentity;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let stopped = false;
    const isPollCurrent = () => !stopped
      && mounted.current
      && currentProjectId.current === projectId
      && currentSourceIdentity.current === identity;
    const refresh = async () => {
      if (inFlight || !isPollCurrent()) return;
      inFlight = true;
      try {
        const active = state.runs.find(activeRun);
        if (!active) return;
        const next = await refreshReproductionRun(projectId, active.id);
        if (isPollCurrent()) {
          setState(next);
          const nextSaved = draftFrom(next);
          setSavedDraft(nextSaved);
          setDraft((current) => sameDraft(current, savedDraft) ? nextSaved : current);
          setCheck(null);
          setActionError("");
        }
      } catch (error) {
        if (isPollCurrent()) setActionError(errorMessage(error, "暂时无法刷新生成状态。"));
      } finally {
        inFlight = false;
        if (isPollCurrent()) timer = setTimeout(refresh, POLL_INTERVAL_MS);
      }
    };
    timer = setTimeout(refresh, POLL_INTERVAL_MS);
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [preparationOnly, action, hasActiveRuns, project.id, savedDraft, sourceIdentity, state]);

  function updatePrompts(key: keyof ReproductionPrompts, value: string) {
    setCheck(null);
    setDraft((current) => current?.prompts ? { ...current, prompts: { ...current.prompts, [key]: value } } : current);
  }

  function updateSetting<Key extends keyof ReproductionSettings>(key: Key, value: ReproductionSettings[Key]) {
    setCheck(null);
    setDraft((current) => current ? { ...current, settings: { ...current.settings, [key]: value } } : current);
  }

  function updateAspectMode(aspectMode: NonNullable<ReproductionSettings["aspectMode"]>) {
    if (!draft) return;
    const resolution = resolveReproductionAspect(aspectMode, draft.settings, project.referenceMedia);
    setCheck(null);
    setDraft((current) => current ? { ...current, settings: { ...current.settings, aspectMode,
      width: resolution.width, height: resolution.height } } : current);
  }

  async function generatePrompts() {
    if (!state || !canGeneratePrompts) return;
    const sequence = ++requestId.current;
    const projectId = project.id;
    const identity = sourceIdentity;
    setAction("prompts"); setActionError("");
    try {
      const next = await generateReproductionPrompts(projectId, currentRevision);
      if (isCurrent(sequence, projectId, identity)) applyState(next);
    } catch (error) {
      if (isCurrent(sequence, projectId, identity)) setActionError(errorMessage(error, "无法生成提示词。"));
    } finally { if (isCurrent(sequence, projectId, identity)) setAction(null); }
  }

  async function save() {
    if (!state || !draft || !draft.prompts || !mutationsAllowed || !dirty) return;
    const sequence = ++requestId.current;
    const projectId = project.id;
    const identity = sourceIdentity;
    setAction("save"); setActionError("");
    try {
      const next = await saveReproduction(projectId, { revision: currentRevision, ...draft });
      if (isCurrent(sequence, projectId, identity)) applyState(next);
    } catch (error) {
      if (isCurrent(sequence, projectId, identity)) setActionError(errorMessage(error, "无法保存复刻方案。"));
    } finally { if (isCurrent(sequence, projectId, identity)) setAction(null); }
  }

  async function runCheck() {
    if (!canUseSavedPlan || action) return;
    const sequence = ++requestId.current;
    const projectId = project.id;
    const identity = sourceIdentity;
    const plan = latestPlan.current;
    setAction("check"); setActionError("");
    try {
      const result = await checkComfy(projectId);
      if (isCurrent(sequence, projectId, identity) && plan && latestPlan.current?.fingerprint === plan.fingerprint) setCheck({ result, ...plan });
    } catch (error) {
      if (isCurrent(sequence, projectId, identity)) setActionError(errorMessage(error, "无法检查本地 ComfyUI。"));
    } finally { if (isCurrent(sequence, projectId, identity)) setAction(null); }
  }

  async function run() {
    if (!canUseSavedPlan || action) return;
    const sequence = ++requestId.current;
    const projectId = project.id;
    const identity = sourceIdentity;
    setAction("run"); setActionError("");
    try {
      const next = await startReproductionRun(projectId, currentRevision);
      if (isCurrent(sequence, projectId, identity)) applyState(next);
    } catch (error) {
      if (isCurrent(sequence, projectId, identity)) {
        const failure = errorMessage(error, "无法提交本地生成。");
        try {
          const next = await getReproduction(projectId);
          if (isCurrent(sequence, projectId, identity)) applyState(next, true);
        } catch {
          // Keep the original submission failure; a recovery read is best effort.
        }
        if (isCurrent(sequence, projectId, identity)) setActionError(failure);
      }
    } finally { if (isCurrent(sequence, projectId, identity)) setAction(null); }
  }

  async function downloadPackage() {
    if (!canUseSavedPlan || action) return;
    const sequence = ++requestId.current;
    const projectId = project.id;
    const identity = sourceIdentity;
    setAction("package"); setActionError("");
    try {
      await downloadReproductionPackage(projectId);
    } catch (error) {
      if (isCurrent(sequence, projectId, identity)) setActionError(errorMessage(error, "无法导出复刻包。"));
    } finally { if (isCurrent(sequence, projectId, identity)) setAction(null); }
  }

  async function copyPrompt(label: string, value: string) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不支持剪贴板写入。");
      await navigator.clipboard.writeText(value);
      if (mounted.current) setCopyFeedback(`已复制${label}。`);
    } catch (error) {
      if (mounted.current) setCopyFeedback(errorMessage(error, `无法复制${label}。请手动选择文本。`));
    }
  }

  async function resolveUnknownRun(run: ReproductionRun, confirmedNotQueued: boolean) {
    const resolution = resolutionDrafts[run.id] ?? { promptId: "", confirmedNotQueued: false };
    if (!confirmedNotQueued && !resolution.promptId.trim()) return;
    const sequence = ++requestId.current;
    const projectId = project.id;
    const identity = sourceIdentity;
    setAction("resolve"); setActionError("");
    try {
      const next = await resolveReproductionRun(projectId, run.id, {
        promptId: confirmedNotQueued ? null : resolution.promptId.trim(),
        confirmedNotQueued,
      });
      if (isCurrent(sequence, projectId, identity)) applyState(next);
    } catch (error) {
      if (isCurrent(sequence, projectId, identity)) setActionError(errorMessage(error, "无法恢复未知生成状态。"));
    } finally { if (isCurrent(sequence, projectId, identity)) setAction(null); }
  }

  const strategyTemplates = useMemo(() => new Map((state?.templates ?? []).map((template) => [template.strategy, template])), [state?.templates]);

  return <section className={`reproduction-panel${!isDesktop ? " reproduction-panel--readonly" : ""}`} aria-labelledby="reproduction-title">
    <div className="reproduction-heading">
      <div>
        <h2 id="reproduction-title">{preparationOnly ? "提示词与工作流准备" : "后续生成工作台"}</h2>
        <p>{preparationOnly ? "编辑提示词、选择候选模板并导出离线工作流；最终生成由外部工具完成。" : "从复刻方案导出工作流，或提交到你填写的本地 ComfyUI。"}</p>
      </div>
      {!isDesktop && <span className="reproduction-readonly">窄屏仅可查看、复制与预览</span>}
    </div>

    {configurationIssue && <p className="reproduction-prerequisite">{configurationIssue}</p>}
    {project.semanticAnalysis?.status === "running" || project.semanticAnalysis?.status === "queued" ? <p role="status">正在等待语义分析完成，完成后可生成全片提示词。</p> : null}
    {loading && <p className="reproduction-loading" role="status"><LoaderCircle className="loading-spinner" size={17} aria-hidden="true" />正在读取复刻方案…</p>}
    {loadingError && <div className="reproduction-error" role="alert"><CircleAlert size={17} aria-hidden="true" /><span>{loadingError}</span><button className="secondary-action" type="button" onClick={() => void load()}>重新读取</button></div>}

    {!loading && !loadingError && !state && !projectAnalysisReady && <div className="reproduction-prerequisite">
      <h3>尚未具备生成前置</h3>
      <p>完整路径：上传参考素材 → 本地预处理 → 语义分析 → 深度捕捉（需要控制策略时）→ 在此编辑并保存复刻方案。</p>
      <p>请先完成当前参考素材的语义分析；完成后可在这里生成可编辑提示词和本地工作流方案。</p>
    </div>}

    {!loadingError && state && draft && <>
      {!state.analysisReady && <div className="reproduction-prerequisite">
        <h3>尚未具备生成前置</h3>
        <p>完整路径：上传参考素材 → 本地预处理 → 语义分析 → 深度捕捉（需要控制策略时）→ 在此编辑并保存复刻方案。</p>
        <p>请先完成当前参考素材的语义分析；完成后可在这里生成可编辑提示词和本地工作流方案。</p>
      </div>}

      {state.stale && <div className="reproduction-stale" role="status"><RefreshCw size={17} aria-hidden="true" />参考素材或分析已更新。请重新生成提示词并保存；导出和执行当前方案已暂停。</div>}
      {dirty && <p className="reproduction-unsaved" role="status">{preparationOnly ? "有未保存修改，请先保存再导出。" : "有未保存修改。保存前不能导出、检测或提交生成。"}</p>}

      <div className="reproduction-section reproduction-prompts">
        <div className="reproduction-section-heading"><h3>中英文提示词</h3>{isDesktop && <button className="secondary-action" type="button" disabled={!canGeneratePrompts} onClick={() => void generatePrompts()}>{action === "prompts" ? <LoaderCircle className="loading-spinner" size={16} aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}{state.prompts ? "重新生成提示词" : "生成提示词"}</button>}</div>
        <p className="reproduction-disclosure">{canGeneratePrompts && "已具备生成条件，点击按钮后才会调用分析服务。"}生成提示词时，只会向已配置的分析服务发送已有的结构化分析，不会发送原始参考素材；不会自动调用 ComfyUI。</p>
        {!state.prompts ? <p className="reproduction-empty">{state.analysisReady ? "尚未生成提示词。确认以上数据边界后，可生成一组可继续编辑的建议。" : "完成语义分析后可生成提示词。"}</p> : <div className="reproduction-prompt-grid">
          {([ ["positiveZh", "中文正向提示词"], ["negativeZh", "中文负向提示词"], ["positiveEn", "English positive prompt"], ["negativeEn", "English negative prompt"] ] as Array<[keyof ReproductionPrompts, string]>).map(([key, label]) => <label key={key} className="reproduction-field"><span>{label}</span><textarea readOnly={!isDesktop || editingLocked} value={draft.prompts?.[key] ?? ""} onChange={(event) => updatePrompts(key, event.target.value)} /><button className="copy-action" type="button" onClick={() => void copyPrompt(label, draft.prompts?.[key] ?? "")}><Clipboard size={15} aria-hidden="true" />复制</button></label>)}
        </div>}
      </div>

      <div className="reproduction-section reproduction-settings">
        <div className="reproduction-section-heading"><h3>模板与输出参数</h3><span>{duration(draft.settings)}</span></div>
        <fieldset disabled={!isDesktop || editingLocked || !hasPrompts}><legend>生成策略</legend><div className="reproduction-strategies">
          {(["wan22_i2v", "wan22_fun_control"] as const).map((strategy) => {
            const template = strategyTemplates.get(strategy);
            const unavailable = strategy === "wan22_fun_control" && controlUnavailable;
            return <label key={strategy} className={`reproduction-strategy${unavailable ? " is-unavailable" : ""}`}><input type="radio" name="strategy" value={strategy} checked={draft.settings.strategy === strategy} disabled={unavailable} onChange={() => updateSetting("strategy", strategy)} /><span><strong>{template?.label ?? (strategy === "wan22_i2v" ? "Wan2.2 I2V" : "Wan2.2 Fun Control")}</strong><small>{unavailable ? "需要当前参考的已确认深度素材" : "候选实验模板，尚未经过本机模型验证"}</small></span></label>;
          })}
        </div></fieldset>
        {reproductionAspect && <AspectRatioPicker name={`reproduction-aspect-${project.id}`} value={draft.settings.aspectMode ?? "smart"}
          onChange={updateAspectMode} resolution={reproductionAspect} disabled={!isDesktop || editingLocked || !hasPrompts} />}
        <div className="reproduction-number-fields">
          {([ ["width", "宽度", 64], ["height", "高度", 64], ["frames", "帧数", 1], ["fps", "FPS", 1], ["seed", "随机种子", 0] ] as Array<[keyof ReproductionSettings, string, number]>).map(([key, label, min]) => <label key={key}>{label}<input type="number" min={min} readOnly={!isDesktop || editingLocked || !hasPrompts} value={draft.settings[key]} onChange={(event) => updateSetting(key, Number(event.target.value))} /></label>)}
        </div>
        {!preparationOnly && <label className="reproduction-url">本地 ComfyUI 地址<input type="url" readOnly={!isDesktop || editingLocked || !hasPrompts} value={draft.comfyUrl} placeholder="http://127.0.0.1:8188" onChange={(event) => { setCheck(null); setDraft((current) => current ? { ...current, comfyUrl: event.target.value } : current); }} /></label>}
        {state.adjustments.length > 0 && <ul className="reproduction-adjustments" aria-label="方案调整说明">{state.adjustments.map((adjustment) => <li key={adjustment}>{adjustment}</li>)}</ul>}
      </div>

      {isDesktop && <div className="reproduction-actions">
        <button className="primary-action" type="button" disabled={!hasPrompts || !dirty || action !== null} onClick={() => void save()}>{action === "save" ? <LoaderCircle className="loading-spinner" size={17} aria-hidden="true" /> : <Save size={17} aria-hidden="true" />}{action === "save" ? "正在保存…" : "保存复刻方案"}</button>
        <button className="secondary-action reproduction-download" type="button" disabled={!canUseSavedPlan || action !== null} onClick={() => void downloadPackage()}>{action === "package" ? <LoaderCircle className="loading-spinner" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}{action === "package" ? "正在导出…" : "导出复刻包"}</button>
      </div>}
      {!preparationOnly && isDesktop && <details className="reproduction-comfy-step">
        <summary>可选：连接本地 ComfyUI 后继续实验</summary>
        <p>此应用不会安装 ComfyUI、节点或模型，也不会自动发起连接。真实 Queue 尚未在当前机器验证；请先自行启动并配置本地 ComfyUI，再手动检查。</p>
        <div className="reproduction-comfy-actions"><button className="secondary-action" type="button" disabled={!canUseSavedPlan || action !== null} onClick={() => void runCheck()}>{action === "check" ? <LoaderCircle className="loading-spinner" size={16} aria-hidden="true" /> : <ServerCog size={16} aria-hidden="true" />}{action === "check" ? "正在检查…" : "检查 ComfyUI"}</button><button className="primary-action" type="button" disabled={!canRun || action !== null} onClick={() => void run()}>{action === "run" ? <LoaderCircle className="loading-spinner" size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}{action === "run" ? "正在提交…" : "提交实验性生成"}</button></div>
        <p className="reproduction-run-disclosure">提交会把首帧与可用的深度控制素材发送到上方填写的本地 ComfyUI。候选实验模板不等同于已验证的可执行工作流。</p>
        {!canRun && !dirty && !state.stale && <p className="reproduction-comfy-pending">需先通过当前方案的手动连接检查，才可提交生成。</p>}
        {check && checkIsCurrent && <div className={`reproduction-check${check.result.ready ? " is-ready" : ""}`} role="status"><strong>{check.result.ready ? <Check size={16} aria-hidden="true" /> : <CircleAlert size={16} aria-hidden="true" />}{check.result.message}</strong>{check.result.version && <span>ComfyUI {check.result.version}</span>}{(check.result.missingNodes.length > 0 || check.result.missingModels.length > 0) && <p>{check.result.missingNodes.length > 0 && `缺少节点：${check.result.missingNodes.join("、")}。`}{check.result.missingModels.length > 0 && `缺少模型：${check.result.missingModels.join("、")}。`}</p>}</div>}
      </details>}
      {copyFeedback && <p className="reproduction-copy-feedback" role="status" aria-live="polite">{copyFeedback}</p>}
      {actionError && <div className="reproduction-error" role="alert"><CircleAlert size={17} aria-hidden="true" /><span>{actionError}</span></div>}

      {!preparationOnly && <div className="reproduction-section reproduction-runs"><div className="reproduction-section-heading"><h3>生成记录与预览</h3><span>{state.runs.length} 次</span></div>
        {state.runs.length === 0 ? <p className="reproduction-empty">尚未提交生成。保存方案并完成本机检查后，可从这里跟踪队列和预览输出。</p> : <ul className="reproduction-run-list">{state.runs.map((item) => <li key={item.id} className={`reproduction-run reproduction-run--${item.status}`}><div><strong>{activeRun(item) && <LoaderCircle className="loading-spinner" size={16} aria-hidden="true" />}{statusLabel(item.status)}</strong><span>{formatTime(item.createdAt)} · 方案版本 {item.revision}</span>{item.error && <p>{item.error}</p>}{item.status === "unknown" && isDesktop && <UnknownRunRecovery value={resolutionDrafts[item.id] ?? { promptId: "", confirmedNotQueued: false }} disabled={action !== null} onChange={(next) => setResolutionDrafts((current) => ({ ...current, [item.id]: next }))} onResolve={(confirmedNotQueued) => void resolveUnknownRun(item, confirmedNotQueued)} />}</div>{item.outputs.length > 0 && <div className="reproduction-output-list">{item.outputs.map((output) => <figure key={output.url}><video controls preload="metadata" src={output.url} aria-label={`生成结果 ${output.filename}`} style={item.width && item.height ? { aspectRatio: `${item.width} / ${item.height}` } : undefined} /><figcaption>{output.filename}</figcaption></figure>)}</div>}</li>)}</ul>}
      </div>}
    </>}
  </section>;
}
