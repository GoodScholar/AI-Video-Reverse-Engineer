/*
 * Four-corner resize follows basketikun/infinite-canvas `canvas-node.tsx`
 * (MIT) and Toonflow's parentNode grouping at d86cce6 (MIT). Group children
 * persist relative coordinates while links continue to address stable node IDs.
 */
import { graphlib, layout } from "@dagrejs/dagre";
import type { CreativeCanvasDocument, CreativeCanvasGroup, CreativeCanvasNode } from "./creativeCanvasApi";

export type ResizeCorner = "top-left" | "top-right" | "bottom-left" | "bottom-right";
export type CanvasDelta = { x: number; y: number };
export type CanvasMeasurements = Record<string, { width: number; height: number }>;

const MIN_WIDTH = 220;
const MIN_HEIGHT = 160;
const MAX_SIZE = 8192;

function clamp(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)); }
function assertGroupSize(width: number, height: number) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width > MAX_SIZE || height > MAX_SIZE) throw new Error("分组尺寸不能超过 8192px");
}
function nodeSize(node: CreativeCanvasNode) {
  return {
    width: clamp(node.width ?? MIN_WIDTH, 64, MAX_SIZE),
    height: clamp(node.height ?? MIN_HEIGHT, 64, MAX_SIZE),
  };
}

/**
 * Adapted from Toonflow arrangeCanvas.ts at d86cce6 (MIT), using its Dagre
 * 3.1.1 dependency.  Connected flows retain LR ranks; disconnected flows are
 * packed into rows instead of accumulating in one vertical lane.
 */
export function arrangeCanvasDocument(document: CreativeCanvasDocument, measurements: CanvasMeasurements = {}, canvas = { width: 0, height: 0 }) {
  const groups = document.groups ?? [];
  const topLevel = [...document.nodes.filter(node => !node.parentId), ...groups.filter(group => !group.parentId)];
  if (!topLevel.length) return document;
  const groupById = new Map(groups.map(group => [group.id, group]));
  const nodeById = new Map(document.nodes.map(node => [node.id, node]));
  const itemSize = (id: string) => {
    const group = groupById.get(id);
    if (group) return { width: group.width, height: group.height };
    const node = nodeById.get(id);
    if (!node) throw new Error("节点不存在");
    return measurements[id] ?? nodeSize(node);
  };
  const rootId = (nodeId: string) => {
    const node = nodeById.get(nodeId);
    if (!node) return undefined;
    let root = node.id;
    let parentId = node.parentId;
    const visited = new Set([root]);
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      root = parentId;
      parentId = groupById.get(parentId)?.parentId;
    }
    return parentId ? undefined : root;
  };
  const widths = topLevel.map(item => itemSize(item.id).width).sort((left, right) => left - right);
  const heights = topLevel.map(item => itemSize(item.id).height).sort((left, right) => left - right);
  const nodeGap = Math.max(96, Math.min(160, heights[Math.floor(heights.length / 2)]! / 4));
  const rankGap = Math.max(160, Math.min(320, widths[Math.floor(widths.length / 2)]! / 2));
  const groupGap = Math.max(nodeGap, rankGap) * 1.5;
  const graph = new graphlib.Graph();
  graph.setGraph({ rankdir: "LR", rankalign: "top", nodesep: nodeGap, ranksep: rankGap, edgesep: 32 });
  graph.setDefaultEdgeLabel(() => ({}));
  topLevel.forEach(item => graph.setNode(item.id, itemSize(item.id)));
  document.edges.forEach(edge => {
    const source = rootId(edge.source); const target = rootId(edge.target);
    if (source && target && source !== target) graph.setEdge(source, target);
  });
  const components = graphlib.alg.components(graph).map(ids => {
    const members = new Set(ids);
    const subgraph = graph.filterNodes(id => members.has(id));
    subgraph.setGraph({ ...graph.graph() });
    layout(subgraph);
    return { ids, graph: subgraph, width: subgraph.graph().width as number, height: subgraph.graph().height as number };
  });
  const aspectRatio = canvas.width > 0 && canvas.height > 0 ? Math.max(.75, Math.min(2, canvas.width / canvas.height)) : 1.6;
  const area = components.reduce((sum, component) => sum + (component.width + groupGap) * (component.height + groupGap), 0);
  const rowWidth = Math.max(...components.map(component => component.width), Math.sqrt(area * aspectRatio));
  const origin = { x: Math.min(...topLevel.map(item => item.position.x)), y: Math.min(...topLevel.map(item => item.position.y)) };
  let x = 0; let y = 0; let rowHeight = 0;
  const positions = new Map<string, { x: number; y: number }>();
  for (const component of components) {
    if (x > 0 && x + component.width > rowWidth) { x = 0; y += rowHeight + groupGap; rowHeight = 0; }
    component.ids.forEach(id => {
      const item = component.graph.node(id) as { x: number; y: number; width: number; height: number };
      positions.set(id, { x: origin.x + x + item.x - item.width / 2, y: origin.y + y + item.y - item.height / 2 });
    });
    x += component.width + groupGap;
    rowHeight = Math.max(rowHeight, component.height);
  }
  return {
    ...document,
    nodes: document.nodes.map(node => positions.has(node.id) ? { ...node, position: positions.get(node.id)! } : node),
    groups: groups.map(group => positions.has(group.id) ? { ...group, position: positions.get(group.id)! } : group),
  };
}

