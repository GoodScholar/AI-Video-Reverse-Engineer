/*
 * Adapted from Toonflow-app/apps/web/src/pages/workspace/panels/canvas/nodeClipboard.ts
 * and packages/nodeScaffold/src/nodeSkeleton.vue at d86cce6b689916ab73f75bc4c1f42a2a7424516c.
 * MIT License, Copyright 2026 HBAI-Ltd; see vendor/Toonflow/LICENSE.
 * React/XYFlow adaptation adds serializable subgraphs and project-asset validation.
 */
import type { CreativeCanvasAsset, CreativeCanvasDocument, CreativeCanvasEdge, CreativeCanvasGroup, CreativeCanvasNode } from "./creativeCanvasApi";
import { absoluteCanvasGroupPosition, absoluteCanvasNodePosition } from "./creativeCanvasGeometry";
import { graphWouldCycle, orderReferenceEdges } from "./creativeCanvasConnection";

export type CanvasClipboardSnapshot = {
  version: 1;
  sourceProjectId: string;
  nodes: CreativeCanvasNode[];
  edges: CreativeCanvasEdge[];
  groups?: CreativeCanvasGroup[];
};

type Position = { x: number; y: number };
type IdFactory = () => string;

const clipboardPrefix = "ai-video-reverse-engineer:creative-canvas:v1:";
const clipboardCommand = new RegExp(`^${clipboardPrefix}[0-9a-f-]{8,}$`, "i");
const storageKey = "ai-video-reverse-engineer.creativeCanvasClipboard.v1";
let memoryClipboard: { command: string; snapshot: CanvasClipboardSnapshot } | undefined;
let memoryOnlyCommand: string | undefined;
const maxDimension = 8192;
const maxPixels = 64_000_000;

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }
function id(): string { return crypto.randomUUID(); }
function fail(message: string): never { throw new Error(message); }
function isRecord(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function isPosition(value: unknown): value is Position { return isRecord(value) && typeof value.x === "number" && Number.isFinite(value.x) && typeof value.y === "number" && Number.isFinite(value.y); }
function isNodeKind(value: unknown): value is CreativeCanvasNode["kind"] { return value === "text" || value === "media" || value === "generate"; }
function isHandle(value: unknown): value is CreativeCanvasEdge["sourceHandle"] { return value === "text" || value === "image"; }
function isTargetHandle(value: unknown): value is CreativeCanvasEdge["targetHandle"] { return value === "prompt" || value === "image" || value === "in"; }
function isSafeStorageId(value: unknown, maxLength = 100) { return typeof value === "string" && value.length > 0 && value.length <= maxLength && value !== "." && value !== ".." && !/[\\/\0]/.test(value); }
function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]) { return Object.keys(value).every(key => allowed.includes(key)); }
function isOptionalString(value: unknown, min: number, max: number) { return value === undefined || typeof value === "string" && value.length >= min && value.length <= max; }
function isOptionalNullableString(value: unknown, min: number, max: number) { return value === undefined || value === null || typeof value === "string" && value.length >= min && value.length <= max; }
function isBoundedInteger(value: unknown, min: number, max: number) { return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max; }
function isBoundedNumber(value: unknown, min: number, max: number) { return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max; }

function validParams(value: unknown) {
  if (!isRecord(value) || !hasOnlyKeys(value, ["negativePrompt", "width", "height", "model", "providerId", "modelId", "configurationVersion", "count", "size", "ratio", "steps", "seed", "denoise"])
    || !isOptionalString(value.negativePrompt, 0, 12_000) || !isOptionalNullableString(value.model, 1, 256)
    || !isOptionalNullableString(value.providerId, 1, 64) || !isOptionalNullableString(value.modelId, 1, 256) || !isOptionalNullableString(value.configurationVersion, 1, 128)
    || Boolean(value.providerId) !== Boolean(value.modelId) || value.configurationVersion != null && !value.providerId
    || value.count !== undefined && !isBoundedInteger(value.count, 1, 15)
    || !isOptionalNullableString(value.size, 3, 32) || !isOptionalNullableString(value.ratio, 3, 32)
    || value.width !== undefined && !isBoundedInteger(value.width, 64, maxDimension)
    || value.height !== undefined && !isBoundedInteger(value.height, 64, maxDimension)
    || value.steps !== undefined && !isBoundedInteger(value.steps, 1, 150)
    || value.seed !== undefined && !isBoundedInteger(value.seed, -1, 2_147_483_647)
    || value.denoise !== undefined && !isBoundedNumber(value.denoise, 0, 1))
    return false;
  const params = value as Record<string, unknown>;
  let width = typeof params.width === "number" ? params.width : 1024;
  let height = typeof params.height === "number" ? params.height : 1024;
  if (typeof params.size === "string") {
    const match = /^([1-9][0-9]{1,3})x([1-9][0-9]{1,3})$/.exec(params.size);
    if (!match) return false;
    width = Number(match[1]); height = Number(match[2]);
  }
  if (typeof value.ratio === "string") {
    const ratio = /^([1-9][0-9]{0,3}):([1-9][0-9]{0,3})$/.exec(value.ratio);
    if (!ratio || width * Number(ratio[2]) !== height * Number(ratio[1])) return false;
  }
  return width >= 64 && height >= 64 && width <= maxDimension && height <= maxDimension && width * height <= maxPixels;
}

