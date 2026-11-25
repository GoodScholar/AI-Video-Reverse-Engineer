import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { SegmentRetrievalPanel } from "./SegmentRetrievalPanel";
import type { PreproductionAsset } from "./preproductionApi";


const assets: PreproductionAsset[] = [
  { id: "asset-a", name: "咖啡冲泡长视频", kind: "video", role: "motion", url: "/asset-a.mp4", duration: 10 },
];

const candidate = (id: string, start: number, end: number, relevance: number, duplicate: "low" | "high" = "low") => ({
  candidateId: id,
  assetId: "asset-a",
  sourceFingerprint: "fingerprint",
  algorithmVersion: "scene-v1",
  keyframeModelVersion: "frame-v1",
  inPointSeconds: start,
  outPointSeconds: end,
  keyframeTimeSeconds: (start + end) / 2,
  visualRelevance: relevance,
  quality: { score: 0.78, evidence: { width: 640, height: 360, contrast: 0.4 } },
  duplicateRisk: { level: duplicate, similarity: duplicate === "high" ? 0.97 : 0.2, candidateId: duplicate === "high" ? "segment-1" : null },
  evidence: { kind: "keyframe_visual_vector", vectorizerVersion: "fixed-v1", basis: "关键帧像素，不使用素材名称或备注" },
  keyframe: { timeSeconds: (start + end) / 2, url: `/frame-${id}` },
  preview: { url: `/asset-a.mp4#t=${start.toFixed(3)},${end.toFixed(3)}`, startSeconds: start, endSeconds: end },
});

const search = {
  schemaVersion: 1,
  id: "segment-search-1",
  query: "咖啡冲泡动作",
  createdAt: "2026-09-30T00:00:00Z",
  updatedAt: "2026-09-30T00:00:00Z",
  retrievalVersion: "local-visual-retrieval-v1",
  vectorizerVersion: "fixed-v1",
  indexVersion: "index-v1",
  validity: { status: "current" as const, reason: null },
  localCandidates: [
    candidate("segment-1", 0, 2, 1),
    candidate("segment-2", 2, 7, 0.86, "high"),
    candidate("segment-3", 7, 10, 0.5),
  ],
  rerank: null,
  rerankHistory: [],
};

const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { "Content-Type": "application/json" },
});

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn());
});

it("按本地视觉证据返回同源多个可播放候选", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ searches: [] }))
    .mockResolvedValueOnce(response(search, 201));
  render(<SegmentRetrievalPanel projectId="p1" assets={assets} enabled />);

  await userEvent.type(await screen.findByLabelText("片段检索文字"), "咖啡冲泡动作");
  await userEvent.click(screen.getByRole("button", { name: "本地检索" }));

  expect(await screen.findByText("3 个本地候选")).toBeVisible();
  expect(screen.getAllByText("来源：咖啡冲泡长视频")).toHaveLength(3);
  expect(screen.getByText("00:02.000 → 00:07.000")).toBeVisible();
  expect(screen.getByText("视觉相关性 100%")).toBeVisible();
  expect(screen.getAllByText("基础质量 78%")).toHaveLength(3);
  expect(screen.getByText("重复风险 高")).toBeVisible();
  expect(screen.getAllByText("关键帧像素，不使用素材名称或备注")).toHaveLength(3);
  const preview = screen.getByLabelText("咖啡冲泡长视频 检索片段预览 00:02.000 至 00:07.000") as HTMLVideoElement;
  Object.defineProperty(preview, "currentTime", { configurable: true, writable: true, value: 0 });
  fireEvent.loadedMetadata(preview);
  expect(preview.currentTime).toBe(2);
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/projects/p1/segment-searches",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ query: "咖啡冲泡动作", limit: 8 }) }),
  );
});

