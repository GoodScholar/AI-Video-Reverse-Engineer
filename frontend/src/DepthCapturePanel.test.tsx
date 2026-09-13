import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { DepthCapturePanel } from "./DepthCapturePanel";
import type { DepthCapture, DepthCaptureStageName, Project, VideoLocalPreprocessing } from "./models";

function stubDesktop(matches = true) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  let currentMatches = matches;
  const mediaQueryList = {
    get matches() { return currentMatches; }, media: "", onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  };
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => {
    mediaQueryList.media = query;
    return mediaQueryList;
  }));
  return {
    setMatches(next: boolean) {
      currentMatches = next;
      listeners.forEach((listener) => listener({ matches: next, media: mediaQueryList.media } as MediaQueryListEvent));
    },
  };
}

const stageNames: DepthCaptureStageName[] = ["preparing", "estimatingDepth", "encoding", "qualityAssessment"];
const checkNames = ["完整性", "动态范围", "时间闪烁", "近远方向稳定性", "边缘连续性", "时间线对齐"];

function capture(overrides: Partial<DepthCapture> = {}): DepthCapture {
  return {
    id: "depth-001", sourceReferenceVideoId: "video-001", algorithmVersion: 1,
    status: "completed", devicePreference: "auto", executionDevice: "mps",
    modelIdentity: {
      modelId: "video-depth-anything-small-relative",
      upstreamCommit: "4f5ae23172ba60fd7bc11ef671cca678842c7072",
      checkpointSha256: "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
    },
    normalizationDirection: "near_white_far_black", currentStage: null,
    stages: stageNames.map((name) => ({ name, status: "completed", startedAt: null, completedAt: null })) as DepthCapture["stages"],
    outputSummary: { width: 640, height: 360, frameRate: 8, frameCount: 20, durationSeconds: 2.5 },
    qualityAssessment: {
      status: "passed", thresholdVersion: 1,
      checks: ["completeness", "dynamicRange", "temporalFlicker", "directionStability", "edgeContinuity", "timelineAlignment"].map((criterion) => ({
        criterion, status: "passed", message: "通过", evidence: "固定样本", metric: 0.1, threshold: 0.2, sampleTimestamps: [0, 1],
      })) as NonNullable<DepthCapture["qualityAssessment"]>["checks"],
    },
    reviewConfirmedAt: null, error: null,
    queuedAt: "2026-09-12T10:00:00+00:00", startedAt: "2026-09-12T10:00:01+00:00",
    updatedAt: "2026-09-12T10:01:00+00:00", completedAt: "2026-09-12T10:01:00+00:00",
    ...overrides,
  };
}