/** Return an absolute position and size while keeping the opposite corner fixed. */
export function resizeCanvasNodeFromCorner(node: CreativeCanvasNode, corner: ResizeCorner, delta: CanvasDelta) {
  const start = nodeSize(node);
  const fromLeft = corner.endsWith("left");
  const fromTop = corner.startsWith("top");
  const rawWidth = start.width + (fromLeft ? -delta.x : delta.x);
  const rawHeight = start.height + (fromTop ? -delta.y : delta.y);
  const scale = node.freeResize === true ? undefined : clamp(
    (Math.abs(delta.x) >= Math.abs(delta.y) ? rawWidth / start.width : rawHeight / start.height),
    Math.max(MIN_WIDTH / start.width, MIN_HEIGHT / start.height),
    Math.min(MAX_SIZE / start.width, MAX_SIZE / start.height),
  );
  const width = scale === undefined ? clamp(rawWidth, MIN_WIDTH, MAX_SIZE) : start.width * scale;
  const height = scale === undefined ? clamp(rawHeight, MIN_HEIGHT, MAX_SIZE) : start.height * scale;
  const right = node.position.x + start.width;
  const bottom = node.position.y + start.height;
  return {
    position: { x: fromLeft ? right - width : node.position.x, y: fromTop ? bottom - height : node.position.y },
    width, height,
  };
}

function groupsById(document: CreativeCanvasDocument) { return new Map((document.groups ?? []).map(group => [group.id, group])); }
function itemParentId(document: CreativeCanvasDocument, id: string) {
  return document.nodes.find(node => node.id === id)?.parentId ?? groupsById(document).get(id)?.parentId;
}
function selectedRoots(document: CreativeCanvasDocument, ids: readonly string[]) {
  const selected = new Set(ids);
  const existing = new Set([...document.nodes.map(node => node.id), ...(document.groups ?? []).map(group => group.id)]);
  if (!selected.size || [...selected].some(id => !existing.has(id))) throw new Error("请选择有效节点或分组");
  return [...selected].filter(id => {
    const visited = new Set([id]);
    let parentId = itemParentId(document, id);
    while (parentId && !visited.has(parentId)) {
      if (selected.has(parentId)) return false;
      visited.add(parentId);
      parentId = itemParentId(document, parentId);
    }
    return true;
  });
}
function itemRectangle(document: CreativeCanvasDocument, id: string, measurements: CanvasMeasurements) {
  const node = document.nodes.find(item => item.id === id);
  if (node) {
    const size = measurements[id] ?? nodeSize(node);
    return { ...absoluteCanvasNodePosition(document, id), width: size.width, height: size.height };
  }
  const group = groupsById(document).get(id);
  if (!group) throw new Error("节点或分组不存在");
  return { ...absoluteCanvasGroupPosition(document, id), width: group.width, height: group.height };
}
function parentAbsolutePosition(document: CreativeCanvasDocument, parentId: string | undefined) {
  return parentId ? absoluteCanvasGroupPosition(document, parentId) : { x: 0, y: 0 };
}

