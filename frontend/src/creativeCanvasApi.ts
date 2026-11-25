export type CreativeCanvasKind = "text" | "media" | "generate";
import { readApiError } from "./referenceMediaApi";

export type CreativeCanvasAsset = { id: string; name: string; kind: "image" | "video" | "audio" | "text"; url: string; role?: string; available?: boolean; mimeType?: string };
export type CreativeCanvasNode = {
  id: string; kind: CreativeCanvasKind; position: { x: number; y: number }; label?: string; text?: string; assetId?: string;
  outputAssetId?: string; currentOutputRef?: CreativeCanvasOutputRef; stale?: true; referenceOrder?: string[];
  /** Optional durable text-generation role. Legacy `text` remains the selected-version projection. */
  prompt?: string; providerId?: string; modelId?: string; configurationVersion?: string; currentTextVersionId?: string; fontSize?: number;
  width?: number; height?: number; freeResize?: boolean; parentId?: string;
  params?: {
    /** Legacy single-provider model selection; retained for old canvas documents. */
    model?: string;
    /** Stable multi-provider selection. providerId and modelId must be saved together. */
    providerId?: string; modelId?: string; configurationVersion?: string;
    size?: string; negativePrompt?: string; width?: number; height?: number; ratio?: string; count?: number; steps?: number; seed?: number; denoise?: number;
  };
};
export type CreativeCanvasOutputRef = { runId: string; resultId: string };
export type CreativeCanvasOutputItem = {
  resultId: string; requestId?: string; assetId?: string; mediaType: "image"; status: "pending" | "running" | "cancel_requested" | "installed" | "failed" | "cancelled" | "unknown" | "deleted";
  sourceRunId: string; createdAt: string; error?: string; cancellationRequested?: boolean;
  localDisposition?: "none" | "queued_cancelled" | "drained";
  providerDisposition?: "not_submitted" | "unsupported" | "unknown";
  billingUnknown?: boolean;
};
export type CreativeCanvasEdge = { id: string; source: string; target: string; sourceHandle: "text" | "image"; targetHandle: "prompt" | "image" | "in" };
export type CreativeCanvasGroup = { id: string; label?: string; parentId?: string; position: { x: number; y: number }; width: number; height: number };
export type CreativeCanvasDocument = {
  revision: number; nodes: CreativeCanvasNode[]; edges: CreativeCanvasEdge[]; groups?: CreativeCanvasGroup[]; viewport: { x: number; y: number; zoom: number };
  settings: { comfyUrl?: string; checkpoint?: string };
};
export type CreativeCanvasRun = {
  id: string; canvasId?: string; nodeId: string; status: "submitting" | "queued" | "running" | "cancel_requested" | "cancelled" | "completed" | "failed" | "unknown";
  error: string | null; promptId: string | null; createdAt: string; inputAssetIds: string[]; outputAssetIds: string[]; outputItems?: CreativeCanvasOutputItem[]; snapshot: Record<string, unknown>;
  idempotencyKey?: string; cancellationRequested?: boolean; localDisposition?: "queued_cancelled" | "transport_aborted" | "none"; providerDisposition?: "confirmed_cancelled" | "pending" | "unsupported" | "unknown"; billingUnknown?: boolean;
};
export type CreativeCanvasTextReferenceSnapshot = {
  edgeId: string; sourceNodeId: string; sourceHandle: string; textVersionId?: string; text?: string;
  assetId?: string; sourceRunId?: string; sourceResultId?: string;
};
export type CreativeCanvasTextVersion = {
  versionId: string; runId: string; status: "pending" | "running" | "installed" | "completed" | "failed" | "cancel_requested" | "cancelled" | "unknown";
  text: string; seq: number; createdAt: string; error?: string; assetId?: string; cancellationRequested?: boolean;
  localDisposition?: "none" | "queued_cancelled" | "drained";
  providerDisposition?: "not_submitted" | "confirmed_cancelled" | "pending" | "unsupported" | "unknown";
  billingUnknown?: boolean;
};
export type CreativeCanvasTextRun = {
  id: string; nodeId: string; canvasId: string;
  status: "submitting" | "queued" | "running" | "cancel_requested" | "cancelled" | "completed" | "failed" | "unknown";
  createdAt: string; error?: string | null;
  snapshot: { prompt: string; providerId: string; modelId: string; configurationVersion?: string; references: CreativeCanvasTextReferenceSnapshot[] };
  versions: CreativeCanvasTextVersion[];
  idempotencyKey?: string; cancellationRequested?: boolean;
  localDisposition?: "queued_cancelled" | "transport_aborted" | "none";
  providerDisposition?: "confirmed_cancelled" | "pending" | "unsupported" | "unknown";
  billingUnknown?: boolean;
};
export type CreativeCanvasBatchStep = {
  nodeId: string; dependsOn: string[]; idempotencyKey: string;
  status: "queued" | "running" | "completed" | "failed" | "blocked" | "cancelled" | "unknown";
  runId?: string; selectedOutputRef?: CreativeCanvasOutputRef & { assetId?: string }; error?: string;
};
export type CreativeCanvasBatch = {
  id: string; canvasId: string; status: "queued" | "running" | "completed" | "failed" | "blocked" | "cancelled" | "unknown";
  createdAt: string; documentRevision: number; documentSnapshot: CreativeCanvasDocument; idempotencyKey?: string; steps: CreativeCanvasBatchStep[];
  externalOutputRefs?: Array<{ edgeId: string; targetNodeId: string; sourceNodeId: string; runId: string; resultId: string; assetId: string }>;
};
export type CreativeCanvasMeta = { id: string; name: string; createdAt: string | null; updatedAt: string | null; deletedAt: string | null };
export type CreativeCanvasWorkspace = { document: CreativeCanvasDocument; runs: CreativeCanvasRun[]; textRuns?: CreativeCanvasTextRun[]; textModels?: import("./textGenerationApi").TextGenerationModel[]; assets: CreativeCanvasAsset[]; batches?: CreativeCanvasBatch[]; canvas?: CreativeCanvasMeta };
export type CreativeCanvasCatalog = { canvases: CreativeCanvasMeta[] };
export type CreatedCreativeCanvas = { canvas: CreativeCanvasMeta; document: CreativeCanvasDocument; runs: CreativeCanvasRun[] };

