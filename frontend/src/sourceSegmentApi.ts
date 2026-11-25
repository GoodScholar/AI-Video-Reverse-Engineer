import { readApiError } from "./referenceMediaApi";

export type SourceSegmentStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type SourceSegmentStaleReason = "source_missing" | "source_changed" | "algorithm_changed" | "keyframe_model_changed";

export type SourceSegmentCandidate = {
  id: string;
  assetId: string;
  sourceFingerprint: string;
  algorithmVersion: string;
  keyframeModelVersion: string;
  inPointSeconds: number;
  outPointSeconds: number;
  keyframe: { timeSeconds: number; url: string };
  preview: { url: string; startSeconds: number; endSeconds: number };
};

export type SourceSegmentIndex = {
  assetId: string;
  runId: string;
  status: SourceSegmentStatus;
  algorithmVersion?: string;
  keyframeModelVersion?: string;
  candidateCount: number;
  validity: { status: "current" | "stale"; reason: SourceSegmentStaleReason | null };
  error?: { code: string; message: string; retryable: boolean } | null;
  candidates: SourceSegmentCandidate[];
};

const intentHeaders = { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" };

function baseUrl(projectId: string) {
  return `/api/projects/${encodeURIComponent(projectId)}/source-segments`;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。");
  }
  if (!response.ok) throw new Error(await readApiError(response, "片段索引操作失败，请重试。"));
  return response.json() as Promise<T>;
}

export async function listSourceSegmentIndexes(projectId: string): Promise<SourceSegmentIndex[]> {
  return (await request<{ indexes: SourceSegmentIndex[] }>(baseUrl(projectId))).indexes;
}

export async function startSourceSegmentIndexes(projectId: string, assetIds: string[]): Promise<SourceSegmentIndex[]> {
  return (await request<{ indexes: SourceSegmentIndex[] }>(baseUrl(projectId), {
    method: "POST", headers: intentHeaders, body: JSON.stringify({ assetIds }),
  })).indexes;
}

export function cancelSourceSegmentIndex(projectId: string, assetId: string): Promise<SourceSegmentIndex> {
  return request(`${baseUrl(projectId)}/assets/${encodeURIComponent(assetId)}/cancel`, {
    method: "POST", headers: intentHeaders, body: "{}",
  });
}

export function retrySourceSegmentIndex(projectId: string, assetId: string): Promise<SourceSegmentIndex> {
  return request(`${baseUrl(projectId)}/assets/${encodeURIComponent(assetId)}/retry`, {
    method: "POST", headers: intentHeaders, body: "{}",
  });
}
