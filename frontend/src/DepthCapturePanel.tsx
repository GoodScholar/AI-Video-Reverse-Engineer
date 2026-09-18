import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, Clock3, LoaderCircle, Play, RotateCcw } from "lucide-react";

import type { DepthCapture, DepthCaptureStageName, DepthDevicePreference, DepthOutputResolution, DepthQualityCriterion, Project } from "./models";
import { confirmDepthReview, depthVideoUrl, referenceVideoContentUrl, startDepthCapture } from "./depthCaptureApi";
import { getProject } from "./localPreprocessingApi";

const DESKTOP_QUERY = "(min-width: 1024px)";
const DEFAULT_POLL_INTERVAL_MS = 1_000;
const DRIFT_TOLERANCE_SECONDS = 0.08;

const stageLabels = {
  preparing: "准备素材",
  estimatingDepth: "估计深度",
  encoding: "编码控制素材",
  qualityAssessment: "质量检查",
} satisfies Record<DepthCaptureStageName, string>;
const stageNames = Object.keys(stageLabels) as DepthCaptureStageName[];

const checkLabels = {
  completeness: "完整性",
  dynamicRange: "动态范围",
  temporalFlicker: "时间闪烁",
  directionStability: "近远方向稳定性",
  edgeContinuity: "边缘连续性",
  timelineAlignment: "时间线对齐",
} as const;
const qualityCriteria = ["completeness", "dynamicRange", "temporalFlicker", "directionStability", "edgeContinuity", "timelineAlignment"] as const satisfies readonly DepthQualityCriterion[];

type Props = {
  project: Project;
  onProjectUpdated: (project: Project) => void;
  start?: typeof startDepthCapture;
  confirm?: typeof confirmDepthReview;
  load?: typeof getProject;
  pollIntervalMs?: number;
  onMutationPendingChange?: (pending: boolean) => void;
};

function useDesktop() {
  const [isDesktop, setIsDesktop] = useState(() => (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(DESKTOP_QUERY).matches
      : false
  ));

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const mediaQuery = window.matchMedia(DESKTOP_QUERY);
    const update = () => setIsDesktop(mediaQuery.matches);
    update();
    mediaQuery.addEventListener("change", update);
    return () => mediaQuery.removeEventListener("change", update);
  }, []);

  return isDesktop;
}

function messageFor(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function isActive(status: DepthCapture["status"] | undefined) {
  return status === "queued" || status === "running";
}

function taskTitle(task: DepthCapture) {
  if (task.status === "queued") return "本地深度捕捉正在排队";
  if (task.status === "running") return "本地深度捕捉正在运行";
  if (task.status === "completed") return "本地深度捕捉已完成";
  return "本地深度捕捉失败";
}

function stageStateText(status: DepthCapture["stages"][number]["status"]) {
  if (status === "completed") return "已完成";
  if (status === "running") return "进行中";
  if (status === "failed") return "失败";
  return "等待中";
}

function StageIcon({ status }: { status: DepthCapture["stages"][number]["status"] }) {
  if (status === "completed") return <Check aria-hidden="true" size={16} />;
  if (status === "running") return <LoaderCircle className="loading-spinner" aria-hidden="true" size={16} />;
  if (status === "failed") return <CircleAlert aria-hidden="true" size={16} />;
  return <Clock3 aria-hidden="true" size={16} />;
}

function mostRecent(captures: DepthCapture[]) {
  return [...captures].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0] ?? null;
}

function currentReferenceVideo(project: Project) {
  return project.referenceMedia?.type === "video" ? project.referenceMedia : null;
}

function selectDisplayCapture(project: Project): DepthCapture | null {
  const video = currentReferenceVideo(project);
  const captures = (project.depthCaptures ?? []).filter((capture) => capture.sourceReferenceVideoId === video?.id);
  const active = captures.filter((capture) => isActive(capture.status));
  return mostRecent(active)
    ?? mostRecent(captures);
}

function canStartDepthCapture(project: Project) {
  return Boolean(currentReferenceVideo(project));
}

function synchroniseVideo(source: HTMLVideoElement | null, target: HTMLVideoElement | null) {
  if (!source || !target || !Number.isFinite(source.currentTime) || !Number.isFinite(target.currentTime)) return;
  if (Math.abs(source.currentTime - target.currentTime) <= DRIFT_TOLERANCE_SECONDS + Number.EPSILON * 8) return;
  try { target.currentTime = source.currentTime; } catch { /* 媒体元数据尚未就绪。 */ }
}

