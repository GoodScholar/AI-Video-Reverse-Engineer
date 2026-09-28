import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

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
      { id: "shot-b", sceneId: "scene-b", rank: "00000001", title: "开始追逐", duration: 4, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
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
});