export function absoluteCanvasGroupPosition(document: CreativeCanvasDocument, groupId: string) {
  const groups = groupsById(document);
  const visited = new Set<string>();
  function position(id: string): CanvasDelta {
    if (visited.has(id)) throw new Error("分组不能形成循环");
    const group = groups.get(id);
    if (!group) throw new Error("分组不存在");
    visited.add(id);
    const parent = group.parentId ? position(group.parentId) : { x: 0, y: 0 };
    visited.delete(id);
    return { x: parent.x + group.position.x, y: parent.y + group.position.y };
  }
  return position(groupId);
}

export function absoluteCanvasNodePosition(document: CreativeCanvasDocument, nodeId: string) {
  const node = document.nodes.find(item => item.id === nodeId);
  if (!node) throw new Error("节点不存在");
  const parent = node.parentId ? absoluteCanvasGroupPosition(document, node.parentId) : { x: 0, y: 0 };
  return { x: parent.x + node.position.x, y: parent.y + node.position.y };
}

/**
 * Toonflow selectionNodes.finishGroupDrag adapted to persisted parentId data.
 * At pointer release it chooses the deepest intersecting group, promotes a
 * dragged item which left its parent, and then tightens every affected frame.
 */
export function finishCanvasGroupDrag(document: CreativeCanvasDocument, draggedIds: readonly string[], measurements: CanvasMeasurements = {}) {
  const nodes = document.nodes.map(node => ({ ...node, position: { ...node.position } }));
  const groups = (document.groups ?? []).map(group => ({ ...group, position: { ...group.position } }));
  if (!draggedIds.length || !groups.length) return document;
  const before: CreativeCanvasDocument = { ...document, nodes, groups };
  const byNode = new Map(nodes.map(node => [node.id, node]));
  const byGroup = new Map(groups.map(group => [group.id, group]));
  const parentId = (id: string) => byNode.get(id)?.parentId ?? byGroup.get(id)?.parentId;
  const ancestors = (id: string) => {
    const result: string[] = [];
    const visited = new Set([id]);
    let parent = parentId(id);
    while (parent && !visited.has(parent)) { visited.add(parent); result.push(parent); parent = parentId(parent); }
    return result;
  };
  const rectangle = (id: string) => {
    const group = byGroup.get(id);
    if (group) return { ...absoluteCanvasGroupPosition(before, id), width: group.width, height: group.height, depth: ancestors(id).length };
    const node = byNode.get(id);
    if (!node) throw new Error("节点或分组不存在");
    const size = measurements[id] ?? nodeSize(node);
    return { ...absoluteCanvasNodePosition(before, id), width: size.width, height: size.height, depth: ancestors(id).length };
  };
  const rectangles = new Map([...nodes, ...groups].map(item => [item.id, rectangle(item.id)]));
  const intersects = (itemId: string, groupId: string) => {
    const item = rectangles.get(itemId)!; const group = rectangles.get(groupId)!;
    return item.width > 0 && item.height > 0 && group.width > 0 && group.height > 0
      && item.x < group.x + group.width && item.x + item.width > group.x
      && item.y < group.y + group.height && item.y + item.height > group.y;
  };
  const orderedGroups = [...groups].sort((left, right) => {
    const first = rectangles.get(left.id)!; const second = rectangles.get(right.id)!;
    return second.depth - first.depth || first.width * first.height - second.width * second.height;
  });
  const affected = new Set<string>();
  const roots = selectedRoots(before, draggedIds);
  for (const id of roots) {
    const item = byNode.get(id) ?? byGroup.get(id);
    if (!item) continue;
    const currentParent = item.parentId ? byGroup.get(item.parentId) : undefined;
    ancestors(id).forEach(ancestor => affected.add(ancestor));
    const retainsParent = Boolean(currentParent && intersects(id, currentParent.id));
    const target = orderedGroups.find(group => {
      const groupAncestors = ancestors(group.id);
      return group.id !== id && intersects(id, group.id) && !groupAncestors.includes(id) && (!retainsParent || groupAncestors.includes(currentParent!.id));
    }) ?? (retainsParent ? currentParent : undefined);
    if (target?.id === currentParent?.id || (!target && !currentParent)) continue;
    const position = rectangles.get(id)!;
    const parentPosition = target ? rectangles.get(target.id)! : { x: 0, y: 0 };
    item.parentId = target?.id;
    item.position = { x: position.x - parentPosition.x, y: position.y - parentPosition.y };
    ancestors(id).forEach(ancestor => affected.add(ancestor));
    if (target) { affected.add(target.id); ancestors(target.id).forEach(ancestor => affected.add(ancestor)); }
  }
  const working = () => ({ ...document, nodes, groups });
  const groupDepth = (id: string) => ancestors(id).length;
  for (const groupId of [...affected].filter(id => byGroup.has(id)).sort((left, right) => groupDepth(right) - groupDepth(left))) {
    const group = byGroup.get(groupId)!;
    const children = [...nodes, ...groups].filter(item => item.parentId === groupId);
    if (!children.length) continue;
    const sizes = children.map(child => {
      const childGroup = byGroup.get(child.id);
      const node = byNode.get(child.id);
      return { item: child, width: childGroup?.width ?? (node ? (measurements[node.id] ?? nodeSize(node)).width : 0), height: childGroup?.height ?? (node ? (measurements[node.id] ?? nodeSize(node)).height : 0) };
    });
    if (sizes.some(size => !size.width || !size.height)) continue;
    const left = Math.min(...sizes.map(size => size.item.position.x)) - 24;
    const top = Math.min(...sizes.map(size => size.item.position.y)) - 40;
    const width = Math.max(...sizes.map(size => size.item.position.x + size.width)) - left + 24;
    const height = Math.max(...sizes.map(size => size.item.position.y + size.height)) - top + 24;
    // The canvas API rejects groups larger than 8192px.  This helper works on
    // detached copies, so rejecting the drop preserves the prior valid graph.
    if (width > MAX_SIZE || height > MAX_SIZE) return document;
    if (left || top) {
      group.position = { x: group.position.x + left, y: group.position.y + top };
      children.forEach(child => { child.position = { x: child.position.x - left, y: child.position.y - top }; });
    }
    group.width = width;
    group.height = height;
  }
  const next = working();
  return JSON.stringify([next.nodes, next.groups]) === JSON.stringify([document.nodes, document.groups]) ? document : next;
}

