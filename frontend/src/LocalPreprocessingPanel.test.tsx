import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import type {
  ImageLocalPreprocessing,
  ImagePreprocessingStageName,
  LocalPreprocessing,
  Project,
  VideoLocalPreprocessing,
  VideoPreprocessingStageName,
} from "./models";
import { LocalPreprocessingPanel } from "./LocalPreprocessingPanel";

function stubDesktop(matches = true) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  let currentMatches = matches;
  const mediaQueryList = {
    get matches() { return currentMatches; },
    media: "",
    onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  };
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => {
    mediaQueryList.media = query;
    return mediaQueryList;
  }));
  return {
    listeners,
    setMatches(next: boolean) {
      currentMatches = next;
      listeners.forEach((listener) => listener({ matches: next, media: mediaQueryList.media } as MediaQueryListEvent));
    },
  };
}

const stageNames: VideoPreprocessingStageName[] = [
  "decoding", "sceneDetection", "keyframeExtraction", "motionAnalysis", "reproducibilityAssessment",
];
const imageStageNames: ImagePreprocessingStageName[] = [
  "imageDecoding", "imageNormalization", "proxyGeneration", "reproducibilityAssessment",
];

function preprocessing(overrides: Partial<VideoLocalPreprocessing> = {}): VideoLocalPreprocessing {
  return {
    id: "preprocessing-001",
    sourceReferenceMediaId: "video-001", mediaType: "video",
    algorithmVersion: 1,
    status: "queued",
    currentStage: null,
    stages: stageNames.map((name) => ({ name, status: "pending", startedAt: null, completedAt: null })),
    queuedAt: "2026-09-11T10:00:00+00:00",
    startedAt: null,
    updatedAt: "2026-09-11T10:00:00+00:00",
    completedAt: null,
    proxySummary: null,
    reproducibilityAssessment: null,
    error: null,
    ...overrides,
  };
}

function project(localPreprocessing: LocalPreprocessing | null = null): Project {
  return {
    id: "project-001", name: "雨夜人像复刻",
    createdAt: "2026-09-11T10:00:00+00:00", updatedAt: "2026-09-11T10:00:00+00:00",
    referenceMedia: {
      type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4", sizeBytes: 11,
      durationSeconds: 2.5, width: 854, height: 480, frameRate: 24,
    },
    localPreprocessing,
  };
}

function completedTask(overrides: Partial<VideoLocalPreprocessing> = {}) {
  return preprocessing({
    status: "completed", currentStage: null, completedAt: "2026-09-11T10:01:00+00:00",
    stages: stageNames.map((name) => ({ name, status: "completed", startedAt: "2026-09-11T10:00:00+00:00", completedAt: "2026-09-11T10:01:00+00:00" })),
    proxySummary: { mediaType: "video", keyframeCount: 8, contactSheetCount: 1, sceneChangeCount: 0, motionP50: 1.2, motionP90: 3.2, motionPeak: 4.1, motionLevel: "moderate" },
    reproducibilityAssessment: {
      status: "pending_semantic_confirmation",
      checks: [{ criterion: "primary_subject_count", status: "pending", message: "待语义分析确认", evidence: "主要主体数量和复杂交互仍待确认。" }],
    },
    ...overrides,
  });
}

function completedImageTask(): ImageLocalPreprocessing {
  return {
    id: "image-preprocessing-001",
    sourceReferenceMediaId: "image-001",
    mediaType: "image",
    algorithmVersion: 1,
    status: "completed",
    currentStage: null,
    stages: imageStageNames.map((name) => ({
      name,
      status: "completed",
      startedAt: "2026-09-11T10:00:00+00:00",
      completedAt: "2026-09-11T10:01:00+00:00",
    })),
    queuedAt: "2026-09-11T10:00:00+00:00",
    startedAt: "2026-09-11T10:00:00+00:00",
    updatedAt: "2026-09-11T10:01:00+00:00",
    completedAt: "2026-09-11T10:01:00+00:00",
    proxySummary: { mediaType: "image" },
    reproducibilityAssessment: {
      status: "pending_semantic_confirmation",
      checks: [],
    },
    error: null,
  };
}

