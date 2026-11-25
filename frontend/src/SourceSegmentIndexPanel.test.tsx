import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { SourceSegmentIndexPanel } from "./SourceSegmentIndexPanel";
import type { PreproductionAsset } from "./preproductionApi";


vi.mock("./SegmentRetrievalPanel", () => ({
  SegmentRetrievalPanel: ({ enabled }: { enabled: boolean }) => <div data-testid="segment-retrieval-seam">{enabled ? "检索可用" : "检索不可用"}</div>,
}));

const assets: PreproductionAsset[] = [
  { id: "video-a", name: "咖啡冲泡长视频", kind: "video", role: "motion", url: "/video-a.mp4", duration: 10 },
  { id: "video-b", name: "包装展示", kind: "video", role: "reference", url: "/video-b.mp4", duration: 8 },
  { id: "image-a", name: "商品主图", kind: "image", role: "reference", url: "/image-a.jpg" },
];

const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { "Content-Type": "application/json" },
});

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn());
});

it("从现有项目视频素材批量启动索引并展示排队状态", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ indexes: [] }))
    .mockResolvedValueOnce(response({ indexes: [
      { assetId: "video-a", runId: "run-a", status: "queued", candidates: [], validity: { status: "current", reason: null } },
      { assetId: "video-b", runId: "run-b", status: "queued", candidates: [], validity: { status: "current", reason: null } },
    ] }, 202));
  render(<SourceSegmentIndexPanel projectId="p1" assets={assets} />);

  await screen.findByText("尚未建立片段索引");
  expect(screen.queryByLabelText("选择 商品主图")).not.toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("选择 咖啡冲泡长视频"));
  await userEvent.click(screen.getByLabelText("选择 包装展示"));
  await userEvent.click(screen.getByRole("button", { name: "为 2 个视频建立索引" }));

  expect(await screen.findAllByText("排队中")).toHaveLength(2);
  expect(screen.getAllByRole("status", { name: "索引状态" })).toHaveLength(2);
  const request = vi.mocked(fetch).mock.calls[1];
  expect(request[0]).toBe("/api/projects/p1/source-segments");
  expect(request[1]).toMatchObject({ method: "POST", headers: expect.objectContaining({ "X-AIVRE-Intent": "semantic-analysis" }) });
  expect(JSON.parse(String(request[1]?.body))).toEqual({ assetIds: ["video-a", "video-b"] });
});

it("展示同源多个候选、关键帧和受入点出点约束的预览", async () => {
  vi.mocked(fetch).mockResolvedValue(response({ indexes: [{
    assetId: "video-a",
    runId: "run-a",
    status: "completed",
    algorithmVersion: "scene-v1",
    keyframeModelVersion: "frame-v1",
    validity: { status: "current", reason: null },
    candidates: [
      { id: "segment-1", assetId: "video-a", sourceFingerprint: "abc", algorithmVersion: "scene-v1", keyframeModelVersion: "frame-v1",
        inPointSeconds: 0, outPointSeconds: 2, keyframe: { timeSeconds: 1, url: "/frame-1" },
        preview: { url: "/video-a.mp4#t=0.000,2.000", startSeconds: 0, endSeconds: 2 } },
      { id: "segment-2", assetId: "video-a", sourceFingerprint: "abc", algorithmVersion: "scene-v1", keyframeModelVersion: "frame-v1",
        inPointSeconds: 2, outPointSeconds: 7, keyframe: { timeSeconds: 4.5, url: "/frame-2" },
        preview: { url: "/video-a.mp4#t=2.000,7.000", startSeconds: 2, endSeconds: 7 } },
    ],
  }] }));
  render(<SourceSegmentIndexPanel projectId="p1" assets={assets} />);

  expect(await screen.findByText("2 个素材片段候选")).toBeVisible();
  expect(screen.getAllByText("来源：咖啡冲泡长视频")).toHaveLength(2);
  expect(screen.getByText("00:02.000 → 00:07.000")).toBeVisible();
  expect(screen.getByRole("img", { name: "咖啡冲泡长视频 关键帧 00:04.500" })).toHaveAttribute("src", "/frame-2");
  expect(screen.getByText("候选尚未进入变体；采用后才会成为正式剪辑片段。")).toBeVisible();
  expect(screen.getByTestId("segment-retrieval-seam")).toHaveTextContent("检索可用");

  const preview = screen.getByLabelText("咖啡冲泡长视频 片段预览 00:02.000 至 00:07.000") as HTMLVideoElement;
  expect(preview.getAttribute("src")).toBe("/video-a.mp4#t=2.000,7.000");
  Object.defineProperty(preview, "currentTime", { configurable: true, writable: true, value: 0 });
  fireEvent.loadedMetadata(preview);
  expect(preview.currentTime).toBe(2);
  preview.currentTime = 1;
  fireEvent.timeUpdate(preview);
  expect(preview.currentTime).toBe(2);
  const pause = vi.spyOn(preview, "pause").mockImplementation(() => undefined);
  preview.currentTime = 7.01;
  fireEvent.timeUpdate(preview);
  expect(pause).toHaveBeenCalled();
  expect(preview.currentTime).toBe(7);
});

it("明确展示失效原因，并允许失败或过期索引重试", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ indexes: [{
      assetId: "video-a", runId: "run-old", status: "completed", candidates: [],
      validity: { status: "stale", reason: "source_changed" },
    }, {
      assetId: "video-b", runId: "run-failed", status: "failed", candidates: [],
      validity: { status: "current", reason: null },
      error: { code: "interrupted", message: "应用重启中断了片段索引，可重试。", retryable: true },
    }] }))
    .mockResolvedValueOnce(response({
      assetId: "video-a", runId: "run-new", status: "queued", candidates: [],
      validity: { status: "current", reason: null },
    }, 202));
  render(<SourceSegmentIndexPanel projectId="p1" assets={assets} />);

  expect(await screen.findByText("源文件内容已变化，旧候选不可用于后续选片。")).toBeVisible();
  expect(screen.getByText("旧候选已停用；重新建立索引后才能预览。")).toBeVisible();
  expect(screen.queryByLabelText(/咖啡冲泡长视频 片段预览/)).not.toBeInTheDocument();
  expect(screen.getByText("应用重启中断了片段索引，可重试。")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "重试 咖啡冲泡长视频" }));

  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/projects/p1/source-segments/assets/video-a/retry",
    expect.objectContaining({ method: "POST" }),
  );
  expect(await screen.findByText("排队中")).toBeVisible();
});

it("运行中的索引可以取消", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ indexes: [{
      assetId: "video-a", runId: "run-a", status: "running", candidates: [],
      validity: { status: "current", reason: null },
    }] }))
    .mockResolvedValueOnce(response({
      assetId: "video-a", runId: "run-a", status: "cancelled", candidates: [],
      validity: { status: "current", reason: null },
    }));
  render(<SourceSegmentIndexPanel projectId="p1" assets={assets} />);

  await userEvent.click(await screen.findByRole("button", { name: "取消 咖啡冲泡长视频" }));

  expect(fetch).toHaveBeenLastCalledWith(
    "/api/projects/p1/source-segments/assets/video-a/cancel",
    expect.objectContaining({ method: "POST" }),
  );
  expect(await screen.findByText("已取消")).toBeVisible();
});