function validOutputRef(value: unknown) {
  return isRecord(value) && hasOnlyKeys(value, ["runId", "resultId"]) && [value.runId, value.resultId].every(id => typeof id === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(id));
}

function assertSnapshot(snapshot: unknown): asserts snapshot is CanvasClipboardSnapshot {
  if (!isRecord(snapshot) || snapshot.version !== 1 || typeof snapshot.sourceProjectId !== "string" || !snapshot.sourceProjectId
    || !Array.isArray(snapshot.nodes) || !Array.isArray(snapshot.edges) || snapshot.groups !== undefined && !Array.isArray(snapshot.groups)
    || !(snapshot.nodes.length || (Array.isArray(snapshot.groups) && snapshot.groups.length)) || snapshot.nodes.length > 500 || snapshot.edges.length > 1000)
    fail("剪贴板数据格式错误，请重新复制。");
  const ids = new Set<string>();
  const nodes = snapshot.nodes as unknown[];
  for (const node of nodes) {
    if (!isRecord(node) || !hasOnlyKeys(node, ["id", "kind", "position", "label", "text", "assetId", "outputAssetId", "currentOutputRef", "stale", "referenceOrder", "params", "parentId", "width", "height", "freeResize", "prompt", "providerId", "modelId", "configurationVersion", "currentTextVersionId", "fontSize"])
      || !isSafeStorageId(node.id) || ids.has(node.id as string) || !isNodeKind(node.kind) || !isPosition(node.position) || Math.abs(node.position.x) > 1_000_000 || Math.abs(node.position.y) > 1_000_000
      || node.label !== undefined && (typeof node.label !== "string" || node.label.length > 1_000) || node.text !== undefined && (typeof node.text !== "string" || node.text.length > 12_000)
      || node.assetId !== undefined && !isSafeStorageId(node.assetId) || node.outputAssetId !== undefined && !isSafeStorageId(node.outputAssetId)
      || node.width != null && !isBoundedNumber(node.width, 64, 8192) || node.height != null && !isBoundedNumber(node.height, 64, 8192) || node.freeResize != null && typeof node.freeResize !== "boolean" || node.parentId != null && !isSafeStorageId(node.parentId)
      || node.params !== undefined && !validParams(node.params)
      || node.stale != null && (node.kind !== "generate" || typeof node.stale !== "boolean")
      || node.currentOutputRef != null && (node.kind !== "generate" || !validOutputRef(node.currentOutputRef))
      || node.kind === "text" && (typeof node.text !== "string" || node.assetId !== undefined || node.outputAssetId !== undefined || node.params !== undefined
        || !isOptionalString(node.prompt, 0, 12_000) || !isOptionalNullableString(node.providerId, 1, 64) || !isOptionalNullableString(node.modelId, 1, 256)
        || !isOptionalNullableString(node.configurationVersion, 1, 128) || Boolean(node.providerId) !== Boolean(node.modelId) || node.configurationVersion != null && !node.providerId
        || node.currentTextVersionId !== undefined && !isSafeStorageId(node.currentTextVersionId) || node.fontSize !== undefined && (typeof node.fontSize !== "number" || !isBoundedInteger(node.fontSize, 10, 32) || node.fontSize % 2 !== 0))
      || node.kind !== "text" && (node.prompt !== undefined || node.providerId !== undefined || node.modelId !== undefined || node.configurationVersion !== undefined || node.currentTextVersionId !== undefined || node.fontSize !== undefined)
      || node.kind === "media" && (typeof node.assetId !== "string" || node.text !== undefined || node.outputAssetId !== undefined || node.referenceOrder !== undefined)
      || node.kind === "generate" && node.assetId !== undefined)
      fail("剪贴板数据格式错误，请重新复制。");
    ids.add(node.id as string);
  }
  const rawGroups = (snapshot.groups ?? []) as unknown[];
  if (rawGroups.length > 500) fail("分组数量过多。");
  const groups = new Map<string, CreativeCanvasGroup>();
  for (const group of rawGroups) {
    if (!isRecord(group) || !hasOnlyKeys(group, ["id", "label", "parentId", "position", "width", "height"]) || !isSafeStorageId(group.id) || ids.has(group.id as string) || groups.has(group.id as string) || !isOptionalString(group.label, 0, 1000) || group.parentId != null && !isSafeStorageId(group.parentId) || !isPosition(group.position) || Math.abs(group.position.x) > 1_000_000 || Math.abs(group.position.y) > 1_000_000 || !isBoundedNumber(group.width, 64, 8192) || !isBoundedNumber(group.height, 64, 8192)) fail("剪贴板分组数据格式错误。");
    groups.set(group.id as string, group as unknown as CreativeCanvasGroup);
  }
  for (const item of [...nodes, ...rawGroups] as Array<CreativeCanvasNode | CreativeCanvasGroup>) {
    const visited = new Set([item.id]); let parent = item.parentId;
    while (parent) { if (visited.has(parent) || !groups.has(parent)) fail("剪贴板分组引用无效。"); visited.add(parent); parent = groups.get(parent)!.parentId; }
  }
  const typedNodes = snapshot.nodes as CreativeCanvasNode[];
  const byId = new Map(typedNodes.map(node => [node.id, node]));
  const edgeIds = new Set<string>();
  const rawEdges = snapshot.edges as unknown[];
  for (const rawEdge of rawEdges) {
    if (!isRecord(rawEdge) || !hasOnlyKeys(rawEdge, ["id", "source", "target", "sourceHandle", "targetHandle"]) || !isSafeStorageId(rawEdge.id) || edgeIds.has(rawEdge.id as string) || !isSafeStorageId(rawEdge.source) || !isSafeStorageId(rawEdge.target)
      || !isHandle(rawEdge.sourceHandle) || !isTargetHandle(rawEdge.targetHandle))
      fail("剪贴板数据格式错误，请重新复制。");
    edgeIds.add(rawEdge.id as string);
  }
  const typedEdges = snapshot.edges as CreativeCanvasEdge[];
  for (const edge of typedEdges) {
    const source = byId.get(edge.source); const target = byId.get(edge.target);
    const validPort = (source?.kind === "text" && edge.sourceHandle === "text" && target?.kind === "generate" && edge.targetHandle === "prompt")
      || ((source?.kind === "media" || source?.kind === "generate") && edge.sourceHandle === "image" && target?.kind === "generate" && edge.targetHandle === "image")
      || (source?.kind === "text" && edge.sourceHandle === "text" && target?.kind === "text" && edge.targetHandle === "in")
      || ((source?.kind === "media" || source?.kind === "generate") && edge.sourceHandle === "image" && target?.kind === "text" && edge.targetHandle === "in");
    if (!source || !target || edge.source === edge.target || !validPort
      || typedEdges.some(other => other !== edge && other.source === edge.source && other.target === edge.target && other.sourceHandle === edge.sourceHandle && other.targetHandle === edge.targetHandle)
      || graphWouldCycle(typedEdges.filter(other => other !== edge), edge.source, edge.target))
      fail("剪贴板数据格式错误，请重新复制。");
  }
  for (const node of typedNodes) {
    if ((node.kind !== "generate" && node.kind !== "text") || node.referenceOrder === undefined) continue;
    const referenceOrder = node.referenceOrder as unknown[];
    const clipboardEdges = typedEdges;
    if (!Array.isArray(referenceOrder) || referenceOrder.some(edgeId => typeof edgeId !== "string") || new Set(referenceOrder).size !== referenceOrder.length
      || referenceOrder.some(edgeId => !edgeIds.has(edgeId as string) || clipboardEdges.find(edge => edge.id === edgeId)?.target !== node.id))
      fail("剪贴板数据格式错误，请重新复制。");
  }
}

