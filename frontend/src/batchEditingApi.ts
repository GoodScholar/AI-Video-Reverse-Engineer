import { readApiError } from "./referenceMediaApi";
import type { AigcBeat, AigcBrief } from "./aigcContentApi";
import type { TimelineAsset, TimelineClip, TimelineTrack } from "./timelineApi";
import type { AspectMode } from "./videoAspect";

export type BatchRun = { id: string; revision: number; contentRevision?: number; subtitleRevision?: number; status: "queued" | "running" | "completed" | "failed" | "cancelled"; error: string | null;
  settings?: { width: number; height: number; fps: number }; url?: string };
export type BatchReview = { id: string; runId: string; decision: "approved" | "rejected"; reason: string;
  contentRevision: number; variantRevision: number; subtitleRevision: number };
export type BatchExportRun = { id: string; status: "queued" | "running" | "completed" | "failed" | "cancelled"; error: string | null;
  contentRevision: number; variantRevision: number; subtitleRevision: number; previewRunId: string; review: BatchReview;
  sellingPoint: string; script: string; assets: Array<{ id: string; name: string; kind: string }>;
  snapshot: { settings: { width: number; height: number; fps: number }; tracks: TimelineTrack[] }; subtitles: BatchCue[]; url?: string };
export type BatchCue = { id: string; start: number; end: number; text: string };
export type BatchRecognition = { id: string; status: "queued" | "running" | "completed" | "failed"; error: string | null;
  timelineRevision: number; subtitleRevision: number; language: "auto" | "zh" | "en" | "ja" | "ko"; cues: BatchCue[] };
export type BatchSubtitles = { revision: number; cues: BatchCue[]; recognitions: BatchRecognition[] };
export type BatchVariant = { id: string; revision: number; settings: { width: number; height: number; fps: number }; tracks: TimelineTrack[]; runs: BatchRun[];
  subtitles?: BatchSubtitles; exports?: BatchExportRun[]; aspectMode?: AspectMode; resolvedAspect?: string; aspectReason?: string };
export type BatchProposal = { baseRevision: number; method: "asset-metadata-keywords"; assetIds: string[]; clips: TimelineClip[]; matches: Array<{
  clipId: string; scriptSegment: string; assetId: string; assetName: string; matchedTerms: string[]; matchedSellingPointTerms: string[];
}> };
export type BatchTask = { voiceover?: {id:string;voiceId:string;status:"queued"|"running"|"completed"|"failed";error:string|null;
  contentRevision:number;variantRevision:number;model:string;modelRevision:string;assetIds:string[]}; id: string; sellingPoint: string; script: string; contentRevision?: number;
  reviews?: BatchReview[]; reviewStatus?: "pending" | "approved" | "rejected" | "stale"; variant: BatchVariant; proposal?: BatchProposal;
  batchId?: string; generationId?: string; generation?: { status: "ready" | "failed"; error: string | null };
  aigcSource?: { candidateId: string; candidateRevision: number; briefRevision: number; generationId: string; screenCopySource: "script";
    facts: Array<{ id: string; text: string }>; brief?: AigcBrief; sellingPoint?: string; beats?: AigcBeat[];
    assets?: Array<{ id: string; name: string; kind: "image" | "video" }> } };
export type BatchAsset = TimelineAsset & { notes?: string };
export type BatchWorkspace = { tasks: BatchTask[]; assets: BatchAsset[] };

