import { describe, expect, it } from "vitest";

import type { PreproductionWorkspace } from "./preproductionApi";
import { createPreproductionWorkspaceStore } from "./preproductionWorkspaceStore";
import { projectWorkflowCanvas } from "./workflowCanvasProjection";

function workspace(): PreproductionWorkspace {
  return {
    schemaVersion: 2,
    revision: 4,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 12, aspect: "16:9", mustPreserve: "服装" },
    assets: [{ id: "asset-a", name: "街头参考", kind: "image", role: "scene", url: "/street.png" }],
    scenes: [
      { id: "scene-a", title: "相遇", rank: "00000001", description: "雨夜街头" },
      { id: "scene-b", title: "追逐", rank: "00000002", description: "穿过巷口" },
    ],
    shots: [
      {
        id: "shot-a", sceneId: "scene-a", rank: "00000001", title: "雨中相遇", duration: 5,
        prompt: "", negativePrompt: "", assetIds: ["asset-a"],
        nodes: [{ id: "trim-a", kind: "trim", input: "asset:asset-a", params: { start: 0, end: 5 }, status: "completed", artifacts: [] }],
      },
      { id: "shot-b", sceneId: "scene-b", rank: "00000002", title: "开始追逐", duration: 4, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
    ],
    workflow: { nodes: [], edges: [] },
    canvasLayout: {
      scope: { type: "project", id: "project-1" }, layoutRevision: 0,
      nodes: {
        "scene-a": { x: 20, y: 40, width: 360, height: 220 },
        "shot:shot-a": { x: 70, y: 120 },
      },
      viewport: { x: 0, y: 0, zoom: 1 },
    },
    checks: [{ level: "warning", shotId: "shot-a", message: "确认雨景连续性" }],
    nodeCatalog: [{ kind: "trim", label: "截取" }],
  };
}

describe("projectWorkflowCanvas", () => {
  it("项目范围按场景包含规范 ShotNode，并使用独立布局而不改写 store", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    const before = store.getState();

    const graph = projectWorkflowCanvas(before, { type: "project" });

    expect(graph.nodes.map(({ id, kind, parentId, position }) => ({ id, kind, parentId, position }))).toEqual([
      { id: "scene:scene-a", kind: "scene", parentId: undefined, position: { x: 20, y: 40 } },
      { id: "shot:shot-a", kind: "shot", parentId: "scene:scene-a", position: { x: 50, y: 80 } },
      { id: "scene:scene-b", kind: "scene", parentId: undefined, position: { x: 0, y: 280 } },
      { id: "shot:shot-b", kind: "shot", parentId: "scene:scene-b", position: { x: 40, y: 80 } },
    ]);
    expect(graph.nodes.find((node) => node.id === "scene:scene-a")?.data).toMatchObject({ label: "相遇", shotCount: 1, duration: 5, issueCount: 1 });
    expect(graph.nodes.find((node) => node.id === "shot:shot-a")?.data).toMatchObject({ label: "雨中相遇", status: "已准备" });
    expect(store.getState()).toBe(before);
  });

  it("镜头范围只投影所属素材、流程节点和现有依赖边", () => {
    const store = createPreproductionWorkspaceStore(workspace());

    const graph = projectWorkflowCanvas(store.getState(), { type: "shot", id: "shot-a" });

    expect(graph.nodes.map(({ id, kind }) => ({ id, kind }))).toEqual([
      { id: "asset:shot-a:asset-a", kind: "asset" },
      { id: "process:shot-a:trim-a", kind: "process" },
    ]);
    expect(graph.edges).toEqual([{
      id: "edge:asset:shot-a:asset-a:process:shot-a:trim-a",
      source: "asset:shot-a:asset-a",
      target: "process:shot-a:trim-a",
      sourceHandle: "asset",
      targetHandle: "input",
      label: "数据",
    }]);
    expect(graph.nodes[0].data).toMatchObject({ label: "街头参考", detail: "场景素材" });
    expect(graph.nodes[1].data).toMatchObject({ label: "截取", status: "已完成", entityId: "trim-a" });
  });

  it.each([
    ["pending", "neutral"],
    ["queued", "running"],
    ["running", "running"],
    ["completed", "success"],
    ["stale", "warning"],
    ["failed", "error"],
  ] as const)("流程状态 %s 使用 %s 语义色", (status, statusTone) => {
    const current = workspace();
    current.shots[0].nodes[0].status = status;
    const graph = projectWorkflowCanvas(createPreproductionWorkspaceStore(current).getState(), { type: "shot", id: "shot-a" });

    expect(graph.nodes.find((node) => node.kind === "process")?.data.statusTone).toBe(statusTone);
  });

  it("空项目产生可呈现的空投影", () => {
    const empty = workspace();
    empty.scenes = [];
    empty.shots = [];
    const store = createPreproductionWorkspaceStore(empty);

    expect(projectWorkflowCanvas(store.getState(), { type: "project" })).toEqual({ nodes: [], edges: [] });
  });
});
