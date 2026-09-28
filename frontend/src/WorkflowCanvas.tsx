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
  type NodeMouseHandler,
  type NodeProps,
} from "@xyflow/react";
import { Box, ChevronDown, ChevronLeft, ChevronRight, Focus, Image, Layers3, Redo2, Undo2 } from "lucide-react";
import { useStore } from "zustand";

import "@xyflow/react/dist/style.css";

import { getCanvasLayout, saveCanvasLayout, type CanvasLayout } from "./preproductionApi";
import { canvasLayoutScopeKey, type PreproductionWorkspaceStore, type SelectableEntity } from "./preproductionWorkspaceStore";
import { projectWorkflowCanvas, type WorkflowCanvasNode } from "./workflowCanvasProjection";

type Props = { store: PreproductionWorkspaceStore; projectId?: string };
type CanvasNodeData = WorkflowCanvasNode["data"] & {
  store: PreproductionWorkspaceStore;
  onOpenShot?: (shotId: string) => void;
  onToggleScene?: (sceneId: string) => void;
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
    <button type="button" className="nodrag" aria-label={`${data.collapsed ? "展开" : "折叠"}场景 ${data.label}`} onClick={(event) => {
      event.stopPropagation();
      data.onToggleScene?.(data.entityId);
    }}>{data.collapsed ? <ChevronRight size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}{data.collapsed ? "展开" : "折叠"}</button>
  </section>;
});

