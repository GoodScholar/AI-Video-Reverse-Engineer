import { readApiError } from "./referenceMediaApi";

export type AssetRole = "character" | "scene" | "motion" | "audio" | "reference";
export type AssetKind = "image" | "video" | "audio";
export type NodeKind = "reference" | "trim" | "first_frame" | "last_frame" | "crop" | "resize" | "prompt";
export type NodeStatus = "pending" | "queued" | "running" | "completed" | "failed" | "stale";

export type PreproductionBrief = {
  theme: string;
  purpose: string;
  style: string;
  duration: number;
  aspect: string;
  mustPreserve: string;
};

export type PreproductionAsset = {
  id: string;
  name: string;
  kind: AssetKind;
  role: AssetRole;
  url: string;
  duration?: number;
  width?: number;
  height?: number;
};

export type PreproductionNode = {
  id: string;
  kind: NodeKind;
  input: string;
  params: Record<string, unknown>;
  status: NodeStatus;
  error?: string;
  artifacts: Array<{ name: string; url: string }>;
};

export type PreproductionShot = {
  id: string;
  title: string;
  duration: number;
  prompt: string;
  negativePrompt: string;
  assetIds: string[];
  nodes: PreproductionNode[];
};

export type PreproductionCheck = {
  level: "error" | "warning";
  shotId?: string;
  nodeId?: string;
  message: string;
};

export type PreproductionWorkspace = {
  revision: number;
  brief: PreproductionBrief;
  assets: PreproductionAsset[];
  shots: PreproductionShot[];
  checks: PreproductionCheck[];
  nodeCatalog: Array<{ kind: NodeKind; label: string }>;
};

export type PreproductionSave = Pick<PreproductionWorkspace, "revision" | "brief"> & {
  shots: PreproductionShot[];
};

const connectionError = "无法连接本地服务，请确认应用服务正在运行后重试。";

function baseUrl(projectId: string) {
  return `/api/projects/${encodeURIComponent(projectId)}/preproduction`;
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

const jsonHeaders = { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" };

export function getPreproductionWorkspace(projectId: string): Promise<PreproductionWorkspace> {
  return request(baseUrl(projectId), {}, "无法读取前置工作台，请重试。");
}

export function savePreproductionWorkspace(projectId: string, workspace: PreproductionSave): Promise<PreproductionWorkspace> {
  return request(baseUrl(projectId), {
    method: "PUT", headers: jsonHeaders,
    body: JSON.stringify({ revision: workspace.revision, brief: workspace.brief, shots: workspace.shots }),
  }, "无法保存前置工作台，请刷新后重试。");
}

export function uploadPreproductionAsset(projectId: string, role: AssetRole, file: File): Promise<PreproductionWorkspace> {
  const data = new FormData();
  data.append("file", file);
  return request(`${baseUrl(projectId)}/assets?role=${encodeURIComponent(role)}`, { method: "POST", body: data }, "无法上传素材，请重试。");
}

export function importReferenceAssets(projectId: string, revision: number): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/import-reference`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }) }, "无法导入现有参考素材，请重试。");
}

export function importPreparationShots(projectId: string, revision: number): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/import-shots`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }) }, "无法导入已有分镜，请重试。");
}

export function importToolkitAssets(projectId: string, revision: number): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/import-toolkit`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }) }, "无法导入工具产物，请重试。");
}

export function runPreproductionNode(projectId: string, shotId: string, nodeId: string, revision: number): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/shots/${encodeURIComponent(shotId)}/nodes/${encodeURIComponent(nodeId)}/run`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }),
  }, "无法运行节点，请先保存并检查输入。");
}

export async function downloadPreproductionPackage(projectId: string, revision: number): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl(projectId)}/package`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }) });
  } catch {
    throw new Error(connectionError);
  }
  if (!response.ok) throw new Error(await readApiError(response, "无法导出当前工作台包，请先完成交付检查。"));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `preproduction-${projectId}.zip`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