/** Create Toonflow-style parented group around selected roots without duplicating selected descendants. */
export function createCanvasGroup(document: CreativeCanvasDocument, ids: readonly string[], label: string, id: string, measurements: CanvasMeasurements = {}): CreativeCanvasDocument {
  if (!id || document.nodes.some(node => node.id === id) || document.groups?.some(group => group.id === id)) throw new Error("分组标识已存在");
  const roots = selectedRoots(document, ids);
  const rectangles = roots.map(rootId => itemRectangle(document, rootId, measurements));
  const bounds = rectangles.reduce((current, rect) => ({
    left: Math.min(current.left, rect.x), top: Math.min(current.top, rect.y), right: Math.max(current.right, rect.x + rect.width), bottom: Math.max(current.bottom, rect.y + rect.height),
  }), { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity });
  const parentId = roots.every(rootId => itemParentId(document, rootId) === itemParentId(document, roots[0]!)) ? itemParentId(document, roots[0]!) : undefined;
  const origin = { x: bounds.left - 24, y: bounds.top - 40 };
  const parentPosition = parentAbsolutePosition(document, parentId);
  const rootIds = new Set(roots);
  const width = bounds.right - bounds.left + 48;
  const height = bounds.bottom - bounds.top + 64;
  assertGroupSize(width, height);
  const group: CreativeCanvasGroup = { id, label: label.trim() || "分组", parentId, position: { x: origin.x - parentPosition.x, y: origin.y - parentPosition.y }, width, height };
  const nodes = document.nodes.map(node => rootIds.has(node.id)
    ? { ...node, parentId: id, position: (() => { const absolute = absoluteCanvasNodePosition(document, node.id); return { x: absolute.x - origin.x, y: absolute.y - origin.y }; })() }
    : node);
  const groups = [...(document.groups ?? []).map(existing => rootIds.has(existing.id)
    ? { ...existing, parentId: id, position: (() => { const absolute = absoluteCanvasGroupPosition(document, existing.id); return { x: absolute.x - origin.x, y: absolute.y - origin.y }; })() }
    : existing), group];
  return { ...document, nodes, groups };
}

