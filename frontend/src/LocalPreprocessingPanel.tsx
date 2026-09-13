import { useEffect, useRef, useState } from "react";
import {
  Check, CircleAlert, Clock3, LoaderCircle, Play, RefreshCw, RotateCcw,
} from "lucide-react";

import type {
  ImageLocalPreprocessing,
  ImagePreprocessingStageName,
  LocalPreprocessing,
  PreprocessingStageName,
  Project,
  VideoLocalPreprocessing,
  VideoPreprocessingStageName,
} from "./models";
import { getProject, startLocalPreprocessing } from "./localPreprocessingApi";

const DESKTOP_QUERY = "(min-width: 1024px)";
const DEFAULT_POLL_INTERVAL_MS = 1_000;

const videoStageLabels = {
  decoding: "解码",
  sceneDetection: "镜头检测",
  keyframeExtraction: "关键帧提取",
  motionAnalysis: "运动分析",
  reproducibilityAssessment: "初步可复刻性判断",
} satisfies Record<VideoPreprocessingStageName, string>;

const imageStageLabels = {
  imageDecoding: "图像解码",
  imageNormalization: "方向与色彩标准化",
  proxyGeneration: "分析代理生成",
  reproducibilityAssessment: "初步可复刻性判断",
} satisfies Record<ImagePreprocessingStageName, string>;

const videoStageOrder = Object.keys(videoStageLabels) as VideoPreprocessingStageName[];
const imageStageOrder = Object.keys(imageStageLabels) as ImagePreprocessingStageName[];

