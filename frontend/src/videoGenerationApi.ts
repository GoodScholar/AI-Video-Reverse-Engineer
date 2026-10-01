import { readApiError } from "./referenceMediaApi";


export type VideoReferenceType = "first-frame" | "last-frame" | "reference";
export type VideoGenerationCombination = {
  durationSeconds: number; aspectRatio: string; resolution: string; audio: boolean;
};
export type VideoGenerationProfile = {
  id: string; mode: "text-to-video" | "image-to-video"; referencePatterns: VideoReferenceType[][];
  combinations: VideoGenerationCombination[]; minResults: number; maxResults: number;
};
export type VideoGenerationCapabilities = {
  version: string; profiles: VideoGenerationProfile[]; cancel: boolean;
};
export type VideoGenerationModel = {
  provider: string; model: string; label: string; adapterId: string; adapterVersion: string;
  configurationVersion: string; capabilities: VideoGenerationCapabilities;
};
export type VideoGenerationReference = {
  assetId: string; assetVersionId: string; referenceType: VideoReferenceType;
};
export type VideoGenerationOutput = {
  index: number; resultId: string; retryable: boolean;
  status: "pending" | "completed" | "reused" | "failed" | "cancelled" | "unknown";
  reason?: string; billingUnknown?: boolean; assetId?: string; assetVersionId?: string;
  url?: string; name?: string; duration?: number; width?: number; height?: number;
};
export type VideoGenerationRun = {
  id: string; status: string; outcome?: string | null; reason?: string | null;
  provider: string; model: string; lastConfirmedRemoteStatus?: string | null;
  localDisposition: "active" | "stopped"; remoteCancelResult?: "cancelled" | "unsupported" | "unknown" | null;
  billingUnknown: boolean; capabilitySnapshot: VideoGenerationCapabilities; outputItems: VideoGenerationOutput[];
  retryOf?: string;
};

const endpoint = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/video-generation`;

async function request<T>(url: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  let response: Response;
  try { response = await fetch(url, init); } catch { throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。"); }
  if (!response.ok) {
    const error = new Error(await readApiError(response, fallback)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}

const mutation = (body?: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export function listVideoGenerationModels(projectId: string) {
  return request<{ models: VideoGenerationModel[] }>(`${endpoint(projectId)}/models`, undefined, "无法读取视频模型目录。");
}

export function listVideoGenerationRuns(projectId: string) {
  return request<{ runs: VideoGenerationRun[] }>(`${endpoint(projectId)}/runs`, undefined, "无法读取视频生成运行。");
}

export function submitVideoGeneration(projectId: string, input: {
  disclosure: { provider: string; model: string; prompt: string; accepted: true };
  videoParams: {
    profileId: string; mode: "text-to-video" | "image-to-video"; references: VideoGenerationReference[];
    durationSeconds: number; aspectRatio: string; resolution: string; audio: boolean; resultCount: number;
  };
  idempotencyKey: string; caller: string;
}) {
  return request<{ run: VideoGenerationRun }>(`${endpoint(projectId)}/runs`, mutation(input), "无法提交视频生成。");
}

export function refreshVideoGeneration(projectId: string, runId: string) {
  return request<{ run: VideoGenerationRun }>(`${endpoint(projectId)}/runs/${encodeURIComponent(runId)}/refresh`, mutation(), "无法查询远端视频任务。");
}

export function stopVideoGenerationLocally(projectId: string, runId: string) {
  return request<{ run: VideoGenerationRun }>(`${endpoint(projectId)}/runs/${encodeURIComponent(runId)}/stop-local`, mutation(), "无法停止本地轮询。");
}

export function cancelVideoGenerationRemotely(projectId: string, runId: string) {
  return request<{ run: VideoGenerationRun }>(`${endpoint(projectId)}/runs/${encodeURIComponent(runId)}/cancel-remote`, mutation(), "无法请求远端取消。");
}

export function retryVideoGenerationDownload(projectId: string, runId: string, idempotencyKey: string) {
  return request<{ run: VideoGenerationRun }>(`${endpoint(projectId)}/runs/${encodeURIComponent(runId)}/retry`, mutation({ idempotencyKey }), "无法重试视频结果下载。");
}
