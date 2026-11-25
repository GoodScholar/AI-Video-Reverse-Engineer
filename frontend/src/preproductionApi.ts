import { readApiError } from "./referenceMediaApi";

export type AssetRole = "character" | "scene" | "motion" | "audio" | "reference";
export type AssetKind = "image" | "video" | "audio" | "text";
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
  assetVersionId?: string;
  sha256?: string;
  videoSource?: {
    schemaVersion: 1;
    sourceId: string;
    sourceType: "local" | "youtube" | "bilibili" | "douyin" | "xiaohongshu" | "kuaishou";
    originalReference: string;
    adapter: { id: string; version: string };
    probe: { durationSeconds: number; width: number; height: number };
    rightsConfirmation?: { confirmedAt: string };
    contentHash: string;
    verifiedAt: string;
    status: "available" | "requires_login" | "temporarily_unavailable" | "unsupported_link";
  };
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

export type PreproductionScene = {
  id: string;
  title: string;
  rank: string;
  description: string;
};

export type PreproductionShot = {
  id: string;
  sceneId: string;
  rank: string;
  title: string;
  duration: number;
  prompt: string;
  negativePrompt: string;
  resultAssetId?: string | null;
  resultVersions?: ShotResultVersion[];
  assetIds: string[];
  nodes: PreproductionNode[];
};

export type WorkflowNode =
  | { id: string; type: "shot"; shotId: string }
  | {
      id: string;
      type: "process";
      ownerShotId: string;
      processKind: NodeKind;
      config: { input: string; params: Record<string, unknown> };
      status: NodeStatus;
      error?: string | null;
      artifacts: Array<{ name: string; url: string }>;
    }
  | { id: string; type: "asset"; assetId: string; ownerShotId?: string; role?: AssetRole };

export type WorkflowEdge = {
  id: string;
  kind: "data";
  source: { nodeId: string; portId: string };
  target: { nodeId: string; portId: string };
};

export type CanvasLayout = {
  scope: { type: "project" | "scene" | "shot"; id: string };
  layoutRevision: number;
  nodes: Record<string, {
    x: number;
    y: number;
    width?: number;
    height?: number;
    collapsed?: boolean;
  }>;
  viewport?: { x: number; y: number; zoom: number };
};

export type PreproductionCheck = {
  level: "error" | "warning";
  code?: string;
  shotId?: string;
  nodeId?: string;
  message: string;
};

export type YoutubeProbe = {
  canonicalUrl: string;
  videoId: string;
  title: string;
  durationSeconds: number | null;
  uploader: string | null;
  status: "available" | "requires_login" | "temporarily_unavailable" | "unsupported_link";
  adapter: { id: "yt-dlp"; version: string };
  externalRequest: { probeHosts: string[]; downloadHosts: string[]; purpose: string; sessionPolicy: string };
  lastRealVerificationAt: string | null;
};

export type YoutubeImportRequest = {
  url: string;
  role: AssetRole;
  rightsConfirmed: boolean;
  externalRequestAccepted: boolean;
  useSession?: boolean;
};

export type YoutubeImportRun = {
  id: string;
  status: "queued" | "running" | "cancel_requested" | "completed" | "failed" | "cancelled";
  error: string | null;
  createdAt: string;
  inputFingerprint: string;
  idempotencyKey: string;
  retryOf: string | null;
  uploadedAssetId: string | null;
};

export type BilibiliProbe = {
  canonicalUrl: string;
  videoId: string;
  title: string;
  uploader: string | null;
  parts: Array<{ number: number; cid: string; title: string; durationSeconds: number | null }>;
  status: "available" | "requires_login" | "temporarily_unavailable" | "unsupported_link";
  adapter: { id: "yt-dlp"; version: string };
  externalRequest: { probeHosts: string[]; downloadHosts: string[]; purpose: string; sessionPolicy: string; shortLinkPolicy: string };
  lastRealVerificationAt: string | null;
};

export type BilibiliImportRequest = {
  url: string;
  part: number;
  role: AssetRole;
  rightsConfirmed: boolean;
  externalRequestAccepted: boolean;
  useSession?: boolean;
};

