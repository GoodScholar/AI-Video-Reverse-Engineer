import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ImageGenerationPanel } from "./ImageGenerationPanel";


const capabilities = {
  adapterId: "fake-image", adapterVersion: "v1", provider: "verified-provider", model: "verified-model",
  configurationVersion: "cfg:probe", capabilities: {
    version: "image-v1", referenceTypes: ["reference", "edit-source"], minReferences: 0, maxReferences: 2,
    imageEditing: true, superResolution: false, aspectRatios: ["1:1", "16:9"],
    resolutions: ["1024x1024", "1536x864"], minResults: 1, maxResults: 3, cancel: true, timeout: true,
  },
};

const configuration = {
  revision: "cfg", credentialState: "configured", baseUrl: null, model: null,
  modelCatalog: [
    { providerId: "verified-provider", modelId: "verified-model", mediaType: "image", label: "已验证图片模型", configured: true, capabilitySource: "verified", capabilityVersion: "probe", capabilities: capabilities.capabilities },
    { providerId: "unknown-provider", modelId: "unknown-model", mediaType: "image", label: "未验证模型", configured: true, capabilitySource: "unknown", capabilityVersion: "cfg", capabilities: {} },
  ],
};

const assets = [
  { id: "asset-1", assetVersionId: "asset-1-v1", name: "商品正面.png", kind: "image" as const, url: "/asset-1" },
  { id: "asset-2", assetVersionId: "asset-2-v1", name: "商品侧面.png", kind: "image" as const, url: "/asset-2" },
];

afterEach(() => vi.unstubAllGlobals());

it("只显示已验证能力，区分模型编辑、模型超分与普通本地处理，并按能力提交多参考多结果", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/image-generation/configuration") return new Response(JSON.stringify(configuration));
    if (url.endsWith("/runs") && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ runs: [] }));
    if (url.includes("/capabilities?")) return new Response(JSON.stringify(capabilities));
    if (url.endsWith("/runs") && init?.method === "POST") return new Response(JSON.stringify({ run: {
      id: "run-1", status: "partial_success", outcome: "partial_success", provider: "verified-provider", model: "verified-model",
      outputItems: [
        { index: 0, resultId: "result-1", status: "completed", assetId: "generated-1", assetVersionId: "generated-1-v1", url: "/generated-1", name: "方案一.png" },
        { index: 1, resultId: "result-2", status: "failed", reason: "provider_rejected" },
      ],
    } }), { status: 202 });
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const installed = vi.fn();
  render(<ImageGenerationPanel projectId="project-1" imageAssets={assets} onInstalled={installed} />);

  expect(await screen.findByRole("option", { name: "已验证图片模型" })).toBeVisible();
  expect(screen.queryByRole("option", { name: "未验证模型" })).not.toBeInTheDocument();
  expect(await screen.findByText("参考图片：0–2 张；结果：1–3 张。比例 1:1、16:9；分辨率 1024x1024、1536x864。")).toBeVisible();
  expect(screen.getByRole("option", { name: "模型图片编辑" })).toBeVisible();
  expect(screen.queryByRole("option", { name: "模型超分辨率" })).not.toBeInTheDocument();
  expect(screen.getByText("普通本地裁切/缩放不会调用模型，也不会显示为模型图片编辑或模型超分辨率。")).toBeVisible();

  const user = userEvent.setup();
  await user.type(screen.getByLabelText("图片提示词"), "为商品制作干净主图");
  await user.click(screen.getByRole("checkbox", { name: "引用商品正面.png" }));
  await user.click(screen.getByRole("checkbox", { name: "引用商品侧面.png" }));
  await user.clear(screen.getByLabelText("结果数量"));
  await user.type(screen.getByLabelText("结果数量"), "2");
  await user.click(screen.getByRole("checkbox", { name: "确认发送提示词和所选图片" }));
  await user.click(screen.getByRole("button", { name: "提交图片生成" }));

  await screen.findByText("部分成功");
  expect(screen.getByRole("link", { name: "预览方案一.png" })).toHaveAttribute("href", "/generated-1");
  expect(screen.getByText("失败 · provider_rejected")).toBeVisible();
  expect(installed).toHaveBeenCalledTimes(1);
  const request = fetchMock.mock.calls.find(call => String(call[0]).endsWith("/runs") && call[1]?.method === "POST");
  const body = JSON.parse(String(request?.[1]?.body));
  expect(body).toMatchObject({
    disclosure: { provider: "verified-provider", model: "verified-model", prompt: "为商品制作干净主图", accepted: true },
    imageParams: {
      operation: "generate", aspectRatio: "1:1", resolution: "1024x1024", resultCount: 2,
      references: [
        { assetId: "asset-1", assetVersionId: "asset-1-v1", referenceType: "reference" },
        { assetId: "asset-2", assetVersionId: "asset-2-v1", referenceType: "reference" },
      ],
    },
  });
  expect(JSON.stringify(body)).not.toContain("apiKey");
});

