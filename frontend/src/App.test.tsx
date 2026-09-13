import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const capabilities = {
  analysisService: { state: "unconfigured", label: "未配置" },
  localComfyui: { state: "disconnected", label: "未连接" },
};

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

describe("项目首页", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("独立读取供应商目录，失败可重试且恢复选中服务", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const readyProject = {
      id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:01:00+00:00",
      referenceMedia: { type: "image" as const, id: "image-001", originalName: "rain.png", format: "png" as const, sizeBytes: 11, width: 1200, height: 1600, hasTransparency: false },
      localPreprocessing: { id: "pre-001", sourceReferenceMediaId: "image-001", mediaType: "image" as const, algorithmVersion: 1, status: "completed" as const, currentStage: null, stages: [], queuedAt: "2026-09-10T10:00:00+00:00", startedAt: null, updatedAt: "2026-09-10T10:01:00+00:00", completedAt: "2026-09-10T10:01:00+00:00", proxySummary: null, reproducibilityAssessment: { status: "pending_semantic_confirmation" as const, checks: [] }, error: null },
    };
    let providerAttempts = 0;
    vi.mocked(fetch).mockImplementation((url) => {
      if (url === "/api/projects") return Promise.resolve(response([readyProject]));
      if (url === "/api/capabilities") return Promise.resolve(response(capabilities));
      if (url === "/api/analysis-providers") {
        providerAttempts += 1;
        return providerAttempts === 1
          ? Promise.reject(new TypeError("offline"))
          : Promise.resolve(response([{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }]));
      }
      return Promise.reject(new Error(`unexpected ${String(url)}`));
    });

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取分析服务设置");
    await userEvent.click(screen.getByRole("button", { name: "重新读取分析服务设置" }));
    expect(await screen.findByText("bailian · qwen3.7-flash")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /雨夜人像复刻/ }));
    expect(screen.getByRole("button", { name: "开始语义分析" })).toBeEnabled();
  });

  it("接入深度审查面板，并在任一深度任务运行时锁定参考素材替换", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const runningDepth = {
      id: "depth-001", sourceReferenceVideoId: "video-001", algorithmVersion: 1, status: "running" as const,
      devicePreference: "auto" as const, executionDevice: "mps" as const,
      modelIdentity: { modelId: "video-depth-anything-small-relative" as const, upstreamCommit: "4f5ae23172ba60fd7bc11ef671cca678842c7072" as const, checkpointSha256: "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609" as const },
      normalizationDirection: "near_white_far_black" as const, currentStage: "estimatingDepth" as const,
      stages: ["preparing", "estimatingDepth", "encoding", "qualityAssessment"].map((name) => ({ name, status: name === "estimatingDepth" ? "running" as const : "pending" as const, startedAt: null, completedAt: null })),
      outputSummary: null, qualityAssessment: null, reviewConfirmedAt: null, error: null,
      queuedAt: "2026-09-12T10:00:00+00:00", startedAt: "2026-09-12T10:00:01+00:00", updatedAt: "2026-09-12T10:00:02+00:00", completedAt: null,
    };
    const readyProject = {
      id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-12T10:00:02+00:00",
      referenceMedia: { type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4" as const, sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 },
      localPreprocessing: { id: "pre-001", sourceReferenceMediaId: "video-001", mediaType: "video", algorithmVersion: 1, status: "completed" as const, currentStage: null, stages: [], queuedAt: "2026-09-12T10:00:00+00:00", startedAt: null, updatedAt: "2026-09-12T10:01:00+00:00", completedAt: "2026-09-12T10:01:00+00:00", proxySummary: null, reproducibilityAssessment: null, error: null },
      depthCaptures: [runningDepth], activeDepthCaptureId: "depth-001",
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([readyProject]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));

    expect(screen.getByRole("heading", { name: "深度捕捉审查" })).toBeVisible();
    expect(screen.getByRole("status", { name: "本地深度捕捉正在运行" })).toBeVisible();
    expect(screen.getByText(/本地预处理或深度捕捉运行时不能更换参考素材/)).toBeVisible();
    expect(screen.getByLabelText("参考素材文件")).toBeDisabled();
  });

  it("深度启动请求尚未完成时也锁定参考素材替换", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const readyProject = {
      id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-12T10:00:02+00:00",
      referenceMedia: { type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4" as const, sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 },
      localPreprocessing: { id: "pre-001", sourceReferenceMediaId: "video-001", mediaType: "video", algorithmVersion: 1, status: "completed" as const, currentStage: null, stages: [], queuedAt: "2026-09-12T10:00:00+00:00", startedAt: null, updatedAt: "2026-09-12T10:01:00+00:00", completedAt: "2026-09-12T10:01:00+00:00", proxySummary: null, reproducibilityAssessment: { status: "pending_semantic_confirmation" as const, checks: [] }, error: null },
      depthCaptures: [], activeDepthCaptureId: null,
    };
    const pending = deferred<Response>();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([readyProject]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockReturnValueOnce(pending.promise);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));
    await userEvent.click(screen.getByRole("button", { name: "开始本地深度捕捉" }));
    expect(screen.getByLabelText("参考素材文件")).toBeDisabled();
  });

  it("在参考素材下方显示本地预处理且不会自动启动", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const readyProject = {
      id: "project-001",
      name: "雨夜人像复刻",
      createdAt: "2026-09-10T10:00:00+00:00",
      updatedAt: "2026-09-10T10:00:00+00:00",
      referenceMedia: { type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4", sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 },
      localPreprocessing: null,
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([readyProject]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));

    expect(screen.getByRole("heading", { name: "本地预处理" })).toBeVisible();
    expect(screen.getByRole("button", { name: "开始本地预处理" })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("图片本地预处理排队时锁定参考素材替换", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const readyProject = {
      id: "project-001", name: "图片复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:00:00+00:00",
      referenceMedia: { type: "image" as const, id: "image-001", originalName: "hero.png", format: "png" as const, sizeBytes: 11, width: 1200, height: 1600, hasTransparency: true },
      localPreprocessing: {
        id: "preprocessing-001", sourceReferenceMediaId: "image-001", mediaType: "image" as const, algorithmVersion: 1, status: "queued" as const,
        currentStage: "imageDecoding" as const, stages: [], queuedAt: "2026-09-10T10:00:00+00:00", startedAt: null,
        updatedAt: "2026-09-10T10:00:00+00:00", completedAt: null, proxySummary: null, reproducibilityAssessment: null, error: null,
      },
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([readyProject]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /图片复刻/ }));

    expect(screen.getByText("本地预处理或深度捕捉运行时不能更换参考素材")).toBeVisible();
    expect(screen.getByLabelText("参考素材文件")).toBeDisabled();
    expect(screen.getByRole("button", { name: "更换参考素材" })).toBeDisabled();
  });

  it("重新打开完成项目直接显示摘要且不会自动 POST", async () => {
    const completed = {
      id: "preprocessing-001", sourceReferenceMediaId: "video-001", mediaType: "video", algorithmVersion: 1, status: "completed" as const,
      currentStage: null, queuedAt: "2026-09-10T10:00:00+00:00", startedAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:01:00+00:00", completedAt: "2026-09-10T10:01:00+00:00",
      stages: ["decoding", "sceneDetection", "keyframeExtraction", "motionAnalysis", "reproducibilityAssessment"].map((name) => ({ name: name as "decoding" | "sceneDetection" | "keyframeExtraction" | "motionAnalysis" | "reproducibilityAssessment", status: "completed" as const, startedAt: null, completedAt: null })),
      proxySummary: { mediaType: "video" as const, keyframeCount: 8, contactSheetCount: 1 as const, sceneChangeCount: 0, motionP50: 1, motionP90: 2, motionPeak: 3, motionLevel: "light" as const },
      reproducibilityAssessment: { status: "pending_semantic_confirmation" as const, checks: [] }, error: null,
    };
    const readyProject = {
      id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:01:00+00:00",
      referenceMedia: { type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4", sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 }, localPreprocessing: completed,
    };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([readyProject]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));

    expect(screen.getByText("8 张关键帧")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("轮询返回新项目时同步当前页与首页集合", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const running = {
      id: "preprocessing-001", sourceReferenceMediaId: "video-001", mediaType: "video", algorithmVersion: 1, status: "running" as const,
      currentStage: "decoding" as const, stages: [], queuedAt: "2026-09-10T10:00:00+00:00", startedAt: null,
      updatedAt: "2026-09-10T10:00:00+00:00", completedAt: null, proxySummary: null, reproducibilityAssessment: null, error: null,
    };
    const projectA = {
      id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:00:00+00:00",
      referenceMedia: { type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4" as const, sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 }, localPreprocessing: running,
    };
    const projectB = { id: "project-002", name: "室内产品复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: null, localPreprocessing: null };
    const completed = { ...projectA, updatedAt: "2026-09-12T10:00:00+00:00", localPreprocessing: { ...running, status: "completed" as const, currentStage: null, completedAt: "2026-09-12T10:00:00+00:00" } };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([projectA, projectB]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(response(completed));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1_050)); });
    expect(screen.getByRole("heading", { name: "本地预处理已完成" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "返回项目首页" }));
    const rows = await screen.findAllByRole("button", { name: /雨夜人像复刻|室内产品复刻/ });
    expect(rows[0]).toHaveAccessibleName(/雨夜人像复刻/);
  });

  it("切换项目后旧轮询响应不能覆盖当前项目", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const pending = deferred<Response>();
    const running = {
      id: "preprocessing-001", sourceReferenceMediaId: "video-001", mediaType: "video", algorithmVersion: 1, status: "running" as const,
      currentStage: "decoding" as const, stages: [], queuedAt: "2026-09-10T10:00:00+00:00", startedAt: null,
      updatedAt: "2026-09-10T10:00:00+00:00", completedAt: null, proxySummary: null, reproducibilityAssessment: null, error: null,
    };
    const projectA = {
      id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:00:00+00:00",
      referenceMedia: { type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4" as const, sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 }, localPreprocessing: running,
    };
    const projectB = { id: "project-002", name: "室内产品复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: null, localPreprocessing: null };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([projectA, projectB]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockReturnValueOnce(pending.promise);

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1_050)); });
    fireEvent.click(screen.getByRole("button", { name: "返回项目首页" }));
    fireEvent.click(screen.getByRole("button", { name: /室内产品复刻/ }));
    await act(async () => { pending.resolve(response({ ...projectA, updatedAt: "2026-09-12T10:00:00+00:00" })); });

    expect(screen.getByRole("heading", { name: "室内产品复刻" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "雨夜人像复刻" })).not.toBeInTheDocument();
  });

  it("让用户创建命名的复刻项目并进入初始项目页", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(
      response(capabilities),
    );
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(
      response({
        id: "project-001",
        name: "雨夜人像复刻",
        createdAt: "2026-09-10T10:00:00+00:00",
        updatedAt: "2026-09-10T10:00:00+00:00",
        referenceMedia: null,
        localPreprocessing: null,
      }, 201),
    );

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "新建复刻项目" }));
    await userEvent.type(screen.getByLabelText("项目名称"), "雨夜人像复刻");
    await userEvent.click(screen.getByRole("button", { name: "创建并进入项目" }));

    expect(await screen.findByRole("heading", { name: "雨夜人像复刻" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "参考素材" })).toBeVisible();
    expect(screen.getByText("请在宽度至少 1024px 的桌面设备添加参考素材"))
      .toBeVisible();
  });

  it("在首页内联显示创建表单，而不是弹出对话框", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "新建复刻项目" }));

    expect(screen.getByLabelText("项目名称")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("首页与空状态将图片和视频都说明为参考素材入口", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "从一份参考素材开始一项可继续的复刻工作。" })).toBeVisible();
    expect(screen.getByText("从一个命名项目开始。参考图片或视频将在下一步添加。")).toBeVisible();
  });

  it("让用户从最近复刻项目列表重新打开本地项目", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      response([
        {
          id: "project-001",
          name: "雨夜人像复刻",
          createdAt: "2026-09-10T10:00:00+00:00",
          updatedAt: "2026-09-10T10:00:00+00:00",
          referenceMedia: null,
          localPreprocessing: null,
        },
      ]),
    );
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));

    expect(await screen.findByRole("heading", { name: "雨夜人像复刻" })).toBeVisible();
    expect(screen.getByText(/创建于 .*· 仅保存在本地/))
      .toBeVisible();
    expect(screen.queryByText("复刻项目 / 初始页面")).not.toBeInTheDocument();
  });

  it("重新打开带有已保存参考素材的项目", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      response([{
        id: "project-001",
        name: "雨夜人像复刻",
        createdAt: "2026-09-10T10:00:00+00:00",
        updatedAt: "2026-09-11T10:00:00+00:00",
        referenceMedia: {
          type: "video", id: "video-001",
          originalName: "clip.mp4",
          format: "mp4",
          sizeBytes: 11,
          durationSeconds: 2.5,
          width: 854,
          height: 480,
          frameRate: 24,
        },
        localPreprocessing: null,
      }]),
    );
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));

    expect(await screen.findByText("clip.mp4")).toBeVisible();
    expect(screen.getByText("854×480")).toBeVisible();
  });

  it("在上传后返回首页仍按更新时间保存并重新打开更新后的项目", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([
      {
        id: "project-001",
        name: "雨夜人像复刻",
        createdAt: "2026-09-10T10:00:00+00:00",
        updatedAt: "2026-09-10T10:00:00+00:00",
        referenceMedia: null,
        localPreprocessing: null,
      },
      {
        id: "project-002",
        name: "室内产品复刻",
        createdAt: "2026-09-10T10:00:00+00:00",
        updatedAt: "2026-09-11T10:00:00+00:00",
        referenceMedia: null,
        localPreprocessing: null,
      },
    ]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(response({
      id: "project-001",
      name: "雨夜人像复刻",
      createdAt: "2026-09-10T10:00:00+00:00",
      updatedAt: "2026-09-12T10:00:00+00:00",
      referenceMedia: {
        type: "video", id: "video-001",
        originalName: "new-clip.mp4",
        format: "mp4",
        sizeBytes: 11,
        durationSeconds: 2.5,
        width: 854,
        height: 480,
        frameRate: 24,
      },
      localPreprocessing: null,
    }));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));
    await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["video"], "new-clip.mp4", { type: "video/mp4" }));
    expect(await screen.findByText("new-clip.mp4")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "返回项目首页" }));
    const projectRows = await screen.findAllByRole("button", { name: /雨夜人像复刻|室内产品复刻/ });
    expect(projectRows).toHaveLength(2);
    expect(projectRows[0]).toHaveAccessibleName(/雨夜人像复刻/);
    expect(projectRows[1]).toHaveAccessibleName(/室内产品复刻/);
    expect(within(projectRows[1]).getByText("最近更新 9月11日 18:00")).toBeVisible();

    await userEvent.click(projectRows[0]);
    expect(await screen.findByText("new-clip.mp4")).toBeVisible();
  });

  it("离开项目页后仍合并上传结果，但不自动返回项目页", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const pending = deferred<Response>();
    const original = { id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:00:00+00:00", referenceMedia: null, localPreprocessing: null };
    const other = { id: "project-002", name: "室内产品复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: null, localPreprocessing: null };
    const updated = { ...original, updatedAt: "2026-09-12T10:00:00+00:00", referenceMedia: { type: "video", id: "video-001", originalName: "new.mp4", format: "mp4", sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 } };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([original, other]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockReturnValueOnce(pending.promise);

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));
    await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["video"], "new.mp4", { type: "video/mp4" }));
    await userEvent.click(screen.getByRole("button", { name: "返回项目首页" }));
    await act(async () => { pending.resolve(response(updated)); });

    const rows = await screen.findAllByRole("button", { name: /雨夜人像复刻|室内产品复刻/ });
    expect(rows[0]).toHaveAccessibleName(/雨夜人像复刻/);
    expect(screen.queryByRole("heading", { name: "雨夜人像复刻" })).not.toBeInTheDocument();
    await userEvent.click(rows[0]);
    expect(await screen.findByText("new.mp4")).toBeVisible();
  });

  it("切到另一个项目后不会被先前上传响应抢回", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const pending = deferred<Response>();
    const original = { id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-10T10:00:00+00:00", referenceMedia: null, localPreprocessing: null };
    const other = { id: "project-002", name: "室内产品复刻", createdAt: "2026-09-10T10:00:00+00:00", updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: null, localPreprocessing: null };
    const updated = { ...original, updatedAt: "2026-09-12T10:00:00+00:00", referenceMedia: { type: "video", id: "video-001", originalName: "new.mp4", format: "mp4", sizeBytes: 11, durationSeconds: 2.5, width: 854, height: 480, frameRate: 24 } };
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([original, other]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockReturnValueOnce(pending.promise);

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));
    await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["video"], "new.mp4", { type: "video/mp4" }));
    await userEvent.click(screen.getByRole("button", { name: "返回项目首页" }));
    await userEvent.click(await screen.findByRole("button", { name: /室内产品复刻/ }));
    await act(async () => { pending.resolve(response(updated)); });

    expect(await screen.findByRole("heading", { name: "室内产品复刻" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "雨夜人像复刻" })).not.toBeInTheDocument();
  });

  it("让用户在读取本地存储失败后重新读取项目", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response({ detail: "本地项目存储不可用" }, 503));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(
      response([
        {
          id: "project-002",
          name: "商品运动复刻",
          createdAt: "2026-09-10T10:00:00+00:00",
          updatedAt: "2026-09-10T10:00:00+00:00",
          referenceMedia: null,
          localPreprocessing: null,
        },
      ]),
    );
    fetchMock.mockResolvedValueOnce(response(capabilities));

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "重新读取" }));

    expect(await screen.findByRole("button", { name: /商品运动复刻/ })).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("让键盘用户以非空名称创建项目，并把焦点移到名称输入框", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(
      response({
        id: "project-003",
        name: "室内产品复刻",
        createdAt: "2026-09-10T10:00:00+00:00",
        updatedAt: "2026-09-10T10:00:00+00:00",
        referenceMedia: null,
        localPreprocessing: null,
      }, 201),
    );

    const user = userEvent.setup();
    render(<App />);
    const newProject = await screen.findByRole("button", { name: "新建复刻项目" });
    newProject.focus();
    await user.keyboard("{Enter}");

    const nameInput = screen.getByLabelText("项目名称");
    expect(nameInput).toHaveFocus();
    expect(screen.getByRole("button", { name: "创建并进入项目" })).toBeDisabled();

    await user.type(nameInput, "室内产品复刻{Enter}");

    expect(await screen.findByRole("heading", { name: "室内产品复刻" })).toBeVisible();
  });

  it("在环境检测失败时分别显示两项状态不可用，仍允许创建项目并可重新检测", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockRejectedValueOnce(new Error("服务未启动"));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(
      response({
        id: "project-004",
        name: "离线创建复刻",
        createdAt: "2026-09-10T10:00:00+00:00",
        updatedAt: "2026-09-10T10:00:00+00:00",
        referenceMedia: null,
        localPreprocessing: null,
      }, 201),
    );

    render(<App />);

    expect(await screen.findAllByText("状态不可用")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "重新检测" }));
    expect(await screen.findAllByText("未配置")).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "新建复刻项目" }));
    await userEvent.type(screen.getByLabelText("项目名称"), "离线创建复刻");
    await userEvent.click(screen.getByRole("button", { name: "创建并进入项目" }));

    expect(await screen.findByRole("heading", { name: "离线创建复刻" })).toBeVisible();
  });

  it("把损坏项目数据的后端说明原样显示给用户", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response({ detail: "本地项目数据已损坏，请从备份恢复 projects.json，或将损坏文件移到其他位置后重新读取。" }, 503));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("本地项目数据已损坏，请从备份恢复 projects.json，或将损坏文件移到其他位置后重新读取。");
  });

  it("取消内联创建后把焦点返还给新建项目按钮", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(response(capabilities));
    fetchMock.mockResolvedValueOnce(response([]));
    const user = userEvent.setup();

    render(<App />);
    const newProject = await screen.findByRole("button", { name: "新建复刻项目" });
    await user.click(newProject);
    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(newProject).toHaveFocus();
  });
});
