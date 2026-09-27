import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShotResultVersions } from "./ShotResultVersions";
const shot = { id: "s1", sceneId: "scene-default", rank: "00000001", title: "镜头", duration: 3, prompt: "", negativePrompt: "", assetIds: [], nodes: [], resultAssetId: "v2", resultVersions: [
  { assetId: "v1", reviewed: true, planChanged: false }, { assetId: "v2", reviewed: false, planChanged: true },
] };
const assets = ["v1", "v2"].map((id) => ({ id, name: id + ".mp4", kind: "video" as const, role: "motion" as const, url: "/" + id, duration: 3 }));
afterEach(() => vi.unstubAllGlobals());
it("显示采用和复查状态，切回候选并独立记录人工检查", () => {
 const select = vi.fn(), review = vi.fn();
 render(<ShotResultVersions shot={shot} assets={assets} dirty={false} busy={false} onSelect={select} onReview={review} />);
 expect(screen.getByText("已按当前方案人工检查")).toBeVisible();
 expect(screen.getByText(/方案已变化或缺少关联记录/)).toBeVisible();
 expect(screen.getByRole("button", { name: "采用候选 2" })).toHaveAttribute("aria-pressed", "true");
 fireEvent.click(screen.getByRole("button", { name: "采用候选 1" }));
 expect(select).toHaveBeenCalledWith("v1");
 fireEvent.click(screen.getByRole("button", { name: "确认已检查候选 2" }));
 expect(review).toHaveBeenCalledWith("v2");
});
it("草稿未保存时不允许将旧方案状态标为已检查", () => {
 render(<ShotResultVersions shot={shot} assets={assets} dirty busy={false} onSelect={vi.fn()} onReview={vi.fn()} />);
 expect(screen.getByRole("button", { name: "确认已检查候选 2" })).toBeDisabled();
 expect(screen.getByText(/保存后重新判断检查状态/)).toBeVisible();
});

it("候选展示关联时的方案，历史候选不伪造关联内容", () => {
 const current = { ...shot, resultVersions: [
   { assetId: "v1", reviewed: false, planChanged: true, association: {
     revision: 4, brief: { theme: "旧主题", purpose: "", style: "", duration: 3, aspect: "16:9", mustPreserve: "" },
     shot: { id: "s1", title: "镜头", duration: 3, prompt: "旧提示词", negativePrompt: "" }, assets: [], nodes: [],
   } },
   { assetId: "v2", reviewed: false, planChanged: true },
 ] };
 render(<ShotResultVersions shot={current} assets={assets} dirty={false} busy={false} onSelect={vi.fn()} onReview={vi.fn()} />);
 const details = screen.getAllByText("查看关联时方案");
 fireEvent.click(details[0]); fireEvent.click(details[1]);
 expect(screen.getByText("提示词：旧提示词")).toBeVisible();
 expect(screen.getByText(/历史关联内容不可还原/)).toBeVisible();
 expect(screen.getByText(/不证明外部模型实际使用/)).toBeVisible();
});

it("从历史关联定位仍存在的步骤，但不打开已失效素材", () => {
 const manage = vi.fn(), focusStep = vi.fn();
 const current = { ...shot, nodes: [{ id: "trim", kind: "trim" as const, input: "asset:source", params: { start: 0, end: 1 }, status: "completed" as const, artifacts: [] }], resultVersions: [
   { assetId: "v1", reviewed: false, planChanged: false, association: {
     revision: 4, brief: { theme: "", purpose: "", style: "", duration: 3, aspect: "", mustPreserve: "" },
     shot: { id: "s1", title: "镜头", duration: 3, prompt: "旧提示词", negativePrompt: "" },
     assets: [{ id: "source", name: "原图", kind: "image" as const, role: "reference" as const }],
     nodes: [{ id: "trim", kind: "trim" as const, input: "asset:source", params: { start: 0, end: 1 }, outputs: ["裁切.png"] }],
   } },
 ] };
 render(<ShotResultVersions shot={current} assets={[...assets, { id: "source", name: "原图", kind: "image", role: "reference", url: "/source", available: false } as const]} dirty={false} busy={false} onSelect={vi.fn()} onReview={vi.fn()} onManage={manage} onFocusStep={focusStep} />);
 fireEvent.click(screen.getByText("查看关联时方案"));
 expect(screen.getByText("文件不可用")).toBeVisible();
 expect(screen.queryByRole("button", { name: "定位素材 原图" })).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole("button", { name: "定位步骤 trim" }));
 expect(focusStep).toHaveBeenCalledWith("trim");
 expect(screen.getByText(/裁切.png/)).toBeVisible();
});

