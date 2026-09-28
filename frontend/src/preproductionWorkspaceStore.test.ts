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
      { id: "shot-b2", sceneId: "scene-b", rank: "00000002", title: "跑出画面", duration: 2, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
      { id: "shot-a2", sceneId: "scene-a", rank: "00000002", title: "抬头", duration: 3, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
      { id: "shot-b1", sceneId: "scene-b", rank: "00000001", title: "开始追逐", duration: 4, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
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
});
