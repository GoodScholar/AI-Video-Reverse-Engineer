/*
 * Node reference ordering is adapted from Toonflow `useNodeReferences.ts` and
 * `components/referenceItem.vue` at d86cce6b689916ab73f75bc4c1f42a2a7424516c
 * (MIT). Vue draggable is replaced by native React drag events.
 * Toolbar, title, image sizing and editor slots are adapted from Toonflow
 * `nodeSkeleton.vue`, `imageGenerationNode/src/index.vue`, `textNode/src/index.vue`
 * and `imageNode/src/index.vue` at d86cce6b689916ab73f75bc4c1f42a2a7424516c (MIT).
 */
import * as React from "react";
import { createPortal } from "react-dom";
import { Handle, NodeToolbar as FlowNodeToolbar, Position, useViewport, type Node, type NodeProps } from "@xyflow/react";
import { ArrowUp, CircleDashed, CircleDot, Download, FileText, ImagePlus, Lock, Maximize2, Minus, Music2, Pencil, Plus, Sparkles, Square, Unlock, Upload, Video, X } from "lucide-react";
import type { CreativeCanvasAsset, CreativeCanvasKind, CreativeCanvasNode, CreativeCanvasRun, CreativeCanvasTextRun } from "./creativeCanvasApi";
import { CreativeCanvasPrompt } from "./CreativeCanvasPrompt";
import { CreativeCanvasTextGeneration, type CanvasTextModel } from "./CreativeCanvasTextGeneration";
import { CreativeCanvasGenerationSettings, generationCapabilityStatus } from "./CreativeCanvasGenerationSettings";
import { CreativeCanvasMediaPlayer, type CreativeCanvasMediaPlayerHandle, type VideoFrameKind } from "./CreativeCanvasMediaPlayer";
import type { ResizeCorner } from "./creativeCanvasGeometry";
import type { ImageGenerationModelCatalogEntry, ImageGenerationProviderEntry } from "./imageGenerationApi";

export type CanvasReference = { id: string; text?: string; asset?: CreativeCanvasAsset; sourceLabel: string; dataType?: "text" | "image" | "video" | "audio" };
export type CanvasNodeData = CreativeCanvasNode & {
  asset?: CreativeCanvasAsset; latestResult?: CreativeCanvasAsset; references?: CanvasReference[]; run?: CreativeCanvasRun;
  modelLabel: string; modelOptions: string[]; modelUnavailable?: boolean; selected?: boolean; submitting?: boolean;
  imageModelCatalog?: ImageGenerationModelCatalogEntry[];
  imageProviders?: Array<Pick<ImageGenerationProviderEntry, "providerId" | "credentialState"> & Partial<Pick<ImageGenerationProviderEntry, "protocol" | "defaultModelId" | "models">>>;
  activeImageProviderId?: string | null; configurationVersion?: string | null;
  /** `target:<handle>` / `source:<handle>` derived from current canvas edges. */
  connectedHandles?: string[];
  onEdit: (id: string, patch: Partial<CreativeCanvasNode>) => void; onRun: (id: string) => void; onCancel: (runId: string) => void; onOpenSettings: () => void;
  onDelete: (id: string) => void; onDuplicate: (id: string) => void; onOpenInfo: (id: string) => void; onPreview: (asset: CreativeCanvasAsset) => void; onDownload: (asset: CreativeCanvasAsset) => void;
  onReplace: (id: string) => void; onBranch: (asset: CreativeCanvasAsset, sourceNodeId: string) => void; onCopyPrompt: (text: string) => void;
  onReorderReferences: (id: string, order: string[]) => void; onRemoveReference: (edgeId: string) => void;
  /** Injected only when the host has durable text-run actions available. */
  textModels?: CanvasTextModel[]; textRun?: CreativeCanvasTextRun; textRuns?: CreativeCanvasTextRun[];
  onRunText?: (id: string) => void; onCancelText?: (runId: string) => void; onSelectTextVersion?: (runId: string, versionId: string) => void; onInstallTextVersion?: (runId: string, versionId: string) => void;
  onGenerateImageFromText?: (id: string) => void;
  onCaptureFrame?: (file: File, kind: VideoFrameKind) => void | Promise<void>;
};
export type CanvasFlowNode = Node<CanvasNodeData, CreativeCanvasKind>;
function stopCanvas(event: React.SyntheticEvent) { event.stopPropagation(); }
function stopCanvasKey(event: React.KeyboardEvent) { event.stopPropagation(); }
function CanvasHandle({ data, type, id, position, style, label }: { data: CanvasNodeData; type: "source" | "target"; id: string; position: Position; style?: React.CSSProperties; label: string }) {
  const connected = data.connectedHandles?.includes(`${type}:${id}`) ?? false;
  const Icon = connected ? CircleDot : CircleDashed;
  return <Handle type={type} position={position} id={id} style={style} aria-label={label} className={`creative-node__handle${connected ? " is-connected" : ""}`}><Icon className="creative-node__handle-icon" size={18} aria-hidden="true" /></Handle>;
}

