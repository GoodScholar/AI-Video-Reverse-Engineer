import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, Clock3, LoaderCircle, Play, RotateCcw } from "lucide-react";

import type { DepthCapture, DepthCaptureStageName, DepthDevicePreference, DepthQualityCriterion, Project } from "./models";
import { confirmDepthReview, depthPreviewUrl, referenceVideoContentUrl, startDepthCapture } from "./depthCaptureApi";
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
  if (status === "running") return <LoaderCircle aria-hidden="true" size={16} />;
  if (status === "failed") return <CircleAlert aria-hidden="true" size={16} />;
  return <Clock3 aria-hidden="true" size={16} />;
}

function mostRecent(captures: DepthCapture[]) {
  return [...captures].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0] ?? null;
}

function isUnresolved(capture: DepthCapture) {
  return capture.status === "failed"
    || capture.qualityAssessment?.status === "failed"
    || (capture.qualityAssessment?.status === "review_required" && !capture.reviewConfirmedAt);
}

function currentReferenceVideo(project: Project) {
  return project.referenceMedia?.type === "video" ? project.referenceMedia : null;
}

function selectDisplayCapture(project: Project): DepthCapture | null {
  const video = currentReferenceVideo(project);
  const captures = (project.depthCaptures ?? []).filter((capture) => capture.sourceReferenceVideoId === video?.id);
  const active = captures.filter((capture) => isActive(capture.status));
  return mostRecent(active)
    ?? mostRecent(captures.filter(isUnresolved))
    ?? captures.find((capture) => capture.id === project.activeDepthCaptureId)
    ?? mostRecent(captures);
}

function canStartDepthCapture(project: Project) {
  const video = currentReferenceVideo(project);
  const preprocessing = project.localPreprocessing;
  return Boolean(
    video
    && preprocessing?.status === "completed"
    && preprocessing.sourceReferenceMediaId === video.id
    && preprocessing.mediaType === "video"
    && preprocessing.reproducibilityAssessment?.status === "pending_semantic_confirmation",
  );
}

function synchroniseDepth(reference: HTMLVideoElement, depth: HTMLVideoElement | null) {
  if (!depth || !Number.isFinite(reference.currentTime) || !Number.isFinite(depth.currentTime)) return;
  if (Math.abs(reference.currentTime - depth.currentTime) > DRIFT_TOLERANCE_SECONDS + Number.EPSILON * 8) depth.currentTime = reference.currentTime;
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

  function mirrorPlay() {
    const reference = referenceRef.current;
    const depth = depthRef.current;
    if (!reference || !depth) return;
    synchroniseDepth(reference, depth);
    void depth.play().catch(() => undefined);
  }

  function mirrorPause() {
    depthRef.current?.pause();
  }

  function correctDrift() {
    const reference = referenceRef.current;
    if (reference) synchroniseDepth(reference, depthRef.current);
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
      const updated = await start(projectId, devicePreference);
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

  if (!project.localPreprocessing) return null;

  return (
    <section className={`depth-capture-panel${isDesktop ? "" : " depth-capture-panel--readonly"}`} aria-labelledby="depth-capture-title">
      <div className="depth-capture-heading">
        <div>
          <p className="depth-capture-kicker">本地控制素材</p>
          <h2 id="depth-capture-title">深度捕捉审查</h2>
        </div>
        {!isDesktop && <p className="depth-capture-readonly">移动端仅查看</p>}
      </div>

      {currentVideo && (
        <div className="depth-preview-grid">
          <figure>
            <figcaption>参考视频 · 时间轴基准</figcaption>
            <video ref={referenceRef} aria-label="参考视频预览" controls preload="metadata" src={referenceVideoContentUrl(project.id)} onPlay={mirrorPlay} onPause={mirrorPause} onSeeking={correctDrift} onTimeUpdate={correctDrift} />
          </figure>
          <figure>
            <figcaption>相对深度控制素材</figcaption>
            {task?.status === "completed" && sourceIsCurrent ? (
              <video ref={depthRef} aria-label="深度控制预览" preload="metadata" src={depthPreviewUrl(project.id, task.id)} />
            ) : <div className="depth-preview-placeholder" aria-label="深度控制预览">{task ? "深度预览将在编码完成后显示" : "尚未生成深度预览"}</div>}
          </figure>
        </div>
      )}

      {!task && (
        <div className="depth-capture-empty">
          {canStart ? <p>参考素材与本地预处理已就绪，可在本机生成相对深度控制素材。</p> : <p>请先完成当前参考视频的本地预处理，并确认它仍在可复刻范围内。</p>}
          {isDesktop && canStart && <div className="depth-capture-actions"><DeviceSelector value={devicePreference} onChange={setDevicePreference} /><button className="primary-action" type="button" disabled={isSubmitting} onClick={() => void submit()}><Play aria-hidden="true" size={17} />{isSubmitting ? "正在启动深度捕捉…" : "开始本地深度捕捉"}</button></div>}
        </div>
      )}

      {task && !sourceIsCurrent && <p className="depth-capture-stale">该深度素材属于已替换的参考视频，不能用于当前工作流。</p>}
      {task && sourceIsCurrent && <TaskDetails task={task} />}

      {task?.status === "failed" && sourceIsCurrent && task.error && (
        <div className="depth-capture-error" role="alert"><CircleAlert aria-hidden="true" size={17} /><span><strong>{stageLabels[task.error.stage]}失败</strong>{task.error.message}</span></div>
      )}
      {(task?.status === "failed" || task?.qualityAssessment?.status === "failed") && sourceIsCurrent && isDesktop && canStart && (
        <div className="depth-capture-actions"><DeviceSelector value={devicePreference} onChange={setDevicePreference} /><button className="secondary-action depth-capture-retry" type="button" disabled={isSubmitting} onClick={() => void submit()}><RotateCcw aria-hidden="true" size={17} />{isSubmitting ? "正在重新启动…" : task?.qualityAssessment?.status === "failed" ? "重新生成深度素材" : "从失败阶段重试"}</button></div>
      )}
      {task?.status === "completed" && sourceIsCurrent && task.qualityAssessment?.status === "review_required" && !task.reviewConfirmedAt && isDesktop && (
        <button className="primary-action" type="button" disabled={isSubmitting} onClick={() => void confirmReview()}>{isSubmitting ? "正在确认…" : "我已检查，继续实验性生成"}</button>
      )}
      {task?.status === "completed" && sourceIsCurrent && (task.qualityAssessment?.status === "passed" || task.reviewConfirmedAt) && (
        <p className="depth-capture-ready" role="status">{task.reviewConfirmedAt ? "已确认实验性复核；深度素材可用于深度控制工作流。" : "深度素材可用于深度控制工作流。"}</p>
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
        {task.executionDevice && <span>最终设备：{task.executionDevice.toUpperCase()}</span>}
      </div>
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