function selected(document: CreativeCanvasDocument, ids: readonly string[]) {
  const all = [...document.nodes, ...(document.groups ?? [])];
  const byId = new Map(all.map(item => [item.id, item]));
  const selectedIds = new Set(ids);
  if (!selectedIds.size || ids.some(id => !byId.has(id))) fail("请选择有效节点后再复制。");
  for (const item of all) { let parent = item.parentId; const visited = new Set<string>(); while (parent && !visited.has(parent)) { if (selectedIds.has(parent)) { selectedIds.add(item.id); break; } visited.add(parent); parent = byId.get(parent)?.parentId; } }
  const nodes = document.nodes.filter(node => selectedIds.has(node.id));
  const groups = (document.groups ?? []).filter(group => selectedIds.has(group.id));
  const roots = [...nodes, ...groups].filter(item => !item.parentId || !selectedIds.has(item.parentId));
  return { selectedIds, nodes, groups, roots };
}

function internalEdges(document: CreativeCanvasDocument, selectedIds: Set<string>) {
  return document.edges.filter(edge => selectedIds.has(edge.source) && selectedIds.has(edge.target));
}

function assetReferences(nodes: CreativeCanvasNode[]) {
  return nodes.flatMap(node => [node.assetId, node.outputAssetId]).filter((assetId): assetId is string => typeof assetId === "string" && Boolean(assetId));
}

