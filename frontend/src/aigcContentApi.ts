import { readApiError } from "./referenceMediaApi";
import type { AspectMode } from "./videoAspect";

export type AigcBrief = { revision: number; productName: string; facts: Array<{ id: string; text: string }>;
  audience: string; sellingPoints: string[]; callToAction: string; forbiddenPhrases: string[]; assetIds: string[]; aspectMode?: AspectMode };
export type AigcAsset = { id: string; name: string; notes?: string; kind: "image" | "video"; width?: number; height?: number };
export type AigcBeat = { text: string; factIds: string[]; assetId: string };
export type AigcCandidate = { id: string; generationId: string; revision: number; briefRevision: number;
  confirmedRevision: number | null; sellingPoint: string; beats: AigcBeat[] };
export type AigcGenerationOutput = { resultId: string; status: string; chunks: Array<{ sequence: number; text: string }>; text: string };
export type AigcGenerationRun = { id: string; provider: string; model: string; status: string; outcome?: string | null; billingUnknown: boolean; disclosure?: { prompt: string }; capabilitySnapshot: Record<string, boolean | string>;
  outputItems: AigcGenerationOutput[] };
export type AigcWorkspace = { brief: AigcBrief; assets: AigcAsset[]; candidates: AigcCandidate[]; generationRuns?: AigcGenerationRun[] };
export type AigcDisclosure = { briefRevision: number; provider: string; model: string; prompt: string; digest: string };

const headers = { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" };
const base = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/aigc-content`;
const pendingKeys = new Map<string, string>();

export function pendingAigcGenerationKey(projectId: string, disclosure: AigcDisclosure): string {
  const fingerprint = `${projectId}:${disclosure.digest}:${disclosure.briefRevision}:${disclosure.provider}:${disclosure.model}`;
  const storageKey = `aigc-generation:${fingerprint}`;
  let value = pendingKeys.get(storageKey);
  try { value ??= sessionStorage.getItem(storageKey) ?? undefined; } catch { /* 内存回退 */ }
  if (!value) value = `aigc-${crypto.randomUUID()}`;
  pendingKeys.set(storageKey, value);
  try { sessionStorage.setItem(storageKey, value); } catch { /* 内存回退 */ }
  return value;
}

export function clearPendingAigcGenerationKey(projectId: string, disclosure: AigcDisclosure): void {
  const storageKey = `aigc-generation:${projectId}:${disclosure.digest}:${disclosure.briefRevision}:${disclosure.provider}:${disclosure.model}`;
  pendingKeys.delete(storageKey);
  try { sessionStorage.removeItem(storageKey); } catch { /* 内存回退 */ }
}

async function request<T>(url: string, init: RequestInit, fallback: string): Promise<T> {
  let response: Response;
  try { response = await fetch(url, init); }
  catch { throw new Error("无法连接本地服务，请检查应用服务。"); }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export function getAigcWorkspace(projectId: string): Promise<AigcWorkspace> {
  return request(base(projectId), {}, "无法读取创作简报。");
}

export function saveAigcBrief(projectId: string, brief: AigcBrief): Promise<{ brief: AigcBrief }> {
  const { revision, ...content } = brief;
  return request(`${base(projectId)}/brief`, { method: "PUT", headers, body: JSON.stringify({ revision, brief: content }) }, "无法保存创作简报。");
}

export function discloseAigcRequest(projectId: string, provider: string, model: string): Promise<AigcDisclosure> {
  return request(`${base(projectId)}/disclosure`, { method: "POST", headers, body: JSON.stringify({ provider, model }) }, "无法预览发送内容。");
}

export function submitAigcGeneration(projectId: string, disclosure: AigcDisclosure, idempotencyKey: string): Promise<{ run: AigcGenerationRun }> {
  return request(`${base(projectId)}/generation-runs`, { method: "POST", headers,
    body: JSON.stringify({ briefRevision: disclosure.briefRevision, provider: disclosure.provider, model: disclosure.model,
      digest: disclosure.digest, disclosureAccepted: true, idempotencyKey }) }, "无法提交脚本生成。");
}

export function getAigcGenerationRun(projectId: string, runId: string): Promise<AigcGenerationRun> {
  return request(`${base(projectId)}/generation-runs/${encodeURIComponent(runId)}`, {}, "无法刷新脚本生成状态。");
}

export function getAigcGenerationCapabilities(projectId: string, provider: string, model: string): Promise<{ capabilities: Record<string, boolean | string> }> {
  return request(`${base(projectId)}/generation-capabilities?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`, {}, "无法读取脚本生成能力。");
}

export function cancelAigcGenerationRun(projectId: string, runId: string): Promise<{ run: AigcGenerationRun }> {
  return request(`${base(projectId)}/generation-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers }, "无法取消脚本生成。");
}

export function adoptAigcGenerationResult(projectId: string, runId: string, resultId: string): Promise<{ candidates: AigcCandidate[] }> {
  return request(`${base(projectId)}/generation-runs/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(resultId)}/adopt`,
    { method: "POST", headers }, "无法采用这次脚本结果。");
}

export function saveAigcCandidate(projectId: string, candidate: AigcCandidate): Promise<{ candidate: AigcCandidate }> {
  return request(`${base(projectId)}/candidates/${encodeURIComponent(candidate.id)}`,
    { method: "PUT", headers, body: JSON.stringify({ revision: candidate.revision, sellingPoint: candidate.sellingPoint,
      beats: candidate.beats }) }, "无法保存脚本候选。");
}

export function confirmAigcCandidate(projectId: string, candidate: AigcCandidate): Promise<{ candidate: AigcCandidate }> {
  return request(`${base(projectId)}/candidates/${encodeURIComponent(candidate.id)}/confirm`,
    { method: "POST", headers, body: JSON.stringify({ revision: candidate.revision }) }, "无法确认脚本候选。");
}

export function handoffAigcGeneration(projectId: string, generationId: string): Promise<{ task: { id: string }; skippedTaskIds?: string[] }> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/batch-edits/from-aigc`,
    { method: "POST", headers, body: JSON.stringify({ generationId }) }, "无法将脚本候选交给批量混剪。");
}
