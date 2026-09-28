import { memo, useCallback, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  type NodeProps,
} from "@xyflow/react";
import { Box, ChevronLeft, Focus, Image, Layers3 } from "lucide-react";
import { useStore } from "zustand";

import "@xyflow/react/dist/style.css";

import type { PreproductionWorkspaceStore } from "./preproductionWorkspaceStore";
import { projectWorkflowCanvas, type WorkflowCanvasNode } from "./workflowCanvasProjection";

type Props = { store: PreproductionWorkspaceStore };
type CanvasNodeData = WorkflowCanvasNode["data"] & { onOpenShot?: (shotId: string) => void };
type CanvasNode = Node<CanvasNodeData, WorkflowCanvasNode["kind"]>;

export function shouldHandleCanvasShortcut(target: EventTarget | null) {
  return !(target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement
    || (target instanceof HTMLElement && target.isContentEditable));
}

const SceneNode = memo(function SceneNode({ data }: NodeProps<CanvasNode>) {
  return <section className="workflow-canvas-node workflow-canvas-node--scene" aria-label={`场景 ${data.label}`}>
    <div><Layers3 size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.shotCount} 个镜头 · {data.duration} 秒{data.issueCount ? ` · ${data.issueCount} 项问题` : ""}</small>
  </section>;
});

const ShotNode = memo(function ShotNode({ data, selected }: NodeProps<CanvasNode>) {
  return <article className={`workflow-canvas-node workflow-canvas-node--shot${selected ? " is-selected" : ""}`} aria-label={`制作镜头 ${data.label}`}>
    <div><Box size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.detail} · {data.status}</small>
    <button type="button" aria-label={`查看${data.label}关系`} onClick={() => data.onOpenShot?.(data.entityId)}>查看关系</button>
  </article>;
});

const AssetNode = memo(function AssetNode({ data, selected }: NodeProps<CanvasNode>) {
  return <article className={`workflow-canvas-node workflow-canvas-node--asset${selected ? " is-selected" : ""}`} aria-label={`素材 ${data.label}`}>
    <Handle id="asset" type="source" position={Position.Right} isConnectable={false} className="workflow-canvas__handle" aria-hidden="true" />
    <div><Image size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.detail}{data.status ? ` · ${data.status}` : ""}</small>
  </article>;
});

const ProcessNode = memo(function ProcessNode({ data, selected }: NodeProps<CanvasNode>) {
  return <article className={`workflow-canvas-node workflow-canvas-node--process${selected ? " is-selected" : ""}`} aria-label={`流程节点 ${data.label}`}>
    <Handle id="input" type="target" position={Position.Left} isConnectable={false} className="workflow-canvas__handle" aria-hidden="true" />
    <Handle id="output" type="source" position={Position.Right} isConnectable={false} className="workflow-canvas__handle" aria-hidden="true" />
    <div><Focus size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.detail} · <span className={`workflow-canvas__status workflow-canvas__status--${data.statusTone ?? "neutral"}`}>{data.status}</span></small>
  </article>;
});

const nodeTypes = { scene: SceneNode, shot: ShotNode, asset: AssetNode, process: ProcessNode };

