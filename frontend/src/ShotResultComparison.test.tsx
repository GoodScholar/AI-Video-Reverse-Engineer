import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CandidateResultComparison, ShotResultComparison } from "./ShotResultComparison";
import type { PreproductionAsset, PreproductionShot } from "./preproductionApi";
const assets: PreproductionAsset[] = [
  { id: "ref", kind: "video", role: "reference", name: "参考.mp4", url: "/ref.mp4", duration: 10 },
  { id: "result", kind: "video", role: "motion", name: "结果.mp4", url: "/result.mp4", duration: 2 },
];
const shot: PreproductionShot = { id: "s1", title: "镜头", duration: 3, prompt: "", negativePrompt: "", assetIds: ["ref"], resultAssetId: "result", nodes: [{ id: "trim", kind: "trim", input: "asset:ref", params: { start: 4, end: 7 }, status: "pending", artifacts: [] }] };
function loaded() {
 const a = screen.getByLabelText("对比参考视频") as HTMLVideoElement;
 const b = screen.getByLabelText("对比结果视频") as HTMLVideoElement;
 Object.defineProperty(a, "duration", { configurable: true, value: 10 });
 Object.defineProperty(b, "duration", { configurable: true, value: 2 });
 fireEvent.loadedMetadata(a); fireEvent.loadedMetadata(b);
 return [a,b];
}
beforeEach(() => { vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(); vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("从截取范围定位参考，按最短媒体限制同步定位及帧步进", () => {
 render(<ShotResultComparison shot={shot} assets={assets} />);
 expect(screen.getByRole("button", { name: "同步播放" })).toBeDisabled();
 const [a,b] = loaded();
 expect(a.currentTime).toBe(4);
 fireEvent.change(screen.getByLabelText("镜头内定位（秒）"), { target: { value: "1" } });
 expect(a.currentTime).toBe(5); expect(b.currentTime).toBe(1);
 fireEvent.change(screen.getByLabelText("步进帧率"), { target: { value: "25" } });
 fireEvent.click(screen.getByRole("button", { name: "下一帧" }));
 expect(b.currentTime).toBeCloseTo(1.04);
 fireEvent.change(screen.getByLabelText("镜头内定位（秒）"), { target: { value: "8" } });
 expect(b.currentTime).toBe(2); expect(a.currentTime).toBe(6);
 expect(screen.getByText(/共同范围短于镜头计划/)).toBeVisible();
});
it("播放被拒绝时暂停两侧并提示，不留下单边播放", async () => {
 vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new Error("blocked"));
 render(<ShotResultComparison shot={shot} assets={assets} />); loaded();
 await act(async () => fireEvent.click(screen.getByRole("button", { name: "同步播放" })));
 expect(await screen.findByRole("alert")).toHaveTextContent("无法同步播放");
 expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
 expect(screen.getByRole("button", { name: "同步播放" })).toBeVisible();
});
it("多个参考候选时要求选择，不猜测当前镜头来源", () => {
 render(<ShotResultComparison shot={{ ...shot, assetIds: ["ref", "other"], nodes: [] }} assets={[...assets, { ...assets[0], id: "other", url: "/other.mp4" }]} />);
 expect(screen.getByLabelText("对比参考素材")).toHaveValue("");
 expect(screen.queryByRole("button", { name: "同步播放" })).not.toBeInTheDocument();
});

it("播放中按参考片段偏移纠正结果时间，暂停和卸载停止两侧", async () => {
 let tick: FrameRequestCallback = () => undefined;
 vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => { tick = callback; return 1; });
 vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
 const view = render(<ShotResultComparison shot={shot} assets={assets} />);
 const [a,b] = loaded();
 await act(async () => fireEvent.click(screen.getByRole("button", { name: "同步播放" })));
 a.currentTime = 4.8; b.currentTime = .2;
 act(() => tick(0));
 expect(b.currentTime).toBeCloseTo(.8);
 fireEvent.click(screen.getByRole("button", { name: "同步暂停" }));
 expect(cancelAnimationFrame).toHaveBeenCalled();
 const pauses = vi.mocked(HTMLMediaElement.prototype.pause).mock.calls.length;
 view.unmount();
 expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.calls.length).toBeGreaterThan(pauses);
});
it("切换镜头时停止旧播放并重置参考位置", () => {
 const view = render(<ShotResultComparison shot={shot} assets={assets} />);
 const [a] = loaded();
 fireEvent.change(screen.getByLabelText("镜头内定位（秒）"), { target: { value: "1" } });
 view.rerender(<ShotResultComparison shot={{ ...shot, id: "s2", nodes: [{ ...shot.nodes[0], params: { start: 1, end: 4 } }] }} assets={assets} />);
 expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
 const [next] = loaded();
 expect(next).not.toBe(a);
 expect(next.currentTime).toBe(1);
 expect(screen.getByLabelText("镜头内定位（秒）")).toHaveValue(0);
});

