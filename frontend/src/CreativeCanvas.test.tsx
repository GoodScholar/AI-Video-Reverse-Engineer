import { act, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let latestFlowProps: {
  nodes: Array<{ id: string; type?: string; data: unknown; measured?: { width?: number; height?: number }; position?: { x: number; y: number }; parentId?: string }>;
  onNodesChange?: (changes: unknown[]) => void;
  onNodeClick?: (_event: { shiftKey?: boolean; metaKey?: boolean; ctrlKey?: boolean }, node: { id: string }) => void;
  onNodeContextMenu?: (_event: { preventDefault: () => void; clientX: number; clientY: number }, node: { id: string }) => void;
  onPaneContextMenu?: (_event: { preventDefault: () => void; clientX: number; clientY: number }) => void;
  onConnect?: (connection: { source?: string; target?: string; sourceHandle?: string; targetHandle?: string }) => void;
  onNodeDragStart?: () => void;
  onNodeDragStop?: () => void;
  onMoveEnd?: (_event: null, viewport: { x: number; y: number; zoom: number }) => void;
} | undefined;

let imageEditorCallbacks: { onAssetCreated: (value: { asset: { id: string; name: string; kind: "image"; url: string }; derivation: { sourceAssetId: string; operation: "crop"; params: { x: number; y: number; width: number; height: number }; operationId: string; index: number; count: number } }) => void; onClose: () => void } | undefined;
vi.mock("./CreativeCanvasImageEditor", () => ({ CreativeCanvasImageEditor: (props: NonNullable<typeof imageEditorCallbacks>) => { imageEditorCallbacks = props; return <div role="dialog" aria-label="图片几何编辑"><button onClick={props.onClose}>关闭图片编辑</button></div>; } }));
vi.mock("./CreativeCanvasPrompt", () => ({ CreativeCanvasPrompt: ({ value, label, onChange }: { value: string; label: string; onChange: (value: string) => void }) => <textarea aria-label={label} value={value} onChange={event => onChange(event.target.value)} /> }));

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal() as typeof import("@xyflow/react");
  return {
    ...actual,
    ReactFlow: (props: {
      nodes: Array<{ id: string; type?: string; data: unknown; measured?: { width?: number; height?: number }; position?: { x: number; y: number }; parentId?: string }>;
      nodeTypes?: Record<string, React.ComponentType<{ id: string; data: unknown }>>;
      onNodesChange?: (changes: unknown[]) => void;
      onNodeClick?: (_event: { shiftKey?: boolean; metaKey?: boolean; ctrlKey?: boolean }, node: { id: string }) => void;
      onNodeContextMenu?: (_event: { preventDefault: () => void; clientX: number; clientY: number }, node: { id: string }) => void;
      onPaneContextMenu?: (_event: { preventDefault: () => void; clientX: number; clientY: number }) => void;
      onConnect?: (connection: { source?: string; target?: string; sourceHandle?: string; targetHandle?: string }) => void;
      onNodeDragStart?: () => void;
      onNodeDragStop?: () => void;
  onMoveEnd?: (_event: null, viewport: { x: number; y: number; zoom: number }) => void;
    }) => {
      latestFlowProps = props;
      const { nodes, nodeTypes } = props;
      return <div data-testid="flow">{nodes.map(node => {
      const NodeComponent = node.type ? nodeTypes?.[node.type] : undefined;
      return NodeComponent ? <NodeComponent key={node.id} id={node.id} data={node.data} /> : null;
    })}</div>;
    },
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    NodeResizer: () => null,
    useViewport: () => ({ x: 0, y: 0, zoom: 1 }),
    NodeToolbar: ({ children, isVisible }: { children: React.ReactNode; isVisible?: boolean }) => isVisible ? <div data-testid="node-toolbar">{children}</div> : null,
    Handle: ({ id, "aria-label": ariaLabel }: { id?: string; "aria-label"?: string }) => <span aria-label={ariaLabel} data-handle={id} />,
  };
});

import { CreativeCanvas, isCreativeCanvasConnectionValid, pruneReferenceOrders, remapReferenceTokens } from "./CreativeCanvas";
import type { CreativeCanvasDocument, CreativeCanvasWorkspace } from "./creativeCanvasApi";
import { createCanvasClipboardSnapshot, saveCanvasClipboard } from "./creativeCanvasClipboard";

const document: CreativeCanvasDocument = {
  revision: 3,
  nodes: [{ id: "text-1", kind: "text" as const, position: { x: 80, y: 80 }, label: "商品描述", text: "一杯冰咖啡" }],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  settings: {},
};

const canvasUrl = "/api/projects/project-1/creative-canvas";

function workspace(overrides: Partial<CreativeCanvasWorkspace> = {}): CreativeCanvasWorkspace {
  return { document: overrides.document ?? document, runs: overrides.runs ?? [], assets: overrides.assets ?? [], ...(overrides.textRuns ? { textRuns: overrides.textRuns } : {}), ...(overrides.textModels ? { textModels: overrides.textModels } : {}), ...(overrides.batches ? { batches: overrides.batches } : {}) };
}

function response(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200 });
}

function configuredModel(model = "model-a", revision = 1) {
  return { baseUrl: "https://images.example", model, models: [model], credentialState: "configured", revision,
    configVersion: `config-${revision}`, providers: [{ providerId: "default", credentialState: "configured", protocol: "openai-compatible", defaultModelId: model, models: [{ modelId: model }] }],
    modelCatalog: [{ providerId: "default", modelId: model, label: model, mediaType: "image", configured: true,
      capabilitySource: "user-configured", capabilityVersion: "1", capabilities: { imageSizes: ["1024x1024", "768x1024"], imageRatios: ["1:1", "3:4"], maxReferences: 4 } }] };
}
const selectedModel = { providerId: "default", modelId: "model-a", configurationVersion: "config-1" };

async function selectFlowNode(id: string) {
  await waitFor(() => expect(latestFlowProps?.nodes.some(item => item.id === id)).toBe(true));
  const node = latestFlowProps?.nodes.find(item => item.id === id);
  if (!node || !latestFlowProps?.onNodeClick) throw new Error(`Node ${id} is unavailable`);
  await act(async () => { latestFlowProps?.onNodeClick?.({}, node); });
  await waitFor(() => expect(screen.getAllByTestId("node-toolbar")).not.toHaveLength(0));
}

async function addTextFromMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "添加到画布" }));
  await user.click(screen.getByRole("button", { name: "新增文本" }));
}

beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:canvas-test") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: vi.fn(function (this: HTMLDialogElement) { this.setAttribute("open", ""); }) });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: vi.fn(function (this: HTMLDialogElement) { this.removeAttribute("open"); }) });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("CreativeCanvas", () => {
  it.each(["audio", "video"] as const)("替换 %s 素材按上传响应身份写回原节点并保留旧资产", async kind => {
    const oldAsset = { id: "old-media", kind, name: `old.${kind}`, url: "/old-media" };
    const uploaded = { id: "uploaded-media", kind, name: `new.${kind}`, url: "/uploaded-media" };
    const unrelated = { id: "other-upload", kind, name: "另一窗口上传", url: "/other-upload" };
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "media-1", kind: "media", label: "媒体", assetId: oldAsset.id, position: { x: 100, y: 100 } }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/preproduction/assets") && init?.method === "POST") return response({ assets: [oldAsset, unrelated, uploaded], uploadedAssetId: uploaded.id });
      return response(workspace({ document: graph, assets: [oldAsset] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("media-1");
    await user.click(screen.getByRole("button", { name: kind === "audio" ? "替换音频" : "替换视频" }));
    await user.upload(screen.getByLabelText("上传项目素材"), new File(["media"], kind === "audio" ? "new.wav" : "new.mp4", { type: kind === "audio" ? "audio/wav" : "video/mp4" }));
    await waitFor(() => expect((latestFlowProps?.nodes[0].data as { assetId: string }).assetId).toBe(uploaded.id));
    expect(latestFlowProps?.nodes).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿");
  });

  it("上传响应缺少明确素材身份时保留节点且不标记草稿", async () => {
    const oldAsset = { id: "old", kind: "audio" as const, name: "old.wav", url: "/old.wav" };
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "media-1", kind: "media", assetId: "old", position: { x: 0, y: 0 } }] };
    vi.stubGlobal("fetch", vi.fn(async (input, init) => response(String(input).includes("/preproduction/assets") && init?.method === "POST"
      ? { assets: [oldAsset, { ...oldAsset, id: "unknown" }] } : workspace({ document: graph, assets: [oldAsset] }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("media-1");
    await user.click(screen.getByRole("button", { name: "替换音频" }));
    await user.upload(screen.getByLabelText("上传项目素材"), new File(["audio"], "new.wav", { type: "audio/wav" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法定位新素材");
    expect((latestFlowProps?.nodes[0].data as { assetId: string }).assetId).toBe("old");
    expect(screen.getByRole("status")).not.toHaveTextContent("有未保存草稿");
  });

  it("上传晚到不恢复已删除并保存的节点，也不产生假草稿", async () => {
    const oldAsset = { id: "old", kind: "audio" as const, name: "old.wav", url: "/old.wav" };
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "media-1", kind: "media", assetId: "old", position: { x: 0, y: 0 } }] };
    let finishUpload: (response: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      if (String(input).includes("/preproduction/assets") && init?.method === "POST") return new Promise<Response>(resolve => { finishUpload = resolve; });
      if (String(input) === canvasUrl && init?.method === "PUT") return response(workspace({ document: { ...graph, nodes: [], revision: 4 }, assets: [oldAsset] }));
      return response(workspace({ document: graph, assets: [oldAsset] }));
    }));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("media-1");
    await user.click(screen.getByRole("button", { name: "替换音频" }));
    await user.upload(screen.getByLabelText("上传项目素材"), new File(["audio"], "new.wav", { type: "audio/wav" }));
    await act(async () => latestFlowProps?.onNodesChange?.([{ type: "remove", id: "media-1" }]));
    await user.click(screen.getByRole("button", { name: "保存画布" }));
    await waitFor(() => expect(screen.getByRole("status")).not.toHaveTextContent("有未保存草稿"));
    await act(async () => finishUpload(response({ assets: [oldAsset, { ...oldAsset, id: "new" }], uploadedAssetId: "new" })));
    expect(latestFlowProps?.nodes).toHaveLength(0);
    expect(screen.getByRole("status")).not.toHaveTextContent("有未保存草稿");
  });

  it("选择候选使用 run/result 身份与版本，等待期间的提示词草稿不被响应覆盖", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "generate-1", kind: "generate", label: "生成", text: "原提示", position: { x: 0, y: 0 }, params: { ...selectedModel, size: "1024x1024" } }] };
    const asset = { id: "candidate", kind: "image" as const, name: "候选图", url: "/candidate.png" };
    const run = { id: "run-1", nodeId: "generate-1", status: "completed" as const, error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: [asset.id], snapshot: {}, outputItems: [{ resultId: "item:1", assetId: asset.id, mediaType: "image" as const, status: "installed" as const, sourceRunId: "run-1", createdAt: "2026-09-30T00:00:00Z" }] };
    let finishSelect: (value: Response) => void = () => {};
    const fetchMock = vi.fn(async (input, init) => {
      if (String(input).endsWith("/outputs/item%3A1/select")) return new Promise<Response>(resolve => { finishSelect = resolve; });
      if (String(input) === "/api/image-generation/configuration") return response(configuredModel());
      return response(workspace({ document: graph, runs: [run], assets: [asset] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("generate-1");
    await user.click(screen.getByRole("button", { name: "生成历史" }));
    await user.click(screen.getByRole("button", { name: "设为当前输出 候选图" }));
    await user.type(screen.getByRole("textbox", { name: "生成画面提示词" }), "，新草稿");
    await act(async () => finishSelect(response(workspace({ document: { ...graph, revision: 4, nodes: [{ ...graph.nodes[0], outputAssetId: asset.id, currentOutputRef: { runId: run.id, resultId: "item:1" } }] }, runs: [run], assets: [asset] }))));
    const current = latestFlowProps?.nodes[0].data as { text: string; currentOutputRef: unknown };
    expect(current.text).toBe("原提示，新草稿");
    expect(current.currentOutputRef).toEqual({ runId: "run-1", resultId: "item:1" });
    expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿");
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/outputs/item%3A1/select"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ revision: 3 });
  });

  it("取消尚未发送的候选明确提示未提交给供应商", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "generate-1", kind: "generate", label: "生成", text: "商品", position: { x: 0, y: 0 }, params: { ...selectedModel, size: "1024x1024" } }] };
    const pendingRun = { id: "run-queued", nodeId: "generate-1", status: "running" as const, error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: [], snapshot: {}, outputItems: [{ resultId: "item-pending", mediaType: "image" as const, status: "pending" as const, sourceRunId: "run-queued", createdAt: "2026-09-30T00:00:00Z" }] };
    const cancelledRun = { ...pendingRun, status: "cancelled" as const, outputItems: [{ ...pendingRun.outputItems[0], status: "cancelled" as const, cancellationRequested: true, localDisposition: "queued_cancelled" as const, providerDisposition: "not_submitted" as const, billingUnknown: false }] };
    vi.stubGlobal("fetch", vi.fn(async (input) => {
      const path = String(input);
      if (path === "/api/image-generation/configuration") return response(configuredModel());
      if (path.endsWith("/runs/run-queued/outputs/item-pending/cancel")) return response(workspace({ document: graph, runs: [cancelledRun] }));
      return response(workspace({ document: graph, runs: [pendingRun] }));
    }));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "生成历史" }));
    await user.click(screen.getByRole("button", { name: "取消结果 item-pending" }));

    expect(await screen.findByRole("status")).toHaveTextContent("候选已取消，未发送给供应商");
  });

  it("运行中已取消候选的晚到素材不会自动成为当前输出", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "generate-1", kind: "generate", label: "生成", text: "商品", position: { x: 0, y: 0 }, params: { ...selectedModel, size: "1024x1024" } }] };
    const runningRun = { id: "run-cancelled-item", nodeId: "generate-1", status: "unknown" as const, error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: [], snapshot: {}, outputItems: [{ resultId: "item-cancelled", mediaType: "image" as const, status: "unknown" as const, sourceRunId: "run-cancelled-item", createdAt: "2026-09-30T00:00:00Z", cancellationRequested: true }] };
    const lateAsset = { id: "late-cancelled-output", kind: "image" as const, name: "晚到结果", url: "/late.png" };
    const completedRun = { ...runningRun, status: "completed" as const, outputAssetIds: [lateAsset.id], outputItems: [{ ...runningRun.outputItems[0], status: "installed" as const, assetId: lateAsset.id, billingUnknown: true }] };
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      const path = String(input);
      if (path === "/api/image-generation/configuration") return response(configuredModel());
      if (path.endsWith("/nodes/generate-1/runs")) return response(workspace({ document: graph, runs: [{ ...runningRun, idempotencyKey: JSON.parse(String(init?.body)).idempotencyKey }] }));
      if (path.endsWith("/runs/run-cancelled-item/refresh")) return response(workspace({ document: graph, runs: [completedRun], assets: [lateAsset] }));
      return response(workspace({ document: graph }));
    }));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    await user.click(screen.getByRole("button", { name: "生成" }));
    await user.click(screen.getByRole("button", { name: "生成历史" }));
    await user.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect(screen.getAllByRole("img", { name: "晚到结果" }).length).toBeGreaterThan(0));
    await act(async () => { await new Promise(resolve => window.setTimeout(resolve, 0)); });

    const node = latestFlowProps?.nodes.find(item => item.id === "generate-1")?.data as { outputAssetId?: string; currentOutputRef?: unknown };
    expect(node.outputAssetId).toBeUndefined();
    expect(node.currentOutputRef).toBeUndefined();
  });

  it("自动采用上游新结果时递归标记下游生成节点失效", async () => {
    const oldAsset = { id: "old-output", kind: "image" as const, name: "旧结果", url: "/old.png" };
    const newAsset = { id: "new-output", kind: "image" as const, name: "新结果", url: "/new.png" };
    const graph: CreativeCanvasDocument = { ...document, nodes: [
      { id: "generate-a", kind: "generate", label: "上游", text: "A", position: { x: 0, y: 0 }, params: { ...selectedModel, size: "1024x1024" }, outputAssetId: oldAsset.id, currentOutputRef: { runId: "old-run", resultId: "old-item" } },
      { id: "generate-b", kind: "generate", label: "下游", text: "B", position: { x: 320, y: 0 }, params: { ...selectedModel, size: "1024x1024" } },
    ], edges: [{ id: "edge-a-b", source: "generate-a", sourceHandle: "image", target: "generate-b", targetHandle: "image" }] };
    const pendingRun = { id: "new-run", nodeId: "generate-a", status: "unknown" as const, error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: [], snapshot: {}, outputItems: [{ resultId: "new-item", mediaType: "image" as const, status: "unknown" as const, sourceRunId: "new-run", createdAt: "2026-09-30T00:00:00Z" }] };
    const completedRun = { ...pendingRun, status: "completed" as const, outputAssetIds: [newAsset.id], outputItems: [{ ...pendingRun.outputItems[0], status: "installed" as const, assetId: newAsset.id }] };
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      const path = String(input);
      if (path === "/api/image-generation/configuration") return response(configuredModel());
      if (path.endsWith("/nodes/generate-a/runs")) return response(workspace({ document: graph, runs: [{ ...pendingRun, idempotencyKey: JSON.parse(String(init?.body)).idempotencyKey }], assets: [oldAsset] }));
      if (path.endsWith("/runs/new-run/refresh")) return response(workspace({ document: graph, runs: [completedRun], assets: [oldAsset, newAsset] }));
      return response(workspace({ document: graph, assets: [oldAsset] }));
    }));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-a");
    await user.click(screen.getByRole("button", { name: "生成" }));
    await user.click(screen.getByRole("button", { name: "生成历史" }));
    await user.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect((latestFlowProps?.nodes.find(item => item.id === "generate-a")?.data as { outputAssetId?: string }).outputAssetId).toBe(newAsset.id));

    expect(latestFlowProps?.nodes.find(item => item.id === "generate-b")?.data).toEqual(expect.objectContaining({ stale: true }));
  });

  it("生成提交响应丢失后重试沿用同一幂等键", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "generate-1", kind: "generate", label: "生成", text: "商品", position: { x: 0, y: 0 }, params: { ...selectedModel, size: "1024x1024", count: 3 } }] };
    const keys: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      if (String(input) === "/api/image-generation/configuration") return response(configuredModel());
      if (String(input).endsWith("/nodes/generate-1/runs")) { expect(JSON.parse(String(init?.body)).count).toBe(3); keys.push(JSON.parse(String(init?.body)).idempotencyKey); throw new Error("响应丢失"); }
      return response(workspace({ document: graph }));
    }));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("generate-1");
    await user.click(screen.getByRole("button", { name: "生成" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "生成" }));
    expect(keys).toHaveLength(2); expect(keys[0]).toBe(keys[1]);
  });

  it("批量提交选区的生成节点并显示步骤状态及取消入口", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: ["a", "b"].map(id => ({ id, kind: "generate", label: id, text: "商品", params: { ...selectedModel, size: "1024x1024" }, position: { x: 0, y: 0 } })) };
    const batch = { id: "batch-1", canvasId: "default", status: "running" as const, createdAt: "2026-09-30T00:00:00Z", documentRevision: 3, documentSnapshot: graph, steps: [{ nodeId: "a", dependsOn: [], idempotencyKey: "step-a", status: "running" as const }, { nodeId: "b", dependsOn: ["a"], idempotencyKey: "step-b", status: "queued" as const }] };
    const fetchMock = vi.fn(async (input, init) => {
      if (String(input) === "/api/image-generation/configuration") return response(configuredModel());
      if (String(input).endsWith("/batches")) return response(workspace({ document: graph, batches: [batch] }));
      if (String(input).endsWith("/batches/batch-1/cancel")) return response(workspace({ document: graph, batches: [{ ...batch, status: "cancelled" }] }));
      return response(workspace({ document: graph }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    await act(async () => latestFlowProps?.onNodesChange?.([{ id: "a", type: "select", selected: true }, { id: "b", type: "select", selected: true }]));
    await act(async () => latestFlowProps?.onNodeContextMenu?.({ preventDefault: vi.fn(), clientX: 200, clientY: 200 }, { id: "a" }));
    await user.click(screen.getByRole("button", { name: "运行选中生成节点" }));
    expect(await screen.findByText("批量生成 · 执行中")).toBeVisible();
    const call = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/batches"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ revision: 3, nodeIds: ["a", "b"], idempotencyKey: expect.any(String) });
    await user.click(screen.getByRole("button", { name: "取消批次 batch-1" }));
    expect(await screen.findByText("批量生成 · 已取消")).toBeVisible();
  });

  it("多选节点可用快捷键成组，再解组而不改变节点的绝对布局", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [
      { id: "text-a", kind: "text", position: { x: 80, y: 80 }, label: "文案 A", text: "A" },
      { id: "text-b", kind: "text", position: { x: 360, y: 120 }, label: "文案 B", text: "B" },
    ] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    await act(async () => latestFlowProps?.onNodesChange?.([{ id: "text-a", type: "select", selected: true }, { id: "text-b", type: "select", selected: true }]));
    expect(await screen.findByRole("button", { name: "打组" })).toBeInTheDocument();
    await user.keyboard("{Control>}g{/Control}");
    await waitFor(() => expect(latestFlowProps?.nodes.find(node => node.type === "canvasGroup")).toBeDefined());
    const group = latestFlowProps?.nodes.find(node => node.type === "canvasGroup");
    expect(group?.measured?.width).toBeGreaterThan(0);
    expect(group?.measured?.height).toBeGreaterThan(0);
    expect(latestFlowProps?.nodes.filter(node => node.type !== "canvasGroup").map(node => node.parentId)).toEqual([group?.id, group?.id]);
    await act(async () => latestFlowProps?.onNodeClick?.({}, { id: group!.id }));
    await user.click(screen.getByRole("button", { name: "解组" }));
    await waitFor(() => expect(latestFlowProps?.nodes.find(node => node.type === "canvasGroup")).toBeUndefined());
    expect(latestFlowProps?.nodes.map(node => node.position)).toEqual([{ x: 80, y: 80 }, { x: 360, y: 120 }]);
  });

  it("删除分组会删除嵌套子树与相连边", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [
      { id: "text-a", kind: "text", position: { x: 20, y: 20 }, text: "A", parentId: "group-child" },
      { id: "text-b", kind: "text", position: { x: 440, y: 20 }, text: "B" },
    ], groups: [
      { id: "group-root", label: "根组", position: { x: 10, y: 10 }, width: 320, height: 240 },
      { id: "group-child", label: "子组", parentId: "group-root", position: { x: 30, y: 30 }, width: 260, height: 180 },
    ], edges: [{ id: "edge-a", source: "text-a", target: "text-b", sourceHandle: "text", targetHandle: "prompt" }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(4));
    await act(async () => latestFlowProps?.onNodesChange?.([{ id: "group-root", type: "remove" }]));
    await waitFor(() => expect(latestFlowProps?.nodes.map(node => node.id)).toEqual(["text-b"]));
  });

  it("分组从左上角缩放时会补偿直接成员坐标，保持成员绝对位置", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "text-a", kind: "text", parentId: "group-a", position: { x: 20, y: 30 }, text: "A" }], groups: [{ id: "group-a", label: "镜头", position: { x: 10, y: 20 }, width: 300, height: 200 }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    const data = latestFlowProps?.nodes.find(node => node.id === "group-a")?.data as { onResizeStart: (id: string) => void; onResize: (id: string, width: number, height: number, position: { x: number; y: number }) => void };
    await act(async () => {
      data.onResizeStart("group-a");
      latestFlowProps?.onNodesChange?.([
        { id: "group-a", type: "position", position: { x: 30, y: 40 } },
        { id: "text-a", type: "position", position: { x: 0, y: 10 } },
        { id: "group-a", type: "dimensions", dimensions: { width: 280, height: 180 } },
      ]);
      data.onResize("group-a", 280, 180, { x: 30, y: 40 });
    });
    expect(latestFlowProps?.nodes.find(node => node.id === "group-a")?.position).toEqual({ x: 30, y: 40 });
    expect(latestFlowProps?.nodes.find(node => node.id === "text-a")?.position).toEqual({ x: 0, y: 10 });
  });

  it("分组的初始尺寸测量不标记草稿或增加撤销记录", async () => {
    const graph: CreativeCanvasDocument = { ...document, groups: [{ id: "group-a", label: "镜头", position: { x: 10, y: 20 }, width: 300, height: 200 }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    await act(async () => latestFlowProps?.onNodesChange?.([{ id: "group-a", type: "dimensions", dimensions: { width: 300, height: 200 } }]));
    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("已保存");
  });

  it("搜索可发现分组并定位选中项", async () => {
    const graph: CreativeCanvasDocument = { ...document, groups: [{ id: "group-campaign", label: "秋季活动", position: { x: 60, y: 80 }, width: 260, height: 180 }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await screen.findByRole("region", { name: "AI 无限画布" });
    await user.keyboard("{Control>}f{/Control}");
    await user.type(screen.getByRole("textbox", { name: "搜索画布节点" }), "秋季");
    expect(screen.getByRole("option", { name: /秋季活动/ })).toHaveTextContent("分组");
    await user.keyboard("{Enter}");
    expect(screen.queryByRole("dialog", { name: "画布节点搜索" })).not.toBeInTheDocument();
  });

  it("StrictMode 整理布局只计算一次，视口改变不占用撤销步骤", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => response(workspace())));
    const user = userEvent.setup();
    render(<StrictMode><CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} /></StrictMode>);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "整理画布" }));
    expect(latestFlowProps?.nodes[0].position).toEqual(document.nodes[0].position);
    await act(async () => latestFlowProps?.onMoveEnd?.(null, { x: 30, y: 40, zoom: 1.5 }));
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(latestFlowProps?.nodes[0].position).toEqual(document.nodes[0].position);
    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();
  });

  it("派生图片加入节点可撤销，重试不重复，离页后旧结果不进入当前画布", async () => {
    const asset = { id: "source", kind: "image" as const, name: "原图", url: "/source.png" };
    const graph: CreativeCanvasDocument = { ...document, nodes: [{ id: "image-1", kind: "media", position: { x: 10, y: 20 }, assetId: asset.id }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph, assets: [asset] }))));
    const user = userEvent.setup();
    const props = { projectId: "project-1", active: true, onBack: vi.fn(), onOpenAssets: vi.fn(), onDirtyChange: vi.fn() };
    const view = render(<CreativeCanvas {...props} />);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(1));
    await act(async () => latestFlowProps?.onNodeContextMenu?.({ preventDefault: vi.fn(), clientX: 100, clientY: 100 }, { id: "image-1" }));
    await user.click(screen.getByRole("button", { name: "裁切图片" }));
    const callbacks = imageEditorCallbacks!;
    const oldNodeChange = latestFlowProps?.onNodesChange;
    const created = { asset: { id: "cropped", name: "裁切图", kind: "image" as const, url: "/cropped.png" }, derivation: { sourceAssetId: "source", operation: "crop" as const, params: { x: 0, y: 0, width: .5, height: .5 }, operationId: "operation-1", index: 0, count: 1 } };
    await act(async () => { callbacks.onAssetCreated(created); oldNodeChange?.([{ type: "position", id: "image-1", position: { x: 20, y: 30 } }]); });
    expect(latestFlowProps?.nodes).toHaveLength(2);
    await act(async () => callbacks.onAssetCreated(created));
    expect(latestFlowProps?.nodes).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "关闭图片编辑" }));
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(latestFlowProps?.nodes).toHaveLength(1);
    view.rerender(<CreativeCanvas {...props} active={false} />);
    await act(async () => callbacks.onAssetCreated({ ...created, asset: { ...created.asset, id: "late" } }));
    expect(latestFlowProps?.nodes).toHaveLength(1);
  });

  it("尺寸浮层可编辑参数并用 Escape 关闭、恢复触发按钮焦点", async () => {
    const graph = { ...document, nodes: [{ id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "图片生成", params: { ...selectedModel, width: 1024, height: 1024 } }] };
    vi.stubGlobal("fetch", vi.fn(async input => response(String(input) === "/api/image-generation/configuration" ? configuredModel() : workspace({ document: graph }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("generate-1");
    await user.click(screen.getByRole("button", { name: "图片生成设置" }));
    await user.click(screen.getByRole("button", { name: "768x1024" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "768x1024" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "图片生成设置" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "图片生成设置" }));
    expect(screen.getByRole("button", { name: "768x1024" })).toHaveAttribute("aria-pressed", "true");
  });

  it("节点标题支持就地改名、取消和撤销", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await user.dblClick(await screen.findByRole("button", { name: "重命名 商品描述" }));
    const input = screen.getByRole("textbox", { name: "节点名称" });
    await user.clear(input);
    await user.type(input, "商品卖点{Enter}");
    expect(screen.getByRole("button", { name: "重命名 商品卖点" })).toBeInTheDocument();
    await user.dblClick(screen.getByRole("button", { name: "重命名 商品卖点" }));
    await user.type(screen.getByRole("textbox", { name: "节点名称" }), "取消这次{Escape}");
    expect(screen.getByRole("button", { name: "重命名 商品卖点" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(screen.getByRole("button", { name: "重命名 商品描述" })).toBeInTheDocument();
  });

  it("重排或移除引用时按稳定边身份重写提示词令牌", () => {
    expect(remapReferenceTokens("组合 {{ref 1}} 与 {{ref 2}}", ["edge-a", "edge-b"], ["edge-b", "edge-a"])).toBe("组合 {{ref 2}} 与 {{ref 1}}");
    expect(remapReferenceTokens("仅保留 {{ref 1}} / {{ref 2}}", ["edge-a", "edge-b"], ["edge-b"])).toBe("仅保留  / {{ref 1}}");
  });
  it("删除边时依据删除前的完整顺序重写不完整 referenceOrder 的提示词令牌", () => {
    const nodes = [{ id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, text: "主体 {{ref 1}}，风格 {{ref 2}}", referenceOrder: ["edge-a"] }];
    const before = [
      { id: "edge-a", source: "media-a", target: "generate-1", sourceHandle: "image" as const, targetHandle: "image" as const },
      { id: "edge-b", source: "media-b", target: "generate-1", sourceHandle: "image" as const, targetHandle: "image" as const },
    ];
    const after = [before[1]];

    expect(pruneReferenceOrders(nodes, after, before)).toEqual([
      expect.objectContaining({ referenceOrder: ["edge-b"], text: "主体 ，风格 {{ref 1}}" }),
    ]);
  });
  it("剪枝引用顺序不会给媒体节点写入后端不允许的 text 字段", () => {
    const media = { id: "media-1", kind: "media" as const, position: { x: 80, y: 80 }, assetId: "asset-1" };

    const [result] = pruneReferenceOrders([media], []);

    expect(result).toEqual(media);
    expect(result).not.toHaveProperty("text");
  });
  it("新增引用时保留旧边的自然顺序，提示词令牌仍指向原来源", async () => {
    const graph = {
      ...document,
      nodes: [
        ...document.nodes,
        { id: "text-2", kind: "text" as const, position: { x: 80, y: 160 }, text: "风格" },
        { id: "text-3", kind: "text" as const, position: { x: 80, y: 240 }, text: "背景" },
        { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, text: "{{ref 1}} / {{ref 2}}" },
      ],
      edges: [
        { id: "edge-a", source: "text-1", target: "generate-1", sourceHandle: "text" as const, targetHandle: "prompt" as const },
        { id: "edge-b", source: "text-2", target: "generate-1", sourceHandle: "text" as const, targetHandle: "prompt" as const },
      ],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await act(async () => { latestFlowProps?.onConnect?.({ source: "text-3", target: "generate-1", sourceHandle: "text", targetHandle: "prompt" }); });
    const data = latestFlowProps?.nodes.find(node => node.id === "generate-1")?.data as { referenceOrder?: string[]; text?: string };
    expect(data.referenceOrder?.slice(0, 2)).toEqual(["edge-a", "edge-b"]);
    expect(data.text).toBe("{{ref 1}} / {{ref 2}}");
  });
  it("首次读取画布期间禁止写入草稿", async () => {
    let resolveLoad: ((value: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { resolveLoad = resolve; })));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "添加到画布" })).toBeDisabled();
    resolveLoad?.(response(workspace()));
    await waitFor(() => expect(screen.getByRole("button", { name: "添加到画布" })).toBeEnabled());
  });

  it("撤销本地画布编辑时保留服务端 revision", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await addTextFromMenu(user);
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "撤销" }));
    await user.click(screen.getByRole("button", { name: "保存画布" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(canvasUrl, expect.objectContaining({ method: "PUT" })));
    const saveCall = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(([path, init]) => path === canvasUrl && (init as RequestInit | undefined)?.method === "PUT");
    expect(JSON.parse(String((saveCall?.[1] as RequestInit).body)).revision).toBe(3);
  });

  it("保存替换画布文档后保留 XYFlow 的瞬态节点尺寸", async () => {
    const savedDocument = { ...document, revision: 4 };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === canvasUrl && init?.method === "PUT") return Promise.resolve(response(workspace({ document: savedDocument })));
      return Promise.resolve(response(workspace()));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    latestFlowProps?.onNodesChange?.([{ id: "text-1", type: "dimensions", dimensions: { width: 260, height: 118 } }]);
    await waitFor(() => expect(latestFlowProps?.nodes.find(node => node.id === "text-1")?.measured).toEqual({ width: 260, height: 118 }));
    await addTextFromMenu(user);
    await user.click(screen.getByRole("button", { name: "保存画布" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(canvasUrl, expect.objectContaining({ method: "PUT" })));
    expect(latestFlowProps?.nodes.find(node => node.id === "text-1")?.measured).toEqual({ width: 260, height: 118 });
  });

  it("拖动后保存的节点只含画布持久化字段", async () => {
    const graph = { ...document, nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, text: "生成", params: { ...selectedModel, width: 1024, height: 1024 }, outputAssetId: "old-output", referenceOrder: ["edge-1"] }] };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(response(workspace({ document: graph }))));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await screen.findByRole("region", { name: "AI 无限画布" });
    latestFlowProps?.onNodesChange?.([{ id: "generate-1", type: "position", position: { x: 500, y: 100 } }]);
    await user.click(screen.getByRole("button", { name: "保存画布" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(canvasUrl, expect.objectContaining({ method: "PUT" })));
    const call = fetchMock.mock.calls.find(([path, init]) => path === canvasUrl && (init as RequestInit | undefined)?.method === "PUT");
    const node = JSON.parse(String((call?.[1] as RequestInit).body)).nodes.find((item: { id: string }) => item.id === "generate-1");
    expect(node).toEqual(expect.objectContaining({ id: "generate-1", kind: "generate", position: { x: 500, y: 100 }, outputAssetId: "old-output", referenceOrder: [] }));
    expect(node).not.toHaveProperty("latestResult");
    expect(node).not.toHaveProperty("modelOptions");
  });

  it("加载项目画布后添加文本节点会标记为未保存", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "AI 无限画布" })).toBeVisible();
    await addTextFromMenu(user);

    expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿");
    expect(screen.getByRole("dialog", { name: "编辑文本" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "编辑文本内容" })).toBeVisible();
  });

  it("StrictMode 下快速双击生成只提交一个运行请求", async () => {
    const generateDocument = { ...document, nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", text: "商品静物", params: { ...selectedModel, width: 1024, height: 1024 } }] };
    let resolveRun: ((value: Response) => void) | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === canvasUrl) return Promise.resolve(response(workspace({ document: generateDocument })));
      if (path === "/api/image-generation/configuration") return Promise.resolve(response(configuredModel()));
      if (path === `${canvasUrl}/nodes/generate-1/runs`) return new Promise<Response>(resolve => { resolveRun = resolve; });
      throw new Error(`Unexpected request: ${path} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<StrictMode><CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} /></StrictMode>);

    await selectFlowNode("generate-1");
    await user.dblClick(screen.getByRole("button", { name: "生成" }));
    expect(fetchMock.mock.calls.filter(([path]) => path === `${canvasUrl}/nodes/generate-1/runs`)).toHaveLength(1);
    resolveRun?.(response(workspace({ document: generateDocument, runs: [{ id: "new-run", nodeId: "generate-1", status: "queued", error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: [], snapshot: {} }] })));
  });

  it("保存失败后可以再次提交同一生成节点", async () => {
    const generateDocument = { ...document, nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", text: "商品静物", params: { ...selectedModel, width: 1024, height: 1024 } }] };
    let saveAttempts = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === canvasUrl && init?.method === "PUT") {
        saveAttempts += 1;
        return saveAttempts === 1 ? Promise.reject(new Error("保存连接失败")) : Promise.resolve(response(workspace({ document: { ...generateDocument, revision: 4 } })));
      }
      if (path === canvasUrl) return Promise.resolve(response(workspace({ document: generateDocument })));
      if (path === "/api/image-generation/configuration") return Promise.resolve(response(configuredModel()));
      if (path === `${canvasUrl}/nodes/generate-1/runs`) return Promise.resolve(response(workspace({ document: { ...generateDocument, revision: 4 }, runs: [{ id: "new-run", nodeId: "generate-1", status: "queued", error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: [], snapshot: {} }] })));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    await user.type(screen.getByRole("textbox", { name: "商品生成画面提示词" }), "，补充细节");
    await user.click(screen.getByRole("button", { name: "生成" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接本地服务");
    await user.click(screen.getByRole("button", { name: "生成" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => path === `${canvasUrl}/nodes/generate-1/runs`)).toHaveLength(1));
    expect(saveAttempts).toBe(2);
  });

  it("两次独立拖动可分别撤销", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await act(async () => { latestFlowProps?.onNodeDragStart?.(); latestFlowProps?.onNodesChange?.([{ id: "text-1", type: "position", position: { x: 120, y: 100 } }]); latestFlowProps?.onNodeDragStop?.(); });
    await act(async () => { latestFlowProps?.onNodeDragStart?.(); latestFlowProps?.onNodesChange?.([{ id: "text-1", type: "position", position: { x: 180, y: 140 } }]); latestFlowProps?.onNodeDragStop?.(); });
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(latestFlowProps?.nodes.find(node => node.id === "text-1")?.position).toEqual({ x: 120, y: 100 });
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(latestFlowProps?.nodes.find(node => node.id === "text-1")?.position).toEqual({ x: 80, y: 80 });
  });

  it("从生成服务返回后重新激活画布会刷新模型选择", async () => {
    const generateDocument = { ...document, nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", params: { ...selectedModel, width: 1024, height: 1024 } }] };
    let configurationCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input) === canvasUrl) return Promise.resolve(response(workspace({ document: generateDocument })));
      if (String(input) === "/api/image-generation/configuration") {
        configurationCalls += 1;
        return Promise.resolve(response(configuredModel(configurationCalls === 1 ? "model-a" : "model-b", configurationCalls)));
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    }));
    const rendered = render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    expect(await screen.findByRole("option", { name: "model-a" })).toBeVisible();
    rendered.rerender(<CreativeCanvas projectId="project-1" active={false} onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    rendered.rerender(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    expect(await screen.findByRole("option", { name: "model-b" })).toBeVisible();
  });

  it("清空模型选择后保存的文档不会提交空模型值", async () => {
    const generateDocument = { ...document, nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", params: { model: "model-a", width: 1024, height: 1024 } }] };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === canvasUrl && init?.method === "PUT") return Promise.resolve(response(workspace({ document: { ...generateDocument, revision: 4 } })));
      if (path === canvasUrl) return Promise.resolve(response(workspace({ document: generateDocument })));
      if (path === "/api/image-generation/configuration") return Promise.resolve(response(configuredModel()));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    await user.selectOptions(screen.getByRole("combobox", { name: "图片模型" }), "");
    await user.click(screen.getByRole("button", { name: "保存画布" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(canvasUrl, expect.objectContaining({ method: "PUT" })));
    const saveCall = fetchMock.mock.calls.find(([path, init]) => path === canvasUrl && (init as RequestInit | undefined)?.method === "PUT");
    const savedNode = JSON.parse(String((saveCall?.[1] as RequestInit).body)).nodes.find((node: { id: string }) => node.id === "generate-1");
    expect(savedNode.params).not.toHaveProperty("model");
  });

  it("不接受从文本端口连接到图片输入", () => {
    const current = {
      ...document,
      nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 380, y: 80 }, label: "生成图片" }],
    };
    expect(isCreativeCanvasConnectionValid({ source: "text-1", sourceHandle: "text", target: "generate-1", targetHandle: "image" }, current, [])).toBe(false);
  });

  it("文本生成节点在单一 in 端口按边身份接收文本与图片引用", () => {
    const current = {
      ...document,
      nodes: [...document.nodes, { id: "text-target", kind: "text" as const, position: { x: 380, y: 80 }, text: "", prompt: "依据 {{ref 1}}" }, { id: "image-source", kind: "media" as const, position: { x: 80, y: 180 }, assetId: "image-a" }],
    };
    const assets = [{ id: "image-a", name: "参考图", kind: "image" as const, url: "/reference.png" }];

    expect(isCreativeCanvasConnectionValid({ source: "text-1", sourceHandle: "text", target: "text-target", targetHandle: "in" }, current, assets)).toBe(true);
    expect(isCreativeCanvasConnectionValid({ source: "image-source", sourceHandle: "image", target: "text-target", targetHandle: "in" }, current, assets)).toBe(true);
    expect(pruneReferenceOrders(current.nodes, [{ id: "text-edge", source: "text-1", target: "text-target", sourceHandle: "text", targetHandle: "in" }], []).find(node => node.id === "text-target")).toMatchObject({ referenceOrder: ["text-edge"], prompt: "依据 " });
  });

  it("文本节点仅在宿主接入持久运行时展示提示词面板，并可真实创建图片生成分支", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("text-1");
    expect(screen.getByRole("textbox", { name: "商品描述生成提示词" })).toBeVisible();
    expect(screen.getByRole("button", { name: "生成文本" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "从文本创建图片生成" }));
    await waitFor(() => expect(latestFlowProps?.nodes.some(node => node.type === "generate")).toBe(true));
    const generated = latestFlowProps?.nodes.find(node => node.type === "generate");
    expect(generated?.data).toEqual(expect.objectContaining({ referenceOrder: expect.any(Array) }));
  });

  it("从后端目录选择已验证文本模型，保存模型身份后提交文本运行", async () => {
    const graph = { ...document, nodes: [{ ...document.nodes[0], prompt: "写一段介绍" }] };
    const textModels = [{ providerId: "default", providerLabel: "文本服务", modelId: "verified-text", label: "已验证模型", configured: true, configurationVersion: "config-1", textVerified: true, imageVerified: false, maxImageReferences: 0 }];
    let persisted = graph;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") persisted = { ...JSON.parse(String(init.body)), revision: 4 };
      return Promise.resolve(response(workspace({ document: persisted, textModels })));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("text-1");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "文本模型" }), JSON.stringify(["default", "verified-text"]));
    await userEvent.click(screen.getByRole("button", { name: "生成文本" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith("/text-runs"))).toBe(true));
    expect(persisted.nodes[0]).toMatchObject({ providerId: "default", modelId: "verified-text", configurationVersion: "config-1" });
  });

  it("入库文本可从素材库读取为可编辑文本副本，并可撤销", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(String(input) === "/text-asset.txt" ? new Response("入库的文案正文") : response(workspace({ assets: [{ id: "text-asset", name: "文案.txt", kind: "text", url: "/text-asset.txt" }] })))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("text-1");
    await userEvent.click(screen.getByRole("button", { name: "添加到画布" }));
    await userEvent.click(screen.getByRole("button", { name: "选择项目素材" }));
    await userEvent.click(screen.getByRole("button", { name: "加入文案.txt" }));
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    expect(latestFlowProps?.nodes[1].data).toMatchObject({ kind: "text", text: "入库的文案正文", label: "文案.txt" });
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(1));
  });

  it("恢复未知文本运行时显示阻断说明，不能重复生成", async () => {
    const textRun = { id: "text-unknown", nodeId: "text-1", canvasId: "default", status: "unknown" as const, createdAt: "2026-09-30T00:00:00Z", snapshot: { prompt: "介绍", providerId: "default", modelId: "text-model", references: [] }, versions: [] };
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(response(workspace({ textRuns: [textRun] })))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("text-1");
    expect(screen.getByText(/生成状态未知/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成文本" })).toBeDisabled();
  });

  it("文本版本入库按 run/version 身份请求，并把返回的 text/plain 素材写回版本", async () => {
    const textDocument = { ...document, nodes: [{ ...document.nodes[0], prompt: "写一段介绍", providerId: "verified", modelId: "text-1", configurationVersion: "v1" }] };
    const textRun = { id: "text-run-1", nodeId: "text-1", canvasId: "default", status: "completed" as const, createdAt: "2026-09-30T00:00:00Z", snapshot: { prompt: "写一段介绍", providerId: "verified", modelId: "text-1", configurationVersion: "v1", references: [] }, versions: [{ versionId: "text-version-1", runId: "text-run-1", status: "installed" as const, text: "正文", seq: 1, createdAt: "2026-09-30T00:00:00Z" }] };
    const installed = { ...textRun, versions: [{ ...textRun.versions[0], assetId: "canvas-text-1" }] };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === canvasUrl) return Promise.resolve(response(workspace({ document: textDocument, textRuns: [textRun] })));
      if (path === `${canvasUrl}/text-runs/text-run-1/versions/text-version-1/assets`) return Promise.resolve(response(workspace({ document: textDocument, textRuns: [installed], assets: [{ id: "canvas-text-1", name: "创作画布文本.txt", kind: "text", url: "/text.txt", mimeType: "text/plain" }] })));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("text-1");
    await user.click(screen.getByRole("button", { name: "将文本版本 text-version-1 加入素材库" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`${canvasUrl}/text-runs/text-run-1/versions/text-version-1/assets`, expect.objectContaining({ method: "POST", body: "{}" })));
    expect(await screen.findByText("已入库")).toBeVisible();
  });

  it("采用文本版本等待期间继续编辑，保留草稿并标记间接下游过期", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [document.nodes[0],
      { id: "other", kind: "text", position: { x: 0, y: 200 }, text: "原文" },
      { id: "middle", kind: "text", position: { x: 250, y: 0 }, text: "中间文案" },
      { id: "image", kind: "generate", position: { x: 500, y: 0 } },
    ], edges: [
      { id: "to-middle", source: "text-1", target: "middle", sourceHandle: "text", targetHandle: "in" },
      { id: "to-image", source: "middle", target: "image", sourceHandle: "text", targetHandle: "prompt" },
    ] };
    const textRun = { id: "run-text", nodeId: "text-1", canvasId: "default", status: "completed" as const, createdAt: "2026-09-30T00:00:00Z", snapshot: { prompt: "介绍", providerId: "p", modelId: "m", configurationVersion: "v1", references: [] }, versions: [{ versionId: "v-text", runId: "run-text", status: "installed" as const, text: "生成正文", seq: 1, createdAt: "2026-09-30T00:00:00Z" }] };
    let resolveSelection!: (value: Response) => void;
    const pendingSelection = new Promise<Response>(resolve => { resolveSelection = resolve; });
    const fetchMock = vi.fn((input: RequestInfo | URL) => String(input).endsWith("/select") ? pendingSelection : Promise.resolve(response(workspace({ document: graph, textRuns: [textRun] }))));
    vi.stubGlobal("fetch", fetchMock);
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await selectFlowNode("text-1");
    await userEvent.click(screen.getByRole("button", { name: "采用文本版本 v-text" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith("/select"))).toBe(true));
    await act(async () => {
      const other = latestFlowProps?.nodes.find(node => node.id === "other")?.data as { onEdit: (id: string, patch: { text: string }) => void };
      other.onEdit("other", { text: "并发草稿" });
    });
    await act(async () => { resolveSelection(response(workspace({ document: { ...graph, revision: 4, nodes: graph.nodes.map(node => node.id === "text-1" ? { ...node, text: "生成正文", currentTextVersionId: "v-text" } : node.id === "image" ? { ...node, stale: true } : node) }, textRuns: [textRun] }))); });
    await waitFor(() => {
      expect(latestFlowProps?.nodes.find(node => node.id === "text-1")?.data).toMatchObject({ text: "生成正文", currentTextVersionId: "v-text" });
      expect(latestFlowProps?.nodes.find(node => node.id === "other")?.data).toMatchObject({ text: "并发草稿" });
      expect(latestFlowProps?.nodes.find(node => node.id === "image")?.data).toMatchObject({ stale: true });
    });
  });

  it("采用版本后手写文本会解除版本冻结，让文本和图片下游都读取新内容并标记图片失效", async () => {
    const graph = {
      ...document,
      nodes: [
        { ...document.nodes[0], text: "采用正文", currentTextVersionId: "text-version-1" },
        { id: "text-downstream", kind: "text" as const, position: { x: 380, y: 80 }, text: "", prompt: "改写 {{ref 1}}" },
        { id: "generate-downstream", kind: "generate" as const, position: { x: 380, y: 260 }, text: "画面 {{ref 1}}" },
      ],
      edges: [
        { id: "text-in", source: "text-1", target: "text-downstream", sourceHandle: "text" as const, targetHandle: "in" as const },
        { id: "generate-in", source: "text-1", target: "generate-downstream", sourceHandle: "text" as const, targetHandle: "prompt" as const },
      ],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("text-1");
    await user.click(within(screen.getByRole("article", { name: "文本节点 商品描述" })).getByRole("button", { name: "编辑" }));
    const editor = screen.getByRole("textbox", { name: "编辑文本内容" });
    await user.clear(editor); await user.type(editor, "手写正文");
    await waitFor(() => {
      const source = latestFlowProps?.nodes.find(node => node.id === "text-1")?.data as { text?: string; currentTextVersionId?: string };
      const textTarget = latestFlowProps?.nodes.find(node => node.id === "text-downstream")?.data as { references?: Array<{ text?: string }> };
      const imageTarget = latestFlowProps?.nodes.find(node => node.id === "generate-downstream")?.data as { references?: Array<{ text?: string }>; stale?: boolean };
      expect(source).toMatchObject({ text: "手写正文" }); expect(source.currentTextVersionId).toBeUndefined();
      expect(textTarget.references?.[0]?.text).toBe("手写正文"); expect(imageTarget.references?.[0]?.text).toBe("手写正文"); expect(imageTarget.stale).toBe(true);
    });
  });

  it("保存冲突时保留本地草稿并显示重新读取入口", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(workspace()))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: "revision_conflict", message: "版本已更新" } }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await addTextFromMenu(user);
    await user.click(screen.getByRole("button", { name: "保存画布" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("版本已更新");
    expect(screen.getByRole("button", { name: "放弃本地草稿并重新读取" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("编辑节点后保存并从节点生成时，运行请求使用保存返回的新 revision", async () => {
    const generateDocument = {
      ...document,
      nodes: [
        ...document.nodes,
        { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", text: "", params: { ...selectedModel, width: 1024, height: 1024 } },
      ],
    };
    const savedDocument = { ...generateDocument, revision: 4 };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === canvasUrl && init?.method === "PUT") return response(workspace({ document: savedDocument }));
      if (path === `${canvasUrl}/nodes/generate-1/runs`) return response(workspace({ document: savedDocument }));
      if (path === canvasUrl) return response(workspace({ document: generateDocument }));
      if (path === "/api/image-generation/configuration") return response(configuredModel());
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    const prompt = await screen.findByRole("textbox", { name: "商品生成画面提示词" });
    await user.type(prompt, "俯拍商品图");
    await user.click(screen.getByRole("button", { name: "生成" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`${canvasUrl}/nodes/generate-1/runs`, expect.objectContaining({ method: "POST" })));
    const saveCall = fetchMock.mock.calls.find(([path, init]) => path === canvasUrl && (init as RequestInit | undefined)?.method === "PUT");
    const runCall = fetchMock.mock.calls.find(([path]) => path === `${canvasUrl}/nodes/generate-1/runs`);
    expect(JSON.parse(String((saveCall?.[1] as RequestInit).body)).nodes.find((node: { id: string }) => node.id === "generate-1").text).toBe("俯拍商品图");
    expect(JSON.parse(String((runCall?.[1] as RequestInit).body))).toEqual(expect.objectContaining({ revision: 4, idempotencyKey: expect.any(String) }));
  });

  it("连接文本提示词后仍保留可编辑的自身提示词和引用", async () => {
    const connectedDocument = {
      ...document,
      nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", text: "不会使用", params: { ...selectedModel, width: 1024, height: 1024 } }],
      edges: [{ id: "edge-1", source: "text-1", sourceHandle: "text" as const, target: "generate-1", targetHandle: "prompt" as const }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: connectedDocument }))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    const prompt = await screen.findByRole("textbox", { name: "商品生成画面提示词" });
    expect(prompt).toHaveValue("不会使用");
    expect(prompt).not.toHaveAttribute("readonly");
    expect(screen.getByText("参考 1")).toBeVisible();
  });

  it("保存快照后继续编辑时不提交旧提示词生成", async () => {
    const generateDocument = {
      ...document,
      nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", text: "", params: { ...selectedModel, width: 1024, height: 1024 } }],
    };
    let resolveSave: ((value: Response) => void) | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === canvasUrl && init?.method === "PUT") return new Promise<Response>(resolve => { resolveSave = resolve; });
      if (path === canvasUrl) return Promise.resolve(response(workspace({ document: generateDocument })));
      if (path === "/api/image-generation/configuration") return Promise.resolve(response({ baseUrl: null, model: null, credentialState: "unconfigured", revision: null }));
      if (path === `${canvasUrl}/nodes/generate-1/runs`) return Promise.resolve(response(workspace()));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    const prompt = await screen.findByRole("textbox", { name: "商品生成画面提示词" });
    await user.type(prompt, "旧提示词");
    await user.click(screen.getByRole("button", { name: "生成" }));
    await user.type(prompt, "，新补充");
    resolveSave?.(response(workspace({ document: { ...generateDocument, revision: 4 } })));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿"));
    expect(fetchMock).not.toHaveBeenCalledWith(`${canvasUrl}/nodes/generate-1/runs`, expect.anything());
  });

  it("保存中继续编辑会保留草稿并用返回的新 revision 进行下一次保存", async () => {
    const generateDocument = {
      ...document,
      nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成", text: "", params: { ...selectedModel, width: 1024, height: 1024 } }],
    };
    let resolveFirstSave: ((value: Response) => void) | undefined;
    const firstSave = new Promise<Response>(resolve => { resolveFirstSave = resolve; });
    let saves = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === canvasUrl && init?.method === "PUT") {
        saves += 1;
        return saves === 1 ? firstSave : Promise.resolve(response(workspace({ document: { ...generateDocument, revision: 5 } })));
      }
      if (path === canvasUrl) return Promise.resolve(response(workspace({ document: generateDocument })));
      if (path === "/api/image-generation/configuration") return Promise.resolve(response({ baseUrl: null, model: null, credentialState: "unconfigured", revision: null }));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await selectFlowNode("generate-1");
    const prompt = await screen.findByRole("textbox", { name: "商品生成画面提示词" });
    await user.type(prompt, "第一句");
    await user.click(screen.getByRole("button", { name: "保存画布" }));
    await user.type(prompt, "，第二句");
    resolveFirstSave?.(response(workspace({ document: { ...generateDocument, revision: 4 } })));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿"));
    await user.click(screen.getByRole("button", { name: "保存画布" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path, init]) => path === canvasUrl && (init as RequestInit | undefined)?.method === "PUT")).toHaveLength(2));
    const secondSave = fetchMock.mock.calls.filter(([path, init]) => path === canvasUrl && (init as RequestInit | undefined)?.method === "PUT")[1];
    const saved = JSON.parse(String((secondSave[1] as RequestInit).body));
    expect(saved.revision).toBe(4);
    expect(saved.nodes.find((node: { id: string }) => node.id === "generate-1").text).toBe("第一句，第二句");
  });

  it("从画布浮层选择已有项目图片加入媒体节点", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ assets: [{ id: "asset-new", name: "咖啡产品图", kind: "image", url: "/coffee.png", available: true }] }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await user.click(screen.getByRole("button", { name: "添加到画布" }));
    await user.click(screen.getByRole("button", { name: "选择项目素材" }));
    await user.click(screen.getByRole("button", { name: "加入咖啡产品图" }));

    expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿");
  });

  it("在历史结果上继续创作后，保存媒体、生成节点和图片连线", async () => {
    const resultAsset = { id: "result-1", name: "生成结果", kind: "image" as const, url: "/result.png", available: true };
    const sourceDocument = { ...document, nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "初始生成" }] };
    const run = { id: "run-1", nodeId: "generate-1", status: "completed" as const, error: null, promptId: "p-1", createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: ["result-1"], snapshot: {} };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === canvasUrl && init?.method === "PUT") return response(workspace({ document: { ...sourceDocument, revision: 4 }, runs: [run], assets: [resultAsset] }));
      if (path === canvasUrl) return response(workspace({ document: sourceDocument, runs: [run], assets: [resultAsset] }));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await user.click(screen.getByRole("button", { name: /^生成历史/ }));
    expect((await screen.findAllByRole("img", { name: "生成结果" })).some(image => image.getAttribute("src") === "/result.png")).toBe(true);
    await user.click(screen.getByRole("button", { name: /^以结果继续创作/ }));
    await user.click(screen.getByRole("button", { name: "保存画布" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(canvasUrl, expect.objectContaining({ method: "PUT" })));
    const saveCall = fetchMock.mock.calls.find(([path, init]) => path === canvasUrl && (init as RequestInit | undefined)?.method === "PUT");
    const saved = JSON.parse(String((saveCall?.[1] as RequestInit).body));
    const media = saved.nodes.find((node: { kind: string }) => node.kind === "media");
    const generated = saved.nodes.find((node: { kind: string; id: string }) => node.kind === "generate" && node.id !== "generate-1");
    expect(media.assetId).toBe("result-1");
    expect(generated).toBeTruthy();
    expect(saved.edges).toEqual([expect.objectContaining({ source: media.id, target: generated.id, sourceHandle: "image", targetHandle: "image" })]);
  });

  it("原生成节点删除后仍可从历史结果创建分支，但不再显示设为当前", async () => {
    const resultAsset = { id: "result-1", name: "生成结果", kind: "image" as const, url: "/result.png", available: true };
    const run = { id: "run-1", nodeId: "deleted-generate", status: "completed" as const, error: null, promptId: "p-1", createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: ["result-1"], snapshot: {} };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ runs: [run], assets: [resultAsset] }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await user.click(screen.getByRole("button", { name: /^生成历史/ }));
    expect(screen.queryByRole("button", { name: /^设为当前/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^以结果继续创作/ })).toBeVisible();
  });

  it("允许多个提示词和图片引用，以及生成结果接到下游生成节点", () => {
    const graph: CreativeCanvasDocument = {
      ...document,
      nodes: [
        ...document.nodes,
        { id: "text-2", kind: "text", position: { x: 80, y: 160 }, text: "暖光" },
        { id: "source-image", kind: "media", position: { x: 80, y: 240 }, assetId: "source" },
        { id: "generate-1", kind: "generate", position: { x: 420, y: 80 }, text: "主体" },
        { id: "generate-2", kind: "generate", position: { x: 720, y: 80 }, text: "细化" },
      ],
      edges: [
        { id: "prompt-1", source: "text-1", target: "generate-1", sourceHandle: "text", targetHandle: "prompt" },
      ],
    };
    const assets = [{ id: "source", name: "原图", kind: "image" as const, url: "/source.png" }];

    expect(isCreativeCanvasConnectionValid({ source: "text-2", target: "generate-1", sourceHandle: "text", targetHandle: "prompt" }, graph, assets)).toBe(true);
    expect(isCreativeCanvasConnectionValid({ source: "source-image", target: "generate-1", sourceHandle: "image", targetHandle: "image" }, graph, assets)).toBe(true);
    expect(isCreativeCanvasConnectionValid({ source: "generate-1", target: "generate-2", sourceHandle: "image", targetHandle: "image" }, graph, assets)).toBe(true);
  });

  it("拒绝重复边与形成环的生成输出连接", () => {
    const graph: CreativeCanvasDocument = {
      ...document,
      nodes: [
        ...document.nodes,
        { id: "generate-1", kind: "generate", position: { x: 420, y: 80 } },
        { id: "generate-2", kind: "generate", position: { x: 720, y: 80 } },
      ],
      edges: [{ id: "chain", source: "generate-1", target: "generate-2", sourceHandle: "image", targetHandle: "image" }],
    };
    expect(isCreativeCanvasConnectionValid({ source: "generate-1", target: "generate-2", sourceHandle: "image", targetHandle: "image" }, graph, [])).toBe(false);
    expect(isCreativeCanvasConnectionValid({ source: "generate-2", target: "generate-1", sourceHandle: "image", targetHandle: "image" }, graph, [])).toBe(false);
  });

  it("节点右键创建副本时保留入边，并提供与剪贴板不同的节点操作", async () => {
    const graph = {
      ...document,
      nodes: [...document.nodes, { id: "generate-1", kind: "generate" as const, position: { x: 420, y: 80 }, label: "商品生成" }],
      edges: [{ id: "edge-in", source: "text-1", target: "generate-1", sourceHandle: "text" as const, targetHandle: "prompt" as const }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await waitFor(() => expect(latestFlowProps?.nodes.some(node => node.id === "generate-1")).toBe(true));
    const node = latestFlowProps?.nodes.find(item => item.id === "generate-1");
    if (!node || !latestFlowProps?.onNodeContextMenu) throw new Error("missing node menu handler");
    await act(async () => { latestFlowProps?.onNodeContextMenu?.({ preventDefault: () => undefined, clientX: 200, clientY: 180 }, node); });

    const menu = screen.getByRole("menu", { name: "节点操作" });
    expect(menu).toBeVisible();
    expect(within(menu).getByRole("button", { name: "复制节点" })).toBeVisible();
    expect(within(menu).getByRole("button", { name: "创建副本" })).toBeVisible();
    expect(within(menu).getByRole("button", { name: "复制到剪贴板" })).toBeVisible();
    await user.click(within(menu).getByRole("button", { name: "创建副本" }));

    await waitFor(() => expect(latestFlowProps?.nodes.filter(item => item.type === "generate")).toHaveLength(2));
    const copied = latestFlowProps?.nodes.find(item => item.id !== "generate-1" && item.type === "generate");
    expect(latestFlowProps?.nodes.find(item => item.id === copied?.id)?.data).toEqual(expect.objectContaining({ label: "商品生成 副本" }));
    expect(latestFlowProps?.nodes).toEqual(expect.arrayContaining([expect.objectContaining({ id: copied?.id })]));
  });

  it("节点右键只打开一个完整菜单，并支持焦点与 Escape 关闭", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(1));
    const node = latestFlowProps?.nodes[0];
    if (!node || !latestFlowProps?.onNodeContextMenu) throw new Error("missing node menu handler");
    await act(async () => { latestFlowProps?.onNodeContextMenu?.({ preventDefault: () => undefined, clientX: 200, clientY: 180 }, node); });

    const menus = screen.getAllByRole("menu", { name: "节点操作" });
    expect(menus).toHaveLength(1);
    expect(within(menus[0]).getByRole("button", { name: "节点信息" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "节点操作" })).not.toBeInTheDocument();
  });

  it("空白处右键只打开画布添加菜单，并支持焦点与 Escape 关闭", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    if (!latestFlowProps?.onPaneContextMenu) throw new Error("missing pane menu handler");
    await act(async () => { latestFlowProps?.onPaneContextMenu?.({ preventDefault: () => undefined, clientX: 200, clientY: 180 }); });

    const menus = screen.getAllByRole("menu", { name: "画布添加与操作" });
    expect(menus).toHaveLength(1);
    expect(within(menus[0]).getByRole("button", { name: "新增文本" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "画布添加与操作" })).not.toBeInTheDocument();
  });

  it("紧凑添加入口让键盘用户可发现空白处的新增操作", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    await user.click(screen.getByRole("button", { name: "添加到画布" }));
    const menu = screen.getByRole("menu", { name: "添加到画布" });
    expect(within(menu).getByRole("button", { name: "新增图片生成" })).toBeVisible();
  });

  it("离开画布后不会拦截全局撤销或粘贴快捷键", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    const rendered = render(<CreativeCanvas projectId="project-1" active={false} onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await screen.findByRole("region", { name: "AI 无限画布" });

    const undo = new KeyboardEvent("keydown", { key: "z", ctrlKey: true, cancelable: true });
    window.dispatchEvent(undo);
    expect(undo.defaultPrevented).toBe(false);

    const paste = new KeyboardEvent("keydown", { key: "v", ctrlKey: true, cancelable: true });
    window.dispatchEvent(paste);
    expect(paste.defaultPrevented).toBe(false);
    rendered.unmount();
  });

  it("异步读取剪贴板期间离开或切换项目时不会写入旧画布", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    let resolveClipboard: ((value: string) => void) | undefined;
    vi.stubGlobal("navigator", { ...navigator, clipboard: { readText: vi.fn(() => new Promise<string>(resolve => { resolveClipboard = resolve; })) } });
    const command = saveCanvasClipboard(createCanvasClipboardSnapshot("project-1", document, ["text-1"]));
    const rendered = render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await screen.findByRole("region", { name: "AI 无限画布" });

    if (!latestFlowProps?.onPaneContextMenu) throw new Error("missing pane menu handler");
    await act(async () => { latestFlowProps?.onPaneContextMenu?.({ preventDefault: () => undefined, clientX: 200, clientY: 180 }); });
    await userEvent.setup().click(screen.getByRole("button", { name: "从剪贴板粘贴" }));
    rendered.rerender(<CreativeCanvas projectId="project-2" active={false} onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    resolveClipboard?.(command);

    await act(async () => undefined);
    expect(latestFlowProps?.nodes).toHaveLength(1);
  });

  it("异步剪贴板读取在仍显示画布但已切换项目后不会追加旧项目节点", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace())));
    let resolveClipboard: ((value: string) => void) | undefined;
    vi.stubGlobal("navigator", { ...navigator, clipboard: { readText: vi.fn(() => new Promise<string>(resolve => { resolveClipboard = resolve; })) } });
    const command = saveCanvasClipboard(createCanvasClipboardSnapshot("project-1", document, ["text-1"]));
    const rendered = render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    await screen.findByRole("region", { name: "AI 无限画布" });

    if (!latestFlowProps?.onPaneContextMenu) throw new Error("missing pane menu handler");
    await act(async () => { latestFlowProps?.onPaneContextMenu?.({ preventDefault: () => undefined, clientX: 200, clientY: 180 }); });
    await userEvent.setup().click(screen.getByRole("button", { name: "从剪贴板粘贴" }));
    rendered.rerender(<CreativeCanvas projectId="project-2" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);
    resolveClipboard?.(command);

    await act(async () => undefined);
    expect(latestFlowProps?.nodes).toHaveLength(1);
  });



  it("节点点击保留 Shift、Meta 和 Ctrl 的多选语义", async () => {
    const graph: CreativeCanvasDocument = { ...document, nodes: [
      ...document.nodes,
      { id: "text-2", kind: "text", position: { x: 240, y: 80 }, label: "补充文案", text: "第二段" },
    ] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ document: graph }))));
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    const first = latestFlowProps?.nodes.find(node => node.id === "text-1");
    const second = latestFlowProps?.nodes.find(node => node.id === "text-2");
    if (!first || !second || !latestFlowProps?.onNodeClick) throw new Error("missing node click handler");
    await act(async () => { latestFlowProps?.onNodeClick?.({}, first); });
    await act(async () => { latestFlowProps?.onNodeClick?.({ shiftKey: true }, second); });
    expect(latestFlowProps?.nodes.filter(node => (node.data as { selected?: boolean }).selected).map(node => node.id)).toEqual(["text-1", "text-2"]);

    await act(async () => { latestFlowProps?.onNodeClick?.({ metaKey: true }, second); });
    expect(latestFlowProps?.nodes.filter(node => (node.data as { selected?: boolean }).selected).map(node => node.id)).toEqual(["text-1"]);
    await act(async () => { latestFlowProps?.onNodeClick?.({ ctrlKey: true }, second); });
    expect(latestFlowProps?.nodes.filter(node => (node.data as { selected?: boolean }).selected).map(node => node.id)).toEqual(["text-1", "text-2"]);
  });

  it("从画布目录切换时按 canvasId 加载独立工作区", async () => {
    const board = { id: "board-b", name: "分镜 B", createdAt: "2026-09-30T00:00:00Z", updatedAt: null, deletedAt: null };
    const boardDocument: CreativeCanvasDocument = { ...document, nodes: [{ id: "board-b-text", kind: "text", position: { x: 120, y: 120 }, label: "B 文案", text: "画布 B" }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === `${canvasUrl}/canvases`) return response({ canvases: [{ id: "default", name: "默认画布", createdAt: null, updatedAt: null, deletedAt: null }, board] });
      if (path === `${canvasUrl}?canvasId=board-b`) return response(workspace({ document: boardDocument }));
      if (path === canvasUrl) return response(workspace());
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await waitFor(() => expect(latestFlowProps?.nodes.map(node => node.id)).toEqual(["text-1"]));
    await user.click(screen.getByRole("button", { name: /当前项目.*默认画布/ }));
    await user.click(await screen.findByRole("button", { name: "分镜 B" }));

    await waitFor(() => expect(latestFlowProps?.nodes.map(node => node.id)).toEqual(["board-b-text"]));
    expect(fetchMock).toHaveBeenCalledWith(`${canvasUrl}?canvasId=board-b`, {});
  });


  it("保留本地草稿后切回会继续标记为未保存", async () => {
    const board = { id: "board-b", name: "分镜 B", createdAt: "2026-09-30T00:00:00Z", updatedAt: null, deletedAt: null };
    const boardDocument: CreativeCanvasDocument = { ...document, nodes: [{ id: "board-b-text", kind: "text", position: { x: 120, y: 120 }, label: "B 文案", text: "画布 B" }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === `${canvasUrl}/canvases`) return response({ canvases: [{ id: "default", name: "默认画布", createdAt: null, updatedAt: null, deletedAt: null }, board] });
      if (path === `${canvasUrl}?canvasId=board-b`) return response(workspace({ document: boardDocument }));
      if (path === canvasUrl && init?.method === "PUT") return response(workspace());
      if (path === canvasUrl) return response(workspace());
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onDirtyChange = vi.fn();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={onDirtyChange} />);

    await waitFor(() => expect(latestFlowProps?.nodes.map(node => node.id)).toEqual(["text-1"]));
    await addTextFromMenu(user);
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: /当前项目.*默认画布/ }));
    await user.click(await screen.findByRole("button", { name: "分镜 B" }));
    await user.click(screen.getByRole("button", { name: "保留本地草稿并切换" }));
    await waitFor(() => expect(latestFlowProps?.nodes.map(node => node.id)).toEqual(["board-b-text"]));

    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    await user.click(screen.getByRole("button", { name: /当前项目.*分镜 B/ }));
    await user.click(await screen.findByRole("button", { name: "默认画布" }));
    await waitFor(() => expect(latestFlowProps?.nodes).toHaveLength(2));
    expect(screen.getByRole("status")).toHaveTextContent("有未保存草稿");
    expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(latestFlowProps?.nodes).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "重做" }));
    expect(latestFlowProps?.nodes).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "保存画布" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(canvasUrl, expect.objectContaining({ method: "PUT" })));
  });

  it("切换画布会使尚未完成的剪贴板读取失效", async () => {
    const board = { id: "board-b", name: "分镜 B", createdAt: "2026-09-30T00:00:00Z", updatedAt: null, deletedAt: null };
    const boardDocument: CreativeCanvasDocument = { ...document, nodes: [{ id: "board-b-text", kind: "text", position: { x: 120, y: 120 }, label: "B 文案", text: "画布 B" }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === `${canvasUrl}/canvases`) return response({ canvases: [{ id: "default", name: "默认画布", createdAt: null, updatedAt: null, deletedAt: null }, board] });
      if (path === `${canvasUrl}?canvasId=board-b`) return response(workspace({ document: boardDocument }));
      if (path === canvasUrl) return response(workspace());
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    let resolveClipboard: ((value: string) => void) | undefined;
    vi.stubGlobal("navigator", { ...navigator, clipboard: { readText: vi.fn(() => new Promise<string>(resolve => { resolveClipboard = resolve; })) } });
    const command = saveCanvasClipboard(createCanvasClipboardSnapshot("project-1", document, ["text-1"]));
    const user = userEvent.setup();
    render(<CreativeCanvas projectId="project-1" active onBack={vi.fn()} onOpenAssets={vi.fn()} onDirtyChange={vi.fn()} />);

    await screen.findByRole("region", { name: "AI 无限画布" });
    if (!latestFlowProps?.onPaneContextMenu) throw new Error("missing pane menu handler");
    await act(async () => { latestFlowProps?.onPaneContextMenu?.({ preventDefault: () => undefined, clientX: 200, clientY: 180 }); });
    await user.click(screen.getByRole("button", { name: "从剪贴板粘贴" }));
    await user.click(screen.getByRole("button", { name: /当前项目.*默认画布/ }));
    await user.click(await screen.findByRole("button", { name: "分镜 B" }));
    await waitFor(() => expect(latestFlowProps?.nodes.map(node => node.id)).toEqual(["board-b-text"]));

    resolveClipboard?.(command);
    await act(async () => undefined);
    expect(latestFlowProps?.nodes.map(node => node.id)).toEqual(["board-b-text"]);
  });

});
