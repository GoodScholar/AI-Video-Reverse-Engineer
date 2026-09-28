import { describe, expect, it } from "vitest";

import type { PreproductionWorkspace } from "./preproductionApi";
import {
  createPreproductionWorkspaceStore,
  selectOrderedSceneSections,
  selectWorkspaceSnapshot,
} from "./preproductionWorkspaceStore";

function workspace(): PreproductionWorkspace {
  return {
    schemaVersion: 2,
    revision: 3,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 12, aspect: "16:9", mustPreserve: "服装" },
    assets: [],
    scenes: [
      { id: "scene-b", title: "追逐", rank: "00000002", description: "" },
      { id: "scene-a", title: "相遇", rank: "00000001", description: "" },
    ],
    shots: [
      { id: "shot-b2", sceneId: "scene-b", rank: "00000004", title: "跑出画面", duration: 2, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
      { id: "shot-a2", sceneId: "scene-a", rank: "00000002", title: "抬头", duration: 3, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
      { id: "shot-b1", sceneId: "scene-b", rank: "00000003", title: "开始追逐", duration: 4, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
      { id: "shot-a1", sceneId: "scene-a", rank: "00000001", title: "雨中相遇", duration: 5, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
    ],
    workflow: { nodes: [], edges: [] },
    canvasLayout: { scope: { type: "project", id: "project-1" }, layoutRevision: 2, nodes: {} },
    checks: [],
    nodeCatalog: [],
  };
}

describe("preproductionWorkspaceStore", () => {
  it("首次服务端回包默认展开新场景并选中首个镜头", () => {
    const empty = workspace();
    empty.revision = 0;
    empty.scenes = [];
    empty.shots = [];
    const store = createPreproductionWorkspaceStore(empty);

    store.getState().actions.acceptServerWorkspace(workspace());

    expect(store.getState().view.expandedSceneIds).toEqual(new Set(["scene-a", "scene-b"]));
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a1"]));
    expect(store.getState().persistence.dirty).toBe(false);
  });

  it("按 Scene.rank 和 Shot.rank 产生稳定分段，而不是沿用接口数组顺序", () => {
    const store = createPreproductionWorkspaceStore(workspace());

    expect(selectOrderedSceneSections(store.getState())).toEqual([
      { sceneId: "scene-a", shotIds: ["shot-a1", "shot-a2"] },
      { sceneId: "scene-b", shotIds: ["shot-b1", "shot-b2"] },
    ]);
    expect(selectWorkspaceSnapshot(store.getState()).shots.map((shot) => shot.id)).toEqual([
      "shot-a1", "shot-a2", "shot-b1", "shot-b2",
    ]);
  });

  it("按镜头 ID 编辑并在场景折叠切换后保留选择和未保存内容", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    store.getState().actions.selectShot("shot-b1");
    store.getState().actions.updateShot("shot-b1", (shot) => ({ ...shot, prompt: "追逐中的低机位" }));
    store.getState().actions.toggleScene("scene-a");

    const state = store.getState();
    expect(state.selection.primaryEntity).toEqual({ type: "shot", id: "shot-b1" });
    expect(state.entities.shotsById["shot-b1"].prompt).toBe("追逐中的低机位");
    expect(state.view.expandedSceneIds.has("scene-a")).toBe(false);
    expect(state.persistence.dirty).toBe(true);
  });

  it("选择变化复用内容快照，节点编辑同步 workflow 投影", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    const beforeSelection = selectWorkspaceSnapshot(store.getState());
    store.getState().actions.selectShot("shot-b1");
    expect(selectWorkspaceSnapshot(store.getState())).toBe(beforeSelection);

    store.getState().actions.updateShot("shot-a1", (shot) => ({ ...shot, nodes: [{
      id: "prompt-a", kind: "prompt", input: "", params: { text: "同步后的提示词" }, status: "pending", artifacts: [],
    }] }));

    const process = store.getState().entities.workflowNodesById["process:shot-a1:prompt-a"];
    expect(process).toMatchObject({ type: "process", ownerShotId: "shot-a1", config: { params: { text: "同步后的提示词" } } });
    expect(selectWorkspaceSnapshot(store.getState()).workflow.nodes).toContain(process);
  });

  it("复制镜头重映射步骤依赖，并保持跨场景 rank 唯一", () => {
    const source = workspace();
    source.shots = source.shots.map((shot) => shot.id === "shot-a1" ? { ...shot, nodes: [
      { id: "source", kind: "prompt", input: "", params: { text: "首步" }, status: "completed", artifacts: [{ name: "prompt.txt", url: "/prompt.txt" }] },
      { id: "frame", kind: "first_frame", input: "node:source", params: {}, status: "completed", artifacts: [{ name: "frame.png", url: "/frame.png" }] },
    ] } : shot);
    const store = createPreproductionWorkspaceStore(source);

    store.getState().actions.duplicateShot("shot-a1");

    const copyId = [...store.getState().selection.selectedShotIds][0];
    const copy = store.getState().entities.shotsById[copyId];
    expect(copy.sceneId).toBe("scene-a");
    expect(copy.nodes[1].input).toBe(`node:${copy.nodes[0].id}`);
    expect(copy.nodes.every((node) => node.status === "pending" && node.artifacts.length === 0)).toBe(true);
    const ranks = selectWorkspaceSnapshot(store.getState()).shots.map((shot) => shot.rank);
    expect(new Set(ranks).size).toBe(ranks.length);
  });

  it("保存回包到达时保留提交后继续编辑的草稿，同时更新已保存修订", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    store.getState().actions.updateShot("shot-a1", (shot) => ({ ...shot, prompt: "提交版本" }));
    const submittedSnapshot = store.getState().persistence.editableSnapshot;
    store.getState().actions.updateShot("shot-a1", (shot) => ({ ...shot, prompt: "提交后继续编辑" }));

    const saved = workspace();
    saved.revision = 4;
    saved.shots = saved.shots.map((shot) => shot.id === "shot-a1" ? { ...shot, prompt: "提交版本" } : shot);
    store.getState().actions.acceptServerWorkspace(saved, submittedSnapshot);

    const state = store.getState();
    expect(state.entities.shotsById["shot-a1"].prompt).toBe("提交后继续编辑");
    expect(state.persistence.revision).toBe(4);
    expect(state.persistence.dirty).toBe(true);
  });

  it("切换列表、分屏、画布和聚焦范围不改变内容快照或未保存草稿", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    store.getState().actions.updateShot("shot-a1", (shot) => ({ ...shot, prompt: "保留草稿" }));
    const before = selectWorkspaceSnapshot(store.getState());

    store.getState().actions.setViewMode("canvas");
    store.getState().actions.focusShot("shot-a1");
    store.getState().actions.setInspectorOpen(false);

    const state = store.getState();
    expect(state.view.mode).toBe("canvas");
    expect(state.view.scope).toEqual({ type: "shot", id: "shot-a1" });
    expect(state.view.inspectorOpen).toBe(false);
    expect(state.entities.shotsById["shot-a1"].prompt).toBe("保留草稿");
    expect(state.persistence.dirty).toBe(true);
    expect(selectWorkspaceSnapshot(state)).toBe(before);

    state.actions.focusProject();
    expect(store.getState().view.scope).toEqual({ type: "project" });
  });

  it("通过统一命令支持替换、增减、范围和框选镜头", () => {
    const store = createPreproductionWorkspaceStore(workspace());

    store.getState().actions.focusShot("shot-b1");
    store.getState().actions.selectShot("shot-a1", { source: "list" });
    expect(store.getState().view.scope).toEqual({ type: "project" });
    store.getState().actions.selectShot("shot-a2", { mode: "toggle", source: "list" });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a1", "shot-a2"]));
    expect(store.getState().selection.primaryEntity).toEqual({ type: "shot", id: "shot-a2" });

    store.getState().actions.selectShot("shot-a2", { mode: "toggle", source: "list" });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a1"]));
    expect(store.getState().selection.primaryEntity).toEqual({ type: "shot", id: "shot-a1" });
    store.getState().actions.selectShot("shot-a1", { mode: "toggle", source: "list" });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set());
    expect(store.getState().selection.primaryEntity).toBeNull();

    store.getState().actions.selectShot("shot-a2", { source: "list" });

    store.getState().actions.selectShot("shot-b1", { mode: "range", source: "list" });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a2", "shot-b1"]));

    store.getState().actions.selectShots(["shot-a2", "shot-b2"], "canvas");
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a2", "shot-b2"]));
    expect(store.getState().selection.primaryEntity).toEqual({ type: "shot", id: "shot-b2" });
    expect(store.getState().view.locateRequest).toMatchObject({ entity: { type: "shot", id: "shot-b2" }, source: "canvas" });
  });

  it("流程节点和场景成为主对象时不污染批量镜头选择", () => {
    const current = workspace();
    current.shots = current.shots.map((shot) => shot.id === "shot-b1" ? {
      ...shot,
      nodes: [{ id: "trim-b1", kind: "trim", input: "", params: {}, status: "pending", artifacts: [] }],
    } : shot);
    const store = createPreproductionWorkspaceStore(current);
    store.getState().actions.selectShot("shot-a2");

    store.getState().actions.selectNode("shot-b1", "trim-b1", "canvas");
    expect(store.getState().selection.primaryEntity).toEqual({ type: "processNode", id: "trim-b1", shotId: "shot-b1" });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a2"]));
    expect(store.getState().view.expandedSceneIds.has("scene-b")).toBe(true);
    expect(store.getState().view.scope).toEqual({ type: "shot", id: "shot-b1" });
    expect(store.getState().view.locateRequest).toMatchObject({ entity: { type: "processNode", id: "trim-b1", shotId: "shot-b1" }, source: "canvas" });

    store.getState().actions.selectShot("shot-b1");
    store.getState().actions.selectNode("shot-b1", "trim-b1", "external");
    expect(store.getState().selection.selectedShotIds).toEqual(new Set());

    store.getState().actions.selectShot("shot-a2");
    store.getState().actions.selectScene("scene-a", "canvas");
    expect(store.getState().selection.primaryEntity).toEqual({ type: "scene", id: "scene-a" });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a2"]));
    expect(store.getState().view.scope).toEqual({ type: "project" });
    expect(store.getState().view.locateRequest).toMatchObject({ entity: { type: "scene", id: "scene-a" }, source: "canvas" });
  });

  it("悬停和键盘焦点保持瞬时，并在切换项目时清理", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    const before = selectWorkspaceSnapshot(store.getState());
    store.getState().actions.setHoveredEntity({ type: "shot", id: "shot-b1" });
    store.getState().actions.setFocusedEntity({ type: "scene", id: "scene-b" });

    expect(store.getState().selection.hoveredEntity).toEqual({ type: "shot", id: "shot-b1" });
    expect(store.getState().selection.focusedEntity).toEqual({ type: "scene", id: "scene-b" });
    expect(selectWorkspaceSnapshot(store.getState())).toBe(before);
    expect(store.getState().persistence.dirty).toBe(false);

    const next = workspace();
    next.scenes = [{ id: "scene-next", title: "下一项目", rank: "00000001", description: "" }];
    next.shots = [{ ...next.shots[0], id: "shot-next", sceneId: "scene-next" }];
    store.getState().actions.hydrate(next);

    expect(store.getState().selection.hoveredEntity).toBeNull();
    expect(store.getState().selection.focusedEntity).toBeNull();
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-next"]));
    expect(store.getState().view.locateRequest).toBeNull();
  });
});
