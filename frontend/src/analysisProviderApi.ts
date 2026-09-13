import type { AnalysisProviderConfiguration, AnalysisProviderId, Project } from "./models";
import { readApiError } from "./referenceMediaApi";

const CONNECTION_ERROR = "无法连接本地服务，请确认应用服务正在运行后重试。";
const ANALYSIS_INTENT_HEADERS = {
  "Content-Type": "application/json",
  "X-AIVRE-Intent": "semantic-analysis",
};

export type AnalysisProviderConfigurationInput = {
  apiKey?: string;
  baseUrl?: string;
  model: string;
};

async function request<T>(url: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  let response: Response;
  try {
    response = init === undefined ? await fetch(url) : await fetch(url, init);
  } catch {
    throw new Error(CONNECTION_ERROR);
  }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export function listAnalysisProviders(): Promise<AnalysisProviderConfiguration[]> {
  return request("/api/analysis-providers", undefined, "无法读取分析服务设置，请重试。");
}

export function saveAnalysisProviderConfiguration(
  provider: AnalysisProviderId,
  configuration: AnalysisProviderConfigurationInput,
): Promise<AnalysisProviderConfiguration> {
  return request(
    `/api/analysis-providers/${encodeURIComponent(provider)}/configuration`,
    { method: "PUT", headers: ANALYSIS_INTENT_HEADERS, body: JSON.stringify(configuration) },
    "无法保存分析服务设置，请重试。",
  );
}

export function testAnalysisProviderConnection(provider: AnalysisProviderId): Promise<{ provider: AnalysisProviderId; model: string; status: "connected" }> {
  return request(
    `/api/analysis-providers/${encodeURIComponent(provider)}/test-connection`,
    { method: "POST", headers: ANALYSIS_INTENT_HEADERS, body: "{}" },
    "无法测试分析服务连接，请重试。",
  );
}

export function startSemanticAnalysis(projectId: string, provider: AnalysisProviderId, model: string): Promise<Project> {
  return request(
    `/api/projects/${encodeURIComponent(projectId)}/semantic-analysis`,
    { method: "POST", headers: ANALYSIS_INTENT_HEADERS, body: JSON.stringify({ provider, model, disclosureAccepted: true }) },
    "无法启动语义分析，请重试。",
  );
}
