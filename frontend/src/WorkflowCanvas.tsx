import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
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

import type { PreproductionWorkspaceStore, SelectableEntity } from "./preproductionWorkspaceStore";
import { projectWorkflowCanvas, type WorkflowCanvasNode } from "./workflowCanvasProjection";

type Props = { store: PreproductionWorkspaceStore };
type CanvasNodeData = WorkflowCanvasNode["data"] & {
  store: PreproductionWorkspaceStore;
  onOpenShot?: (shotId: string) => void;
};
type CanvasNode = Node<CanvasNodeData, WorkflowCanvasNode["kind"]>;

export function shouldHandleCanvasShortcut(target: EventTarget | null) {
  return !(target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement
    || (target instanceof HTMLElement && target.isContentEditable));
}

function canvasEntity(kind: WorkflowCanvasNode["kind"], data: CanvasNodeData): SelectableEntity | null {
  if (kind === "asset") return null;
  if (kind === "process") return data.ownerShotId
    ? { type: "processNode", id: data.entityId, shotId: data.ownerShotId }
    : null;
  return { type: kind, id: data.entityId };
}

function sameEntity(current: SelectableEntity | null, target: SelectableEntity | null) {
  return Boolean(current && target && current.type === target.type && current.id === target.id
    && (current.type !== "processNode" || target.type !== "processNode" || current.shotId === target.shotId));
}

function useCanvasNodeState(kind: WorkflowCanvasNode["kind"], data: CanvasNodeData) {
  const entity = canvasEntity(kind, data);
  const selected = useStore(data.store, (state) => kind === "shot"
    ? state.selection.selectedShotIds.has(data.entityId)
    : sameEntity(state.selection.primaryEntity, entity));
  const hovered = useStore(data.store, (state) => sameEntity(state.selection.hoveredEntity, entity));
  const focused = useStore(data.store, (state) => sameEntity(state.selection.focusedEntity, entity));
  const setTransient = (type: "hover" | "focus", active: boolean) => {
    if (type === "hover") data.store.getState().actions.setHoveredEntity(active ? entity : null);
    else data.store.getState().actions.setFocusedEntity(active ? entity : null);
  };
  return { selected, hovered, focused, setTransient };
}

const SceneNode = memo(function SceneNode({ data, selected: boxSelected }: NodeProps<CanvasNode>) {
  const { selected, hovered, focused, setTransient } = useCanvasNodeState("scene", data);
  return <section className={`workflow-canvas-node workflow-canvas-node--scene${selected || boxSelected ? " is-selected" : ""}${hovered ? " is-hovered" : ""}${focused ? " is-focused" : ""}`} aria-label={`场景 ${data.label}`} tabIndex={0}
    onMouseEnter={() => setTransient("hover", true)} onMouseLeave={() => setTransient("hover", false)} onFocus={() => setTransient("focus", true)} onBlur={() => setTransient("focus", false)}>
    <div><Layers3 size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.shotCount} 个镜头 · {data.duration} 秒{data.issueCount ? ` · ${data.issueCount} 项问题` : ""}</small>
  </section>;
});

const ShotNode = memo(function ShotNode({ data, selected: boxSelected }: NodeProps<CanvasNode>) {
  const { selected, hovered, focused, setTransient } = useCanvasNodeState("shot", data);
  return <article className={`workflow-canvas-node workflow-canvas-node--shot${selected || boxSelected ? " is-selected" : ""}${hovered ? " is-hovered" : ""}${focused ? " is-focused" : ""}`} aria-label={`制作镜头 ${data.label}`} tabIndex={0}
    onMouseEnter={() => setTransient("hover", true)} onMouseLeave={() => setTransient("hover", false)} onFocus={() => setTransient("focus", true)} onBlur={() => setTransient("focus", false)}>
    <div><Box size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.detail} · {data.status}</small>
    <button type="button" aria-label={`查看${data.label}关系`} onClick={(event) => { event.stopPropagation(); data.onOpenShot?.(data.entityId); }}>查看关系</button>
  </article>;
});

const AssetNode = memo(function AssetNode({ data, selected }: NodeProps<CanvasNode>) {
  return <article className={`workflow-canvas-node workflow-canvas-node--asset${selected ? " is-selected" : ""}`} aria-label={`素材 ${data.label}`}>
    <Handle id="asset" type="source" position={Position.Right} isConnectable={false} className="workflow-canvas__handle" aria-hidden="true" />
    <div><Image size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.detail}{data.status ? ` · ${data.status}` : ""}</small>
  </article>;
});

