import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  listAnalysisProviders,
  saveAnalysisProviderConfiguration,
  startSemanticAnalysis,
  testAnalysisProviderConnection,
} from "./analysisProviderApi";

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("analysisProviderApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("读取后端公开的供应商目录", async () => {
    const providers = [{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }];
    vi.mocked(fetch).mockResolvedValue(response(providers));

    await expect(listAnalysisProviders()).resolves.toEqual(providers);
    expect(fetch).toHaveBeenCalledWith("/api/analysis-providers");
  });

  it("保存时只将本次表单中的密钥写入配置请求", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ provider: "local_openai_compatible", model: "vision-local", baseUrl: "http://127.0.0.1:8080", credentialState: "unconfigured", selectedProvider: "local_openai_compatible" }));

    await saveAnalysisProviderConfiguration("local_openai_compatible", {
      apiKey: "one-shot-secret",
      baseUrl: "http://127.0.0.1:8080",
      model: "vision-local",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/analysis-providers/local_openai_compatible/configuration",
      expect.objectContaining({
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" },
        body: JSON.stringify({ apiKey: "one-shot-secret", baseUrl: "http://127.0.0.1:8080", model: "vision-local" }),
      }),
    );
  });

  it("将连接测试的结构化错误交给界面说明", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ detail: { code: "provider_error", message: "本地服务未响应" } }, 502));

    await expect(testAnalysisProviderConnection("bailian")).rejects.toThrow("本地服务未响应");
  });

  it("所有可能读凭据或触发外发的请求都声明 JSON 意图", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(response({ provider: "bailian", model: "qwen3.7-flash", status: "connected" }))
      .mockResolvedValueOnce(response({ id: "project-001" }));

    await testAnalysisProviderConnection("bailian");
    await startSemanticAnalysis("project-001", "bailian", "qwen3.7-flash");

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/analysis-providers/bailian/test-connection", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" },
      body: "{}",
    });
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/projects/project-001/semantic-analysis", expect.objectContaining({
      headers: { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" },
    }));
  });
});
