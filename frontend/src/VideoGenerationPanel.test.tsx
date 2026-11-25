import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { VideoGenerationPanel } from "./VideoGenerationPanel";


const capabilities = {
  version: "video-v1",
  profiles: [
    {
      id: "t2v-main", mode: "text-to-video" as const, referencePatterns: [[]],
      combinations: [{ durationSeconds: 5, aspectRatio: "16:9", resolution: "1280x720", audio: false }],
      minResults: 1, maxResults: 2,
    },
    {
      id: "i2v-main", mode: "image-to-video" as const,
      referencePatterns: [["first-frame" as const], ["first-frame" as const, "last-frame" as const], ["reference" as const, "reference" as const]],
      combinations: [{ durationSeconds: 6, aspectRatio: "9:16", resolution: "720x1280", audio: true }],
      minResults: 1, maxResults: 1,
    },
  ],
  cancel: true,
};

const model = {
  provider: "verified-video", model: "cinema-1", label: "已验证视频模型",
  adapterId: "video-adapter", adapterVersion: "v1", configurationVersion: "cfg-1", capabilities,
};
const routingModel = { ...model, provider: "backup-video", model: "cinema-backup", label: "备用已验证视频模型" };

const assets = [
  { id: "asset-1", assetVersionId: "asset-1-v1", name: "商品首帧.png", kind: "image" as const, url: "/asset-1" },
  { id: "asset-2", assetVersionId: "asset-2-v1", name: "商品尾帧.png", kind: "image" as const, url: "/asset-2" },
];

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

it("没有服务端验证的视频模型时不显示未知组合或提交入口", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/models")) return new Response(JSON.stringify({ models: [] }));
    if (url.endsWith("/runs")) return new Response(JSON.stringify({ runs: [] }));
    throw new Error(`unexpected ${url}`);
  }));

  render(<VideoGenerationPanel projectId="project-1" imageAssets={assets} />);

  expect(await screen.findByText("当前没有已通过异步能力验证的视频模型。连接 Provider 不等于该模型组合已验证。")).toBeVisible();
  expect(screen.queryByRole("button", { name: "提交视频生成" })).not.toBeInTheDocument();
});

it("未配置的 ComfyUI 只显示为可选待验证，不显示为已支持模型", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/models")) return new Response(JSON.stringify({ models: [] }));
    if (url.endsWith("/runs")) return new Response(JSON.stringify({ runs: [] }));
    if (url === "/api/comfyui-adapter/status") return new Response(JSON.stringify({ state: "unconfigured", workflowVerified: false }));
    throw new Error(`unexpected ${url}`);
  }));

  render(<VideoGenerationPanel projectId="project-1" imageAssets={assets} />);

  expect(await screen.findByText("ComfyUI 为可选本地适配器：尚未配置，未纳入已支持视频模型。")).toBeVisible();
  expect(screen.queryByRole("option", { name: /ComfyUI/ })).not.toBeInTheDocument();
});

it("只用目录中的图生视频首尾帧与精确参数组合提交", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/models")) return new Response(JSON.stringify({ models: [model, routingModel] }));
    if (url.endsWith("/runs") && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ runs: [] }));
    if (url === "/api/generation-routing-policies?modality=video") return new Response(JSON.stringify({ revision: 0, policies: [] }));
    if (url === "/api/generation-routing-policies/video-routing-policy" && init?.method === "PUT") return new Response(JSON.stringify({ policy: {
      id: "video-routing-policy", name: "视频备用", modality: "video", revision: 1,
      routes: [{ provider: "backup-video", model: "cinema-backup" }, { provider: "verified-video", model: "cinema-1" }],
    } }));
    if (url.endsWith("/runs") && init?.method === "POST") return new Response(JSON.stringify({ run: {
      id: "run-1", status: "running", outcome: null, provider: "verified-video", model: "cinema-1",
      lastConfirmedRemoteStatus: "queued", localDisposition: "active", remoteCancelResult: null,
      billingUnknown: false, capabilitySnapshot: capabilities,
      outputItems: [{ index: 0, resultId: "result-1", status: "pending" }],
    } }), { status: 202 });
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<VideoGenerationPanel projectId="project-1" imageAssets={assets} />);

  expect(await screen.findByRole("option", { name: "已验证视频模型" })).toBeVisible();
  await user.selectOptions(screen.getByLabelText("视频模型"), JSON.stringify(["backup-video", "cinema-backup"]));
  await user.selectOptions(screen.getByLabelText("生成档位"), "i2v-main");
  await user.selectOptions(screen.getByLabelText("参考图组合"), JSON.stringify(["first-frame", "last-frame"]));
  await user.selectOptions(screen.getByLabelText("首帧"), "asset-1");
  await user.selectOptions(screen.getByLabelText("尾帧"), "asset-2");
  await user.type(screen.getByLabelText("视频提示词"), "商品从首帧平滑旋转到尾帧");
  await user.click(screen.getByRole("checkbox", { name: "确认发送提示词和所选图片" }));
  await user.click(screen.getByRole("button", { name: "读取已保存策略" }));
  await user.selectOptions(screen.getByLabelText("备用路由（可选）"), "verified-video\u0000cinema-1");
  await user.click(screen.getByRole("button", { name: "保存当前顺序" }));

  expect(screen.getAllByText("6 秒 · 9:16 · 720x1280 · 含音频")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "提交视频生成" }));

  await screen.findByText("远端等待中");
  const request = fetchMock.mock.calls.find(call => String(call[0]).endsWith("/runs") && call[1]?.method === "POST");
  const body = JSON.parse(String(request?.[1]?.body));
  expect(body).toMatchObject({
    disclosure: { provider: "backup-video", model: "cinema-backup", prompt: "商品从首帧平滑旋转到尾帧", accepted: true },
    videoParams: {
      profileId: "i2v-main", mode: "image-to-video", durationSeconds: 6, aspectRatio: "9:16",
      resolution: "720x1280", audio: true, resultCount: 1,
      references: [
        { assetId: "asset-1", assetVersionId: "asset-1-v1", referenceType: "first-frame" },
        { assetId: "asset-2", assetVersionId: "asset-2-v1", referenceType: "last-frame" },
      ],
    },
    routingPolicyRef: { id: "video-routing-policy", revision: 1 },
  });
  expect(JSON.stringify(body)).not.toContain("apiKey");
});

