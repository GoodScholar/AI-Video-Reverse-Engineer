import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReproductionFlow } from "./ReproductionFlow";
import type { Project } from "./models";
import type { PreproductionWorkspace } from "./preproductionApi";
const project: Project = { id: "p1", name: "项目", createdAt: "", updatedAt: "", referenceMedia: null, localPreprocessing: null };
const workspace: PreproductionWorkspace = { revision: 0, brief: { inputKind: "depth_video", theme: "", purpose: "", style: "", duration: 0, aspect: "", mustPreserve: "" }, assets: [], shots: [], checks: [], nodeCatalog: [] };
const actions = { disabled: false, dirty: false, onKindChange: vi.fn(), onNavigate: vi.fn(), onImportShots: vi.fn() };
beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());
it("收起流程保留摘要，重开项目恢复折叠偏好且不改变方案", () => {
 const view = render(<ReproductionFlow project={project} workspace={workspace} {...actions} />);
 fireEvent.click(screen.getByRole("button", { name: "收起流程" }));
 expect(screen.getByRole("button", { name: "展开流程" })).toHaveAttribute("aria-expanded", "false");
 expect(screen.queryByRole("radio", { name: "灰度深度视频" })).not.toBeInTheDocument();
 expect(screen.getByText(/灰度深度视频 · 0 个镜头/)).toBeVisible();
 expect(actions.onKindChange).not.toHaveBeenCalled();
 view.unmount();
 const second = render(<ReproductionFlow project={project} workspace={workspace} {...actions} />);
 expect(screen.getByRole("button", { name: "展开流程" })).toBeVisible();
 second.rerender(<ReproductionFlow project={{ ...project, id: "p2" }} workspace={workspace} {...actions} />);
 expect(screen.getByRole("button", { name: "收起流程" })).toHaveAttribute("aria-expanded", "true");
});

it("按已保存检查列出准备缺项并定位镜头，控制素材保持可选", () => {
 const locate = vi.fn();
 const saved = { ...workspace, brief: { ...workspace.brief, inputKind: "reference_video" as const }, shots: [
   { id: "s1", title: "镜头一", duration: 2, prompt: "", negativePrompt: "", assetIds: [], nodes: [] },
 ], checks: [{ level: "warning" as const, code: "shot_prompt_missing", shotId: "s1", message: "镜头尚未填写提示词。" }] };
 render(<ReproductionFlow project={project} workspace={saved} {...actions} dirty onLocateCheck={locate} />);
 expect(screen.getByText(/进度依据已保存版本/)).toBeVisible();
 expect(screen.getByText(/控制素材可选/)).toBeVisible();
 fireEvent.click(screen.getByRole("button", { name: "定位镜头尚未填写提示词。" }));
 expect(locate).toHaveBeenCalledWith(saved.checks[0]);
});

it("回传进度区分已上传、时长合格与人工复核", () => {
 const locate = vi.fn();
 const saved = { ...workspace, assets: [{ id: "v1", name: "结果", kind: "video" as const, role: "motion" as const, url: "/v1", duration: 2 }], shots: [
   { id: "s1", title: "镜头一", duration: 2, prompt: "光线", negativePrompt: "", assetIds: [], nodes: [], resultAssetId: "v1", resultVersions: [{ assetId: "v1", reviewed: false, planChanged: false }] },
 ] };
 render(<ReproductionFlow project={project} workspace={saved} {...actions} onLocateCheck={locate} />);
 expect(screen.getByText(/已回传 1 \/ 1.*时长合格 1.*已人工复核 0/)).toBeVisible();
 fireEvent.click(screen.getByRole("button", { name: "定位镜头一尚未人工复核" }));
 expect(locate).toHaveBeenCalledWith(expect.objectContaining({ code: "result_review_required", shotId: "s1" }));
 expect(screen.getByText(/本应用未验证外部模型运行/)).toBeVisible();
});

it("剪辑阶段显示已保存片段与导出提醒，提醒不算阻断", async () => {
 vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => ({ ok: true, json: async () => url.endsWith("/preflight")
   ? { revision: 2, format: "mp4", ready: true, issues: [{ level: "warning", label: "空白", message: "留白" }] }
   : { revision: 2, settings: { width: 1280, height: 720, fps: 30 }, tracks: [{ id: "t1", name: "主轨", clips: [{ id: "c1", assetId: "v1" }] }], assets: [], runs: [] } })));
 render(<ReproductionFlow project={project} workspace={workspace} {...actions} />);
 fireEvent.click(screen.getByRole("button", { name: "检查剪辑状态" }));
 expect(await screen.findByText(/1 个已保存片段.*可导出.*1 项提醒/)).toBeVisible();
});