it("工作台忙碌禁用操作时停止对比播放", async () => {
 const view = render(<ShotResultComparison shot={shot} assets={assets} />); loaded();
 await act(async () => fireEvent.click(screen.getByRole("button", { name: "同步播放" })));
 expect(screen.getByRole("button", { name: "同步暂停" })).toBeVisible();
 view.rerender(<ShotResultComparison shot={shot} assets={assets} disabled />);
 expect(screen.getByRole("button", { name: "同步播放" })).toBeVisible();
 expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
});

it("启动阶段单侧缓冲时暂停两侧并回到共同位置，迟到启动不恢复播放", async () => {
 let finish: () => void = () => undefined;
 vi.mocked(HTMLMediaElement.prototype.play).mockResolvedValueOnce().mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }));
 render(<ShotResultComparison shot={shot} assets={assets} />);
 const [a,b] = loaded();
 await act(async () => fireEvent.click(screen.getByRole("button", { name: "同步播放" })));
 a.currentTime = 4.7;
 fireEvent.waiting(b);
 expect(screen.getByRole("alert")).toHaveTextContent("缓冲");
 expect(a.currentTime).toBe(4); expect(b.currentTime).toBe(0);
 expect(screen.getByRole("button", { name: "同步播放" })).toBeVisible();
 await act(async () => finish());
 expect(screen.getByRole("button", { name: "同步播放" })).toBeVisible();
});

it("同一镜头任选两个候选同步对照，切换候选不改变采用结果", () => {
 const candidates = [
  { ...assets[1], id: "a", name: "候选A.mp4", url: "/a.mp4" },
  { ...assets[1], id: "b", name: "候选B.mp4", url: "/b.mp4" },
  { ...assets[1], id: "c", name: "候选C.mp4", url: "/c.mp4" },
 ];
 const comparedShot = { ...shot, resultAssetId: "b", resultVersions: candidates.map((asset) => ({ assetId: asset.id, reviewed: false, planChanged: false })) };
 render(<CandidateResultComparison shot={comparedShot} assets={candidates} />);
 expect(screen.getByLabelText("候选 A")).toHaveValue("a");
 expect(screen.getByLabelText("候选 B")).toHaveValue("b");
 expect(screen.getByLabelText("候选 A 视频")).toHaveAttribute("src", "/a.mp4");
 fireEvent.change(screen.getByLabelText("候选 A"), { target: { value: "c" } });
 expect(screen.getByLabelText("候选 A 视频")).toHaveAttribute("src", "/c.mp4");
 expect(comparedShot.resultAssetId).toBe("b");
 const a = screen.getByLabelText("候选 A 视频") as HTMLVideoElement;
 const b = screen.getByLabelText("候选 B 视频") as HTMLVideoElement;
 Object.defineProperty(a, "duration", { configurable: true, value: 2 });
 Object.defineProperty(b, "duration", { configurable: true, value: 3 });
 fireEvent.loadedMetadata(a); fireEvent.loadedMetadata(b);
 fireEvent.change(screen.getByLabelText("镜头内定位（秒）"), { target: { value: "1" } });
 expect(a.currentTime).toBe(1); expect(b.currentTime).toBe(1);
 fireEvent.change(screen.getByLabelText("候选 B"), { target: { value: "c" } });
 expect(screen.queryByLabelText("候选 A 视频")).not.toBeInTheDocument();
 expect(screen.getByText(/请选择两个不同的候选/)).toBeVisible();
});