/** Title, card and floating slots adapted from Toonflow nodeSkeleton.vue (MIT). */
function NodeTitle({ data, icon }: { data: CanvasNodeData; icon: React.ReactNode }) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const cancelled = React.useRef(false);
  const begin = () => { cancelled.current = false; setDraft(data.label || "未命名节点"); setEditing(true); };
  const commit = () => { if (!cancelled.current && draft.trim() && draft.trim() !== data.label) data.onEdit(data.id, { label: draft.trim() }); setEditing(false); };
  return <header className="creative-node__title">{icon}{editing
    ? <input autoFocus className="nodrag nopan" aria-label="节点名称" value={draft} onFocus={event => event.target.select()} onChange={event => setDraft(event.target.value)} onBlur={commit} onPointerDown={stopCanvas} onKeyDown={event => { stopCanvasKey(event); if (event.nativeEvent.isComposing) return; if (event.key === "Enter") { event.preventDefault(); commit(); } if (event.key === "Escape") { cancelled.current = true; setEditing(false); } }} />
    : <button type="button" className="creative-node__name" aria-label={`重命名 ${data.label || "未命名节点"}`} title="双击或按 Enter 编辑名称" onDoubleClick={event => { stopCanvas(event); begin(); }} onKeyDown={event => { if (event.key === "Enter") { stopCanvasKey(event); begin(); } }}>{data.label || "未命名节点"}</button>}
    <button type="button" className="creative-node__remove nodrag" aria-label={`移除 ${data.label || "节点"}`} title="移除节点" onPointerDown={stopCanvas} onClick={() => data.onDelete(data.id)}><X size={14} /></button>
  </header>;
}

function ActionToolbar({ data, children }: { data: CanvasNodeData; children: React.ReactNode }) {
  const toolbar = React.useRef<HTMLDivElement>(null);
  const viewport = useViewport();
  const [shift, setShift] = React.useState({ x: 0, y: 0 });
  React.useLayoutEffect(() => {
    if (!data.selected) return;
    const update = () => {
      const node = window.document.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(data.id)}"]`);
      const rect = node?.getBoundingClientRect();
      if (!rect || !toolbar.current) return;
      const width = toolbar.current.offsetWidth; const height = toolbar.current.offsetHeight;
      const left = rect.left + rect.width / 2 - width / 2;
      const top = rect.top - height - 10;
      setShift({ x: Math.max(12 - left, Math.min(0, window.innerWidth - 12 - width - left)), y: Math.max((window.innerWidth <= 700 ? 120 : 76) - top, Math.min(0, window.innerHeight - 12 - height - top)) });
    };
    const observer = new ResizeObserver(update);
    if (toolbar.current) observer.observe(toolbar.current);
    update(); window.addEventListener("resize", update);
    return () => { observer.disconnect(); window.removeEventListener("resize", update); };
  }, [data.id, data.selected, data.position.x, data.position.y, viewport.x, viewport.y, viewport.zoom]);
  return <FlowNodeToolbar isVisible={data.selected} position={Position.Top} offset={10}><div ref={toolbar} style={{ transform: `translate(${shift.x}px, ${shift.y}px)` }} className="creative-node__toolbar nodrag nopan nowheel" onMouseDown={stopCanvas} onPointerDown={stopCanvas} onClick={stopCanvas} onKeyDown={stopCanvasKey}>{children}</div></FlowNodeToolbar>;
}
/** The selected-node toolbar follows Toonflow nodeSkeleton's mediaToolbar. */
function mediaToolbar(data: CanvasNodeData, asset?: CreativeCanvasAsset, naturalRatio?: number, options: { replaceKind?: CreativeCanvasAsset["kind"]; onFullscreen?: () => void } = {}) {
  const kind = asset?.kind ?? options.replaceKind; const image = kind === "image";
  const toggleResizeMode = () => {
    if (!data.freeResize) return data.onEdit(data.id, { freeResize: true });
    const width = data.width ?? 220; const previousHeight = data.height ?? width / (naturalRatio || 1);
    const height = width / (naturalRatio || width / previousHeight || 1);
    data.onEdit(data.id, { freeResize: false, width, height, position: { ...data.position, y: data.position.y + previousHeight / 2 - height / 2 } });
  };
  const actions: React.ReactNode[] = [];
  if (kind) actions.push(<button key="replace" type="button" onClick={() => data.onReplace(data.id)} aria-label={`替换${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}`} title={`替换${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}`}><Upload size={14} /></button>);
  if (asset) actions.push(<button key="download" type="button" onClick={() => data.onDownload(asset)} aria-label="下载素材" title="下载素材"><Download size={14} /></button>);
  if (image && asset) actions.push(<button key="ratio" type="button" onClick={toggleResizeMode} aria-label={data.freeResize ? "锁定图片比例" : "启用自由缩放"} title={data.freeResize ? "锁定图片比例" : "启用自由缩放"}>{data.freeResize ? <Lock size={14} /> : <Unlock size={14} />}</button>);
  if (asset && (image || options.onFullscreen)) actions.push(<button key="fullscreen" type="button" onClick={() => image ? data.onPreview(asset) : options.onFullscreen?.()} aria-label="全屏" title="全屏"><Maximize2 size={14} /></button>);
  return actions.length ? <ActionToolbar data={data}>{actions}</ActionToolbar> : null;
}