export type BilibiliImportRun = {
  id: string;
  status: "queued" | "running" | "cancel_requested" | "completed" | "failed" | "cancelled";
  error: string | null;
  createdAt: string;
  inputFingerprint: string;
  idempotencyKey: string;
  retryOf: string | null;
  uploadedAssetId: string | null;
};

export type DouyinProbe = YoutubeProbe & {
  externalRequest: { probeHosts: string[]; downloadHosts: string[]; purpose: string; sessionPolicy: string; shortLinkPolicy: string };
};
export type DouyinImportRequest = YoutubeImportRequest;
export type DouyinImportRun = YoutubeImportRun;

export type KuaishouProbe = Omit<DouyinProbe, "adapter"> & { adapter: { id: "kuaishou-public-page"; version: string } };
export type KuaishouImportRequest = YoutubeImportRequest;
export type KuaishouImportRun = YoutubeImportRun;
export type KuaishouDisclosure = {
  probeHosts: string[];
  downloadHosts: string[];
  purpose: string;
  sessionPolicy: string;
  shortLinkPolicy: string;
};

export type PreproductionWorkspace = {
  uploadedAssetId?: string;
  schemaVersion: 2;
  revision: number;
  brief: PreproductionBrief;
  assets: PreproductionAsset[];
  scenes: PreproductionScene[];
  shots: PreproductionShot[];
  workflow: { nodes: WorkflowNode[]; edges: WorkflowEdge[] };
  canvasLayout: CanvasLayout;
  checks: PreproductionCheck[];
  nodeCatalog: Array<{ kind: NodeKind; label: string }>;
};

export type PreproductionSave = Pick<PreproductionWorkspace, "revision" | "brief"> & {
  scenes: PreproductionScene[];
  shots: PreproductionShot[];
  canvasLayout?: CanvasLayout;
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
  const canvasLayout = workspace.canvasLayout?.scope.type === "project" && workspace.canvasLayout.scope.id === projectId
    ? workspace.canvasLayout
    : undefined;
  return request(baseUrl(projectId), {
    method: "PUT", headers: jsonHeaders,
    body: JSON.stringify({
      revision: workspace.revision,
      brief: workspace.brief,
      scenes: workspace.scenes,
      shots: workspace.shots,
      ...(canvasLayout ? { canvasLayout } : {}),
    }),
  }, "无法保存前置工作台，请刷新后重试。");
}

function layoutUrl(projectId: string, scope: CanvasLayout["scope"]) {
  return `${baseUrl(projectId)}/layouts/${scope.type}/${encodeURIComponent(scope.id)}`;
}

export function getCanvasLayout(projectId: string, scope: CanvasLayout["scope"]): Promise<CanvasLayout> {
  return request(layoutUrl(projectId, scope), {}, "无法读取画布布局，请重试。");
}

export function saveCanvasLayout(projectId: string, layout: CanvasLayout): Promise<CanvasLayout> {
  return request(layoutUrl(projectId, layout.scope), {
    method: "PUT", headers: jsonHeaders,
    body: JSON.stringify({
      layoutRevision: layout.layoutRevision,
      nodes: layout.nodes,
      viewport: layout.viewport,
    }),
  }, "无法保存画布布局，请重新读取后重试。");
}

export function uploadPreproductionAsset(projectId: string, role: AssetRole, file: File, derivation?: Omit<ImageGeometryOperation, "createdAt">, rightsConfirmed = false): Promise<PreproductionWorkspace> {
  const data = new FormData();
  data.append("file", file);
  if (derivation) data.append("derivation", JSON.stringify(derivation));
  if (rightsConfirmed) data.append("rightsConfirmed", "true");
  return request(`${baseUrl(projectId)}/assets?role=${encodeURIComponent(role)}`, { method: "POST", body: data }, "无法上传素材，请重试。");
}

export function probeYoutubeVideo(projectId: string, url: string, useSession = false): Promise<YoutubeProbe> {
  return request(`${baseUrl(projectId)}/youtube/probe`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify({ url, externalRequestAccepted: true, useSession }),
  }, "无法读取 YouTube 视频信息，请检查链接或稍后重试。");
}

export function importYoutubeVideo(projectId: string, body: YoutubeImportRequest): Promise<{ run: YoutubeImportRun }> {
  return request(`${baseUrl(projectId)}/youtube/import`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify(body),
  }, "无法导入 YouTube 视频，请检查授权、链接和网络状态。");
}