function synchronisePlaybackRate(source: HTMLVideoElement | null, target: HTMLVideoElement | null) {
  if (!source || !target || !Number.isFinite(source.playbackRate) || !Number.isFinite(target.playbackRate)) return;
  if (Math.abs(source.playbackRate - target.playbackRate) <= Number.EPSILON * 8) return;
  try { target.playbackRate = source.playbackRate; } catch { /* 浏览器不支持该播放速率。 */ }
}

function qualityStatusText(status: "passed" | "review_required" | "failed") {
  if (status === "passed") return "通过";
  if (status === "review_required") return "需复核";
  return "失败";
}

function DeviceSelector({ value, onChange }: { value: DepthDevicePreference; onChange: (value: DepthDevicePreference) => void }) {
  return (
    <div className="depth-capture-device">
      <label htmlFor="depth-device-preference">深度计算设备</label>
      <select id="depth-device-preference" value={value} onChange={(event) => onChange(event.target.value as DepthDevicePreference)}>
        <option value="auto">自动选择（优先 CUDA，再 MPS）</option>
        <option value="cuda">CUDA</option>
        <option value="mps">Apple MPS</option>
        <option value="cpu">CPU</option>
      </select>
      <p>{value === "cpu" ? "CPU 慢速路径：开始前请预留更长处理时间。" : value === "auto" ? "自动选择在无 GPU 加速时可能回退到 CPU，处理会明显更慢。" : "若所选设备不可用，任务不会自动改用其他设备。"}</p>
    </div>
  );
}

function OutputResolutionSelector({ value, source, onChange }: { value: DepthOutputResolution; source: NonNullable<Project["referenceMedia"]>; onChange: (value: DepthOutputResolution) => void }) {
  const requestedShortEdge = Number.parseInt(value, 10);
  const sourceShortEdge = Math.min(source.width, source.height);
  return (
    <div className="depth-capture-device">
      <label htmlFor="depth-output-resolution">输出清晰度</label>
      <select id="depth-output-resolution" value={value} onChange={(event) => onChange(event.target.value as DepthOutputResolution)}>
        <option value="480p">480P（短边）</option>
        <option value="720p">720P（短边）</option>
      </select>
      <p>{sourceShortEdge < requestedShortEdge ? `原视频短边为 ${sourceShortEdge}px；提升到 ${requestedShortEdge}P 只会放大，不会增加画面细节。` : "按短边输出，保持原始画幅比例。"}</p>
    </div>
  );
}

