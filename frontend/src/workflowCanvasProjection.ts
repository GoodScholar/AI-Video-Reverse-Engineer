import type { WorkflowNode } from "./preproductionApi";
import type { PreproductionWorkspaceState } from "./preproductionWorkspaceStore";
import { getShotPreparationStatus } from "./shotPreparationStatus";

export type WorkflowCanvasScope = { type: "project" } | { type: "shot"; id: string };

export type WorkflowCanvasNode = {
  id: string;
  kind: "scene" | "shot" | "asset" | "process";
  position: { x: number; y: number };
  parentId?: string;
  width?: number;
  height?: number;
  data: {
    label: string;
    detail?: string;
    status?: string;
    statusTone?: "neutral" | "running" | "success" | "warning" | "error";
    entityId: string;
    shotCount?: number;
    duration?: number;
    issueCount?: number;
  };
};

export type WorkflowCanvasEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle: string;
  targetHandle: string;
  label: string;
};

export type WorkflowCanvasGraph = { nodes: WorkflowCanvasNode[]; edges: WorkflowCanvasEdge[] };

type RelationshipWorkflowNode = Extract<WorkflowNode, { type: "asset" | "process" }>;

const roleLabels = { character: "角色素材", scene: "场景素材", motion: "动作素材", audio: "音频素材", reference: "普通参考" } as const;
const statusLabels = { pending: "待处理", queued: "排队中", running: "处理中", completed: "已完成", failed: "失败", stale: "需重做" } as const;
const statusTones = { pending: "neutral", queued: "running", running: "running", completed: "success", failed: "error", stale: "warning" } as const;

function processEntityId(workflowNodeId: string, shotId: string) {
  const prefix = `process:${encodeURIComponent(shotId)}:`;
  return workflowNodeId.startsWith(prefix) ? decodeURIComponent(workflowNodeId.slice(prefix.length)) : workflowNodeId;
}

function projectGraph(state: PreproductionWorkspaceState): WorkflowCanvasGraph {
  if (!state.order.sceneIds.some((sceneId) => (state.order.shotIdsByScene[sceneId]?.length ?? 0) > 0)) return { nodes: [], edges: [] };
  const nodes: WorkflowCanvasNode[] = [];
  state.order.sceneIds.forEach((sceneId, sceneIndex) => {
    const scene = state.entities.scenesById[sceneId];
    if (!scene) return;
    const shotIds = state.order.shotIdsByScene[sceneId] ?? [];
    const savedScene = state.layout.nodes[sceneId];
    const scenePosition = { x: savedScene?.x ?? 0, y: savedScene?.y ?? sceneIndex * 280 };
    const sceneNodeId = `scene:${sceneId}`;
    nodes.push({
      id: sceneNodeId,
      kind: "scene",
      position: scenePosition,
      width: savedScene?.width ?? Math.max(320, 80 + shotIds.length * 260),
      height: savedScene?.height ?? 240,
      data: {
        label: scene.title,
        detail: scene.description,
        entityId: sceneId,
        shotCount: shotIds.length,
        duration: shotIds.reduce((total, shotId) => total + (state.entities.shotsById[shotId]?.duration ?? 0), 0),
        issueCount: state.checks.reduce((total, check) => total + Number(Boolean(check.shotId && shotIds.includes(check.shotId))), 0),
      },
    });
    shotIds.forEach((shotId, shotIndex) => {
      const shot = state.entities.shotsById[shotId];
      if (!shot) return;
      const shotNodeId = Object.values(state.entities.workflowNodesById)
        .find((node) => node.type === "shot" && node.shotId === shotId)?.id ?? `shot:${encodeURIComponent(shotId)}`;
      const savedShot = state.layout.nodes[shotNodeId];
      nodes.push({
        id: shotNodeId,
        kind: "shot",
        parentId: sceneNodeId,
        position: savedShot
          ? { x: savedShot.x - scenePosition.x, y: savedShot.y - scenePosition.y }
          : { x: 40 + shotIndex * 260, y: 80 },
        width: savedShot?.width ?? 220,
        height: savedShot?.height ?? 112,
        data: { label: shot.title, detail: `${shot.duration} 秒`, status: getShotPreparationStatus(shot), entityId: shotId },
      });
    });
  });
  return { nodes, edges: [] };
}

function shotGraph(state: PreproductionWorkspaceState, shotId: string): WorkflowCanvasGraph {
  if (!state.entities.shotsById[shotId]) return { nodes: [], edges: [] };
  const workflowNodes = Object.values(state.entities.workflowNodesById)
    .filter((node): node is RelationshipWorkflowNode => node.type !== "shot" && node.ownerShotId === shotId)
    .sort((left, right) => Number(left.type === "process") - Number(right.type === "process") || left.id.localeCompare(right.id));
  const visibleIds = new Set(workflowNodes.map((node) => node.id));
  const nodes = workflowNodes.map<WorkflowCanvasNode>((node, index) => {
    const saved = state.layout.nodes[node.id];
    if (node.type === "asset") {
      const asset = state.assets.find((item) => item.id === node.assetId);
      return {
        id: node.id,
        kind: "asset",
        position: { x: saved?.x ?? 40, y: saved?.y ?? 40 + index * 140 },
        width: saved?.width ?? 210,
        height: saved?.height ?? 96,
        data: {
          label: asset?.name ?? "缺失素材",
          detail: roleLabels[node.role ?? asset?.role ?? "reference"],
          entityId: node.assetId,
          status: asset ? undefined : "素材不可用",
        },
      };
    }
    const catalog = state.nodeCatalog.find((item) => item.kind === node.processKind);
    return {
      id: node.id,
      kind: "process",
      position: { x: saved?.x ?? 320, y: saved?.y ?? 40 + index * 140 },
      width: saved?.width ?? 220,
      height: saved?.height ?? 104,
      data: {
        label: catalog?.label ?? node.processKind,
        detail: "流程节点",
        status: statusLabels[node.status],
        statusTone: statusTones[node.status],
        entityId: processEntityId(node.id, shotId),
      },
    };
  });
  const edges = Object.values(state.entities.workflowEdgesById)
    .filter((edge) => visibleIds.has(edge.source.nodeId) && visibleIds.has(edge.target.nodeId))
    .map((edge) => ({
      id: edge.id,
      source: edge.source.nodeId,
      target: edge.target.nodeId,
      sourceHandle: edge.source.portId,
      targetHandle: edge.target.portId,
      label: "数据",
    }));
  return { nodes, edges };
}

export function projectWorkflowCanvas(state: PreproductionWorkspaceState, scope: WorkflowCanvasScope): WorkflowCanvasGraph {
  return scope.type === "project" ? projectGraph(state) : shotGraph(state, scope.id);
}
