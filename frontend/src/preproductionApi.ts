import { readApiError } from "./referenceMediaApi";

export type AssetRole = "character" | "scene" | "motion" | "audio" | "reference";
export type AssetKind = "image" | "video" | "audio";
export type NodeKind = "reference" | "trim" | "first_frame" | "last_frame" | "crop" | "resize" | "prompt";
export type NodeStatus = "pending" | "queued" | "running" | "completed" | "failed" | "stale";

export type ReproductionInputKind = "reference_video" | "depth_video" | "white_model_video";

export type PreproductionBrief = {
  inputKind?: ReproductionInputKind;
  theme: string;
  purpose: string;
  style: string;
  duration: number;
  aspect: string;
  mustPreserve: string;
};

export type PreproductionAsset = {
  source?: {pageUrl:string;imageUrl:string;productName:string;retrievedAt:string;usage:string};
  notes?: string;
  available?: boolean;
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

export type ResultAssociation = {
  revision: number;
  brief: PreproductionBrief;
  shot: Pick<PreproductionShot, "id" | "title" | "duration" | "prompt" | "negativePrompt">;
  assets: Array<Pick<PreproductionAsset, "id" | "name" | "kind" | "role">>;
  nodes: Array<Pick<PreproductionNode, "id" | "kind" | "input" | "params"> & { outputs: string[] }>;
};
export type ShotResultVersion = { assetId: string; reviewed: boolean; planChanged: boolean; association?: ResultAssociation; externalNote?: string; adoptionReason?: string };

export type PreproductionShot = {
  id: string;
  title: string;
  duration: number;
  prompt: string;
  negativePrompt: string;
  resultAssetId?: string | null;
  resultVersions?: ShotResultVersion[];
  assetIds: string[];
  nodes: PreproductionNode[];
};

export type PreproductionCheck = {
  level: "error" | "warning";
  code?: string;
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

export function uploadShotResult(projectId: string, shotId: string, revision: number, file: File): Promise<PreproductionWorkspace> {
  const data = new FormData();
  data.append("file", file);
  return request(`${baseUrl(projectId)}/assets?role=motion&resultForShot=${encodeURIComponent(shotId)}&revision=${revision}`, { method: "POST", body: data }, "无法上传镜头结果，请检查视频并重新读取方案。");
}

export function reviewShotResult(projectId: string, shotId: string, assetId: string, revision: number): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/shots/${encodeURIComponent(shotId)}/results/${encodeURIComponent(assetId)}/review`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }),
  }, "无法记录检查状态，请重新读取当前方案。");
}

export function updateShotResultNote(projectId: string, shotId: string, assetId: string, revision: number, note: string): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/shots/${encodeURIComponent(shotId)}/results/${encodeURIComponent(assetId)}/note`, {
    method: "PUT", headers: jsonHeaders, body: JSON.stringify({ revision, note }),
  }, "无法保存候选备注，请重新读取当前方案。");
}

export function updateShotAdoptionReason(projectId: string, shotId: string, assetId: string, revision: number, reason: string): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/shots/${encodeURIComponent(shotId)}/results/${encodeURIComponent(assetId)}/adoption-reason`, {
    method: "PUT", headers: jsonHeaders, body: JSON.stringify({ revision, reason }),
  }, "无法保存采用理由，请重新读取当前方案。");
}

export type AssetReference = { kind: string; label: string; trackId?: string; clipId?: string; runId?: string };
export function getAssetReferences(projectId: string, assetId: string): Promise<{ references: AssetReference[] }> {
  return request(`${baseUrl(projectId)}/assets/${encodeURIComponent(assetId)}/references`, {}, "无法读取素材引用。");
}
export function updateAssetMetadata(projectId: string, assetId: string, revision: number, name: string, notes: string): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/assets/${encodeURIComponent(assetId)}`, {
    method: "PUT", headers: jsonHeaders, body: JSON.stringify({ revision, name, notes }),
  }, "无法保存素材信息。");
}
export function deletePreproductionAsset(projectId: string, assetId: string, revision: number): Promise<PreproductionWorkspace> {
  return request(`${baseUrl(projectId)}/assets/${encodeURIComponent(assetId)}/delete`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify({ revision }),
  }, "无法删除素材。");
}
