import { act, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PreproductionWorkspace } from "./preproductionApi";
import { createPreproductionWorkspaceStore } from "./preproductionWorkspaceStore";

const flow = vi.hoisted(() => ({ props: null as Record<string, any> | null }));
const api = vi.hoisted(() => ({
  getCanvasLayout: vi.fn(),
  saveCanvasLayout: vi.fn(),
}));

vi.mock("./preproductionApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("./preproductionApi")>(),
  getCanvasLayout: api.getCanvasLayout,
  saveCanvasLayout: api.saveCanvasLayout,
}));

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  return {
    Background: () => null,
    Controls: () => null,
    Handle: () => React.createElement("span"),
    MarkerType: { ArrowClosed: "arrowclosed" },
    Position: { Left: "left", Right: "right" },
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) => children,
    SelectionMode: { Partial: "partial" },
    useReactFlow: () => ({ fitView: vi.fn(), setViewport: vi.fn(), screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x, y }) }),
    ReactFlow: (props: Record<string, any>) => {
      flow.props = props;
      return React.createElement("div", { "data-testid": "flow" }, props.nodes.map((node: Record<string, any>) => {
        const Component = props.nodeTypes[node.type];
        return React.createElement(Component, { ...node, key: node.id, selected: false });
      }), props.children);
    },
  };
});

import { WorkflowCanvas } from "./WorkflowCanvas";

function workspace(): PreproductionWorkspace {
  return {
    schemaVersion: 2,
    revision: 7,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 5, aspect: "16:9", mustPreserve: "" },
    assets: [],
    scenes: [{ id: "scene-a", title: "相遇", rank: "00000001", description: "" }],
    shots: [{
      id: "shot-a", sceneId: "scene-a", rank: "00000001", title: "雨中相遇", duration: 5,
      prompt: "", negativePrompt: "", assetIds: [], nodes: [],
    }],
    workflow: { nodes: [], edges: [] },
    canvasLayout: {
      scope: { type: "project", id: "project-1" }, layoutRevision: 2,
      nodes: {
        "scene-a": { x: 0, y: 0, width: 360, height: 220, collapsed: false },
        "shot:shot-a": { x: 40, y: 80, width: 220, height: 112 },
      },
      viewport: { x: 0, y: 0, zoom: 1 },
    },
    checks: [],
    nodeCatalog: [],
  };
}

beforeEach(() => {
  flow.props = null;
  api.getCanvasLayout.mockReset();
  api.saveCanvasLayout.mockReset();
  api.saveCanvasLayout.mockImplementation(async (_projectId, layout) => ({
    ...layout,
    layoutRevision: layout.layoutRevision + 1,
  }));
});

