import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreativeCanvasAsset, CreativeCanvasDocument } from "./creativeCanvasApi";
import { copyCanvasNodes, createCanvasClipboardSnapshot, duplicateCanvasSubgraph, pasteCanvasClipboardSnapshot, readCanvasClipboard, saveCanvasClipboard } from "./creativeCanvasClipboard";

const document: CreativeCanvasDocument = {
  revision: 3,
  nodes: [
    { id: "text-1", kind: "text", position: { x: 40, y: 80 }, text: "主卖点" },
    { id: "image-1", kind: "media", position: { x: 40, y: 180 }, assetId: "asset-1" },
    { id: "generate-1", kind: "generate", position: { x: 320, y: 120 }, text: "{{ref 1}} {{ref 2}}", outputAssetId: "result-1", referenceOrder: ["edge-text", "edge-image"] },
    { id: "generate-2", kind: "generate", position: { x: 640, y: 120 }, text: "画布外节点" },
  ],
  edges: [
    { id: "edge-text", source: "text-1", target: "generate-1", sourceHandle: "text", targetHandle: "prompt" },
    { id: "edge-image", source: "image-1", target: "generate-1", sourceHandle: "image", targetHandle: "image" },
    { id: "edge-inbound", source: "generate-1", target: "generate-2", sourceHandle: "image", targetHandle: "image" },
  ],
  viewport: { x: 0, y: 0, zoom: 1 },
  settings: {},
};

const assets: CreativeCanvasAsset[] = [
  { id: "asset-1", name: "原始商品图", kind: "image", url: "/asset-1.png", available: true },
  { id: "result-1", name: "生成结果", kind: "image", url: "/result-1.png", available: true },
];