function project(depthCaptures: DepthCapture[] = [capture()]): Project {
  return {
    id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-12T10:00:00+00:00", updatedAt: "2026-09-12T10:01:00+00:00",
    referenceMedia: { type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4", sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 },
    localPreprocessing: {
      id: "pre-001", sourceReferenceMediaId: "video-001", mediaType: "video", algorithmVersion: 1, status: "completed", currentStage: null, stages: [],
      queuedAt: "2026-09-12T10:00:00+00:00", startedAt: null, updatedAt: "2026-09-12T10:01:00+00:00", completedAt: "2026-09-12T10:01:00+00:00",
      proxySummary: null, reproducibilityAssessment: { status: "pending_semantic_confirmation", checks: [] }, error: null,
    },
    depthCaptures, activeDepthCaptureId: depthCaptures[0]?.id ?? null,
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("以参考素材为准，仅在漂移超过 0.08 秒时校正深度预览", () => {
  stubDesktop();
  render(<DepthCapturePanel project={project()} onProjectUpdated={vi.fn()} />);
  const reference = screen.getByLabelText("参考视频预览") as HTMLVideoElement;
  const depth = screen.getByLabelText("深度控制预览") as HTMLVideoElement;
  Object.defineProperty(reference, "currentTime", { configurable: true, writable: true, value: 1.25 });
  Object.defineProperty(depth, "currentTime", { configurable: true, writable: true, value: 1.18 });
  fireEvent.timeUpdate(reference);
  expect(depth.currentTime).toBeCloseTo(1.18, 2);

  Object.defineProperty(depth, "currentTime", { configurable: true, writable: true, value: 0.8 });
  fireEvent.seeking(reference);
  expect(depth.currentTime).toBeCloseTo(1.25, 2);

  Object.defineProperty(reference, "currentTime", { configurable: true, writable: true, value: 1.25 });
  Object.defineProperty(depth, "currentTime", { configurable: true, writable: true, value: 1.17 });
  fireEvent.timeUpdate(reference);
  expect(depth.currentTime).toBeCloseTo(1.17, 2);

  Object.defineProperty(depth, "currentTime", { configurable: true, writable: true, value: 1.169 });
  fireEvent.timeUpdate(reference);
  expect(depth.currentTime).toBeCloseTo(1.25, 2);
});

it("镜像参考素材的播放和暂停", () => {
  stubDesktop();
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  render(<DepthCapturePanel project={project()} onProjectUpdated={vi.fn()} />);
  const reference = screen.getByLabelText("参考视频预览");
  expect(screen.getByLabelText("深度控制预览")).not.toHaveAttribute("controls");
  fireEvent.play(reference);
  fireEvent.pause(reference);
  expect(play).toHaveBeenCalledTimes(1);
  expect(pause).toHaveBeenCalledTimes(1);
  play.mockRestore();
  pause.mockRestore();
});

it("呈现状态、最终设备、CPU 慢速提示与六项固定质量检查", () => {
  stubDesktop();
  const completed = capture();
  const checks = [...completed.qualityAssessment!.checks].reverse().map((check, index) => ({ ...check, status: index === 1 ? "review_required" as const : index === 2 ? "failed" as const : "passed" as const, message: "质量证据" })) as NonNullable<DepthCapture["qualityAssessment"]>["checks"];
  const cpu = capture({ executionDevice: "cpu", qualityAssessment: { ...completed.qualityAssessment!, checks } });
  render(<DepthCapturePanel project={project([cpu])} onProjectUpdated={vi.fn()} />);
  expect(screen.getByRole("status", { name: "本地深度捕捉已完成" })).toBeVisible();
  expect(screen.getByText("最终设备：CPU")).toBeVisible();
  expect(screen.getByText(/CPU 慢速路径/)).toBeVisible();
  expect(screen.getByRole("list", { name: "深度质量检查" }).querySelectorAll("li")).toHaveLength(6);
  for (const name of checkNames) expect(screen.getByText(name)).toBeVisible();
  expect(screen.getByRole("list", { name: "深度质量检查" }).textContent).toMatch(/完整性.*动态范围.*时间闪烁.*近远方向稳定性.*边缘连续性.*时间线对齐/);
  expect(screen.getAllByText("通过").length).toBeGreaterThan(0);
  expect(screen.getAllByText("需复核").length).toBeGreaterThan(0);
  expect(screen.getAllByText("失败").length).toBeGreaterThan(0);
});

it("优先展示当前参考下最新的未解决深度任务，而不是旧 active 捕捉", async () => {
  vi.useFakeTimers();
  stubDesktop();
  const oldActive = capture({ id: "depth-old", updatedAt: "2026-09-12T10:01:00+00:00" });
  const running = capture({ id: "depth-new", status: "running", currentStage: "estimatingDepth", qualityAssessment: null, completedAt: null, updatedAt: "2026-09-12T10:02:00+00:00" });
  const load = vi.fn().mockResolvedValue(project([oldActive, running]));
  render(<DepthCapturePanel project={{ ...project([oldActive, running]), activeDepthCaptureId: "depth-old" }} onProjectUpdated={vi.fn()} load={load} />);
  expect(screen.getByRole("status", { name: "本地深度捕捉正在运行" })).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(load).toHaveBeenCalledWith("project-001");
});

it("在当前参考下依次选择未解决失败、未确认复核与排队捕捉", () => {
  stubDesktop();
  const oldActive = capture({ id: "depth-old", updatedAt: "2026-09-12T10:01:00+00:00" });
  const failed = capture({ id: "depth-failed", status: "failed", qualityAssessment: null, updatedAt: "2026-09-12T10:02:00+00:00" });
  const review = capture({ id: "depth-review", qualityAssessment: { ...capture().qualityAssessment!, status: "review_required" }, updatedAt: "2026-09-12T10:03:00+00:00" });
  const queued = capture({ id: "depth-queued", status: "queued", currentStage: null, qualityAssessment: null, completedAt: null, updatedAt: "2026-09-12T10:04:00+00:00" });
  const view = render(<DepthCapturePanel project={{ ...project([oldActive, failed]), activeDepthCaptureId: oldActive.id }} onProjectUpdated={vi.fn()} />);
  expect(screen.getByRole("status", { name: "本地深度捕捉失败" })).toBeVisible();
  view.rerender(<DepthCapturePanel project={{ ...project([oldActive, review]), activeDepthCaptureId: oldActive.id }} onProjectUpdated={vi.fn()} />);
  expect(screen.getByRole("button", { name: "我已检查，继续实验性生成" })).toBeVisible();
  view.rerender(<DepthCapturePanel project={{ ...project([oldActive, review, queued]), activeDepthCaptureId: oldActive.id }} onProjectUpdated={vi.fn()} />);
  expect(screen.getByRole("status", { name: "本地深度捕捉正在排队" })).toBeVisible();
});

it("缺少可复刻性判断时不允许启动深度捕捉", () => {
  stubDesktop();
  const incomplete = project([]);
  incomplete.localPreprocessing = { ...incomplete.localPreprocessing!, reproducibilityAssessment: null };
  render(<DepthCapturePanel project={incomplete} onProjectUpdated={vi.fn()} />);
  expect(screen.getByText(/请先完成当前参考视频的本地预处理/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "开始本地深度捕捉" })).not.toBeInTheDocument();
});

it("参考图片不显示视频预览或深度启动动作", () => {
  stubDesktop();
  render(
    <DepthCapturePanel
      project={{ ...project([]), referenceMedia: { type: "image", id: "image-001", originalName: "hero.png", format: "png", sizeBytes: 11, width: 1200, height: 1600, hasTransparency: false } }}
      onProjectUpdated={vi.fn()}
    />,
  );

  expect(screen.queryByLabelText("参考视频预览")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "开始本地深度捕捉" })).not.toBeInTheDocument();
});

