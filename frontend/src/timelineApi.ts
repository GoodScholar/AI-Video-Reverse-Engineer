import { readApiError } from "./referenceMediaApi";
import type { Packaging, Reframe, ReframeAnalysis } from "./ReframeControls";

export type TimelineAssetKind = "image" | "video" | "audio";
export type TimelineTrackKind = "video" | "audio";
export type TimelineFormat = "preview" | "mp4" | "wav";
export type TimelineRunStatus = "queued" | "running" | "cancel_requested" | "cancelled" | "failed" | "unknown" | "partial_success" | "completed";

export type TimelineClip = { id: string; assetId: string; start: number; inPoint: number; duration: number; speed: number; volume: number; fadeIn: number; fadeOut: number; reframe?: Reframe };
export type TimelineTrack = { id: string; name: string; kind: TimelineTrackKind; muted: boolean; hidden: boolean; clips: TimelineClip[] };
export type TimelineTransitionTemplate = "cut" | "dissolve" | "push" | "wipe" | "flash-white" | "pixelize" | "speed-change";
export type TimelineTransition = { id: string; leftClipId: string; rightClipId: string; template: TimelineTransitionTemplate;
  enabled: boolean; duration: number; overlap: number; audioCrossfade: boolean; reason: string };
export type TimelineAsset = { id: string; name: string; kind: TimelineAssetKind; url: string; duration?: number; width?: number; height?: number };
export type TimelineRun = { id: string; commonRunId?: string; domainKind?: string; revision: number; format: TimelineFormat; status: TimelineRunStatus; error: string | null; inputSnapshot?: unknown; inputFingerprint?: string | null; idempotencyKey?: string | null; retryOf?: string | null; attempt?: number; logs?: Array<{ event: string; attempt?: number; reason?: string }>; recovery?: { reason: string; attempt?: number }; url?: string };
export type TimelineWorkspace = { revision: number; settings: { width: number; height: number; fps: 24 | 25 | 30 }; tracks: TimelineTrack[]; transitions?: TimelineTransition[]; assets: TimelineAsset[]; runs: TimelineRun[]; reframeAnalysis?: Record<string, ReframeAnalysis>; packaging?: Packaging };
export type TimelineSave = Pick<TimelineWorkspace, "revision" | "settings" | "tracks" | "transitions" | "packaging">;

const connectionError = "无法连接本地服务，请确认应用服务正在运行后重试。";
const jsonHeaders = { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" };

function timelineBase(projectId: string) { return `/api/projects/${encodeURIComponent(projectId)}/timeline`; }

async function request<T>(url: string, init: RequestInit, fallback: string): Promise<T> {
  let response: Response;
  try { response = await fetch(url, init); }
  catch { throw new Error(connectionError); }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export function getTimeline(projectId: string): Promise<TimelineWorkspace> {
  return request(timelineBase(projectId), {}, "无法读取复刻剪辑时间线，请重试。");
}

export function saveTimeline(projectId: string, workspace: TimelineSave): Promise<TimelineWorkspace> {
  const { revision, settings, tracks, transitions, packaging } = workspace;
  return request(timelineBase(projectId), { method: "PUT", headers: jsonHeaders,
    body: JSON.stringify({ revision, settings, tracks, ...(transitions ? { transitions } : {}), ...(packaging !== undefined ? { packaging } : {}) }) }, "无法保存剪辑时间线，请刷新后重试。");
}

export function analyzeTimelineReframe(projectId: string, clipId: string, revision: number): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/reframes/${encodeURIComponent(clipId)}/analyze`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }) }, "无法提交主体分析，请重试。");
}
export function cancelTimelineReframe(projectId: string, clipId: string, revision: number): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/reframes/${encodeURIComponent(clipId)}/cancel`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }) }, "无法取消主体分析，请重试。");
}

export function startTimelineRun(projectId: string, revision: number, format: TimelineFormat, idempotencyKey?: string): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/runs`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision, format, ...(idempotencyKey ? { idempotencyKey } : {}) }) }, "无法提交渲染任务，请先保存当前时间线后重试。");
}

export function cancelTimelineRun(projectId: string, runId: string): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers: jsonHeaders, body: "{}" }, "无法取消渲染任务，请重试。");
}

export function retryTimelineRun(projectId: string, runId: string, idempotencyKey: string): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/runs/${encodeURIComponent(runId)}/retry`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ idempotencyKey }) }, "无法重试渲染任务，请重试。");
}

export function timelineOutputUrl(projectId: string, runId: string) {
  return `${timelineBase(projectId)}/runs/${encodeURIComponent(runId)}/output`;
}

export function uploadTimelineAudio(projectId: string, file: File): Promise<unknown> {
  const data = new FormData();
  data.append("file", file);
  return request(`/api/projects/${encodeURIComponent(projectId)}/preproduction/assets?role=audio`, { method: "POST", body: data }, "无法上传录音，请重试。");
}

export function importShotResults(projectId: string, revision: number, preproductionRevision: number): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/import-shot-results`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision, preproductionRevision }) }, "无法导入镜头结果，请检查每个镜头的视频和时长。");
}

export function validateTimelineDraft(projectId: string, draft: TimelineSave): Promise<TimelineSave> {
  return request(`${timelineBase(projectId)}/validate-draft`, { method: "POST", headers: jsonHeaders, body: JSON.stringify(draft) }, "无法恢复草稿，请检查片段参数与项目素材。");
}

export type TimelinePreflight = {
  revision: number; format: TimelineFormat; ready: boolean;
  issues: Array<{ level: "error" | "warning"; label: string; message: string; trackId?: string; clipId?: string; assetId?: string }>;
};
export function preflightTimeline(projectId: string, revision: number, format: TimelineFormat): Promise<TimelinePreflight> {
  return request(`${timelineBase(projectId)}/preflight`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision, format }) }, "无法完成导出预检，请重试。");
}