export function DepthCapturePanel({
  project,
  onProjectUpdated,
  start = startDepthCapture,
  confirm = confirmDepthReview,
  load = getProject,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  onMutationPendingChange = () => undefined,
}: Props) {
  const isDesktop = useDesktop();
  const task = selectDisplayCapture(project);
  const currentVideo = currentReferenceVideo(project);
  const sourceIsCurrent = task?.sourceReferenceVideoId === currentVideo?.id;
  const canPoll = Boolean(task && sourceIsCurrent && isActive(task.status));
  const canStart = canStartDepthCapture(project);
  const referenceRef = useRef<HTMLVideoElement>(null);
  const depthRef = useRef<HTMLVideoElement>(null);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const currentProjectIdRef = useRef(project.id);
  const callbackRef = useRef(onProjectUpdated);
  const mutationCallbackRef = useRef(onMutationPendingChange);
  const currentReferenceVideoIdRef = useRef(currentVideo?.id ?? null);
  const currentTaskIdRef = useRef(task?.id ?? null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [devicePreference, setDevicePreference] = useState<DepthDevicePreference>("auto");
  const [outputResolution, setOutputResolution] = useState<DepthOutputResolution>("480p");
  const [submitError, setSubmitError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  currentProjectIdRef.current = project.id;
  callbackRef.current = onProjectUpdated;
  mutationCallbackRef.current = onMutationPendingChange;
  currentReferenceVideoIdRef.current = currentVideo?.id ?? null;
  currentTaskIdRef.current = task?.id ?? null;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
      mutationCallbackRef.current(false);
    };
  }, []);

  useEffect(() => {
    requestGenerationRef.current += 1;
    setIsSubmitting(false);
    setSubmitError("");
    setRefreshError("");
    mutationCallbackRef.current(false);
  }, [project.id, currentVideo?.id, task?.id, task?.sourceReferenceVideoId]);

  useEffect(() => {
    if (!canPoll) return undefined;
    const generation = ++requestGenerationRef.current;
    const projectId = project.id;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active = true;
    let inFlight = false;
    const isCurrent = () => (
      active
      && mountedRef.current
      && requestGenerationRef.current === generation
      && currentProjectIdRef.current === projectId
    );
    const schedule = () => {
      if (!isCurrent() || timer !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        void refresh();
      }, pollIntervalMs);
    };
    const refresh = async () => {
      if (!isCurrent() || inFlight) return;
      inFlight = true;
      try {
        const updated = await load(projectId);
        if (!isCurrent()) return;
        setRefreshError("");
        callbackRef.current(updated);
        if (isCurrent() && isActive(selectDisplayCapture(updated)?.status)) schedule();
      } catch (error) {
        if (!isCurrent()) return;
        setRefreshError(messageFor(error, "暂时无法刷新状态"));
        schedule();
      } finally {
        inFlight = false;
      }
    };
    schedule();
    return () => {
      active = false;
      requestGenerationRef.current += 1;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [canPoll, load, pollIntervalMs, project.id, task?.id, task?.status]);

  function mirrorPlayFromReference() {
    const reference = referenceRef.current;
    const depth = depthRef.current;
    if (!reference || !depth) return;
    synchroniseVideo(reference, depth);
    void depth.play().catch(() => undefined);
  }

  function mirrorPlayFromDepth() {
    const reference = referenceRef.current;
    const depth = depthRef.current;
    if (!reference || !depth) return;
    synchroniseVideo(depth, reference);
    void reference.play().catch(() => undefined);
  }

  function mirrorPauseFromReference() {
    depthRef.current?.pause();
  }

  function mirrorPauseFromDepth() {
    referenceRef.current?.pause();
  }

  function synchroniseFromReference() {
    synchroniseVideo(referenceRef.current, depthRef.current);
  }

  function synchroniseFromDepth() {
    synchroniseVideo(depthRef.current, referenceRef.current);
  }

  function synchroniseRateFromReference() {
    synchronisePlaybackRate(referenceRef.current, depthRef.current);
  }

  function synchroniseRateFromDepth() {
    synchronisePlaybackRate(depthRef.current, referenceRef.current);
  }

  async function submit() {
    if (!isDesktop || !canStart || isSubmitting) return;
    const generation = ++requestGenerationRef.current;
    const projectId = project.id;
    const referenceVideoId = currentVideo?.id ?? null;
    const isCurrent = () => (
      mountedRef.current
      && requestGenerationRef.current === generation
      && currentProjectIdRef.current === projectId
      && currentReferenceVideoIdRef.current === referenceVideoId
    );
    setIsSubmitting(true);
    mutationCallbackRef.current(true);
    setSubmitError("");
    try {
      const updated = await start(projectId, devicePreference, outputResolution);
      if (isCurrent()) callbackRef.current(updated);
    } catch (error) {
      if (isCurrent()) setSubmitError(messageFor(error, "无法启动本地深度捕捉，请重试。"));
    } finally {
      if (isCurrent()) {
        setIsSubmitting(false);
        mutationCallbackRef.current(false);
      }
    }
  }

  async function confirmReview() {
    if (!isDesktop || !task || !sourceIsCurrent || isSubmitting || task.status !== "completed" || task.qualityAssessment?.status !== "review_required" || task.reviewConfirmedAt) return;
    const generation = ++requestGenerationRef.current;
    const projectId = project.id;
    const referenceVideoId = currentVideo?.id ?? null;
    const captureId = task.id;
    const isCurrent = () => (
      mountedRef.current
      && requestGenerationRef.current === generation
      && currentProjectIdRef.current === projectId
      && currentReferenceVideoIdRef.current === referenceVideoId
      && currentTaskIdRef.current === captureId
    );
    setIsSubmitting(true);
    mutationCallbackRef.current(true);
    setSubmitError("");
    try {
      const updated = await confirm(projectId, captureId);
      if (isCurrent()) callbackRef.current(updated);
    } catch (error) {
      if (isCurrent()) setSubmitError(messageFor(error, "无法确认深度质量复核，请重试。"));
    } finally {
      if (isCurrent()) {
        setIsSubmitting(false);
        mutationCallbackRef.current(false);
      }
    }
  }

  return (
    <section className={`depth-capture-panel${isDesktop ? "" : " depth-capture-panel--readonly"}`} aria-labelledby="depth-capture-title">
      <div className="depth-capture-heading">
        <div>
          <p className="depth-capture-kicker">本地控制素材</p>
          <h2 id="depth-capture-title">深度动作捕捉</h2>
        </div>
        {!isDesktop && <p className="depth-capture-readonly">移动端仅查看</p>}
      </div>

      {currentVideo && (
        <div className="depth-preview-grid">
          <figure>
            <figcaption>参考视频 · 时间轴基准</figcaption>
            <video ref={referenceRef} aria-label="参考视频预览" controls preload="metadata" src={referenceVideoContentUrl(project.id)} onPlay={mirrorPlayFromReference} onPause={mirrorPauseFromReference} onSeeking={synchroniseFromReference} onTimeUpdate={synchroniseFromReference} onRateChange={synchroniseRateFromReference} />
          </figure>
          <figure>
            <figcaption>灰度深度视频 · 同步预览</figcaption>
            {task?.status === "completed" && sourceIsCurrent ? (
              <video ref={depthRef} aria-label="灰度深度视频预览" controls preload="metadata" src={depthVideoUrl(project.id, task.id)} onPlay={mirrorPlayFromDepth} onPause={mirrorPauseFromDepth} onSeeking={synchroniseFromDepth} onTimeUpdate={synchroniseFromDepth} onRateChange={synchroniseRateFromDepth} />
            ) : <div className="depth-preview-placeholder" aria-label="灰度深度视频预览">{isActive(task?.status) && <LoaderCircle className="loading-spinner" aria-hidden="true" size={32} />}<span>{task ? "灰度视频将在编码完成后显示" : "尚未生成灰度深度视频"}</span></div>}
          </figure>
        </div>
      )}

      {!task && (
        <div className="depth-capture-empty">
          {canStart ? <p>选择输出清晰度后，可从当前参考视频提取整段灰度深度视频。</p> : <p>请先上传一段参考视频。</p>}
          {isDesktop && canStart && currentVideo && <div className="depth-capture-actions"><OutputResolutionSelector value={outputResolution} source={currentVideo} onChange={setOutputResolution} /><DeviceSelector value={devicePreference} onChange={setDevicePreference} /><button className="primary-action" type="button" disabled={isSubmitting} onClick={() => void submit()}>{isSubmitting ? <LoaderCircle className="loading-spinner" aria-hidden="true" size={17} /> : <Play aria-hidden="true" size={17} />}{isSubmitting ? "正在启动深度捕捉…" : "提取整段深度视频"}</button></div>}
        </div>
      )}

      {task && !sourceIsCurrent && <p className="depth-capture-stale">该深度素材属于已替换的参考视频，不能用于当前工作流。</p>}
      {task && sourceIsCurrent && <TaskDetails task={task} />}

      {task?.status === "failed" && sourceIsCurrent && task.error && (
        <div className="depth-capture-error" role="alert"><CircleAlert aria-hidden="true" size={17} /><span><strong>{stageLabels[task.error.stage]}失败</strong>{task.error.message}</span></div>
      )}
      {task?.status === "failed" && sourceIsCurrent && isDesktop && canStart && (
        <div className="depth-capture-actions">{currentVideo && <OutputResolutionSelector value={outputResolution} source={currentVideo} onChange={setOutputResolution} />}<DeviceSelector value={devicePreference} onChange={setDevicePreference} /><button className="secondary-action depth-capture-retry" type="button" disabled={isSubmitting} onClick={() => void submit()}>{isSubmitting ? <LoaderCircle className="loading-spinner" aria-hidden="true" size={17} /> : <RotateCcw aria-hidden="true" size={17} />}{isSubmitting ? "正在重新启动…" : task?.qualityAssessment?.status === "failed" ? "重新提取整段深度视频" : "从失败阶段重试"}</button></div>
      )}
      {task?.status === "completed" && sourceIsCurrent && isDesktop && canStart && currentVideo && (
        <div className="depth-capture-actions"><OutputResolutionSelector value={outputResolution} source={currentVideo} onChange={setOutputResolution} /><DeviceSelector value={devicePreference} onChange={setDevicePreference} /><button className="secondary-action depth-capture-retry" type="button" disabled={isSubmitting} onClick={() => void submit()}>{isSubmitting ? <LoaderCircle className="loading-spinner" aria-hidden="true" size={17} /> : <RotateCcw aria-hidden="true" size={17} />}{isSubmitting ? "正在重新启动…" : "重新提取整段深度视频"}</button></div>
      )}
      {task?.status === "completed" && sourceIsCurrent && task.qualityAssessment?.status === "review_required" && !task.reviewConfirmedAt && isDesktop && (
        <button className="primary-action" type="button" disabled={isSubmitting} onClick={() => void confirmReview()}>{isSubmitting && <LoaderCircle className="loading-spinner" aria-hidden="true" size={17} />}{isSubmitting ? "正在确认…" : "我已检查，继续实验性生成"}</button>
      )}
      {task?.status === "completed" && sourceIsCurrent && (task.qualityAssessment?.status === "passed" || task.reviewConfirmedAt) && (
        <p className="depth-capture-ready" role="status">{task.reviewConfirmedAt ? "已确认实验性复核；深度素材可用于深度控制工作流。" : "深度素材可用于深度控制工作流。"}</p>
      )}
      {task?.status === "completed" && sourceIsCurrent && isDesktop && (
        <div className="depth-capture-export">
          <a className="secondary-action" href={depthVideoUrl(project.id, task.id, true)} download>下载灰度深度视频</a>
          <a className="secondary-action" href={`/api/projects/${encodeURIComponent(project.id)}/depth-captures/${encodeURIComponent(task.id)}/package`} download>下载完整深度素材包</a>
          <p>下载完整灰度控制视频；素材包同时包含彩色预览、质量报告和版本记录。两种下载都保留整段时间线。</p>
        </div>
      )}
      {submitError && <p className="depth-capture-error" role="alert"><CircleAlert aria-hidden="true" size={17} />{submitError}</p>}
      {refreshError && canPoll && <p className="depth-capture-refresh-error">暂时无法刷新状态：{refreshError}</p>}
    </section>
  );
}

