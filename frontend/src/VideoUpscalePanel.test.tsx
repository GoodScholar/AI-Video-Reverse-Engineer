import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VideoUpscalePanel } from "./VideoUpscalePanel";
import { getVideoUpscaleState, startVideoUpscale, type UpscaleRun } from "./videoUpscaleApi";
import type { Project } from "./models";

vi.mock("./videoUpscaleApi", () => ({
  getVideoUpscaleState: vi.fn(),
  startVideoUpscale: vi.fn(),
  upscaleVideoUrl: (projectId: string, runId: string, download = false) =>
    `/api/projects/${projectId}/upscale/${runId}/video${download ? "?download=true" : ""}`,
}));

const mockedGet = vi.mocked(getVideoUpscaleState);
const mockedStart = vi.mocked(startVideoUpscale);

function project(sourceId = "video-001", width = 1280, height = 720): Project {
  return {
    id: "project-001",
    name: "雨夜人像",
    createdAt: "2026-09-16T10:00:00Z",
    updatedAt: "2026-09-16T10:00:00Z",
    referenceMedia: {
      type: "video",
      id: sourceId,
      originalName: "clip.mp4",
      format: "mp4",
      sizeBytes: 11,
      durationSeconds: 5,
      width,
      height,
      frameRate: 24,
    },
    localPreprocessing: null,
  };
}

