import { createDraftCache } from "./draftStorage";
import { type ChangeEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, Download, FilePlus2, LoaderCircle, PackageCheck, Play, Save, Trash2, Upload } from "lucide-react";
import { useStore } from "zustand";

import type { Project } from "./models";
import {
  downloadPreproductionPackage,
  getPreproductionWorkspace,
  importPreparationShots,
  importToolkitAssets,
  importReferenceAssets,
  importYoutubeVideo,
  getYoutubeImportRun,
  cancelYoutubeImportRun,
  retryYoutubeImportRun,
  probeYoutubeVideo,
  importBilibiliVideo,
  getBilibiliImportRun,
  cancelBilibiliImportRun,
  retryBilibiliImportRun,
  probeBilibiliVideo,
  importDouyinVideo,
  getDouyinImportRun,
  cancelDouyinImportRun,
  retryDouyinImportRun,
  probeDouyinVideo,
  runPreproductionNode,
  savePreproductionWorkspace,
  uploadPreproductionAsset,
  uploadShotResult,
  reviewShotResult,
  type AssetRole,
  type CanvasLayout,
  type NodeKind,
  type PreproductionNode,
  type PreproductionShot,
  type PreproductionWorkspace as Workspace,
  type YoutubeProbe,
  type YoutubeImportRun,
  type BilibiliProbe,
  type BilibiliImportRun,
  type DouyinProbe,
  type DouyinImportRun,
} from "./preproductionApi";
import "./preproduction.css";
import { ReproductionFlow } from "./ReproductionFlow";
import { AssetManager } from "./AssetManager";
import { ShotResultVersions } from "./ShotResultVersions";
import { CandidateResultComparison, ShotResultComparison } from "./ShotResultComparison";
import { TimelineEditor } from "./TimelineEditor";
import { BatchEditor } from "./BatchEditor";
import { ShotWorkspaceLayout } from "./ShotWorkspaceLayout";
import { createPreproductionWorkspaceStore, selectWorkspaceDirty, selectWorkspaceSnapshot } from "./preproductionWorkspaceStore";
import { ImageGenerationPanel } from "./ImageGenerationPanel";
import { VideoGenerationPanel } from "./VideoGenerationPanel";
import { XiaohongshuImportPanel } from "./XiaohongshuImportPanel";
import { KuaishouImportPanel } from "./KuaishouImportPanel";

type Props = { project: Project; tools: ReactNode; onDraftChange?: (projectId: string, dirty: boolean) => void;
  importedWorkspace?: {projectId:string;workspace:Workspace}; sectionOverride?: Section; studioMode?: boolean; onAssetsChanged?: (assets: Workspace["assets"]) => void };
type Section = "brief" | "assets" | "shots" | "tools" | "timeline" | "batch" | "delivery";

const roleLabels: Record<AssetRole, string> = { character: "角色", scene: "场景", motion: "动作", audio: "音频", reference: "参考" };
const kindLabels: Record<NodeKind, string> = {
  reference: "引用素材", trim: "裁切时段", first_frame: "首帧", last_frame: "尾帧", crop: "画面裁切", resize: "调整尺寸", prompt: "提示词",
};
const statusLabels = { pending: "待运行", queued: "已排队", running: "运行中", completed: "已完成", failed: "错误", stale: "已过期" } as const;
const activeStatuses = new Set(["queued", "running"]);

function TextAssetPreview({ url, name }: { url: string; name: string }) {
  const [text, setText] = useState<string>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setText(undefined); setError("");
    void fetch(url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error("文本素材暂不可读取，请重试下载。");
      return response.text();
    }).then(value => { if (!controller.signal.aborted) setText(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "文本素材暂不可读取。"); });
    return () => controller.abort();
  }, [url]);
  return error ? <p role="alert">{error}</p> : <pre aria-label={`${name} 预览`} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 240, overflow: "auto" }}>{text ?? "正在读取文本…"}</pre>;
}

