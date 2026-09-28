import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PreproductionWorkspace } from "./preproductionApi";
import { createPreproductionWorkspaceStore } from "./preproductionWorkspaceStore";
import { ShotWorkspaceLayout } from "./ShotWorkspaceLayout";

class TestResizeObserver implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function setViewport(narrow: boolean) {
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    matches: query === "(max-width: 899px)" ? narrow : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

function workspace(): PreproductionWorkspace {
  return {
    schemaVersion: 2,
    revision: 1,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 12, aspect: "16:9", mustPreserve: "服装" },
    assets: [],
    scenes: [{ id: "scene-a", title: "相遇", rank: "00000001", description: "" }],
    shots: [{ id: "shot-a", sceneId: "scene-a", rank: "00000001", title: "雨中相遇", duration: 5, prompt: "", negativePrompt: "", assetIds: [], nodes: [] }],
    workflow: { nodes: [], edges: [] },
    canvasLayout: { scope: { type: "project", id: "project-1" }, layoutRevision: 0, nodes: {} },
    checks: [],
    nodeCatalog: [],
  };
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ShotWorkspaceLayout", () => {
  it("桌面默认分屏，并在三种模式间切换时保留选择和未保存草稿", async () => {
    setViewport(false);
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    store.getState().actions.updateShot("shot-a", (shot) => ({ ...shot, prompt: "保留草稿" }));
    render(<ShotWorkspaceLayout store={store}><div>镜头检查器内容</div></ShotWorkspaceLayout>);

    expect(screen.getByRole("button", { name: "分屏视图" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("complementary", { name: "按场景组织的镜头列表" })).toBeVisible();
    expect(screen.getByRole("region", { name: "镜头关系图" })).toBeVisible();
    expect(screen.getByRole("complementary", { name: "工作区检查器" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "画布视图" }));
    expect(screen.queryByRole("complementary", { name: "按场景组织的镜头列表" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "镜头关系图" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "列表视图" }));
    expect(screen.getByRole("complementary", { name: "按场景组织的镜头列表" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "镜头关系图" })).not.toBeInTheDocument();
    expect(store.getState().entities.shotsById["shot-a"].prompt).toBe("保留草稿");
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a"]));
    expect(store.getState().persistence.dirty).toBe(true);
  });

  it("窄屏首次进入默认列表，并允许打开画布", async () => {
    setViewport(true);
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    render(<ShotWorkspaceLayout store={store}><div>镜头检查器内容</div></ShotWorkspaceLayout>);

    await waitFor(() => expect(store.getState().view.mode).toBe("list"));
    expect(screen.getByRole("complementary", { name: "按场景组织的镜头列表" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "镜头关系图" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "画布视图" }));
    expect(screen.getByRole("region", { name: "镜头关系图" })).toBeVisible();
    expect(screen.queryByRole("complementary", { name: "按场景组织的镜头列表" })).not.toBeInTheDocument();
  });

  it("检查器抽屉可以关闭和重新打开", async () => {
    setViewport(false);
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    render(<ShotWorkspaceLayout store={store}><div>镜头检查器内容</div></ShotWorkspaceLayout>);

    await user.click(screen.getByRole("button", { name: "关闭检查器" }));
    expect(screen.queryByRole("complementary", { name: "工作区检查器" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开检查器" })).toHaveAttribute("aria-expanded", "false");

    await user.click(screen.getByRole("button", { name: "打开检查器" }));
    expect(screen.getByRole("complementary", { name: "工作区检查器" })).toBeVisible();
  });
});
