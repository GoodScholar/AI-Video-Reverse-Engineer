import { readApiError } from "./referenceMediaApi";

export type ShotPrompts = {
  positiveZh: string;
  negativeZh: string;
  positiveEn: string;
  negativeEn: string;
};

export type ShotControls = {
  depth: "available" | "review_required" | "failed" | "missing";
  pose: "missing" | "running" | "available" | "review_required" | "failed" | "unavailable";
  mask: "missing" | "running" | "available" | "review_required" | "failed" | "unavailable";
};

export type PersonControlQuality = {
  status: "passed" | "review_required" | "failed";
  frameCount: number;
  detectedFrameCount: number;
  missingTimesSeconds: number[];
  multiplePersonTimesSeconds: number[];
  message: string;
};

export type PersonControl = {
  status: "not_started" | "queued" | "running" | "completed" | "failed";
  error: string | null;
  quality: PersonControlQuality | null;
  runId: string | null;
  outputs: { pose?: string; mask?: string; overlay?: string };
};

export type PreparationShot = {
  id: string;
  startSeconds: number;
  endSeconds: number;
  representativeSeconds: number;
  notes: string;
  prompts: ShotPrompts;
  controls: ShotControls;
  personControl?: PersonControl;
};

export type ShotPreparationState = {
  sourceId: string;
  preprocessingId: string;
  revision: number;
  shots: PreparationShot[];
  canAnalyze: boolean;
  timelineOverride: { toolkitRunId: string; cutRevision: number } | null;
  toolkitScenes: Array<{ toolkitRunId: string; cutRevision: number; cuts: number[]; createdAt?: string; label: string }>;
  personControlEnvironment?: { ready: boolean; message: string };
};

export type ShotPreparationSave = Pick<ShotPreparationState, "revision" | "sourceId" | "preprocessingId"> & {
  shots: Array<Pick<PreparationShot, "id" | "notes" | "prompts">>;
};

const connectionError = "无法连接本地服务，请确认应用服务正在运行后重试。";

function baseUrl(projectId: string) {
  return `/api/projects/${encodeURIComponent(projectId)}/preparation`;
}

async function request<T>(url: string, init: RequestInit, fallback: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error(connectionError);
  }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export function getShotPreparation(projectId: string): Promise<ShotPreparationState> {
  return request<ShotPreparationState>(baseUrl(projectId), {}, "无法读取逐镜头准备数据，请重试。").then((state) => ({
    ...state,
    timelineOverride: state.timelineOverride ?? null,
    toolkitScenes: Array.isArray(state.toolkitScenes) ? state.toolkitScenes : [],
  }));
}

export function saveShotPreparation(projectId: string, state: ShotPreparationSave): Promise<ShotPreparationState> {
  return request(baseUrl(projectId), {
    method: "PUT", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: JSON.stringify(state),
  }, "无法保存逐镜头准备，请重试。");
}

export function analyzePreparationShot(
  projectId: string,
  shotId: string,
  state: Pick<ShotPreparationState, "revision" | "sourceId" | "preprocessingId"> & { disclosureAccepted: true },
): Promise<ShotPreparationState> {
  return request(`${baseUrl(projectId)}/shots/${encodeURIComponent(shotId)}/analyze`, {
    method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: JSON.stringify(state),
  }, "无法分析当前镜头，请重试。");
}

export function extractShotPersonControl(
  projectId: string,
  shotId: string,
  state: Pick<ShotPreparationState, "revision" | "sourceId" | "preprocessingId">,
): Promise<ShotPreparationState> {
  return request(`${baseUrl(projectId)}/shots/${encodeURIComponent(shotId)}/person-control`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" },
    body: JSON.stringify(state),
  }, "无法提取当前镜头的人物控制素材，请重试。");
}

export type TimelineRequest = Pick<ShotPreparationState, "revision" | "sourceId" | "preprocessingId">;

export function applyToolkitTimeline(
  projectId: string,
  body: TimelineRequest & { toolkitRunId: string; cutRevision: number },
): Promise<ShotPreparationState> {
  return request(`${baseUrl(projectId)}/timeline/apply`, {
    method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: JSON.stringify(body),
  }, "无法应用视频工具切点，请刷新后重试。");
}

export function restoreDetectedTimeline(projectId: string, body: TimelineRequest): Promise<ShotPreparationState> {
  return request(`${baseUrl(projectId)}/timeline/restore`, {
    method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: JSON.stringify(body),
  }, "无法恢复本地检测切点，请刷新后重试。");
}

export function shotRepresentativeFrameUrl(projectId: string, shotId: string, sourceId: string, preprocessingId: string, revision: number) {
  const version = new URLSearchParams({ sourceId, preprocessingId, revision: String(revision) });
  return `${baseUrl(projectId)}/shots/${encodeURIComponent(shotId)}/frame?${version}`;
}

export async function downloadShotPreparationPackage(projectId: string, state: TimelineRequest): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl(projectId)}/package?${new URLSearchParams({ sourceId: state.sourceId, preprocessingId: state.preprocessingId, revision: String(state.revision) })}`);
  } catch {
    throw new Error(connectionError);
  }
  if (!response.ok) throw new Error(await readApiError(response, "无法导出逐镜头准备包，请重试。"));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `shot-preparation-${projectId}.zip`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