function url(projectId: string) { return `/api/projects/${encodeURIComponent(projectId)}/creative-canvas`; }
function scoped(path: string, canvasId?: string) { return canvasId ? `${path}?canvasId=${encodeURIComponent(canvasId)}` : path; }
async function request<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  let response: Response;
  try { response = await fetch(path, init); } catch { throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。"); }
  if (!response.ok) {
    const error = new Error(await readApiError(response, fallback)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}
const json = { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" };
export function listCreativeCanvases(projectId: string, includeDeleted = false) {
  return request<CreativeCanvasCatalog>(`${url(projectId)}/canvases${includeDeleted ? "?includeDeleted=true" : ""}`, {}, "无法读取创作画布目录，请重试。");
}
export function createCreativeCanvas(projectId: string, name: string) {
  return request<CreatedCreativeCanvas>(`${url(projectId)}/canvases`, { method: "POST", headers: json, body: JSON.stringify({ name }) }, "无法新建画布，请重试。");
}
export function renameCreativeCanvas(projectId: string, canvasId: string, name: string) {
  return request<{ canvas: CreativeCanvasMeta }>(`${url(projectId)}/canvases/${encodeURIComponent(canvasId)}`, { method: "PATCH", headers: json, body: JSON.stringify({ name }) }, "无法重命名画布，请重试。");
}
export function deleteCreativeCanvas(projectId: string, canvasId: string) {
  return request<{ canvas: CreativeCanvasMeta }>(`${url(projectId)}/canvases/${encodeURIComponent(canvasId)}`, { method: "DELETE", headers: json }, "无法删除画布，请重试。");
}
export function restoreCreativeCanvas(projectId: string, canvasId: string) {
  return request<{ canvas: CreativeCanvasMeta }>(`${url(projectId)}/canvases/${encodeURIComponent(canvasId)}/restore`, { method: "POST", headers: json, body: "{}" }, "无法恢复画布，请重试。");
}
export function getCreativeCanvas(projectId: string, canvasId?: string) { return request<CreativeCanvasWorkspace>(scoped(url(projectId), canvasId), {}, "无法读取创作画布，请重试。"); }
export function saveCreativeCanvas(projectId: string, document: CreativeCanvasDocument, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(url(projectId), canvasId), { method: "PUT", headers: json, body: JSON.stringify(document) }, "无法保存画布，请重试。");
}
export function runCreativeCanvasNode(projectId: string, nodeId: string, revision: number, idempotencyKey?: string, canvasId?: string, count?: number) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/nodes/${encodeURIComponent(nodeId)}/runs`, canvasId), { method: "POST", headers: json, body: JSON.stringify({ revision, ...(idempotencyKey ? { idempotencyKey } : {}), ...(count !== undefined ? { count } : {}) }) }, "无法运行节点，请先保存并检查输入。");
}
/** Durable text runs are separate from image-output runs and are polled through the workspace payload. */
export function runCreativeCanvasTextNode(projectId: string, nodeId: string, revision: number, idempotencyKey?: string, canvasId?: string, count?: number) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/nodes/${encodeURIComponent(nodeId)}/text-runs`, canvasId), { method: "POST", headers: json, body: JSON.stringify({ revision, ...(idempotencyKey ? { idempotencyKey } : {}), ...(count !== undefined ? { count } : {}) }) }, "无法提交文本生成，请先保存并检查文本模型与引用。");
}
export function selectCreativeCanvasTextVersion(projectId: string, runId: string, versionId: string, revision: number, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/text-runs/${encodeURIComponent(runId)}/versions/${encodeURIComponent(versionId)}/select`, canvasId), { method: "POST", headers: json, body: JSON.stringify({ revision }) }, "无法采用该文本版本，请刷新后重试。");
}
export function cancelCreativeCanvasTextRun(projectId: string, runId: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/text-runs/${encodeURIComponent(runId)}/cancel`, canvasId), { method: "POST", headers: json, body: "{}" }, "无法请求停止文本生成，请重试。");
}
export function installCreativeCanvasTextVersion(projectId: string, runId: string, versionId: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/text-runs/${encodeURIComponent(runId)}/versions/${encodeURIComponent(versionId)}/assets`, canvasId), { method: "POST", headers: json, body: "{}" }, "无法将文本版本加入素材库，请重试。");
}
export function refreshCreativeCanvasRun(projectId: string, runId: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/runs/${encodeURIComponent(runId)}/refresh`, canvasId), { method: "POST", headers: json, body: "{}" }, "无法刷新运行状态，请重试。");
}
export function cancelCreativeCanvasRun(projectId: string, runId: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/runs/${encodeURIComponent(runId)}/cancel`, canvasId), { method: "POST", headers: json, body: "{}" }, "无法请求停止生成，请重试。");
}
function outputPath(projectId: string, runId: string, resultId: string) {
  return `${url(projectId)}/runs/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(resultId)}`;
}
export function selectCreativeCanvasOutput(projectId: string, runId: string, resultId: string, revision: number, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${outputPath(projectId, runId, resultId)}/select`, canvasId), { method: "POST", headers: json, body: JSON.stringify({ revision }) }, "无法设为当前输出，请刷新画布后重试。");
}
export function deleteCreativeCanvasOutput(projectId: string, runId: string, resultId: string, revision: number, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(outputPath(projectId, runId, resultId), canvasId), { method: "DELETE", headers: json, body: JSON.stringify({ revision }) }, "无法删除该结果，请刷新画布后重试。");
}
export function retryCreativeCanvasOutput(projectId: string, runId: string, resultId: string, revision: number, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${outputPath(projectId, runId, resultId)}/retry`, canvasId), { method: "POST", headers: json, body: JSON.stringify({ revision }) }, "无法重试该结果，请刷新画布后重试。");
}
export function cancelCreativeCanvasOutput(projectId: string, runId: string, resultId: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${outputPath(projectId, runId, resultId)}/cancel`, canvasId), { method: "POST", headers: json }, "无法取消该结果，请重试。");
}
export function runCreativeCanvasBatch(projectId: string, nodeIds: string[], revision: number, idempotencyKey?: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/batches`, canvasId), { method: "POST", headers: json, body: JSON.stringify({ revision, nodeIds, ...(idempotencyKey ? { idempotencyKey } : {}) }) }, "无法提交批量生成，请先保存并检查节点依赖。");
}
export function cancelCreativeCanvasBatch(projectId: string, batchId: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/batches/${encodeURIComponent(batchId)}/cancel`, canvasId), { method: "POST", headers: json, body: "{}" }, "无法取消批量生成，请重试。");
}
export function refreshCreativeCanvasBatch(projectId: string, batchId: string, canvasId?: string) {
  return request<CreativeCanvasWorkspace>(scoped(`${url(projectId)}/batches/${encodeURIComponent(batchId)}/refresh`, canvasId), { method: "POST", headers: json, body: "{}" }, "无法刷新批量生成状态，请重试。");
}