type Props = {
  project: Project;
  onProjectUpdated: (project: Project) => void;
  start?: typeof startLocalPreprocessing;
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

function taskTitle(task: LocalPreprocessing) {
  if (task.status === "queued") return "本地预处理正在排队";
  if (task.status === "running") return "本地预处理正在运行";
  if (task.status === "completed") return "本地预处理已完成";
  return "本地预处理失败";
}

function stageStateText(status: LocalPreprocessing["stages"][number]["status"]) {
  if (status === "completed") return "已完成";
  if (status === "running") return "进行中";
  if (status === "failed") return "失败";
  return "等待中";
}

function StageIcon({ status }: { status: LocalPreprocessing["stages"][number]["status"] }) {
  if (status === "completed") return <Check aria-hidden="true" size={16} />;
  if (status === "running") return <LoaderCircle aria-hidden="true" size={16} />;
  if (status === "failed") return <CircleAlert aria-hidden="true" size={16} />;
  return <Clock3 aria-hidden="true" size={16} />;
}

function motionText(summary: NonNullable<VideoLocalPreprocessing["proxySummary"]>) {
  if (summary.motionLevel === "unavailable") return "运动强度无法独立评估";
  const level = { light: "低", moderate: "中度", high: "高" }[summary.motionLevel];
  const p90 = summary.motionP90 === null ? "无法读取" : summary.motionP90.toFixed(3);
  return `${level}运动 · P90 ${p90}`;
}

function VideoPreprocessingConclusion({ task }: { task: VideoLocalPreprocessing }) {
  const summary = task.proxySummary;
  const assessment = task.reproducibilityAssessment;
  if (!summary || !assessment) return null;
  const isOutOfScope = assessment.status === "out_of_scope";

  return (
    <div className={`local-preprocessing-conclusion ${isOutOfScope ? "local-preprocessing-conclusion--out-of-scope" : ""}`}>
      <h3>本地预处理摘要</h3>
      <ul className="local-preprocessing-summary" aria-label="本地预处理摘要">
        <li>{summary.keyframeCount} 张关键帧</li>
        <li>{summary.sceneChangeCount + 1} 个镜头</li>
        <li>{motionText(summary)}</li>
      </ul>
      <div className="local-preprocessing-assessment" aria-label={`初步结论：${isOutOfScope ? "超出当前可复刻范围" : "待语义分析确认"}`}>
        <h3>{isOutOfScope ? "超出当前可复刻范围" : "待语义分析确认"}</h3>
        {assessment.checks.map((check) => (
          <p key={check.criterion} className={`local-preprocessing-check local-preprocessing-check--${check.status}`}>
            <strong>{check.message}</strong>
            <span>{check.evidence}</span>
          </p>
        ))}
        {isOutOfScope && <p>后续仍可生成分析报告，但不承诺生成可靠的可执行工作流</p>}
      </div>
    </div>
  );
}

function ImagePreprocessingConclusion({ task }: { task: ImageLocalPreprocessing }) {
  if (!task.proxySummary || !task.reproducibilityAssessment) return null;
  const summary = task.proxySummary;

  return (
    <div className="local-preprocessing-conclusion local-preprocessing-conclusion--image">
      <h3>本地预处理摘要</h3>
      <ul className="local-preprocessing-summary" aria-label="本地预处理摘要">
        <li>代理尺寸：{summary.proxySize.width}×{summary.proxySize.height}</li>
        <li>分析代理仅用于后续语义分析，不上传原始参考图片。</li>
        {summary.transparencyFlattened && <li>透明区域已使用白色背景处理。</li>}
      </ul>
      <div className="local-preprocessing-assessment" aria-label="初步结论：待语义分析确认">
        <h3>待语义分析确认</h3>
        <p className="local-preprocessing-image-note">本地预处理未执行主体或交互的语义判断。</p>
      </div>
    </div>
  );
}

function stageLabel(name: PreprocessingStageName) {
  return name in imageStageLabels
    ? imageStageLabels[name as ImagePreprocessingStageName]
    : videoStageLabels[name as VideoPreprocessingStageName];
}

export function LocalPreprocessingPanel({
  project,
  onProjectUpdated,
  start = startLocalPreprocessing,
  load = getProject,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}: Props) {
  const isDesktop = useDesktop();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const task = project.localPreprocessing;
  const canPoll = task?.status === "queued" || task?.status === "running";
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const currentProjectIdRef = useRef(project.id);
  const callbackRef = useRef(onProjectUpdated);
  const manualRefreshRef = useRef<(() => void) | null>(null);
  const statusHeadingRef = useRef<HTMLHeadingElement>(null);
  const startButtonRef = useRef<HTMLButtonElement>(null);
  const retryButtonRef = useRef<HTMLButtonElement>(null);
  const focusStatusForProjectRef = useRef<string | null>(null);
  const previousTaskRef = useRef<{ projectId: string; status: LocalPreprocessing["status"] | null }>({
    projectId: project.id,
    status: task?.status ?? null,
  });
  currentProjectIdRef.current = project.id;
  callbackRef.current = onProjectUpdated;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
    };
  }, []);

  useEffect(() => {
    requestGenerationRef.current += 1;
    setIsSubmitting(false);
    setSubmitError("");
    setRefreshError("");
  }, [project.id]);

  useEffect(() => {
    if (focusStatusForProjectRef.current === project.id && task) {
      statusHeadingRef.current?.focus();
      focusStatusForProjectRef.current = null;
    }
  }, [project.id, task?.status]);

  useEffect(() => {
    if (submitError) startButtonRef.current?.focus();
  }, [submitError]);

  useEffect(() => {
    const previousTask = previousTaskRef.current;
    const sameProject = previousTask.projectId === project.id;
    const completedFromActiveTask = sameProject
      && (previousTask.status === "queued" || previousTask.status === "running")
      && task?.status === "completed";
    if (completedFromActiveTask) {
      statusHeadingRef.current?.focus();
    } else if (sameProject && task?.status === "failed" && previousTask.status !== "failed" && isDesktop) {
      retryButtonRef.current?.focus();
    }
    previousTaskRef.current = { projectId: project.id, status: task?.status ?? null };
  }, [isDesktop, project.id, task?.status]);

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
    const cancelScheduledRefresh = () => {
      if (timer === undefined) return;
      clearTimeout(timer);
      timer = undefined;
    };
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
        if (!isCurrent()) return;
        if (updated.localPreprocessing?.status === "queued" || updated.localPreprocessing?.status === "running") schedule();
      } catch (error) {
        if (!isCurrent()) return;
        setRefreshError(messageFor(error, "暂时无法刷新状态"));
        schedule();
      } finally {
        inFlight = false;
      }
    };
    const refreshNow = () => {
      cancelScheduledRefresh();
      void refresh();
    };
    manualRefreshRef.current = refreshNow;
    schedule();
    return () => {
      active = false;
      requestGenerationRef.current += 1;
      cancelScheduledRefresh();
      if (manualRefreshRef.current === refreshNow) manualRefreshRef.current = null;
    };
  }, [canPoll, load, pollIntervalMs, project.id, task?.status]);

  async function submit() {
    if (!isDesktop || !project.referenceMedia || isSubmitting) return;
    const generation = ++requestGenerationRef.current;
    const projectId = project.id;
    const isCurrent = () => (
      mountedRef.current
      && requestGenerationRef.current === generation
      && currentProjectIdRef.current === projectId
    );
    setIsSubmitting(true);
    setSubmitError("");
    setRefreshError("");
    try {
      const updated = await start(projectId);
      if (!isCurrent()) return;
      focusStatusForProjectRef.current = projectId;
      callbackRef.current(updated);
      if (!isCurrent()) return;
    } catch (error) {
      if (!isCurrent()) return;
      setSubmitError(messageFor(error, "无法启动本地预处理，请重试。"));
    } finally {
      if (isCurrent()) setIsSubmitting(false);
    }
  }

  function manuallyRefresh() {
    manualRefreshRef.current?.();
  }

  return (
    <section className={`local-preprocessing-panel${isDesktop ? "" : " local-preprocessing-panel--readonly"}`} aria-labelledby="local-preprocessing-title">
      <h2 id="local-preprocessing-title">本地预处理</h2>
      {!task && isDesktop && (
        <div className="local-preprocessing-not-started">
          {project.referenceMedia ? (
            <>
              <p>{project.referenceMedia.type === "image"
                ? "此步骤只在本机处理，将生成方向与色彩标准化后的分析代理。"
                : "此步骤只在本机处理，尚不会发送分析代理。"}</p>
              <button ref={startButtonRef} className="primary-action" type="button" disabled={isSubmitting} onClick={() => void submit()}>
                <Play aria-hidden="true" size={17} />{isSubmitting ? "正在启动本地预处理…" : "开始本地预处理"}
              </button>
            </>
          ) : <p>请先添加并校验参考素材，再开始本地预处理。</p>}
      </div>
      )}
      {!task && !isDesktop && <p>{project.referenceMedia
        ? "请在宽度至少 1024px 的桌面设备开始或重试本地预处理"
        : "请先添加并校验参考素材，再开始本地预处理。"}</p>}
      {task && <TaskContent task={task} headingRef={statusHeadingRef} />}
      {task?.status === "failed" && task.error && (
        <div className="local-preprocessing-error" role="alert">
          <CircleAlert aria-hidden="true" size={17} />
          <span><strong>{stageLabel(task.error.stage)}失败</strong>{task.error.message}</span>
        </div>
      )}
      {task?.status === "failed" && isDesktop && (
        <button ref={retryButtonRef} className="secondary-action" type="button" disabled={isSubmitting} onClick={() => void submit()}>
          <RotateCcw aria-hidden="true" size={17} />{isSubmitting ? "正在重新启动…" : "从失败阶段重试"}
        </button>
      )}
      {submitError && <p className="local-preprocessing-error" role="alert"><CircleAlert aria-hidden="true" size={17} />{submitError}</p>}
      {refreshError && task && canPoll && (
        <p className="local-preprocessing-refresh-error">
          暂时无法刷新状态：{refreshError}
          <button className="secondary-action" type="button" onClick={() => void manuallyRefresh()}><RefreshCw aria-hidden="true" size={16} />重新读取</button>
        </p>
      )}
    </section>
  );
}

