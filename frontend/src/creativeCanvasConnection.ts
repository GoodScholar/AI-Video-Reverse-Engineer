/*
 * Adapted from Toonflow connection.ts, commit d86cce6b689916ab73f75bc4c1f42a2a7424516c.
 * Source: https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/connection.ts
 * Licensed under MIT; see vendor/Toonflow/LICENSE.
 */
export type CanvasDataType = "text" | "image" | "*" | (string & {});
export type CanvasHandleDataType = CanvasDataType | CanvasDataType[];

// Direct adaptation of Toonflow's pure compatibility functions. The canvas
// still passes only its text/image ports at the call site.
export function getDataTypes(dataType: CanvasHandleDataType) {
  const types = Array.isArray(dataType) ? dataType : [dataType];
  return types.every(type => typeof type === "string")
    ? types.flatMap(type => type.split(",")).map(type => type.trim()).filter(Boolean)
    : [];
}

export function isTypeCompatible(sourceType: CanvasHandleDataType, targetType: CanvasHandleDataType) {
  const sourceTypes = getDataTypes(sourceType);
  const targetTypes = getDataTypes(targetType);
  return sourceTypes.length > 0 && targetTypes.length > 0
    && (sourceTypes.includes("*") || targetTypes.includes("*") || sourceTypes.some(type => targetTypes.includes(type)));
}

/**
 * Adapted from Toonflow's `useNodeReferences` ordering model (MIT,
 * d86cce6b689916ab73f75bc4c1f42a2a7424516c).  References are edges, so the
 * persisted order remains stable when a source node label or text changes.
 */
export function orderReferenceEdges<T extends { id: string }>(edges: T[], referenceOrder?: string[]) {
  const order = new Map(referenceOrder?.map((id, index) => [id, index]));
  return [...edges].sort((left, right) => (order.get(left.id) ?? order.size) - (order.get(right.id) ?? order.size));
}

export function graphWouldCycle(edges: Array<{ source: string; target: string }>, source: string, target: string) {
  const outgoing = new Map<string, string[]>();
  edges.forEach(edge => outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]));
  const todo = [target];
  const seen = new Set<string>();
  while (todo.length) {
    const node = todo.pop()!;
    if (node === source) return true;
    if (seen.has(node)) continue;
    seen.add(node);
    todo.push(...(outgoing.get(node) ?? []));
  }
  return false;
}