describe("创作画布剪贴板", () => {
  beforeEach(() => { window.localStorage.clear(); vi.restoreAllMocks(); });
  it("稳定模型与数量可复制，复制的输出冻结为素材而不串用原节点运行指针", () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: document.nodes.map(node => node.id === "generate-1" ? { ...node, params: { providerId: "default", modelId: "model-a", configurationVersion: "v1", count: 3, size: "1024x1024", ratio: "1:1" }, currentOutputRef: { runId: "run-1", resultId: "run-1:legacy:0" }, stale: true } : node) };
    const snapshot = createCanvasClipboardSnapshot("project-a", graph, ["generate-1"]);
    const copy = pasteCanvasClipboardSnapshot(graph, snapshot, "project-a", assets, { x: 800, y: 400 }).nodes[0];
    expect(copy.params).toEqual(graph.nodes[2].params);
    expect(copy.outputAssetId).toBe("result-1");
    expect(copy).not.toHaveProperty("currentOutputRef");
    expect(duplicateCanvasSubgraph(graph, ["generate-1"]).nodes[0]).not.toHaveProperty("currentOutputRef");
  });

  it.each([{ providerId: "default" }, { count: 16 }, { configurationVersion: "v1" }, { size: "1024x1024", ratio: "16:9" }])("拒绝与后端不一致的模型参数 %j", params => {
    const snapshot = createCanvasClipboardSnapshot("project-a", document, ["generate-1"]);
    snapshot.nodes[0].params = params;
    expect(() => pasteCanvasClipboardSnapshot(document, snapshot, "project-a", assets, { x: 800, y: 400 })).toThrow("格式错误");
  });

  it("粘贴子图时为节点和内部边分配新身份，并重写引用顺序", () => {
    const snapshot = createCanvasClipboardSnapshot("project-a", document, ["text-1", "image-1", "generate-1"]);

    const pasted = pasteCanvasClipboardSnapshot(document, snapshot, "project-a", assets, { x: 800, y: 400 }, (() => {
      let index = 0;
      return () => `new-${++index}`;
    })());

    expect(pasted.nodes).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "new-1", position: { x: 800, y: 400 } }),
      expect.objectContaining({ id: "new-2", position: { x: 800, y: 500 }, assetId: "asset-1" }),
      expect.objectContaining({ id: "new-3", position: { x: 1080, y: 440 }, outputAssetId: "result-1", referenceOrder: ["new-4", "new-5"] }),
    ]));
    expect(pasted.edges).toEqual([
      expect.objectContaining({ id: "new-4", source: "new-1", target: "new-3" }),
      expect.objectContaining({ id: "new-5", source: "new-2", target: "new-3" }),
    ]);
    expect(pasted.nodes.find(node => node.id === "new-3")?.text).toBe("{{ref 1}} {{ref 2}}");
  });

  it("创建副本保留进入选区的入边，而剪贴板快照排除选区外连线", () => {
    const copied = duplicateCanvasSubgraph(document, ["generate-1"], (() => {
      let index = 0;
      return () => `copy-${++index}`;
    })());
    const copiedWithoutInputs = copyCanvasNodes(document, ["generate-1"], () => "plain-copy");
    const snapshot = createCanvasClipboardSnapshot("project-a", document, ["generate-1"]);

    expect(copied.edges).toEqual([
      expect.objectContaining({ source: "text-1", target: "copy-1", id: "copy-2" }),
      expect.objectContaining({ source: "image-1", target: "copy-1", id: "copy-3" }),
    ]);
    expect(copiedWithoutInputs).toEqual(expect.objectContaining({ edges: [], nodes: [expect.objectContaining({ id: "plain-copy", text: " " })] }));
    expect(copiedWithoutInputs.nodes[0]).not.toHaveProperty("referenceOrder");
    expect(snapshot.edges).toEqual([]);
  });

  it("拒绝缺失素材和伪造快照，且不会产生部分粘贴结果", () => {
    const snapshot = createCanvasClipboardSnapshot("project-a", document, ["image-1", "generate-1"]);

    expect(() => pasteCanvasClipboardSnapshot(document, snapshot, "project-a", [], { x: 0, y: 0 })).toThrow("素材不可用");
    expect(() => pasteCanvasClipboardSnapshot(document, { ...snapshot, nodes: [{ ...snapshot.nodes[0], kind: "unknown" as never }] }, "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, { ...snapshot, nodes: [{ ...snapshot.nodes[0], assetId: 1 as never }] }, "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
  });

  it("补全不完整引用顺序并拒绝错误端口和循环子图", () => {
    const partiallyOrdered = { ...document, nodes: document.nodes.map(node => node.id === "generate-1" ? { ...node, referenceOrder: ["edge-text"] } : node) };
    const snapshot = createCanvasClipboardSnapshot("project-a", partiallyOrdered, ["text-1", "image-1", "generate-1"]);
    const pasted = pasteCanvasClipboardSnapshot(document, snapshot, "project-a", assets, { x: 0, y: 0 }, (() => { let index = 0; return () => `complete-${++index}`; })());
    const generated = pasted.nodes.find(node => node.kind === "generate");

    expect(generated?.referenceOrder).toEqual(["complete-4", "complete-5"]);
    expect(() => pasteCanvasClipboardSnapshot(document, { ...snapshot, edges: [{ ...snapshot.edges[0], sourceHandle: "image" }] }, "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, { ...snapshot, edges: [...snapshot.edges, { id: "cycle", source: "generate-1", target: "text-1", sourceHandle: "image", targetHandle: "prompt" }] }, "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
  });

  it("复制文本生成节点时保留 prompt、稳定模型与 in 输入引用", () => {
    const graph: CreativeCanvasDocument = {
      ...document,
      nodes: [...document.nodes, { id: "text-target", kind: "text", position: { x: 320, y: 300 }, text: "已采用正文", prompt: "总结 {{ref 1}}", providerId: "text-provider", modelId: "text-model", configurationVersion: "text-v1", currentTextVersionId: "version-1", fontSize: 18, referenceOrder: ["text-input"] }],
      edges: [...document.edges, { id: "text-input", source: "text-1", target: "text-target", sourceHandle: "text", targetHandle: "in" }],
    };
    const snapshot = createCanvasClipboardSnapshot("project-a", graph, ["text-1", "text-target"]);
    const pasted = pasteCanvasClipboardSnapshot(graph, snapshot, "project-a", assets, { x: 800, y: 400 }, (() => { let index = 0; return () => `text-copy-${++index}`; })());
    const copied = pasted.nodes.find(node => node.kind === "text" && node.prompt)?.id;

    expect(pasted.nodes.find(node => node.id === copied)).toMatchObject({ prompt: "总结 {{ref 1}}", providerId: "text-provider", modelId: "text-model", configurationVersion: "text-v1", text: "已采用正文", fontSize: 18, referenceOrder: ["text-copy-3"] });
    expect(pasted.nodes.find(node => node.id === copied)).not.toHaveProperty("currentTextVersionId");
    expect(duplicateCanvasSubgraph(graph, ["text-target"]).nodes[0]).not.toHaveProperty("currentTextVersionId");
    expect(pasted.edges).toContainEqual(expect.objectContaining({ sourceHandle: "text", targetHandle: "in", target: copied }));
  });

  it("跨项目只允许不含素材或结果指针的纯文本子图", () => {
    const textOnly = createCanvasClipboardSnapshot("project-a", document, ["text-1"]);
    const withAsset = createCanvasClipboardSnapshot("project-a", document, ["image-1"]);
    const withResult = createCanvasClipboardSnapshot("project-a", document, ["generate-1"]);

    expect(() => pasteCanvasClipboardSnapshot(document, textOnly, "project-b", [], { x: 0, y: 0 })).not.toThrow();
    expect(() => pasteCanvasClipboardSnapshot(document, withAsset, "project-b", assets, { x: 0, y: 0 })).toThrow("不能跨项目粘贴");
    expect(() => pasteCanvasClipboardSnapshot(document, withResult, "project-b", assets, { x: 0, y: 0 })).toThrow("不能跨项目粘贴");
  });

  it("拒绝不能安全渲染或保存的参数字段", () => {
    const snapshot = createCanvasClipboardSnapshot("project-a", document, ["text-1"]);
    const malformed = (params: unknown) => ({ ...snapshot, nodes: [{ ...snapshot.nodes[0], params }] });

    expect(() => pasteCanvasClipboardSnapshot(document, malformed({ ratio: { x: 1 } }), "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, malformed({ negativePrompt: null }), "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, malformed({ width: 63 }), "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, malformed({ width: 64.5 }), "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, malformed({ size: "64x8193" }), "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, malformed({ denoise: Number.NaN }), "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
    expect(() => pasteCanvasClipboardSnapshot(document, malformed({ unsupported: true }), "project-a", assets, { x: 0, y: 0 })).toThrow("格式错误");
  });

  it("只读取持久化的最新快照，并在配额不足时只保留当前页快照", () => {
    const first = saveCanvasClipboard(createCanvasClipboardSnapshot("project-a", document, ["text-1"]), () => "11111111-1111-1111-1111-111111111111");
    const latest = "ai-video-reverse-engineer:creative-canvas:v1:22222222-2222-2222-2222-222222222222";
    window.localStorage.setItem("ai-video-reverse-engineer.creativeCanvasClipboard.v1", JSON.stringify({
      command: latest,
      snapshot: createCanvasClipboardSnapshot("project-a", document, ["image-1"]),
    }));

    expect(() => readCanvasClipboard(first)).toThrow("已失效");
    expect(readCanvasClipboard(latest)?.nodes[0]?.assetId).toBe("asset-1");

    window.localStorage.clear();
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw new DOMException("quota", "QuotaExceededError"); });
    const memoryOnly = saveCanvasClipboard(createCanvasClipboardSnapshot("project-a", document, ["text-1"]), () => "33333333-3333-3333-3333-333333333333");

    expect(readCanvasClipboard(memoryOnly)?.nodes[0]?.id).toBe("text-1");
  });

  it("配额写入失败时，新令牌读取当前页内存快照并使旧快照失效", () => {
    const persisted = saveCanvasClipboard(createCanvasClipboardSnapshot("project-a", document, ["image-1"]), () => "44444444-4444-4444-4444-444444444444");
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw new DOMException("quota", "QuotaExceededError"); });
    const memoryOnly = saveCanvasClipboard(createCanvasClipboardSnapshot("project-a", document, ["text-1"]), () => "55555555-5555-5555-5555-555555555555");

    expect(readCanvasClipboard(memoryOnly)?.nodes[0]?.id).toBe("text-1");
    expect(() => readCanvasClipboard(persisted)).toThrow("已失效");
    expect(window.localStorage.getItem("ai-video-reverse-engineer.creativeCanvasClipboard.v1")).toBeNull();
    window.localStorage.setItem("ai-video-reverse-engineer.creativeCanvasClipboard.v1", JSON.stringify({ command: persisted, snapshot: createCanvasClipboardSnapshot("project-a", document, ["image-1"]) }));
    expect(() => readCanvasClipboard(memoryOnly)).toThrow("已失效");
  });
});