const ShotNode = memo(function ShotNode({ data, selected: boxSelected }: NodeProps<CanvasNode>) {
  const { selected, hovered, focused, setTransient } = useCanvasNodeState("shot", data);
  return <article className={`workflow-canvas-node workflow-canvas-node--shot${selected || boxSelected ? " is-selected" : ""}${hovered ? " is-hovered" : ""}${focused ? " is-focused" : ""}`} aria-label={`制作镜头 ${data.label}`} tabIndex={0}
    onMouseEnter={() => setTransient("hover", true)} onMouseLeave={() => setTransient("hover", false)} onFocus={() => setTransient("focus", true)} onBlur={() => setTransient("focus", false)}>
    <div><Box size={16} aria-hidden="true" /><strong>{data.label}</strong></div>
    <small>{data.detail} · {data.status}</small>
    <button type="button" className="nodrag" aria-label={`查看${data.label}关系`} onClick={(event) => { event.stopPropagation(); data.onOpenShot?.(data.entityId); }}>查看关系</button>
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
const emptyLayoutHistory: Array<CanvasLayout["nodes"]> = [];

function WorkflowCanvasInner({ store, projectId: providedProjectId }: Props) {
  const entities = useStore(store, (state) => state.entities);
  const order = useStore(store, (state) => state.order);
  const assets = useStore(store, (state) => state.assets);
  const checks = useStore(store, (state) => state.checks);
  const nodeCatalog = useStore(store, (state) => state.nodeCatalog);
  const layout = useStore(store, (state) => state.layout);
  const scope = useStore(store, (state) => state.view.scope);
  const locateRequest = useStore(store, (state) => state.view.locateRequest);
  const layoutSaveStatus = useStore(store, (state) => state.layoutPersistence.saveStatus);
  const layoutMessage = useStore(store, (state) => state.layoutPersistence.conflictMessage);
  const { fitView, setViewport } = useReactFlow<CanvasNode, Edge>();
  const boxSelectedShotIds = useRef<string[]>([]);
  const boxSelecting = useRef(false);
  const savingLayout = useRef(false);
  const restoringViewport = useRef(false);
  const prefersReducedMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const projectId = providedProjectId ?? (layout.scope.type === "project" ? layout.scope.id : "");
  const layoutScope = useMemo<CanvasLayout["scope"]>(() => scope.type === "project"
    ? { type: "project", id: projectId }
    : { type: "shot", id: scope.id }, [projectId, scope]);
  const layoutReady = canvasLayoutScopeKey(layout.scope) === canvasLayoutScopeKey(layoutScope);
  const historyByScope = useStore(store, (state) => state.layoutPersistence.historyByScope);
  const futureByScope = useStore(store, (state) => state.layoutPersistence.futureByScope);
  const layoutHistory = historyByScope[canvasLayoutScopeKey(layoutScope)] ?? emptyLayoutHistory;
  const layoutFuture = futureByScope[canvasLayoutScopeKey(layoutScope)] ?? emptyLayoutHistory;

  const graph = useMemo(() => projectWorkflowCanvas({
    ...store.getState(), entities, order, assets, checks, nodeCatalog, layout,
  }, scope), [assets, checks, entities, layout, nodeCatalog, order, scope, store]);

  const openShot = useCallback((shotId: string) => {
    store.getState().actions.selectShot(shotId, { source: "canvas" });
    store.getState().actions.focusShot(shotId);
  }, [store]);

  const persistLayout = useCallback((next: CanvasLayout | null) => {
    if (!next || !projectId || savingLayout.current) return;
    savingLayout.current = true;
    store.getState().actions.setLayoutSaveStatus("saving");
    void saveCanvasLayout(projectId, next).then((saved) => {
      const currentScope = store.getState().view.scope;
      const activeScope: CanvasLayout["scope"] = currentScope.type === "project"
        ? { type: "project", id: projectId }
        : { type: "shot", id: currentScope.id };
      store.getState().actions.acceptLayout(saved, canvasLayoutScopeKey(activeScope) === canvasLayoutScopeKey(saved.scope));
    }).catch((reason: unknown) => {
      const message = reason instanceof Error ? reason.message : "无法保存画布布局。";
      store.getState().actions.setLayoutSaveStatus(message.includes("已被更新") ? "conflict" : "error", message);
    }).finally(() => { savingLayout.current = false; });
  }, [projectId, store]);

  const reloadLayout = useCallback(() => {
    if (!projectId) return;
    store.getState().actions.setLayoutSaveStatus("saving");
    void getCanvasLayout(projectId, layoutScope).then((loaded) => {
      const currentScope = store.getState().view.scope;
      const activeScope: CanvasLayout["scope"] = currentScope.type === "project"
        ? { type: "project", id: projectId }
        : { type: "shot", id: currentScope.id };
      store.getState().actions.acceptLayout(loaded, canvasLayoutScopeKey(activeScope) === canvasLayoutScopeKey(loaded.scope));
    }).catch((reason: unknown) => {
      store.getState().actions.setLayoutSaveStatus("error", reason instanceof Error ? reason.message : "无法读取画布布局。");
    });
  }, [layoutScope, projectId, store]);

  const toggleScene = useCallback((sceneId: string) => {
    if (layoutSaveStatus === "saving") return;
    persistLayout(store.getState().actions.toggleCanvasScene(sceneId));
  }, [layoutSaveStatus, persistLayout, store]);

  const nodes = useMemo<CanvasNode[]>(() => graph.nodes.map((node) => ({
    id: node.id,
    type: node.kind,
    position: node.position,
    parentId: node.parentId,
    extent: node.parentId ? "parent" : undefined,
    selectable: node.kind !== "asset",
    width: node.width,
    height: node.height,
    style: { width: node.width, height: node.height },
    data: {
      ...node.data,
      store,
      onOpenShot: node.kind === "shot" ? openShot : undefined,
      onToggleScene: node.kind === "scene" ? toggleScene : undefined,
    },
  })), [graph.nodes, openShot, store, toggleScene]);

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

  useEffect(() => {
    if (!projectId) return;
    if (store.getState().actions.activateLayout(layoutScope)) return;
    let active = true;
    store.getState().actions.setLayoutSaveStatus("saving");
    void getCanvasLayout(projectId, layoutScope).then((loaded) => {
      if (active) store.getState().actions.acceptLayout(loaded, true);
    }).catch((reason: unknown) => {
      if (active) store.getState().actions.setLayoutSaveStatus("error", reason instanceof Error ? reason.message : "无法读取画布布局。");
    });
    return () => { active = false; };
  }, [layoutScope, projectId, store]);

  useEffect(() => {
    if (!layoutReady || !layout.viewport) return;
    restoringViewport.current = true;
    void Promise.resolve(setViewport(layout.viewport, { duration: 0 })).finally(() => {
      requestAnimationFrame(() => { restoringViewport.current = false; });
    });
  }, [layout.layoutRevision, layout.scope, layout.viewport, layoutReady, setViewport]);

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
      } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z" && layoutSaveStatus !== "saving") {
        event.preventDefault();
        persistLayout(event.shiftKey ? store.getState().actions.redoLayout() : store.getState().actions.undoLayout());
      }
    }}
  >
    <div className="workflow-canvas__toolbar-stack"><header className="workflow-canvas__toolbar">
      <nav aria-label="画布范围">
        {scope.type === "shot" && <button type="button" aria-label="返回项目范围" onClick={() => store.getState().actions.focusProject()}><ChevronLeft size={16} aria-hidden="true" />项目</button>}
        <span aria-current="page">{scopeShot?.title ?? "项目关系"}</span>
      </nav>
      <div>
        <button type="button" aria-label="撤销布局" disabled={!layoutHistory.length || layoutSaveStatus === "saving"} onClick={() => persistLayout(store.getState().actions.undoLayout())}><Undo2 size={16} aria-hidden="true" />撤销</button>
        <button type="button" aria-label="重做布局" disabled={!layoutFuture.length || layoutSaveStatus === "saving"} onClick={() => persistLayout(store.getState().actions.redoLayout())}><Redo2 size={16} aria-hidden="true" />重做</button>
        <button type="button" onClick={focusSelection}><Focus size={16} aria-hidden="true" />聚焦选择</button>
        <button type="button" onClick={() => fitCurrent()}><Layers3 size={16} aria-hidden="true" />适配当前范围</button>
      </div>
    </header>
    {layoutSaveStatus !== "idle" && <div className={`workflow-canvas__layout-status workflow-canvas__layout-status--${layoutSaveStatus}`} role={layoutSaveStatus === "conflict" || layoutSaveStatus === "error" ? "alert" : "status"}>
      <span>{layoutSaveStatus === "saving" ? "正在保存画布布局…" : layoutMessage}</span>
      {(layoutSaveStatus === "conflict" || layoutSaveStatus === "error") && <button type="button" onClick={reloadLayout}>重新读取布局</button>}
    </div>}</div>
    <div className="workflow-canvas__surface">
      <ReactFlow<CanvasNode, Edge>
        key={scope.type === "project" ? "project" : `shot:${scope.id}`}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={layoutReady && layoutSaveStatus !== "saving"}
        nodesConnectable={false}
        nodesFocusable={false}
        elementsSelectable
        onNodeDragStart={() => store.getState().actions.beginLayoutChange()}
        onNodeDrag={(_, node) => store.getState().actions.previewLayoutNode(
          node.type === "scene" ? node.data.entityId : node.id,
          node.position,
          {
            sceneId: node.type === "scene" ? node.data.entityId : node.parentId?.replace(/^scene:/, ""),
            width: node.type === "scene" && node.data.collapsed ? undefined : node.measured?.width ?? node.width,
            height: node.type === "scene" && node.data.collapsed ? undefined : node.measured?.height ?? node.height,
          },
        )}
        onNodeDragStop={(_, node) => {
          store.getState().actions.previewLayoutNode(
            node.type === "scene" ? node.data.entityId : node.id,
            node.position,
            {
              sceneId: node.type === "scene" ? node.data.entityId : node.parentId?.replace(/^scene:/, ""),
              width: node.type === "scene" && node.data.collapsed ? undefined : node.measured?.width ?? node.width,
              height: node.type === "scene" && node.data.collapsed ? undefined : node.measured?.height ?? node.height,
            },
          );
          persistLayout(store.getState().actions.finishLayoutChange());
        }}
        onMoveEnd={(_, viewport) => {
          if (restoringViewport.current || layoutSaveStatus === "saving") return;
          persistLayout(store.getState().actions.updateLayoutViewport(viewport));
        }}
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
        aria-label="镜头关系画布"
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