const ProcessNode = memo(function ProcessNode({ data, selected: boxSelected }: NodeProps<CanvasNode>) {
  const { selected, hovered, focused, setTransient } = useCanvasNodeState("process", data);
  return <article className={`workflow-canvas-node workflow-canvas-node--process${selected || boxSelected ? " is-selected" : ""}${hovered ? " is-hovered" : ""}${focused ? " is-focused" : ""}`} aria-label={`流程节点 ${data.label}`} tabIndex={0}
    onMouseEnter={() => setTransient("hover", true)} onMouseLeave={() => setTransient("hover", false)} onFocus={() => setTransient("focus", true)} onBlur={() => setTransient("focus", false)}>
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
  const locateRequest = useStore(store, (state) => state.view.locateRequest);
  const { fitView } = useReactFlow<CanvasNode, Edge>();
  const boxSelectedShotIds = useRef<string[]>([]);
  const boxSelecting = useRef(false);
  const prefersReducedMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  const graph = useMemo(() => projectWorkflowCanvas({
    ...store.getState(), entities, order, assets, checks, nodeCatalog, layout,
  }, scope), [assets, checks, entities, layout, nodeCatalog, order, scope, store]);

  const openShot = useCallback((shotId: string) => {
    store.getState().actions.selectShot(shotId, { source: "canvas" });
    store.getState().actions.focusShot(shotId);
  }, [store]);

  const nodes = useMemo<CanvasNode[]>(() => graph.nodes.map((node) => ({
    id: node.id,
    type: node.kind,
    position: node.position,
    parentId: node.parentId,
    extent: node.parentId ? "parent" : undefined,
    draggable: false,
    selectable: node.kind !== "asset",
    width: node.width,
    height: node.height,
    style: { width: node.width, height: node.height },
    data: {
      ...node.data,
      store,
      onOpenShot: node.kind === "shot" ? openShot : undefined,
    },
  })), [graph.nodes, openShot, store]);

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
    const primaryEntity = store.getState().selection.primaryEntity;
    const selected = nodes.filter((node) => {
      if (!primaryEntity) return false;
      if (primaryEntity.type === "shot") return node.type === "shot" && node.data.entityId === primaryEntity.id;
      if (primaryEntity.type === "scene") return node.type === "scene" && node.data.entityId === primaryEntity.id;
      return node.type === "process" && node.data.entityId === primaryEntity.id && node.data.ownerShotId === primaryEntity.shotId;
    });
    fitCurrent(selected.length ? selected : undefined);
  }, [fitCurrent, nodes, store]);

  const fitMeasuredNodes = useCallback((changes: NodeChange<CanvasNode>[]) => {
    if (!changes.some((change) => change.type === "dimensions")) return;
    requestAnimationFrame(() => fitCurrent());
  }, [fitCurrent]);

  useEffect(() => {
    if (!locateRequest || locateRequest.source === "canvas") return;
    const target = nodes.find((node) => {
      if (locateRequest.entity.type === "processNode") return node.type === "process"
        && node.data.entityId === locateRequest.entity.id
        && node.data.ownerShotId === locateRequest.entity.shotId;
      return node.type === locateRequest.entity.type && node.data.entityId === locateRequest.entity.id;
    });
    if (target) fitCurrent([target]);
  }, [fitCurrent, locateRequest, nodes]);

  const selectNode: NodeMouseHandler<CanvasNode> = useCallback((event, node) => {
    if (node.type === "shot") store.getState().actions.selectShot(node.data.entityId, {
      mode: event.metaKey || event.ctrlKey ? "toggle" : "replace",
      source: "canvas",
    });
    if (node.type === "scene") store.getState().actions.selectScene(node.data.entityId, "canvas");
    if (node.type === "process" && node.data.ownerShotId) store.getState().actions.selectNode(node.data.ownerShotId, node.data.entityId, "canvas");
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
        nodesFocusable={false}
        elementsSelectable
        onNodesChange={fitMeasuredNodes}
        onNodeClick={selectNode}
        onSelectionStart={() => { boxSelecting.current = true; boxSelectedShotIds.current = []; }}
        onSelectionChange={({ nodes: selectedNodes }) => {
          if (boxSelecting.current) boxSelectedShotIds.current = selectedNodes.filter((node) => node.type === "shot").map((node) => node.data.entityId);
        }}
        onSelectionEnd={() => {
          if (!boxSelecting.current) return;
          boxSelecting.current = false;
          store.getState().actions.selectShots(boxSelectedShotIds.current, "canvas");
        }}
        deleteKeyCode={null}
        selectionKeyCode={null}
        multiSelectionKeyCode={["Meta", "Control"]}
        selectionOnDrag={scope.type === "project"}
        selectionMode={SelectionMode.Partial}
        panActivationKeyCode="Space"
        panOnDrag={scope.type === "project" ? [2] : [1, 2]}
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