type ResizeFrame = { position: { x: number; y: number }; width: number; height: number };
const resizeCorners: Array<{ corner: ResizeCorner; label: string }> = [
  { corner: "top-left", label: "从左上角缩放图片" }, { corner: "top-right", label: "从右上角缩放图片" },
  { corner: "bottom-left", label: "从左下角缩放图片" }, { corner: "bottom-right", label: "从右下角缩放图片" },
];
function resizeImageFrame(start: ResizeFrame, corner: ResizeCorner, delta: { x: number; y: number }, freeResize: boolean, naturalRatio?: number): ResizeFrame {
  const fromLeft = corner.endsWith("left"); const fromTop = corner.startsWith("top");
  const rawWidth = start.width + (fromLeft ? -delta.x : delta.x); const rawHeight = start.height + (fromTop ? -delta.y : delta.y);
  let width: number; let height: number;
  if (freeResize) { width = Math.min(8192, Math.max(220, rawWidth)); height = Math.min(8192, Math.max(160, rawHeight)); }
  else {
    const ratio = naturalRatio && Number.isFinite(naturalRatio) && naturalRatio > 0 ? naturalRatio : start.width / Math.max(1, start.height);
    const candidateWidth = Math.abs(delta.x) >= Math.abs(delta.y) ? rawWidth : rawHeight * ratio;
    const maximumWidth = Math.min(8192, 8192 * ratio);
    const minimumWidth = Math.min(maximumWidth, Math.max(220, 160 * ratio));
    width = Math.min(maximumWidth, Math.max(minimumWidth, candidateWidth)); height = width / ratio;
  }
  const right = start.position.x + start.width; const bottom = start.position.y + start.height;
  return { width, height, position: { x: fromLeft ? right - width : start.position.x, y: fromTop ? bottom - height : start.position.y } };
}
function useImageResize(data: CanvasNodeData, naturalRatio?: number, enabled = true) {
  const viewport = useViewport(); const element = React.useRef<HTMLElement>(null); const active = React.useRef<{ pointerId: number; startClient: { x: number; y: number }; start: ResizeFrame; corner: ResizeCorner; frame: ResizeFrame }>(); const [preview, setPreview] = React.useState<ResizeFrame>();
  const latest = React.useRef({ data, viewport, naturalRatio }); latest.current = { data, viewport, naturalRatio };
  const finish = React.useCallback((commit: boolean) => {
    const current = active.current; if (!current) return;
    const { data } = latest.current;
    window.removeEventListener("pointermove", move, true); window.removeEventListener("pointerup", up, true); window.removeEventListener("pointercancel", cancel, true);
    active.current = undefined; setPreview(undefined);
    if (commit && (current.frame.width !== current.start.width || current.frame.height !== current.start.height || current.frame.position.x !== current.start.position.x || current.frame.position.y !== current.start.position.y)) data.onEdit(data.id, current.frame);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const move = React.useCallback((event: PointerEvent) => {
    const current = active.current; if (!current || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return;
    const { data, viewport, naturalRatio } = latest.current;
    const zoom = Math.max(.01, viewport.zoom || 1); const frame = resizeImageFrame(current.start, current.corner, { x: (event.clientX - current.startClient.x) / zoom, y: (event.clientY - current.startClient.y) / zoom }, Boolean(data.freeResize), naturalRatio);
    current.frame = frame; setPreview(frame);
  }, []);
  const up = React.useCallback(() => { if (active.current) finish(true); }, [finish]);
  const cancel = React.useCallback(() => { if (active.current) finish(false); }, [finish]);
  React.useEffect(() => () => { const current = active.current; if (current) { window.removeEventListener("pointermove", move, true); window.removeEventListener("pointerup", up, true); window.removeEventListener("pointercancel", cancel, true); active.current = undefined; } }, [cancel, move, up]);
  const start = React.useCallback((event: React.PointerEvent<HTMLButtonElement>, corner: ResizeCorner) => {
    const { data, viewport } = latest.current;
    if ((event.button !== 0 && event.button !== undefined) || !data.selected || !enabled || active.current) return; event.preventDefault(); event.stopPropagation();
    const rect = element.current?.getBoundingClientRect(); const zoom = Math.max(.01, viewport.zoom || 1); const width = data.width ?? (rect?.width ? rect.width / zoom : 220); const height = data.height ?? (rect?.height ? rect.height / zoom : 160);
    const frame = { position: { ...data.position }, width, height }; active.current = { pointerId: event.pointerId, startClient: { x: event.clientX, y: event.clientY }, start: frame, corner, frame };
    window.addEventListener("pointermove", move, true); window.addEventListener("pointerup", up, true); window.addEventListener("pointercancel", cancel, true);
  }, [cancel, move, up]);
  const style = preview ? { width: preview.width, height: preview.height, transform: `translate(${preview.position.x - data.position.x}px,${preview.position.y - data.position.y}px)` } : data.width && data.height ? { width: data.width, height: data.height } : undefined;
  return { element, style: enabled ? style : undefined, handles: enabled && data.selected ? <div className="creative-node__resize-handles nodrag nopan nowheel">{resizeCorners.map(({ corner, label }) => <button key={corner} type="button" className={`creative-node__resize-handle is-${corner}`} aria-label={label} onPointerDown={event => start(event, corner)} />)}</div> : null };
}

export function CanvasTextNode({ data }: NodeProps<CanvasFlowNode>) {
  const [editing, setEditing] = React.useState(Boolean(data.selected && !data.text));
  const dialogRef = React.useRef<HTMLDialogElement>(null);
  const [downloadUrl, setDownloadUrl] = React.useState("");
  React.useEffect(() => {
    if (!data.selected || !data.text?.trim()) { setDownloadUrl(""); return; }
    const url = URL.createObjectURL(new Blob([data.text], { type: "text/plain;charset=utf-8" }));
    setDownloadUrl(url); return () => URL.revokeObjectURL(url);
  }, [data.selected, data.text]);
  const [fullscreen, setFullscreen] = React.useState(false);
  React.useEffect(() => {
    if (!editing) return;
    dialogRef.current?.showModal();
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setEditing(false); setFullscreen(false); } };
    window.document.addEventListener("keydown", close, true);
    return () => window.document.removeEventListener("keydown", close, true);
  }, [editing]);
  const host = window.document.querySelector(".creative-canvas");
  const fontSize = data.fontSize ?? 16;
  const editFontSize = (delta: number) => data.onEdit(data.id, { fontSize: Math.max(10, Math.min(32, fontSize + delta)) });
  const dialog = editing ? <dialog ref={dialogRef} aria-label="编辑文本" className={`creative-node__text-dialog${fullscreen ? " is-fullscreen" : ""}`} onCancel={event => { event.preventDefault(); setEditing(false); setFullscreen(false); }} onMouseDown={stopCanvas} onPointerDown={stopCanvas} onKeyDown={stopCanvasKey}><div className="creative-node__text-dialog-head"><strong>编辑文本</strong><div><button type="button" aria-label="全屏编辑文本" onClick={() => setFullscreen(value => !value)}><Maximize2 size={16} /></button><button type="button" aria-label="关闭文本编辑" onClick={() => { setEditing(false); setFullscreen(false); }}><X size={16} /></button></div></div><textarea autoFocus className="nodrag nowheel" style={{ fontSize }} value={data.text ?? ""} onMouseDown={stopCanvas} onPointerDown={stopCanvas} onKeyDown={stopCanvasKey} onChange={event => data.onEdit(data.id, { text: event.target.value })} aria-label="编辑文本内容" /></dialog> : null;
  const textGeneration = data.onRunText && data.onCancelText && data.onSelectTextVersion && data.onInstallTextVersion ? <FlowNodeToolbar isVisible={data.selected} position={Position.Bottom} offset={12}><div className="nodrag nopan nowheel" onMouseDown={stopCanvas} onPointerDown={stopCanvas} onClick={stopCanvas} onKeyDown={stopCanvasKey}><CreativeCanvasTextGeneration label={data.label ?? "文本"} referenceList={<ReferenceList data={data} />} value={{
    prompt: data.prompt ?? "", selectedModel: data.providerId && data.modelId ? { providerId: data.providerId, modelId: data.modelId, configurationVersion: data.configurationVersion } : undefined, models: data.textModels ?? [],
    references: (data.references ?? []).map((reference, index) => ({ id: reference.id, name: `参考 ${index + 1}`, value: `{{ref ${index + 1}}}`, avatar: reference.asset?.url })),
    count: data.params?.count ?? 1,
    versions: (data.textRuns ?? []).flatMap(run => run.versions.map(version => ({ ...version, runId: run.id, cancellationRequested: version.cancellationRequested || run.cancellationRequested }))), currentVersionId: data.currentTextVersionId, runStatus: data.textRun?.status,
    onPromptChange: prompt => data.onEdit(data.id, { prompt }), onModelChange: selection => data.onEdit(data.id, { providerId: selection.providerId, modelId: selection.modelId, configurationVersion: selection.configurationVersion }),
    onCountChange: count => data.onEdit(data.id, { params: { count } }),
    onRun: () => data.onRunText?.(data.id), onCancel: () => data.textRun && data.onCancelText?.(data.textRun.id), onSelectVersion: (runId, versionId) => data.onSelectTextVersion?.(runId, versionId), onInstallVersion: (runId, versionId) => data.onInstallTextVersion?.(runId, versionId),
    onPreviewReference: edgeId => { const asset = data.references?.find(reference => reference.id === edgeId)?.asset; if (asset) data.onPreview(asset); },
  }} /></div></FlowNodeToolbar> : null;
  return <><article className="creative-node creative-node--text" aria-label={`文本节点 ${data.label ?? "文本"}`}>
    <CanvasHandle data={data} type="target" position={Position.Left} id="in" label="文本或图片引用输入" /><CanvasHandle data={data} type="source" position={Position.Right} id="text" label="文本输出" /><ActionToolbar data={data}>{downloadUrl && <a href={downloadUrl} download={`${data.label || "文本"}.txt`} aria-label="下载文本" title="下载文本"><Download size={14} /></a>}{data.onGenerateImageFromText && data.text?.trim() && <button type="button" aria-label="从文本创建图片生成" title="从文本创建图片生成" onClick={() => data.onGenerateImageFromText?.(data.id)}><ImagePlus size={14} /></button>}<button type="button" aria-label="减小字号" title="减小字号" disabled={fontSize <= 10} onClick={() => editFontSize(-2)}><Minus size={14} /></button><button type="button" aria-label="增大字号" title="增大字号" disabled={fontSize >= 32} onClick={() => editFontSize(2)}><Plus size={14} /></button><button type="button" aria-label="全屏编辑文本" title="全屏编辑文本" onClick={() => { setFullscreen(true); setEditing(true); }}><Maximize2 size={14} /></button></ActionToolbar>
    <NodeTitle data={data} icon={<FileText size={16} />} /><div className={`creative-node__card creative-node__text-content${data.text?.trim() ? "" : " is-empty"}`}><div className="creative-node__text-preview" aria-label="文本内容" style={{ fontSize }}>{data.text || "未填写文本"}</div><button type="button" className="creative-node__edit-text nodrag" onPointerDown={stopCanvas} onClick={() => setEditing(true)}><Pencil size={14} />编辑</button></div>{dialog && createPortal(dialog, host || window.document.body)}
  </article>{textGeneration}</>;
}

export function CanvasMediaNode({ data }: NodeProps<CanvasFlowNode>) {
  const asset = data.asset; const [naturalRatio, setNaturalRatio] = React.useState<number>(); const [naturalWidth, setNaturalWidth] = React.useState<number>(); const player = React.useRef<CreativeCanvasMediaPlayerHandle>(null); const audio = React.useRef<HTMLDivElement>(null);
  const imageLoaded = (event: React.SyntheticEvent<HTMLImageElement>) => { const image = event.currentTarget; if (image.naturalWidth && image.naturalHeight) { setNaturalRatio(image.naturalWidth / image.naturalHeight); if (!data.width) setNaturalWidth(240 * image.naturalWidth / image.naturalHeight + 18); } };
  const imageResize = useImageResize(data, naturalRatio, asset?.kind === "image");
  const media = asset?.kind === "image" ? <img src={asset.url} alt={asset.name} draggable={false} onLoad={imageLoaded} /> : asset?.kind === "video" ? <CreativeCanvasMediaPlayer ref={player} src={asset.url} label={asset.name} onCaptureFrame={data.onCaptureFrame} /> : asset?.kind === "audio" ? <div ref={audio} className="creative-media-audio"><audio src={asset.url} controls preload="metadata" draggable={false} aria-label={asset.name} onPointerDown={stopCanvas} onMouseDown={stopCanvas} onDoubleClick={stopCanvas} onError={() => undefined} /></div> : null;
  const style = imageResize?.style ?? (naturalWidth ? { width: naturalWidth } : undefined);
  return <article ref={imageResize?.element} className={`creative-node creative-node--media${imageResize?.style ? " is-resizing" : ""}${data.width && data.height ? " creative-node--resized" : ""}`} aria-label={`素材节点 ${data.label ?? "项目素材"}`} style={style}>
    <CanvasHandle data={data} type="source" position={Position.Right} id="image" label="图片输出" />{mediaToolbar(data, asset, naturalRatio, { onFullscreen: asset?.kind === "video" ? () => void player.current?.enterFullscreen() : asset?.kind === "audio" ? () => { void audio.current?.requestFullscreen(); } : undefined })}
    <NodeTitle data={data} icon={<ImagePlus size={16} />} />
    <div className="creative-node__card"><div className="creative-node__preview" role={media ? undefined : "img"} aria-label={media ? undefined : "尚未选择图片"} onDoubleClick={() => asset?.kind === "image" && data.onPreview(asset)}>{media ?? <ImagePlus size={48} strokeWidth={1.5} aria-hidden="true" />}</div></div>{imageResize?.handles}
    {asset?.available === false && <p role="alert">素材已不可用</p>}
  </article>;
}

export function runErrorMessage(error?: string | null, concealCancelledError = false) {
  if (!error) return "";
  if (error.includes("input_budget_exceeded")) return "参考图片超过本次请求容量，请减少图片或压缩后重试。";
  if (error.includes("creative_canvas_queue_draining")) return "生成队列正在清理取消任务，请稍后重试。";
  return concealCancelledError ? "停止处理未返回可用结果。" : error;
}
export function runStatusMessage(run?: CreativeCanvasRun) {
  if (!run) return "";
  if (run.status === "cancel_requested") return "已请求停止，等待服务返回。";
  if (run.status === "cancelled") return run.localDisposition === "queued_cancelled" ? "已取消（未发送）。" : run.outputAssetIds.length ? "已停止自动采用，返回结果已保留在历史中。" : "已停止采用；服务未返回可用结果。";
  if (run.status === "unknown") return "状态未知，不会重新提交；请在生成历史中刷新。";
  return runErrorMessage(run.error);
}
function RunStatus({ run }: { run?: CreativeCanvasRun }) {
  if (!run) return null;
  const status = runStatusMessage(run); const error = runErrorMessage(run.error, run.status === "cancelled");
  return status ? <><p role={run.status === "failed" ? "alert" : "status"}>{status}</p>{run.status === "cancelled" && run.billingUnknown && <p>供应商计费状态未知。</p>}{run.status === "cancelled" && run.localDisposition !== "queued_cancelled" && error && <p role="alert">{error}</p>}</> : null;
}
function ReferenceList({ data }: { data: CanvasNodeData }) {
  const [dragged, setDragged] = React.useState<string>(); const references = data.references ?? [];
  const move = (from: number, to: number) => { if (from === to || to < 0 || to >= references.length) return; const next = [...references]; const [item] = next.splice(from, 1); if (!item) return; next.splice(to, 0, item); data.onReorderReferences(data.id, next.map(value => value.id)); };
  return references.length ? <div className="creative-node__references" aria-label="输入引用">{references.map((reference, index) => <div key={reference.id} className="creative-node__reference" draggable onDragStart={() => setDragged(reference.id)} onDragOver={event => event.preventDefault()} onDrop={() => { move(references.findIndex(item => item.id === dragged), index); setDragged(undefined); }} tabIndex={0} aria-keyshortcuts="Alt+ArrowLeft Alt+ArrowRight" onKeyDown={event => { if (!event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return; event.preventDefault(); stopCanvasKey(event); move(index, index + (event.key === "ArrowLeft" ? -1 : 1)); }}>
    {(reference.dataType === "image" || (!reference.dataType && reference.asset?.kind === "image")) && reference.asset && reference.asset.available !== false ? <button type="button" aria-label={`预览参考 ${index + 1}`} onClick={() => data.onPreview(reference.asset!)}><img src={reference.asset.url} alt="" /></button> : <span className="creative-node__reference-icon" title={`引用 ${index + 1}：${reference.dataType === "image" ? "图片素材不可用" : reference.text || reference.sourceLabel}`} aria-label={`引用 ${index + 1}：${reference.dataType === "image" ? "图片素材不可用" : reference.text || reference.sourceLabel}`}>{reference.dataType === "image" ? <ImagePlus size={21} aria-hidden="true" /> : reference.dataType === "video" || reference.asset?.kind === "video" ? <Video size={21} aria-hidden="true" /> : reference.dataType === "audio" || reference.asset?.kind === "audio" ? <Music2 size={21} aria-hidden="true" /> : <FileText size={21} aria-hidden="true" />}</span>}<span className="creative-node__reference-index">{index + 1}</span><button type="button" className="creative-node__reference-remove" aria-label={`移除参考 ${index + 1}`} onClick={() => data.onRemoveReference(reference.id)}>×</button><span className="creative-node__reference-label">参考 {index + 1}</span>
  </div>)}</div> : null;
}


function GenerateEditor({ data, run, prompt, busy, layoutKey }: { data: CanvasNodeData; run?: CreativeCanvasRun; prompt: string; busy: boolean; layoutKey: string }) {
  const catalog = data.imageModelCatalog?.length ? data.imageModelCatalog : data.modelOptions.map(modelId => ({ providerId: "legacy", modelId, mediaType: "image" as const, label: modelId, configured: true, capabilitySource: "unknown" as const, capabilityVersion: null, capabilities: {} }));
  const providers = data.imageProviders ?? [{ providerId: "legacy", credentialState: "configured" as const }];
  const isImageReference = (reference: CanvasReference) => reference.dataType === "image" || (!reference.dataType && reference.asset?.kind === "image");
  const imageReferenceCount = (data.references ?? []).filter(isImageReference).length;
  const missingImageIndex = (data.references ?? []).findIndex(reference => isImageReference(reference) && (!reference.asset || reference.asset.available === false));
  const missingImageReference = missingImageIndex >= 0 ? { index: missingImageIndex + 1, sourceLabel: data.references?.[missingImageIndex]?.sourceLabel ?? "引用" } : undefined;
  const capability = generationCapabilityStatus({ params: data.params, catalog, providers, configurationVersion: data.configurationVersion, activeProviderId: data.activeImageProviderId, referenceCount: imageReferenceCount, missingImageReference });
  const selectedModelValue = data.params?.providerId && data.params.modelId ? JSON.stringify([data.params.providerId, data.params.modelId]) : data.params?.model ? `legacy:${data.params.model}` : "";
  const knownModelValues = new Set(catalog.map(item => JSON.stringify([item.providerId, item.modelId])));
  const modelGroups = new Map<string, typeof catalog>();
  for (const option of catalog) modelGroups.set(option.providerId, [...(modelGroups.get(option.providerId) ?? []), option]);
  const chooseModel = (value: string) => {
    if (!value) { data.onEdit(data.id, { params: { model: undefined, providerId: undefined, modelId: undefined, configurationVersion: undefined } }); return; }
    try {
      const parsed = JSON.parse(value) as unknown;
      if (!Array.isArray(parsed) || parsed.length !== 2 || typeof parsed[0] !== "string" || typeof parsed[1] !== "string") return;
      data.onEdit(data.id, { params: { model: undefined, providerId: parsed[0], modelId: parsed[1], configurationVersion: data.configurationVersion ?? undefined } });
    } catch { /* Ignore a disabled legacy option that cannot be selected. */ }
  };
  const [placement, setPlacement] = React.useState({ position: Position.Bottom, maxHeight: 320, shiftX: 0 });
  const viewport = useViewport();
  React.useLayoutEffect(() => {
    if (!data.selected) return;
    const updatePosition = () => {
      const node = window.document.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(data.id)}"]`);
      const rect = node?.getBoundingClientRect();
      if (!rect) return;
      const margin = 16;
      const leftInset = window.innerWidth > 700 ? 84 : margin;
      const topSpace = Math.max(0, rect.top - (window.innerWidth <= 700 ? 120 : 76) - margin);
      const bottomSpace = Math.max(0, window.innerHeight - rect.bottom - 76);
      const position = bottomSpace >= 250 || bottomSpace >= topSpace ? Position.Bottom : Position.Top;
      const maxHeight = Math.min(410, Math.max(1, position === Position.Top ? topSpace - 58 : bottomSpace - 12));
      const editorWidth = Math.min(660, window.innerWidth - leftInset - margin);
      const naturalLeft = rect.left + rect.width / 2 - editorWidth / 2;
      const shiftX = Math.max(leftInset - naturalLeft, Math.min(0, window.innerWidth - margin - editorWidth - naturalLeft));
      setPlacement({ position, maxHeight, shiftX });
    };
    updatePosition(); window.addEventListener("resize", updatePosition); return () => window.removeEventListener("resize", updatePosition);
  }, [data.id, data.selected, layoutKey, viewport.x, viewport.y, viewport.zoom]);
  return <FlowNodeToolbar isVisible={data.selected} position={placement.position} offset={placement.position === Position.Top ? 58 : 12} style={{ "--creative-editor-max-height": `${placement.maxHeight}px`, "--creative-editor-shift-x": `${placement.shiftX}px` } as React.CSSProperties}>
    <section className="creative-node__editor nodrag nopan nowheel" aria-label={`${data.label ?? "图片生成"}编辑`} onMouseDown={stopCanvas} onPointerDown={stopCanvas} onClick={stopCanvas} onKeyDown={stopCanvasKey}>
      <ReferenceList data={data} />
      <CreativeCanvasPrompt value={prompt} label={`${data.label ?? "图片生成"}画面提示词`} references={(data.references ?? []).map((reference, index) => ({ id: reference.id, name: `参考 ${index + 1}`, value: `{{ref ${index + 1}}}`, avatar: reference.asset?.url }))} onChange={text => data.onEdit(data.id, { text })} onPreview={id => { const asset = data.references?.find(reference => reference.id === id)?.asset; if (asset) data.onPreview(asset); }} />
      <div className="creative-node__composer-footer">
        <div className="creative-node__model"><Sparkles size={16} /><label>模型<select value={selectedModelValue} onChange={event => chooseModel(event.target.value)} aria-label="图片模型"><option value="">{catalog.length ? "选择模型" : data.modelLabel}</option>{selectedModelValue && !knownModelValues.has(selectedModelValue) && <option value={selectedModelValue} disabled>{data.params?.model ? `历史模型：${data.params.model}` : "当前模型不可用，请重新选择"}</option>}{[...modelGroups].map(([providerId, options]) => <optgroup key={providerId} label={providerId === "legacy" ? "旧模型" : providerId}>{options.map(option => <option key={`${option.providerId}:${option.modelId}`} value={JSON.stringify([option.providerId, option.modelId])}>{option.providerId === "legacy" ? option.label || option.modelId : `${option.label || option.modelId}${option.capabilitySource === "unknown" ? "（能力未知）" : ""}`}</option>)}</optgroup>)}</select></label></div>
        <CreativeCanvasGenerationSettings params={data.params} catalog={catalog} providers={providers} configurationVersion={data.configurationVersion} activeProviderId={data.activeImageProviderId} referenceCount={imageReferenceCount} missingImageReference={missingImageReference} onEdit={params => data.onEdit(data.id, { params })} />
        <div className="creative-node__footer">{run?.status === "unknown" ? <button type="button" disabled title="状态未知，请先在生成历史中刷新">状态未知，先刷新</button> : busy ? <button type="button" disabled={run?.status === "cancel_requested"} onClick={() => run && data.onCancel(run.id)}><Square size={14} />{run?.status === "cancel_requested" ? "停止请求中" : "停止"}</button> : <button type="button" disabled={!capability.runnable} title={capability.runnable ? "生成" : capability.reason} aria-label="生成" onClick={() => capability.runnable && data.onRun(data.id)}><ArrowUp size={16} /><span className="creative-canvas__sr-only">生成</span></button>}</div>
      </div><RunStatus run={run} />
    </section>
  </FlowNodeToolbar>;
}

export function CanvasGenerateNode({ data, positionAbsoluteX = 0, positionAbsoluteY = 0 }: NodeProps<CanvasFlowNode>) {
  const run = data.run; const busy = data.submitting || Boolean(run && ["submitting", "queued", "running", "cancel_requested"].includes(run.status)); const prompt = data.text ?? ""; const [naturalRatio, setNaturalRatio] = React.useState<number>(); const imageResize = useImageResize(data, naturalRatio, data.latestResult?.kind === "image");
  const imageLoaded = (event: React.SyntheticEvent<HTMLImageElement>) => { const image = event.currentTarget; if (image.naturalWidth && image.naturalHeight) setNaturalRatio(image.naturalWidth / image.naturalHeight); };
  return <article ref={imageResize?.element} className={`creative-node creative-node--generate${imageResize?.style ? " is-resizing" : ""}${data.width && data.height ? " creative-node--resized" : ""}`} aria-label={`生成节点 ${data.label ?? "图片生成"}`} style={imageResize?.style}>
    <CanvasHandle data={data} type="target" position={Position.Left} id="prompt" style={{ top: "25%" }} label="提示词输入" /><CanvasHandle data={data} type="target" position={Position.Left} id="image" style={{ top: "65%" }} label="图片输入" /><CanvasHandle data={data} type="source" position={Position.Right} id="image" label="图片输出" />{mediaToolbar(data, data.latestResult, naturalRatio, { replaceKind: "image" })}
    <NodeTitle data={data} icon={<ImagePlus size={16} />} />
    <div className="creative-node__card"><div className="creative-node__preview" role={data.latestResult ? undefined : "img"} aria-label={data.latestResult ? undefined : (busy ? "正在生成图片" : "暂无生成图片")} onDoubleClick={() => data.latestResult && data.onPreview(data.latestResult)}>{data.latestResult ? <img src={data.latestResult.url} alt={data.latestResult.name} draggable={false} onLoad={imageLoaded} /> : <div className="creative-node__empty"><ImagePlus size={48} strokeWidth={1.25} aria-hidden="true" /></div>}</div></div>{imageResize?.handles}
    <GenerateEditor data={data} run={run} prompt={prompt} busy={busy} layoutKey={`${positionAbsoluteX}:${positionAbsoluteY}`} />
  </article>;
}
export const creativeCanvasNodeTypes = { text: CanvasTextNode, media: CanvasMediaNode, generate: CanvasGenerateNode };