it("质量失败的已完成任务允许重新生成，并将选择的设备传给请求", async () => {
  stubDesktop();
  const failedQuality = capture({ qualityAssessment: { ...capture().qualityAssessment!, status: "failed" } });
  const start = vi.fn().mockResolvedValue(project());
  render(<DepthCapturePanel project={project([failedQuality])} onProjectUpdated={vi.fn()} start={start} />);
  await userEvent.selectOptions(screen.getByLabelText("深度计算设备"), "cpu");
  expect(screen.getByText(/CPU 慢速路径/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "重新生成深度素材" }));
  expect(start).toHaveBeenCalledWith("project-001", "cpu");
});

it("参考素材变化后解除提交锁并忽略旧的深度启动响应", async () => {
  stubDesktop();
  const pending = deferred<Project>();
  const onProjectUpdated = vi.fn();
  const onMutationPendingChange = vi.fn();
  const initial = project([]);
  const view = render(<DepthCapturePanel project={initial} onProjectUpdated={onProjectUpdated} start={vi.fn().mockReturnValue(pending.promise)} onMutationPendingChange={onMutationPendingChange} />);
  await userEvent.click(screen.getByRole("button", { name: "开始本地深度捕捉" }));
  expect(onMutationPendingChange).toHaveBeenCalledWith(true);
  const changedReference = { ...initial, referenceMedia: { ...initial.referenceMedia!, id: "video-002", originalName: "replacement.mp4" }, localPreprocessing: { ...initial.localPreprocessing!, sourceReferenceMediaId: "video-002", mediaType: "video" as const } as VideoLocalPreprocessing };
  view.rerender(<DepthCapturePanel project={changedReference} onProjectUpdated={onProjectUpdated} start={vi.fn().mockReturnValue(pending.promise)} onMutationPendingChange={onMutationPendingChange} />);
  await act(async () => { pending.resolve(project()); await pending.promise; });
  expect(onProjectUpdated).not.toHaveBeenCalled();
  expect(onMutationPendingChange).toHaveBeenCalledWith(false);
});

it("复核需要人工确认，并在确认后报告素材已可用于深度控制工作流", async () => {
  stubDesktop();
  const review = capture({ qualityAssessment: { ...capture().qualityAssessment!, status: "review_required" } });
  const confirmed = project([{ ...review, reviewConfirmedAt: "2026-09-12T10:02:00+00:00" }]);
  const confirm = vi.fn().mockResolvedValue(confirmed);
  const onProjectUpdated = vi.fn();
  render(<DepthCapturePanel project={project([review])} onProjectUpdated={onProjectUpdated} confirm={confirm} />);
  await userEvent.click(screen.getByRole("button", { name: "我已检查，继续实验性生成" }));
  expect(confirm).toHaveBeenCalledWith("project-001", "depth-001");
  expect(onProjectUpdated).toHaveBeenCalledWith(confirmed);
});

it("仅在排队或运行时轮询，项目切换后忽略旧响应", async () => {
  vi.useFakeTimers();
  stubDesktop();
  const pending = deferred<Project>();
  const load = vi.fn().mockReturnValue(pending.promise);
  const updated = vi.fn();
  const running = capture({ status: "running", currentStage: "estimatingDepth", qualityAssessment: null, completedAt: null });
  const view = render(<DepthCapturePanel project={project([running])} onProjectUpdated={updated} load={load} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(load).toHaveBeenCalledWith("project-001");
  view.rerender(<DepthCapturePanel project={{ ...project([running]), id: "project-002" }} onProjectUpdated={updated} load={load} />);
  await act(async () => { pending.resolve(project()); await pending.promise; });
  expect(updated).not.toHaveBeenCalled();
});

it("窄屏保留预览和结果，但不提供启动、重试或确认变更", () => {
  stubDesktop(false);
  const failed = capture({ status: "failed", error: { code: "depth_failed", message: "深度任务失败", stage: "encoding", retryable: true }, qualityAssessment: null });
  render(<DepthCapturePanel project={project([failed])} onProjectUpdated={vi.fn()} />);
  expect(screen.getByLabelText("参考视频预览")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("深度任务失败");
  expect(screen.queryByRole("button", { name: /开始本地深度捕捉|从失败阶段重试|我已检查/ })).not.toBeInTheDocument();
});
