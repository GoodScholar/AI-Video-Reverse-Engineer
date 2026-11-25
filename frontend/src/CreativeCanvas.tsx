import { useCallback, useEffect, useMemo, useLayoutEffect, useRef, useState } from "react";
import { Background, MiniMap, ReactFlow, type Connection, type Edge, type EdgeChange, type Node, type NodeChange, type Viewport } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ArrowLeft, BoxSelect, ChevronDown, CircleHelp, Clipboard, Copy, Download, EyeOff, Film, Focus, History, ImagePlus, Info, Magnet, Map as MapIcon, Music, Maximize2, Network, PanelsTopLeft, Pencil, Plus, Redo2, RotateCcw, Save, Sparkles, Trash2, Undo2, Upload, X, ZoomIn, ZoomOut } from "lucide-react";
import { uploadPreproductionAsset } from "./preproductionApi";
import { graphWouldCycle, isTypeCompatible, orderReferenceEdges } from "./creativeCanvasConnection";
import { cancelCreativeCanvasOutput, refreshCreativeCanvasBatch, runCreativeCanvasBatch, cancelCreativeCanvasBatch, selectCreativeCanvasOutput, deleteCreativeCanvasOutput, retryCreativeCanvasOutput, type CreativeCanvasBatch, type CreativeCanvasOutputRef, cancelCreativeCanvasRun, cancelCreativeCanvasTextRun, createCreativeCanvas, deleteCreativeCanvas, getCreativeCanvas, installCreativeCanvasTextVersion, listCreativeCanvases, refreshCreativeCanvasRun, renameCreativeCanvas, restoreCreativeCanvas, runCreativeCanvasNode, runCreativeCanvasTextNode, saveCreativeCanvas, selectCreativeCanvasTextVersion, type CreativeCanvasAsset, type CreativeCanvasDocument, type CreativeCanvasEdge, type CreativeCanvasKind, type CreativeCanvasMeta, type CreativeCanvasNode, type CreativeCanvasRun, type CreativeCanvasTextRun, type CreativeCanvasWorkspace } from "./creativeCanvasApi";
import { creativeCanvasNodeTypes, type CanvasFlowNode } from "./CreativeCanvasNodes";
import { getImageGenerationConfiguration, type ImageGenerationConfiguration } from "./imageGenerationApi";
import { copyCanvasNodes, createCanvasClipboardSnapshot, duplicateCanvasSubgraph, isCanvasClipboardCommand, pasteCanvasClipboardSnapshot, readCanvasClipboard, saveCanvasClipboard } from "./creativeCanvasClipboard";
import { absoluteCanvasGroupPosition, absoluteCanvasNodePosition, arrangeCanvasDocument, createCanvasGroup, finishCanvasGroupDrag, mergeCanvasGroups, ungroupCanvas } from "./creativeCanvasGeometry";
import "./creativeCanvas.css";
import "./creativeCanvasClipboard.css";
import "./creativeCanvasControls.css";
import { CreativeCanvasNodeSearch } from "./CreativeCanvasNodeSearch";
import { CreativeCanvasImageEditor } from "./CreativeCanvasImageEditor";
import { CreativeCanvasHistory, canvasRunOutputItems } from "./CreativeCanvasHistory";
import { canvasNodeOutput } from "./creativeCanvasOutputs";
import { CreativeCanvasGroupNode } from "./CreativeCanvasGroupNode";
import type { ImageGeometryOperation } from "./creativeCanvasImageGeometry";
import type { TextGenerationModel } from "./textGenerationApi";

const EMPTY_DOCUMENT: CreativeCanvasDocument = { revision: 0, nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 }, settings: {} };
const START: Record<CreativeCanvasKind, { x: number; y: number }> = { text: { x: 140, y: 140 }, media: { x: 140, y: 380 }, generate: { x: 500, y: 190 } };
type CreativeCanvasImageAsset = CreativeCanvasAsset & { kind: "image" };
type ImageEditSession = { id: string; projectId: string; canvasId: string; source: CreativeCanvasImageAsset; position: { x: number; y: number }; mode: "crop" | "split"; installed: Set<string> };
type OutputSelection = { assetId?: string; ref?: CreativeCanvasOutputRef };
type SaveResult = { saved: boolean; revision: number; changedDuringSave: boolean };
type NodeMeasurements = Record<string, { width?: number; height?: number }>;
type CanvasContextMenu = { x: number; y: number; position: { x: number; y: number }; kind: "node" | "selection" | "pane"; ids: string[] };
type CanvasDraft = { document: CreativeCanvasDocument; assets: CreativeCanvasAsset[]; runs: CreativeCanvasRun[]; textRuns?: CreativeCanvasTextRun[]; textModels?: TextGenerationModel[]; batches?: CreativeCanvasBatch[]; dirty?: boolean; undo?: CreativeCanvasDocument[]; redo?: CreativeCanvasDocument[] };
type CanvasFlowGroup = Node<{ label: string; onRename: (id: string, label: string) => void; onResizeStart: (id: string) => void; onResize: (id: string, width: number, height: number, position?: { x: number; y: number }) => void }, "canvasGroup">;

function isImage(asset: CreativeCanvasAsset | undefined): asset is CreativeCanvasImageAsset { return asset?.kind === "image" && asset.available !== false; }
function markDownstreamStale(document: CreativeCanvasDocument, sourceIds: Set<string>) {
  const nodes = new Map(document.nodes.map(node => [node.id, node]));
  const pending = [...sourceIds];
  const visited = new Set<string>();
  while (pending.length) {
    const source = pending.pop()!;
    if (visited.has(source)) continue;
    visited.add(source);
    for (const edge of document.edges) {
      if (edge.source !== source) continue;
      const target = nodes.get(edge.target);
      if (!target) continue;
      if (target.kind === "generate" && !target.stale) nodes.set(target.id, { ...target, stale: true });
      pending.push(target.id);
    }
  }
  return { ...document, nodes: document.nodes.map(node => nodes.get(node.id) ?? node) };
}
function nodePosition(document: CreativeCanvasDocument, kind: CreativeCanvasKind) { const count = document.nodes.filter(node => node.kind === kind).length; const base = START[kind]; const zoom = document.viewport.zoom || 1; return { x: (base.x - document.viewport.x) / zoom + count * 36, y: (base.y - document.viewport.y) / zoom + count * 28 }; }
function newNode(document: CreativeCanvasDocument, kind: CreativeCanvasKind, asset?: CreativeCanvasAsset): CreativeCanvasNode { return { id: `${kind}-${crypto.randomUUID()}`, kind, position: nodePosition(document, kind), label: asset?.name ?? (kind === "text" ? "文本" : kind === "media" ? "项目素材" : "图片生成"), ...(kind === "text" ? { text: "" } : {}), ...(kind === "media" ? { assetId: asset?.id } : {}), ...(kind === "generate" ? { params: { count: 1, width: 1024, height: 1024, size: "1024x1024", ratio: "1:1" } } : {}) }; }
function merge<T extends { id: string }>(current: T[], next: T[]) { const items = new Map(current.map(item => [item.id, item])); next.forEach(item => items.set(item.id, item)); return Array.from(items.values()); }
function groupSubtreeIds(document: CreativeCanvasDocument, ids: Iterable<string>) {
  const removed = new Set(ids);
  let changed = true;
  while (changed) {
    changed = false;
    for (const group of document.groups ?? []) if (group.parentId && removed.has(group.parentId) && !removed.has(group.id)) { removed.add(group.id); changed = true; }
    for (const node of document.nodes) if (node.parentId && removed.has(node.parentId) && !removed.has(node.id)) { removed.add(node.id); changed = true; }
  }
  return removed;
}
export function pruneReferenceOrders(nodes: CreativeCanvasNode[], edges: CreativeCanvasEdge[], previousEdges: CreativeCanvasEdge[] = edges) {
  return nodes.map(node => {
    if (node.kind !== "generate" && node.kind !== "text") return node;
    // Persisted orders can predate a newly connected edge.  Derive both lists
    // from their complete edge sets so {{ref n}} keeps tracking edge identity.
    const previous = orderReferenceEdges(previousEdges.filter(edge => edge.target === node.id), node.referenceOrder).map(edge => edge.id);
    const next = orderReferenceEdges(edges.filter(edge => edge.target === node.id), previous).map(edge => edge.id);
    return node.kind === "generate"
      ? { ...node, referenceOrder: node.referenceOrder || previous.length || next.length ? next : undefined, text: remapReferenceTokens(node.text, previous, next) }
      : { ...node, referenceOrder: node.referenceOrder || previous.length || next.length ? next : undefined, prompt: remapReferenceTokens(node.prompt, previous, next) };
  });
}
/** Textarea adaptation of Toonflow promptInput's stable Custom reference IDs. */
export function remapReferenceTokens(text: string | undefined, previous: string[], next: string[]) {
  return (text ?? "").replace(/\{\{ref\s+(\d+)\}\}/g, (_token, value: string) => { const edgeId = previous[Number(value) - 1]; const index = edgeId ? next.indexOf(edgeId) : -1; return index < 0 ? "" : `{{ref ${index + 1}}}`; });
}

export function isCreativeCanvasConnectionValid(connection: Connection, document: CreativeCanvasDocument, assets: CreativeCanvasAsset[]) {
  if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle || connection.source === connection.target) return false;
  const targetType = connection.targetHandle === "prompt" ? "text" : connection.targetHandle === "image" ? "image" : "text,image";
  if (!isTypeCompatible(connection.sourceHandle as "text" | "image", targetType)) return false;
  const source = document.nodes.find(node => node.id === connection.source);
  const target = document.nodes.find(node => node.id === connection.target);
  const sourceMatchesHandle = (source?.kind === "text" && connection.sourceHandle === "text") || ((source?.kind === "media" || source?.kind === "generate") && connection.sourceHandle === "image");
  const sourceHasImage = source?.kind === "generate" || source?.kind !== "media" || isImage(assets.find(asset => asset.id === source.assetId));
  const validTarget = target?.kind === "generate"
    ? (connection.targetHandle === "prompt" && connection.sourceHandle === "text") || (connection.targetHandle === "image" && connection.sourceHandle === "image")
    : target?.kind === "text" && connection.targetHandle === "in";
  return Boolean(sourceMatchesHandle && sourceHasImage && validTarget
    && !document.edges.some(edge => edge.source === connection.source && edge.target === connection.target && edge.sourceHandle === connection.sourceHandle && edge.targetHandle === connection.targetHandle)
    && !graphWouldCycle(document.edges, connection.source, connection.target));
}

