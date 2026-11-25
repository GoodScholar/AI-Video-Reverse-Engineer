import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CreativeCanvasGenerationSettings, generationCapabilityStatus } from "./CreativeCanvasGenerationSettings";
import type { CreativeCanvasNode } from "./creativeCanvasApi";
import type { ImageGenerationModelCatalogEntry, ImageGenerationProviderEntry } from "./imageGenerationApi";

const catalog: ImageGenerationModelCatalogEntry[] = [
  { providerId: "ark", modelId: "seed", mediaType: "image", label: "Seed Image", configured: true, capabilitySource: "user-configured", capabilityVersion: "c1", capabilities: { imageSizes: ["1024x1024"], imageRatios: ["1:1"], maxReferences: 1 } },
  { providerId: "ark", modelId: "unknown", mediaType: "image", label: "Unknown Image", configured: true, capabilitySource: "unknown", capabilityVersion: "c1", capabilities: {} },
];
const providers: Pick<ImageGenerationProviderEntry, "providerId" | "credentialState">[] = [{ providerId: "ark", credentialState: "configured" }];
const stableParams: NonNullable<CreativeCanvasNode["params"]> = { providerId: "ark", modelId: "seed", configurationVersion: "c1", size: "1024x1024", ratio: "1:1" };

function settings(params = stableParams, referenceCount = 0, onEdit = vi.fn()) {
  return render(<div className="creative-canvas"><CreativeCanvasGenerationSettings params={params} catalog={catalog} providers={providers} configurationVersion="c1" referenceCount={referenceCount} onEdit={onEdit} /></div>);
}

