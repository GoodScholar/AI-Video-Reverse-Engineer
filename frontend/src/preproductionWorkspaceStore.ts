import { createStore, type StoreApi } from "zustand/vanilla";

import type {
  CanvasLayout,
  PreproductionAsset,
  PreproductionBrief,
  PreproductionCheck,
  PreproductionScene,
  PreproductionShot,
  PreproductionWorkspace,
  WorkflowEdge,
  WorkflowNode,
} from "./preproductionApi";

export type SelectableEntity =
  | { type: "shot"; id: string }
  | { type: "processNode"; id: string; shotId: string }
  | { type: "scene"; id: string };

type PrimaryEntity = SelectableEntity | null;
export type SelectionSource = "list" | "canvas" | "external";
export type ShotSelectionMode = "replace" | "toggle" | "range";
export type ShotSelectionOptions = { mode?: ShotSelectionMode; source?: SelectionSource };

export type WorkspaceViewMode = "split" | "canvas" | "list";
export type WorkspaceViewScope = { type: "project" } | { type: "shot"; id: string };
type LayoutScope = CanvasLayout["scope"];
type LayoutNodes = CanvasLayout["nodes"];
type LayoutPosition = Pick<LayoutNodes[string], "x" | "y">;
type OrganizationSnapshot = {
  scenes: PreproductionScene[];
  shots: PreproductionShot[];
  layoutNodes: Record<string, LayoutNodes[string] | null>;
};

export type SceneSection = { sceneId: string; shotIds: string[] };

export type PreproductionWorkspaceState = {
  schemaVersion: 2;
  brief: PreproductionBrief;
  assets: PreproductionAsset[];
  checks: PreproductionCheck[];
  nodeCatalog: PreproductionWorkspace["nodeCatalog"];
  entities: {
    scenesById: Record<string, PreproductionScene>;
    shotsById: Record<string, PreproductionShot>;
    workflowNodesById: Record<string, WorkflowNode>;
    workflowEdgesById: Record<string, WorkflowEdge>;
  };
  order: {
    sceneIds: string[];
    shotIdsByScene: Record<string, string[]>;
  };
  selection: {
    primaryEntity: PrimaryEntity;
    selectedShotIds: Set<string>;
    selectionAnchorShotId: string | null;
    hoveredEntity: SelectableEntity | null;
    focusedEntity: SelectableEntity | null;
  };
  view: {
    mode: WorkspaceViewMode;
    scope: WorkspaceViewScope;
    inspectorOpen: boolean;
    expandedSceneIds: Set<string>;
    locateRequest: { entity: SelectableEntity; source: SelectionSource; requestId: number } | null;
  };
  layout: CanvasLayout;
  layoutPersistence: {
    layoutsByScope: Record<string, CanvasLayout>;
    historyByScope: Record<string, LayoutNodes[]>;
    futureByScope: Record<string, LayoutNodes[]>;
    interactionBase: LayoutNodes | null;
    saveStatus: "idle" | "saving" | "conflict" | "error";
    conflictMessage: string | null;
  };
  organizationHistory: {
    history: OrganizationSnapshot[];
    future: OrganizationSnapshot[];
  };
  persistence: {
    revision: number;
    savedSnapshot: string;
    editableSnapshot: string;
    dirty: boolean;
    saveStatus: "idle" | "saving" | "conflict" | "error";
    conflictMessage: string | null;
  };
  actions: {
    hydrate: (workspace: PreproductionWorkspace) => void;
    restoreDraft: (workspace: PreproductionWorkspace, savedSnapshot: string) => void;
    acceptServerWorkspace: (workspace: PreproductionWorkspace, submittedSnapshot?: string) => void;
    editWorkspace: (change: (workspace: PreproductionWorkspace) => PreproductionWorkspace) => void;
    updateBrief: (change: (brief: PreproductionBrief) => PreproductionBrief) => void;
    updateShot: (shotId: string, change: (shot: PreproductionShot) => PreproductionShot) => void;
    replaceShots: (shots: PreproductionShot[]) => void;
    duplicateShot: (shotId: string) => void;
    moveShot: (shotId: string, direction: -1 | 1) => void;
    reorderShots: (orderedShotIds: string[]) => boolean;
    moveShotsToScene: (shotIds: string[], sceneId: string) => boolean;
    createShot: (shot: PreproductionShot, position?: LayoutPosition) => boolean;
    placeShotOnCanvas: (shotId: string, position: LayoutPosition) => CanvasLayout | null;
    deleteScene: (
      sceneId: string,
      resolution: { migrateToSceneId?: string; deleteShots?: boolean },
    ) => boolean;
    undoOrganization: () => boolean;
    redoOrganization: () => boolean;
    selectShot: (shotId: string, options?: ShotSelectionOptions) => void;
    selectShots: (shotIds: string[], source?: SelectionSource) => void;
    selectNode: (shotId: string, nodeId: string, source?: SelectionSource) => void;
    selectScene: (sceneId: string, source?: SelectionSource) => void;
    setHoveredEntity: (entity: SelectableEntity | null) => void;
    setFocusedEntity: (entity: SelectableEntity | null) => void;
    toggleScene: (sceneId: string) => void;
    setViewMode: (mode: WorkspaceViewMode) => void;
    focusProject: () => void;
    focusShot: (shotId: string) => void;
    setInspectorOpen: (open: boolean) => void;
    setSaveStatus: (status: PreproductionWorkspaceState["persistence"]["saveStatus"], message?: string | null) => void;
    activateLayout: (scope: LayoutScope) => boolean;
    acceptLayout: (layout: CanvasLayout, activate?: boolean) => void;
    beginLayoutChange: () => void;
    previewLayoutNode: (
      nodeId: string,
      position: LayoutPosition,
      options?: { sceneId?: string; width?: number; height?: number },
    ) => void;
    finishLayoutChange: () => CanvasLayout | null;
    toggleCanvasScene: (sceneId: string) => CanvasLayout | null;
    updateLayoutViewport: (viewport: NonNullable<CanvasLayout["viewport"]>) => CanvasLayout | null;
    undoLayout: () => CanvasLayout | null;
    redoLayout: () => CanvasLayout | null;
    setLayoutSaveStatus: (
      status: PreproductionWorkspaceState["layoutPersistence"]["saveStatus"],
      message?: string | null,
    ) => void;
  };
};