function isVideoFile(file: File) {
  return file.type.startsWith("video/") || /\.(mp4|mov|webm)$/i.test(file.name);
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function createShot(sceneId: string, rank: string): PreproductionShot {
  return { id: `shot-${Date.now()}`, sceneId, rank, title: "新镜头", duration: 3, prompt: "", negativePrompt: "", assetIds: [], nodes: [] };
}

function createNode(kind: NodeKind): PreproductionNode {
  return { id: `node-${Date.now()}`, kind, input: "", params: kind === "prompt" ? { text: "" } : {}, status: "pending", artifacts: [] };
}

function emptyWorkspace(projectId: string): Workspace {
  return {
    schemaVersion: 2,
    revision: 0,
    brief: { theme: "", purpose: "", style: "", duration: 0, aspect: "", mustPreserve: "" },
    assets: [], scenes: [], shots: [], workflow: { nodes: [], edges: [] },
    canvasLayout: { scope: { type: "project", id: projectId }, layoutRevision: 0, nodes: {} },
    checks: [], nodeCatalog: [],
  };
}

function editableSnapshot(workspace: Workspace | null) {
  return workspace ? JSON.stringify({ revision: workspace.revision, brief: workspace.brief, scenes: workspace.scenes, shots: workspace.shots }) : "";
}

function draftKey(projectId: string) { return `aivre:preproduction-draft:${projectId}`; }
async function readDraft(cache: ReturnType<typeof createDraftCache>): Promise<{
  draft: Pick<Workspace, "revision" | "brief" | "shots"> & {
    scenes?: Workspace["scenes"];
    canvasLayout?: CanvasLayout;
  };
  baseline: string;
} | null> {
  try {
    const cached = JSON.parse(await cache.read() ?? "null");
    if (!cached || typeof cached.baseline !== "string" || !Number.isInteger(cached.draft?.revision)
      || !cached.draft.brief || (cached.draft.scenes !== undefined && !Array.isArray(cached.draft.scenes)) || !Array.isArray(cached.draft.shots)
      || (cached.draft.canvasLayout !== undefined && !isDraftLayout(cached.draft.canvasLayout))
      || !cached.draft.shots.every((shot: PreproductionShot) => shot && Array.isArray(shot.nodes) && Array.isArray(shot.assetIds))) return null;
    return cached;
  } catch { return null; }
}

function isDraftLayout(value: unknown): value is CanvasLayout {
  if (!value || typeof value !== "object") return false;
  const layout = value as Partial<CanvasLayout>;
  return Boolean(layout.scope && ["project", "scene", "shot"].includes(layout.scope.type)
    && typeof layout.scope.id === "string"
    && Number.isInteger(layout.layoutRevision)
    && layout.layoutRevision! >= 0
    && layout.nodes && typeof layout.nodes === "object");
}

function updateShot(workspace: Workspace, shotId: string, change: (shot: PreproductionShot) => PreproductionShot): Workspace {
  return { ...workspace, shots: workspace.shots.map((shot) => shot.id === shotId ? change(shot) : shot) };
}

function InputOptions({ workspace, shot, nodeIndex }: { workspace: Workspace; shot: PreproductionShot; nodeIndex: number }) {
  const earlierNodes = shot.nodes.slice(0, nodeIndex);
  return <>
    <option value="">请选择输入</option>
    {workspace.assets.filter((asset) => shot.assetIds.includes(asset.id)).map((asset) => <option key={`asset-${asset.id}`} value={`asset:${asset.id}`}>素材：{asset.name}</option>)}
    {earlierNodes.map((node) => <option key={`node-${node.id}`} value={`node:${node.id}`}>前一步：{kindLabels[node.kind]}{node.status === "completed" && node.artifacts.length > 0 ? "" : `（${statusLabels[node.status]}）`}</option>)}
  </>;
}

function NodeParameters({ node, onChange }: { node: PreproductionNode; onChange: (params: Record<string, unknown>) => void }) {
  const number = (name: string, label: string) => <label>{label}<input aria-label={label} type="number" step="0.01" value={String(node.params[name] ?? "")} onChange={(event) => onChange({ ...node.params, [name]: Number(event.target.value) })} /></label>;
  if (node.kind === "trim") return <div className="preproduction-node-parameters">{number("start", "开始秒数")}{number("end", "结束秒数")}</div>;
  if (node.kind === "crop") return <div className="preproduction-node-parameters">{number("x", "裁切 X")}{number("y", "裁切 Y")}{number("width", "裁切宽度")}{number("height", "裁切高度")}</div>;
  if (node.kind === "resize") return <div className="preproduction-node-parameters">{number("width", "宽度像素")}{number("height", "高度像素")}</div>;
  if (node.kind === "prompt") return <label className="preproduction-full-field">节点提示词<textarea value={String(node.params.text ?? "")} onChange={(event) => onChange({ ...node.params, text: event.target.value })} /></label>;
  return null;
}

export function PreproductionWorkspace({ project, tools, onDraftChange, sectionOverride, studioMode = false, onAssetsChanged, importedWorkspace }: Props) {
  const draftCache = useMemo(() => createDraftCache(draftKey(project.id)), [project.id]);
  const workspaceStore = useMemo(() => createPreproductionWorkspaceStore(emptyWorkspace(project.id)), [project.id]);
  const resultInputRef = useRef<HTMLInputElement>(null);
  const [toolTarget, setToolTarget] = useState<string | null>(null);
  const [section, setSection] = useState<Section>(sectionOverride ?? "shots");
  useEffect(() => { if (sectionOverride) setSection(sectionOverride); }, [sectionOverride]);
  const workspace = useStore(workspaceStore, selectWorkspaceSnapshot);
  const [savedWorkspace, setSavedWorkspace] = useState<Workspace | null>(null);
  useEffect(() => { if(savedWorkspace) onAssetsChanged?.(savedWorkspace.assets); }, [savedWorkspace,onAssetsChanged]);
  const savedSnapshot = useStore(workspaceStore, (state) => state.persistence.savedSnapshot);
  const dirty = useStore(workspaceStore, selectWorkspaceDirty);
  const layoutSaving = useStore(workspaceStore, (state) => state.layoutPersistence.saveStatus === "saving");
  const selectedShotIds = useStore(workspaceStore, (state) => state.selection.selectedShotIds);
  const primaryEntity = useStore(workspaceStore, (state) => state.selection.primaryEntity);
  const [managedAssetId, setManagedAssetId] = useState<string | null>(null);
  const [imageGenerationOpen, setImageGenerationOpen] = useState(false);
  const [videoGenerationOpen, setVideoGenerationOpen] = useState(false);
  useEffect(() => { setImageGenerationOpen(false); }, [project.id]);
  const [roleFilter, setRoleFilter] = useState<AssetRole | "all">("all");
  const [uploadRole, setUploadRole] = useState<AssetRole>("reference");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [youtubeProbe, setYoutubeProbe] = useState<YoutubeProbe | null>(null);
  const [youtubeRun, setYoutubeRun] = useState<YoutubeImportRun | null>(null);
  const [youtubeDisclosureAccepted, setYoutubeDisclosureAccepted] = useState(false);
  const [youtubeUseSession, setYoutubeUseSession] = useState(false);
  const [bilibiliUrl, setBilibiliUrl] = useState("");
  const [bilibiliProbe, setBilibiliProbe] = useState<BilibiliProbe | null>(null);
  const [bilibiliPart, setBilibiliPart] = useState<number | null>(null);
  const [bilibiliRun, setBilibiliRun] = useState<BilibiliImportRun | null>(null);
  const [bilibiliDisclosureAccepted, setBilibiliDisclosureAccepted] = useState(false);
  const [bilibiliUseSession, setBilibiliUseSession] = useState(false);
  const [douyinUrl, setDouyinUrl] = useState("");
  const [douyinProbe, setDouyinProbe] = useState<DouyinProbe | null>(null);
  const [douyinRun, setDouyinRun] = useState<DouyinImportRun | null>(null);
  const [douyinDisclosureAccepted, setDouyinDisclosureAccepted] = useState(false);
  const [douyinUseSession, setDouyinUseSession] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const [draftNotice, setDraftNotice] = useState("");
  const loadedProjectRef = useRef<string | null>(null);
  const mounted = useRef(true);
  const currentProjectId = useRef(project.id);
  const [timelineDirty, setTimelineDirty] = useState(false);
  const [timelineOpened, setTimelineOpened] = useState(false);
  const [batchDirty, setBatchDirty] = useState(false);
  const [batchOpened, setBatchOpened] = useState(false);
  const [timelineFocus, setTimelineFocus] = useState<{ trackId: string; clipId: string } | null>(null);
  useEffect(() => { if (section === "timeline") setTimelineOpened(true); }, [section]);
  useEffect(() => { if (section === "batch") setBatchOpened(true); }, [section]);
  useEffect(() => { onDraftChange?.(project.id, dirty || timelineDirty || batchDirty); }, [project.id, dirty, timelineDirty, batchDirty, onDraftChange]);
  const workspaceRef = useRef<Workspace>(workspace);
  const dirtyRef = useRef(dirty);
  const busyRef = useRef<string | null>(busy);
  workspaceRef.current = workspace;
  dirtyRef.current = dirty;
  busyRef.current = busy;

  const selectedScene = primaryEntity?.type === "scene" ? workspace.scenes.find((scene) => scene.id === primaryEntity.id) ?? null : null;
  const selectedShot = primaryEntity?.type === "shot"
    ? workspace.shots.find((shot) => shot.id === primaryEntity.id) ?? null
    : primaryEntity?.type === "processNode"
      ? workspace.shots.find((shot) => shot.id === primaryEntity.shotId) ?? null
      : null;
  const selectedSceneShots = selectedScene ? workspace.shots.filter((shot) => shot.sceneId === selectedScene.id) : [];
  const multiShotSelection = primaryEntity?.type === "shot" && selectedShotIds.size > 1;
  const selectedNodeId = primaryEntity?.type === "processNode" ? primaryEntity.id : selectedShot?.nodes[0]?.id ?? null;
  const selectedNode = selectedShot?.nodes.find((node) => node.id === selectedNodeId) ?? selectedShot?.nodes[0] ?? null;
  const hasActiveNodes = workspace.shots.some((shot) => shot.nodes.some((node) => activeStatuses.has(node.status)));
  const selectedInputReady = !selectedNode || selectedNode.kind === "prompt" || !selectedNode.input.startsWith("node:") || Boolean(
    selectedShot?.nodes.find((node) => node.id === selectedNode.input.slice(5) && node.status === "completed" && node.artifacts.length > 0),
  );

  function accept(next: Workspace, submittedSnapshot?: string) {
    if (!mounted.current || currentProjectId.current !== project.id) return;
    const current = workspaceRef.current;
    if (next.revision < current.revision) return;
    workspaceStore.getState().actions.acceptServerWorkspace(next, submittedSnapshot);
    setSavedWorkspace(next);
  }

  useEffect(() => {
    mounted.current = true;
    currentProjectId.current = project.id;
    loadedProjectRef.current = null; setDraftNotice("");
    setLoading(true); setError(""); workspaceStore.getState().actions.hydrate(emptyWorkspace(project.id)); setSavedWorkspace(null); setTimelineFocus(null);
    void getPreproductionWorkspace(project.id).then(async (next) => {
      if (!mounted.current || currentProjectId.current !== project.id) return;
      const cached = await readDraft(draftCache);
      if (!mounted.current || currentProjectId.current !== project.id) return;
      loadedProjectRef.current = project.id;
      if (!cached) {
        if (next.canvasLayout) workspaceStore.getState().actions.acceptLayout(next.canvasLayout, true);
        accept(next);
        return;
      }
      workspaceStore.getState().actions.restoreDraft({
        ...next,
        ...cached.draft,
        scenes: cached.draft.scenes ?? next.scenes,
        canvasLayout: cached.draft.canvasLayout ?? next.canvasLayout,
      }, cached.baseline);
      if (cached.draft.canvasLayout
        && JSON.stringify(cached.draft.canvasLayout) !== JSON.stringify(next.canvasLayout)) {
        workspaceStore.getState().actions.deferLayoutToWorkspaceSave(cached.draft.canvasLayout);
      }
      setSavedWorkspace(next);
      setDraftNotice(next.revision === cached.draft.revision
        ? "已恢复此浏览器保存的未保存草稿，请保存更改。"
        : "已保存版本已更新；已保留你的旧版草稿，保存时会检查版本冲突。请先复制需要保留的内容，再重新读取已保存版本。");
    }).catch((reason) => {
      if (mounted.current && currentProjectId.current === project.id) setError(errorMessage(reason, "无法读取前置工作台。"));
    }).finally(() => {
      if (mounted.current && currentProjectId.current === project.id) setLoading(false);
    });
    return () => { mounted.current = false; };
  }, [project.id, workspaceStore]);

  useEffect(() => {
    const runId = window.sessionStorage.getItem(`aivre:youtube-import:${project.id}`);
    if (!runId) { setYoutubeRun(null); return; }
    void getYoutubeImportRun(project.id, runId).then(result => setYoutubeRun(result.run)).catch(() => {
      window.sessionStorage.removeItem(`aivre:youtube-import:${project.id}`);
      setYoutubeRun(null);
    });
  }, [project.id]);

  useEffect(() => {
    const runId = window.sessionStorage.getItem(`aivre:douyin-import:${project.id}`);
    if (!runId) { setDouyinRun(null); return; }
    void getDouyinImportRun(project.id, runId).then(result => setDouyinRun(result.run)).catch(() => {
      window.sessionStorage.removeItem(`aivre:douyin-import:${project.id}`); setDouyinRun(null);
    });
  }, [project.id]);

  useEffect(() => {
    const runId = window.sessionStorage.getItem(`aivre:bilibili-import:${project.id}`);
    if (!runId) { setBilibiliRun(null); return; }
    void getBilibiliImportRun(project.id, runId).then(result => setBilibiliRun(result.run)).catch(() => {
      window.sessionStorage.removeItem(`aivre:bilibili-import:${project.id}`);
      setBilibiliRun(null);
    });
  }, [project.id]);

  useEffect(() => {
    if (loading || !importedWorkspace || importedWorkspace.projectId !== project.id) return;
    const next = importedWorkspace.workspace;
    if (next.revision <= (workspaceRef.current?.revision ?? -1)) return;
    accept(next, dirtyRef.current ? savedSnapshot : undefined);
  }, [importedWorkspace, loading, project.id]);

  useEffect(() => {
    if (!workspace || dirty || !workspace.shots.some((shot) => shot.nodes.some((node) => activeStatuses.has(node.status)))) return undefined;
    const timer = window.setInterval(() => {
      void getPreproductionWorkspace(project.id).then((next) => {
        const currentRevision = workspaceRef.current?.revision ?? -1;
        if (mounted.current && currentProjectId.current === project.id && !dirtyRef.current && !busyRef.current && next.revision >= currentRevision) accept(next);
      }).catch(() => undefined);
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [dirty, project.id, workspace]);

  useEffect(() => {
    if (loading || !workspace || loadedProjectRef.current !== project.id) return;
    let active = true;
    void draftCache.write(dirty ? JSON.stringify({
      draft: {
        revision: workspace.revision,
        brief: workspace.brief,
        scenes: workspace.scenes,
        shots: workspace.shots,
        canvasLayout: workspace.canvasLayout,
      },
      baseline: savedSnapshot,
    }) : null).then(() => { if (active && !dirty) setDraftNotice(""); })
      .catch((reason) => { if (active) setDraftNotice(errorMessage(reason, "浏览器无法缓存草稿，请在离开项目前保存更改。")); });
    return () => { active = false; };
  }, [loading, workspace, dirty, savedSnapshot, project.id, draftCache]);

  useEffect(() => {
    if (!dirty) return;
    const warnBeforeClose = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warnBeforeClose);
    return () => window.removeEventListener("beforeunload", warnBeforeClose);
  }, [dirty]);

  function edit(change: (current: Workspace) => Workspace) { workspaceStore.getState().actions.editWorkspace(change); }

  function addShot(position?: { x: number; y: number }) {
    const sceneId = workspace.scenes[0]?.id ?? "scene-default";
    const occupiedRanks = new Set(workspace.shots.map((shot) => shot.rank));
    let nextRank = workspace.shots.reduce((highest, shot) => {
      const rank = Number(shot.rank);
      return Number.isSafeInteger(rank) ? Math.max(highest, rank) : highest;
    }, workspace.shots.length) + 1;
    while (occupiedRanks.has(String(nextRank).padStart(8, "0"))) nextRank += 1;
    const shot = createShot(sceneId, String(nextRank).padStart(8, "0"));
    if (!workspaceStore.getState().actions.createShot(shot, position)) {
      setError("无法新建镜头，请刷新工作台后重试。");
    }
  }

  function deleteSelectedNode() {
    if (!selectedShot || !selectedNode) return;
    edit((current) => updateShot(current, selectedShot.id, (shot) => ({
      ...shot,
      nodes: shot.nodes.filter((node) => node.id !== selectedNode.id).map((node) => node.input === `node:${selectedNode.id}`
        ? { ...node, input: "", status: "pending", artifacts: [] }
        : node),
    })));
  }

  async function save() {
    if (busy || workspaceStore.getState().layoutPersistence.saveStatus === "saving") return;
    setBusy("save"); setError("");
    workspaceStore.getState().actions.setSaveStatus("saving");
    const submittedSnapshot = workspaceStore.getState().persistence.editableSnapshot;
    try {
      const saved = await savePreproductionWorkspace(project.id, workspace);
      accept(saved, submittedSnapshot);
      if (saved.canvasLayout.scope.type === "project" && saved.canvasLayout.scope.id === project.id) {
        workspaceStore.getState().actions.acceptLayout(saved.canvasLayout, true);
      }
    }
    catch (reason) {
      const message = errorMessage(reason, "无法保存前置工作台。");
      workspaceStore.getState().actions.setSaveStatus(message.includes("冲突") || message.includes("已被更新") ? "conflict" : "error", message);
      setError(message);
    }
    finally { if (mounted.current) setBusy(null); }
  }

  async function importAction(kind: "reference" | "shots" | "toolkit") {
    if (!workspace || busy || dirty) return;
    setBusy(kind); setError("");
    try {
      if (kind === "reference") accept(await importReferenceAssets(project.id, workspace.revision));
      else if (kind === "shots") accept(await importPreparationShots(project.id, workspace.revision));
      else accept(await importToolkitAssets(project.id, workspace.revision));
    } catch (reason) { setError(errorMessage(reason, "无法导入已有内容。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || busy || dirty) return;
    const rightsConfirmed = !isVideoFile(file) || window.confirm("请确认：你拥有该本地视频的使用权，并同意将其导入当前项目。取消不会上传或登记素材。");
    if (!rightsConfirmed) { event.target.value = ""; return; }
    setBusy("upload"); setError("");
    try { accept(await uploadPreproductionAsset(project.id, uploadRole, file, undefined, isVideoFile(file))); }
    catch (reason) { setError(errorMessage(reason, "无法上传素材。")); }
    finally { event.target.value = ""; if (mounted.current) setBusy(null); }
  }

  async function probeYoutube() {
    if (!youtubeUrl.trim() || busy || dirty) return;
    setBusy("youtube-probe"); setError("");
    try { setYoutubeProbe(await probeYoutubeVideo(project.id, youtubeUrl.trim(), youtubeUseSession)); }
    catch (reason) { setYoutubeProbe(null); setError(errorMessage(reason, "无法读取 YouTube 视频信息。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function importYoutube() {
    if (!youtubeProbe || busy || dirty) return;
    if (!window.confirm("请确认：你拥有该 YouTube 视频的使用权。系统不会自动登录或绕过会员、私密、地区和 DRM 限制。")) return;
    if (!window.confirm(`即将向 ${youtubeProbe.externalRequest.downloadHosts.join("、")} 发起下载请求：${youtubeProbe.externalRequest.purpose}`)) return;
    setBusy("youtube-import"); setError("");
    try {
      const started = await importYoutubeVideo(project.id, {
        url: youtubeUrl.trim(), role: uploadRole, rightsConfirmed: true, externalRequestAccepted: true, useSession: youtubeUseSession,
      });
      setYoutubeRun(started.run);
      window.sessionStorage.setItem(`aivre:youtube-import:${project.id}`, started.run.id);
    } catch (reason) { setError(errorMessage(reason, "无法导入 YouTube 视频。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function refreshYoutubeRun() {
    if (!youtubeRun || busy) return;
    setBusy("youtube-status"); setError("");
    try {
      const next = await getYoutubeImportRun(project.id, youtubeRun.id);
      setYoutubeRun(next.run);
      if (["completed", "failed", "cancelled"].includes(next.run.status)) window.sessionStorage.removeItem(`aivre:youtube-import:${project.id}`);
      if (next.run.status === "completed") await reloadSavedWorkspace();
    } catch (reason) { setError(errorMessage(reason, "无法读取 YouTube 导入状态。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function changeYoutubeRun(action: "cancel" | "retry") {
    if (!youtubeRun || busy) return;
    setBusy(`youtube-${action}`); setError("");
    try { setYoutubeRun((await (action === "cancel" ? cancelYoutubeImportRun(project.id, youtubeRun.id) : retryYoutubeImportRun(project.id, youtubeRun.id))).run); }
    catch (reason) { setError(errorMessage(reason, "无法更新 YouTube 导入状态。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function probeBilibili() {
    if (!bilibiliUrl.trim() || busy || dirty) return;
    setBusy("bilibili-probe"); setError("");
    try {
      const next = await probeBilibiliVideo(project.id, bilibiliUrl.trim(), bilibiliUseSession);
      setBilibiliProbe(next); setBilibiliPart(next.parts.length === 1 ? next.parts[0].number : null);
    } catch (reason) { setBilibiliProbe(null); setBilibiliPart(null); setError(errorMessage(reason, "无法读取 B站视频信息。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function importBilibili() {
    if (!bilibiliProbe || bilibiliPart === null || busy || dirty) return;
    if (!window.confirm("请确认：你拥有所选 B站分P视频的使用权。系统不会自动登录、读取浏览器 Cookie 或绕过会员、私密、地区和 DRM 限制。")) return;
    if (!window.confirm(`即将向 ${bilibiliProbe.externalRequest.downloadHosts.join("、")} 发起下载请求：${bilibiliProbe.externalRequest.purpose}`)) return;
    setBusy("bilibili-import"); setError("");
    try {
      const started = await importBilibiliVideo(project.id, { url: bilibiliUrl.trim(), part: bilibiliPart, role: uploadRole, rightsConfirmed: true, externalRequestAccepted: true, useSession: bilibiliUseSession });
      setBilibiliRun(started.run); window.sessionStorage.setItem(`aivre:bilibili-import:${project.id}`, started.run.id);
    } catch (reason) { setError(errorMessage(reason, "无法导入 B站视频。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function probeDouyin() {
    if (!douyinUrl.trim() || busy || dirty) return;
    setBusy("douyin-probe"); setError("");
    try { setDouyinProbe(await probeDouyinVideo(project.id, douyinUrl.trim(), douyinUseSession)); }
    catch (reason) { setDouyinProbe(null); setError(errorMessage(reason, "无法读取抖音视频信息。")); }
    finally { setBusy(null); }
  }

  async function importDouyin() {
    if (!douyinProbe || busy || dirty) return;
    if (!window.confirm(`即将向 ${douyinProbe.externalRequest.downloadHosts.join("、")} 发起下载请求：${douyinProbe.externalRequest.purpose}`)) return;
    setBusy("douyin-import"); setError("");
    try {
      const started = await importDouyinVideo(project.id, { url: douyinUrl.trim(), role: uploadRole, rightsConfirmed: true, externalRequestAccepted: true, useSession: douyinUseSession });
      setDouyinRun(started.run); window.sessionStorage.setItem(`aivre:douyin-import:${project.id}`, started.run.id);
    } catch (reason) { setError(errorMessage(reason, "无法导入抖音视频。")); }
    finally { setBusy(null); }
  }

  async function refreshDouyinRun() {
    if (!douyinRun || busy) return;
    setBusy("douyin-status"); setError("");
    try {
      const next = await getDouyinImportRun(project.id, douyinRun.id); setDouyinRun(next.run);
      if (["completed", "failed", "cancelled"].includes(next.run.status)) window.sessionStorage.removeItem(`aivre:douyin-import:${project.id}`);
    } catch (reason) { setError(errorMessage(reason, "无法读取抖音导入状态。")); }
    finally { setBusy(null); }
  }

  async function changeDouyinRun(action: "cancel" | "retry") {
    if (!douyinRun || busy) return;
    setBusy(`douyin-${action}`); setError("");
    try { setDouyinRun((await (action === "cancel" ? cancelDouyinImportRun(project.id, douyinRun.id) : retryDouyinImportRun(project.id, douyinRun.id))).run); }
    catch (reason) { setError(errorMessage(reason, "无法更新抖音导入运行。")); }
    finally { setBusy(null); }
  }

  async function refreshBilibiliRun() {
    if (!bilibiliRun || busy) return;
    setBusy("bilibili-status"); setError("");
    try {
      const next = await getBilibiliImportRun(project.id, bilibiliRun.id); setBilibiliRun(next.run);
      if (["completed", "failed", "cancelled"].includes(next.run.status)) window.sessionStorage.removeItem(`aivre:bilibili-import:${project.id}`);
      if (next.run.status === "completed") await reloadSavedWorkspace();
    } catch (reason) { setError(errorMessage(reason, "无法读取 B站导入状态。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function changeBilibiliRun(action: "cancel" | "retry") {
    if (!bilibiliRun || busy) return;
    setBusy(`bilibili-${action}`); setError("");
    try { setBilibiliRun((await (action === "cancel" ? cancelBilibiliImportRun(project.id, bilibiliRun.id) : retryBilibiliImportRun(project.id, bilibiliRun.id))).run); }
    catch (reason) { setError(errorMessage(reason, "无法更新 B站导入状态。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function uploadResult(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file || !workspace || !selectedShot || busy || dirty) return;
    if (!window.confirm("请确认：你拥有该本地视频的使用权，并同意将其导入当前项目。取消不会上传或登记素材。")) { input.value = ""; return; }
    setBusy("result"); setError("");
    try { accept(await uploadShotResult(project.id, selectedShot.id, workspace.revision, file, true)); }
    catch (reason) { if (mounted.current) setError(errorMessage(reason, "无法上传镜头结果。")); }
    finally { input.value = ""; if (mounted.current) setBusy(null); }
  }

  async function reviewResult(assetId: string) {
    if (!workspace || !selectedShot || dirty || busy || hasActiveNodes) return;
    setBusy("review-result"); setError("");
    try { accept(await reviewShotResult(project.id, selectedShot.id, assetId, workspace.revision)); }
    catch (reason) { if (mounted.current) setError(errorMessage(reason, "无法记录检查状态。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function runNode() {
    if (!workspace || !selectedShot || !selectedNode || busy || dirty) return;
    setBusy("run"); setError("");
    try { accept(await runPreproductionNode(project.id, selectedShot.id, selectedNode.id, workspace.revision)); }
    catch (reason) { setError(errorMessage(reason, "无法运行节点。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function download() {
    if (!workspace || busy || dirty) return;
    setBusy("package"); setError("");
    try { await downloadPreproductionPackage(project.id, workspace.revision); }
    catch (reason) { setError(errorMessage(reason, "无法导出当前工作台包。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  async function reloadSavedWorkspace() {
    if (busy) return;
    setBusy("reload"); setError("");
    try { accept(await getPreproductionWorkspace(project.id)); }
    catch (reason) { setError(errorMessage(reason, "无法重新读取已保存版本。")); }
    finally { if (mounted.current) setBusy(null); }
  }

  useEffect(() => {
    if (section !== "tools" || !toolTarget) return;
    const target = document.getElementById(toolTarget);
    if (target) { target.tabIndex = -1; target.focus(); target.scrollIntoView?.({ block: "start" }); }
    setToolTarget(null);
  }, [section, toolTarget]);

  const visibleAssets = useMemo(() => workspace?.assets.filter((asset) => roleFilter === "all" || asset.role === roleFilter) ?? [], [roleFilter, workspace]);
  const canRun = Boolean(selectedNode && !dirty && !busy && !hasActiveNodes && selectedInputReady);

  if (loading) return <section className="preproduction-workspace preproduction-workspace--loading" aria-label="前置工作台"><LoaderCircle className="loading-spinner" aria-hidden="true" size={18} />正在读取前置工作台…</section>;
  if (!workspace.revision && error) return <section className="preproduction-workspace" aria-label="前置工作台"><p role="alert">{error}</p><button type="button" className="secondary-action" onClick={() => window.location.reload()}>重新读取</button></section>;

  return <section className="preproduction-workspace" aria-label="视频创作前置工作台">
    <header className="preproduction-header">
      <div><p className="preproduction-kicker">VIDEO PREPRODUCTION</p><h2>前置工作台</h2><p>组织需求、素材与可复用的镜头步骤。</p></div>
      <div className="preproduction-header-actions">
        {dirty && <span className="preproduction-dirty" role="status">有未保存更改</span>}
        <button type="button" className="secondary-action" disabled={!dirty || Boolean(busy) || layoutSaving || hasActiveNodes} onClick={() => void save()}><Save size={16} aria-hidden="true" />{busy === "save" ? "正在保存…" : "保存更改"}</button>
      </div>
    </header>
    {draftNotice && <p className="preproduction-draft-notice" role="status">{draftNotice}</p>}
    {error && <div className="preproduction-error" role="alert"><CircleAlert size={17} aria-hidden="true" /><span>{error}</span><button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void reloadSavedWorkspace()}>{dirty ? "放弃修改并重新读取" : "重新读取已保存版本"}</button></div>}

    {(!studioMode || sectionOverride === "shots") && <ReproductionFlow project={project} workspace={savedWorkspace ?? workspace} disabled={Boolean(busy) || hasActiveNodes} dirty={dirty} timelineDirty={timelineDirty} draftInputKind={workspace.brief.inputKind} onImportShots={() => { setSection("shots"); void importAction("shots"); }}
      onKindChange={(inputKind) => edit((current) => ({ ...current, brief: { ...current.brief, inputKind } }))}
      onNavigate={(next, target) => { setSection(next); setToolTarget(target ?? null); }}
      onLocateCheck={(check) => { if (check.shotId) { if (check.nodeId) workspaceStore.getState().actions.selectNode(check.shotId, check.nodeId); else workspaceStore.getState().actions.selectShot(check.shotId); } setSection(check.shotId ? "shots" : check.code === "brief_incomplete" ? "brief" : "shots"); }}
      onFocusClip={(trackId, clipId) => { setTimelineFocus({ trackId, clipId }); setSection("timeline"); }} />}
    <div className="preproduction-layout">
      {(!studioMode || sectionOverride === "shots") && <nav className="preproduction-nav" aria-label="工作台导航">
        {([ ["brief", "需求"], ["assets", "素材"], ["shots", "镜头"], ["tools", "工具"], ["timeline", "剪辑"], ["batch", "批量混剪"], ["delivery", "交付"] ] as const).map(([id, label]) => <button type="button" key={id} aria-current={section === id ? "page" : undefined} className={section === id ? "is-active" : ""} onClick={() => setSection(id)}>{label}</button>)}
      </nav>}

      <fieldset className="preproduction-interactions" disabled={Boolean(busy) || (hasActiveNodes && section !== "tools" && section !== "timeline" && section !== "batch")}>
      <div className="preproduction-main">
        {section === "brief" && <section className="preproduction-panel preproduction-native-form" aria-labelledby="preproduction-brief-title">
          <h3 id="preproduction-brief-title">创作需求</h3>
          <div className="preproduction-form-grid">
            {([ ["theme", "主题"], ["purpose", "用途"], ["style", "风格"], ["aspect", "画幅"], ["mustPreserve", "必须保留"] ] as const).map(([key, label]) => <label key={key}>{label}<input value={workspace.brief[key]} onChange={(event) => edit((current) => ({ ...current, brief: { ...current.brief, [key]: event.target.value } }))} /></label>)}
            <label>时长（秒）<input type="number" min="1" value={workspace.brief.duration} onChange={(event) => edit((current) => ({ ...current, brief: { ...current.brief, duration: Number(event.target.value) } }))} /></label>
          </div>
        </section>}

        {section === "assets" && <section className="preproduction-panel preproduction-native-form" aria-labelledby="preproduction-assets-title">
          <div className="preproduction-panel-heading"><div><h3 id="preproduction-assets-title">素材库</h3><p>上传后按用途标记，镜头可以绑定多份素材。</p></div><div className="preproduction-import-actions"><button type="button" className="secondary-action" onClick={() => setImageGenerationOpen((open) => !open)}>{imageGenerationOpen ? "收起图片生成" : "图片生成"}</button><button type="button" className="secondary-action" onClick={() => setVideoGenerationOpen((open) => !open)}>{videoGenerationOpen ? "收起视频生成" : "视频生成"}</button><button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => void importAction("reference")}>导入参考素材</button><button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => void importAction("shots")}>导入已有分镜</button><button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => void importAction("toolkit")}>导入工具产物</button></div></div>
          {imageGenerationOpen && <ImageGenerationPanel
            projectId={project.id}
            imageAssets={workspace.assets.filter((asset) => asset.kind === "image").map((asset) => ({
              id: asset.id,
              assetVersionId: (asset as typeof asset & { assetVersionId?: string }).assetVersionId ?? `${asset.id}-v1`,
              name: asset.name,
              kind: "image",
              url: asset.url,
            }))}
            onInstalled={() => void reloadSavedWorkspace()}
          />}
          {videoGenerationOpen && <VideoGenerationPanel
            projectId={project.id}
            imageAssets={workspace.assets.filter((asset) => asset.kind === "image").map((asset) => ({
              id: asset.id,
              assetVersionId: (asset as typeof asset & { assetVersionId?: string }).assetVersionId ?? `${asset.id}-v1`,
              name: asset.name,
              kind: "image",
              url: asset.url,
            }))}
            onInstalled={() => void reloadSavedWorkspace()}
          />}
          <div className="preproduction-upload"><label>标记为<select value={uploadRole} onChange={(event) => setUploadRole(event.target.value as AssetRole)}>{Object.entries(roleLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><button type="button" className="secondary-action" disabled={Boolean(busy) || dirty} onClick={() => uploadInputRef.current?.click()}><Upload size={16} aria-hidden="true" />{busy === "upload" ? "正在上传…" : "上传素材"}</button><input ref={uploadInputRef} aria-label="上传素材文件" hidden type="file" accept="image/*,video/*,audio/*" disabled={Boolean(busy) || dirty} onChange={upload} /></div>
          <div className="preproduction-upload" aria-label="导入 YouTube 视频">
            <label>YouTube 链接<input aria-label="YouTube 链接" value={youtubeUrl} placeholder="https://youtu.be/..." onChange={(event) => { setYoutubeUrl(event.target.value); setYoutubeProbe(null); }} /></label>
            <p>读取链接会访问 YouTube 页面与播放器元数据 API；确认下载后还会访问 *.googlevideo.com。默认不读取或发送会话。</p>
            <label><input type="checkbox" checked={youtubeDisclosureAccepted} onChange={(event) => setYoutubeDisclosureAccepted(event.target.checked)} />我已阅读并同意上述外发范围</label>
            <label><input type="checkbox" checked={youtubeUseSession} onChange={(event) => setYoutubeUseSession(event.target.checked)} />仅在此链接需要时使用系统安全存储中的合法会话</label>
            <button type="button" className="secondary-action" disabled={Boolean(busy) || dirty || !youtubeUrl.trim() || !youtubeDisclosureAccepted} onClick={() => void probeYoutube()}>{busy === "youtube-probe" ? "正在读取…" : "读取链接"}</button>
            {youtubeProbe && <div role="status"><strong>{youtubeProbe.title}</strong><small>适配器：{youtubeProbe.adapter.id} {youtubeProbe.adapter.version} · 状态：{youtubeProbe.status} · 最近真实验证：{youtubeProbe.lastRealVerificationAt ?? "尚无真实验证（受控测试不计入）"}</small><p>{youtubeProbe.externalRequest.purpose}</p><button type="button" className="primary-action" disabled={Boolean(busy) || dirty} onClick={() => void importYoutube()}>{busy === "youtube-import" ? "正在创建导入…" : "确认权利并下载"}</button></div>}
            {youtubeRun && <div role="status"><strong>导入运行：{youtubeRun.status}</strong><small>{youtubeRun.error ? `诊断：${youtubeRun.error}` : `运行标识：${youtubeRun.id}`}</small><button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void refreshYoutubeRun()}>刷新导入状态</button>{["queued", "running", "cancel_requested"].includes(youtubeRun.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void changeYoutubeRun("cancel")}>取消导入</button>}{["failed", "cancelled"].includes(youtubeRun.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void changeYoutubeRun("retry")}>重试导入</button>}</div>}
          </div>
          <div className="preproduction-upload" aria-label="导入 B站视频">
            <label>B站链接<input aria-label="B站链接" value={bilibiliUrl} placeholder="https://www.bilibili.com/video/BV... 或 https://b23.tv/..." onChange={(event) => { setBilibiliUrl(event.target.value); setBilibiliProbe(null); setBilibiliPart(null); }} /></label>
            <p>读取链接会访问 B站页面和元数据 API；b23.tv 仅做有限 HTTPS 跳转解析，最终必须为标准 BV 视频页。不会自动下载合集。</p>
            <label><input type="checkbox" checked={bilibiliDisclosureAccepted} onChange={(event) => setBilibiliDisclosureAccepted(event.target.checked)} />我已阅读并同意上述外发范围</label>
            <label><input type="checkbox" checked={bilibiliUseSession} onChange={(event) => setBilibiliUseSession(event.target.checked)} />仅在此链接需要时使用系统安全存储中的合法 B站会话</label>
            <button type="button" className="secondary-action" disabled={Boolean(busy) || dirty || !bilibiliUrl.trim() || !bilibiliDisclosureAccepted} onClick={() => void probeBilibili()}>{busy === "bilibili-probe" ? "正在读取…" : "读取链接与分P"}</button>
            {bilibiliProbe && <div role="status"><strong>{bilibiliProbe.title}</strong><small>适配器：{bilibiliProbe.adapter.id} {bilibiliProbe.adapter.version} · 状态：{bilibiliProbe.status} · 最近真实验证：{bilibiliProbe.lastRealVerificationAt ?? "尚无真实验证（受控测试不计入）"}</small><p>{bilibiliProbe.parts.length > 1 ? "该视频有多个分P，请明确选择一个后再下载。" : "单分P视频已可选择。"}</p><label>选择分P<select aria-label="选择 B站分P" value={bilibiliPart ?? ""} onChange={(event) => setBilibiliPart(Number(event.target.value) || null)}><option value="">请选择</option>{bilibiliProbe.parts.map(part => <option key={part.number} value={part.number}>P{part.number} · {part.title} · {part.durationSeconds ?? "未知"}秒</option>)}</select></label><button type="button" className="primary-action" disabled={Boolean(busy) || dirty || bilibiliPart === null} onClick={() => void importBilibili()}>{busy === "bilibili-import" ? "正在创建导入…" : "确认权利并下载所选分P"}</button></div>}
            {bilibiliRun && <div role="status"><strong>导入运行：{bilibiliRun.status}</strong><small>{bilibiliRun.error ? `诊断：${bilibiliRun.error}` : `运行标识：${bilibiliRun.id}`}</small><button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void refreshBilibiliRun()}>刷新导入状态</button>{["queued", "running", "cancel_requested"].includes(bilibiliRun.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void changeBilibiliRun("cancel")}>取消导入</button>}{["failed", "cancelled"].includes(bilibiliRun.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void changeBilibiliRun("retry")}>重试导入</button>}</div>}
          </div>
          <div className="preproduction-upload" aria-label="导入抖音视频">
            <label>抖音链接或分享文本<input aria-label="抖音链接或分享文本" value={douyinUrl} placeholder="https://www.douyin.com/video/... 或 https://v.douyin.com/..." onChange={(event) => { setDouyinUrl(event.target.value); setDouyinProbe(null); }} /></label>
            <p>读取分享文本只接受唯一抖音链接；v.douyin.com 仅做有限、无代理且 DNS 固定的 HTTPS 跳转解析。系统不会绕过验证码、风控、地区或私密限制。</p>
            <label><input type="checkbox" checked={douyinDisclosureAccepted} onChange={(event) => setDouyinDisclosureAccepted(event.target.checked)} />我已阅读并同意上述外发范围</label>
            <label><input type="checkbox" checked={douyinUseSession} onChange={(event) => setDouyinUseSession(event.target.checked)} />仅在此链接需要时使用系统安全存储中的合法抖音会话</label>
            <button type="button" className="secondary-action" disabled={Boolean(busy) || dirty || !douyinUrl.trim() || !douyinDisclosureAccepted} onClick={() => void probeDouyin()}>{busy === "douyin-probe" ? "正在读取…" : "读取链接"}</button>
            {douyinProbe && <div role="status"><strong>{douyinProbe.title}</strong><small>适配器：{douyinProbe.adapter.id} {douyinProbe.adapter.version} · 状态：{douyinProbe.status} · 最近真实验证：{douyinProbe.lastRealVerificationAt ?? "尚无真实验证（受控测试不计入）"}</small><p>{douyinProbe.externalRequest.purpose}</p><button type="button" className="primary-action" disabled={Boolean(busy) || dirty} onClick={() => void importDouyin()}>{busy === "douyin-import" ? "正在创建导入…" : "确认权利并下载"}</button></div>}
            {douyinRun && <div role="status"><strong>导入运行：{douyinRun.status}</strong><small>{douyinRun.error ? `诊断：${douyinRun.error}` : `运行标识：${douyinRun.id}`}</small><button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void refreshDouyinRun()}>刷新导入状态</button>{["queued", "running", "cancel_requested"].includes(douyinRun.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void changeDouyinRun("cancel")}>取消导入</button>}{["failed", "cancelled"].includes(douyinRun.status) && <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void changeDouyinRun("retry")}>重试导入</button>}</div>}
          </div>
          <XiaohongshuImportPanel projectId={project.id} role={uploadRole} disabled={dirty || Boolean(busy)} />
          <KuaishouImportPanel projectId={project.id} role={uploadRole} disabled={dirty || Boolean(busy)} onInstalled={() => void reloadSavedWorkspace()} />
          <div className="preproduction-filters" role="group" aria-label="素材标签筛选"><button type="button" aria-pressed={roleFilter === "all"} className={roleFilter === "all" ? "is-active" : ""} onClick={() => setRoleFilter("all")}>全部</button>{(Object.entries(roleLabels) as Array<[AssetRole, string]>).map(([role, label]) => <button type="button" key={role} aria-pressed={roleFilter === role} className={roleFilter === role ? "is-active" : ""} onClick={() => setRoleFilter(role)}>{label}</button>)}</div>
          {visibleAssets.length ? <ul className="preproduction-assets">{visibleAssets.map((asset) => <li key={asset.id}><span className="preproduction-asset-kind">{asset.kind}</span><div className="preproduction-asset-info"><strong>{asset.name}</strong>{asset.notes && <p>{asset.notes}</p>}<button type="button" className="secondary-action" aria-label={`管理素材 ${asset.name}`} onClick={() => setManagedAssetId(asset.id)}>管理素材</button><span className="preproduction-asset-role">{roleLabels[asset.role]}</span>{asset.width && asset.height ? <small>{asset.width}×{asset.height}</small> : null}<a className="preproduction-asset-download" href={asset.url} download>下载{asset.name}</a><details className={`preproduction-asset-preview ${studioMode ? "studio-asset-preview" : ""}`} open={studioMode || undefined}><summary>预览</summary>{asset.kind === "image" ? <img src={asset.url} alt={`${asset.name} 预览`} /> : asset.kind === "video" ? <video controls preload="metadata" aria-label={`${asset.name} 预览`} src={asset.url} /> : asset.kind === "text" ? <TextAssetPreview url={asset.url} name={asset.name} /> : <audio controls preload="metadata" aria-label={`${asset.name} 预览`} src={asset.url} />}</details></div></li>)}</ul> : <p className="preproduction-empty">还没有匹配的素材。上传素材，或从已有参考和工具结果导入。</p>}
          {workspace.assets.find((asset) => asset.id === managedAssetId) && (() => {
            const asset = workspace.assets.find((item) => item.id === managedAssetId)!;
            return <AssetManager key={`${project.id}:${asset.id}`} projectId={project.id} asset={asset} revision={workspace.revision}
              blocked={dirty || Boolean(busy) || hasActiveNodes} onBusy={(active) => setBusy(active ? "asset" : null)}
              onChanged={(next) => accept(next, editableSnapshot(workspace))} />;
          })()}
        </section>}

        {section === "shots" && <ShotWorkspaceLayout projectId={project.id} store={workspaceStore} onCreateShot={addShot}>
          <section className="preproduction-workspace-inspector" aria-label="镜头检查器">
          {selectedScene ? <section className="preproduction-selection-summary" aria-label={`${selectedScene.title}场景摘要`}>
            <h3>场景摘要</h3>
            <strong>{selectedScene.title}</strong>
            <p>{selectedScene.description || "这个场景还没有说明。"}</p>
            <p>{selectedSceneShots.length} 个镜头 · {selectedSceneShots.reduce((total, shot) => total + shot.duration, 0)} 秒</p>
          </section> : multiShotSelection ? <section className="preproduction-selection-summary" aria-label="多镜头选择摘要">
            <h3>已选择 {selectedShotIds.size} 个镜头</h3>
            <p>批量选择只显示可同时应用的操作。继续在列表或画布中调整所选镜头。</p>
          </section> : <>
          <div className="preproduction-editor">{selectedShot ? <><div className="preproduction-editor-heading"><label>镜头名称<input value={selectedShot.title} onChange={(event) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, title: event.target.value })))} /></label><label>时长（秒）<input type="number" min="0.1" step="0.1" value={selectedShot.duration} onChange={(event) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, duration: Number(event.target.value) })))} /></label><button type="button" aria-label="删除当前镜头" className="icon-action" onClick={() => edit((current) => ({ ...current, shots: current.shots.filter((shot) => shot.id !== selectedShot.id) }))}><Trash2 size={17} /></button></div>
            <label className="preproduction-full-field">画面提示词<textarea value={selectedShot.prompt} onChange={(event) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, prompt: event.target.value })))} /></label><label className="preproduction-full-field">负面提示词<textarea value={selectedShot.negativePrompt} onChange={(event) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, negativePrompt: event.target.value })))} /></label>
            <div className="preproduction-bindings"><h4>绑定素材</h4>{workspace.assets.length ? workspace.assets.map((asset) => <button type="button" key={asset.id} aria-pressed={selectedShot.assetIds.includes(asset.id)} className={selectedShot.assetIds.includes(asset.id) ? "is-bound" : ""} onClick={() => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, assetIds: shot.assetIds.includes(asset.id) ? shot.assetIds.filter((id) => id !== asset.id) : [...shot.assetIds, asset.id] })))}>{selectedShot.assetIds.includes(asset.id) ? "已绑定 " : "绑定 "}{asset.name}</button>) : <p>先在素材页添加素材。</p>}</div>
            <div className="preproduction-shot-result"><h4>本镜头生成结果</h4>
              <p>上传新结果会保留旧候选并采用新视频；选择已有视频后保存也会记录候选。剪辑从采用视频起点取计划时长，切换候选不会替换已有剪辑。</p>
              <label>结果视频<select value={selectedShot.resultAssetId ?? ""} onChange={(event) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, resultAssetId: event.target.value || null })))}><option value="">尚未关联</option>{workspace.assets.filter((asset) => asset.kind === "video").map((asset) => <option key={asset.id} value={asset.id}>{asset.name} · {asset.duration ?? "未知"} 秒</option>)}</select></label>
              <button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => resultInputRef.current?.click()}>上传本镜头结果</button>
              <input ref={resultInputRef} type="file" accept="video/mp4,video/quicktime,video/webm" hidden aria-label="上传本镜头结果文件" onChange={(event) => void uploadResult(event)} />
              {dirty && <p>先保存镜头方案，再上传结果。</p>}
              {selectedShot.resultAssetId && (() => { const asset = workspace.assets.find((item) => item.id === selectedShot.resultAssetId); return asset ? <><a href={asset.url} target="_blank" rel="noreferrer">查看结果视频</a>{(!asset.duration || asset.duration < selectedShot.duration) && <p role="status">视频时长不足以覆盖镜头计划，暂不能按镜头导入剪辑。</p>}</> : <p role="alert">关联的视频不存在，请重新选择。</p>; })()}
              <ShotResultVersions projectId={project.id} revision={workspace.revision} onBusy={(active) => setBusy(active ? "candidate-cleanup" : null)} onChanged={(next, action) => { accept(next, editableSnapshot(workspace)); setDraftNotice(action === "note" ? "候选备注已保存。" : action === "reason" ? "采用理由已保存。" : "候选已移出历史，素材文件仍保留。"); }} shot={selectedShot} assets={workspace.assets} dirty={dirty} busy={Boolean(busy) || hasActiveNodes}
                onManage={(assetId) => { setManagedAssetId(assetId); setRoleFilter("all"); setSection("assets"); }}
                onFocusStep={(nodeId) => { if (selectedShot) workspaceStore.getState().actions.selectNode(selectedShot.id, nodeId); setSection("shots"); }}
                onFocusClip={(trackId, clipId) => { setTimelineFocus({ trackId, clipId }); setSection("timeline"); }}
                onSelect={(assetId) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, resultAssetId: assetId })))} onReview={(assetId) => void reviewResult(assetId)} />
            </div>
            <div className="preproduction-nodes"><div className="preproduction-list-heading"><h4>有序步骤</h4><select aria-label="添加节点类型" defaultValue="" onChange={(event) => { const kind = event.target.value as NodeKind; if (!kind) return; const node = createNode(kind); edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, nodes: [...shot.nodes, node] }))); workspaceStore.getState().actions.selectNode(selectedShot.id, node.id); event.currentTarget.value = ""; }}><option value="">添加节点…</option>{workspace.nodeCatalog.map((node) => <option key={node.kind} value={node.kind}>{node.label}</option>)}</select></div>{selectedShot.nodes.length ? <ol>{selectedShot.nodes.map((node, index) => <li key={node.id} className={node.id === selectedNode?.id ? "is-active" : ""}><button type="button" onClick={() => workspaceStore.getState().actions.selectNode(selectedShot.id, node.id)}><span>{index + 1}</span>{kindLabels[node.kind]}<small className={`preproduction-status preproduction-status--${node.status}`}>{statusLabels[node.status]}</small></button></li>)}</ol> : <p>添加步骤以整理镜头输入和产物。</p>}</div>
          </> : <div className="preproduction-empty"><h3>从已有参考或分镜开始</h3><p>导入现有内容，或新建镜头来准备下一步。</p><button type="button" className="secondary-action" disabled={dirty || Boolean(busy)} onClick={() => void importAction("reference")}>导入参考素材</button><button type="button" className="primary-action" onClick={() => addShot()}>新建镜头</button></div>}</div>
          <aside className="preproduction-node-inspector">{selectedShot && selectedNode ? <><h3>步骤参数</h3><label>节点类型<select value={selectedNode.kind} onChange={(event) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, nodes: shot.nodes.map((node) => node.id === selectedNode.id ? { ...node, kind: event.target.value as NodeKind, params: event.target.value === "prompt" ? { text: "" } : {}, status: "pending", artifacts: [] } : node) })))}>{workspace.nodeCatalog.map((node) => <option key={node.kind} value={node.kind}>{node.label}</option>)}</select></label>{selectedNode.kind !== "prompt" && <label>输入<select value={selectedNode.input} onChange={(event) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, nodes: shot.nodes.map((node) => node.id === selectedNode.id ? { ...node, input: event.target.value, status: "pending", artifacts: [] } : node) })))}><InputOptions workspace={workspace} shot={selectedShot} nodeIndex={selectedShot.nodes.findIndex((node) => node.id === selectedNode.id)} /></select></label>}<NodeParameters node={selectedNode} onChange={(params) => edit((current) => updateShot(current, selectedShot.id, (shot) => ({ ...shot, nodes: shot.nodes.map((node) => node.id === selectedNode.id ? { ...node, params, status: "pending", artifacts: [] } : node) })))} />
            <p className={`preproduction-node-status preproduction-node-status--${selectedNode.status}`}>{statusLabels[selectedNode.status]}{selectedNode.error ? `：${selectedNode.error}` : ""}</p>{selectedNode.artifacts.map((artifact) => <a key={artifact.name} href={artifact.url} download>下载 {artifact.name}</a>)}{!selectedInputReady && <p className="preproduction-save-note">等待所选前序节点完成后才能运行。</p>}<button type="button" className="primary-action" disabled={!canRun} onClick={() => void runNode()}><Play size={16} aria-hidden="true" />{busy === "run" ? "正在提交…" : "运行当前节点"}</button><button type="button" className="icon-action" aria-label="删除当前节点" onClick={deleteSelectedNode}><Trash2 size={17} /></button>{dirty && <p className="preproduction-save-note">先保存更改，才能运行或导出。</p>}</> : <p className="preproduction-empty">选择一个步骤后编辑参数。</p>}</aside>
          {selectedShot && <CandidateResultComparison shot={selectedShot} assets={workspace.assets} disabled={Boolean(busy) || hasActiveNodes} />}
          {selectedShot && <ShotResultComparison shot={selectedShot} assets={workspace.assets} disabled={Boolean(busy) || hasActiveNodes} />}
          </>}
          </section>
        </ShotWorkspaceLayout>}

        {section === "tools" && <section className="preproduction-panel" aria-labelledby="preproduction-tools-title"><h3 id="preproduction-tools-title">现有工具</h3><p>这些工具沿用原有面板；完成后可在素材页导入其产物并绑定镜头。</p>{tools}</section>}

        {(section === "timeline" || timelineOpened) && <div hidden={section !== "timeline"}><TimelineEditor onDraftChange={setTimelineDirty} focusClip={timelineFocus} projectId={project.id} preproductionRevision={workspace.revision} preparationDirty={dirty} shotResults={workspace.shots} /></div>}

        {(section === "batch" || batchOpened) && <div hidden={section !== "batch"}><BatchEditor projectId={project.id} onDraftChange={setBatchDirty} /></div>}

        {section === "delivery" && <section className="preproduction-panel" aria-labelledby="preproduction-delivery-title">
          <div className="preproduction-panel-heading">
            <div><h3 id="preproduction-delivery-title">交付检查</h3><p>导出包含逐镜交接说明（HANDOFF.md）、需求、提示词、已引用素材、当前步骤产物及检查报告。解压后先阅读交接说明，再到外部工具继续制作。</p></div>
            <button type="button" className="primary-action" disabled={dirty || Boolean(busy) || workspace.checks.some((check) => check.level === "error")} onClick={() => void download()}><Download size={16} aria-hidden="true" />{busy === "package" ? "正在打包…" : "下载 ZIP"}</button>
          </div>
          <p>错误需要修复后导出；提醒可随交付包保留，供后续制作确认。</p>
          {dirty && <p className="preproduction-save-note">检查结果基于已保存版本。保存当前更改后重新检查，才能导出。</p>}
          {workspace.checks.length ? <ul className="preproduction-checks">{workspace.checks.map((check, index) => {
            const shot = workspace.shots.find((item) => item.id === check.shotId);
            const node = shot?.nodes.find((item) => item.id === check.nodeId);
            const location = shot ? `${shot.title || shot.id}${node ? ` · ${kindLabels[node.kind]}` : ""}` : "";
            return <li key={`${check.message}-${index}`} className={`preproduction-check--${check.level}`}>
              <CircleAlert size={16} aria-hidden="true" />
              <span>{check.level === "error" ? "错误" : "提醒"}：{location && `${location}：`}{check.message}</span>
              {shot ? <button type="button" className="secondary-action" aria-label={`定位${location}`} onClick={() => {
                const nodeId = node?.id ?? shot.nodes[0]?.id;
                if (nodeId) workspaceStore.getState().actions.selectNode(shot.id, nodeId);
                else workspaceStore.getState().actions.selectShot(shot.id);
                setSection("shots");
              }}>定位</button> : !check.shotId && <button type="button" className="secondary-action" onClick={() => setSection(workspace.shots.length ? "brief" : "shots")}>{workspace.shots.length ? "查看需求" : "添加镜头"}</button>}
            </li>;
          })}</ul> : <p className="preproduction-delivery-ready"><PackageCheck size={18} aria-hidden="true" />尚未发现交付阻断项。</p>}
        </section>}

      </div>
    </fieldset>
    </div>
  </section>;
}
