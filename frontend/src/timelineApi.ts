import { readApiError } from "./referenceMediaApi";

export type TimelineAssetKind = "image" | "video" | "audio";
export type TimelineTrackKind = "video" | "audio";
export type TimelineFormat = "preview" | "mp4" | "wav";
export type TimelineRunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type TimelineClip = { id: string; assetId: string; start: number; inPoint: number; duration: number; speed: number; volume: number; fadeIn: number; fadeOut: number };
export type TimelineTrack = { id: string; name: string; kind: TimelineTrackKind; muted: boolean; hidden: boolean; clips: TimelineClip[] };
export type TimelineAsset = { id: string; name: string; kind: TimelineAssetKind; url: string; duration?: number; width?: number; height?: number };
export type TimelineRun = { id: string; revision: number; format: TimelineFormat; status: TimelineRunStatus; error: string | null; url?: string };
export type TimelineWorkspace = { revision: number; settings: { width: number; height: number; fps: 24 | 25 | 30 }; tracks: TimelineTrack[]; assets: TimelineAsset[]; runs: TimelineRun[] };
export type TimelineSave = Pick<TimelineWorkspace, "revision" | "settings" | "tracks">;

const connectionError = "无法连接本地服务，请确认应用服务正在运行后重试。";
const jsonHeaders = { "Content-Type": "application/json", "x-aivre-intent": "timeline-edit" };

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
  const { revision, settings, tracks } = workspace;
  return request(timelineBase(projectId), { method: "PUT", headers: jsonHeaders, body: JSON.stringify({ revision, settings, tracks }) }, "无法保存剪辑时间线，请刷新后重试。");
}

export function startTimelineRun(projectId: string, revision: number, format: TimelineFormat): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/runs`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision, format }) }, "无法提交渲染任务，请先保存当前时间线后重试。");
}

export function cancelTimelineRun(projectId: string, runId: string): Promise<TimelineWorkspace> {
  return request(`${timelineBase(projectId)}/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers: jsonHeaders, body: "{}" }, "无法取消渲染任务，请重试。");
}

export function timelineOutputUrl(projectId: string, runId: string) {
  return `${timelineBase(projectId)}/runs/${encodeURIComponent(runId)}/output`;
}

export function uploadTimelineAudio(projectId: string, file: File): Promise<unknown> {
  const data = new FormData();
  data.append("file", file);
  return request(`/api/projects/${encodeURIComponent(projectId)}/preproduction/assets?role=audio`, { method: "POST", body: data }, "无法上传录音，请重试。");
}