describe("WorkflowCanvas layout persistence", () => {
  it("拖动期间只更新交互状态，结束时只保存最后位置一次", async () => {
    const store = createPreproductionWorkspaceStore(workspace());
    render(<WorkflowCanvas projectId="project-1" store={store} />);
    const shot = () => flow.props!.nodes.find((node: Record<string, any>) => node.id === "shot:shot-a");

    act(() => {
      flow.props!.onNodeDragStart({}, shot());
      flow.props!.onNodeDrag({}, { ...shot(), position: { x: 80, y: 100 } });
      flow.props!.onNodeDrag({}, { ...shot(), position: { x: 150, y: 120 } });
    });
    expect(api.saveCanvasLayout).not.toHaveBeenCalled();

    act(() => flow.props!.onNodeDragStop({}, { ...shot(), position: { x: 150, y: 120 } }));

    await waitFor(() => expect(api.saveCanvasLayout).toHaveBeenCalledTimes(1));
    expect(api.saveCanvasLayout).toHaveBeenCalledWith("project-1", expect.objectContaining({
      layoutRevision: 2,
      nodes: expect.objectContaining({ "shot:shot-a": expect.objectContaining({ x: 150, y: 120 }) }),
    }));
    expect(store.getState().persistence.revision).toBe(7);
    expect(store.getState().persistence.dirty).toBe(false);
  });

  it("场景折叠持久化，并可从布局工具栏撤销和重做", async () => {
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    render(<WorkflowCanvas projectId="project-1" store={store} />);

    await user.click(screen.getByRole("button", { name: "折叠场景 相遇" }));
    await waitFor(() => expect(api.saveCanvasLayout).toHaveBeenCalledTimes(1));
    expect(store.getState().layout.nodes["scene-a"].collapsed).toBe(true);
    expect(screen.queryByRole("article", { name: "制作镜头 雨中相遇" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "撤销布局" }));
    await waitFor(() => expect(api.saveCanvasLayout).toHaveBeenCalledTimes(2));
    expect(store.getState().layout.nodes["scene-a"].collapsed).toBe(false);

    await user.click(screen.getByRole("button", { name: "重做布局" }));
    await waitFor(() => expect(api.saveCanvasLayout).toHaveBeenCalledTimes(3));
    expect(store.getState().layout.nodes["scene-a"].collapsed).toBe(true);
  });

  it("视口结束时保存但不增加布局撤销记录", async () => {
    const store = createPreproductionWorkspaceStore(workspace());
    render(<WorkflowCanvas projectId="project-1" store={store} />);
    await act(async () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));

    act(() => flow.props!.onMoveEnd(null, { x: -90, y: 30, zoom: 1.3 }));

    await waitFor(() => expect(api.saveCanvasLayout).toHaveBeenCalledTimes(1));
    expect(store.getState().layout.viewport).toEqual({ x: -90, y: 30, zoom: 1.3 });
    expect(store.getState().layoutPersistence.historyByScope["project:project-1"] ?? []).toHaveLength(0);
  });

  it("布局 CAS 冲突不强制覆盖，并允许重新读取服务端布局", async () => {
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    const serverLayout = {
      ...workspace().canvasLayout,
      layoutRevision: 3,
      nodes: {
        ...workspace().canvasLayout.nodes,
        "scene-a": { ...workspace().canvasLayout.nodes["scene-a"], x: 240, collapsed: false },
      },
    };
    api.saveCanvasLayout.mockRejectedValueOnce(new Error("画布布局已被更新，请重新读取后重试。"));
    api.getCanvasLayout.mockResolvedValueOnce(serverLayout);
    render(<WorkflowCanvas projectId="project-1" store={store} />);

    await user.click(screen.getByRole("button", { name: "折叠场景 相遇" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("画布布局已被更新");
    expect(store.getState().layout.nodes["scene-a"].collapsed).toBe(true);
    expect(api.getCanvasLayout).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "重新读取布局" }));
    await waitFor(() => expect(store.getState().layout.layoutRevision).toBe(3));
    expect(store.getState().layout.nodes["scene-a"]).toEqual(expect.objectContaining({ x: 240, collapsed: false }));
  });

  it("从列表投放只重新摆放现有 ShotNode，画布新建通过一个领域命令完成", async () => {
    const store = createPreproductionWorkspaceStore(workspace());
    const create = vi.fn((position: { x: number; y: number }) => {
      store.getState().actions.createShot({
        id: "shot-new", sceneId: "scene-a", rank: "00000002", title: "画布镜头", duration: 3,
        prompt: "", negativePrompt: "", assetIds: [], nodes: [],
      }, position);
    });
    render(<WorkflowCanvas projectId="project-1" store={store} onCreateShot={create} />);
    const data = new Map([["application/x-aivre-shot-id", "shot-a"]]);
    const dataTransfer = { getData: (type: string) => data.get(type) ?? "" } as DataTransfer;

    const canvas = screen.getByRole("region", { name: "镜头关系图" });
    const drop = createEvent.drop(canvas, { dataTransfer });
    Object.defineProperties(drop, { clientX: { value: 480 }, clientY: { value: 220 } });
    fireEvent(canvas, drop);

    await waitFor(() => expect(api.saveCanvasLayout).toHaveBeenCalledTimes(1));
    expect(store.getState().layout.nodes["shot:shot-a"]).toMatchObject({ x: 480, y: 220 });
    expect(Object.values(store.getState().entities.shotsById)).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "在画布新建镜头" }));
    expect(create).toHaveBeenCalledWith({ x: 120, y: 120 });
    expect(store.getState().entities.shotsById["shot-new"]).toBeDefined();
    expect(store.getState().layout.nodes["shot:shot-new"]).toMatchObject({ x: 120, y: 120 });
    expect(Object.values(store.getState().entities.workflowNodesById)
      .filter((node) => node.type === "shot" && node.shotId === "shot-new")).toHaveLength(1);
  });
});
