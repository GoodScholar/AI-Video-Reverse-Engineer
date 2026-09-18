import { readApiError } from "./referenceMediaApi";

export type ReproductionPrompts = {
  positiveZh: string;
  negativeZh: string;
  positiveEn: string;
  negativeEn: string;
};

export type ReproductionSettings = {
  strategy: "wan22_i2v" | "wan22_fun_control";
  width: number;
  height: number;
  frames: number;
  fps: number;
  seed: number;
};

export type ReproductionRun = {
  id: string;
  promptId: string | null;
  status: "submitting" | "queued" | "running" | "completed" | "failed" | "unknown";
  createdAt: string;
  error: string | null;
  outputs: Array<{ filename: string; url: string }>;
  revision: number;
};

export type ReproductionTemplate = {
  strategy: ReproductionSettings["strategy"];
  label: string;
  status: "candidate";
  [key: string]: unknown;
};

export type ReproductionState = {
  prompts: ReproductionPrompts | null;
  revision: number;
  sourceHash: string | null;
  stale: boolean;
  settings: ReproductionSettings;
  comfyUrl: string;
  runs: ReproductionRun[];
  canGeneratePrompts: boolean;
  hasDepth: boolean;
  analysisReady: boolean;
  adjustments: string[];
  templates: ReproductionTemplate[];
};

const connectionError = "无法连接本地服务，请确认应用服务正在运行后重试。";
const headers = { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" };

function baseUrl(projectId: string) {
  return `/api/projects/${encodeURIComponent(projectId)}/reproduction`;
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

export function getReproduction(projectId: string): Promise<ReproductionState> {
  return request(baseUrl(projectId), {}, "无法读取复刻方案，请重试。");
}

export function generateReproductionPrompts(projectId: string, revision: number): Promise<ReproductionState> {
  return request(`${baseUrl(projectId)}/prompts`, {
    method: "POST", headers, body: JSON.stringify({ disclosureAccepted: true, revision }),
  }, "无法生成提示词，请重试。");
}

export function saveReproduction(projectId: string, state: Pick<ReproductionState, "revision" | "prompts" | "settings" | "comfyUrl">): Promise<ReproductionState> {
  return request(baseUrl(projectId), {
    method: "PUT", headers, body: JSON.stringify(state),
  }, "无法保存复刻方案，请重试。");
}

export type ComfyCheck = {
  connected: boolean;
  ready: boolean;
  version: string | null;
  missingNodes: string[];
  missingModels: string[];
  message: string;
};

export function checkComfy(projectId: string): Promise<ComfyCheck> {
  return request(`${baseUrl(projectId)}/check`, {
    method: "POST", headers, body: JSON.stringify({}),
  }, "无法检查本地 ComfyUI，请重试。");
}

export function startReproductionRun(projectId: string, revision: number): Promise<ReproductionState> {
  return request(`${baseUrl(projectId)}/runs`, {
    method: "POST", headers, body: JSON.stringify({ revision, disclosureAccepted: true }),
  }, "无法提交本地生成，请重试。");
}

export function refreshReproductionRun(projectId: string, runId: string): Promise<ReproductionState> {
  return request(`${baseUrl(projectId)}/runs/${encodeURIComponent(runId)}/refresh`, {
    method: "POST", headers, body: JSON.stringify({}),
  }, "无法刷新生成状态，请重试。");
}

export function resolveReproductionRun(
  projectId: string,
  runId: string,
  body: { promptId: string | null; confirmedNotQueued: boolean },
): Promise<ReproductionState> {
  return request(`${baseUrl(projectId)}/runs/${encodeURIComponent(runId)}/resolve`, {
    method: "POST", headers, body: JSON.stringify(body),
  }, "无法恢复未知生成状态，请重试。");
}

export async function downloadReproductionPackage(projectId: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl(projectId)}/package`);
  } catch {
    throw new Error(connectionError);
  }
  if (!response.ok) throw new Error(await readApiError(response, "无法导出复刻包，请重试。"));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `reproduction-${projectId}.zip`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