function assertAssetsAvailable(nodes: CreativeCanvasNode[], assets: CreativeCanvasAsset[]) {
  const available = new Set(assets.filter(asset => asset.available !== false).map(asset => asset.id));
  if (assetReferences(nodes).some(assetId => !available.has(assetId))) fail("引用素材不可用，无法粘贴到当前项目。");
}

function anchor(nodes: Array<{ position: Position }>) {
  return nodes.reduce((current, node) => ({ x: Math.min(current.x, node.position.x), y: Math.min(current.y, node.position.y) }), { x: Infinity, y: Infinity });
}

function remapNode(node: CreativeCanvasNode, nodeIds: Map<string, string>, edgeIds: Map<string, string>, position?: Position, suffix?: string): CreativeCanvasNode {
  const copy = clone(node);
  copy.id = nodeIds.get(node.id)!;
  // Runs belong to the source node. A copy retains the immutable asset, not a
  // current-output pointer whose run would belong to a different node/canvas.
  if (copy.currentOutputRef && !copy.outputAssetId) fail("当前结果尚未解析为素材，请刷新后再复制。");
  delete copy.currentOutputRef;
  // Keep the selected text, but its version belongs to the original node.
  delete copy.currentTextVersionId;
  if (position) copy.position = position;
  if (suffix) copy.label = `${copy.label || "节点"}${suffix}`;
  if (copy.referenceOrder) copy.referenceOrder = copy.referenceOrder.flatMap(edgeId => edgeIds.get(edgeId) ?? []);
  return copy;
}

function remapEdges(edges: CreativeCanvasEdge[], nodeIds: Map<string, string>, edgeIds: Map<string, string>, retainExternalSources: boolean) {
  return edges.flatMap(edge => {
    const target = nodeIds.get(edge.target);
    const source = nodeIds.get(edge.source) ?? (retainExternalSources ? edge.source : undefined);
    if (!source || !target) return [];
    return [{ ...clone(edge), id: edgeIds.get(edge.id)!, source, target }];
  });
}

function remapReferenceTokens(text: string | undefined, previous: string[], next: string[]) {
  return (text ?? "").replace(/\{\{ref\s+(\d+)\}\}/g, (_token, value: string) => {
    const edgeId = previous[Number(value) - 1];
    const index = edgeId ? next.indexOf(edgeId) : -1;
    return index < 0 ? "" : `{{ref ${index + 1}}}`;
  });
}