export type PreproductionWorkspaceStore = StoreApi<PreproductionWorkspaceState>;

function compareRank(left: { rank: string; id: string }, right: { rank: string; id: string }) {
  return left.rank.localeCompare(right.rank) || left.id.localeCompare(right.id);
}

function editableSnapshot(
  revision: number,
  brief: PreproductionBrief,
  scenes: PreproductionScene[],
  shots: PreproductionShot[],
) {
  return JSON.stringify({ revision, brief, scenes, shots });
}

export function canvasLayoutScopeKey(scope: LayoutScope) {
  return `${scope.type}:${scope.id}`;
}

function cloneLayoutNodes(nodes: LayoutNodes): LayoutNodes {
  return Object.fromEntries(Object.entries(nodes).map(([id, node]) => [id, { ...node }]));
}

function sameLayoutNodes(left: LayoutNodes, right: LayoutNodes) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function withCachedLayout(
  state: PreproductionWorkspaceState,
  layout: CanvasLayout,
  layoutPersistence: Partial<PreproductionWorkspaceState["layoutPersistence"]> = {},
) {
  const key = canvasLayoutScopeKey(layout.scope);
  return {
    layout,
    layoutPersistence: {
      ...state.layoutPersistence,
      ...layoutPersistence,
      layoutsByScope: { ...state.layoutPersistence.layoutsByScope, [key]: layout },
    },
  };
}

function indexById<T extends { id: string }>(items: T[]) {
  return Object.fromEntries(items.map((item) => [item.id, item])) as Record<string, T>;
}

function workflowId(kind: "shot" | "asset" | "process", ...parts: string[]) {
  return [kind, ...parts.map(encodeURIComponent)].join(":");
}

function projectWorkflow(shots: PreproductionShot[], assets: PreproductionAsset[]) {
  const nodes: WorkflowNode[] = [];
  const edges: WorkflowEdge[] = [];
  const assetsById = indexById(assets);
  for (const shot of shots) {
    nodes.push({ id: workflowId("shot", shot.id), type: "shot", shotId: shot.id });
    const assetIds = new Set([
      ...shot.assetIds,
      ...shot.nodes.filter((node) => node.input.startsWith("asset:")).map((node) => node.input.slice(6)),
    ]);
    for (const assetId of assetIds) {
      nodes.push({
        id: workflowId("asset", shot.id, assetId),
        type: "asset",
        assetId,
        ownerShotId: shot.id,
        role: assetsById[assetId]?.role ?? "reference",
      });
    }
    for (const step of shot.nodes) {
      const processId = workflowId("process", shot.id, step.id);
      nodes.push({
        id: processId,
        type: "process",
        ownerShotId: shot.id,
        processKind: step.kind,
        config: { input: step.input, params: step.params },
        status: step.status,
        error: step.error,
        artifacts: step.artifacts,
      });
      const sourceId = step.input.startsWith("asset:")
        ? workflowId("asset", shot.id, step.input.slice(6))
        : step.input.startsWith("node:")
          ? workflowId("process", shot.id, step.input.slice(5))
          : null;
      if (sourceId) edges.push({
        id: `edge:${sourceId}:${processId}`,
        kind: "data",
        source: { nodeId: sourceId, portId: step.input.startsWith("asset:") ? "asset" : "output" },
        target: { nodeId: processId, portId: "input" },
      });
    }
  }
  return { nodes, edges };
}

function normalize(workspace: PreproductionWorkspace) {
  const scenes = [...(workspace.scenes?.length ? workspace.scenes : [{ id: "scene-default", title: "未分场", rank: "00000001", description: "" }])].sort(compareRank);
  const fallbackSceneId = scenes[0].id;
  const shots = [...(workspace.shots ?? [])].map((shot, index) => ({
    ...shot,
    sceneId: shot.sceneId ?? fallbackSceneId,
    rank: shot.rank ?? String(index + 1).padStart(8, "0"),
  })).sort(compareRank);
  const workflow = projectWorkflow(shots, workspace.assets ?? []);
  const shotIdsByScene: Record<string, string[]> = Object.fromEntries(scenes.map((scene) => [scene.id, []]));
  for (const shot of shots) (shotIdsByScene[shot.sceneId] ??= []).push(shot.id);
  return {
    entities: {
      scenesById: indexById(scenes),
      shotsById: indexById(shots),
      workflowNodesById: indexById(workflow.nodes),
      workflowEdgesById: indexById(workflow.edges),
    },
    order: { sceneIds: scenes.map((scene) => scene.id), shotIdsByScene },
  };
}

export function selectOrderedSceneSections(state: PreproductionWorkspaceState): SceneSection[] {
  return state.order.sceneIds.map((sceneId) => ({
    sceneId,
    shotIds: state.order.shotIdsByScene[sceneId] ?? [],
  }));
}

