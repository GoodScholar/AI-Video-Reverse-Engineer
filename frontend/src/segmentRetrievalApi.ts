import { readApiError } from "./referenceMediaApi";


export type SegmentSearchValidityReason = "segment_index_changed";
export type RerankValidityReason = SegmentSearchValidityReason | "model_changed" | "provider_configuration_changed";

export type SegmentSearchCandidate = {
  candidateId: string;
  assetId: string;
  sourceFingerprint: string;
  algorithmVersion: string;
  keyframeModelVersion: string;
  inPointSeconds: number;
  outPointSeconds: number;
  keyframeTimeSeconds: number;
  visualRelevance: number;
  quality: { score: number; evidence: { width: number; height: number; contrast: number } };
  duplicateRisk: { level: "low" | "high"; similarity: number; candidateId: string | null };
  evidence: { kind: "keyframe_visual_vector"; vectorizerVersion: string; basis: string };
  keyframe: { timeSeconds: number; url: string };
  preview: { url: string; startSeconds: number; endSeconds: number };
};

export type SegmentRerank = {
  id: string;
  status: "completed" | "failed";
  provider: "doubao";
  model: string;
  configurationRevision: string;
  indexVersion: string;
  candidateIds: string[];
  createdAt: string;
  completedAt: string | null;
  ranking: Array<{ candidateId: string; reason: string; confidence: number }>;
  error: { code: string; message: string; retryable: boolean } | null;
  validity: { status: "current" | "stale"; reason: RerankValidityReason | null };
};

export type SegmentSearch = {
  id: string;
  query: string;
  createdAt: string;
  updatedAt: string;
  retrievalVersion: string;
  vectorizerVersion: string;
  indexVersion: string;
  validity: { status: "current" | "stale"; reason: SegmentSearchValidityReason | null };
  localCandidates: SegmentSearchCandidate[];
  rerank: SegmentRerank | null;
  rerankHistory: SegmentRerank[];
};

export type SegmentRerankDisclosure = {
  searchId: string;
  query: string;
  indexVersion: string;
  provider: "doubao";
  model: string;
  configurationRevision: string;
  candidates: Array<{
    candidateId: string;
    assetId: string;
    inPointSeconds: number;
    outPointSeconds: number;
    keyframeTimeSeconds: number;
  }>;
  sentContent: {
    text: string;
    prompt: string;
    analysisContext: string;
    keyframeCount: number;
    keyframes: string[];
  };
  notSent: string[];
  digest: string;
};

const intentHeaders = { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" };

function baseUrl(projectId: string) {
  return `/api/projects/${encodeURIComponent(projectId)}/segment-searches`;
}

async function request<T>(url: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  let response: Response;
  try {
    response = init === undefined ? await fetch(url) : await fetch(url, init);
  } catch {
    throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。");
  }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export async function listSegmentSearches(projectId: string): Promise<SegmentSearch[]> {
  return (await request<{ searches: SegmentSearch[] }>(
    baseUrl(projectId), undefined, "无法读取片段检索历史，请重试。",
  )).searches;
}

export function searchSegments(projectId: string, query: string, limit = 8): Promise<SegmentSearch> {
  return request(baseUrl(projectId), {
    method: "POST", headers: intentHeaders, body: JSON.stringify({ query, limit }),
  }, "本地视觉检索失败，请重试。");
}

export function prepareSegmentRerank(
  projectId: string, searchId: string, candidateIds: string[],
): Promise<SegmentRerankDisclosure> {
  return request(`${baseUrl(projectId)}/${encodeURIComponent(searchId)}/rerank-disclosure`, {
    method: "POST", headers: intentHeaders, body: JSON.stringify({ candidateIds }),
  }, "无法预览豆包发送内容，请重试。");
}

export function runSegmentRerank(
  projectId: string,
  searchId: string,
  candidateIds: string[],
  digest: string,
): Promise<SegmentSearch> {
  return request(`${baseUrl(projectId)}/${encodeURIComponent(searchId)}/rerank`, {
    method: "POST",
    headers: intentHeaders,
    body: JSON.stringify({ candidateIds, digest, disclosureAccepted: true }),
  }, "豆包视觉复排失败；本地候选仍可使用。");
}