function snapshotNodes(nodes: CreativeCanvasNode[], edges: CreativeCanvasEdge[], document: CreativeCanvasDocument) {
  return nodes.map(node => {
    const copy = clone(node);
    if (copy.kind !== "generate" && copy.kind !== "text") return copy;
    const all = orderReferenceEdges(document.edges.filter(edge => edge.target === node.id), node.referenceOrder).map(edge => edge.id);
    const previous = all;
    const next = previous.filter(edgeId => edges.some(edge => edge.id === edgeId));
    copy.referenceOrder = next.length ? next : undefined;
    if (copy.kind === "generate") copy.text = remapReferenceTokens(copy.text, previous, next);
    else copy.prompt = remapReferenceTokens(copy.prompt, previous, next);
    return copy;
  });
}

export function createCanvasClipboardSnapshot(sourceProjectId: string, document: CreativeCanvasDocument, ids: readonly string[]): CanvasClipboardSnapshot {
  if (!sourceProjectId) fail("请先打开项目后再复制。");
  const { selectedIds, nodes, groups } = selected(document, ids);
  const edges = internalEdges(document, selectedIds);
  const copiedNodes = snapshotNodes(nodes, edges, document).map(node => node.parentId && !selectedIds.has(node.parentId) ? { ...node, parentId: undefined, position: absoluteCanvasNodePosition(document, node.id) } : node);
  const copiedGroups = groups.map(group => group.parentId && !selectedIds.has(group.parentId) ? { ...clone(group), parentId: undefined, position: absoluteCanvasGroupPosition(document, group.id) } : clone(group));
  return { version: 1, sourceProjectId, nodes: copiedNodes, edges: clone(edges), ...(copiedGroups.length ? { groups: copiedGroups } : {}) };
}

/** Duplicate whole selection trees; only roots are offset. External parents stay valid locally. */
function copySelection(document: CreativeCanvasDocument, ids: readonly string[], withEdges: boolean, makeId: IdFactory) {
  const { selectedIds, nodes, groups, roots } = selected(document, ids);
  const mapping = new Map([...nodes, ...groups].map(node => [node.id, makeId()]));
  const copiedEdges = withEdges ? document.edges.filter(edge => selectedIds.has(edge.target)) : [];
  const edgeIds = new Map(copiedEdges.map(edge => [edge.id, makeId()]));
  const rootIds = new Set(roots.map(item => item.id));
  const bounds = [...nodes, ...groups].map(item => ({ ...item, position: document.nodes.some(node => node.id === item.id) ? absoluteCanvasNodePosition(document, item.id) : absoluteCanvasGroupPosition(document, item.id) }));
  const origin = anchor(bounds);
  const offset = groups.length ? Math.max(...bounds.map(item => item.position.x + (item.width ?? 220))) - origin.x + 64 : 36;
  const shift = (item: CreativeCanvasNode | CreativeCanvasGroup) => rootIds.has(item.id) ? { x: item.position.x + offset, y: item.position.y + (groups.length ? 0 : 28) } : item.position;
  const copies = (withEdges ? nodes : snapshotNodes(nodes, [], document)).map(node => ({ ...remapNode(node, mapping, edgeIds, shift(node), " 副本"), parentId: mapping.get(node.parentId ?? "") ?? node.parentId }));
  const groupCopies = groups.map(group => ({ ...clone(group), id: mapping.get(group.id)!, parentId: mapping.get(group.parentId ?? "") ?? group.parentId, position: shift(group), label: `${group.label || "分组"} 副本` }));
  return { nodes: copies, groups: groupCopies, edges: remapEdges(copiedEdges, mapping, edgeIds, withEdges), selectedIds: roots.map(item => mapping.get(item.id)!), origin };
}
export function duplicateCanvasSubgraph(document: CreativeCanvasDocument, ids: readonly string[], makeId: IdFactory = id) { return copySelection(document, ids, true, makeId); }
export function copyCanvasNodes(document: CreativeCanvasDocument, ids: readonly string[], makeId: IdFactory = id) { return copySelection(document, ids, false, makeId); }