it("候选显示当前剪辑片段和历史输出，并可定位当前片段", async () => {
 vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ references: [
   { kind: "timeline", label: "时间线 · 主轨 · 片段 c1", trackId: "t1", clipId: "c1" },
   { kind: "timeline_history", label: "历史输出 r1 · 主轨 · 片段 c1", runId: "r1", trackId: "t1", clipId: "c1" },
 ] }) }));
 const focusClip = vi.fn();
 render(<ShotResultVersions shot={shot} assets={assets} dirty={false} busy={false} onSelect={vi.fn()} onReview={vi.fn()} projectId="p1" onFocusClip={focusClip} />);
 fireEvent.click(screen.getAllByText("查看关联时方案")[0]);
 fireEvent.click(await screen.findByRole("button", { name: "定位时间线 · 主轨 · 片段 c1" }));
 expect(focusClip).toHaveBeenCalledWith("t1", "c1");
 expect(screen.getByText(/历史输出 r1/)).toBeVisible();
});

it("用户可保存候选专属外部制作备注", async () => {
 const saved = { revision: 7, shots: shot.resultVersions };
 const request = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => ({ ok: true, json: async () => init?.method === "PUT" ? saved : { references: [] } }));
 vi.stubGlobal("fetch", request);
 const changed = vi.fn();
 render(<ShotResultVersions shot={shot} assets={assets} dirty={false} busy={false} onSelect={vi.fn()} onReview={vi.fn()} projectId="p1" revision={6} onBusy={vi.fn()} onChanged={changed} />);
 fireEvent.click(screen.getAllByText("查看关联时方案")[0]);
 fireEvent.change(screen.getAllByLabelText(/外部制作备注/)[0], { target: { value: "外部工具制作" } });
 fireEvent.click(screen.getAllByRole("button", { name: "保存候选备注" })[0]);
 await screen.findByText("当前时间线和历史输出中没有引用。");
 expect(request).toHaveBeenCalledWith(expect.stringContaining("/results/v1/note"), expect.objectContaining({ method: "PUT", body: JSON.stringify({ revision: 6, note: "外部工具制作" }) }));
 expect(changed).toHaveBeenCalledWith(saved, "note");
});

it("采用理由独立于外部制作备注保存，历史候选仍可查看", async () => {
 const saved = { revision: 7, shots: shot.resultVersions };
 const request = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => ({ ok: true, json: async () => init?.method === "PUT" ? saved : { references: [] } }));
 vi.stubGlobal("fetch", request);
 const changed = vi.fn();
 const current = { ...shot, resultVersions: [{ ...shot.resultVersions[0], adoptionReason: "动作更自然" }, shot.resultVersions[1]] };
 render(<ShotResultVersions shot={current} assets={assets} dirty={false} busy={false} onSelect={vi.fn()} onReview={vi.fn()} projectId="p1" revision={6} onBusy={vi.fn()} onChanged={changed} />);
 expect(screen.getByText("采用理由：动作更自然")).toBeVisible();
 fireEvent.click(screen.getAllByText("查看关联时方案")[0]);
 fireEvent.change(screen.getAllByLabelText("采用理由（可选）")[0], { target: { value: "构图更稳定" } });
 fireEvent.click(screen.getAllByRole("button", { name: "保存采用理由" })[0]);
 await screen.findByText("当前时间线和历史输出中没有引用。");
 expect(request).toHaveBeenCalledWith(expect.stringContaining("/results/v1/adoption-reason"), expect.objectContaining({ method: "PUT", body: JSON.stringify({ revision: 6, reason: "构图更稳定" }) }));
 expect(changed).toHaveBeenCalledWith(saved, "reason");
});