describe("嵌套分组剪贴板", () => {
  const nested: CreativeCanvasDocument = { ...document, nodes: [{ id: "leaf", kind: "text", text: "分组文字", parentId: "inner", width: 300, height: 200, freeResize: true, position: { x: 5, y: 6 } }], edges: [], groups: [
    { id: "outer", position: { x: 100, y: 200 }, width: 600, height: 500 },
    { id: "inner", parentId: "outer", position: { x: 20, y: 30 }, width: 400, height: 300 },
  ] };
  function ids() { let count = 0; return () => `new-${++count}`; }
  it("整组复制展开子树而不重复后代，跨项目纯文本保留尺寸及层级", () => {
    const snapshot = createCanvasClipboardSnapshot("p1", nested, ["outer", "leaf"]);
    expect(snapshot.nodes).toHaveLength(1); expect(snapshot.groups).toHaveLength(2);
    const pasted = pasteCanvasClipboardSnapshot(document, snapshot, "p2", [], { x: 500, y: 600 }, ids());
    const outer = pasted.groups.find(group => !group.parentId)!;
    const inner = pasted.groups.find(group => group.parentId)!;
    expect(outer.position).toEqual({ x: 500, y: 600 });
    expect(inner.parentId).toBe(outer.id); expect(inner.position).toEqual({ x: 20, y: 30 });
    expect(pasted.nodes[0]).toMatchObject({ parentId: inner.id, position: { x: 5, y: 6 }, width: 300, height: 200, freeResize: true });
    expect(pasted.selectedIds).toEqual([outer.id]);
  });
  it("单独复制子节点脱离外部父级，保存绝对坐标", () => {
    const snapshot = createCanvasClipboardSnapshot("p1", nested, ["leaf"]);
    expect(snapshot.nodes[0].parentId).toBeUndefined();
    expect(snapshot.nodes[0].position).toEqual({ x: 125, y: 236 });
    const pasted = pasteCanvasClipboardSnapshot(document, snapshot, "p2", [], { x: 900, y: 800 }, ids());
    expect(pasted.nodes[0].position).toEqual({ x: 900, y: 800 });
    expect(pasted.nodes[0].parentId).toBeUndefined();
    const duplicate = duplicateCanvasSubgraph(nested, ["inner"], ids());
    expect(duplicate.groups[0].parentId).toBe("outer");
    expect(duplicate.nodes[0].parentId).toBe(duplicate.groups[0].id);
    expect(duplicate.nodes[0].position).toEqual({ x: 5, y: 6 });
  });
  it("拒绝伪造父级、父级循环、非法尺寸和组内跨项目素材", () => {
    const snapshot = createCanvasClipboardSnapshot("p1", nested, ["outer"]);
    const paste = (value: unknown) => pasteCanvasClipboardSnapshot(document, value, "p2", assets, { x: 0, y: 0 }, ids());
    expect(() => paste({ ...snapshot, nodes: [{ ...snapshot.nodes[0], parentId: "absent" }] })).toThrow("分组引用无效");
    expect(() => paste({ ...snapshot, groups: snapshot.groups!.map(group => group.id === "outer" ? { ...group, parentId: "inner" } : group) })).toThrow("分组引用无效");
    expect(() => paste({ ...snapshot, nodes: [{ ...snapshot.nodes[0], width: Infinity }] })).toThrow("格式错误");
    expect(() => paste({ ...snapshot, nodes: [{ id:"image", kind:"media", parentId:"inner", position:{x:0,y:0}, assetId:"asset-1" }] })).toThrow("不能跨项目");
  });
});