/** Move a group in its parent's coordinate space; descendants follow through parentId. */
export function moveCanvasGroup(document: CreativeCanvasDocument, groupId: string, delta: CanvasDelta): CreativeCanvasDocument {
  const group = document.groups?.find(item => item.id === groupId);
  if (!group) throw new Error("分组不存在");
  return { ...document, groups: document.groups?.map(item => item.id === groupId
    ? { ...item, position: { x: item.position.x + delta.x, y: item.position.y + delta.y } }
    : item) };
}

/** Promote direct children into the parent space before removing a group. */
export function ungroupCanvas(document: CreativeCanvasDocument, groupId: string): CreativeCanvasDocument {
  const documentGroups = document.groups ?? [];
  const group = documentGroups.find(item => item.id === groupId);
  if (!group) throw new Error("分组不存在");
  const parentPosition = group.parentId ? absoluteCanvasGroupPosition(document, group.parentId) : { x: 0, y: 0 };
  const groupPosition = absoluteCanvasGroupPosition(document, groupId);
  const relative = (position: CanvasDelta) => ({ x: position.x - parentPosition.x, y: position.y - parentPosition.y });
  const groups = documentGroups.filter(item => item.id !== groupId).map(item => item.parentId === groupId
    ? { ...item, parentId: group.parentId, position: relative(absoluteCanvasGroupPosition(document, item.id)) }
    : item);
  const nodes = document.nodes.map(node => node.parentId === groupId
    ? { ...node, parentId: group.parentId, position: relative({ x: groupPosition.x + node.position.x, y: groupPosition.y + node.position.y }) }
    : node);
  return { ...document, nodes, groups };
}

/** Merge selected group roots like Toonflow: retain the first id and reparent direct children. */
export function mergeCanvasGroups(document: CreativeCanvasDocument, ids: readonly string[]): CreativeCanvasDocument {
  const groupIds = selectedRoots(document, ids).filter(id => groupsById(document).has(id));
  if (groupIds.length < 2) throw new Error("请至少选择两个分组");
  const selected = new Set(groupIds);
  const groups = groupsById(document);
  const rectangles = groupIds.map(id => itemRectangle(document, id, {}));
  const bounds = rectangles.reduce((current, rect) => ({
    left: Math.min(current.left, rect.x), top: Math.min(current.top, rect.y), right: Math.max(current.right, rect.x + rect.width), bottom: Math.max(current.bottom, rect.y + rect.height),
  }), { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity });
  const first = groups.get(groupIds[0]!)!;
  const parentId = groupIds.every(groupId => groups.get(groupId)?.parentId === first.parentId) ? first.parentId : undefined;
  const parentPosition = parentAbsolutePosition(document, parentId);
  const width = bounds.right - bounds.left;
  const height = bounds.bottom - bounds.top;
  assertGroupSize(width, height);
  const merged: CreativeCanvasGroup = { ...first, parentId, position: { x: bounds.left - parentPosition.x, y: bounds.top - parentPosition.y }, width, height };
  const relativeToMerged = (absolute: CanvasDelta) => ({ x: absolute.x - bounds.left, y: absolute.y - bounds.top });
  const nodes = document.nodes.map(node => node.parentId && selected.has(node.parentId)
    ? { ...node, parentId: first.id, position: relativeToMerged(absoluteCanvasNodePosition(document, node.id)) }
    : node);
  const remainingGroups = (document.groups ?? []).flatMap(group => {
    if (selected.has(group.id)) return [];
    return group.parentId && selected.has(group.parentId)
      ? [{ ...group, parentId: first.id, position: relativeToMerged(absoluteCanvasGroupPosition(document, group.id)) }]
      : [group];
  });
  return { ...document, nodes, groups: [...remainingGroups, merged] };
}