it("没有已验证图片模型时不展示未知能力或提交入口", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/image-generation/configuration") return new Response(JSON.stringify({ ...configuration, modelCatalog: configuration.modelCatalog.slice(1) }));
    if (url.endsWith("/runs")) return new Response(JSON.stringify({ runs: [] }));
    throw new Error(`unexpected ${url}`);
  }));

  render(<ImageGenerationPanel projectId="project-1" imageAssets={assets} />);

  expect(await screen.findByText("当前没有已通过能力验证的图片模型。保存配置或连接成功不等于模型能力已验证。")).toBeVisible();
  expect(screen.queryByRole("button", { name: "提交图片生成" })).not.toBeInTheDocument();
});

it("提交响应丢失后手动重试复用同一幂等键，成功响应后才清除", async () => {
  const keys: string[] = [];
  let attempts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/image-generation/configuration") return new Response(JSON.stringify(configuration));
    if (url.endsWith("/runs") && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ runs: [] }));
    if (url.includes("/capabilities?")) return new Response(JSON.stringify(capabilities));
    if (url.endsWith("/runs") && init?.method === "POST") {
      keys.push(JSON.parse(String(init.body)).idempotencyKey);
      attempts += 1;
      if (attempts === 1) throw new Error("response lost");
      return new Response(JSON.stringify({ run: {
        id: "run-recovered", status: "completed", outcome: "completed", provider: "verified-provider", model: "verified-model",
        outputItems: [{ index: 0, resultId: "result-recovered", status: "completed", assetId: "generated", assetVersionId: "generated-v1", url: "/generated", name: "恢复.png" }],
      } }), { status: 202 });
    }
    throw new Error(`unexpected ${url}`);
  }));
  const user = userEvent.setup();
  render(<ImageGenerationPanel projectId="project-retry" imageAssets={assets} />);
  await screen.findByRole("option", { name: "已验证图片模型" });
  await user.type(screen.getByLabelText("图片提示词"), "保持幂等");
  await user.click(screen.getByRole("checkbox", { name: "确认发送提示词和所选图片" }));
  await user.click(screen.getByRole("button", { name: "提交图片生成" }));
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "提交图片生成" }));
  await screen.findByRole("link", { name: "预览恢复.png" });

  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
});

it("纯文生图能力不显示或提交参考图", async () => {
  const textOnly = {
    ...capabilities,
    capabilities: {
      ...capabilities.capabilities,
      referenceTypes: [], minReferences: 0, maxReferences: 0,
      imageEditing: false, superResolution: false,
    },
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/image-generation/configuration") return new Response(JSON.stringify(configuration));
    if (url.endsWith("/runs") && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ runs: [] }));
    if (url.includes("/capabilities?")) return new Response(JSON.stringify(textOnly));
    if (url.endsWith("/runs") && init?.method === "POST") return new Response(JSON.stringify({ run: {
      id: "run-text-only", status: "completed", provider: "verified-provider", model: "verified-model", outputItems: [],
    } }), { status: 202 });
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<ImageGenerationPanel projectId="project-text-only" imageAssets={assets} />);
  await screen.findByText("参考图片：0–0 张；结果：1–3 张。比例 1:1、16:9；分辨率 1024x1024、1536x864。");
  expect(screen.queryByRole("checkbox", { name: "引用商品正面.png" })).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("图片提示词"), "只用文字生成");
  await user.click(screen.getByRole("checkbox", { name: "确认发送提示词和所选图片" }));
  await user.click(screen.getByRole("button", { name: "提交图片生成" }));
  const body = JSON.parse(String(fetchMock.mock.calls.find((call) => call[1]?.method === "POST")?.[1]?.body));
  expect(body.imageParams.references).toEqual([]);
});