describe("CreativeCanvasGenerationSettings", () => {
  it("只以 providerId/modelId 的用户声明能力驱动尺寸、比例与图片引用上限", async () => {
    const onEdit = vi.fn();
    settings(stableParams, 0, onEdit);
    expect(generationCapabilityStatus({ params: stableParams, catalog, providers, configurationVersion: "c1", referenceCount: 0 })).toMatchObject({ runnable: true, model: catalog[0] });

    await userEvent.click(screen.getByRole("button", { name: "图片生成设置" }));
    expect(screen.getByRole("button", { name: "1024x1024" })).toBeVisible();
    expect(screen.getByRole("button", { name: "比例 1:1" })).toBeVisible();
    expect(screen.getByText("最多 1 张图片参考")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "比例 1:1" }));
    expect(onEdit).toHaveBeenCalledWith({ ratio: "1:1", size: "1024x1024" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "生成数量" }), "3");
    expect(onEdit).toHaveBeenCalledWith({ count: 3 });
  });

  it("保留切换模型前的历史尺寸和比例，并明确阻止不兼容值提交", () => {
    const historical = { ...stableParams, size: "1536x1024", ratio: "3:2" };
    const status = generationCapabilityStatus({ params: historical, catalog, providers, configurationVersion: "c1", referenceCount: 0 });
    expect(status).toMatchObject({ runnable: false, reason: "当前尺寸 1536x1024 不受所选模型支持，请明确选择兼容尺寸。" });

    settings(historical);
    expect(screen.getByText("当前尺寸 1536x1024 不受所选模型支持，请明确选择兼容尺寸。")).toBeVisible();
    expect(screen.getByRole("button", { name: "图片生成设置" })).toHaveTextContent("3:2 · 1536x1024 · 1张");
  });

  it("能力未知、未配置密钥或配置版本变化时明确不可执行", () => {
    const unknown = { ...stableParams, modelId: "unknown" };
    expect(generationCapabilityStatus({ params: unknown, catalog, providers, configurationVersion: "c1", referenceCount: 0 })).toMatchObject({ runnable: false, reason: "所选模型的能力未知，无法验证尺寸、比例和图片参考。" });
    expect(generationCapabilityStatus({ params: stableParams, catalog, providers, configurationVersion: "c2", referenceCount: 0 })).toMatchObject({ runnable: false, reason: "生成服务配置已更新，请重新选择模型。" });
    expect(generationCapabilityStatus({ params: stableParams, catalog, providers: [{ providerId: "ark", credentialState: "unconfigured" }], configurationVersion: "c1", referenceCount: 0 })).toMatchObject({ runnable: false, reason: "请先为所选供应商配置 API Key。" });
  });

  it("图片引用数超出已声明上限时阻止生成，而未声明上限不会伪造限制", () => {
    expect(generationCapabilityStatus({ params: stableParams, catalog, providers, configurationVersion: "c1", referenceCount: 2 })).toMatchObject({ runnable: false, reason: "图片参考数量超过所选模型上限（1 张）。" });
    const unbounded = [{ ...catalog[0], capabilities: { imageSizes: ["1024x1024"], imageRatios: ["1:1"] } }];
    expect(generationCapabilityStatus({ params: stableParams, catalog: unbounded, providers, configurationVersion: "c1", referenceCount: 20 })).toMatchObject({ runnable: true });
  });

  it("将协议单图上限与模型声明上限取更严格值", () => {
    const openaiProvider: ImageGenerationProviderEntry[] = [{ providerId: "ark", protocol: "openai-compatible", baseUrl: "https://images.example/v1", defaultModelId: "seed", credentialState: "configured", models: [{ modelId: "seed" }] }];
    const twoReferences = [{ ...catalog[0], capabilities: { ...catalog[0].capabilities, maxReferences: 2 } }];
    expect(generationCapabilityStatus({ params: stableParams, catalog: twoReferences, providers: openaiProvider, configurationVersion: "c1", referenceCount: 2 })).toMatchObject({ runnable: false, reason: "图片参考数量超过所选模型上限（1 张）。" });
  });

  it("图片引用声明为图片但素材已缺失时，在提交前指出精确引用", () => {
    expect(generationCapabilityStatus({ params: stableParams, catalog, providers, configurationVersion: "c1", referenceCount: 1, missingImageReference: { index: 2, sourceLabel: "商品主图" } })).toMatchObject({ runnable: false, reason: "第 2 个图片参考（商品主图）不可用，请恢复素材或移除引用。" });
  });

  it("旧 model 仅在活动供应商的默认模型、白名单和密钥有效时沿用协议边界", () => {
    const legacy = { model: "legacy-image", size: "1024x1024", ratio: "1:1" };
    const legacyProviders: ImageGenerationProviderEntry[] = [{ providerId: "default", protocol: "openai-compatible", baseUrl: "https://images.example/v1", defaultModelId: "legacy-image", credentialState: "configured", models: [{ modelId: "legacy-image" }] }];
    expect(generationCapabilityStatus({ params: legacy, providers: legacyProviders, activeProviderId: "ark", referenceCount: 1 })).toMatchObject({ runnable: true, legacy: true, notice: "旧模型，能力未核验，仅按协议及应用边界运行。" });
    expect(generationCapabilityStatus({ params: legacy, providers: legacyProviders, activeProviderId: "ark", referenceCount: 2 })).toMatchObject({ runnable: false, reason: "当前旧协议最多支持 1 张图片参考。" });
    expect(generationCapabilityStatus({ params: legacy, providers: [{ ...legacyProviders[0], credentialState: "unconfigured" }], activeProviderId: "default", referenceCount: 1 })).toMatchObject({ runnable: false, reason: "旧模型当前不可用，请在生成服务设置中检查默认模型、白名单和密钥。" });
  });

  it("旧 model 严格读取 default 供应商，不因 activeProviderId 同名模型跨供应商放行", () => {
    const legacy = { model: "same-name", size: "1024x1024", ratio: "1:1" };
    const configuredDefault: ImageGenerationProviderEntry = { providerId: "default", protocol: "openai-compatible", baseUrl: "https://default.example/v1", defaultModelId: "same-name", credentialState: "configured", models: [{ modelId: "same-name" }] };
    const configuredArk: ImageGenerationProviderEntry = { providerId: "ark", protocol: "volcengine-ark", baseUrl: "https://ark.example/v1", defaultModelId: "same-name", credentialState: "configured", models: [{ modelId: "same-name" }] };
    expect(generationCapabilityStatus({ params: legacy, providers: [{ ...configuredDefault, credentialState: "unconfigured" }, configuredArk], activeProviderId: "ark", referenceCount: 0 })).toMatchObject({ runnable: false, reason: "旧模型当前不可用，请在生成服务设置中检查默认模型、白名单和密钥。" });
    expect(generationCapabilityStatus({ params: legacy, providers: [configuredDefault, { ...configuredArk, credentialState: "unconfigured" }], activeProviderId: "ark", referenceCount: 0 })).toMatchObject({ runnable: true, legacy: true });
  });

  it("拒绝目录中仍可能存在的越界尺寸和尺寸比例不一致组合", () => {
    expect(generationCapabilityStatus({ params: { ...stableParams, size: "8192x8192" }, catalog, providers, configurationVersion: "c1", referenceCount: 0 })).toMatchObject({ runnable: false, reason: "图片尺寸必须为 64–8192 像素且总像素不超过 6400 万。" });
    expect(generationCapabilityStatus({ params: { ...stableParams, ratio: "16:9" }, catalog: [{ ...catalog[0], capabilities: { imageSizes: ["1024x1024"], imageRatios: ["1:1", "16:9"] } }], providers, configurationVersion: "c1", referenceCount: 0 })).toMatchObject({ runnable: false, reason: "当前尺寸 1024x1024 与比例 16:9 不一致，请明确选择兼容尺寸或比例。" });
    expect(generationCapabilityStatus({ params: { ...stableParams, count: 16 }, catalog, providers, configurationVersion: "c1", referenceCount: 0 })).toMatchObject({ runnable: false, reason: "生成数量必须为 1–15 张。" });
  });
});