function state(sourceId = "video-001", available = true) {
  return {
    environment: { available, message: available ? "Real-ESRGAN 已就绪" : "未找到本地 Real-ESRGAN 模型" },
    runs: [{
      id: "upscale-001", sourceId, outputResolution: "1080p" as const, status: "completed" as const,
      stage: "完成", progress: 100, error: null,
      output: { width: 1920, height: 1080, frameRate: 24, durationSeconds: 5 },
      createdAt: "2026-09-16T10:00:00Z",
    }],
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  mockedGet.mockReset();
  mockedStart.mockReset();
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("VideoUpscalePanel", () => {
  it("本地 Real-ESRGAN 未就绪时展示原因并禁止开始", async () => {
    mockedGet.mockResolvedValueOnce(state("video-001", false)).mockResolvedValueOnce(state());

    render(<VideoUpscalePanel project={project()} />);

    expect(await screen.findByText("未找到本地 Real-ESRGAN 模型")).toBeVisible();
    expect(screen.getByRole("button", { name: "开始 1080P 超分" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
    expect(await screen.findByText("Real-ESRGAN 已就绪")).toBeVisible();
  });

  it("提交完成任务后展示实际输出和下载入口", async () => {
    mockedGet.mockResolvedValue({ ...state(), runs: [] });
    mockedStart.mockResolvedValue(state().runs[0]);

    render(<VideoUpscalePanel project={project()} />);
    await screen.findByText("Real-ESRGAN 已就绪");
    await userEvent.click(screen.getByRole("button", { name: "开始 1080P 超分" }));

    expect(mockedStart).toHaveBeenCalledWith("project-001", "video-001", "1080p");
    expect(await screen.findByText("1920×1080 · 24 fps · 5 秒")).toBeVisible();
    expect(screen.getByRole("link", { name: "下载超分视频" })).toHaveAttribute(
      "href", "/api/projects/project-001/upscale/upscale-001/video?download=true",
    );
  });

  it("切换参考来源后忽略旧来源的迟到读取结果", async () => {
    const old = deferred<ReturnType<typeof state>>();
    mockedGet.mockReturnValueOnce(old.promise).mockResolvedValueOnce({
      ...state("video-002"),
      environment: { available: true, message: "新参考环境状态" },
    });
    const view = render(<VideoUpscalePanel project={project()} />);
    view.rerender(<VideoUpscalePanel project={project("video-002")} />);

    expect(await screen.findByText("新参考环境状态")).toBeVisible();
    await act(async () => { old.resolve({ ...state("video-001"), environment: { available: false, message: "旧参考环境状态" } }); });
    expect(screen.queryByText("旧参考环境状态")).not.toBeInTheDocument();
  });

  it("切换参考来源后不展示旧提交的失败结果", async () => {
    let rejectStart: (error: Error) => void = () => undefined;
    mockedGet
      .mockResolvedValueOnce({ ...state(), runs: [] })
      .mockResolvedValueOnce({ ...state("video-002"), runs: [], environment: { available: true, message: "新参考环境状态" } });
    mockedStart.mockReturnValue(new Promise<UpscaleRun>((_resolve, reject) => { rejectStart = reject; }));
    const view = render(<VideoUpscalePanel project={project()} />);
    await screen.findByText("Real-ESRGAN 已就绪");
    await userEvent.click(screen.getByRole("button", { name: "开始 1080P 超分" }));
    view.rerender(<VideoUpscalePanel project={project("video-002")} />);
    await screen.findByText("新参考环境状态");
    await act(async () => { rejectStart(new Error("旧提交失败")); });

    expect(screen.queryByText("旧提交失败")).not.toBeInTheDocument();
  });

  it("同一项目的另一来源仍在超分时提示等待并禁止新的提交", async () => {
    mockedGet.mockResolvedValue({
      ...state("video-001"),
      runs: [{
        ...state("video-001").runs[0],
        status: "running",
        stage: "逐帧超分",
        progress: 42,
        output: null,
      }],
    });

    render(<VideoUpscalePanel project={project("video-002")} />);

    expect(await screen.findByText("当前项目仍有视频超分任务在后台处理中，完成后才能开始或重试。")).toBeVisible();
    expect(screen.getByRole("button", { name: "开始 1080P 超分" })).toBeDisabled();
    expect(screen.queryByText("逐帧超分")).not.toBeInTheDocument();
  });

  it("轮询失败后保留活动任务，并在下一次成功刷新时恢复", async () => {
    vi.useFakeTimers();
    const running = {
      ...state(),
      runs: [{ ...state().runs[0], status: "running" as const, stage: "upscaling", progress: 42, output: null }],
    };
    mockedGet
      .mockResolvedValueOnce(running)
      .mockRejectedValueOnce(new Error("刷新失败"))
      .mockResolvedValueOnce({ ...running, runs: [{ ...running.runs[0], status: "completed" as const, stage: "完成", progress: 100, output: state().runs[0].output }] });
    render(<VideoUpscalePanel project={project()} />);
    await act(async () => undefined);
    expect(screen.getByText("Real-ESRGAN 超分")).toBeVisible();

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(screen.getByRole("alert")).toHaveTextContent("刷新失败");
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/已完成/)).toBeVisible();
  });

  it("等待迟到轮询响应时不发起重叠请求", async () => {
    vi.useFakeTimers();
    const running = {
      ...state(),
      runs: [{ ...state().runs[0], status: "running" as const, stage: "upscaling", progress: 42, output: null }],
    };
    const delayedPoll = deferred<{ environment: { available: boolean; message: string }; runs: UpscaleRun[] }>();
    mockedGet.mockResolvedValueOnce(running).mockReturnValueOnce(delayedPoll.promise).mockResolvedValue(running);
    render(<VideoUpscalePanel project={project()} />);
    await act(async () => undefined);

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(mockedGet).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(mockedGet).toHaveBeenCalledTimes(2);
    await act(async () => { delayedPoll.resolve({ ...running, runs: [{ ...state().runs[0], status: "completed" as const }] }); });
    expect(screen.getByText(/已完成/)).toBeVisible();
  });

  it("初次读取失败后可重新读取本地环境状态", async () => {
    mockedGet.mockRejectedValueOnce(new Error("本地服务暂不可用")).mockResolvedValueOnce(state());
    render(<VideoUpscalePanel project={project()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("本地服务暂不可用");
    await userEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
    expect(await screen.findByText("Real-ESRGAN 已就绪")).toBeVisible();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it("来源已达目标清晰度时禁用对应选项", async () => {
    mockedGet.mockResolvedValue({ ...state(), runs: [] });
    render(<VideoUpscalePanel project={project("video-001", 1920, 1080)} />);
    expect(await screen.findByText("1080P 目标：1920×1080，原片已达到此清晰度")).toBeVisible();
    expect(screen.getByRole("button", { name: "开始 1080P 超分" })).toBeDisabled();
    expect(screen.getByText("2K 目标：2560×1440")).toBeVisible();
    expect(screen.getByRole("button", { name: "开始 2K 超分" })).toBeEnabled();
  });

  it("竖屏按短边输出并提交2K清晰度", async () => {
    mockedGet.mockResolvedValue({ ...state(), runs: [] });
    mockedStart.mockResolvedValue({ ...state().runs[0], outputResolution: "2k" });
    render(<VideoUpscalePanel project={project("video-001", 720, 1410)} />);
    expect(await screen.findByText("1080P 目标：1080×2116")).toBeVisible();
    expect(screen.getByText("2K 目标：1440×2820")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "开始 2K 超分" }));
    expect(mockedStart).toHaveBeenCalledWith("project-001", "video-001", "2k");
  });

  it("旧倍率任务保留实际标签与下载，不误标为新清晰度", async () => {
    mockedGet.mockResolvedValue({ ...state(), runs: [{ ...state().runs[0], outputResolution: undefined, scale: 2 }] });
    render(<VideoUpscalePanel project={project()} />);
    expect(await screen.findByText("2× Real-ESRGAN · 已完成")).toBeVisible();
    expect(screen.getByRole("link", { name: "下载超分视频" })).toBeVisible();
  });
});