export function getYoutubeImportRun(projectId: string, runId: string): Promise<{ run: YoutubeImportRun }> {
  return request(`${baseUrl(projectId)}/youtube/imports/${encodeURIComponent(runId)}`, {}, "无法读取 YouTube 导入状态，请重试。");
}

export function getYoutubeImportRuns(projectId: string): Promise<{ runs: YoutubeImportRun[] }> {
  return request(`${baseUrl(projectId)}/youtube/imports`, {}, "无法恢复 YouTube 导入运行状态，请重试。");
}

export function cancelYoutubeImportRun(projectId: string, runId: string): Promise<{ run: YoutubeImportRun }> {
  return request(`${baseUrl(projectId)}/youtube/imports/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers: jsonHeaders }, "无法取消 YouTube 导入，请重试。");
}

export function retryYoutubeImportRun(projectId: string, runId: string): Promise<{ run: YoutubeImportRun }> {
  return request(`${baseUrl(projectId)}/youtube/imports/${encodeURIComponent(runId)}/retry`, { method: "POST", headers: jsonHeaders }, "无法重试 YouTube 导入，请重试。");
}

export function probeBilibiliVideo(projectId: string, url: string, useSession = false): Promise<BilibiliProbe> {
  return request(`${baseUrl(projectId)}/bilibili/probe`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify({ url, externalRequestAccepted: true, useSession }),
  }, "无法读取 B站视频信息，请检查链接或稍后重试。");
}

export function importBilibiliVideo(projectId: string, body: BilibiliImportRequest): Promise<{ run: BilibiliImportRun }> {
  return request(`${baseUrl(projectId)}/bilibili/import`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify(body),
  }, "无法导入 B站视频，请检查授权、分P、链接和网络状态。");
}

export function getBilibiliImportRun(projectId: string, runId: string): Promise<{ run: BilibiliImportRun }> {
  return request(`${baseUrl(projectId)}/bilibili/imports/${encodeURIComponent(runId)}`, {}, "无法读取 B站导入状态，请重试。");
}

export function cancelBilibiliImportRun(projectId: string, runId: string): Promise<{ run: BilibiliImportRun }> {
  return request(`${baseUrl(projectId)}/bilibili/imports/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers: jsonHeaders }, "无法取消 B站导入，请重试。");
}

export function retryBilibiliImportRun(projectId: string, runId: string): Promise<{ run: BilibiliImportRun }> {
  return request(`${baseUrl(projectId)}/bilibili/imports/${encodeURIComponent(runId)}/retry`, { method: "POST", headers: jsonHeaders }, "无法重试 B站导入，请重试。");
}

export function probeDouyinVideo(projectId: string, url: string, useSession = false): Promise<DouyinProbe> {
  return request(`${baseUrl(projectId)}/douyin/probe`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify({ url, externalRequestAccepted: true, useSession }),
  }, "无法读取抖音视频信息，请检查链接或稍后重试。");
}

export function importDouyinVideo(projectId: string, body: DouyinImportRequest): Promise<{ run: DouyinImportRun }> {
  return request(`${baseUrl(projectId)}/douyin/import`, { method: "POST", headers: jsonHeaders, body: JSON.stringify(body) }, "无法导入抖音视频，请检查授权、链接和网络状态。");
}

export function getDouyinImportRun(projectId: string, runId: string): Promise<{ run: DouyinImportRun }> {
  return request(`${baseUrl(projectId)}/douyin/imports/${encodeURIComponent(runId)}`, {}, "无法读取抖音导入状态，请重试。");
}

