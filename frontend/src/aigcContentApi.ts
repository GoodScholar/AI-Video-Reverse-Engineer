import { readApiError } from "./referenceMediaApi";
import type { AspectMode } from "./videoAspect";

export type AigcBrief = { revision: number; productName: string; facts: Array<{ id: string; text: string }>;
  audience: string; sellingPoints: string[]; callToAction: string; forbiddenPhrases: string[]; assetIds: string[]; aspectMode?: AspectMode };
export type AigcAsset = { id: string; name: string; notes?: string; kind: "image" | "video"; width?: number; height?: number };
export type AigcBeat = { text: string; factIds: string[]; assetId: string };
export type AigcCandidate = { id: string; generationId: string; revision: number; briefRevision: number;
  confirmedRevision: number | null; sellingPoint: string; beats: AigcBeat[] };
export type AigcWorkspace = { brief: AigcBrief; assets: AigcAsset[]; candidates: AigcCandidate[] };
export type AigcDisclosure = { briefRevision: number; provider: string; model: string; prompt: string; digest: string };

const headers = { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" };
const base = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/aigc-content`;

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

export function generateAigcCandidates(projectId: string, disclosure: AigcDisclosure): Promise<{ candidates: AigcCandidate[] }> {
  return request(`${base(projectId)}/candidates`, { method: "POST", headers,
    body: JSON.stringify({ briefRevision: disclosure.briefRevision, provider: disclosure.provider, model: disclosure.model,
      digest: disclosure.digest, disclosureAccepted: true }) }, "无法生成脚本候选。");
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