type WorkspaceSnapshotCache = {
  brief: PreproductionBrief;
  assets: PreproductionAsset[];
  checks: PreproductionCheck[];
  nodeCatalog: PreproductionWorkspace["nodeCatalog"];
  layout: CanvasLayout;
  order: PreproductionWorkspaceState["order"];
  revision: number;
  snapshot: PreproductionWorkspace;
};

const workspaceSnapshotCache = new WeakMap<PreproductionWorkspaceState["entities"], WorkspaceSnapshotCache>();

export function selectWorkspaceSnapshot(state: PreproductionWorkspaceState): PreproductionWorkspace {
  const cached = workspaceSnapshotCache.get(state.entities);
  if (cached
    && cached.brief === state.brief
    && cached.assets === state.assets
    && cached.checks === state.checks
    && cached.nodeCatalog === state.nodeCatalog
    && cached.layout === state.layout
    && cached.order === state.order
    && cached.revision === state.persistence.revision) return cached.snapshot;
  const scenes = state.order.sceneIds.map((id) => state.entities.scenesById[id]).filter(Boolean);
  const shots = state.order.sceneIds.flatMap((sceneId) => state.order.shotIdsByScene[sceneId] ?? [])
    .map((id) => state.entities.shotsById[id]).filter(Boolean);
  const snapshot: PreproductionWorkspace = {
    schemaVersion: state.schemaVersion,
    revision: state.persistence.revision,
    brief: state.brief,
    assets: state.assets,
    scenes,
    shots,
    workflow: {
      nodes: Object.values(state.entities.workflowNodesById),
      edges: Object.values(state.entities.workflowEdgesById),
    },
    canvasLayout: state.layout,
    checks: state.checks,
    nodeCatalog: state.nodeCatalog,
  };
  workspaceSnapshotCache.set(state.entities, {
    brief: state.brief,
    assets: state.assets,
    checks: state.checks,
    nodeCatalog: state.nodeCatalog,
    layout: state.layout,
    order: state.order,
    revision: state.persistence.revision,
    snapshot,
  });
  return snapshot;
}

function initialState(workspace: PreproductionWorkspace) {
  const normalized = normalize(workspace);
  const snapshot = editableSnapshot(
    workspace.revision,
    workspace.brief,
    selectScenes(normalized.order, normalized.entities.scenesById),
    selectShots(normalized.order, normalized.entities.shotsById),
  );
  const firstShotId = normalized.order.sceneIds.flatMap((sceneId) => normalized.order.shotIdsByScene[sceneId] ?? [])[0] ?? null;
  const layout = workspace.canvasLayout ?? { scope: { type: "project" as const, id: "legacy" }, layoutRevision: 0, nodes: {} };
  const layoutKey = canvasLayoutScopeKey(layout.scope);
  return {
    schemaVersion: 2 as const,
    brief: workspace.brief,
    assets: workspace.assets ?? [],
    checks: workspace.checks ?? [],
    nodeCatalog: workspace.nodeCatalog ?? [],
    ...normalized,
    selection: {
      primaryEntity: firstShotId ? { type: "shot" as const, id: firstShotId } : null,
      selectedShotIds: new Set(firstShotId ? [firstShotId] : []),
      selectionAnchorShotId: firstShotId,
      hoveredEntity: null,
      focusedEntity: null,
    },
    view: {
      mode: "split" as const,
      scope: { type: "project" as const },
      inspectorOpen: true,
      expandedSceneIds: new Set(normalized.order.sceneIds),
      locateRequest: null,
    },
    layout,
    layoutPersistence: {
      layoutsByScope: { [layoutKey]: layout },
      historyByScope: {},
      futureByScope: {},
      interactionBase: null,
      saveStatus: "idle" as const,
      conflictMessage: null,
    },
    organizationHistory: { history: [], future: [] },
    persistence: {
      revision: workspace.revision,
      savedSnapshot: snapshot,
      editableSnapshot: snapshot,
      dirty: false,
      saveStatus: "idle" as const,
      conflictMessage: null,
    },
  };
}

function selectShots(order: PreproductionWorkspaceState["order"], shotsById: Record<string, PreproductionShot>) {
  return order.sceneIds.flatMap((sceneId) => order.shotIdsByScene[sceneId] ?? [])
    .map((id) => shotsById[id]).filter(Boolean);
}

function selectScenes(order: PreproductionWorkspaceState["order"], scenesById: Record<string, PreproductionScene>) {
  return order.sceneIds.map((id) => scenesById[id]).filter(Boolean);
}

function orderedShotIds(state: PreproductionWorkspaceState) {
  return state.order.sceneIds.flatMap((sceneId) => state.order.shotIdsByScene[sceneId] ?? []);
}

function locateRequest(
  state: PreproductionWorkspaceState,
  entity: SelectableEntity,
  source: SelectionSource,
) {
  return { entity, source, requestId: (state.view.locateRequest?.requestId ?? 0) + 1 };
}

function rerankShots(order: PreproductionWorkspaceState["order"], shotsById: Record<string, PreproductionShot>) {
  const ranked = { ...shotsById };
  selectShots(order, shotsById).forEach((shot, index) => {
    ranked[shot.id] = { ...shot, rank: String(index + 1).padStart(8, "0") };
  });
  return ranked;
}

function withDirtyState(state: PreproductionWorkspaceState, change: Partial<PreproductionWorkspaceState>) {
  const next = { ...state, ...change };
  const shots = selectShots(next.order, next.entities.shotsById);
  const workflow = projectWorkflow(shots, next.assets);
  const projected = {
    ...next,
    entities: {
      ...next.entities,
      workflowNodesById: indexById(workflow.nodes),
      workflowEdgesById: indexById(workflow.edges),
    },
  };
  const snapshot = editableSnapshot(
    projected.persistence.revision,
    projected.brief,
    selectScenes(projected.order, projected.entities.scenesById),
    shots,
  );
  return {
    ...projected,
    persistence: {
      ...projected.persistence,
      editableSnapshot: snapshot,
      dirty: snapshot !== projected.persistence.savedSnapshot,
      saveStatus: projected.persistence.saveStatus === "conflict" ? "conflict" as const : "idle" as const,
    },
  };
}

