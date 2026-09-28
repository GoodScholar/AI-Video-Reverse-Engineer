import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SceneShotList } from "./SceneShotList";
import type { PreproductionWorkspace } from "./preproductionApi";
import { createPreproductionWorkspaceStore } from "./preproductionWorkspaceStore";

function workspace(): PreproductionWorkspace {
  return {
    schemaVersion: 2,
    revision: 3,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 12, aspect: "16:9", mustPreserve: "服装" },
    assets: [
      { id: "thumb", name: "雨夜街头", kind: "image", role: "scene", url: "/street.png" },
      { id: "result", name: "镜头候选", kind: "video", role: "motion", url: "/result.mp4", duration: 5 },
    ],
    scenes: [
      { id: "scene-b", title: "追逐", rank: "00000002", description: "" },
      { id: "scene-a", title: "相遇", rank: "00000001", description: "" },
    ],
    shots: [
      { id: "shot-b", sceneId: "scene-b", rank: "00000002", title: "开始追逐", duration: 4, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
      { id: "shot-a", sceneId: "scene-a", rank: "00000001", title: "雨中相遇", duration: 5, prompt: "", negativePrompt: "", assetIds: ["thumb"], resultAssetId: "result", resultVersions: [{ assetId: "result", reviewed: false, planChanged: false }], nodes: [
        { id: "node-a", kind: "reference", input: "asset:thumb", params: {}, status: "failed", error: "素材无法读取", artifacts: [] },
      ] },
    ],
    workflow: { nodes: [], edges: [] },
    canvasLayout: { scope: { type: "project", id: "project-1" }, layoutRevision: 0, nodes: {} },
    checks: [{ level: "error", shotId: "shot-a", nodeId: "node-a", message: "步骤运行失败" }],
    nodeCatalog: [{ kind: "reference", label: "引用素材" }],
  };
}

describe("SceneShotList", () => {
  it("以虚拟列表稳定呈现场景和镜头摘要，不在卡片复制详细表单", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    render(<SceneShotList store={store} />);

    const headings = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(headings).toEqual(["相遇", "追逐"]);
    expect(screen.getByRole("img", { name: "雨中相遇缩略图" })).toHaveAttribute("src", "/street.png");
    expect(screen.getByText("5 秒")).toBeVisible();
    expect(screen.getByText("需处理")).toBeVisible();
    expect(screen.getByText("1 个候选")).toBeVisible();
    expect(screen.getByText("步骤运行失败")).toBeVisible();
    expect(screen.getByRole("button", { name: "打开雨中相遇" })).toBeVisible();
    expect(screen.getByRole("button", { name: "1 雨中相遇 5 秒" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "复制雨中相遇" })).toBeVisible();
    expect(screen.getByRole("button", { name: "定位雨中相遇" })).toBeVisible();
    expect(screen.getByText("更多雨中相遇")).toBeVisible();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("定位折叠场景中的镜头会重新展开并保留未保存编辑", async () => {
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    render(<SceneShotList store={store} />);
    store.getState().actions.updateShot("shot-a", (shot) => ({ ...shot, prompt: "保留的草稿" }));

    await user.click(screen.getByRole("button", { name: "折叠场景相遇" }));
    expect(screen.queryByRole("button", { name: "打开雨中相遇" })).not.toBeInTheDocument();
    act(() => store.getState().actions.selectShot("shot-a"));

    expect(await screen.findByRole("button", { name: "打开雨中相遇" })).toBeVisible();
    expect(store.getState().entities.shotsById["shot-a"].prompt).toBe("保留的草稿");
    expect(store.getState().persistence.dirty).toBe(true);
  });

  it("定位视口外镜头时通过虚拟列表滚动并呈现目标卡片", async () => {
    const longWorkspace = workspace();
    longWorkspace.scenes = [longWorkspace.scenes[0]];
    longWorkspace.shots = Array.from({ length: 30 }, (_, index) => ({
      id: `shot-${index + 1}`,
      sceneId: longWorkspace.scenes[0].id,
      rank: String(index + 1).padStart(8, "0"),
      title: `长列表镜头 ${index + 1}`,
      duration: 2,
      prompt: "",
      negativePrompt: "",
      assetIds: [],
      nodes: [],
    }));
    const store = createPreproductionWorkspaceStore(longWorkspace);
    const view = render(<SceneShotList store={store} />);
    const scroll = view.container.querySelector<HTMLElement>(".scene-shot-list__scroll")!;
    Object.defineProperty(scroll, "scrollTo", { configurable: true, value: vi.fn((options: ScrollToOptions) => {
      scroll.scrollTop = options.top ?? 0;
      queueMicrotask(() => fireEvent.scroll(scroll));
    }) });
    expect(screen.queryByRole("button", { name: "打开长列表镜头 30" })).not.toBeInTheDocument();

    await act(async () => {
      store.getState().actions.selectShot("shot-30");
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(await screen.findByRole("button", { name: "打开长列表镜头 30" })).toBeVisible();
    expect(scroll.scrollTo).toHaveBeenCalled();
  });

  it("支持 Cmd/Ctrl 增减和 Shift 范围选择", async () => {
    const store = createPreproductionWorkspaceStore(workspace());
    render(<SceneShotList store={store} />);

    fireEvent.click(screen.getByRole("button", { name: "2 开始追逐 4 秒" }), { metaKey: true });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a", "shot-b"]));

    fireEvent.click(screen.getByRole("button", { name: "1 雨中相遇 5 秒" }), { metaKey: true });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-b"]));

    fireEvent.click(screen.getByRole("button", { name: "2 开始追逐 4 秒" }), { shiftKey: true });
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-a", "shot-b"]));
  });

  it("流程节点只弱高亮所属镜头，场景选择成为检查器主对象", async () => {
    const user = userEvent.setup();
    const store = createPreproductionWorkspaceStore(workspace());
    render(<SceneShotList store={store} />);
    store.getState().actions.selectShot("shot-b");

    act(() => store.getState().actions.selectNode("shot-a", "node-a", "canvas"));
    expect(screen.getByRole("article", { name: "雨中相遇" })).toHaveClass("is-context");
    expect(screen.getByRole("button", { name: "1 雨中相遇 5 秒" })).toHaveAttribute("aria-pressed", "false");
    expect(store.getState().selection.selectedShotIds).toEqual(new Set(["shot-b"]));

    await user.click(screen.getByRole("button", { name: "折叠场景相遇" }));
    expect(store.getState().selection.primaryEntity).toEqual({ type: "scene", id: "scene-a" });
  });

  it("悬停和键盘焦点只更新瞬时反馈", () => {
    const store = createPreproductionWorkspaceStore(workspace());
    render(<SceneShotList store={store} />);
    const card = screen.getByRole("article", { name: "雨中相遇" });
    const open = screen.getByRole("button", { name: "1 雨中相遇 5 秒" });

    fireEvent.mouseEnter(card);
    expect(card).toHaveClass("is-hovered");
    fireEvent.focus(open);
    expect(card).toHaveClass("is-focused");
    expect(store.getState().persistence.dirty).toBe(false);

    fireEvent.mouseLeave(card);
    fireEvent.blur(open);
    expect(card).not.toHaveClass("is-hovered");
    expect(card).not.toHaveClass("is-focused");
  });
});