function imageProject(localPreprocessing: LocalPreprocessing | null = null): Project {
  return {
    ...project(localPreprocessing),
    referenceMedia: {
      type: "image", id: "image-001", originalName: "hero.png", format: "png", sizeBytes: 11,
      width: 5760, height: 3840, hasTransparency: true,
    },
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason?: unknown) => void = () => undefined;
  const promise = new Promise<T>((nextResolve, nextReject) => { resolve = nextResolve; reject = nextReject; });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("有视频时等待用户手动开始", () => {
  stubDesktop();
  const start = vi.fn();
  render(<LocalPreprocessingPanel project={project()} onProjectUpdated={vi.fn()} start={start} />);

  expect(screen.getByText("此步骤只在本机处理，尚不会发送分析代理。")).toBeVisible();
  expect(screen.getByRole("button", { name: "开始本地预处理" })).toBeVisible();
  expect(start).not.toHaveBeenCalled();
});

it("图片也等待用户手动启动本地预处理", async () => {
  stubDesktop();
  const start = vi.fn().mockResolvedValue(imageProject(completedImageTask()));
  render(
    <LocalPreprocessingPanel
      project={imageProject()}
      onProjectUpdated={vi.fn()}
      start={start}
    />,
  );

  expect(screen.getByText("此步骤只在本机处理，将生成方向与色彩标准化后的分析代理。")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "开始本地预处理" }));
  expect(start).toHaveBeenCalledWith("project-001");
});

it("没有参考素材时说明前置条件且不提供启动按钮", () => {
  stubDesktop();
  render(<LocalPreprocessingPanel project={{ ...project(), referenceMedia: null }} onProjectUpdated={vi.fn()} />);

  expect(screen.getByText("请先添加并校验参考素材，再开始本地预处理。")).toBeVisible();
  expect(screen.queryByRole("button", { name: "开始本地预处理" })).not.toBeInTheDocument();
});

it("启动成功后更新项目并把焦点移到状态标题", async () => {
  stubDesktop();
  const updated = project(preprocessing());
  const onProjectUpdated = vi.fn();
  let view: ReturnType<typeof render>;
  onProjectUpdated.mockImplementation((next: Project) => {
    view.rerender(<LocalPreprocessingPanel project={next} onProjectUpdated={onProjectUpdated} start={vi.fn().mockResolvedValue(updated)} />);
  });
  view = render(<LocalPreprocessingPanel project={project()} onProjectUpdated={onProjectUpdated} start={vi.fn().mockResolvedValue(updated)} />);

  await userEvent.click(screen.getByRole("button", { name: "开始本地预处理" }));

  expect(onProjectUpdated).toHaveBeenCalledWith(updated);
  expect(screen.getByRole("heading", { name: "本地预处理正在排队" })).toHaveFocus();
});

it("启动失败显示具体错误并保留启动按钮焦点", async () => {
  stubDesktop();
  render(<LocalPreprocessingPanel project={project()} onProjectUpdated={vi.fn()} start={vi.fn().mockRejectedValue(new Error("本地队列不可用"))} />);
  const button = screen.getByRole("button", { name: "开始本地预处理" });

  await userEvent.click(button);

  expect(await screen.findByRole("alert")).toHaveTextContent("本地队列不可用");
  expect(button).toHaveFocus();
});

it("按固定顺序显示五个文字阶段和非语义图标", () => {
  stubDesktop();
  const running = preprocessing({
    status: "running", currentStage: "keyframeExtraction",
    stages: stageNames.map((name) => ({
      name, status: name === "decoding" || name === "sceneDetection" ? "completed" : name === "keyframeExtraction" ? "running" : "pending",
      startedAt: null, completedAt: null,
    })),
  });
  render(<LocalPreprocessingPanel project={project(running)} onProjectUpdated={vi.fn()} />);

  for (const label of ["解码", "镜头检测", "关键帧提取", "运动分析", "初步可复刻性判断"]) expect(screen.getByText(label)).toBeVisible();
  for (const status of ["已完成", "进行中", "等待中"]) expect(screen.getAllByText(status)[0]).toBeVisible();
  expect(screen.getAllByRole("status")[0].querySelectorAll("svg[aria-hidden='true']").length).toBeGreaterThan(0);
});