function WorkflowCanvasInner({ store }: Props) {
  const entities = useStore(store, (state) => state.entities);
  const order = useStore(store, (state) => state.order);
  const assets = useStore(store, (state) => state.assets);
  const checks = useStore(store, (state) => state.checks);
  const nodeCatalog = useStore(store, (state) => state.nodeCatalog);
  const layout = useStore(store, (state) => state.layout);
  const scope = useStore(store, (state) => state.view.scope);
  const primaryEntity = useStore(store, (state) => state.selection.primaryEntity);
  const { fitView } = useReactFlow<CanvasNode, Edge>();
  const prefersReducedMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  const graph = useMemo(() => projectWorkflowCanvas({
    ...store.getState(), entities, order, assets, checks, nodeCatalog, layout,
  }, scope), [assets, checks, entities, layout, nodeCatalog, order, scope, store]);

  const openShot = useCallback((shotId: string) => {
    store.getState().actions.selectShot(shotId);
    store.getState().actions.focusShot(shotId);
  }, [store]);

  const nodes = useMemo<CanvasNode[]>(() => graph.nodes.map((node) => ({
    id: node.id,
    type: node.kind,
    position: node.position,
    parentId: node.parentId,
    extent: node.parentId ? "parent" : undefined,
    draggable: false,
    selectable: node.kind !== "scene",
    selected: node.kind === "process"
      ? primaryEntity?.type === "processNode" && primaryEntity.id === node.data.entityId
      : primaryEntity?.type === node.kind && primaryEntity.id === node.data.entityId,
    width: node.width,
    height: node.height,
    style: { width: node.width, height: node.height },
    data: { ...node.data, onOpenShot: node.kind === "shot" ? openShot : undefined },
  })), [graph.nodes, openShot, primaryEntity]);

  const edges = useMemo<Edge[]>(() => graph.edges.map((edge) => ({
    ...edge,
    type: "smoothstep",
    focusable: true,
    selectable: false,
    markerEnd: { type: MarkerType.ArrowClosed },
    ariaLabel: `${edge.label}：${edge.source} 到 ${edge.target}`,
  })), [graph.edges]);

  const fitCurrent = useCallback((targetNodes?: CanvasNode[]) => {
    void fitView({ nodes: targetNodes, padding: 0.18, duration: prefersReducedMotion ? 0 : 220 });
  }, [fitView, prefersReducedMotion]);

  const focusSelection = useCallback(() => {
    const selected = nodes.filter((node) => {
      if (!primaryEntity) return false;
      if (primaryEntity.type === "shot") return node.type === "shot" && node.data.entityId === primaryEntity.id;
      if (primaryEntity.type === "scene") return node.type === "scene" && node.data.entityId === primaryEntity.id;
      return node.type === "process" && node.data.entityId === primaryEntity.id;
    });
    fitCurrent(selected.length ? selected : undefined);
  }, [fitCurrent, nodes, primaryEntity]);

  const fitMeasuredNodes = useCallback((changes: NodeChange<CanvasNode>[]) => {
    if (!changes.some((change) => change.type === "dimensions")) return;
    requestAnimationFrame(() => fitCurrent());
  }, [fitCurrent]);

  const selectNode: NodeMouseHandler<CanvasNode> = useCallback((_event, node) => {
    if (node.type === "shot") store.getState().actions.selectShot(node.data.entityId);
    if (node.type === "scene") store.getState().actions.selectScene(node.data.entityId);
  }, [store]);

  const scopeShot = scope.type === "shot" ? entities.shotsById[scope.id] : null;
  return <section
    className="workflow-canvas"
    aria-label="镜头关系图"
    role="region"
    tabIndex={0}
    onKeyDown={(event) => {
      if (!shouldHandleCanvasShortcut(event.target)) return;
      if ((event.metaKey || event.ctrlKey) && event.key === "0") {
        event.preventDefault();
        fitCurrent();
      } else if (!event.metaKey && !event.ctrlKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        focusSelection();
      }
    }}
  >
    <header className="workflow-canvas__toolbar">
      <nav aria-label="画布范围">
        {scope.type === "shot" && <button type="button" aria-label="返回项目范围" onClick={() => store.getState().actions.focusProject()}><ChevronLeft size={16} aria-hidden="true" />项目</button>}
        <span aria-current="page">{scopeShot?.title ?? "项目关系"}</span>
      </nav>
      <div>
        <button type="button" onClick={focusSelection}><Focus size={16} aria-hidden="true" />聚焦选择</button>
        <button type="button" onClick={() => fitCurrent()}><Layers3 size={16} aria-hidden="true" />适配当前范围</button>
      </div>
    </header>
    <div className="workflow-canvas__surface">
      <ReactFlow<CanvasNode, Edge>
        key={scope.type === "project" ? "project" : `shot:${scope.id}`}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        onNodesChange={fitMeasuredNodes}
        onNodeClick={selectNode}
        deleteKeyCode={null}
        selectionKeyCode={null}
        panActivationKeyCode="Space"
        panOnDrag={[1, 2]}
        panOnScroll
        zoomOnScroll={false}
        zoomOnPinch
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        aria-label="只读镜头关系画布"
      >
        <Background color="#dce1e9" gap={24} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
      {nodes.length === 0 && <div className="workflow-canvas__empty"><Layers3 size={24} aria-hidden="true" /><strong>{scope.type === "project" ? "还没有可查看的镜头" : "这个镜头还没有素材或流程关系"}</strong><span>先在镜头检查器中准备内容，关系会自动出现在这里。</span></div>}
    </div>
  </section>;
}

export function WorkflowCanvas(props: Props) {
  return <ReactFlowProvider><WorkflowCanvasInner {...props} /></ReactFlowProvider>;
}