export function CreativeCanvas({ projectId, projectName = "当前项目", active, onBack, onOpenAssets, onOpenSettings = () => undefined, onDirtyChange }: { projectId: string; projectName?: string; active: boolean; onBack: () => void; onOpenAssets: () => void; onOpenSettings?: () => void; onDirtyChange: (dirty: boolean) => void }) {
  const [document, setDocument] = useState<CreativeCanvasDocument>(EMPTY_DOCUMENT);
  const [assets, setAssets] = useState<CreativeCanvasAsset[]>([]);
  const [runs, setRuns] = useState<CreativeCanvasRun[]>([]);
  const [textRuns, setTextRuns] = useState<CreativeCanvasTextRun[]>([]);
  const [textModels, setTextModels] = useState<TextGenerationModel[]>([]);
  const [batches, setBatches] = useState<CreativeCanvasBatch[]>([]);
  const [resultActionBusy, setResultActionBusy] = useState(false);
  const resultActionLock = useRef(false);
  const pendingBatchKey = useRef<{ fingerprint: string; key: string }>();
  const [selectedId, setSelectedId] = useState<string>();
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [selectedNodeIds, setSelectedNodeIds] = useState<Set<string>>(new Set());
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<Set<string>>(new Set());
  const [nodeMeasurements, setNodeMeasurements] = useState<NodeMeasurements>({});
  const [assetPickerOpen, setAssetPickerOpen] = useState(false);
  const [assetPickerPosition, setAssetPickerPosition] = useState<{ x: number; y: number }>();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [discardPending, setDiscardPending] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [runningNodeId, setRunningNodeId] = useState<string>();
  const [modelLabel, setModelLabel] = useState("未配置模型");
  const [configuration, setConfiguration] = useState<ImageGenerationConfiguration>();
  const [imageEdit, setImageEdit] = useState<ImageEditSession>();
  const imageEditRef = useRef<ImageEditSession>();
  const [previewAsset, setPreviewAsset] = useState<CreativeCanvasAsset>();
  const [replaceNodeId, setReplaceNodeId] = useState<string>();
  const [uploadPosition, setUploadPosition] = useState<{ x: number; y: number }>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadedReplacement, setUploadedReplacement] = useState<{ nodeId: string; assetId: string; session: number }>();
  const [pendingOutputReplacements, setPendingOutputReplacements] = useState<{ id: number; replacements: Array<{ nodeId: string; output: string; outputRef: CreativeCanvasOutputRef; selectedAtSubmit: OutputSelection }> }>();
  const [contextMenu, setContextMenu] = useState<CanvasContextMenu>();
  const [quickAddOpen, setQuickAddOpen] = useState(false);
  const [canvasMenuOpen, setCanvasMenuOpen] = useState(false);
  const [canvases, setCanvases] = useState<CreativeCanvasMeta[]>([{ id: "default", name: "默认画布", createdAt: null, updatedAt: null, deletedAt: null }]);
  const [activeCanvasId, setActiveCanvasId] = useState("default");
  const [newCanvasName, setNewCanvasName] = useState("");
  const [renamingCanvasId, setRenamingCanvasId] = useState<string>();
  const [renameCanvasName, setRenameCanvasName] = useState("");
  const [showDeletedCanvases, setShowDeletedCanvases] = useState(false);
  const [pendingCanvasId, setPendingCanvasId] = useState<string>();
  const [deleteCanvasId, setDeleteCanvasId] = useState<string>();
  const [canvasActionBusy, setCanvasActionBusy] = useState(false);
  const [zoomMenuOpen, setZoomMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [showEdges, setShowEdges] = useState(true);
  const [zoom, setZoom] = useState(1);
  const inputRef = useRef<HTMLInputElement>(null);
  const imageDialogRef = useRef<HTMLDialogElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const flowRef = useRef<{
    fitView: (options?: { padding?: number; maxZoom?: number; nodes?: { id: string }[]; duration?: number }) => unknown;
    getViewport: () => Viewport;
    zoomTo: (nextZoom: number) => unknown;
  } | null>(null);
  const activeRef = useRef(active);
  const clipboardRequest = useRef(0);
  const previewTrigger = useRef<HTMLElement | null>(null);
  const editVersion = useRef(0);
  const dirtyRef = useRef(false);
  const requestId = useRef(0);
  const assetRequestId = useRef(0);
  const alive = useRef(true);
  const projectRef = useRef(projectId);
  const canvasRef = useRef("default");
  const localDrafts = useRef(new Map<string, CanvasDraft>());
  const catalogRequest = useRef(0);
  const canvasSession = useRef(0);
  const dragFinishTimer = useRef<number>();
  const runOutputBases = useRef(new Map<string, OutputSelection>());
  const responseSequence = useRef(0);
  const appliedOutputResponse = useRef(0);
  const documentRef = useRef<CreativeCanvasDocument>(EMPTY_DOCUMENT);
  const runInflight = useRef(new Set<string>());
  const textRunInflight = useRef(new Set<string>());
  const runRequestKeys = useRef(new Map<string, string>());
  const previousDocument = useRef<CreativeCanvasDocument>(EMPTY_DOCUMENT);
  const undoStack = useRef<CreativeCanvasDocument[]>([]);
  const redoStack = useRef<CreativeCanvasDocument[]>([]);
  const applyingHistory = useRef(false);
  const historyKind = useRef<"edit" | "drag">("edit");
  const previousHistoryKind = useRef<"edit" | "drag">("edit");
  const groupResizeOrigins = useRef(new Map<string, { x: number; y: number }>());
  const [, setHistoryRevision] = useState(0);
  const previousActive = useRef(active);
  const valid = useCallback((project: string, request: number, canvasId: string) => alive.current && projectRef.current === project && canvasRef.current === canvasId && request === requestId.current, []);
  const canvasScope = (canvasId = canvasRef.current) => canvasId === "default" ? undefined : canvasId;
  const markDirty = useCallback((kind: "edit" | "drag" = "edit") => { historyKind.current = kind; editVersion.current += 1; dirtyRef.current = true; setDirty(true); }, []);
  const selected = document.nodes.find(node => node.id === selectedId);
  const activeCanvas = canvases.find(canvas => canvas.id === activeCanvasId) ?? { id: "default", name: "默认画布", createdAt: null, updatedAt: null, deletedAt: null };
  const generateEditorOpen = document.nodes.some(node => (node.kind === "generate" || node.kind === "text") && selectedNodeIds.has(node.id));
  const activeRun = (nodeId: string) => runs.find(run => run.nodeId === nodeId && ["submitting", "queued", "running", "cancel_requested", "unknown"].includes(run.status));
  const activeTextRun = (nodeId: string) => textRuns.find(run => run.nodeId === nodeId && ["submitting", "queued", "running", "cancel_requested", "unknown"].includes(run.status));

  const applyResponse = useCallback((workspace: CreativeCanvasWorkspace, version: number, documentSafe: boolean) => {
    setRuns(current => merge(current, workspace.runs));
    if (workspace.textRuns) setTextRuns(current => merge(current, workspace.textRuns!));
    if (workspace.textModels) setTextModels(workspace.textModels);
    if (workspace.batches) setBatches(current => merge(current, workspace.batches!));
    setAssets(current => merge(current, workspace.assets));
    if (documentSafe) setDocument(current => version === editVersion.current ? workspace.document : { ...current, revision: workspace.document.revision });
    else {
      const responseId = ++responseSequence.current;
      const outputReplacements = new Map<string, { output: string; outputRef: CreativeCanvasOutputRef; selectedAtSubmit: OutputSelection }>();
      for (const run of workspace.runs) {
        const selectedAtSubmit = runOutputBases.current.get(run.id);
        if (run.status !== "completed" || !selectedAtSubmit || run.cancellationRequested) continue;
        runOutputBases.current.delete(run.id);
        const item = canvasRunOutputItems(run).find(item => item.status === "installed" && item.assetId && !item.cancellationRequested);
        if (item?.assetId) outputReplacements.set(run.nodeId, { output: item.assetId, outputRef: { runId: run.id, resultId: item.resultId }, selectedAtSubmit });
      }
      if (outputReplacements.size) {
        const replacements = [...outputReplacements].map(([nodeId, candidate]) => ({ nodeId, ...candidate }));
        setPendingOutputReplacements(current => ({ id: responseId, replacements: [...(current?.replacements ?? []), ...replacements] }));
      }
    }
  }, []);

  function editNode(id: string, patch: Partial<CreativeCanvasNode>) {
    if (!document.nodes.some(item => item.id === id)) return;
    setDocument(current => {
      let editedText = false;
      const nodes = current.nodes.map(item => {
      if (item.id !== id) return item;
      let params = patch.params ? { ...item.params, ...patch.params } : item.params;
      const legacyRatioEdit = Boolean(!params?.providerId && !params?.modelId && patch.params?.ratio && patch.params.width === undefined && patch.params.height === undefined);
      if (legacyRatioEdit && patch.params?.ratio) {
        const [left, right] = patch.params.ratio.split(":").map(Number);
        if (left > 0 && right > 0) { const width = params?.width ?? 1024; params = { ...params, width, height: Math.max(1, Math.round(width * right / left)) }; }
      }
      const width = params?.width; const height = params?.height;
      const explicitPixels = patch.params?.width !== undefined || patch.params?.height !== undefined || legacyRatioEdit;
      const synchronized = explicitPixels && width && height ? { ...params, size: `${width}x${height}`, ratio: patch.params?.ratio ?? `${width}:${height}` } : params;
        editedText = item.kind === "text" && patch.text !== undefined && patch.text !== item.text;
        const updated = { ...item, ...patch, params: synchronized };
        return editedText ? { ...updated, currentTextVersionId: undefined } : updated;
      });
      const next = { ...current, nodes };
      return editedText ? markDownstreamStale(next, new Set([id])) : next;
    });
    markDirty();
  }
  function deleteNode(id: string) { deleteNodes([id]); }
  function deleteNodes(ids: string[]) {
    const removed = groupSubtreeIds(documentRef.current, ids);
    if (!removed.size) return;
    setDocument(current => {
      const edges = current.edges.filter(edge => !removed.has(edge.source) && !removed.has(edge.target));
      return { ...current, nodes: pruneReferenceOrders(current.nodes.filter(node => !removed.has(node.id)), edges, current.edges), groups: current.groups?.filter(group => !removed.has(group.id)), edges };
    });
    setSelectedId(current => current && removed.has(current) ? undefined : current);
    setInspectorOpen(false);
    setSelectedNodeIds(current => new Set([...current].filter(id => !removed.has(id))));
    markDirty();
  }
  function addCopiedGraph(result: { nodes: CreativeCanvasNode[]; groups?: CreativeCanvasDocument["groups"]; edges: CreativeCanvasEdge[]; selectedIds: string[] }, message: string) {
    if (!result.nodes.length && !result.groups?.length) return;
    setDocument(current => ({ ...current, nodes: [...current.nodes, ...result.nodes], groups: [...(current.groups ?? []), ...(result.groups ?? [])], edges: [...current.edges, ...result.edges] }));
    setSelectedId(result.selectedIds[0]);
    setSelectedNodeIds(new Set(result.selectedIds));
    markDirty();
    setNotice(message);
  }
  function duplicateNode(id: string) {
    try { addCopiedGraph(duplicateCanvasSubgraph(documentRef.current, [id]), "已创建节点副本，并保留原有输入连接。"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法创建节点副本。"); }
  }
  function copyNodes(ids: string[]) {
    try { addCopiedGraph(copyCanvasNodes(documentRef.current, ids), "已复制节点，不包含输入连接。"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法复制节点。"); }
  }
  function duplicateNodes(ids: string[]) {
    try { addCopiedGraph(duplicateCanvasSubgraph(documentRef.current, ids), "已创建选区副本，并保留原有输入连接。"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法创建选区副本。"); }
  }
  async function copyToClipboard(ids: string[]) {
    try {
      const command = saveCanvasClipboard(createCanvasClipboardSnapshot(projectId, documentRef.current, ids));
      if (!navigator.clipboard?.writeText) throw new Error("浏览器不支持写入剪贴板。");
      await navigator.clipboard.writeText(command);
      setNotice("已复制到剪贴板，可在画布中粘贴。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法写入剪贴板，请检查浏览器权限。"); }
  }
  async function pasteFromClipboard(position: { x: number; y: number }, text?: string) {
    const request = ++clipboardRequest.current;
    const project = projectId;
    const canvasId = canvasRef.current;
    try {
      const command = text ?? await navigator.clipboard?.readText();
      if (!activeRef.current || projectRef.current !== project || canvasRef.current !== canvasId || request !== clipboardRequest.current) return;
      if (!command || !isCanvasClipboardCommand(command)) throw new Error("剪贴板中没有可粘贴的画布节点。");
      const snapshot = readCanvasClipboard(command);
      if (!snapshot) throw new Error("剪贴板中没有可粘贴的画布节点。");
      addCopiedGraph(pasteCanvasClipboardSnapshot(documentRef.current, snapshot, projectId, assets, position), "已粘贴节点子图。");
    } catch (reason) {
      if (!activeRef.current || projectRef.current !== project || canvasRef.current !== canvasId || request !== clipboardRequest.current) return;
      setError(reason instanceof Error ? reason.message : "无法读取剪贴板，请检查浏览器权限。");
    }
  }
  function canvasPosition(clientX: number, clientY: number) {
    const rect = stageRef.current?.getBoundingClientRect();
    const viewport = documentRef.current.viewport;
    if (!rect) return { x: 0, y: 0 };
    return { x: (clientX - rect.left - viewport.x) / viewport.zoom, y: (clientY - rect.top - viewport.y) / viewport.zoom };
  }
  function centerCanvasPosition() {
    const rect = stageRef.current?.getBoundingClientRect();
    const viewport = documentRef.current.viewport;
    return rect ? { x: (rect.width / 2 - viewport.x) / viewport.zoom, y: (rect.height / 2 - viewport.y) / viewport.zoom } : { x: 0, y: 0 };
  }
  function openContextMenu(event: { preventDefault: () => void; clientX: number; clientY: number }, kind: CanvasContextMenu["kind"], ids: string[]) {
    event.preventDefault();
    if (loading || !activeRef.current) return;
    setContextMenu({ x: event.clientX, y: event.clientY, position: canvasPosition(event.clientX, event.clientY), kind, ids });
  }
  function reorderReferences(id: string, order: string[]) { setDocument(current => { const node = current.nodes.find(item => item.id === id); const previous = orderReferenceEdges(current.edges.filter(edge => edge.target === id), node?.referenceOrder).map(edge => edge.id); return { ...current, nodes: current.nodes.map(item => item.id !== id ? item : item.kind === "text" ? { ...item, referenceOrder: order, prompt: remapReferenceTokens(item.prompt, previous, order) } : { ...item, referenceOrder: order, text: remapReferenceTokens(item.text, previous, order) }) }; }); markDirty(); }
  function removeReference(edgeId: string) { setDocument(current => { const edge = current.edges.find(item => item.id === edgeId); const edges = current.edges.filter(item => item.id !== edgeId); return { ...current, edges, nodes: current.nodes.map(node => { if (node.id !== edge?.target) return { ...node, referenceOrder: node.referenceOrder?.filter(id => id !== edgeId) }; const previous = orderReferenceEdges(current.edges.filter(item => item.target === node.id), node.referenceOrder).map(item => item.id); const next = previous.filter(id => id !== edgeId); return node.kind === "text" ? { ...node, referenceOrder: next, prompt: remapReferenceTokens(node.prompt, previous, next) } : { ...node, referenceOrder: next, text: remapReferenceTokens(node.text, previous, next) }; }) }; }); markDirty(); }
  function closeImageEdit() { imageEditRef.current = undefined; setImageEdit(undefined); }
  function openImageEdit(nodeId: string, mode: "crop" | "split") {
    const source = nodeCurrentAsset(nodeId);
    const node = documentRef.current.nodes.find(item => item.id === nodeId);
    if (!node || !isImage(source) || !source) return;
    const position = absoluteCanvasNodePosition(documentRef.current, nodeId);
    const session: ImageEditSession = { id: crypto.randomUUID(), projectId, canvasId: canvasRef.current, source, position: { x: position.x + (nodeMeasurements[nodeId]?.width ?? node.width ?? 220) + 96, y: position.y }, mode, installed: new Set() };
    imageEditRef.current = session; setImageEdit(session); setContextMenu(undefined);
  }
  function installEditedAsset(session: ImageEditSession, asset: CreativeCanvasAsset, derivation: ImageGeometryOperation) {
    if (!alive.current || !activeRef.current || projectRef.current !== session.projectId || canvasRef.current !== session.canvasId || imageEditRef.current !== session || session.installed.has(asset.id)) return;
    session.installed.add(asset.id);
    setAssets(current => merge(current, [asset]));
    const node = newNode(documentRef.current, "media", asset);
    const columns = "columns" in derivation.params ? derivation.params.columns : 1;
    node.position = { x: session.position.x + derivation.index % columns * 260, y: session.position.y + Math.floor(derivation.index / columns) * 300 };
    setDocument(current => ({ ...current, nodes: [...current.nodes, node] }));
    setSelectedId(node.id); setSelectedNodeIds(new Set([node.id])); markDirty();
  }
  function downloadAsset(asset: CreativeCanvasAsset) { const anchor = window.document.createElement("a"); anchor.href = asset.url; anchor.download = asset.name || "creative-canvas-image"; anchor.click(); }
  function copyPrompt(prompt: string) { void navigator.clipboard?.writeText(prompt).then(() => setNotice("提示词已复制"), () => setError("无法复制提示词，请检查浏览器权限。")); }
  function branchAsset(asset: CreativeCanvasAsset, sourceNodeId: string) {
    const source = documentRef.current.nodes.find(node => node.id === sourceNodeId);
    if (source?.kind === "media" && source.assetId === asset.id) {
      const generated = newNode(documentRef.current, "generate");
      const edge = { id: `edge-${crypto.randomUUID()}`, source: source.id, target: generated.id, sourceHandle: "image" as const, targetHandle: "image" as const };
      generated.referenceOrder = [edge.id];
      setSelectedId(generated.id); setSelectedNodeIds(new Set([generated.id]));
      setDocument(current => current.nodes.some(node => node.id === source.id) ? { ...current, nodes: [...current.nodes, generated], edges: [...current.edges, edge] } : current);
      markDirty(); setNotice("已从素材创建图片生成分支"); return;
    }
    const runItem = runs.find(run => run.outputAssetIds.includes(asset.id));
    if (runItem) branch(runItem, asset.id);
  }
  function generateImageFromText(sourceNodeId: string) {
    const source = documentRef.current.nodes.find(node => node.id === sourceNodeId);
    if (source?.kind !== "text" || !source.text?.trim()) return;
    const generated = newNode(documentRef.current, "generate");
    generated.position = { x: source.position.x + 300, y: source.position.y };
    const edge: CreativeCanvasEdge = { id: `edge-${crypto.randomUUID()}`, source: source.id, target: generated.id, sourceHandle: "text", targetHandle: "prompt" };
    generated.referenceOrder = [edge.id];
    setDocument(current => current.nodes.some(node => node.id === source.id) ? { ...current, nodes: [...current.nodes, generated], edges: [...current.edges, edge] } : current);
    setSelectedId(generated.id); setSelectedNodeIds(new Set([generated.id])); markDirty(); setNotice("已从文本创建图片生成节点");
  }
  function nodeCurrentAsset(id: string) {
    const asset = canvasNodeOutput(documentRef.current.nodes.find(item => item.id === id), runs, assets);
    return isImage(asset) ? asset : undefined;
  }
  function openNodeInfo(id: string) { setSelectedId(id); setSelectedNodeIds(new Set([id])); setInspectorOpen(true); }

  const applyWorkspace = useCallback((workspace: CanvasDraft, draftDirty = false) => {
    setDocument(workspace.document); previousDocument.current = workspace.document; undoStack.current = workspace.undo ?? []; redoStack.current = workspace.redo ?? []; setHistoryRevision(value => value + 1);
    setRuns(workspace.runs); setTextRuns(workspace.textRuns ?? []); if (workspace.textModels) setTextModels(workspace.textModels); setBatches(workspace.batches ?? []); setAssets(workspace.assets); dirtyRef.current = draftDirty; setDirty(draftDirty);
  }, []);
  const load = useCallback(async (canvasId = canvasRef.current, discard = false) => {
    if (dirtyRef.current && canvasId === canvasRef.current && !discard) return;
    const cached = localDrafts.current.get(canvasId);
    if (cached && !discard) { applyWorkspace(cached, Boolean(cached.dirty)); setLoading(false); return; }
    const request = ++requestId.current; const project = projectId;
    setLoading(true); setError("");
    try { const workspace = await getCreativeCanvas(project, canvasScope(canvasId)); if (!valid(project, request, canvasId)) return; applyWorkspace(workspace); localDrafts.current.delete(canvasId); }
    catch (reason) { if (valid(project, request, canvasId)) setError(reason instanceof Error ? reason.message : "无法读取创作画布。"); }
    finally { if (valid(project, request, canvasId)) setLoading(false); }
  }, [applyWorkspace, projectId, valid]);

  const refreshCatalog = useCallback(async (includeDeleted = showDeletedCanvases) => {
    const request = ++catalogRequest.current; const project = projectId;
    try {
      const result = await listCreativeCanvases(project, includeDeleted);
      if (!alive.current || projectRef.current !== project || request !== catalogRequest.current) return;
      setCanvases(result.canvases);
    } catch (reason) { if (alive.current && projectRef.current === project) setError(reason instanceof Error ? reason.message : "无法读取画布目录。"); }
  }, [projectId, showDeletedCanvases]);

  const activateCanvas = useCallback((canvasId: string) => {
    if (canvasId === canvasRef.current) return;
    closeImageEdit(); clipboardRequest.current += 1; setUploading(false); setResultActionBusy(false); setReplaceNodeId(undefined); setUploadPosition(undefined); runInflight.current.clear(); runOutputBases.current.clear(); setPendingOutputReplacements(undefined); setSelectedId(undefined); setSelectedNodeIds(new Set()); setSelectedEdgeIds(new Set());
    canvasSession.current += 1; canvasRef.current = canvasId; setActiveCanvasId(canvasId); setCanvasMenuOpen(false); setHistoryOpen(false); setError("");
    void load(canvasId);
  }, [load]);

  const requestCanvasSwitch = useCallback((canvasId: string) => {
    if (canvasId === canvasRef.current || canvasActionBusy) return;
    if (dirtyRef.current) { setPendingCanvasId(canvasId); return; }
    activateCanvas(canvasId);
  }, [activateCanvas, canvasActionBusy]);

  const refreshAssets = useCallback(async () => {
    const request = ++assetRequestId.current; const project = projectId; const canvasId = canvasRef.current;
    try { const workspace = await getCreativeCanvas(project, canvasScope(canvasId)); if (alive.current && projectRef.current === project && canvasRef.current === canvasId && request === assetRequestId.current) { setAssets(workspace.assets); if (workspace.textModels) setTextModels(workspace.textModels); } } catch { /* Preserve the local canvas draft. */ }
  }, [projectId]);

  const createCanvas = async () => {
    const name = newCanvasName.trim(); if (!name || canvasActionBusy) return;
    const project = projectId; setCanvasActionBusy(true); setError("");
    try { const result = await createCreativeCanvas(project, name); if (!alive.current || projectRef.current !== project) return; setCanvases(current => [...current.filter(item => item.id !== result.canvas.id), result.canvas]); setNewCanvasName(""); if (dirtyRef.current) setPendingCanvasId(result.canvas.id); else activateCanvas(result.canvas.id); }
    catch (reason) { if (alive.current && projectRef.current === project) setError(reason instanceof Error ? reason.message : "无法新建画布。"); }
    finally { if (alive.current && projectRef.current === project) setCanvasActionBusy(false); }
  };
  const renameCanvas = async (canvasId: string) => {
    const name = renameCanvasName.trim(); if (!name || canvasActionBusy) return;
    const project = projectId; setCanvasActionBusy(true); setError("");
    try { const result = await renameCreativeCanvas(project, canvasId, name); if (!alive.current || projectRef.current !== project) return; setCanvases(current => current.map(item => item.id === canvasId ? result.canvas : item)); setRenamingCanvasId(undefined); }
    catch (reason) { if (alive.current && projectRef.current === project) setError(reason instanceof Error ? reason.message : "无法重命名画布。"); }
    finally { if (alive.current && projectRef.current === project) setCanvasActionBusy(false); }
  };
  const removeCanvas = async (canvasId: string) => {
    if (canvasActionBusy) return;
    if (canvasId === canvasRef.current && dirtyRef.current) { setDeleteCanvasId(undefined); setError("当前画布有未保存草稿；请先保存，或切换时选择保留草稿。"); return; }
    const project = projectId; setCanvasActionBusy(true); setError("");
    try { const result = await deleteCreativeCanvas(project, canvasId); if (!alive.current || projectRef.current !== project) return; setCanvases(current => current.map(item => item.id === canvasId ? result.canvas : item)); setDeleteCanvasId(undefined); if (canvasId === canvasRef.current) activateCanvas("default"); }
    catch (reason) { if (alive.current && projectRef.current === project) setError(reason instanceof Error ? reason.message : "无法删除画布。"); }
    finally { if (alive.current && projectRef.current === project) setCanvasActionBusy(false); }
  };
  const restoreCanvas = async (canvasId: string) => {
    if (canvasActionBusy) return;
    const project = projectId; setCanvasActionBusy(true); setError("");
    try { const result = await restoreCreativeCanvas(project, canvasId); if (!alive.current || projectRef.current !== project) return; setCanvases(current => current.map(item => item.id === canvasId ? result.canvas : item)); }
    catch (reason) { if (alive.current && projectRef.current === project) setError(reason instanceof Error ? reason.message : "无法恢复画布。"); }
    finally { if (alive.current && projectRef.current === project) setCanvasActionBusy(false); }
  };
  const keepDraftAndSwitch = () => {
    const target = pendingCanvasId; if (!target) return;
    localDrafts.current.set(canvasRef.current, { document, assets, runs, textRuns, batches, dirty: true, undo: [...undoStack.current], redo: [...redoStack.current] }); dirtyRef.current = false; setDirty(false); setPendingCanvasId(undefined); activateCanvas(target);
  };
  const saveAndSwitch = async () => {
    const target = pendingCanvasId; if (!target) return;
    setCanvasActionBusy(true); const result = await save(); setCanvasActionBusy(false);
    if (result.saved) { setPendingCanvasId(undefined); activateCanvas(target); }
  };

  useEffect(() => { alive.current = true; projectRef.current = projectId; canvasSession.current += 1; canvasRef.current = "default"; setActiveCanvasId("default"); localDrafts.current.clear(); void load("default", true); return () => { alive.current = false; requestId.current += 1; catalogRequest.current += 1; }; }, [load, projectId]);
  useEffect(() => () => { activeRef.current = false; clipboardRequest.current += 1; if (dragFinishTimer.current) window.clearTimeout(dragFinishTimer.current); }, []);
  useEffect(() => { activeRef.current = active; if (!active) { if (dragFinishTimer.current) window.clearTimeout(dragFinishTimer.current); clipboardRequest.current += 1; setContextMenu(undefined); setQuickAddOpen(false); setCanvasMenuOpen(false); setZoomMenuOpen(false); setHelpOpen(false); } }, [active]);
  useEffect(() => { closeImageEdit(); setSearchOpen(false); }, [projectId, active, activeCanvasId]);
  const projectHasDrafts = dirty || [...localDrafts.current.values()].some(draft => draft.dirty);
  useEffect(() => { onDirtyChange(projectHasDrafts); }, [projectHasDrafts, onDirtyChange]);
  useEffect(() => { documentRef.current = document; }, [document]);
  useEffect(() => {
    if (!pendingOutputReplacements || appliedOutputResponse.current === pendingOutputReplacements.id) return;
    appliedOutputResponse.current = pendingOutputReplacements.id;
    let current = documentRef.current;
    let changed = false;
    const changedSources = new Set<string>();
    for (const candidate of pendingOutputReplacements.replacements) {
      const node = current.nodes.find(item => item.id === candidate.nodeId);
      if (!node || node.outputAssetId !== candidate.selectedAtSubmit.assetId || JSON.stringify(node.currentOutputRef) !== JSON.stringify(candidate.selectedAtSubmit.ref)) continue;
      if (node.outputAssetId === candidate.output && JSON.stringify(node.currentOutputRef) === JSON.stringify(candidate.outputRef)) continue;
      changed = true;
      changedSources.add(candidate.nodeId);
      current = { ...current, nodes: current.nodes.map(item => item.id === candidate.nodeId ? { ...item, outputAssetId: candidate.output, currentOutputRef: candidate.outputRef } : item) };
    }
    if (changed) {
      current = markDownstreamStale(current, changedSources);
      setDocument(current);
      markDirty();
    }
    setPendingOutputReplacements(current => current?.id === pendingOutputReplacements.id ? undefined : current);
  }, [markDirty, pendingOutputReplacements]);
  useEffect(() => {
    if (!uploadedReplacement) return;
    if (uploadedReplacement.session === canvasSession.current) {
      const node = document.nodes.find(item => item.id === uploadedReplacement.nodeId);
      if ((node?.kind === "media" ? node.assetId : node?.outputAssetId) === uploadedReplacement.assetId) {
        markDirty(); setNotice("素材已替换，历史文件保持不变。");
      } else setNotice("素材已入库；节点已变化，未覆盖新选择。");
    }
    setUploadedReplacement(undefined);
  }, [document, uploadedReplacement, markDirty]);
  const closePreview = useCallback(() => { const dialog = imageDialogRef.current; if (dialog?.open) { if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open"); } setPreviewAsset(undefined); window.setTimeout(() => previewTrigger.current?.focus(), 0); }, []);
  useEffect(() => {
    const dialog = imageDialogRef.current;
    if (!dialog || !previewAsset) return;
    if (!dialog.open) { if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", ""); }
    window.setTimeout(() => dialog.querySelector<HTMLButtonElement>("[data-dialog-close]")?.focus(), 0);
    return () => { if (dialog.open && typeof dialog.close === "function") dialog.close(); };
  }, [previewAsset]);
  useEffect(() => {
    if (applyingHistory.current) { applyingHistory.current = false; previousDocument.current = document; previousHistoryKind.current = "edit"; return; }
    const previous = previousDocument.current;
    if (dirtyRef.current && JSON.stringify([previous.nodes, previous.edges, previous.groups, previous.settings]) !== JSON.stringify([document.nodes, document.edges, document.groups, document.settings])) {
      if (!(historyKind.current === "drag" && previousHistoryKind.current === "drag")) {
        undoStack.current.push(previous); redoStack.current = []; setHistoryRevision(value => value + 1);
      }
      previousHistoryKind.current = historyKind.current;
    }
    previousDocument.current = document;
  }, [document]);
  useEffect(() => { const returning = active && !previousActive.current; previousActive.current = active; if (returning) void refreshAssets(); }, [active, refreshAssets]);
  useEffect(() => { if (!active || selected?.kind !== "generate") return; const project = projectId; void getImageGenerationConfiguration().then(config => { if (alive.current && projectRef.current === project) { setConfiguration(config); setModelLabel(config.models?.[0] || config.model || "未配置模型"); } }).catch(() => undefined); }, [active, projectId, selected?.kind]);
  useEffect(() => {
    const pending = runs.find(run => ["submitting", "queued", "running", "cancel_requested"].includes(run.status));
    const batch = batches.find(item => ["queued", "running", "cancel_requested"].includes(item.status));
    if (!active || (!pending && !batch)) return;
    const project = projectId; const canvasId = activeCanvasId; const session = canvasSession.current;
    let disposed = false;
    let timer: number;
    const poll = async () => {
      const version = editVersion.current;
      try {
        const workspace = batch ? await refreshCreativeCanvasBatch(project, batch.id, canvasScope(canvasId)) : await refreshCreativeCanvasRun(project, pending!.id, canvasScope(canvasId));
        if (!disposed && alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, version, false);
      } catch (reason) { if (!disposed) setError(reason instanceof Error ? reason.message : "无法刷新生成状态。"); }
      finally { if (!disposed) timer = window.setTimeout(poll, 5000); }
    };
    timer = window.setTimeout(poll, 5000);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [active, activeCanvasId, applyResponse, projectId, runs, batches]);

  useEffect(() => {
    if (!active || !textRuns.some(run => ["submitting", "queued", "running", "cancel_requested"].includes(run.status))) return;
    const project = projectId; const canvasId = activeCanvasId; const session = canvasSession.current;
    let disposed = false;
    const poll = async () => {
      try {
        const workspace = await getCreativeCanvas(project, canvasScope(canvasId));
        if (!disposed && alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, editVersion.current, false);
      } catch (reason) { if (!disposed) setError(reason instanceof Error ? reason.message : "无法刷新文本生成状态。"); }
    };
    const timer = window.setInterval(() => void poll(), 1000);
    void poll();
    return () => { disposed = true; window.clearInterval(timer); };
  }, [active, activeCanvasId, applyResponse, projectId, textRuns]);


  const renameGroup = (id: string, label: string) => { setDocument(current => ({ ...current, groups: current.groups?.map(group => group.id === id ? { ...group, label } : group) })); markDirty(); };
  const startGroupResize = (id: string) => {
    const group = documentRef.current.groups?.find(item => item.id === id);
    if (group) groupResizeOrigins.current.set(id, { ...group.position });
  };
  const resizeGroup = (id: string, width: number, height: number, position?: { x: number; y: number }) => {
    const boundedWidth = Math.min(8192, Math.max(120, width));
    const boundedHeight = Math.min(8192, Math.max(80, height));
    setDocument(current => {
      const group = current.groups?.find(item => item.id === id);
      if (!group) return current;
      const origin = groupResizeOrigins.current.get(id) ?? group.position;
      const nextPosition = position ?? group.position;
      const delta = { x: nextPosition.x - origin.x, y: nextPosition.y - origin.y };
      groupResizeOrigins.current.delete(id);
      return {
        ...current,
        nodes: current.nodes.map(node => node.parentId === id ? { ...node, position: { x: node.position.x - delta.x, y: node.position.y - delta.y } } : node),
        groups: current.groups?.map(item => item.id === id ? { ...item, position: nextPosition, width: boundedWidth, height: boundedHeight } : item.parentId === id ? { ...item, position: { x: item.position.x - delta.x, y: item.position.y - delta.y } } : item),
      };
    });
    markDirty();
  };
  const flowNodes = useMemo(() => {
    const byAsset = new Map(assets.map(asset => [asset.id, asset]));
    const byNode = new Map(document.nodes.map(node => [node.id, node]));
    const nodes = document.nodes.map(node => {
      const matchingRuns = node.kind === "generate" ? runs.filter(item => item.nodeId === node.id) : [];
      const currentRun = matchingRuns[matchingRuns.length - 1];
      const matchingTextRuns = node.kind === "text" ? textRuns.filter(item => item.nodeId === node.id) : [];
      const currentTextRun = [...matchingTextRuns].reverse().find(item => ["submitting", "queued", "running", "cancel_requested", "unknown"].includes(item.status));
      const output = canvasNodeOutput(node, runs, assets);
      const latestResult = isImage(output) ? output : undefined;
      const references = node.kind === "generate" || node.kind === "text" ? orderReferenceEdges(document.edges.filter(edge => edge.target === node.id), node.referenceOrder).map(edge => {
        const source = byNode.get(edge.source);
        const asset = edge.sourceHandle === "image" ? canvasNodeOutput(source, runs, assets) : undefined;
        return { id: edge.id, dataType: edge.sourceHandle, sourceLabel: source?.label ?? "引用", text: edge.sourceHandle === "text" ? source?.text : undefined, asset: isImage(asset) ? asset : undefined };
      }) : [];
      const configuredModels = configuration?.models ?? (configuration?.model ? [configuration.model] : []);
      const nodeModelLabel = node.params?.model ?? configuration?.model ?? "未配置模型";
      const modelUnavailable = Boolean(node.params?.model && !configuredModels.includes(node.params.model));
      const modelOptions = modelUnavailable && node.params?.model ? [node.params.model, ...configuredModels] : configuredModels;
      return { id: node.id, type: node.kind, position: node.position, parentId: node.parentId, selected: selectedNodeIds.has(node.id), measured: nodeMeasurements[node.id], data: {
        ...node, connectedHandles: [...new Set(document.edges.flatMap(edge => [...(edge.source === node.id ? [`source:${edge.sourceHandle}`] : []), ...(edge.target === node.id ? [`target:${edge.targetHandle}`] : [])]))], imageModelCatalog: configuration?.modelCatalog ?? [], imageProviders: configuration?.providers?.map(({ providerId, credentialState, protocol, defaultModelId, models }) => ({ providerId, credentialState, protocol, defaultModelId, models })) ?? [], configurationVersion: node.kind === "text" ? node.configurationVersion : configuration?.configVersion ?? undefined, asset: node.assetId ? byAsset.get(node.assetId) : undefined, latestResult, run: currentRun, textRun: currentTextRun, textRuns: matchingTextRuns, textModels, references, modelLabel: nodeModelLabel, modelOptions, modelUnavailable, selected: selectedNodeIds.has(node.id), submitting: runningNodeId === node.id,
        onEdit: editNode, onRun: (id: string) => { setSelectedId(id); setSelectedNodeIds(new Set([id])); void run(id); }, onCancel: (id: string) => void cancelRun(id), onRunText: (id: string) => void runText(id), onCancelText: (id: string) => void cancelTextRun(id), onSelectTextVersion: (runId: string, versionId: string) => void selectTextVersion(runId, versionId), onInstallTextVersion: (runId: string, versionId: string) => void installTextVersion(runId, versionId), onGenerateImageFromText: generateImageFromText, onOpenSettings,
        onDelete: deleteNode, onDuplicate: duplicateNode, onOpenInfo: (id: string) => { setSelectedId(id); setInspectorOpen(true); }, onPreview: (asset: CreativeCanvasAsset) => { previewTrigger.current = window.document.activeElement as HTMLElement | null; setPreviewAsset(asset); }, onDownload: downloadAsset, onReplace: (id: string) => { if (!activeRun(id)) { setReplaceNodeId(id); inputRef.current?.click(); } }, onBranch: (asset: CreativeCanvasAsset, sourceNodeId: string) => branchAsset(asset, sourceNodeId), onCopyPrompt: copyPrompt, onReorderReferences: reorderReferences, onRemoveReference: removeReference,
      } };
    });
    const groups: CanvasFlowGroup[] = (document.groups ?? []).map(group => ({ id: group.id, type: "canvasGroup", position: group.position, parentId: group.parentId, width: group.width, height: group.height, measured: nodeMeasurements[group.id] ?? { width: group.width, height: group.height }, selected: selectedNodeIds.has(group.id), style: { width: group.width, height: group.height }, data: { label: group.label ?? "分组", onRename: renameGroup, onResizeStart: startGroupResize, onResize: resizeGroup } }));
    const groupsById = new Map(groups.map(group => [group.id, group]));
    const orderedGroups: CanvasFlowGroup[] = [];
    const appended = new Set<string>();
    const append = (group: CanvasFlowGroup) => {
      if (appended.has(group.id)) return;
      const parent = group.parentId ? groupsById.get(group.parentId) : undefined;
      if (parent) append(parent);
      appended.add(group.id);
      orderedGroups.push(group);
    };
    groups.forEach(append);
    return [...orderedGroups, ...nodes] as unknown as CanvasFlowNode[];
  }, [assets, configuration, document, modelLabel, nodeMeasurements, onOpenSettings, runningNodeId, runs, selectedNodeIds, textRuns, textModels]);
  const flowEdges = useMemo(() => showEdges ? document.edges.map(edge => ({ ...edge, selected: selectedEdgeIds.has(edge.id), style: { stroke: "#409eff", strokeDasharray: "5 4" } })) : [], [document.edges, selectedEdgeIds, showEdges]);

  const groupSelected = (ids = [...selectedNodeIds]) => { if (ids.length < 2) return; try { const id = `group-${crypto.randomUUID()}`; const next = createCanvasGroup(documentRef.current, ids, "分组", id, nodeMeasurements as Record<string, { width: number; height: number }>); setDocument(next); setSelectedId(id); setSelectedNodeIds(new Set([id])); markDirty(); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法创建分组。"); } };
  const mergeSelectedGroups = (ids = [...selectedNodeIds]) => { try { const next = mergeCanvasGroups(documentRef.current, ids); setDocument(next); markDirty(); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法合并分组。"); } };
  const ungroupSelected = (selection = [...selectedNodeIds]) => { const ids = selection.filter(id => documentRef.current.groups?.some(group => group.id === id)); if (!ids.length) return; try { const next = ids.reduce((next, id) => ungroupCanvas(next, id), documentRef.current); setDocument(next); markDirty(); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法解组。"); } };
  const selectedGroupIds = [...selectedNodeIds].filter(id => document.groups?.some(group => group.id === id));
  const groupToolbarVisible = selectedNodeIds.size > 1 || selectedGroupIds.length > 0;
  const groupToolbarStyle = useMemo(() => {
    if (!groupToolbarVisible) return undefined;
    const selectedIds = [...selectedNodeIds];
    try {
      const rectangles = selectedIds.map(id => {
        const group = document.groups?.find(item => item.id === id);
        if (group) return { ...absoluteCanvasGroupPosition(document, id), width: group.width, height: group.height };
        const node = document.nodes.find(item => item.id === id);
        if (!node) return undefined;
        return { ...absoluteCanvasNodePosition(document, id), width: nodeMeasurements[id]?.width ?? node.width ?? 220, height: nodeMeasurements[id]?.height ?? node.height ?? 160 };
      }).filter((rectangle): rectangle is { x: number; y: number; width: number; height: number } => Boolean(rectangle));
      if (!rectangles.length) return undefined;
      const left = Math.min(...rectangles.map(rectangle => rectangle.x));
      const bottom = Math.max(...rectangles.map(rectangle => rectangle.y + rectangle.height));
      const viewport = document.viewport;
      const stage = stageRef.current;
      const x = viewport.x + left * viewport.zoom;
      const y = viewport.y + bottom * viewport.zoom + 10;
      return { left: Math.max(8, Math.min(x, (stage?.clientWidth ?? window.innerWidth) - 190)), top: Math.max(8, Math.min(y, (stage?.clientHeight ?? window.innerHeight) - 42)) };
    } catch { return undefined; }
  }, [document, groupToolbarVisible, nodeMeasurements, selectedNodeIds]);

  const add = (kind: CreativeCanvasKind, asset?: CreativeCanvasAsset, position?: { x: number; y: number }) => { if (loading || !activeRef.current) return; const node = newNode(documentRef.current, kind, asset); const appliedPosition = position ?? (kind === "media" ? assetPickerPosition : undefined); if (appliedPosition) node.position = appliedPosition; setSelectedId(node.id); setSelectedNodeIds(new Set([node.id])); setDocument(current => ({ ...current, nodes: [...current.nodes, node] })); setAssetPickerOpen(false); setAssetPickerPosition(undefined); markDirty(); };

  async function addTextAsset(asset: CreativeCanvasAsset) {
    const project = projectId; const session = canvasSession.current; const request = ++clipboardRequest.current;
    try {
      const response = await fetch(asset.url);
      if (!response.ok) throw new Error("无法读取文本素材，请确认文件仍可用。");
      const text = await response.text();
      if ([...text].length > 12000) throw new Error("文本素材超过节点的 12,000 字符上限，请先缩短正文。");
      if (!alive.current || !activeRef.current || projectRef.current !== project || canvasSession.current !== session || clipboardRequest.current !== request) return;
      const node = { ...newNode(documentRef.current, "text", asset), text, ...(assetPickerPosition ? { position: assetPickerPosition } : {}) };
      setDocument(current => ({ ...current, nodes: [...current.nodes, node] }));
      setSelectedId(node.id); setSelectedNodeIds(new Set([node.id])); setAssetPickerOpen(false); setAssetPickerPosition(undefined); markDirty();
      setNotice("文本已作为可编辑副本加入画布，原素材保持不变。");
    } catch (reason) {
      if (alive.current && activeRef.current && projectRef.current === project && canvasSession.current === session && clipboardRequest.current === request) setError(reason instanceof Error ? reason.message : "无法读取文本素材。");
    }
  }
  const save = useCallback(async (): Promise<SaveResult> => {
    if (saving) return { saved: false, revision: document.revision, changedDuringSave: false };
    if (!dirtyRef.current) return { saved: true, revision: document.revision, changedDuringSave: false };
    const snapshot = document; const version = editVersion.current; const request = ++requestId.current; const project = projectId; const canvasId = canvasRef.current;
    setSaving(true); setError("");
    try { const workspace = await saveCreativeCanvas(project, snapshot, canvasScope(canvasId)); if (!valid(project, request, canvasId)) return { saved: false, revision: snapshot.revision, changedDuringSave: false }; const changedDuringSave = version !== editVersion.current; applyResponse(workspace, version, true); if (!changedDuringSave) { dirtyRef.current = false; localDrafts.current.delete(canvasId); setDirty(false); setNotice("画布已保存"); } return { saved: !changedDuringSave, revision: workspace.document.revision, changedDuringSave }; }
    catch (reason) { if (valid(project, request, canvasId)) { const current = (reason instanceof Error ? reason : new Error("无法保存画布。")) as Error & { status?: number }; setError(current.status === 409 ? `${current.message}。本地草稿已保留。` : current.message); } return { saved: false, revision: snapshot.revision, changedDuringSave: false }; }
    finally { if (valid(project, request, canvasId)) setSaving(false); }
  }, [applyResponse, document, projectId, saving, valid]);

  async function run(nodeId: string) {
    if (runInflight.current.has(nodeId) || runningNodeId || activeRun(nodeId)) return;
    runInflight.current.add(nodeId);
    const submittedNode = document.nodes.find(node => node.id === nodeId);
    const selectedBeforeRun: OutputSelection = { assetId: submittedNode?.outputAssetId, ref: submittedNode?.currentOutputRef };
    setRunningNodeId(nodeId);
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    try {
      const saved = await save();
      if (!saved.saved) { if (saved.changedDuringSave) setError("保存期间提示词已更新，请先保存当前草稿后再生成。"); return; }
      const version = editVersion.current;
      if (!alive.current || projectRef.current !== project || canvasRef.current !== canvasId || canvasSession.current !== session) return;
      const requestFingerprint = JSON.stringify([project, canvasId, nodeId, saved.revision]);
      const idempotencyKey = runRequestKeys.current.get(requestFingerprint) ?? crypto.randomUUID();
      runRequestKeys.current.set(requestFingerprint, idempotencyKey);
      const workspace = await runCreativeCanvasNode(project, nodeId, saved.revision, idempotencyKey, canvasScope(canvasId), submittedNode?.params?.count ?? 1);
      runRequestKeys.current.delete(requestFingerprint);
      const submitted = workspace.runs.find(run => run.idempotencyKey === idempotencyKey);
      if (submitted && alive.current && canvasSession.current === session) runOutputBases.current.set(submitted.id, selectedBeforeRun);
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, version, false);
    }
    catch (reason) { if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) { const message = reason instanceof Error ? reason.message : "无法提交图片生成。"; setError(message.includes("input_budget_exceeded") ? "参考图片超过本次请求容量，请减少图片或压缩后重试。" : message.includes("creative_canvas_queue_draining") ? "生成队列正在清理取消任务，请稍后重试。" : message); } }
    finally { runInflight.current.delete(nodeId); if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) setRunningNodeId(undefined); }
  }

  async function cancelRun(runId: string) {
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current; const version = editVersion.current;
    try { const workspace = await cancelCreativeCanvasRun(project, runId, canvasScope(canvasId)); if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, version, false); }
    catch (reason) { if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) setError(reason instanceof Error ? reason.message : "无法请求停止生成。"); }
  }

  async function runText(nodeId: string) {
    if (textRunInflight.current.has(nodeId) || activeTextRun(nodeId)) return;
    textRunInflight.current.add(nodeId);
    const submittedNode = document.nodes.find(node => node.id === nodeId);
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    try {
      const saved = await save();
      if (!saved.saved) { if (saved.changedDuringSave) setError("保存期间文本提示词已更新，请先保存当前草稿后再生成。"); return; }
      if (!alive.current || projectRef.current !== project || canvasRef.current !== canvasId || canvasSession.current !== session) return;
      const idempotencyKey = crypto.randomUUID();
      const workspace = await runCreativeCanvasTextNode(project, nodeId, saved.revision, idempotencyKey, canvasScope(canvasId), submittedNode?.params?.count ?? 1);
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, editVersion.current, false);
    } catch (reason) {
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) {
        const message = reason instanceof Error ? reason.message : "无法提交文本生成。";
        setError(message.includes("text_generation_unconfigured") ? "当前没有已验证的文本生成服务或模型，请继续手写编辑。" : message);
      }
    } finally { textRunInflight.current.delete(nodeId); }
  }

  async function cancelTextRun(runId: string) {
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    try {
      const workspace = await cancelCreativeCanvasTextRun(project, runId, canvasScope(canvasId));
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, editVersion.current, false);
    } catch (reason) { if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) setError(reason instanceof Error ? reason.message : "无法请求停止文本生成。"); }
  }

  async function installTextVersion(runId: string, versionId: string) {
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    try {
      const workspace = await installCreativeCanvasTextVersion(project, runId, versionId, canvasScope(canvasId));
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, editVersion.current, false);
    } catch (reason) { if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) setError(reason instanceof Error ? reason.message : "无法将文本版本加入素材库。"); }
  }

  async function selectTextVersion(runId: string, versionId: string) {
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current; const before = documentRef.current;
    const saved = await save();
    if (!saved.saved || !alive.current || projectRef.current !== project || canvasRef.current !== canvasId || canvasSession.current !== session) return;
    const version = editVersion.current;
    try {
      const workspace = await selectCreativeCanvasTextVersion(project, runId, versionId, saved.revision, canvasScope(canvasId));
      if (!alive.current || projectRef.current !== project || canvasRef.current !== canvasId || canvasSession.current !== session) return;
      setTextRuns(workspace.textRuns ?? []); setAssets(workspace.assets);
      setDocument(current => {
        if (version === editVersion.current) return workspace.document;
        const updated = workspace.document.nodes.find(node => node.id === workspace.textRuns?.find(run => run.id === runId)?.nodeId);
        const previous = before.nodes.find(node => node.id === updated?.id);
        const target = current.nodes.find(node => node.id === updated?.id);
        const canApply = updated && target && target.text === previous?.text && target.currentTextVersionId === previous?.currentTextVersionId;
        const merged = { ...current, revision: workspace.document.revision, nodes: current.nodes.map(node => canApply && node.id === updated.id ? { ...node, text: updated.text, currentTextVersionId: updated.currentTextVersionId } : node) };
        return canApply ? markDownstreamStale(merged, new Set([updated.id])) : merged;
      });
    } catch (reason) { if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) setError(reason instanceof Error ? reason.message : "无法采用文本版本。"); }
  }

  async function refreshRun(runItem: CreativeCanvasRun) {
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    const version = editVersion.current;
    try {
      const workspace = await refreshCreativeCanvasRun(project, runItem.id, canvasScope(canvasId));
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, version, false);
    } catch (reason) {
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) setError(reason instanceof Error ? reason.message : "无法刷新云端运行状态。");
    }
  }

  async function outputAction(action: "select" | "delete" | "retry" | "cancel", runId: string, resultId: string) {
    if (resultActionLock.current || loading) return;
    resultActionLock.current = true; setResultActionBusy(true); setError("");
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    const currentSession = () => alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session;
    try {
      const saved = action === "cancel" ? { saved: true, revision: documentRef.current.revision } : await save();
      if (!saved.saved || !currentSession()) return;
      const before = documentRef.current;
      const version = editVersion.current;
      const request = action === "select" ? selectCreativeCanvasOutput : action === "delete" ? deleteCreativeCanvasOutput : retryCreativeCanvasOutput;
      const workspace = action === "cancel" ? await cancelCreativeCanvasOutput(project, runId, resultId, canvasScope(canvasId)) : await request(project, runId, resultId, saved.revision, canvasScope(canvasId));
      if (!currentSession()) return;
      if (action === "cancel") {
        applyResponse(workspace, version, false);
        const cancelled = workspace.runs.find(item => item.id === runId)?.outputItems?.find(item => item.resultId === resultId);
        setNotice(cancelled?.localDisposition === "queued_cancelled" && cancelled.providerDisposition === "not_submitted"
          ? "候选已取消，未发送给供应商。"
          : "已请求停止该候选；请以供应商确认状态为准。");
        return;
      }
      setRuns(workspace.runs); setAssets(workspace.assets); setBatches(workspace.batches ?? []);
      setDocument(current => {
        if (!currentSession()) return current;
        if (version === editVersion.current) return workspace.document;
        return { ...current, revision: workspace.document.revision, nodes: current.nodes.map(node => {
          const old = before.nodes.find(item => item.id === node.id);
          const next = workspace.document.nodes.find(item => item.id === node.id);
          if (!next || !old || old.outputAssetId !== node.outputAssetId || JSON.stringify(old.currentOutputRef) !== JSON.stringify(node.currentOutputRef)) return node;
          return { ...node, outputAssetId: next.outputAssetId, currentOutputRef: next.currentOutputRef, stale: next.stale };
        }) };
      });
      setNotice(action === "select" ? "已设为当前输出；下游历史保留。" : action === "delete" ? "候选已移除；历史素材和运行快照保留。" : "已提交该结果的重试。");
    } catch (reason) { if (currentSession()) setError(reason instanceof Error ? reason.message : "无法更新候选结果。"); }
    finally { resultActionLock.current = false; if (currentSession()) setResultActionBusy(false); }
  }

  async function runBatch(ids: string[]) {
    if (resultActionLock.current || loading) return;
    const selectedIds = groupSubtreeIds(documentRef.current, ids);
    const nodeIds = documentRef.current.nodes.filter(node => node.kind === "generate" && selectedIds.has(node.id)).map(node => node.id);
    if (!nodeIds.length) { setNotice("请先选择要运行的生成节点。"); return; }
    resultActionLock.current = true; setResultActionBusy(true); setError("");
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    const currentSession = () => alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session;
    try {
      const saved = await save();
      if (!saved.saved || !currentSession()) return;
      const fingerprint = JSON.stringify([project, canvasId, saved.revision, [...nodeIds].sort()]);
      if (pendingBatchKey.current?.fingerprint !== fingerprint) pendingBatchKey.current = { fingerprint, key: crypto.randomUUID() };
      const workspace = await runCreativeCanvasBatch(project, nodeIds, saved.revision, pendingBatchKey.current.key, canvasScope(canvasId));
      pendingBatchKey.current = undefined;
      if (!currentSession()) return;
      applyResponse(workspace, editVersion.current, false); setHistoryOpen(true); setContextMenu(undefined); setNotice("批次已提交，可在生成历史查看每一步。");
    } catch (reason) { if (currentSession()) setError(reason instanceof Error ? reason.message : "无法提交批量生成。"); }
    finally { resultActionLock.current = false; if (currentSession()) setResultActionBusy(false); }
  }

  async function cancelBatch(batchId: string) {
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    try {
      const workspace = await cancelCreativeCanvasBatch(project, batchId, canvasScope(canvasId));
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) applyResponse(workspace, editVersion.current, false);
    } catch (reason) { if (alive.current && canvasSession.current === session) setError(reason instanceof Error ? reason.message : "无法请求停止批次。"); }
  }

  const onNodesChange = (changes: NodeChange<CanvasFlowNode>[]) => {
    const dimensionChanges = changes.filter(change => change.type === "dimensions" && change.dimensions);
    if (dimensionChanges.length) setNodeMeasurements(current => {
      const next = { ...current };
      dimensionChanges.forEach(change => { if (change.type === "dimensions" && change.dimensions) next[change.id] = { ...change.dimensions }; });
      return next;
    });
    const selectionChanges = changes.filter(change => change.type === "select");
    if (selectionChanges.length) setSelectedNodeIds(current => {
      const next = new Set(current);
      selectionChanges.forEach(change => { if (change.type === "select") change.selected ? next.add(change.id) : next.delete(change.id); });
      return next;
    });
    const removedIds = changes.filter(change => change.type === "remove").map(change => change.id);
    if (removedIds.length) setNodeMeasurements(current => {
      const next = { ...current };
      groupSubtreeIds(documentRef.current, removedIds).forEach(id => delete next[id]);
      return next;
    });
    const isResizeCompensationChange = (id: string) => {
      if (groupResizeOrigins.current.has(id)) return true;
      const parentId = documentRef.current.nodes.find(node => node.id === id)?.parentId ?? documentRef.current.groups?.find(group => group.id === id)?.parentId;
      return Boolean(parentId && groupResizeOrigins.current.has(parentId));
    };
    const groupDimensions = new Map(dimensionChanges.flatMap(change => {
      if (change.type !== "dimensions" || !change.dimensions) return [];
      const group = documentRef.current.groups?.find(item => item.id === change.id);
      return group && !groupResizeOrigins.current.has(group.id) && (change.dimensions.width !== group.width || change.dimensions.height !== group.height) ? [[group.id, change.dimensions] as const] : [];
    }));
    const hasPersistedPosition = changes.some(change => change.type === "position" && change.position && !isResizeCompensationChange(change.id));
    if (!removedIds.length && !hasPersistedPosition && !groupDimensions.size) return;
    setDocument(current => {
      const removed = groupSubtreeIds(current, removedIds);
      const positions = new Map(changes.flatMap(change => change.type === "position" && change.position && !isResizeCompensationChange(change.id) ? [[change.id, change.position] as const] : []));
      const nodes = current.nodes.filter(node => !removed.has(node.id)).map(node => positions.has(node.id) ? { ...node, position: positions.get(node.id)! } : node);
      const groups = current.groups?.filter(group => !removed.has(group.id)).map(group => {
        const dimensions = groupDimensions.get(group.id);
        return positions.has(group.id) || dimensions ? { ...group, ...(positions.has(group.id) ? { position: positions.get(group.id)! } : {}), ...(dimensions ? { width: dimensions.width ?? group.width, height: dimensions.height ?? group.height } : {}) } : group;
      });
      const edges = current.edges.filter(edge => !removed.has(edge.source) && !removed.has(edge.target));
      return { ...current, nodes: pruneReferenceOrders(nodes, edges, current.edges), groups, edges };
    });
    markDirty(hasPersistedPosition ? "drag" : "edit");
  };
  const onEdgesChange = (changes: EdgeChange<Edge>[]) => { const selectionChanges = changes.filter(change => change.type === "select"); if (selectionChanges.length) setSelectedEdgeIds(current => { const next = new Set(current); selectionChanges.forEach(change => { if (change.type === "select") change.selected ? next.add(change.id) : next.delete(change.id); }); return next; }); if (!changes.some(change => change.type === "remove")) return; setDocument(current => { const removed = new Set(changes.filter(change => change.type === "remove").map(change => change.id)); const edges = current.edges.filter(edge => !removed.has(edge.id)); return { ...current, edges, nodes: pruneReferenceOrders(current.nodes, edges, current.edges) }; }); markDirty(); };
  const onConnect = (connection: Connection) => { if (loading) return; if (!isCreativeCanvasConnectionValid(connection, document, assets)) { setError("此连线无效：提示词只能接文本，图片输入只能接项目图片或历史结果；重复边、自环和循环会被拒绝。"); return; } const edge = { id: `edge-${crypto.randomUUID()}`, source: connection.source!, target: connection.target!, sourceHandle: connection.sourceHandle as "text" | "image", targetHandle: connection.targetHandle as "prompt" | "image" }; setDocument(current => ({ ...current, edges: [...current.edges, edge], nodes: current.nodes.map(node => node.id === edge.target ? { ...node, referenceOrder: [...orderReferenceEdges(current.edges.filter(item => item.target === node.id), node.referenceOrder).map(item => item.id), edge.id] } : node) })); markDirty(); };
  const onMoveEnd = (_event: MouseEvent | TouchEvent | null, viewport: Viewport) => { setZoom(viewport.zoom); if (JSON.stringify(documentRef.current.viewport) === JSON.stringify(viewport)) return; setDocument(current => ({ ...current, viewport })); markDirty(); };
  const fitCanvas = () => { void flowRef.current?.fitView({ padding: 0.2, maxZoom: 1 }); };
  const applyZoom = (nextZoom: number) => {
    const bounded = Math.min(8, Math.max(0.25, nextZoom));
    setZoom(bounded);
    void flowRef.current?.zoomTo(bounded);
  };
  const arrangeCanvas = () => {
    if (!documentRef.current.nodes.length && !documentRef.current.groups?.length) return;
    const stage = stageRef.current;
    setDocument(current => arrangeCanvasDocument(current, nodeMeasurements as Record<string, { width: number; height: number }>, { width: stage?.clientWidth ?? 0, height: stage?.clientHeight ?? 0 }));
    markDirty();
    window.setTimeout(fitCanvas, 0);
  };
  const onNodeDragStart = () => { previousHistoryKind.current = "edit"; };
  const onNodeDragStop = (_event: unknown, node?: { id: string }, nodes?: Array<{ id: string }>) => {
    const draggedIds = (nodes?.length ? nodes : node ? [node] : []).map(item => item.id);
    if (!draggedIds.length) { previousHistoryKind.current = "edit"; return; }
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    if (dragFinishTimer.current) window.clearTimeout(dragFinishTimer.current);
    dragFinishTimer.current = window.setTimeout(() => {
      dragFinishTimer.current = undefined;
      if (!alive.current || !activeRef.current || projectRef.current !== project || canvasRef.current !== canvasId || canvasSession.current !== session) { previousHistoryKind.current = "edit"; return; }
      const current = documentRef.current;
      const next = finishCanvasGroupDrag(current, draggedIds, nodeMeasurements as Record<string, { width: number; height: number }>);
      previousHistoryKind.current = "edit";
      if (JSON.stringify(next) === JSON.stringify(current)) return;
      setDocument(next); markDirty("drag");
    }, 0);
  };
  const upload = async (file: File | undefined) => {
    if (!file || uploading || loading) return;
    if (inputRef.current) inputRef.current.value = "";
    const project = projectId; const canvasId = canvasRef.current; const session = canvasSession.current;
    const replacing = replaceNodeId;
    const target = replacing ? documentRef.current.nodes.find(node => node.id === replacing) : undefined;
    const expectedKind = target?.kind === "generate" ? "image" : assets.find(asset => asset.id === target?.assetId)?.kind;
    if (expectedKind && !file.type.startsWith(`${expectedKind}/`)) { setError(`请选择${expectedKind === "image" ? "图片" : expectedKind === "video" ? "视频" : "音频"}文件。`); return; }
    if (!file.size || file.size > 100 * 1024 * 1024) { setError("素材不能为空且不能超过 100 MB。"); return; }
    setUploading(true);
    try {
      const workspace = await uploadPreproductionAsset(project, "reference", file);
      if (!alive.current || projectRef.current !== project || canvasRef.current !== canvasId || canvasSession.current !== session) return;
      const next = workspace.assets.map(asset => ({ ...asset })); setAssets(next);
      const added = next.find(asset => asset.id === workspace.uploadedAssetId);
      if (!added) throw new Error("上传成功，但无法定位新素材，请在素材库中查看。");
      if (expectedKind && added.kind !== expectedKind) throw new Error("上传文件的实际媒体类型不匹配，原节点已保留。");
      if (replacing) {
        const currentTarget = documentRef.current.nodes.find(node => node.id === replacing);
        if (!currentTarget) { setNotice("素材已入库；原节点已移除。"); return; }
        if (currentTarget.kind !== target?.kind || currentTarget.assetId !== target?.assetId || currentTarget.outputAssetId !== target?.outputAssetId) { setNotice("素材已入库；节点的当前素材已发生变化，未覆盖新选择。"); return; }
        setDocument(current => {
          if (!alive.current || projectRef.current !== project || canvasRef.current !== canvasId || canvasSession.current !== session) return current;
          const selected = current.nodes.find(node => node.id === replacing);
          if (!selected || selected.kind !== target?.kind || selected.assetId !== target?.assetId || selected.outputAssetId !== target?.outputAssetId) return current;
          return { ...current, nodes: current.nodes.map(node => node.id === replacing ? node.kind === "media" ? { ...node, assetId: added.id } : { ...node, outputAssetId: added.id, currentOutputRef: undefined } : node) };
        });
        setUploadedReplacement({ nodeId: replacing, assetId: added.id, session });
      } else add("media", added, uploadPosition);
    } catch (reason) {
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) setError(reason instanceof Error ? reason.message : "无法上传素材。");
    } finally {
      if (alive.current && projectRef.current === project && canvasRef.current === canvasId && canvasSession.current === session) { setUploading(false); setReplaceNodeId(undefined); setUploadPosition(undefined); if (inputRef.current) inputRef.current.value = ""; }
    }
  };
  const branch = (runItem: CreativeCanvasRun, assetId: string) => { if (loading) return; const asset = assets.find(item => item.id === assetId); if (!isImage(asset)) return; const media = newNode(documentRef.current, "media", asset); const generated = newNode({ ...documentRef.current, nodes: [...documentRef.current.nodes, media] }, "generate"); const edge = { id: `edge-${crypto.randomUUID()}`, source: media.id, target: generated.id, sourceHandle: "image" as const, targetHandle: "image" as const }; generated.referenceOrder = [edge.id]; setSelectedId(generated.id); setSelectedNodeIds(new Set([generated.id])); setDocument(current => ({ ...current, nodes: [...current.nodes, media, generated], edges: [...current.edges, edge] })); markDirty(); setNotice(`已从 ${new Date(runItem.createdAt).toLocaleString("zh-CN")} 创建分支`); };
  const undo = () => { const previous = undoStack.current.pop(); if (!previous) return; redoStack.current.push(document); applyingHistory.current = true; setDocument({ ...previous, revision: document.revision, viewport: document.viewport }); markDirty(); setHistoryRevision(value => value + 1); };
  const redo = () => { const next = redoStack.current.pop(); if (!next) return; undoStack.current.push(document); applyingHistory.current = true; setDocument({ ...next, revision: document.revision, viewport: document.viewport }); markDirty(); setHistoryRevision(value => value + 1); };
  useEffect(() => {
    const editable = (target: EventTarget | null) => target instanceof HTMLElement && Boolean(target.closest("input,textarea,select,[contenteditable=true],[role=textbox]"));
    const keyboard = (event: KeyboardEvent) => {
      if (!active || imageEditRef.current || searchOpen || editable(event.target) || !(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      if (key === "f") { event.preventDefault(); setSearchOpen(true); return; }
      if (key === "g") { event.preventDefault(); if (event.shiftKey) ungroupSelected(); else groupSelected(); return; }
      if (key === "c" && selectedNodeIds.size) { event.preventDefault(); void copyToClipboard([...selectedNodeIds]); return; }
      if (key !== "z") return;
      event.preventDefault(); if (event.shiftKey) redo(); else undo();
    };
    const paste = (event: ClipboardEvent) => {
      if (!active || imageEditRef.current || searchOpen || editable(event.target)) return;
      const text = event.clipboardData?.getData("text/plain") ?? "";
      if (!isCanvasClipboardCommand(text)) return;
      event.preventDefault();
      void pasteFromClipboard(centerCanvasPosition(), text);
    };
    window.addEventListener("keydown", keyboard);
    window.document.addEventListener("paste", paste);
    return () => { window.removeEventListener("keydown", keyboard); window.document.removeEventListener("paste", paste); };
  }, [active, searchOpen, selectedNodeIds]);
  useLayoutEffect(() => {
    if (!contextMenu) return;
    const menu = stageRef.current?.querySelector<HTMLElement>(".creative-canvas__context-menu");
    if (menu) { menu.style.left = `${Math.max(8, Math.min(contextMenu.x, window.innerWidth - menu.offsetWidth - 8))}px`; menu.style.top = `${Math.max(8, Math.min(contextMenu.y, window.innerHeight - menu.offsetHeight - 8))}px`; }
    stageRef.current?.querySelector<HTMLButtonElement>("[role=menu] button:not(:disabled)")?.focus();
  }, [contextMenu]);
  useEffect(() => {
    if (!active) return;
    const closeMenus = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (contextMenu || quickAddOpen || canvasMenuOpen || zoomMenuOpen || helpOpen) {
        event.preventDefault(); setContextMenu(undefined); setQuickAddOpen(false); setCanvasMenuOpen(false); setZoomMenuOpen(false); setHelpOpen(false);
      }
    };
    window.addEventListener("keydown", closeMenus);
    return () => window.removeEventListener("keydown", closeMenus);
  }, [active, canvasMenuOpen, contextMenu, helpOpen, quickAddOpen, zoomMenuOpen]);

  return <section className="creative-canvas" aria-label="AI 无限画布">
    <header className="creative-canvas__topbar creative-canvas__topbar--toonflow">
      <div className="creative-canvas__identity">
        <button type="button" className="creative-canvas__back" aria-label="返回项目制作" title="返回项目制作" onClick={onBack}><ArrowLeft size={17} /></button>
        <button type="button" className="creative-canvas__back" aria-label="生成服务设置" title="生成服务设置" onClick={onOpenSettings}><Sparkles size={16} /></button>
        <div className="creative-canvas__project-menu"><button type="button" aria-expanded={canvasMenuOpen} aria-haspopup="menu" onClick={() => setCanvasMenuOpen(open => { const next = !open; if (next) void refreshCatalog(); return next; })}>{projectName}<span>{activeCanvas.name} · AI 无限画布</span><ChevronDown size={15} /></button>{canvasMenuOpen && <div role="menu" aria-label="画布菜单" className="creative-canvas__popup creative-canvas__canvas-menu"><p>当前项目画布</p><div className="creative-canvas__canvas-list">{canvases.filter(canvas => showDeletedCanvases || !canvas.deletedAt).map(canvas => canvas.deletedAt ? <div key={canvas.id} className="is-deleted"><span>{canvas.name}（已删除）</span><button type="button" onClick={() => void restoreCanvas(canvas.id)} disabled={canvasActionBusy}><RotateCcw size={14} />恢复</button></div> : renamingCanvasId === canvas.id ? <form key={canvas.id} onSubmit={event => { event.preventDefault(); void renameCanvas(canvas.id); }}><input aria-label={`重命名${canvas.name}`} value={renameCanvasName} onChange={event => setRenameCanvasName(event.target.value)} autoFocus /><button type="submit" disabled={canvasActionBusy}>确定</button><button type="button" onClick={() => setRenamingCanvasId(undefined)}>取消</button></form> : <div key={canvas.id} className={canvas.id === activeCanvasId ? "is-active" : ""}><button type="button" aria-current={canvas.id === activeCanvasId ? "page" : undefined} onClick={() => requestCanvasSwitch(canvas.id)}>{canvas.name}</button><button type="button" aria-label={`重命名${canvas.name}`} onClick={() => { setRenamingCanvasId(canvas.id); setRenameCanvasName(canvas.name); }} disabled={canvasActionBusy}><Pencil size={14} /></button>{canvas.id !== "default" && <button type="button" aria-label={`删除${canvas.name}`} onClick={() => setDeleteCanvasId(canvas.id)} disabled={canvasActionBusy}><Trash2 size={14} /></button>}</div>)}</div><form className="creative-canvas__canvas-create" onSubmit={event => { event.preventDefault(); void createCanvas(); }}><input aria-label="新画布名称" value={newCanvasName} onChange={event => setNewCanvasName(event.target.value)} placeholder="新画布名称" /><button type="submit" disabled={!newCanvasName.trim() || canvasActionBusy}><Plus size={14} />新建画布</button></form><button type="button" aria-pressed={showDeletedCanvases} onClick={() => { const next = !showDeletedCanvases; setShowDeletedCanvases(next); void refreshCatalog(next); }}>{showDeletedCanvases ? "隐藏已删除画布" : "查看已删除画布"}</button><hr /><button type="button" onClick={() => { setSearchOpen(true); setCanvasMenuOpen(false); }}>搜索节点</button><button type="button" onClick={() => { void save(); setCanvasMenuOpen(false); }}><Save size={15} />保存画布</button><button type="button" onClick={() => { setHistoryOpen(true); setCanvasMenuOpen(false); }}><History size={15} />生成历史</button></div>}</div>
      </div>
      <span role="status">{loading ? "正在读取画布…" : saving ? "正在保存…" : dirty ? "有未保存草稿" : notice || "已保存"}</span>
      <div className="creative-canvas__top-actions">
        <button type="button" aria-label="撤销" title="撤销" disabled={!undoStack.current.length} onClick={undo}><Undo2 size={16} /></button><button type="button" aria-label="重做" title="重做" disabled={!redoStack.current.length} onClick={redo}><Redo2 size={16} /></button><button type="button" aria-label="生成历史" title="生成历史" onClick={() => setHistoryOpen(open => !open)}><History size={16} /></button><button type="button" aria-label="保存画布" title="保存画布" disabled={saving || loading} onClick={() => void save()}><Save size={16} /></button>
      </div>
    </header>
    {error && <div className="creative-canvas__alert" role="alert"><span>{error}</span>{error.includes("本地草稿已保留") && !discardPending && <button type="button" onClick={() => setDiscardPending(true)}>放弃本地草稿并重新读取</button>}{discardPending && <span>这会丢弃本地草稿。<button type="button" onClick={() => { setDiscardPending(false); void load(canvasRef.current, true); }}>确认放弃</button><button type="button" onClick={() => setDiscardPending(false)}>取消</button></span>}</div>}
    {pendingCanvasId && <div className="creative-canvas__alert creative-canvas__canvas-confirm" role="alert"><span>当前画布有未保存草稿。</span><button type="button" onClick={() => void saveAndSwitch()} disabled={canvasActionBusy || saving}>保存并切换</button><button type="button" onClick={keepDraftAndSwitch} disabled={canvasActionBusy}>保留本地草稿并切换</button><button type="button" onClick={() => setPendingCanvasId(undefined)} disabled={canvasActionBusy}>取消</button></div>}
    {deleteCanvasId && <div className="creative-canvas__alert creative-canvas__canvas-confirm" role="alert"><span>删除画布会保留其数据和素材来源，可稍后恢复。</span><button type="button" onClick={() => void removeCanvas(deleteCanvasId)} disabled={canvasActionBusy}>确认删除</button><button type="button" onClick={() => setDeleteCanvasId(undefined)} disabled={canvasActionBusy}>取消</button></div>}
    <div ref={stageRef} className="creative-canvas__stage">
      <div className="creative-canvas__quick-add"><button type="button" aria-label="添加到画布" aria-expanded={quickAddOpen} aria-haspopup="menu" disabled={loading} onClick={() => setQuickAddOpen(open => !open)}><Plus size={17} />添加</button>{quickAddOpen && <div role="menu" aria-label="添加到画布" className="creative-canvas__popup creative-canvas__quick-add-menu"><button type="button" onClick={() => { add("text"); setQuickAddOpen(false); }}><Plus size={15} />新增文本</button><button type="button" onClick={() => { add("generate"); setQuickAddOpen(false); }}><ImagePlus size={15} />新增图片生成</button><button type="button" onClick={() => { setReplaceNodeId(undefined); inputRef.current?.click(); setQuickAddOpen(false); }}><Upload size={15} />上传素材</button><button type="button" onClick={() => { setAssetPickerOpen(true); setQuickAddOpen(false); }}><PanelsTopLeft size={15} />选择项目素材</button></div>}</div>
      {!loading && <ReactFlow nodes={flowNodes} edges={flowEdges} nodeTypes={{ ...creativeCanvasNodeTypes, canvasGroup: CreativeCanvasGroupNode } as typeof creativeCanvasNodeTypes} onInit={instance => { flowRef.current = instance; setZoom(instance.getViewport().zoom); }} onPaneClick={() => { setContextMenu(undefined); setQuickAddOpen(false); setSelectedId(undefined); setSelectedNodeIds(new Set()); setInspectorOpen(false); }} onPaneContextMenu={event => openContextMenu(event, selectedNodeIds.size ? "selection" : "pane", [...selectedNodeIds])} onConnect={onConnect} onNodeClick={(event, node) => { const appendSelection = event.shiftKey || event.metaKey || event.ctrlKey; const wasSelected = selectedNodeIds.has(node.id); const next = appendSelection ? new Set(selectedNodeIds) : new Set<string>(); if (appendSelection && wasSelected) next.delete(node.id); else next.add(node.id); setContextMenu(undefined); setSelectedId(appendSelection && wasSelected ? undefined : node.id); setSelectedNodeIds(next); }} onNodeContextMenu={(event, node) => { const ids = selectedNodeIds.has(node.id) ? [...selectedNodeIds] : [node.id]; setSelectedId(node.id); setSelectedNodeIds(new Set(ids)); openContextMenu(event, ids.length > 1 ? "selection" : "node", ids); }} onNodeDragStart={onNodeDragStart} onNodeDragStop={onNodeDragStop} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onMoveEnd={onMoveEnd} snapToGrid={snapEnabled} snapGrid={[16, 16]} minZoom={0.25} maxZoom={8} defaultViewport={document.viewport} deleteKeyCode={imageEdit || searchOpen ? null : ["Backspace", "Delete"]} aria-label="AI 无限画布" proOptions={{ hideAttribution: true }}><Background id="creative-canvas-grid" gap={16} size={1} color="#dcdfe6" />{showMap && <MiniMap pannable zoomable className={generateEditorOpen ? "is-obscured" : undefined} aria-label="画布缩略图" />}</ReactFlow>}
      <div className="creative-canvas__view-controls" aria-label="画布视图控制">
        <button type="button" aria-label="素材库" title="素材库" onClick={onOpenAssets}><PanelsTopLeft size={17} /></button><button type="button" aria-label="整理画布" title="整理画布" disabled={!document.nodes.length} onClick={arrangeCanvas}><Network size={17} /></button><button type="button" aria-label="显示或隐藏地图" title={showMap ? "隐藏地图" : "显示地图"} aria-pressed={showMap} onClick={() => setShowMap(value => !value)}><MapIcon size={17} /></button><button type="button" aria-label="网格吸附" title={snapEnabled ? "关闭网格吸附" : "开启网格吸附"} aria-pressed={snapEnabled} onClick={() => setSnapEnabled(value => !value)}><Magnet size={17} /></button><button type="button" aria-label="显示或隐藏连线" title={showEdges ? "隐藏连线" : "显示连线"} aria-pressed={showEdges} onClick={() => setShowEdges(value => !value)}><EyeOff size={17} /></button><button type="button" aria-label="适应视图" title="适应视图" onClick={fitCanvas}><Focus size={17} /></button><div className="creative-canvas__zoom"><button type="button" aria-label="缩放菜单" aria-expanded={zoomMenuOpen} aria-haspopup="menu" onClick={() => setZoomMenuOpen(open => !open)}>{Math.round(zoom * 100)}%</button>{zoomMenuOpen && <div role="menu" aria-label="缩放菜单" className="creative-canvas__popup creative-canvas__zoom-menu"><label>缩放百分比<input aria-label="缩放百分比" type="number" min="25" max="800" value={Math.round(zoom * 100)} onChange={event => applyZoom(Number(event.target.value) / 100)} /></label><button type="button" onClick={() => applyZoom(zoom + .1)}><ZoomIn size={15} />放大</button><button type="button" onClick={() => applyZoom(zoom - .1)}><ZoomOut size={15} />缩小</button><button type="button" onClick={fitCanvas}><Focus size={15} />适合屏幕</button><p>当前画布缩放范围为 25%–800%。</p></div>}</div><div className="creative-canvas__help"><button type="button" aria-label="帮助" aria-expanded={helpOpen} aria-haspopup="menu" onClick={() => setHelpOpen(open => !open)}><CircleHelp size={17} /></button>{helpOpen && <div role="menu" aria-label="画布帮助" className="creative-canvas__popup creative-canvas__help-menu"><p>右键空白处可添加节点；右键节点可复制、预览或删除。</p><p>⌘/Ctrl+F 搜索节点，⌘/Ctrl+C 复制选中节点，⌘/Ctrl+Z 撤销，Esc 关闭菜单。</p></div>}</div>
      </div>
      <input ref={inputRef} className="creative-canvas__upload-input" type="file" accept="image/*,video/*,audio/*" aria-label="上传项目素材" onChange={event => void upload(event.target.files?.[0])} />
      {groupToolbarVisible && <div className="creative-canvas__group-toolbar" aria-label="选区分组操作" style={groupToolbarStyle}><span>选区</span>{selectedNodeIds.size > 1 && <button type="button" onClick={() => groupSelected()}>打组</button>}{selectedGroupIds.length > 1 && <button type="button" onClick={() => mergeSelectedGroups()}>合并分组</button>}{selectedGroupIds.length > 0 && <button type="button" onClick={() => ungroupSelected()}>解组</button>}</div>}
      {contextMenu?.kind === "node" && <div role="menu" aria-label="节点操作" className="creative-canvas__context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onContextMenu={event => event.preventDefault()} onMouseDown={event => event.stopPropagation()}>{!documentRef.current.groups?.some(group => group.id === contextMenu.ids[0]) && <button type="button" onClick={() => { openNodeInfo(contextMenu.ids[0] ?? ""); setContextMenu(undefined); }}><Info size={15} />节点信息</button>}<button type="button" onClick={() => { copyNodes(contextMenu.ids); setContextMenu(undefined); }}><Copy size={15} />复制节点</button><button type="button" disabled={resultActionBusy} onClick={() => void runBatch(contextMenu.ids)}><Sparkles size={15} />运行选中生成节点</button><button type="button" onClick={() => { duplicateNodes(contextMenu.ids); setContextMenu(undefined); }}><Copy size={15} />创建副本</button><button type="button" onClick={() => { void copyToClipboard(contextMenu.ids); setContextMenu(undefined); }}><Clipboard size={15} />复制到剪贴板</button>{documentRef.current.groups?.some(group => group.id === contextMenu.ids[0]) && <button type="button" onClick={() => { ungroupSelected(contextMenu.ids); setContextMenu(undefined); }}>解组</button>}{documentRef.current.nodes.find(node => node.id === contextMenu.ids[0])?.text && <button type="button" onClick={() => { copyPrompt(documentRef.current.nodes.find(node => node.id === contextMenu.ids[0])?.text ?? ""); setContextMenu(undefined); }}><Copy size={15} />复制提示词</button>}{nodeCurrentAsset(contextMenu.ids[0] ?? "") && <><button type="button" onClick={() => openImageEdit(contextMenu.ids[0], "crop")}>裁切图片</button><button type="button" onClick={() => openImageEdit(contextMenu.ids[0], "split")}>切分图片</button><button type="button" onClick={() => { const asset = nodeCurrentAsset(contextMenu.ids[0] ?? ""); if (asset) branchAsset(asset, contextMenu.ids[0] ?? ""); setContextMenu(undefined); }}><ImagePlus size={15} />继续创作</button><button type="button" onClick={() => { const asset = nodeCurrentAsset(contextMenu.ids[0] ?? ""); if (asset) downloadAsset(asset); setContextMenu(undefined); }}><Download size={15} />下载</button><button type="button" onClick={() => { const asset = nodeCurrentAsset(contextMenu.ids[0] ?? ""); if (asset) { previewTrigger.current = window.document.activeElement as HTMLElement | null; setPreviewAsset(asset); } setContextMenu(undefined); }}><Maximize2 size={15} />查看预览</button></>}<hr /><button type="button" className="is-danger" onClick={() => { deleteNodes(contextMenu.ids); setContextMenu(undefined); }}><Trash2 size={15} />删除节点</button></div>}
      {contextMenu?.kind === "selection" && <div role="menu" aria-label="选区操作" className="creative-canvas__context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onContextMenu={event => event.preventDefault()} onMouseDown={event => event.stopPropagation()}><button type="button" disabled={resultActionBusy} onClick={() => void runBatch(contextMenu.ids)}><Sparkles size={15} />运行选中生成节点</button><button type="button" onClick={() => { duplicateNodes(contextMenu.ids); setContextMenu(undefined); }}><Copy size={15} />创建副本</button><button type="button" onClick={() => { void copyToClipboard(contextMenu.ids); setContextMenu(undefined); }}><Clipboard size={15} />复制到剪贴板</button>{contextMenu.ids.length > 1 && <button type="button" onClick={() => { groupSelected(contextMenu.ids); setContextMenu(undefined); }}>打组</button>}{contextMenu.ids.filter(id => documentRef.current.groups?.some(group => group.id === id)).length > 1 && <button type="button" onClick={() => { mergeSelectedGroups(contextMenu.ids); setContextMenu(undefined); }}>合并分组</button>}{contextMenu.ids.some(id => documentRef.current.groups?.some(group => group.id === id)) && <button type="button" onClick={() => { ungroupSelected(contextMenu.ids); setContextMenu(undefined); }}>解组</button>}<hr /><button type="button" className="is-danger" onClick={() => { deleteNodes(contextMenu.ids); setContextMenu(undefined); }}><Trash2 size={15} />删除选中节点</button></div>}
      {contextMenu?.kind === "pane" && <div role="menu" aria-label="画布添加与操作" className="creative-canvas__context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onContextMenu={event => event.preventDefault()} onMouseDown={event => event.stopPropagation()}><button type="button" onClick={() => { add("text", undefined, contextMenu.position); setContextMenu(undefined); }}><Plus size={15} />新增文本</button><button type="button" onClick={() => { add("generate", undefined, contextMenu.position); setContextMenu(undefined); }}><ImagePlus size={15} />新增图片生成</button><button type="button" onClick={() => { setReplaceNodeId(undefined); setUploadPosition(contextMenu.position); inputRef.current?.click(); setContextMenu(undefined); }}><Upload size={15} />上传素材</button><button type="button" onClick={() => { setAssetPickerPosition(contextMenu.position); setAssetPickerOpen(true); setContextMenu(undefined); }}><PanelsTopLeft size={15} />选择项目素材</button><hr /><button type="button" onClick={() => { void pasteFromClipboard(contextMenu.position); setContextMenu(undefined); }}><Clipboard size={15} />从剪贴板粘贴</button><hr /><button type="button" disabled={!undoStack.current.length} onClick={() => { undo(); setContextMenu(undefined); }}><Undo2 size={15} />撤销</button><button type="button" disabled={!redoStack.current.length} onClick={() => { redo(); setContextMenu(undefined); }}><Redo2 size={15} />重做</button></div>}
      {assetPickerOpen && <aside className="creative-canvas__asset-picker" aria-label="选择项目素材"><div><h2>选择项目素材</h2><button type="button" aria-label="关闭素材选择" onClick={() => setAssetPickerOpen(false)}><X size={17} /></button></div>{assets.filter(asset => asset.available !== false).map(asset => <button key={asset.id} type="button" onClick={() => asset.kind === "text" ? void addTextAsset(asset) : add("media", asset)} aria-label={`加入${asset.name}`}>{asset.kind === "image" ? <img src={asset.url} alt="" /> : asset.kind === "video" ? <Film size={32} /> : asset.kind === "text" ? <Pencil size={32} /> : <Music size={32} />}<span>{asset.name}</span></button>)}{!assets.some(asset => asset.available !== false) && <p>还没有可用素材。上传文件，或前往素材管理添加。</p>}</aside>}
      {selected && inspectorOpen && <aside className="creative-canvas__inspector" aria-label="节点属性"><div><h2>{selected.label || "节点属性"}</h2><button type="button" aria-label="关闭属性面板" onClick={() => setInspectorOpen(false)}><X size={17} /></button></div><label>名称<input value={selected.label ?? ""} onChange={event => editNode(selected.id, { label: event.target.value })} /></label><p>从工具栏打开此信息面板。</p><button type="button" className="creative-canvas__delete" onClick={() => deleteNode(selected.id)}>删除此节点</button></aside>}
      {!selected && document.nodes.length === 0 && <p className="creative-canvas__hint"><BoxSelect size={16} />右键空白处，或使用左上角“添加”开始创作。</p>}
    </div>
    {searchOpen && active && <CreativeCanvasNodeSearch nodes={document.nodes} groups={document.groups} onClose={() => setSearchOpen(false)} onSelect={id => { setSearchOpen(false); setSelectedId(id); setSelectedNodeIds(new Set([id])); void flowRef.current?.fitView({ nodes: [{ id }], padding: .4, maxZoom: 1.4, duration: 200 }); }} />}
    {imageEdit && active && <CreativeCanvasImageEditor key={imageEdit.id} open projectId={imageEdit.projectId} source={imageEdit.source} initialMode={imageEdit.mode}
      onClose={() => { if (imageEditRef.current === imageEdit) closeImageEdit(); }}
      onAssetCreated={({ asset, derivation }) => installEditedAsset(imageEdit, asset, derivation)}
      onBatchComplete={({ succeeded, failed }) => { if (imageEditRef.current === imageEdit) setNotice(failed.length ? `已加入 ${succeeded.length} 张图片，${failed.length} 张处理失败；可在编辑器重试。` : `已加入 ${succeeded.length} 张新图片，可撤销节点编辑。`); }} />}
    {previewAsset && <dialog ref={imageDialogRef} className="creative-canvas__image-dialog" aria-label={`预览 ${previewAsset.name}`} onCancel={event => { event.preventDefault(); closePreview(); }}><button data-dialog-close type="button" aria-label="关闭图片预览" onClick={closePreview}><X size={18} /></button><img src={previewAsset.url} alt={previewAsset.name} /></dialog>}
    {historyOpen && <CreativeCanvasHistory busy={resultActionBusy} onDownloadAsset={downloadAsset} onPreviewAsset={asset => { previewTrigger.current = window.document.activeElement as HTMLElement | null; setPreviewAsset(asset); }} document={document} runs={runs} assets={assets} batches={batches} onClose={() => setHistoryOpen(false)}
      onRefreshRun={runItem => void refreshRun(runItem)} onSelectOutput={(runId, resultId) => void outputAction("select", runId, resultId)}
      onDeleteOutput={(runId, resultId) => void outputAction("delete", runId, resultId)} onRetryOutput={(runId, resultId) => void outputAction("retry", runId, resultId)}
      onBranchOutput={(runId, resultId, assetId) => { const source = runs.find(run => run.id === runId); if (source && canvasRunOutputItems(source).some(item => item.resultId === resultId && item.assetId === assetId && item.status === "installed")) branch(source, assetId); }}
      onCancelOutput={(runId, resultId) => void outputAction("cancel", runId, resultId)} onCancelBatch={batchId => void cancelBatch(batchId)} />}

  </section>;
}