function TaskDetails({ task }: { task: DepthCapture }) {
  const stages = new Map(task.stages.map((stage) => [stage.name, stage]));
  const checks = new Map((task.qualityAssessment?.checks ?? []).map((check) => [check.criterion, check]));
  return (
    <div className={`depth-capture-task depth-capture-task--${task.status}`} role="status" aria-live="polite" aria-label={taskTitle(task)}>
      <div className="depth-capture-task-head">
        <h3>{taskTitle(task)}</h3>
        <span>请求设备：{task.devicePreference.toUpperCase()}</span>
        {task.outputResolution && <span>请求清晰度：短边 {task.outputResolution.toUpperCase()}</span>}
        {task.executionDevice && <span>最终设备：{task.executionDevice.toUpperCase()}</span>}
      </div>
      {task.outputSummary && <p className="depth-capture-output">实际输出：{task.outputSummary.width} × {task.outputSummary.height} · {task.outputSummary.durationSeconds.toFixed(2)} 秒</p>}
      {task.executionDevice === "cpu" && <p className="depth-capture-cpu-warning">CPU 慢速路径：此设备上的深度估计可能需要较长时间。</p>}
      <ol className="depth-capture-stages" aria-label="深度捕捉阶段">
        {stageNames.map((name) => {
          const stage = stages.get(name) ?? { name, status: "pending" as const };
          return <li key={name} className={`depth-capture-stage depth-capture-stage--${stage.status}`}><StageIcon status={stage.status} /><span>{stageLabels[name]}</span><span>{stageStateText(stage.status)}</span></li>;
        })}
      </ol>
      {task.status === "completed" && task.qualityAssessment && (
        <div className="depth-quality-ledger">
          <h3>六项质量检查</h3>
          <ul aria-label="深度质量检查">
            {qualityCriteria.map((criterion) => {
              const check = checks.get(criterion);
              const status = check?.status ?? "failed";
              return <li key={criterion} className={`depth-quality-check depth-quality-check--${status}`}><strong>{checkLabels[criterion]}</strong><span className="depth-quality-status">{qualityStatusText(status)}</span><span>{check?.message ?? "检查数据不可用"}</span><small>{check?.evidence ?? "无法读取此项质量证据"}</small></li>;
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
