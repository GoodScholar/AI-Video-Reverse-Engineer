import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, LoaderCircle, RotateCcw, Sparkles } from "lucide-react";

import type { Project } from "./models";
import { getVideoUpscaleState, startVideoUpscale, upscaleVideoUrl, type UpscaleRun, type UpscaleResolution, type VideoUpscaleState } from "./videoUpscaleApi";
import "./videoUpscale.css";

const POLL_INTERVAL_MS = 2_000;
const MAX_OUTPUT_LONG_EDGE = 7_680;
const MAX_OUTPUT_SHORT_EDGE = 4_320;

const stageLabels: Record<string, string> = {
  queued: "排队中",
  preparing: "准备素材",
  upscaling: "Real-ESRGAN 超分",
  encoding: "编码输出",
  completed: "已完成",
};

function active(run: UpscaleRun) {
  return run.status === "queued" || run.status === "running";
}

function sourceVideo(project: Project) {
  return project.referenceMedia?.type === "video" ? project.referenceMedia : null;
}

function statusLabel(run: UpscaleRun) {
  if (run.status === "queued") return "排队中";
  if (run.status === "running") return "超分中";
  if (run.status === "completed") return "已完成";
  return "失败";
}

function outputText(output: NonNullable<UpscaleRun["output"]>) {
  const duration = Number.isInteger(output.durationSeconds)
    ? String(output.durationSeconds)
    : output.durationSeconds.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return `${output.width}×${output.height} · ${output.frameRate} fps · ${duration} 秒`;
}

function resolutionLabel(resolution: UpscaleResolution) {
  return resolution === "1080p" ? "1080P" : "2K";
}

function runLabel(run: UpscaleRun) {
  return run.outputResolution ? resolutionLabel(run.outputResolution) : `${run.scale}×`;
}

function targetSize(video: NonNullable<ReturnType<typeof sourceVideo>>, resolution: UpscaleResolution) {
  const shortSide = resolution === "1080p" ? 1080 : 1440;
  const ratio = shortSide / Math.min(video.width, video.height);
  return { width: Math.round(video.width * ratio / 2) * 2, height: Math.round(video.height * ratio / 2) * 2 };
}

function targetUnavailable(video: NonNullable<ReturnType<typeof sourceVideo>>, resolution: UpscaleResolution) {
  if (Math.min(video.width, video.height) >= (resolution === "1080p" ? 1080 : 1440)) return "原片已达到此清晰度";
  const size = targetSize(video, resolution);
  if (Math.max(size.width, size.height) > MAX_OUTPUT_LONG_EDGE || Math.min(size.width, size.height) > MAX_OUTPUT_SHORT_EDGE) return "超过最大输出尺寸";
  return "";
}

function replaceRun(runs: UpscaleRun[], next: UpscaleRun) {
  const previous = runs.findIndex((run) => run.id === next.id);
  if (previous < 0) return [next, ...runs];
  return runs.map((run) => run.id === next.id ? next : run);
}

