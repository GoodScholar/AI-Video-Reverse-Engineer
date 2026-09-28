import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import type { PreproductionWorkspace } from "./preproductionApi";
import { createPreproductionWorkspaceStore } from "./preproductionWorkspaceStore";
import { shouldHandleCanvasShortcut, WorkflowCanvas } from "./WorkflowCanvas";

class TestResizeObserver implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function workspace(): PreproductionWorkspace {
  return {
    schemaVersion: 2,
    revision: 1,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 12, aspect: "16:9", mustPreserve: "服装" },
    assets: [{ id: "asset-a", name: "街头参考", kind: "image", role: "scene", url: "/street.png" }],
    scenes: [{ id: "scene-a", title: "相遇", rank: "00000001", description: "雨夜街头" }],
    shots: [{
      id: "shot-a", sceneId: "scene-a", rank: "00000001", title: "雨中相遇", duration: 5,
      prompt: "", negativePrompt: "", assetIds: ["asset-a"],
      nodes: [{ id: "trim-a", kind: "trim", input: "asset:asset-a", params: {}, status: "completed", artifacts: [] }],
    }],
    workflow: { nodes: [], edges: [] },
    canvasLayout: {
      scope: { type: "project", id: "project-1" }, layoutRevision: 0,
      nodes: { "scene-a": { x: 0, y: 0, width: 360, height: 220 }, "shot:shot-a": { x: 40, y: 80 } },
    },
    checks: [],
    nodeCatalog: [{ kind: "trim", label: "截取" }],
  };
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe("WorkflowCanvas", () => {
  it("以真实只读画布呈现场景和镜头，不暴露连线、自动排布或生成入口", async () => {
    const store = createPreproductionWorkspaceStore(workspace());
    const { container } = render(<div style={{ width: 900, height: 600 }}><WorkflowCanvas store={store} /></div>);

    expect(await screen.findByRole("region", { name: "镜头关系图" })).toBeVisible();
    expect(screen.getByText("相遇")).toBeVisible();
    expect(screen.getByText("雨中相遇")).toBeVisible();
    expect(screen.getByRole("button", { name: "查看雨中相遇关系" })).toBeVisible();
    expect(screen.getByRole("button", { name: "适配当前范围" })).toBeVisible();
    expect(container.querySelector(".react-flow__handle")).not.toBeInTheDocument();
    expect(screen.queryByText(/自动排布|新建连线|最终 AI 视频生成/)).not.toBeInTheDocument();
  });

  it("从镜头节点进入已有素材与流程关系，并能返回项目范围", async () => {
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    const { container } = render(<div style={{ width: 900, height: 600 }}><WorkflowCanvas store={store} /></div>);

    await user.click(await screen.findByRole("button", { name: "查看雨中相遇关系" }));

    expect(store.getState().view.scope).toEqual({ type: "shot", id: "shot-a" });
    expect(screen.getByRole("button", { name: "返回项目范围" })).toBeVisible();
    expect(screen.getByText("街头参考")).toBeVisible();
    expect(screen.getByText("截取")).toBeVisible();
    expect(screen.getByText("已完成")).toBeVisible();
    expect(container.querySelector('[data-handleid="asset"]')).toHaveClass("workflow-canvas__handle");
    expect(container.querySelector('[data-handleid="input"]')).toHaveClass("workflow-canvas__handle");
    expect(container.querySelector('[data-handleid="output"]')).toHaveClass("workflow-canvas__handle");
    act(() => store.getState().actions.selectNode("shot-a", "trim-a"));
    expect(screen.getByRole("article", { name: "流程节点 截取" })).toHaveClass("is-selected");

    await user.click(screen.getByRole("button", { name: "返回项目范围" }));
    expect(store.getState().view.scope).toEqual({ type: "project" });
    expect(screen.getByText("相遇")).toBeVisible();
  });

  it("文本输入获得焦点时不接管画布快捷键", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    const canvas = document.createElement("div");

    expect(shouldHandleCanvasShortcut(input)).toBe(false);
    expect(shouldHandleCanvasShortcut(textarea)).toBe(false);
    expect(shouldHandleCanvasShortcut(canvas)).toBe(true);
  });
});
