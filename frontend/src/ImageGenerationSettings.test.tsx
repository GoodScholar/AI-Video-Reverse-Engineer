import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ImageGenerationSettings } from "./ImageGenerationSettings";

const configured = {
  baseUrl: "https://ark.cn-beijing.volces.com/api/v3", provider: "volcengine-ark", model: "seed-image", models: ["seed-image"], credentialState: "configured", revision: "r1", configVersion: "r1", activeProviderId: "ark-main",
  providers: [
    { providerId: "ark-main", protocol: "volcengine-ark", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", defaultModelId: "seed-image", credentialState: "configured", models: [{ modelId: "seed-image", label: "Seed Image", capabilities: { imageSizes: ["1024x1024"], imageRatios: ["1:1"], maxReferences: 4 }, configured: true }] },
    { providerId: "openai-lab", protocol: "openai-compatible", baseUrl: "https://images.example/v1", defaultModelId: "gpt-image", credentialState: "unconfigured", models: [{ modelId: "gpt-image", configured: true }] },
  ],
  modelCatalog: [{ providerId: "ark-main", modelId: "seed-image", mediaType: "image", label: "Seed Image", configured: true, capabilitySource: "user-configured", capabilityVersion: "r1", capabilities: { imageSizes: ["1024x1024"], imageRatios: ["1:1"], maxReferences: 4 } }, { providerId: "openai-lab", modelId: "gpt-image", mediaType: "image", label: "gpt-image", configured: true, capabilitySource: "unknown", capabilityVersion: "r1", capabilities: {} }],
};

afterEach(() => vi.restoreAllMocks());

it("读取活动供应商与已核对的用户能力，整表保存时不回显旧密钥", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(configured), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify(configured), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock); const user = userEvent.setup();
  render(<ImageGenerationSettings />);
  expect(await screen.findByLabelText("活动图片供应商")).toHaveValue("ark-main");
  expect(screen.getByText("能力来源：用户配置")).toBeVisible();
  expect(screen.getByLabelText("ark-main seed-image 可用尺寸")).toHaveValue("1024x1024");
  expect(screen.getByLabelText("ark-main API Key")).toHaveValue("");

  await user.click(screen.getByRole("button", { name: "保存图片生成配置" }));
  const body = JSON.parse(fetchMock.mock.calls[1][1].body);
  expect(body).toMatchObject({ activeProviderId: "ark-main", revision: "r1", providers: [{ providerId: "ark-main", defaultModelId: "seed-image", models: [{ modelId: "seed-image", capabilities: { maxReferences: 4 } }] }, { providerId: "openai-lab" }] });
  expect(JSON.stringify(body)).not.toContain("apiKey");
});

it("添加供应商时单独提交密钥和模型能力，未知能力不伪造为预设", async () => {
  const oneProvider = { ...configured, providers: [configured.providers[0]], modelCatalog: [configured.modelCatalog[0]] };
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(oneProvider), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify(oneProvider), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock); const user = userEvent.setup();
  render(<ImageGenerationSettings />);
  await screen.findByText("能力来源：用户配置");
  await user.click(screen.getByRole("button", { name: "添加供应商" }));
  expect(screen.getByText(/能力来源：未知（画布不会预设此模型的尺寸、比例或参考图限制）/)).toBeVisible();
  await user.clear(screen.getByLabelText("图片服务 Base URL")); await user.type(screen.getByLabelText("图片服务 Base URL"), "https://new.example/v1");
  await user.type(screen.getByLabelText("provider-2 API Key"), "new-secret");
  fireEvent.change(screen.getByLabelText("provider-2 image-model 可用尺寸"), { target: { value: "768x768\n1024x1024" } });
  await user.clear(screen.getByLabelText("provider-2 image-model 最大参考图")); await user.type(screen.getByLabelText("provider-2 image-model 最大参考图"), "2");
  await user.click(screen.getByRole("button", { name: "保存图片生成配置" }));
  const body = JSON.parse(fetchMock.mock.calls[1][1].body);
  expect(body.providers).toEqual(expect.arrayContaining([expect.objectContaining({ providerId: "provider-2", apiKey: "new-secret", models: [expect.objectContaining({ modelId: "image-model", capabilities: { imageSizes: ["768x768", "1024x1024"], maxReferences: 2 } })] })]));
});

it("旧单条响应会映射为默认供应商并以新整表形状保存", async () => {
  const legacy = { baseUrl: "https://images.example/v1", model: "legacy-image", provider: "openai-compatible", models: ["legacy-image"], credentialState: "configured", revision: "legacy-1" };
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(legacy), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify(legacy), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock); const user = userEvent.setup();
  render(<ImageGenerationSettings />);
  expect(await screen.findByLabelText("活动图片供应商")).toHaveValue("default");
  await user.click(screen.getByRole("button", { name: "保存图片生成配置" }));
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ activeProviderId: "default", providers: [{ providerId: "default", protocol: "openai-compatible", defaultModelId: "legacy-image" }], revision: "legacy-1" });
});
