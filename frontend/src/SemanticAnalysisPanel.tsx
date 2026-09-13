import { type RefObject, useEffect, useRef, useState } from "react";
import { Check, CircleAlert, Clock3, LoaderCircle, Play, RefreshCw, RotateCcw } from "lucide-react";

import type { AnalysisProviderConfiguration, Project, SemanticAnalysis, StaticVisualFacts, StructuredVisualAnalysis, TemporalFacts } from "./models";
import { startSemanticAnalysis } from "./analysisProviderApi";
import { getProject } from "./localPreprocessingApi";

const DESKTOP_QUERY = "(min-width: 1024px)";
const DEFAULT_POLL_INTERVAL_MS = 1_000;

type Props = {
  project: Project;
  provider: AnalysisProviderConfiguration | null;
  onProjectUpdated: (project: Project) => void;
  start?: typeof startSemanticAnalysis;
  load?: typeof getProject;
  pollIntervalMs?: number;
};

function useDesktop() {
  const [isDesktop, setIsDesktop] = useState(() => (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(DESKTOP_QUERY).matches
      : false
 ));
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const query = window.matchMedia(DESKTOP_QUERY);
    const update = () => setIsDesktop(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return isDesktop;
}

function messageFor(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function providerLabel(provider: string) {
  return provider === "bailian" ? "阿里云百炼" : "本地 OpenAI 兼容服务";
}

function taskTitle(task: SemanticAnalysis) {
  if (task.status === "queued") return "语义分析正在排队";
  if (task.status === "running") return "语义分析正在运行";
  if (task.status === "completed") return "语义分析已完成";
  return "语义分析失败";
}

const factLabels: Record<string, string> = {
  subject: "主体", scene: "场景", composition: "构图", viewpoint: "视角", lighting: "光线", color: "色彩", visualStyle: "视觉风格",
  subjectMotion: "主体运动", environmentalMotion: "环境运动", cameraMotion: "镜头运动", rhythm: "节奏", suggestedDuration: "建议时长", audio: "音频建议",
};

function FactList({ facts }: { facts: StaticVisualFacts | TemporalFacts | StructuredVisualAnalysis["generationSuggestions"] }) {
  return <dl className="semantic-analysis-ledger">{Object.entries(facts).map(([key, value]) => <div key={key}><dt>{factLabels[key]}</dt><dd>{key === "suggestedDuration" ? `${value} 秒` : value}</dd></div>)}</dl>;
}

function Result({ task, mediaType, resultHeadingRef }: { task: SemanticAnalysis; mediaType: "image" | "video"; resultHeadingRef: RefObject<HTMLHeadingElement> }) {
  if (task.status !== "completed" || !task.result) return null;
  const { observedFacts, generationSuggestions } = task.result;
  return <div className="semantic-analysis-result">
    <section aria-labelledby="observed-facts-title">
      <h3 ref={resultHeadingRef} id="observed-facts-title" tabIndex={-1}>可观察事实</h3>
      <FactList facts={observedFacts.staticVisual} />
      {mediaType === "video" && observedFacts.temporal && <FactList facts={observedFacts.temporal} />}
    </section>
    <section aria-labelledby="generation-suggestions-title">
      <h3 id="generation-suggestions-title">生成建议</h3>
      <FactList facts={generationSuggestions} />
    </section>
  </div>;
}

function Disclosure({ project, provider, onCancel, onConfirm, isSubmitting }: { project: Project; provider: AnalysisProviderConfiguration; onCancel: () => void; onConfirm: () => void; isSubmitting: boolean }) {
  const preprocessing = project.localPreprocessing!;
  const isImage = project.referenceMedia?.type === "image";
  const dimensions = preprocessing.proxySummary?.mediaType === "image"
    ? preprocessing.proxySummary.proxySize
    : null;
  const ratio = dimensions ? `${dimensions.width}:${dimensions.height}` : "由本地预处理记录";
  return <div className="semantic-analysis-disclosure" aria-labelledby="semantic-disclosure-title">
    <h3 id="semantic-disclosure-title">确认发送分析代理</h3>
    <p>将使用 {providerLabel(provider.provider)} 的 {provider.model} 进行本次语义分析。</p>
    <dl>
      <div><dt>实际发送内容</dt><dd>{isImage ? `analysis-proxy.jpg（${dimensions?.width ?? "未知"}×${dimensions?.height ?? "未知"}，比例 ${ratio}）` : "contact-sheet.jpg 与 analysis-proxy.json"}</dd></div>
      <div><dt>明确不发送</dt><dd>不会发送原始素材、项目名或本地文件路径。</dd></div>
      <div><dt>失败处理</dt><dd>失败后保留本地预处理结果，且不会自动切换供应商或模型。</dd></div>
    </dl>
    <div className="semantic-analysis-disclosure-actions">
      <button className="secondary-action" type="button" disabled={isSubmitting} onClick={onCancel}>取消</button>
      <button className="primary-action" type="button" disabled={isSubmitting} onClick={onConfirm}><Play aria-hidden="true" size={17} />{isSubmitting ? "正在提交…" : "确认并开始语义分析"}</button>
    </div>
  </div>;
}

export function SemanticAnalysisPanel({
  project,
  provider,
  onProjectUpdated,
  start = startSemanticAnalysis,
  load = getProject,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}: Props) {
  const isDesktop = useDesktop();
  const task = project.semanticAnalysis ?? null;
  const canPoll = task?.status === "queued" || task?.status === "running";
  const canStart = Boolean(project.referenceMedia && project.localPreprocessing?.status === "completed" && provider?.model && (provider.credentialState === "configured" || provider.provider === "local_openai_compatible"));
  const [showDisclosure, setShowDisclosure] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [shouldRestoreDisclosureFocus, setShouldRestoreDisclosureFocus] = useState(false);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const currentProjectIdRef = useRef(project.id);
  const callbackRef = useRef(onProjectUpdated);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const startButtonRef = useRef<HTMLButtonElement>(null);
  const retryButtonRef = useRef<HTMLButtonElement>(null);
  const disclosureOriginRef = useRef<"start" | "retry">("start");
  const previousStatusRef = useRef<SemanticAnalysis["status"] | null>(task?.status ?? null);
  currentProjectIdRef.current = project.id;
  callbackRef.current = onProjectUpdated;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; requestGenerationRef.current += 1; };
  }, []);

  useEffect(() => {
    requestGenerationRef.current += 1;
    setShowDisclosure(false);
    setIsSubmitting(false);
    setSubmitError("");
    setRefreshError("");
  }, [project.id]);

  useEffect(() => {
    previousStatusRef.current = task?.status ?? null;
  }, [project.id]);

  useEffect(() => {
    if (!shouldRestoreDisclosureFocus) return;
    (disclosureOriginRef.current === "retry" ? retryButtonRef.current : startButtonRef.current)?.focus();
    setShouldRestoreDisclosureFocus(false);
  }, [shouldRestoreDisclosureFocus]);

  useEffect(() => {
    if (previousStatusRef.current === "queued" || previousStatusRef.current === "running") {
      if (task?.status === "completed") resultHeadingRef.current?.focus();
    }
    previousStatusRef.current = task?.status ?? null;
  }, [task?.status]);

  useEffect(() => {
    if (!canPoll) return undefined;
    const generation = ++requestGenerationRef.current;
    const projectId = project.id;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active = true;
    let inFlight = false;
    const isCurrent = () => active && mountedRef.current && requestGenerationRef.current === generation && currentProjectIdRef.current === projectId;
    const schedule = () => {
      if (!isCurrent() || timer !== undefined) return;
      timer = setTimeout(() => { timer = undefined; void refresh(); }, pollIntervalMs);
    };
    const refresh = async () => {
      if (!isCurrent() || inFlight) return;
      inFlight = true;
      try {
        const updated = await load(projectId);
        if (!isCurrent()) return;
        setRefreshError("");
        callbackRef.current(updated);
        if (isCurrent() && (updated.semanticAnalysis?.status === "queued" || updated.semanticAnalysis?.status === "running")) schedule();
      } catch (error) {
        if (isCurrent()) { setRefreshError(messageFor(error, "暂时无法刷新状态")); schedule(); }
      } finally { inFlight = false; }
    };
    schedule();
    return () => { active = false; requestGenerationRef.current += 1; if (timer !== undefined) clearTimeout(timer); };
  }, [canPoll, load, pollIntervalMs, project.id, task?.status]);

  function openDisclosure(origin: "start" | "retry") {
    if (!isDesktop || !canStart || isSubmitting) return;
    setSubmitError("");
    disclosureOriginRef.current = origin;
    setShowDisclosure(true);
  }

  function cancelDisclosure() {
    setShowDisclosure(false);
    setShouldRestoreDisclosureFocus(true);
  }

  async function confirm() {
    if (!isDesktop || !provider || !provider.model || isSubmitting) return;
    const generation = ++requestGenerationRef.current;
    const projectId = project.id;
    const isCurrent = () => mountedRef.current && requestGenerationRef.current === generation && currentProjectIdRef.current === projectId;
    setIsSubmitting(true);
    setSubmitError("");
    try {
      const updated = await start(projectId, provider.provider, provider.model);
      if (!isCurrent()) return;
      setShowDisclosure(false);
      callbackRef.current(updated);
    } catch (error) {
      if (isCurrent()) setSubmitError(messageFor(error, "无法启动语义分析，请重试。"));
    } finally { if (isCurrent()) setIsSubmitting(false); }
  }

  return <section className={`semantic-analysis-panel${isDesktop ? "" : " semantic-analysis-panel--readonly"}`} aria-labelledby="semantic-analysis-title">
    <div className="semantic-analysis-heading"><div><h2 id="semantic-analysis-title">语义分析</h2><p>基于本地生成的分析代理，不上传原始参考素材。</p></div>{!isDesktop && <p className="analysis-readonly">窄屏仅查看任务与结果</p>}</div>
    {(!task || task.status === "completed") && !showDisclosure && isDesktop && <div className="semantic-analysis-start"><p>{canStart ? "确认发送内容后，才会向所选服务提交本次分析。" : "请先完成本地预处理，并在上方保存当前分析服务配置。"}</p><button ref={startButtonRef} className="primary-action" type="button" disabled={!canStart} onClick={() => openDisclosure("start")}><Play aria-hidden="true" size={17} />开始语义分析</button></div>}
    {task?.status === "failed" && isDesktop && !showDisclosure && <button ref={retryButtonRef} className="secondary-action semantic-analysis-retry" type="button" disabled={!canStart} onClick={() => openDisclosure("retry")}><RotateCcw aria-hidden="true" size={17} />只重试语义分析</button>}
    {showDisclosure && provider && <Disclosure project={project} provider={provider} isSubmitting={isSubmitting} onCancel={cancelDisclosure} onConfirm={() => void confirm()} />}
    {task && <div className={`semantic-analysis-task semantic-analysis-task--${task.status}`} role="status" aria-live="polite" aria-label={taskTitle(task)}><div><h3>{taskTitle(task)}</h3><p>{providerLabel(task.provider)} · {task.model}</p></div>{task.status === "completed" ? <Check aria-hidden="true" size={20} /> : task.status === "failed" ? <CircleAlert aria-hidden="true" size={20} /> : task.status === "running" ? <LoaderCircle aria-hidden="true" size={20} /> : <Clock3 aria-hidden="true" size={20} />}</div>}
    {task?.status === "failed" && task.error && <p className="semantic-analysis-error" role="alert"><CircleAlert aria-hidden="true" size={17} />{task.error.message}</p>}
    {submitError && <p className="semantic-analysis-error" role="alert"><CircleAlert aria-hidden="true" size={17} />{submitError}</p>}
    {refreshError && canPoll && <p className="semantic-analysis-refresh-error">暂时无法刷新状态：{refreshError} <RefreshCw aria-hidden="true" size={15} /></p>}
    {task && <Result task={task} mediaType={project.referenceMedia?.type ?? "image"} resultHeadingRef={resultHeadingRef} />}
  </section>;
}