function organizationSnapshot(
  state: PreproductionWorkspaceState,
  layoutNodeIds: string[] = [],
): OrganizationSnapshot {
  return {
    scenes: selectScenes(state.order, state.entities.scenesById),
    shots: selectShots(state.order, state.entities.shotsById),
    layoutNodes: Object.fromEntries(layoutNodeIds.map((nodeId) => [
      nodeId,
      state.layout.nodes[nodeId] ? { ...state.layout.nodes[nodeId] } : null,
    ])),
  };
}

function restoreOrganizationSnapshot(
  state: PreproductionWorkspaceState,
  snapshot: OrganizationSnapshot,
) {
  const scenes = snapshot.scenes.map((scene) => state.entities.scenesById[scene.id] ?? scene);
  const shots = snapshot.shots.map((shot) => {
    const current = state.entities.shotsById[shot.id];
    return current ? { ...current, sceneId: shot.sceneId, rank: shot.rank } : shot;
  });
  const normalized = normalize({
    ...selectWorkspaceSnapshot(state),
    scenes,
    shots,
  });
  const next = withDirtyState(state, normalized);
  const nodes = cloneLayoutNodes(next.layout.nodes);
  for (const [nodeId, node] of Object.entries(snapshot.layoutNodes)) {
    if (node) nodes[nodeId] = { ...node };
    else delete nodes[nodeId];
  }
  const layout = { ...next.layout, nodes };
  return { ...next, ...withCachedLayout(next, layout) };
}

function commitOrganization(
  state: PreproductionWorkspaceState,
  scenes: PreproductionScene[],
  shots: PreproductionShot[],
  layoutNodes: LayoutNodes = state.layout.nodes,
  affectedLayoutNodeIds: string[] = [],
) {
  const before = organizationSnapshot(state, affectedLayoutNodeIds);
  const normalized = normalize({ ...selectWorkspaceSnapshot(state), scenes, shots });
  let next = withDirtyState(state, normalized);
  if (layoutNodes !== state.layout.nodes) {
    const layout = { ...next.layout, nodes: cloneLayoutNodes(layoutNodes) };
    next = { ...next, ...withCachedLayout(next, layout) };
  }
  return {
    ...next,
    organizationHistory: {
      history: [...state.organizationHistory.history, before],
      future: [],
    },
  };
}