function TaskContent({ task, headingRef }: { task: LocalPreprocessing; headingRef: React.RefObject<HTMLHeadingElement> }) {
  const states = new Map(task.stages.map((stage) => [stage.name, stage]));
  const statusText = task.status === "completed"
    ? `本地预处理已完成；初步结论：${task.reproducibilityAssessment?.status === "out_of_scope" ? "超出当前可复刻范围" : "待语义分析确认"}`
    : taskTitle(task);

  return (
    <div className={`local-preprocessing-task local-preprocessing-task--${task.status}`} role="status" aria-live="polite" aria-label={statusText}>
      <h3 ref={headingRef} tabIndex={-1}>{taskTitle(task)}</h3>
      <ol className="local-preprocessing-stages" aria-label="本地预处理阶段">
        {(task.mediaType === "image" ? imageStageOrder : videoStageOrder).map((name) => {
          const stage = states.get(name) ?? { name, status: "pending" as const, startedAt: null, completedAt: null };
          return (
            <li key={name} className={`local-preprocessing-stage local-preprocessing-stage--${stage.status}`}>
              <StageIcon status={stage.status} />
              <span>{stageLabel(name)}</span>
              <span>{stageStateText(stage.status)}</span>
            </li>
          );
        })}
      </ol>
      {task.status === "completed" && task.mediaType === "image" && (
        <ImagePreprocessingConclusion task={task} />
      )}
      {task.status === "completed" && task.mediaType === "video" && <VideoPreprocessingConclusion task={task} />}
    </div>
  );
}