export function VideoUpscalePanel({ project }: { project: Project }) {
  const video = sourceVideo(project);
  const sourceId = video?.id ?? null;
  const [state, setState] = useState<VideoUpscaleState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [submittingResolution, setSubmittingResolution] = useState<UpscaleResolution | null>(null);
  const [reloadRevision, setReloadRevision] = useState(0);
  const requestGenerationRef = useRef(0);

  useEffect(() => {
    const generation = ++requestGenerationRef.current;
    setState(null);
    setLoadError("");
    setSubmitError("");
    setSubmittingResolution(null);
    void getVideoUpscaleState(project.id).then(
      (next) => {
        if (requestGenerationRef.current !== generation) return;
        setState(next);
      },
      (error: unknown) => {
        if (requestGenerationRef.current !== generation) return;
        setLoadError(error instanceof Error ? error.message : "无法读取本地视频超分状态，请重试。");
      },
    );
    return () => { requestGenerationRef.current += 1; };
  }, [project.id, reloadRevision, sourceId]);

  useEffect(() => {
    if (!state?.runs.some(active)) return undefined;
    const generation = requestGenerationRef.current;
    let disposed = false;
    let timer: number | undefined;
    const schedule = () => {
      timer = window.setTimeout(() => { void refresh(); }, POLL_INTERVAL_MS);
    };
    const refresh = async () => {
      try {
        const next = await getVideoUpscaleState(project.id);
        if (disposed || requestGenerationRef.current !== generation) return;
        setState(next);
        setLoadError("");
      } catch (error) {
        if (disposed || requestGenerationRef.current !== generation) return;
        setLoadError(error instanceof Error ? error.message : "无法刷新本地视频超分状态，请重试。");
      } finally {
        if (!disposed && requestGenerationRef.current === generation) schedule();
      }
    };
    schedule();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [project.id, sourceId, state]);

  const visibleRuns = state?.runs.filter((run) => run.sourceId === sourceId) ?? [];
  const environmentReady = state?.environment.available === true;
  const projectHasActiveRun = state?.runs.some(active) ?? false;

  async function start(resolution: UpscaleResolution) {
    if (!sourceId || !video || !environmentReady || projectHasActiveRun || targetUnavailable(video, resolution) || submittingResolution !== null) return;
    const generation = requestGenerationRef.current;
    setSubmittingResolution(resolution);
    setSubmitError("");
    try {
      const run = await startVideoUpscale(project.id, sourceId, resolution);
      if (requestGenerationRef.current !== generation) return;
      setState((previous) => previous && {
        ...previous,
        runs: replaceRun(previous.runs, run),
      });
    } catch (error) {
      if (requestGenerationRef.current !== generation) return;
      setSubmitError(error instanceof Error ? error.message : "无法启动本地视频超分，请重试。");
    } finally {
      if (requestGenerationRef.current === generation) setSubmittingResolution(null);
    }
  }

  return (
    <section className="video-upscale-panel" aria-labelledby="video-upscale-title">
      <div className="video-upscale-heading">
        <div>
          <p className="video-upscale-kicker">LOCAL REAL-ESRGAN</p>
          <h2 id="video-upscale-title">视频超分</h2>
        </div>
        {state && <p className={`video-upscale-environment ${environmentReady ? "video-upscale-environment--ready" : "video-upscale-environment--unavailable"}`} role="status">
          {environmentReady ? <Check aria-hidden="true" size={16} /> : <CircleAlert aria-hidden="true" size={16} />}
          {state.environment.message}
        </p>}
        {state && !environmentReady && <button type="button" className="secondary-action video-upscale-refresh" onClick={() => setReloadRevision((revision) => revision + 1)}>重新读取状态</button>}
      </div>

      {!state && !loadError && <p className="video-upscale-loading" role="status"><LoaderCircle className="loading-spinner" aria-hidden="true" size={16} />正在读取本地 Real-ESRGAN 环境</p>}
      {loadError && <div className="video-upscale-error" role="alert">
        <span>{loadError}</span>
        <button type="button" className="secondary-action" onClick={() => setReloadRevision((revision) => revision + 1)}>重新读取状态</button>
      </div>}
      {!video && <p className="video-upscale-readonly">仅参考视频可执行 Real-ESRGAN 超分。请先上传或选择一个参考视频。</p>}

      <div className="video-upscale-actions">
        <div>
          <p className="video-upscale-source">{video ? `当前参考：${video.originalName}` : "当前没有参考视频"}</p>
          <p className="video-upscale-note">保持画面比例和音轨，1080P 短边 1080 像素，2K（1440P）短边 1440 像素。</p>
        </div>
        <div className="video-upscale-buttons">
          {(["1080p", "2k"] as const).map((resolution) => {
            const size = video ? targetSize(video, resolution) : null;
            const unavailable = video ? targetUnavailable(video, resolution) : "";
            return <div key={resolution} className="video-upscale-option">
              <button
                type="button"
                className={resolution === "1080p" ? "primary-action" : "secondary-action"}
                disabled={!video || !environmentReady || projectHasActiveRun || Boolean(unavailable) || submittingResolution !== null}
                onClick={() => void start(resolution)}
              >
                {submittingResolution === resolution ? <LoaderCircle className="loading-spinner" aria-hidden="true" size={16} /> : <Sparkles aria-hidden="true" size={16} />}
                开始 {resolutionLabel(resolution)} 超分
              </button>
              {size && <span className={unavailable ? "video-upscale-target video-upscale-target--exceeded" : "video-upscale-target"}>
                {resolutionLabel(resolution)} 目标：{size.width}×{size.height}{unavailable && `，${unavailable}`}
              </span>}
            </div>;
          })}
        </div>
      </div>
      {video && Math.min(video.width, video.height) < 360 && <p className="video-upscale-note">原片分辨率较低，部分目标超过模型原生 4 倍范围；额外放大不会增加模型生成的细节。</p>}
      {projectHasActiveRun && <p className="video-upscale-waiting" role="status">当前项目仍有视频超分任务在后台处理中，完成后才能开始或重试。</p>}
      {submitError && <p className="video-upscale-error" role="alert">{submitError}</p>}

      {visibleRuns.length > 0 && <ul className="video-upscale-runs" aria-label="当前参考的视频超分任务">
        {visibleRuns.map((run) => (
          <li key={run.id} className={`video-upscale-run video-upscale-run--${run.status}`}>
            <div className="video-upscale-run-header">
              <strong>{runLabel(run)} Real-ESRGAN · {statusLabel(run)}</strong>
              <span>{Math.round(run.progress)}%</span>
            </div>
            <p>{stageLabels[run.stage] ?? run.stage}</p>
            {active(run) && <progress aria-label={`${runLabel(run)} 超分进度`} max="100" value={Math.max(0, Math.min(100, run.progress))} />}
            {run.status === "failed" && <div className="video-upscale-failure">
              <p>{run.error ?? "本地超分任务失败，请重试。"}</p>
              <button type="button" className="secondary-action" disabled={!video || !environmentReady || projectHasActiveRun || submittingResolution !== null || Boolean(video && targetUnavailable(video, run.outputResolution ?? "1080p"))} onClick={() => void start(run.outputResolution ?? "1080p")}>
                <RotateCcw aria-hidden="true" size={16} />重试 {resolutionLabel(run.outputResolution ?? "1080p")} 超分
              </button>
            </div>}
            {run.status === "completed" && run.output && <div className="video-upscale-output">
              <p>{outputText(run.output)}</p>
              <video controls preload="metadata" aria-label={`${runLabel(run)} 超分视频预览`} src={upscaleVideoUrl(project.id, run.id)} />
              <a className="secondary-action" href={upscaleVideoUrl(project.id, run.id, true)}>下载超分视频</a>
            </div>}
          </li>
        ))}
      </ul>}
    </section>
  );
}
