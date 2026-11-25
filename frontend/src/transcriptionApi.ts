import { readApiError } from "./referenceMediaApi";

export type TranscriptionRun = {
  assetId: string; runId: string; status: "queued" | "running" | "completed" | "failed" | "cancelled";
  language: string; model: string; modelRevision: string | null; modelIdentityStatus?: "verified" | "unverified"; policyVersion: string; progress: number;
  completedBlocks: number; blockCount: number; sourceSha256: string; error?: { code: string; message: string; retryable: boolean } | null;
  words: { startSeconds: number; endSeconds: number; text: string }[];
};

const headers = { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" };
const base = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/transcriptions`;
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(await readApiError(response, "转录操作失败，请重试。"));
  return response.json() as Promise<T>;
}
export const listTranscriptions = async (projectId: string) => (await request<{ transcriptions: TranscriptionRun[] }>(base(projectId))).transcriptions;
export const startTranscription = (projectId: string, assetId: string) => request<TranscriptionRun>(base(projectId), { method: "POST", headers, body: JSON.stringify({ assetId }) });
export const retryTranscription = (projectId: string, assetId: string) => request<TranscriptionRun>(`${base(projectId)}/assets/${encodeURIComponent(assetId)}/retry`, { method: "POST", headers, body: "{}" });
export const cancelTranscription = (projectId: string, assetId: string) => request<TranscriptionRun>(`${base(projectId)}/assets/${encodeURIComponent(assetId)}/cancel`, { method: "POST", headers, body: "{}" });