export function pasteCanvasClipboardSnapshot(document: CreativeCanvasDocument, snapshot: unknown, target: string, assets: CreativeCanvasAsset[], position: Position, makeId: IdFactory = id) {
  assertSnapshot(snapshot);
  if (!target || !isPosition(position)) fail("无法确定粘贴位置。");
  if (snapshot.sourceProjectId !== target && assetReferences(snapshot.nodes).length) fail("此节点引用项目素材或结果，不能跨项目粘贴。");
  assertAssetsAvailable(snapshot.nodes, assets);
  const all = [...snapshot.nodes, ...(snapshot.groups ?? [])];
  const mapping = new Map(all.map(node => [node.id, makeId()]));
  if (new Set(mapping.values()).size !== mapping.size || [...mapping.values()].some(nodeId => [...document.nodes, ...(document.groups ?? [])].some(node => node.id === nodeId))) fail("无法创建唯一节点身份，请重试。");
  const edgeIds = new Map(snapshot.edges.map(edge => [edge.id, makeId()]));
  if (new Set(edgeIds.values()).size !== edgeIds.size || [...edgeIds.values()].some(edgeId => document.edges.some(edge => edge.id === edgeId))) fail("无法创建唯一连线身份，请重试。");
  const roots = all.filter(item => !item.parentId);
  const origin = anchor(roots);
  const shift = (item: CreativeCanvasNode | CreativeCanvasGroup) => item.parentId ? item.position : { x: position.x + item.position.x - origin.x, y: position.y + item.position.y - origin.y };
  const nodes = snapshot.nodes.map(node => ({ ...remapNode(node, mapping, edgeIds, shift(node)), parentId: mapping.get(node.parentId ?? "") }));
  const groups = (snapshot.groups ?? []).map(group => ({ ...clone(group), id: mapping.get(group.id)!, parentId: mapping.get(group.parentId ?? ""), position: shift(group) }));
  const edges = remapEdges(snapshot.edges, mapping, edgeIds, false);
  if (document.nodes.length + nodes.length > 500 || (document.groups?.length ?? 0) + groups.length > 500 || document.edges.length + edges.length > 1000) fail("粘贴后超过画布容量，请减少节点后重试。");
  return { nodes, groups, edges, selectedIds: roots.map(node => mapping.get(node.id)!) };
}

export function saveCanvasClipboard(snapshot: CanvasClipboardSnapshot, makeId: IdFactory = id) {
  assertSnapshot(snapshot);
  const command = `${clipboardPrefix}${makeId()}`;
  memoryClipboard = { command, snapshot: clone(snapshot) };
  memoryOnlyCommand = undefined;
  try { window.localStorage.setItem(storageKey, JSON.stringify(memoryClipboard)); }
  catch {
    try { window.localStorage.removeItem(storageKey); }
    catch { memoryClipboard = undefined; memoryOnlyCommand = undefined; fail("浏览器限制了剪贴板快照存储，请重试。"); }
    memoryOnlyCommand = command;
  }
  return command;
}

export function readCanvasClipboard(command: string): CanvasClipboardSnapshot | undefined {
  if (!clipboardCommand.test(command)) return undefined;
  // A failed write leaves the prior persisted entry intact. Its command must
  // not shadow the newly issued in-page command that could not be persisted.
  if (memoryOnlyCommand === command && memoryClipboard?.command === command) {
    let persisted: string | null = null;
    try { persisted = window.localStorage.getItem(storageKey); } catch { /* Current-page memory remains readable when storage access is denied. */ }
    if (persisted !== null) fail("剪贴板数据已失效，请重新复制。");
    assertSnapshot(memoryClipboard.snapshot);
    return clone(memoryClipboard.snapshot);
  }
  let entry: { command: string; snapshot: CanvasClipboardSnapshot } | undefined;
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (raw !== null) {
      const parsed: unknown = JSON.parse(raw);
      if (!isRecord(parsed) || typeof parsed.command !== "string" || !("snapshot" in parsed)) fail("剪贴板数据格式错误，请重新复制。");
      entry = { command: parsed.command, snapshot: parsed.snapshot as CanvasClipboardSnapshot };
    }
  } catch (error) {
    if (error instanceof Error && error.message.includes("剪贴板数据格式错误")) throw error;
    if (memoryOnlyCommand === command && memoryClipboard?.command === command) entry = memoryClipboard;
    else fail("浏览器限制了剪贴板快照存储，请重新复制。");
  }
  if (!entry && memoryOnlyCommand === command && memoryClipboard?.command === command) entry = memoryClipboard;
  if (!entry || entry.command !== command) fail("剪贴板数据已失效，请重新复制。");
  assertSnapshot(entry.snapshot);
  return clone(entry.snapshot);
}

export function isCanvasClipboardCommand(value: string) { return clipboardCommand.test(value); }