it("完成后展示本地摘要和待语义分析结论", () => {
  stubDesktop();
  render(<LocalPreprocessingPanel project={project(completedTask())} onProjectUpdated={vi.fn()} />);

  expect(screen.getByText("8 张关键帧")).toBeVisible();
  expect(screen.getByText("1 个镜头")).toBeVisible();
  expect(screen.getByText("中度运动 · P90 3.200")).toBeVisible();
  expect(screen.getAllByText("待语义分析确认")[0]).toBeVisible();
  expect(screen.getByText(/主要主体数量和复杂交互仍待确认/)).toBeVisible();
  expect(screen.queryByText("处于可复刻范围")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveAccessibleName(expect.stringContaining("初步结论"));
});

it("完成图片任务显示四个图片阶段和专属摘要，不混入视频结论", () => {
  stubDesktop();
  render(<LocalPreprocessingPanel project={imageProject(completedImageTask())} onProjectUpdated={vi.fn()} />);

  for (const label of ["图像解码", "方向与色彩标准化", "分析代理生成", "初步可复刻性判断"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  expect(screen.getByText("代理尺寸：2048×1365")).toBeVisible();
  expect(screen.getByText("透明区域已使用白色背景处理。")).toBeVisible();
  expect(screen.getByText("待语义分析确认")).toBeVisible();
  expect(screen.queryByText(/镜头/)).not.toBeInTheDocument();
  expect(screen.queryByText(/运动强度/)).not.toBeInTheDocument();
});

it("图片任务失败时把焦点移到桌面端重试按钮", () => {
  stubDesktop();
  const running: ImageLocalPreprocessing = {
    ...completedImageTask(),
    status: "running",
    currentStage: "proxyGeneration",
    completedAt: null,
    proxySummary: null,
    reproducibilityAssessment: null,
    stages: imageStageNames.map((name) => ({
      name,
      status: name === "imageDecoding" || name === "imageNormalization" ? "completed" : name === "proxyGeneration" ? "running" : "pending",
      startedAt: null,
      completedAt: null,
    })),
  };
  const view = render(<LocalPreprocessingPanel project={imageProject(running)} onProjectUpdated={vi.fn()} />);
  const failed: ImageLocalPreprocessing = {
    ...running,
    status: "failed",
    error: { code: "proxy_generation_failed", message: "分析代理无法压缩到安全上限。", stage: "proxyGeneration", retryable: true },
    stages: running.stages.map((stage) => ({ ...stage, status: stage.name === "proxyGeneration" ? "failed" : stage.status })),
  };

  view.rerender(<LocalPreprocessingPanel project={imageProject(failed)} onProjectUpdated={vi.fn()} />);

  expect(screen.getByRole("button", { name: "从失败阶段重试" })).toHaveFocus();
});

it("超出范围时完整展示失败检查，且不可用运动不伪称低运动", () => {
  stubDesktop();
  const task = completedTask({
    proxySummary: { mediaType: "video", keyframeCount: 2, contactSheetCount: 1, sceneChangeCount: 2, motionP50: null, motionP90: null, motionPeak: null, motionLevel: "unavailable" },
    reproducibilityAssessment: {
      status: "out_of_scope",
      checks: [{ criterion: "single_shot", status: "failed", message: "检测到多个镜头", evidence: "检测到 2 次镜头切换。" }],
    },
  });
  render(<LocalPreprocessingPanel project={project(task)} onProjectUpdated={vi.fn()} />);

  expect(screen.getByText("运动强度无法独立评估")).toBeVisible();
  expect(screen.getByText("检测到多个镜头")).toBeVisible();
  expect(screen.getByText("检测到 2 次镜头切换。")).toBeVisible();
  expect(screen.getByText("后续仍可生成分析报告，但不承诺生成可靠的可执行工作流")).toBeVisible();
  expect(screen.queryByText("低运动")).not.toBeInTheDocument();
});

it("后台失败显示失败阶段并允许桌面端从失败阶段重试", async () => {
  stubDesktop();
  const failed = preprocessing({
    status: "failed", currentStage: "motionAnalysis",
    error: { code: "motion_failed", message: "无法读取运动向量", stage: "motionAnalysis", retryable: true },
    stages: stageNames.map((name) => ({ name, status: name === "motionAnalysis" ? "failed" : "pending", startedAt: null, completedAt: null })),
  });
  const start = vi.fn().mockResolvedValue(project(preprocessing()));
  render(<LocalPreprocessingPanel project={project(failed)} onProjectUpdated={vi.fn()} start={start} />);

  expect(screen.getByRole("alert")).toHaveTextContent("无法读取运动向量");
  expect(screen.getByText("运动分析失败")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "从失败阶段重试" }));
  expect(start).toHaveBeenCalledWith("project-001");
});

it("运行中每秒读取一次，未完成请求不重叠且完成后停止", async () => {
  vi.useFakeTimers();
  stubDesktop();
  const pending = deferred<Project>();
  const load = vi.fn().mockReturnValueOnce(pending.promise);
  render(<LocalPreprocessingPanel project={project(preprocessing({ status: "running", currentStage: "decoding" }))} onProjectUpdated={vi.fn()} load={load} />);

  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(project(completedTask())); await pending.promise; });
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(load).toHaveBeenCalledTimes(1);
});