export function createPreproductionWorkspaceStore(workspace: PreproductionWorkspace): PreproductionWorkspaceStore {
  return createStore<PreproductionWorkspaceState>()((set, get) => ({
    ...initialState(workspace),
    actions: {
      hydrate(next) {
        set((state) => ({ ...initialState(next), actions: state.actions }));
      },
      restoreDraft(next, savedSnapshot) {
        set((state) => {
          const restored = initialState(next);
          return {
            ...restored,
            actions: state.actions,
            persistence: {
              ...restored.persistence,
              savedSnapshot,
              dirty: restored.persistence.editableSnapshot !== savedSnapshot,
            },
          };
        });
      },
      acceptServerWorkspace(next, submittedSnapshot) {
        set((state) => {
          if (next.revision < state.persistence.revision) return state;
          const keepNewerDraft = Boolean(submittedSnapshot && state.persistence.editableSnapshot !== submittedSnapshot);
          const accepted = keepNewerDraft
            ? {
                ...next,
                brief: state.brief,
                scenes: selectScenes(state.order, state.entities.scenesById),
                shots: selectShots(state.order, state.entities.shotsById),
              }
            : next;
          const base = initialState(accepted);
          const savedState = normalize(next);
          const saved = editableSnapshot(
            next.revision,
            next.brief,
            selectScenes(savedState.order, savedState.entities.scenesById),
            selectShots(savedState.order, savedState.entities.shotsById),
          );
          const primary = state.selection.primaryEntity;
          const validPrimary = primary?.type === "shot" && base.entities.shotsById[primary.id]
            ? primary
            : primary?.type === "processNode" && base.entities.shotsById[primary.shotId]?.nodes.some((node) => node.id === primary.id)
              ? primary
              : primary?.type === "scene" && base.entities.scenesById[primary.id]
                ? primary
                : base.selection.primaryEntity;
          const selectedShotIds = new Set([...state.selection.selectedShotIds].filter((id) => Boolean(base.entities.shotsById[id])));
          if (selectedShotIds.size === 0) {
            for (const id of base.selection.selectedShotIds) selectedShotIds.add(id);
          }
          const previousSceneIds = new Set(state.order.sceneIds);
          const expandedSceneIds = new Set([...state.view.expandedSceneIds].filter((id) => Boolean(base.entities.scenesById[id])));
          for (const sceneId of base.order.sceneIds) {
            if (!previousSceneIds.has(sceneId)) expandedSceneIds.add(sceneId);
          }
          const draftSnapshot = editableSnapshot(
            next.revision,
            accepted.brief,
            selectScenes(base.order, base.entities.scenesById),
            selectShots(base.order, base.entities.shotsById),
          );
          return {
            ...base,
            actions: state.actions,
            layout: state.layout,
            layoutPersistence: state.layoutPersistence,
            selection: {
              ...state.selection,
              primaryEntity: validPrimary,
              selectedShotIds,
            },
            view: {
              ...state.view,
              expandedSceneIds,
            },
            persistence: {
              revision: next.revision,
              savedSnapshot: saved,
              editableSnapshot: draftSnapshot,
              dirty: draftSnapshot !== saved,
              saveStatus: "idle",
              conflictMessage: null,
            },
          };
        });
      },
      editWorkspace(change) {
        set((state) => {
          const next = change(selectWorkspaceSnapshot(state));
          const normalized = normalize(next);
          return withDirtyState(state, {
            brief: next.brief,
            assets: next.assets,
            checks: next.checks,
            nodeCatalog: next.nodeCatalog,
            ...normalized,
          });
        });
      },
      updateBrief(change) {
        set((state) => withDirtyState(state, { brief: change(state.brief) }));
      },
      updateShot(shotId, change) {
        set((state) => {
          const shot = state.entities.shotsById[shotId];
          if (!shot) return state;
          return withDirtyState(state, {
            entities: {
              ...state.entities,
              shotsById: { ...state.entities.shotsById, [shotId]: change(shot) },
            },
          });
        });
      },
      replaceShots(shots) {
        set((state) => {
          const normalized = normalize({ ...selectWorkspaceSnapshot(state), shots });
          return withDirtyState(state, normalized);
        });
      },
      duplicateShot(shotId) {
        set((state) => {
          const source = state.entities.shotsById[shotId];
          if (!source) return state;
          const sceneShotIds = state.order.shotIdsByScene[source.sceneId] ?? [];
          const sourceIndex = sceneShotIds.indexOf(shotId);
          const copyId = `shot-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
          const nodeIds = new Map(source.nodes.map((node, index) => [node.id, `${copyId}-node-${index + 1}`]));
          const copy: PreproductionShot = {
            ...source,
            id: copyId,
            title: `${source.title} 副本`,
            resultAssetId: null,
            resultVersions: [],
            nodes: source.nodes.map((node) => ({
              ...node,
              id: nodeIds.get(node.id)!,
              input: node.input.startsWith("node:")
                ? `node:${nodeIds.get(node.input.slice(5)) ?? ""}`
                : node.input,
              status: "pending",
              error: undefined,
              artifacts: [],
            })),
          };
          const nextSceneShotIds = [...sceneShotIds];
          nextSceneShotIds.splice(sourceIndex + 1, 0, copyId);
          const order = { ...state.order, shotIdsByScene: { ...state.order.shotIdsByScene, [source.sceneId]: nextSceneShotIds } };
          const shotsById = rerankShots(order, { ...state.entities.shotsById, [copyId]: copy });
          return withDirtyState(state, {
            entities: { ...state.entities, shotsById },
            order,
            selection: {
              ...state.selection,
              primaryEntity: { type: "shot", id: copyId },
              selectedShotIds: new Set([copyId]),
              selectionAnchorShotId: copyId,
            },
            view: {
              ...state.view,
              expandedSceneIds: new Set([...state.view.expandedSceneIds, source.sceneId]),
              locateRequest: locateRequest(state, { type: "shot", id: copyId }, "external"),
            },
          });
        });
      },
      moveShot(shotId, direction) {
        set((state) => {
          const shot = state.entities.shotsById[shotId];
          if (!shot) return state;
          const current = state.order.shotIdsByScene[shot.sceneId] ?? [];
          const from = current.indexOf(shotId);
          const to = from + direction;
          if (from < 0 || to < 0 || to >= current.length) return state;
          const nextIds = [...current];
          [nextIds[from], nextIds[to]] = [nextIds[to], nextIds[from]];
          const order = { ...state.order, shotIdsByScene: { ...state.order.shotIdsByScene, [shot.sceneId]: nextIds } };
          const shotsById = rerankShots(order, state.entities.shotsById);
          return commitOrganization(
            state,
            selectScenes(state.order, state.entities.scenesById),
            selectShots(order, shotsById),
          );
        });
      },
      reorderShots(orderedIds) {
        let changed = false;
        set((state) => {
          const existingIds = orderedShotIds(state);
          if (orderedIds.length !== existingIds.length
            || new Set(orderedIds).size !== orderedIds.length
            || orderedIds.some((id) => !state.entities.shotsById[id])) return state;
          if (orderedIds.every((id, index) => id === existingIds[index])) return state;
          const shots = orderedIds.map((id, index) => ({
            ...state.entities.shotsById[id],
            rank: String(index + 1).padStart(8, "0"),
          }));
          changed = true;
          return commitOrganization(
            state,
            selectScenes(state.order, state.entities.scenesById),
            shots,
          );
        });
        return changed;
      },
      moveShotsToScene(shotIds, sceneId) {
        let changed = false;
        set((state) => {
          if (!state.entities.scenesById[sceneId]
            || !shotIds.length
            || new Set(shotIds).size !== shotIds.length
            || shotIds.some((id) => !state.entities.shotsById[id])) return state;
          if (shotIds.every((id) => state.entities.shotsById[id].sceneId === sceneId)) return state;
          const requested = new Set(shotIds);
          const shots = orderedShotIds(state).map((id) => {
            const shot = state.entities.shotsById[id];
            return requested.has(id) ? { ...shot, sceneId } : shot;
          });
          changed = true;
          return commitOrganization(
            state,
            selectScenes(state.order, state.entities.scenesById),
            shots,
          );
        });
        return changed;
      },
      createShot(shot, position) {
        let created = false;
        set((state) => {
          const nodeId = `shot:${encodeURIComponent(shot.id)}`;
          if (state.entities.shotsById[shot.id]
            || !state.entities.scenesById[shot.sceneId]
            || Object.values(state.entities.shotsById).some((item) => item.rank === shot.rank)
            || state.layout.nodes[nodeId]) return state;
          const shots = [...selectShots(state.order, state.entities.shotsById), shot];
          const nodes = position
            ? { ...state.layout.nodes, [nodeId]: { x: position.x, y: position.y } }
            : state.layout.nodes;
          created = true;
          const next = commitOrganization(
            state,
            selectScenes(state.order, state.entities.scenesById),
            shots,
            nodes,
            position ? [nodeId] : [],
          );
          return {
            ...next,
            selection: {
              ...next.selection,
              primaryEntity: { type: "shot", id: shot.id },
              selectedShotIds: new Set([shot.id]),
              selectionAnchorShotId: shot.id,
            },
            view: {
              ...next.view,
              expandedSceneIds: new Set([...next.view.expandedSceneIds, shot.sceneId]),
              locateRequest: locateRequest(next, { type: "shot", id: shot.id }, "external"),
            },
          };
        });
        return created;
      },
      placeShotOnCanvas(shotId, position) {
        let result: CanvasLayout | null = null;
        set((state) => {
          if (!state.entities.shotsById[shotId]) return state;
          const nodeId = `shot:${encodeURIComponent(shotId)}`;
          const previous = cloneLayoutNodes(state.layout.nodes);
          const current = previous[nodeId];
          if (current?.x === position.x && current.y === position.y) return state;
          const key = canvasLayoutScopeKey(state.layout.scope);
          const layout = {
            ...state.layout,
            nodes: { ...state.layout.nodes, [nodeId]: { ...current, x: position.x, y: position.y } },
          };
          result = layout;
          return withCachedLayout(state, layout, {
            historyByScope: {
              ...state.layoutPersistence.historyByScope,
              [key]: [...(state.layoutPersistence.historyByScope[key] ?? []), previous],
            },
            futureByScope: { ...state.layoutPersistence.futureByScope, [key]: [] },
          });
        });
        return result;
      },
      deleteScene(sceneId, resolution) {
        let deleted = false;
        set((state) => {
          if (!state.entities.scenesById[sceneId] || state.order.sceneIds.length <= 1) return state;
          const affectedIds = state.order.shotIdsByScene[sceneId] ?? [];
          if (affectedIds.length && !resolution.migrateToSceneId && !resolution.deleteShots) return state;
          if (resolution.migrateToSceneId
            && (resolution.migrateToSceneId === sceneId || !state.entities.scenesById[resolution.migrateToSceneId])) return state;
          if (resolution.deleteShots && affectedIds.some((id) => state.entities.shotsById[id].nodes
            .some((node) => node.status === "queued" || node.status === "running"))) return state;
          const scenes = selectScenes(state.order, state.entities.scenesById).filter((scene) => scene.id !== sceneId);
          const affected = new Set(affectedIds);
          const shots = selectShots(state.order, state.entities.shotsById)
            .filter((shot) => !resolution.deleteShots || !affected.has(shot.id))
            .map((shot) => affected.has(shot.id) && resolution.migrateToSceneId
              ? { ...shot, sceneId: resolution.migrateToSceneId }
              : shot);
          const nodes = { ...state.layout.nodes };
          delete nodes[sceneId];
          if (resolution.deleteShots) {
            for (const shotId of affectedIds) delete nodes[`shot:${encodeURIComponent(shotId)}`];
          }
          deleted = true;
          const affectedLayoutNodeIds = [
            sceneId,
            ...(resolution.deleteShots ? affectedIds.map((shotId) => `shot:${encodeURIComponent(shotId)}`) : []),
          ];
          const next = commitOrganization(state, scenes, shots, nodes, affectedLayoutNodeIds);
          const selectedShotIds = new Set([...next.selection.selectedShotIds].filter((id) => next.entities.shotsById[id]));
          const primary = next.selection.primaryEntity;
          const primaryEntity = primary?.type === "scene" && primary.id === sceneId
            ? null
            : primary?.type === "shot" && !next.entities.shotsById[primary.id]
              ? null
              : primary?.type === "processNode" && !next.entities.shotsById[primary.shotId]
                ? null
                : primary;
          return {
            ...next,
            selection: { ...next.selection, primaryEntity, selectedShotIds },
            view: {
              ...next.view,
              expandedSceneIds: new Set([...next.view.expandedSceneIds].filter((id) => id !== sceneId)),
            },
          };
        });
        return deleted;
      },
      undoOrganization() {
        let undone = false;
        set((state) => {
          const history = state.organizationHistory.history;
          const previous = history[history.length - 1];
          if (!previous) return state;
          undone = true;
          const current = organizationSnapshot(state, Object.keys(previous.layoutNodes));
          const next = restoreOrganizationSnapshot(state, previous);
          return {
            ...next,
            organizationHistory: {
              history: history.slice(0, -1),
              future: [...state.organizationHistory.future, current],
            },
          };
        });
        return undone;
      },
      redoOrganization() {
        let redone = false;
        set((state) => {
          const future = state.organizationHistory.future;
          const nextSnapshot = future[future.length - 1];
          if (!nextSnapshot) return state;
          redone = true;
          const current = organizationSnapshot(state, Object.keys(nextSnapshot.layoutNodes));
          const next = restoreOrganizationSnapshot(state, nextSnapshot);
          return {
            ...next,
            organizationHistory: {
              history: [...state.organizationHistory.history, current],
              future: future.slice(0, -1),
            },
          };
        });
        return redone;
      },
      selectShot(shotId, options = {}) {
        const shot = get().entities.shotsById[shotId];
        if (!shot) return;
        const mode = options.mode ?? "replace";
        const source = options.source ?? "external";
        set((state) => {
          let selectedShotIds: Set<string>;
          if (mode === "toggle") {
            selectedShotIds = new Set(state.selection.selectedShotIds);
            if (selectedShotIds.has(shotId)) selectedShotIds.delete(shotId);
            else selectedShotIds.add(shotId);
          } else if (mode === "range") {
            const ids = orderedShotIds(state);
            const anchor = state.selection.selectionAnchorShotId ?? shotId;
            const anchorIndex = ids.indexOf(anchor);
            const targetIndex = ids.indexOf(shotId);
            const start = Math.min(anchorIndex < 0 ? targetIndex : anchorIndex, targetIndex);
            const end = Math.max(anchorIndex < 0 ? targetIndex : anchorIndex, targetIndex);
            selectedShotIds = new Set(ids.slice(start, end + 1));
          } else {
            selectedShotIds = new Set([shotId]);
          }
          const primaryShotId = mode === "toggle" && !selectedShotIds.has(shotId)
            ? [...selectedShotIds].pop() ?? null
            : shotId;
          return {
            selection: {
              ...state.selection,
              primaryEntity: primaryShotId ? { type: "shot", id: primaryShotId } : null,
              selectedShotIds,
              selectionAnchorShotId: mode === "range" ? state.selection.selectionAnchorShotId ?? shotId : shotId,
            },
            view: {
              ...state.view,
              scope: { type: "project" },
              expandedSceneIds: new Set([...state.view.expandedSceneIds, shot.sceneId]),
              locateRequest: primaryShotId ? locateRequest(state, { type: "shot", id: primaryShotId }, source) : null,
            },
          };
        });
      },
      selectShots(shotIds, source = "external") {
        set((state) => {
          const requested = new Set(shotIds);
          const selected = orderedShotIds(state).filter((id) => requested.has(id));
          const primaryShotId = [...selected].pop() ?? null;
          if (!primaryShotId) return {
            selection: { ...state.selection, primaryEntity: null, selectedShotIds: new Set(), selectionAnchorShotId: null },
          };
          const expandedSceneIds = new Set(state.view.expandedSceneIds);
          for (const id of selected) expandedSceneIds.add(state.entities.shotsById[id].sceneId);
          return {
            selection: {
              ...state.selection,
              primaryEntity: { type: "shot", id: primaryShotId },
              selectedShotIds: new Set(selected),
              selectionAnchorShotId: primaryShotId,
            },
            view: {
              ...state.view,
              scope: { type: "project" },
              expandedSceneIds,
              locateRequest: locateRequest(state, { type: "shot", id: primaryShotId }, source),
            },
          };
        });
      },
      selectNode(shotId, nodeId, source = "external") {
        if (!get().entities.shotsById[shotId]?.nodes.some((node) => node.id === nodeId)) return;
        set((state) => {
          const selectedShotIds = new Set(state.selection.selectedShotIds);
          selectedShotIds.delete(shotId);
          const entity = { type: "processNode" as const, id: nodeId, shotId };
          return {
            selection: {
              ...state.selection,
              primaryEntity: entity,
              selectedShotIds,
              selectionAnchorShotId: state.selection.selectionAnchorShotId === shotId
                ? [...selectedShotIds].pop() ?? null
                : state.selection.selectionAnchorShotId,
            },
            view: {
              ...state.view,
              scope: { type: "shot", id: shotId },
              expandedSceneIds: new Set([...state.view.expandedSceneIds, state.entities.shotsById[shotId].sceneId]),
              locateRequest: locateRequest(state, entity, source),
            },
          };
        });
      },
      selectScene(sceneId, source = "external") {
        if (!get().entities.scenesById[sceneId]) return;
        set((state) => ({
          selection: { ...state.selection, primaryEntity: { type: "scene", id: sceneId } },
          view: { ...state.view, scope: { type: "project" }, locateRequest: locateRequest(state, { type: "scene", id: sceneId }, source) },
        }));
      },
      setHoveredEntity(hoveredEntity) {
        set((state) => ({ selection: { ...state.selection, hoveredEntity } }));
      },
      setFocusedEntity(focusedEntity) {
        set((state) => ({ selection: { ...state.selection, focusedEntity } }));
      },
      toggleScene(sceneId) {
        set((state) => {
          const expandedSceneIds = new Set(state.view.expandedSceneIds);
          if (expandedSceneIds.has(sceneId)) expandedSceneIds.delete(sceneId);
          else expandedSceneIds.add(sceneId);
          return { view: { ...state.view, expandedSceneIds } };
        });
      },
      setViewMode(mode) {
        set((state) => ({ view: { ...state.view, mode } }));
      },
      focusProject() {
        set((state) => ({ view: { ...state.view, scope: { type: "project" } } }));
      },
      focusShot(shotId) {
        if (!get().entities.shotsById[shotId]) return;
        set((state) => ({ view: { ...state.view, scope: { type: "shot", id: shotId } } }));
      },
      setInspectorOpen(open) {
        set((state) => ({ view: { ...state.view, inspectorOpen: open } }));
      },
      setSaveStatus(saveStatus, message = null) {
        set((state) => ({ persistence: { ...state.persistence, saveStatus, conflictMessage: message } }));
      },
      activateLayout(scope) {
        const cached = get().layoutPersistence.layoutsByScope[canvasLayoutScopeKey(scope)];
        if (!cached) return false;
        set((state) => ({
          layout: cached,
          layoutPersistence: {
            ...state.layoutPersistence,
            interactionBase: null,
            saveStatus: "idle",
            conflictMessage: null,
          },
        }));
        return true;
      },
      acceptLayout(layout, activate = false) {
        set((state) => {
          const nextKey = canvasLayoutScopeKey(layout.scope);
          const cached = { ...state.layoutPersistence.layoutsByScope, [nextKey]: layout };
          return {
            layout: activate ? layout : state.layout,
            layoutPersistence: {
              ...state.layoutPersistence,
              layoutsByScope: cached,
              ...(activate ? {
                interactionBase: null,
                saveStatus: "idle" as const,
                conflictMessage: null,
              } : {}),
            },
          };
        });
      },
      beginLayoutChange() {
        set((state) => state.layoutPersistence.interactionBase ? state : ({
          layoutPersistence: {
            ...state.layoutPersistence,
            interactionBase: cloneLayoutNodes(state.layout.nodes),
          },
        }));
      },
      previewLayoutNode(nodeId, position, options = {}) {
        set((state) => {
          const current = state.layout.nodes[nodeId];
          if (!current) return state;
          const nodes = cloneLayoutNodes(state.layout.nodes);
          let x = position.x;
          let y = position.y;
          if (options.sceneId && nodeId !== options.sceneId) {
            const scene = nodes[options.sceneId];
            if (scene) { x += scene.x; y += scene.y; }
          }
          if (options.sceneId === nodeId) {
            const dx = x - current.x;
            const dy = y - current.y;
            for (const shotId of state.order.shotIdsByScene[nodeId] ?? []) {
              const shotNodeId = `shot:${encodeURIComponent(shotId)}`;
              const shotNode = nodes[shotNodeId];
              if (shotNode) nodes[shotNodeId] = { ...shotNode, x: shotNode.x + dx, y: shotNode.y + dy };
            }
          }
          nodes[nodeId] = {
            ...current,
            x,
            y,
            ...(options.width ? { width: options.width } : {}),
            ...(options.height ? { height: options.height } : {}),
          };
          const layout = { ...state.layout, nodes };
          return withCachedLayout(state, layout);
        });
      },
      finishLayoutChange() {
        let result: CanvasLayout | null = null;
        set((state) => {
          const base = state.layoutPersistence.interactionBase;
          if (!base || sameLayoutNodes(base, state.layout.nodes)) {
            return { layoutPersistence: { ...state.layoutPersistence, interactionBase: null } };
          }
          const key = canvasLayoutScopeKey(state.layout.scope);
          result = state.layout;
          return {
            layoutPersistence: {
              ...state.layoutPersistence,
              historyByScope: {
                ...state.layoutPersistence.historyByScope,
                [key]: [...(state.layoutPersistence.historyByScope[key] ?? []), base],
              },
              futureByScope: { ...state.layoutPersistence.futureByScope, [key]: [] },
              interactionBase: null,
            },
          };
        });
        return result;
      },
      toggleCanvasScene(sceneId) {
        let result: CanvasLayout | null = null;
        set((state) => {
          const scene = state.layout.nodes[sceneId];
          if (!scene) return state;
          const key = canvasLayoutScopeKey(state.layout.scope);
          const previous = cloneLayoutNodes(state.layout.nodes);
          const layout = {
            ...state.layout,
            nodes: { ...state.layout.nodes, [sceneId]: { ...scene, collapsed: !scene.collapsed } },
          };
          result = layout;
          return withCachedLayout(state, layout, {
            historyByScope: {
              ...state.layoutPersistence.historyByScope,
              [key]: [...(state.layoutPersistence.historyByScope[key] ?? []), previous],
            },
            futureByScope: { ...state.layoutPersistence.futureByScope, [key]: [] },
          });
        });
        return result;
      },
      updateLayoutViewport(viewport) {
        let result: CanvasLayout | null = null;
        set((state) => {
          if (state.layout.viewport?.x === viewport.x
            && state.layout.viewport.y === viewport.y
            && state.layout.viewport.zoom === viewport.zoom) return state;
          const layout = { ...state.layout, viewport };
          result = layout;
          return withCachedLayout(state, layout);
        });
        return result;
      },
      undoLayout() {
        let result: CanvasLayout | null = null;
        set((state) => {
          const key = canvasLayoutScopeKey(state.layout.scope);
          const history = state.layoutPersistence.historyByScope[key] ?? [];
          const previous = history[history.length - 1];
          if (!previous) return state;
          const layout = { ...state.layout, nodes: cloneLayoutNodes(previous) };
          result = layout;
          return withCachedLayout(state, layout, {
            historyByScope: { ...state.layoutPersistence.historyByScope, [key]: history.slice(0, -1) },
            futureByScope: {
              ...state.layoutPersistence.futureByScope,
              [key]: [...(state.layoutPersistence.futureByScope[key] ?? []), cloneLayoutNodes(state.layout.nodes)],
            },
          });
        });
        return result;
      },
      redoLayout() {
        let result: CanvasLayout | null = null;
        set((state) => {
          const key = canvasLayoutScopeKey(state.layout.scope);
          const future = state.layoutPersistence.futureByScope[key] ?? [];
          const next = future[future.length - 1];
          if (!next) return state;
          const layout = { ...state.layout, nodes: cloneLayoutNodes(next) };
          result = layout;
          return withCachedLayout(state, layout, {
            historyByScope: {
              ...state.layoutPersistence.historyByScope,
              [key]: [...(state.layoutPersistence.historyByScope[key] ?? []), cloneLayoutNodes(state.layout.nodes)],
            },
            futureByScope: { ...state.layoutPersistence.futureByScope, [key]: future.slice(0, -1) },
          });
        });
        return result;
      },
      setLayoutSaveStatus(saveStatus, message = null) {
        set((state) => ({
          layoutPersistence: { ...state.layoutPersistence, saveStatus, conflictMessage: message },
        }));
      },
    },
  }));
}