const headers = { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" };
const base = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/batch-edits`;

async function request<T>(url: string, init: RequestInit, fallback: string): Promise<T> {
  let response: Response;
  try { response = await fetch(url, init); }
  catch { throw new Error("无法连接本地服务，请检查应用服务。"); }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export function getBatchWorkspace(projectId: string): Promise<BatchWorkspace> {
  return request(base(projectId), {}, "无法读取批量混剪任务。");
}

export function createBatchTask(projectId: string, sellingPoint: string, script: string): Promise<{ task: BatchTask }> {
  return request(base(projectId), { method: "POST", headers, body: JSON.stringify({ sellingPoint, script }) }, "无法创建批量任务。");
}

export function saveBatchVariant(projectId: string, taskId: string, revision: number, tracks: TimelineTrack[], settings?: BatchVariant["settings"], aspectMode?: AspectMode): Promise<{ variant: BatchVariant }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/variant`,
    { method: "PUT", headers, body: JSON.stringify({ revision, tracks, ...(settings ? { settings } : {}), ...(aspectMode ? { aspectMode } : {}) }) }, "无法保存短视频变体。");
}

export function saveBatchContent(projectId: string, taskId: string, revision: number, sellingPoint: string, script: string): Promise<{ task: BatchTask }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/content`,
    { method: "PUT", headers, body: JSON.stringify({ revision, sellingPoint, script }) }, "无法保存卖点与脚本。");
}

export function reviewBatchVariant(projectId: string, taskId: string, runId: string, decision: BatchReview["decision"], reason: string): Promise<{ task: BatchTask }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/reviews`,
    { method: "POST", headers, body: JSON.stringify({ runId, decision, reason }) }, "无法保存审核结果。");
}

export function startBatchPreview(projectId: string, taskId: string, revision: number): Promise<{ variant: BatchVariant }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/variant/previews`,
    { method: "POST", headers, body: JSON.stringify({ revision }) }, "无法生成变体预览。");
}

export function recommendBatchVariant(projectId: string, taskId: string, revision: number, assetIds: string[]): Promise<{ proposal: BatchProposal }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/recommendations`,
    { method: "POST", headers, body: JSON.stringify({ revision, assetIds }) }, "无法依据脚本推荐镜头。");
}

export function createBulkVariants(projectId: string, taskId: string, items: Array<{ sellingPoint: string; script: string }>, assetIds: string[]): Promise<{ generationId: string; tasks: BatchTask[] }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/variants/bulk`,
    { method: "POST", headers, body: JSON.stringify({ items, assetIds }) }, "无法批量创建短视频变体。");
}

export function startGenerationPreviews(projectId: string, taskId: string, generationId: string): Promise<{ results: Array<{ taskId: string; status: BatchRun["status"]; error?: string }> }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/generations/${encodeURIComponent(generationId)}/previews`,
    { method: "POST", headers, body: "{}" }, "无法批量生成预览。");
}

export function submitBatchExports(projectId: string, taskId: string, taskIds: string[]): Promise<{ results: Array<{ taskId: string; runId: string; status: BatchExportRun["status"]; error?: string }> }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/exports`,
    { method: "POST", headers, body: JSON.stringify({ taskIds }) }, "无法提交成片导出。");
}

export function cancelBatchExport(projectId: string, taskId: string, runId: string): Promise<{ task: BatchTask }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/exports/${encodeURIComponent(runId)}/cancel`,
    { method: "POST", headers, body: "{}" }, "无法取消成片导出。");
}

export function cancelBatchPreview(projectId: string, taskId: string, runId: string): Promise<{ variant: BatchVariant }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/variant/previews/${encodeURIComponent(runId)}/cancel`,
    { method: "POST", headers, body: "{}" }, "无法取消预览。");
}

export function saveBatchSubtitles(projectId: string, taskId: string, revision: number, timelineRevision: number, cues: BatchCue[]): Promise<{ subtitles: BatchSubtitles }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/subtitles`,
    { method: "PUT", headers, body: JSON.stringify({ revision, timelineRevision, cues }) }, "无法保存字幕。");
}

export function recognizeBatchSubtitles(projectId: string, taskId: string, language: BatchRecognition["language"]): Promise<{ subtitles: BatchSubtitles }> {
  return request(`${base(projectId)}/${encodeURIComponent(taskId)}/subtitles/recognitions`,
    { method: "POST", headers, body: JSON.stringify({ language }) }, "无法识别语音字幕。");
}