it("刷新失败保留最后状态并可手动重新读取", async () => {
  vi.useFakeTimers();
  stubDesktop();
  const load = vi.fn().mockRejectedValueOnce(new Error("服务暂不可达")).mockResolvedValue(project(preprocessing({ status: "running", currentStage: "decoding" })));
  render(<LocalPreprocessingPanel project={project(preprocessing({ status: "running", currentStage: "decoding" }))} onProjectUpdated={vi.fn()} load={load} />);

  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(screen.getByText(/暂时无法刷新状态/)).toBeVisible();
  expect(screen.getByRole("heading", { name: "本地预处理正在运行" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await act(async () => { await Promise.resolve(); });
  expect(load).toHaveBeenCalledTimes(2);
});

it("项目切换或卸载后忽略旧轮询响应并清理定时器", async () => {
  vi.useFakeTimers();
  stubDesktop();
  const pending = deferred<Project>();
  const load = vi.fn().mockReturnValue(pending.promise);
  const updated = vi.fn();
  const view = render(<LocalPreprocessingPanel project={project(preprocessing({ status: "running" }))} onProjectUpdated={updated} load={load} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  view.rerender(<LocalPreprocessingPanel project={{ ...project(), id: "project-002" }} onProjectUpdated={updated} load={load} />);
  await act(async () => { pending.resolve(project(completedTask())); await pending.promise; });
  expect(updated).not.toHaveBeenCalled();

  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(load).toHaveBeenCalledTimes(1);
});

it("1023px 为只读，1024px 恢复启动操作", () => {
  const desktop = stubDesktop(false);
  const view = render(<LocalPreprocessingPanel project={project()} onProjectUpdated={vi.fn()} />);
  expect(screen.getByText("请在宽度至少 1024px 的桌面设备开始或重试本地预处理")).toBeVisible();
  expect(screen.queryByRole("button", { name: "开始本地预处理" })).not.toBeInTheDocument();

  act(() => desktop.setMatches(true));
  expect(screen.getByRole("button", { name: "开始本地预处理" })).toBeVisible();
  view.unmount();
  expect(desktop.listeners.size).toBe(0);
});

it("窄屏保留后台失败详情，但不提供提交重试", () => {
  stubDesktop(false);
  const failed = preprocessing({
    status: "failed", currentStage: "motionAnalysis",
    error: { code: "motion_failed", message: "无法读取运动向量", stage: "motionAnalysis", retryable: true },
    stages: stageNames.map((name) => ({ name, status: name === "motionAnalysis" ? "failed" : "pending", startedAt: null, completedAt: null })),
  });
  render(<LocalPreprocessingPanel project={project(failed)} onProjectUpdated={vi.fn()} />);

  expect(screen.getByRole("alert")).toHaveTextContent("运动分析失败");
  expect(screen.getByRole("alert")).toHaveTextContent("无法读取运动向量");
  expect(screen.queryByRole("button", { name: "从失败阶段重试" })).not.toBeInTheDocument();
});

it("窄屏图片任务只读，不显示启动操作", () => {
  stubDesktop(false);
  render(<LocalPreprocessingPanel project={imageProject()} onProjectUpdated={vi.fn()} />);

  expect(screen.getByText("请在宽度至少 1024px 的桌面设备开始或重试本地预处理")).toBeVisible();
  expect(screen.queryByRole("button", { name: "开始本地预处理" })).not.toBeInTheDocument();
});

it("窄屏保留刷新错误并可重新读取状态", async () => {
  vi.useFakeTimers();
  stubDesktop(false);
  const load = vi.fn().mockRejectedValueOnce(new Error("服务暂不可达")).mockResolvedValue(project(preprocessing({ status: "running" })));
  render(<LocalPreprocessingPanel project={project(preprocessing({ status: "running" }))} onProjectUpdated={vi.fn()} load={load} />);

  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(screen.getByText(/暂时无法刷新状态/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await act(async () => { await Promise.resolve(); });
  expect(load).toHaveBeenCalledTimes(2);
});

it("手动重新读取取消旧定时器，始终只保留一条后续轮询链", async () => {
  vi.useFakeTimers();
  stubDesktop();
  const running = project(preprocessing({ status: "running", currentStage: "decoding" }));
  const load = vi.fn().mockRejectedValueOnce(new Error("暂不可达")).mockResolvedValue(running);
  render(<LocalPreprocessingPanel project={running} onProjectUpdated={vi.fn()} load={load} />);

  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await act(async () => { await Promise.resolve(); });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(load).toHaveBeenCalledTimes(3);
});

it("切换项目时清除旧启动中的提交态并忽略旧响应", async () => {
  stubDesktop();
  const pending = deferred<Project>();
  const start = vi.fn().mockReturnValue(pending.promise);
  const onProjectUpdated = vi.fn();
  const view = render(<LocalPreprocessingPanel project={project()} onProjectUpdated={onProjectUpdated} start={start} />);

  await userEvent.click(screen.getByRole("button", { name: "开始本地预处理" }));
  const projectB = { ...project(), id: "project-002", name: "室内产品复刻" };
  view.rerender(<LocalPreprocessingPanel project={projectB} onProjectUpdated={onProjectUpdated} start={start} />);
  const newProjectButton = screen.getByRole("button", { name: "开始本地预处理" });
  expect(newProjectButton).not.toBeDisabled();

  await act(async () => { pending.resolve(project(preprocessing())); await pending.promise; });
  expect(onProjectUpdated).not.toHaveBeenCalled();
  expect(newProjectButton).toBeEnabled();
});
