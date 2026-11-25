import { describe, expect, it } from "vitest";
import type { CreativeCanvasDocument } from "./creativeCanvasApi";
import { absoluteCanvasNodePosition, arrangeCanvasDocument, createCanvasGroup, finishCanvasGroupDrag, mergeCanvasGroups, moveCanvasGroup, resizeCanvasNodeFromCorner, ungroupCanvas } from "./creativeCanvasGeometry";

const document: CreativeCanvasDocument = {
  revision: 1,
  nodes: [
    { id: "text-1", kind: "text", parentId: "group-1", position: { x: 100, y: 80 }, text: "caption", width: 300, height: 150, freeResize: true },
    { id: "generate-1", kind: "generate", parentId: "group-1", position: { x: 480, y: 80 } },
  ],
  edges: [{ id: "edge-1", source: "text-1", target: "generate-1", sourceHandle: "text", targetHandle: "prompt" }],
  groups: [{ id: "group-1", label: "首屏", position: { x: 50, y: 20 }, width: 700, height: 300 }],
  viewport: { x: 0, y: 0, zoom: 1 },
  settings: {},
};

describe("创作画布几何", () => {
  it("四角自由缩放保持对侧边界，并钳制最小尺寸", () => {
    expect(resizeCanvasNodeFromCorner(document.nodes[0], "top-left", { x: 80, y: 50 })).toEqual({
      position: { x: 180, y: 70 }, width: 220, height: 160,
    });
    expect(resizeCanvasNodeFromCorner(document.nodes[0], "bottom-right", { x: -999, y: -999 })).toEqual({
      position: { x: 100, y: 80 }, width: 220, height: 160,
    });
  });

  it("锁定比例节点按主轴缩放，freeResize 节点允许独立宽高", () => {
    const locked = { ...document.nodes[0], freeResize: false };
    expect(resizeCanvasNodeFromCorner(locked, "bottom-right", { x: 120, y: 10 })).toEqual({
      position: { x: 100, y: 80 }, width: 420, height: 210,
    });
    expect(resizeCanvasNodeFromCorner(document.nodes[0], "bottom-right", { x: 120, y: 10 })).toEqual({
      position: { x: 100, y: 80 }, width: 420, height: 160,
    });
    const constrained = resizeCanvasNodeFromCorner({ ...document.nodes[0], width: 220, height: 160, freeResize: false }, "bottom-right", { x: 10000, y: 1 });
    expect(constrained.width / constrained.height).toBeCloseTo(220 / 160);
    expect(constrained.width).toBeLessThanOrEqual(8192);
    expect(constrained.height).toBeLessThanOrEqual(8192);
  });

  it("移动和解组保持成员绝对坐标与连接", () => {
    const moved = moveCanvasGroup(document, "group-1", { x: 40, y: -20 });
    expect(moved.nodes.map(node => node.position)).toEqual([{ x: 100, y: 80 }, { x: 480, y: 80 }]);
    expect(absoluteCanvasNodePosition(moved, "text-1")).toEqual({ x: 190, y: 80 });
    expect(moved.edges).toEqual(document.edges);

    const ungrouped = ungroupCanvas(moved, "group-1");
    expect(ungrouped.groups).toEqual([]);
    expect(ungrouped.nodes.map(node => node.position)).toEqual([{ x: 190, y: 80 }, { x: 570, y: 80 }]);
    expect(ungrouped.edges).toEqual(document.edges);
  });

  it("解组嵌套分组时将子节点提升到父组坐标系", () => {
    const nested: CreativeCanvasDocument = {
      ...document,
      nodes: [{ ...document.nodes[0], parentId: "group-child" }],
      edges: [],
      groups: [
        { id: "group-root", position: { x: 30, y: 10 }, width: 800, height: 500 },
        { id: "group-child", parentId: "group-root", position: { x: 50, y: 20 }, width: 500, height: 260 },
      ],
    };

    expect(absoluteCanvasNodePosition(nested, "text-1")).toEqual({ x: 180, y: 110 });
    const ungrouped = ungroupCanvas(nested, "group-child");
    expect(ungrouped.groups).toEqual([{ id: "group-root", position: { x: 30, y: 10 }, width: 800, height: 500 }]);
    expect(ungrouped.nodes[0]).toEqual(expect.objectContaining({ parentId: "group-root", position: { x: 150, y: 100 } }));
    expect(absoluteCanvasNodePosition(ungrouped, "text-1")).toEqual({ x: 180, y: 110 });
  });

  it("打组只重挂选区根节点，保留后代、绝对位置和连接", () => {
    const grouped = createCanvasGroup(document, ["group-1", "text-1"], "产品", "group-2", { "text-1": { width: 300, height: 150 } });
    expect(grouped.groups?.map(group => group.id)).toEqual(["group-1", "group-2"]);
    expect(grouped.groups?.find(group => group.id === "group-2")).toEqual(expect.objectContaining({ label: "产品", position: { x: 26, y: -20 }, width: 748, height: 364 }));
    expect(grouped.groups?.find(group => group.id === "group-1")).toEqual(expect.objectContaining({ parentId: "group-2", position: { x: 24, y: 40 } }));
    expect(grouped.nodes[0]).toEqual(expect.objectContaining({ parentId: "group-1", position: document.nodes[0].position }));
    expect(absoluteCanvasNodePosition(grouped, "text-1")).toEqual(absoluteCanvasNodePosition(document, "text-1"));
    expect(grouped.edges).toEqual(document.edges);
  });

  it("打组或合并产生超过后端上限的分组时拒绝并保留原文档", () => {
    const spread: CreativeCanvasDocument = { ...document, nodes: [
      { id: "left", kind: "text", position: { x: 0, y: 0 }, text: "左" },
      { id: "right", kind: "text", position: { x: 8100, y: 0 }, text: "右" },
    ], edges: [], groups: [] };
    expect(() => createCanvasGroup(spread, ["left", "right"], "过宽", "group-wide")).toThrow("8192");
    expect(spread.nodes.map(node => node.parentId)).toEqual([undefined, undefined]);
    const groups: CreativeCanvasDocument = { ...spread, nodes: [], groups: [
      { id: "group-left", position: { x: 0, y: 0 }, width: 300, height: 160 },
      { id: "group-right", position: { x: 8000, y: 0 }, width: 300, height: 160 },
    ] };
    expect(() => mergeCanvasGroups(groups, ["group-left", "group-right"])).toThrow("8192");
    expect(groups.groups?.map(group => group.id)).toEqual(["group-left", "group-right"]);
  });

  it("合并分组保留第一个组并将直接子节点重挂到合并坐标系", () => {
    const merging: CreativeCanvasDocument = {
      ...document,
      nodes: [
        { ...document.nodes[0], parentId: "group-a", position: { x: 20, y: 30 } },
        { ...document.nodes[1], parentId: "group-b-child", position: { x: 8, y: 12 } },
      ],
      groups: [
        { id: "group-a", position: { x: 0, y: 0 }, width: 300, height: 200 },
        { id: "group-b", position: { x: 400, y: 0 }, width: 200, height: 200 },
        { id: "group-b-child", parentId: "group-b", position: { x: 10, y: 20 }, width: 100, height: 80 },
      ],
    };
    const merged = mergeCanvasGroups(merging, ["group-a", "group-b", "group-b-child"]);

    expect(merged.groups?.map(group => group.id)).toEqual(["group-b-child", "group-a"]);
    expect(merged.groups?.find(group => group.id === "group-a")).toEqual(expect.objectContaining({ position: { x: 0, y: 0 }, width: 600, height: 200 }));
    expect(merged.groups?.find(group => group.id === "group-b-child")).toEqual(expect.objectContaining({ parentId: "group-a", position: { x: 410, y: 20 } }));
    expect(absoluteCanvasNodePosition(merged, "text-1")).toEqual(absoluteCanvasNodePosition(merging, "text-1"));
    expect(absoluteCanvasNodePosition(merged, "generate-1")).toEqual(absoluteCanvasNodePosition(merging, "generate-1"));
    expect(merged.edges).toEqual(merging.edges);
  });

  it("拖放结束时按最深相交组重归属，并在离开组后提升为根节点", () => {
    const dragging: CreativeCanvasDocument = {
      ...document,
      nodes: [
        { id: "dragged", kind: "text", parentId: "group-a", position: { x: 420, y: 40 }, text: "拖动" },
        { id: "stays", kind: "text", parentId: "group-a", position: { x: 40, y: 40 }, text: "保留" },
      ],
      edges: [],
      groups: [
        { id: "group-a", position: { x: 0, y: 0 }, width: 300, height: 260 },
        { id: "group-b", position: { x: 400, y: 0 }, width: 300, height: 260 },
      ],
    };
    const moved = finishCanvasGroupDrag(dragging, ["dragged"]);
    expect(moved.nodes.find(node => node.id === "dragged")).toEqual(expect.objectContaining({ parentId: "group-b", position: { x: 24, y: 40 } }));
    expect(absoluteCanvasNodePosition(moved, "dragged")).toEqual({ x: 420, y: 40 });

    const out = finishCanvasGroupDrag({ ...dragging, groups: [{ id: "group-a", position: { x: 0, y: 0 }, width: 300, height: 260 }] }, ["dragged"]);
    expect(out.nodes.find(node => node.id === "dragged")).toEqual(expect.objectContaining({ parentId: undefined, position: { x: 420, y: 40 } }));
  });

  it("拖放后收紧受影响组框，同时保持成员绝对位置", () => {
    const compacted = finishCanvasGroupDrag({
      ...document,
      nodes: [{ id: "inside", kind: "text", parentId: "group-a", position: { x: 100, y: 90 }, text: "内容" }],
      edges: [],
      groups: [{ id: "group-a", position: { x: 0, y: 0 }, width: 700, height: 500 }],
    }, ["inside"]);
    expect(compacted.groups?.[0]).toEqual(expect.objectContaining({ position: { x: 76, y: 50 }, width: 268, height: 224 }));
    expect(compacted.nodes[0]).toEqual(expect.objectContaining({ position: { x: 24, y: 40 } }));
    expect(absoluteCanvasNodePosition(compacted, "inside")).toEqual({ x: 100, y: 90 });
  });

  it("拖放贴合会超出 8192px 时保留原有合法分组", () => {
    const oversized: CreativeCanvasDocument = {
      ...document,
      nodes: [{ id: "inside", kind: "text", parentId: "group-a", position: { x: 8000, y: 40 }, text: "内容" }, { id: "anchor", kind: "text", parentId: "group-a", position: { x: 0, y: 40 }, text: "锚点" }],
      edges: [],
      groups: [{ id: "group-a", position: { x: 0, y: 0 }, width: 8192, height: 300 }],
    };
    expect(finishCanvasGroupDrag(oversized, ["inside"])).toBe(oversized);
  });

  it("Dagre 整理保留流程横向层级，并将孤立图按连通分量分行", () => {
    const isolated: CreativeCanvasDocument = {
      ...document,
      nodes: Array.from({ length: 9 }, (_, index) => ({ id: `isolated-${index}`, kind: "text" as const, position: { x: index * 15, y: index * 20 }, text: "节点" })),
      edges: [],
      groups: [],
    };
    const arrangedIsolated = arrangeCanvasDocument(isolated, {}, { width: 1600, height: 900 });
    expect(new Set(arrangedIsolated.nodes.map(node => node.position.y)).size).toBeLessThan(4);
    expect(new Set(arrangedIsolated.nodes.map(node => node.position.x)).size).toBeGreaterThan(2);

    const flow = arrangeCanvasDocument({ ...document, nodes: [{ id: "source", kind: "text", position: { x: 0, y: 0 }, text: "输入" }, { id: "target", kind: "generate", position: { x: 0, y: 300 } }], edges: [{ id: "edge", source: "source", target: "target", sourceHandle: "text", targetHandle: "prompt" }], groups: [] });
    expect(flow.nodes.find(node => node.id === "target")!.position.x).toBeGreaterThan(flow.nodes.find(node => node.id === "source")!.position.x);
  });
});