it("候选文件不可用时不能进入 A/B 播放", () => {
 const comparedShot = { ...shot, resultVersions: [{ assetId: "a", reviewed: false, planChanged: false }, { assetId: "b", reviewed: false, planChanged: false }] };
 render(<CandidateResultComparison shot={comparedShot} assets={[{ ...assets[1], id: "a" }, { ...assets[1], id: "b", available: false }]} />);
 expect(screen.queryByLabelText("候选 A 视频")).not.toBeInTheDocument();
 expect(screen.getByText(/至少两个可播放候选/)).toBeVisible();
});

it("已选候选失效后改用另一可播放候选并停止旧对照", async () => {
 const candidates = ["a", "b", "c"].map((id) => ({ ...assets[1], id, name: `${id}.mp4`, url: `/${id}.mp4` }));
 const comparedShot = { ...shot, resultAssetId: "a", resultVersions: candidates.map((asset) => ({ assetId: asset.id, reviewed: false, planChanged: false })) };
 const view = render(<CandidateResultComparison shot={comparedShot} assets={candidates} />);
 expect(screen.getByLabelText("候选 A")).toHaveValue("b");
 expect(screen.getByLabelText("候选 B")).toHaveValue("a");
 const firstVideo = screen.getByLabelText("候选 A 视频") as HTMLVideoElement;
 for (const video of [firstVideo, screen.getByLabelText("候选 B 视频") as HTMLVideoElement]) {
  Object.defineProperty(video, "duration", { configurable: true, value: 3 });
  fireEvent.loadedMetadata(video);
 }
 await act(async () => fireEvent.click(screen.getByRole("button", { name: "同步播放" })));
 view.rerender(<CandidateResultComparison shot={comparedShot} assets={candidates.map((asset) => asset.id === "b" ? { ...asset, available: false } : asset)} />);
 expect(screen.getByLabelText("候选 A")).toHaveValue("c");
 expect(screen.getByLabelText("候选 B")).toHaveValue("a");
 expect(screen.getByLabelText("候选 A 视频")).toHaveAttribute("src", "/c.mp4");
 expect(screen.getByLabelText("候选 A 视频")).not.toBe(firstVideo);
 expect(screen.getByRole("button", { name: "同步播放" })).toBeVisible();
 expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
});

it("跳过不可用视频后仍使用候选列表中的原始编号", () => {
 const comparedShot = { ...shot, resultVersions: ["a", "b", "c"].map((assetId) => ({ assetId, reviewed: false, planChanged: false })) };
 const candidates = [
  { ...assets[1], id: "a", available: false },
  { ...assets[1], id: "b", name: "第二版.mp4" },
  { ...assets[1], id: "c", name: "第三版.mp4" },
 ];
 render(<CandidateResultComparison shot={comparedShot} assets={candidates} />);
 expect(screen.getAllByRole("option", { name: "候选 2 · 第二版.mp4" })).toHaveLength(2);
 expect(screen.getAllByRole("option", { name: "候选 3 · 第三版.mp4" })).toHaveLength(2);
});

it("没有候选时不占用镜头编辑器的对照空间", () => {
 render(<CandidateResultComparison shot={{ ...shot, resultVersions: [], resultAssetId: null }} assets={assets} />);
 expect(screen.queryByRole("region", { name: "镜头候选 A/B 对照" })).not.toBeInTheDocument();
});
