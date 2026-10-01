import { readApiError } from "./referenceMediaApi";
import type { GenerationExecutionSemantics, GenerationRoute, GenerationRouteAttempt, GenerationRoutingPolicyRef } from "./generationRoutingApi";

export type ImageGenerationProvider = "openai-compatible" | "volcengine-ark";
export type ImageGenerationCapabilities = { imageSizes?: string[]; imageRatios?: string[]; maxReferences?: number };
export type ImageGenerationModel = { modelId: string; label?: string; capabilities?: ImageGenerationCapabilities; configured?: boolean };
export type ImageGenerationProviderEntry = {
  providerId: string; protocol: ImageGenerationProvider; baseUrl: string; defaultModelId: string; models: ImageGenerationModel[];
  credentialState: "configured" | "unconfigured";
};
export type ImageGenerationModelCatalogEntry = {
  providerId: string; modelId: string; mediaType: "image"; label: string; configured: boolean;
  capabilitySource: "verified" | "user-configured" | "unverified-user-claim" | "unknown"; capabilityVersion: string | null; capabilities: ImageGenerationCapabilities;
};
export type ImageGenerationConfiguration = {
  baseUrl: string | null; model: string | null; credentialState: "configured" | "unconfigured"; revision: string | null;
  provider?: ImageGenerationProvider | null; models?: string[];
  protocolCapabilities?: { referenceImages: "single" | "multiple"; sizeMode: string; sizePattern?: string } | null;
  configVersion?: string | null; activeProviderId?: string | null; providers?: ImageGenerationProviderEntry[]; modelCatalog?: ImageGenerationModelCatalogEntry[];
};
export type ImageGenerationProviderInput = Omit<ImageGenerationProviderEntry, "credentialState" | "models"> & { models: Array<Omit<ImageGenerationModel, "configured">>; apiKey?: string };
export type ImageGenerationConfigurationInput = {
  // Retained for the backend's old single-provider shape.
  baseUrl?: string | null; model?: string | null; provider?: ImageGenerationProvider; models?: string[]; apiKey?: string;
  // New multi-provider shape: the list is atomically replaced under revision CAS.
  providers?: ImageGenerationProviderInput[]; activeProviderId?: string; revision?: string | null;
};
const endpoint = "/api/image-generation/configuration";
async function request<T>(init: RequestInit | undefined, fallback: string): Promise<T> {
  let response: Response;
  try { response = init ? await fetch(endpoint, init) : await fetch(endpoint); } catch { throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。"); }
  if (!response.ok) {
    const error = new Error(await readApiError(response, fallback)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}
export function getImageGenerationConfiguration() { return request<ImageGenerationConfiguration>(undefined, "无法读取图片生成配置，请重试。"); }
export function saveImageGenerationConfiguration(input: ImageGenerationConfigurationInput) { return request<ImageGenerationConfiguration>({ method: "PUT", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: JSON.stringify(input) }, "无法保存图片生成配置，请重试。"); }

export type VerifiedImageCapabilities = {
  version: string; referenceTypes: Array<"reference" | "edit-source" | "upscale-source">;
  minReferences: number; maxReferences: number; imageEditing: boolean; superResolution: boolean;
  aspectRatios: string[]; resolutions: string[]; minResults: number; maxResults: number;
  cancel: boolean; timeout: boolean;
};
export type ImageCapabilityCatalog = {
  adapterId: string; adapterVersion: string; provider: string; model: string; configurationVersion: string;
  capabilities: VerifiedImageCapabilities;
};
export type ImageGenerationReference = {
  assetId: string; assetVersionId: string; referenceType: "reference" | "edit-source" | "upscale-source";
};
export type ImageGenerationOutput = {
  index: number; resultId: string; status: "pending" | "completed" | "reused" | "failed" | "cancelled" | "timed_out";
  reason?: string; assetId?: string; assetVersionId?: string; url?: string; name?: string;
};
export type ImageGenerationRun = {
  id: string; status: string; outcome?: string | null; reason?: string | null; provider: string; model: string;
  outputItems: ImageGenerationOutput[]; retryOf?: string; billingUnknown?: boolean; capabilitySnapshot?: VerifiedImageCapabilities;
  actualRoute?: GenerationRoute; routeAttempts?: GenerationRouteAttempt[]; executionSemantics?: GenerationExecutionSemantics;
};

const projectEndpoint = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/image-generation`;
async function projectRequest<T>(url: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  let response: Response;
  try { response = await fetch(url, init); } catch { throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。"); }
  if (!response.ok) {
    const error = new Error(await readApiError(response, fallback)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}
export function getVerifiedImageCapabilities(projectId: string, provider: string, model: string) {
  const query = `provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`;
  return projectRequest<ImageCapabilityCatalog>(`${projectEndpoint(projectId)}/capabilities?${query}`, undefined, "无法读取图片模型能力。");
}
export function listImageGenerationRuns(projectId: string) {
  return projectRequest<{ runs: ImageGenerationRun[] }>(`${projectEndpoint(projectId)}/runs`, undefined, "无法读取图片生成运行。");
}
export function submitImageGeneration(projectId: string, input: {
  disclosure: { provider: string; model: string; prompt: string; accepted: true };
  imageParams: { operation: "generate" | "edit" | "super-resolution"; references: ImageGenerationReference[]; aspectRatio: string; resolution: string; resultCount: number; timeoutSeconds?: number };
  idempotencyKey: string; caller: string; routingPolicyRef?: GenerationRoutingPolicyRef;
}) {
  return projectRequest<{ run: ImageGenerationRun }>(`${projectEndpoint(projectId)}/runs`, {
    method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: JSON.stringify(input),
  }, "无法提交图片生成。");
}
export function cancelImageGeneration(projectId: string, runId: string) {
  return projectRequest<{ run: ImageGenerationRun }>(`${projectEndpoint(projectId)}/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" },
  }, "无法取消图片生成。");
}
export function retryImageGeneration(projectId: string, runId: string, idempotencyKey: string) {
  return projectRequest<{ run: ImageGenerationRun }>(`${projectEndpoint(projectId)}/runs/${encodeURIComponent(runId)}/retry`, {
    method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: JSON.stringify({ idempotencyKey }),
  }, "无法重试图片生成。");
}