export function cancelDouyinImportRun(projectId: string, runId: string): Promise<{ run: DouyinImportRun }> {
  return request(`${baseUrl(projectId)}/douyin/imports/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers: jsonHeaders }, "无法取消抖音导入，请重试。");
}

export function retryDouyinImportRun(projectId: string, runId: string): Promise<{ run: DouyinImportRun }> {
  return request(`${baseUrl(projectId)}/douyin/imports/${encodeURIComponent(runId)}/retry`, { method: "POST", headers: jsonHeaders }, "无法重试抖音导入，请重试。");
}

export function probeKuaishouVideo(projectId: string, url: string, rightsConfirmed: boolean, useSession = false): Promise<KuaishouProbe> {
  return request(`${baseUrl(projectId)}/kuaishou/probe`, { method: "POST", headers: jsonHeaders, body: JSON.stringify({ url, rightsConfirmed, externalRequestAccepted: true, useSession }) }, "无法读取快手视频信息，请检查链接或稍后重试。");
}
export function getKuaishouDisclosure(projectId: string): Promise<KuaishouDisclosure> {
  return request(`${baseUrl(projectId)}/kuaishou/disclosure`, {}, "无法读取快手外发说明，请稍后重试。");
}
export function importKuaishouVideo(projectId: string, body: KuaishouImportRequest): Promise<{ run: KuaishouImportRun }> {
  return request(`${baseUrl(projectId)}/kuaishou/import`, { method: "POST", headers: jsonHeaders, body: JSON.stringify(body) }, "无法导入快手视频，请检查授权、链接和网络状态。");
}
export function getKuaishouImportRun(projectId: string, runId: string): Promise<{ run: KuaishouImportRun }> { return request(`${baseUrl(projectId)}/kuaishou/imports/${encodeURIComponent(runId)}`, {}, "无法读取快手导入状态，请重试。"); }
export function cancelKuaishouImportRun(projectId: string, runId: string): Promise<{ run: KuaishouImportRun }> { return request(`${baseUrl(projectId)}/kuaishou/imports/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers: jsonHeaders }, "无法取消快手导入，请重试。"); }
export function retryKuaishouImportRun(projectId: string, runId: string): Promise<{ run: KuaishouImportRun }> { return request(`${baseUrl(projectId)}/kuaishou/imports/${encodeURIComponent(runId)}/retry`, { method: "POST", headers: jsonHeaders }, "无法重试快手导入，请重试。"); }

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

export function uploadShotResult(projectId: string, shotId: string, revision: number, file: File, rightsConfirmed = false): Promise<PreproductionWorkspace> {
  const data = new FormData();
  data.append("file", file);
  if (rightsConfirmed) data.append("rightsConfirmed", "true");
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
export type XiaohongshuProbe = DouyinProbe;
export type XiaohongshuImportRequest = YoutubeImportRequest;
export type XiaohongshuImportRun = YoutubeImportRun;

export function probeXiaohongshuVideo(projectId: string, url: string, rightsConfirmed: boolean, useSession = false): Promise<XiaohongshuProbe> {
  return request(`${baseUrl(projectId)}/xiaohongshu/probe`, {
    method: "POST", headers: jsonHeaders, body: JSON.stringify({ url, rightsConfirmed, externalRequestAccepted: true, useSession }),
  }, "无法读取小红书视频信息，请检查链接、权利确认或稍后重试。");
}

export function importXiaohongshuVideo(projectId: string, body: XiaohongshuImportRequest): Promise<{ run: XiaohongshuImportRun }> {
  return request(`${baseUrl(projectId)}/xiaohongshu/import`, { method: "POST", headers: jsonHeaders, body: JSON.stringify(body) }, "无法导入小红书视频，请检查授权、链接和网络状态。");
}

export function getXiaohongshuImportRun(projectId: string, runId: string): Promise<{ run: XiaohongshuImportRun }> {
  return request(`${baseUrl(projectId)}/xiaohongshu/imports/${encodeURIComponent(runId)}`, {}, "无法读取小红书导入状态，请重试。");
}

export function cancelXiaohongshuImportRun(projectId: string, runId: string): Promise<{ run: XiaohongshuImportRun }> {
  return request(`${baseUrl(projectId)}/xiaohongshu/imports/${encodeURIComponent(runId)}/cancel`, { method: "POST", headers: jsonHeaders }, "无法取消小红书导入，请重试。");
}

export function retryXiaohongshuImportRun(projectId: string, runId: string): Promise<{ run: XiaohongshuImportRun }> {
  return request(`${baseUrl(projectId)}/xiaohongshu/imports/${encodeURIComponent(runId)}/retry`, { method: "POST", headers: jsonHeaders }, "无法重试小红书导入，请重试。");
}