it("披露实际文字和所选关键帧后才允许豆包复排", async () => {
  const disclosure = {
    searchId: search.id,
    query: search.query,
    indexVersion: search.indexVersion,
    provider: "doubao",
    model: "doubao-seed-2.1-pro",
    configurationRevision: "config-1",
    candidates: [
      { candidateId: "segment-1", assetId: "asset-a", inPointSeconds: 0, outPointSeconds: 2, keyframeTimeSeconds: 1 },
      { candidateId: "segment-3", assetId: "asset-a", inPointSeconds: 7, outPointSeconds: 10, keyframeTimeSeconds: 8.5 },
    ],
    sentContent: {
      text: `复排指令\n查询：${search.query}\n候选：segment-1,segment-3\n媒体上下文`,
      prompt: `复排指令\n查询：${search.query}\n候选：segment-1,segment-3`,
      analysisContext: "媒体上下文",
      keyframeCount: 2,
      keyframes: ["segment-1", "segment-3"],
    },
    notSent: ["完整视频", "音频", "未选择的项目素材与候选"],
    digest: "a".repeat(64),
  };
  const completed = {
    ...search,
    rerank: {
      id: "rerank-1", status: "completed", provider: "doubao", model: disclosure.model,
      configurationRevision: "config-1", indexVersion: search.indexVersion,
      candidateIds: ["segment-1", "segment-3"], createdAt: search.createdAt, completedAt: search.createdAt,
      ranking: [
        { candidateId: "segment-3", reason: "动作与收尾画面更匹配", confidence: 0.92 },
        { candidateId: "segment-1", reason: "商品主体清楚", confidence: 0.75 },
      ],
      error: null, validity: { status: "current", reason: null },
    },
    rerankHistory: [],
  };
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ searches: [search] }))
    .mockResolvedValueOnce(response(disclosure))
    .mockResolvedValueOnce(response({ ...completed, rerankHistory: [completed.rerank] }));
  render(<SegmentRetrievalPanel projectId="p1" assets={assets} enabled />);

  await userEvent.click(await screen.findByLabelText("选择复排候选 segment-1"));
  await userEvent.click(screen.getByLabelText("选择复排候选 segment-3"));
  await userEvent.click(screen.getByRole("button", { name: "预览豆包发送内容" }));

  const disclosureRegion = await screen.findByRole("region", { name: "豆包发送前核对" });
  expect(disclosureRegion).toBeVisible();
  expect(within(disclosureRegion).getByText("火山方舟豆包 · doubao-seed-2.1-pro")).toBeVisible();
  const outboundText = within(disclosureRegion).getByText(/复排指令/, { selector: "pre" });
  expect(outboundText).toHaveTextContent("查询：咖啡冲泡动作");
  expect(outboundText).toHaveTextContent("候选：segment-1,segment-3");
  expect(outboundText).toHaveTextContent("媒体上下文");
  expect(within(disclosureRegion).getByText("00:00.000 → 00:02.000")).toBeVisible();
  expect(within(disclosureRegion).getByText("00:07.000 → 00:10.000")).toBeVisible();
  expect(within(disclosureRegion).getByText("不会发送：完整视频、音频、未选择的项目素材与候选")).toBeVisible();
  const confirm = screen.getByRole("button", { name: "确认并开始豆包复排" });
  expect(confirm).toBeDisabled();
  await userEvent.click(screen.getByLabelText("我已核对本次发送内容"));
  await userEvent.click(confirm);

  expect(await screen.findByText("豆包复排已完成")).toBeVisible();
  expect(screen.getByText("动作与收尾画面更匹配")).toBeVisible();
  expect(screen.getByText("置信度 92%")).toBeVisible();
  const body = JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body));
  expect(body).toEqual({ candidateIds: ["segment-1", "segment-3"], digest: "a".repeat(64), disclosureAccepted: true });
});