it("页面恢复后继续查询远端任务并明确显示本地停止、取消未知、迟到成功和计费未知", async () => {
  const activeRun = {
    id: "run-active", status: "running", outcome: null, provider: "verified-video", model: "cinema-1",
    lastConfirmedRemoteStatus: "unknown", localDisposition: "active", remoteCancelResult: "unknown",
    billingUnknown: true, capabilitySnapshot: capabilities,
    outputItems: [{ index: 0, resultId: "result-active", status: "pending" }],
  };
  const stoppedRun = {
    ...activeRun, id: "run-stopped", localDisposition: "stopped", remoteCancelResult: null,
    outputItems: [{ index: 0, resultId: "result-stopped", status: "pending" }],
  };
  const lateRun = {
    ...stoppedRun, id: "run-late", status: "completed", outcome: "late_success", lastConfirmedRemoteStatus: "succeeded",
    billingUnknown: false,
    outputItems: [{ index: 0, resultId: "result-late", status: "completed", url: "/late.mp4", name: "迟到.mp4" }],
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/models")) return new Response(JSON.stringify({ models: [model] }));
    if (url.endsWith("/runs") && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ runs: [activeRun, stoppedRun, lateRun] }));
    if (url.endsWith("/run-active/refresh") && init?.method === "POST") return new Response(JSON.stringify({ run: activeRun }));
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<VideoGenerationPanel projectId="project-1" imageAssets={assets} />);

  expect(await screen.findByText("远端状态未知")).toBeVisible();
  expect(screen.getByText("远端取消结果未知")).toBeVisible();
  expect(screen.getAllByText("计费状态未知")).toHaveLength(2);
  expect(screen.getByText("本地已停止轮询，远端任务未被视为已取消")).toBeVisible();
  expect(screen.getByText("迟到成功（未自动采用）")).toBeVisible();
  expect(screen.getByLabelText("预览迟到.mp4")).toHaveAttribute("src", "/late.mp4");
  expect(screen.getAllByRole("button", { name: "请求远端取消" })).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "仅停止本地轮询" })).toHaveLength(1);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/projects/project-1/video-generation/runs/run-active/refresh",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(fetchMock.mock.calls.some(call => String(call[0]).includes("run-stopped/refresh"))).toBe(false);
});

it("同一运行的自动恢复与手动查询复用同一个在途请求", async () => {
  const activeRun = {
    id: "run-active", status: "running", outcome: null, provider: "verified-video", model: "cinema-1",
    lastConfirmedRemoteStatus: "running", localDisposition: "active", remoteCancelResult: null,
    billingUnknown: false, capabilitySnapshot: capabilities,
    outputItems: [{ index: 0, resultId: "result-active", status: "pending", retryable: false }],
  };
  let resolveRefresh!: (response: Response) => void;
  const refreshResponse = new Promise<Response>((resolve) => { resolveRefresh = resolve; });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/models")) return new Response(JSON.stringify({ models: [model] }));
    if (url.endsWith("/runs") && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ runs: [activeRun] }));
    if (url.endsWith("/run-active/refresh") && init?.method === "POST") return refreshResponse;
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<VideoGenerationPanel projectId="project-1" imageAssets={assets} />);

  const query = await screen.findByRole("button", { name: "查询远端" });
  await waitFor(() => expect(fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/run-active/refresh"))).toHaveLength(1));
  await user.click(query);
  expect(fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/run-active/refresh"))).toHaveLength(1);
  resolveRefresh(new Response(JSON.stringify({ run: activeRun })));
  await waitFor(() => expect(screen.getByText("远端处理中")).toBeVisible());
});
