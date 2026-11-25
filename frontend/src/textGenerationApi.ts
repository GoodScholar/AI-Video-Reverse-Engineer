import { readApiError } from "./referenceMediaApi";

export type TextGenerationModel = {
  providerId: string; providerLabel: string; modelId: string; label: string;
  configured: boolean; configurationVersion: string;
  textVerified: boolean; imageVerified: boolean; maxImageReferences: number;
};
export type TextGenerationConfiguration = {
  providerId: string; baseUrl: string | null; models: Array<{ modelId: string; label?: string }>;
  revision: string | null; credentialState: "configured" | "unconfigured";
  modelCatalog: TextGenerationModel[];
};
export type TextGenerationInput = Pick<TextGenerationConfiguration, "baseUrl" | "models" | "revision"> & { apiKey?: string };

async function request(path: string, init?: RequestInit): Promise<TextGenerationConfiguration> {
  const response = await fetch(`/api/text-generation/${path}`, init);
  if (!response.ok) throw new Error(await readApiError(response, "文本生成服务请求失败，请重试。"));
  return response.json();
}
export const getTextGenerationConfiguration = () => request("configuration");
export const saveTextGenerationConfiguration = (input: TextGenerationInput) => request("configuration", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) });
export const verifyTextGenerationModel = (revision: string, modelId: string, mode: "text" | "image") => request("test-connection", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision, modelId, mode }) });