it("豆包失败时保留本地候选，并明确没有完成云端复排", async () => {
  const disclosure = {
    searchId: search.id, query: search.query, indexVersion: search.indexVersion,
    provider: "doubao", model: "doubao-seed-2.1-pro", configurationRevision: "config-1",
    candidates: [{ candidateId: "segment-1", assetId: "asset-a", inPointSeconds: 0, outPointSeconds: 2, keyframeTimeSeconds: 1 }],
    sentContent: {
      text: `复排指令\n查询：${search.query}\n候选：segment-1\n媒体上下文`,
      prompt: `复排指令\n查询：${search.query}\n候选：segment-1`,
      analysisContext: "媒体上下文",
      keyframeCount: 1,
      keyframes: ["segment-1"],
    },
    notSent: ["完整视频", "音频", "未选择的项目素材与候选"], digest: "b".repeat(64),
  };
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ searches: [search] }))
    .mockResolvedValueOnce(response(disclosure))
    .mockResolvedValueOnce(response({ detail: { code: "timeout", message: "豆包视觉复排响应超时，请稍后重试。" } }, 504));
  render(<SegmentRetrievalPanel projectId="p1" assets={assets} enabled />);

  await userEvent.click(await screen.findByLabelText("选择复排候选 segment-1"));
  await userEvent.click(screen.getByRole("button", { name: "预览豆包发送内容" }));
  await userEvent.click(await screen.findByLabelText("我已核对本次发送内容"));
  await userEvent.click(screen.getByRole("button", { name: "确认并开始豆包复排" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("豆包视觉复排响应超时，请稍后重试。");
  expect(screen.getByText("云端复排未完成；本地候选仍可浏览和人工采用。")).toBeVisible();
  expect(screen.getByText("3 个本地候选")).toBeVisible();
  expect(screen.queryByText(/已切换/)).not.toBeInTheDocument();
});

it("索引变化后保留历史理由，但停用旧候选媒体和再次复排", async () => {
  const stale = {
    ...search,
    validity: { status: "stale" as const, reason: "segment_index_changed" },
    rerank: {
      id: "rerank-old", status: "completed", provider: "doubao", model: "doubao-seed-2.1-pro",
      configurationRevision: "config-old", indexVersion: "index-v1", candidateIds: ["segment-1"],
      createdAt: search.createdAt, completedAt: search.createdAt,
      ranking: [{ candidateId: "segment-1", reason: "旧结果理由", confidence: 0.8 }], error: null,
      validity: { status: "stale" as const, reason: "segment_index_changed" },
    },
    rerankHistory: [],
  };
  vi.mocked(fetch).mockResolvedValueOnce(response({ searches: [stale] }));
  render(<SegmentRetrievalPanel projectId="p1" assets={assets} enabled />);

  expect(await screen.findByText("片段索引已变化，这次检索和复排仅作为历史证据保留。")).toBeVisible();
  expect(screen.getByText("已过期：片段索引已变化")).toBeVisible();
  expect(screen.getByText("旧结果理由")).toBeVisible();
  expect(screen.queryByLabelText(/检索片段预览/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "预览豆包发送内容" })).toBeDisabled();
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
});

it("切换项目后忽略上一项目尚未完成的检索响应", async () => {
  let resolveSearch!: (value: Response) => void;
  const pendingSearch = new Promise<Response>((resolve) => { resolveSearch = resolve; });
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ searches: [] }))
    .mockReturnValueOnce(pendingSearch)
    .mockResolvedValueOnce(response({ searches: [] }));
  const view = render(<SegmentRetrievalPanel projectId="p1" assets={assets} enabled />);

  await userEvent.type(await screen.findByLabelText("片段检索文字"), "咖啡冲泡动作");
  await userEvent.click(screen.getByRole("button", { name: "本地检索" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  view.rerender(<SegmentRetrievalPanel projectId="p2" assets={assets} enabled />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  await act(async () => {
    resolveSearch(response(search, 201));
    await pendingSearch;
  });

  expect(screen.queryByText("3 个本地候选")).not.toBeInTheDocument();
  expect(screen.getByLabelText("片段检索文字")).toHaveValue("");
  expect(fetch).toHaveBeenLastCalledWith("/api/projects/p2/segment-searches");
});
