import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BatchEditor } from "./BatchEditor";

const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const task = {
  id: "task-a", sellingPoint: "省时", script: "先展示操作，再展示结果",
  variant: { id: "variant-a", revision: 0, aspectMode: "9:16", resolvedAspect: "9:16", aspectReason: "商品制作默认比例。", settings: { width: 720, height: 1280, fps: 30 },
    tracks: [{ id: "video", name: "画面", kind: "video", muted: false, hidden: false, clips: [] },
      { id: "audio", name: "声音", kind: "audio", muted: false, hidden: false, clips: [] }], runs: [] },
};

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("BatchEditor", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("单条精修可覆盖比例并把新画布作为新变体保存", async () => {
    const changed = { ...task.variant, revision: 1, aspectMode: "16:9", resolvedAspect: "16:9",
      aspectReason: "单条作品覆盖批次视频比例。", settings: { width: 1280, height: 720, fps: 30 } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task], assets: [] }))
      .mockResolvedValueOnce(response({ variant: changed }));
    render(<BatchEditor projectId="p1" presentation="edit" />);

    await screen.findByRole("heading", { name: "省时 · 短视频变体" });
    await userEvent.click(screen.getByRole("radio", { name: "16:9" }));
    await userEvent.click(screen.getByRole("button", { name: "保存变体" }));

    const payload = JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string);
    expect(payload).toEqual(expect.objectContaining({ aspectMode: "16:9", settings: { width: 1280, height: 720, fps: 30 } }));
    expect(await screen.findByText(/^比例 16:9 · 1280×720/)).toBeVisible();
  });

  it("保存变体成功后通知刷新服务端阶段", async () => {
    const changed = { ...task.variant, revision: 1, aspectMode: "16:9", resolvedAspect: "16:9",
      aspectReason: "单条作品覆盖批次视频比例。", settings: { width: 1280, height: 720, fps: 30 } };
    const onPersistedChange = vi.fn();
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task], assets: [] }))
      .mockResolvedValueOnce(response({ variant: changed }));
    render(<BatchEditor projectId="p1" presentation="edit" onPersistedChange={onPersistedChange} />);

    await screen.findByRole("heading", { name: "省时 · 短视频变体" });
    await userEvent.click(screen.getByRole("radio", { name: "16:9" }));
    await userEvent.click(screen.getByRole("button", { name: "保存变体" }));

    expect(await screen.findByText(/^比例 16:9 · 1280×720/)).toBeVisible();
    expect(onPersistedChange).toHaveBeenCalledTimes(1);
  });

  it("选声与审片呈现之间保留同一条精修草稿，不暴露重复脚本录入", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ tasks: [task], assets: [] }));
    const view = render(<BatchEditor projectId="p1" presentation="edit" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.clear(screen.getByLabelText("当前脚本"));
    await userEvent.type(screen.getByLabelText("当前脚本"), "修改后文案");
    view.rerender(<BatchEditor projectId="p1" presentation="review" />);
    expect(screen.queryByLabelText("客户卖点")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("当前脚本")).not.toBeInTheDocument();
    view.rerender(<BatchEditor projectId="p1" presentation="edit" />);
    expect(screen.getByLabelText("当前脚本")).toHaveValue("修改后文案");
  });

  it("可从批量混剪进入 AI 商品内容创作", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [], assets: [] }))
      .mockResolvedValueOnce(response({ brief: { revision: 0, productName: "", facts: [], audience: "", sellingPoints: [],
        callToAction: "", forbiddenPhrases: [], assetIds: [] }, candidates: [], assets: [] }))
      .mockResolvedValueOnce(response([]));
    render(<BatchEditor projectId="p1" />);

    await userEvent.click(await screen.findByRole("button", { name: "AI 商品内容创作" }));

    expect(await screen.findByRole("heading", { name: "AI 商品内容创作" })).toBeVisible();
  });

  it("标明自动写入的屏幕文案来自已确认脚本", async () => {
    const aigcTask = { ...task, aigcSource: { candidateId: "c1", candidateRevision: 0,
      briefRevision: 1, generationId: "g1", screenCopySource: "script", sellingPoint: "通勤方便",
      brief: { revision: 1, productName: "晴雨杯", facts: [{ id: "f1", text: "杯盖防泼溅" }], audience: "通勤者",
        sellingPoints: ["便携"], callToAction: "查看详情", forbiddenPhrases: ["绝对防水"], assetIds: ["front"] },
      facts: [{ id: "f1", text: "杯盖防泼溅" }], assets: [{ id: "front", name: "杯子正面", kind: "image" }],
      beats: [{ text: "杯盖防泼溅", factIds: ["f1"], assetId: "front" }] },
      variant: { ...task.variant, subtitles: { revision: 0, cues: [
        { id: "line-1", start: 0, end: 2, text: "杯盖防泼溅" }], recognitions: [] } } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [aigcTask], assets: [] }));
    render(<BatchEditor projectId="p1" />);

    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));

    expect(screen.getByText("屏幕文案来自已确认脚本，可在此逐句修改。保存后会烧录进下一次预览。")).toBeVisible();
    await userEvent.click(screen.getByText("创作来源与交接快照"));
    expect(screen.getByText("商品名称：晴雨杯")).toBeVisible();
    expect(screen.getByText("目标受众：通勤者")).toBeVisible();
    expect(screen.getByText("行动引导：查看详情")).toBeVisible();
    expect(screen.getByText("候选 c1 · 第 0 版；简报第 1 版")).toBeVisible();
    expect(screen.getByText("引用素材：杯子正面")).toBeVisible();
  });

  it("收起创作区再打开时保留未保存的简报草稿", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [], assets: [] }))
      .mockResolvedValueOnce(response({ brief: { revision: 0, productName: "", facts: [], audience: "", sellingPoints: [],
        callToAction: "", forbiddenPhrases: [], assetIds: [] }, candidates: [], assets: [] }))
      .mockResolvedValueOnce(response([]));
    render(<BatchEditor projectId="p1" />);
    const toggle = await screen.findByRole("button", { name: "AI 商品内容创作" });
    await userEvent.click(toggle);
    await userEvent.type(await screen.findByLabelText("商品名称"), "晴雨杯");
    await userEvent.click(toggle);
    await userEvent.click(toggle);

    expect(screen.getByLabelText("商品名称")).toHaveValue("晴雨杯");
  });

  it("交付人员录入卖点与脚本后可在任务列表中重新看到它们", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [], assets: [] })).mockResolvedValueOnce(response({ task }));
    render(<BatchEditor projectId="p1" />);

    await userEvent.type(await screen.findByLabelText("客户卖点"), "省时");
    await userEvent.type(screen.getByLabelText("已确认脚本"), "先展示操作，再展示结果");
    await userEvent.click(screen.getByRole("button", { name: "创建批量任务" }));

    expect((await screen.findAllByText("先展示操作，再展示结果"))[0]).toBeVisible();
    const request = vi.mocked(fetch).mock.calls[1];
    expect(request[0]).toContain("/api/projects/p1/batch-edits");
    expect(JSON.parse(request[1]?.body as string)).toEqual({ sellingPoint: "省时", script: "先展示操作，再展示结果" });
  });

  it("可从多段素材编排变体并保存片段参数", async () => {
    const assets = [
      { id: "shot", name: "开场", kind: "video", duration: 4, url: "/shot.mp4" },
      { id: "shot2", name: "效果", kind: "video", duration: 5, url: "/shot2.mp4" },
      { id: "music", name: "配乐", kind: "audio", duration: 10, url: "/music.wav" },
    ];
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task], assets }))
      .mockResolvedValueOnce(response({ variant: { ...task.variant, revision: 1 } }));
    render(<BatchEditor projectId="p1" />);

    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("button", { name: "添加素材 开场" }));
    await userEvent.click(screen.getByRole("button", { name: "添加素材 效果" }));
    await userEvent.click(screen.getByRole("button", { name: "添加素材 配乐" }));
    await userEvent.click(screen.getByRole("button", { name: "上移 效果" }));
    await userEvent.clear(screen.getByLabelText("效果 入点（秒）"));
    await userEvent.type(screen.getByLabelText("效果 入点（秒）"), "0.5");
    await userEvent.click(screen.getByRole("button", { name: "保存变体" }));

    const request = vi.mocked(fetch).mock.calls[1];
    expect(request[0]).toContain("/batch-edits/task-a/variant");
    const body = JSON.parse(request[1]?.body as string);
    expect(body.revision).toBe(0);
    expect(body.tracks[0].clips.map((clip: { assetId: string }) => clip.assetId)).toEqual(["shot2", "shot"]);
    expect(body.tracks[0].clips[0].inPoint).toBe(0.5);
    expect(body.tracks[0].clips.map((clip: { start: number }) => clip.start)).toEqual([0, 3]);
    expect(body.tracks[1].clips[0].assetId).toBe("music");
  });

  it("只能预览已保存版本，并能播放完成的预览", async () => {
    const saved = { ...task, variant: { ...task.variant, revision: 1, tracks: [
      { ...task.variant.tracks[0], clips: [{ id: "clip-a", assetId: "shot", start: 0, inPoint: 0, duration: 2,
        speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 }] }, task.variant.tracks[1],
    ] } };
    const queued = { ...saved.variant, runs: [{ id: "run-a", revision: 1, status: "queued", error: null }] };
    const completed = { ...saved, variant: { ...saved.variant, runs: [{ id: "run-a", revision: 1, status: "completed", error: null, url: "/preview.mp4" }] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [saved], assets: [{ id: "shot", name: "开场", kind: "video", duration: 4, url: "/shot.mp4" }] }))
      .mockResolvedValueOnce(response({ variant: queued }))
      .mockResolvedValueOnce(response({ tasks: [completed], assets: [] }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.clear(screen.getByLabelText("开场 入点（秒）"));
    await userEvent.type(screen.getByLabelText("开场 入点（秒）"), "0.5");
    expect(screen.getByRole("button", { name: "生成预览" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("开场 入点（秒）"));
    await userEvent.type(screen.getByLabelText("开场 入点（秒）"), "0");
    await userEvent.click(screen.getByRole("button", { name: "生成预览" }));
    expect(await screen.findByText("排队中")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "刷新预览状态" }));
    expect(await screen.findByLabelText("短视频变体预览")).toHaveAttribute("src", "/preview.mp4");
  });

  it("切换项目后忽略旧项目迟到的任务创建结果", async () => {
    const pending = deferred<Response>();
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [], assets: [] }))
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(response({ tasks: [], assets: [] }));
    const view = render(<BatchEditor projectId="p1" />);
    await userEvent.type(await screen.findByLabelText("客户卖点"), "省时");
    await userEvent.type(screen.getByLabelText("已确认脚本"), "演示操作");
    await userEvent.click(screen.getByRole("button", { name: "创建批量任务" }));
    view.rerender(<BatchEditor projectId="p2" />);
    await act(async () => pending.resolve(response({ task })));
    expect(screen.queryByRole("button", { name: "编辑 省时" })).not.toBeInTheDocument();
  });

  it("初次任务列表尚未读取时不提交新任务", async () => {
    const pending = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    render(<BatchEditor projectId="p1" />);
    await userEvent.type(screen.getByLabelText("客户卖点"), "省时");
    await userEvent.type(screen.getByLabelText("已确认脚本"), "演示操作");
    expect(screen.getByRole("button", { name: "创建批量任务" })).toBeDisabled();
    await act(async () => pending.resolve(response({ tasks: [], assets: [] })));
    expect(screen.getByRole("button", { name: "创建批量任务" })).toBeEnabled();
  });

  it("切换任务再返回时保留各自未保存的片段", async () => {
    const second = { ...task, id: "task-b", sellingPoint: "易用", variant: { ...task.variant, id: "variant-b" } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task, second], assets: [{ id: "shot", name: "开场", kind: "video", duration: 4, url: "/shot.mp4" }] }));
    const onDraftChange = vi.fn();
    render(<BatchEditor projectId="p1" onDraftChange={onDraftChange} />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("button", { name: "添加素材 开场" }));
    await userEvent.click(screen.getByRole("button", { name: "编辑 易用" }));
    await userEvent.click(screen.getByRole("button", { name: "编辑 省时" }));
    expect(screen.getByLabelText("开场 入点（秒）")).toBeVisible();
    expect(onDraftChange).toHaveBeenLastCalledWith(true);
  });

  it("A 的迟到保存结果不会进入 B 的草稿", async () => {
    const second = { ...task, id: "task-b", sellingPoint: "易用", variant: { ...task.variant, id: "variant-b" } };
    const pending = deferred<Response>();
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task, second], assets: [{ id: "shot", name: "开场", kind: "video", duration: 4, url: "/shot.mp4" }] }))
      .mockReturnValueOnce(pending.promise);
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("button", { name: "添加素材 开场" }));
    await userEvent.click(screen.getByRole("button", { name: "保存变体" }));
    await userEvent.click(screen.getByRole("button", { name: "编辑 易用" }));
    const savedTracks = [{ ...task.variant.tracks[0], clips: [{ id: "clip-a", assetId: "shot", start: 0, inPoint: 0, duration: 3,
      speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 }] }, task.variant.tracks[1]];
    await act(async () => pending.resolve(response({ variant: { ...task.variant, revision: 1, tracks: savedTracks } })));
    expect(screen.queryByLabelText("开场 入点（秒）")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "易用 · 短视频变体" })).toBeVisible();
  });

  it("重排镜头时保留人工设置的片段间隔", async () => {
    const withGap = { ...task, variant: { ...task.variant, tracks: [
      { ...task.variant.tracks[0], clips: [
        { id: "clip-a", assetId: "shot", start: 0, inPoint: 0, duration: 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
        { id: "clip-b", assetId: "shot2", start: 5, inPoint: 0, duration: 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
      ] }, task.variant.tracks[1],
    ] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [withGap], assets: [
      { id: "shot", name: "开场", kind: "video", duration: 10, url: "/shot.mp4" },
      { id: "shot2", name: "效果", kind: "video", duration: 10, url: "/shot2.mp4" },
    ] }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("button", { name: "上移 效果" }));
    expect(screen.getByLabelText("效果 起点（秒）")).toHaveValue(0);
    expect(screen.getByLabelText("开场 起点（秒）")).toHaveValue(5);
  });

  it("先展示脚本与素材的匹配依据，确认后才把推荐镜头写入草稿", async () => {
    const assets = [
      { id: "shot", name: "操作演示", notes: "省时，先展示操作", kind: "video", duration: 4, url: "/shot.mp4" },
      { id: "shot2", name: "结果对比", notes: "展示结果", kind: "video", duration: 4, url: "/shot2.mp4" },
    ];
    const clips = [
      { id: "clip-a", assetId: "shot", start: 0, inPoint: 0, duration: 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
      { id: "clip-b", assetId: "shot2", start: 3, inPoint: 0, duration: 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
    ];
    const proposal = { baseRevision: 0, method: "asset-metadata-keywords", assetIds: ["shot", "shot2"], clips, matches: [
      { clipId: "clip-a", scriptSegment: "先展示操作", assetId: "shot", assetName: "操作演示", matchedTerms: ["展示", "操作"], matchedSellingPointTerms: ["省时"] },
      { clipId: "clip-b", scriptSegment: "再展示结果", assetId: "shot2", assetName: "结果对比", matchedTerms: ["结果"], matchedSellingPointTerms: [] },
    ] };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task], assets }))
      .mockResolvedValueOnce(response({ proposal }))
      .mockResolvedValueOnce(response({ variant: { ...task.variant, revision: 1, tracks: [
        { ...task.variant.tracks[0], clips }, task.variant.tracks[1],
      ] } }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择推荐素材 操作演示" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择推荐素材 结果对比" }));
    await userEvent.click(screen.getByRole("button", { name: "按脚本推荐镜头" }));

    expect(await screen.findByText(/先展示操作.*操作演示.*展示、操作/)).toBeVisible();
    expect(screen.getByText(/卖点匹配：省时/)).toBeVisible();
    expect(screen.queryByLabelText("操作演示 入点（秒）")).not.toBeInTheDocument();
    expect(screen.getByText(/素材名称和备注的词语匹配/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "应用推荐到草稿" }));
    expect(screen.getByLabelText("操作演示 入点（秒）")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "保存变体" }));
    const recommendationRequest = vi.mocked(fetch).mock.calls[1];
    expect(recommendationRequest[0]).toContain("/batch-edits/task-a/recommendations");
    expect(JSON.parse(recommendationRequest[1]?.body as string).assetIds).toEqual(["shot", "shot2"]);
    const saved = JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string);
    expect(saved.tracks[0].clips.map((clip: { assetId: string }) => clip.assetId)).toEqual(["shot", "shot2"]);
  });

  it("匹配失败时展示可行动的原因，保留原有片段", async () => {
    const existing = { ...task, variant: { ...task.variant, tracks: [
      { ...task.variant.tracks[0], clips: [{ id: "old", assetId: "shot", start: 0, inPoint: 0,
        duration: 2, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 }] }, task.variant.tracks[1],
    ] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [existing], assets: [
      { id: "shot", name: "操作演示", kind: "video", duration: 4, url: "/shot.mp4" },
      { id: "shot2", name: "结果对比", kind: "video", duration: 4, url: "/shot2.mp4" },
    ] })).mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: "batch_match_unavailable", message: "第 2 段没有匹配素材，请补充备注。" } }), { status: 422 }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择推荐素材 操作演示" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择推荐素材 结果对比" }));
    await userEvent.click(screen.getByRole("button", { name: "按脚本推荐镜头" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("第 2 段没有匹配素材");
    expect(screen.getByLabelText("操作演示 入点（秒）")).toHaveValue(0);
  });

  it("重新选择素材后旧推荐不能应用，已保存的推荐可追溯", async () => {
    const clip = { id: "clip-a", assetId: "shot", start: 0, inPoint: 0, duration: 2, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 };
    const proposal = { baseRevision: 0, method: "asset-metadata-keywords", assetIds: ["shot", "shot2"], clips: [clip], matches: [
      { clipId: "clip-a", scriptSegment: "先展示操作", assetId: "shot", assetName: "操作演示", matchedTerms: ["展示"], matchedSellingPointTerms: [] },
    ] };
    const saved = { ...task, proposal, variant: { ...task.variant, revision: 1, tracks: [
      { ...task.variant.tracks[0], clips: [clip] }, task.variant.tracks[1],
    ] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [saved], assets: [
      { id: "shot", name: "操作演示", kind: "video", duration: 4, url: "/shot.mp4" },
      { id: "shot2", name: "结果对比", kind: "video", duration: 4, url: "/shot2.mp4" },
    ] }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    expect(screen.getByText("已保存版本包含此推荐镜头。")).toBeVisible();
    await userEvent.click(screen.getByRole("checkbox", { name: "选择推荐素材 结果对比" }));
    expect(screen.getByRole("button", { name: "应用推荐到草稿" })).toBeDisabled();
    expect(screen.getByText("素材选择已变化，请重新推荐后再应用。")).toBeVisible();
  });

  it("一次提交五条脚本并集中比较每条的卖点、镜头和预览状态", async () => {
    const assets = Array.from({ length: 5 }, (_, index) => ({ id: `shot${index}`, name: `素材${index + 1}`,
      kind: "video", duration: 3, url: `/shot${index}.mp4` }));
    const children = assets.map((asset, index) => ({
      ...task, id: `child${index}`, batchId: task.id, generationId: "generation-a", sellingPoint: `卖点${index + 1}`,
      script: `脚本${index + 1}`, generation: { status: "ready", error: null },
      proposal: { baseRevision: 0, method: "asset-metadata-keywords", assetIds: assets.map((item) => item.id),
        clips: [], matches: [{ clipId: `clip${index}`, scriptSegment: `脚本${index + 1}`, assetId: asset.id,
          assetName: asset.name, matchedTerms: [`脚本${index + 1}`], matchedSellingPointTerms: [] }] },
      variant: { ...task.variant, id: `variant${index}`, tracks: [
        { ...task.variant.tracks[0], clips: [{ id: `clip${index}`, assetId: asset.id, start: 0, inPoint: 0,
          duration: 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 }] }, task.variant.tracks[1],
      ] },
    }));
    const queued = children.map((child) => ({ ...child, variant: { ...child.variant, runs: [
      { id: `run-${child.id}`, revision: 0, status: "queued", error: null },
    ] } }));
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task], assets }))
      .mockResolvedValueOnce(response({ generationId: "generation-a", tasks: children }))
      .mockResolvedValueOnce(response({ results: children.map((child) => ({ taskId: child.id, status: "queued" })) }))
      .mockResolvedValueOnce(response({ tasks: [task, ...queued], assets }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    for (const asset of assets) await userEvent.click(screen.getByRole("checkbox", { name: `选择推荐素材 ${asset.name}` }));
    for (let index = 0; index < 5; index++) {
      await userEvent.type(screen.getByLabelText(`第 ${index + 1} 条卖点`), `卖点${index + 1}`);
      await userEvent.type(screen.getByLabelText(`第 ${index + 1} 条脚本`), `脚本${index + 1}`);
    }
    await userEvent.click(screen.getByRole("button", { name: "创建 5 条变体" }));
    expect(await screen.findByText("批次 generation-a")).toBeVisible();
    expect(screen.getByText(/脚本1.*素材1/)).toBeVisible();
    expect(screen.getByRole("button", { name: "编辑变体 卖点1" })).toBeVisible();
    const request = vi.mocked(fetch).mock.calls[1];
    expect(request[0]).toContain("/batch-edits/task-a/variants/bulk");
    expect(JSON.parse(request[1]?.body as string)).toEqual({ assetIds: assets.map((asset) => asset.id),
      items: children.map((child) => ({ sellingPoint: child.sellingPoint, script: child.script })) });
    await userEvent.click(screen.getByRole("button", { name: "生成本批预览" }));
    expect(await screen.findByText(/5 条排队中/)).toBeVisible();
  });

  it("批量目标超限与未填脚本时不提交，失败条目仍可单独编辑", async () => {
    const failed = { ...task, id: "child-failed", batchId: task.id, generationId: "generation-a",
      generation: { status: "failed", error: "第 1 段没有匹配素材" } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task, failed], assets: [] }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    expect(screen.getByRole("button", { name: "创建 5 条变体" })).toBeDisabled();
    expect(screen.getByText(/第 1 段没有匹配素材/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "编辑变体 省时" }));
    expect(screen.getByRole("heading", { name: "省时 · 短视频变体" })).toBeVisible();
    expect(screen.getByRole("button", { name: "返回批量任务" })).toBeVisible();
  });

  it("集中列表允许取消单条排队预览并重试，其他变体保持完成状态", async () => {
    const queuedRun = { id: "run-a", revision: 0, status: "queued", error: null };
    const completedRun = { id: "run-b", revision: 0, status: "completed", error: null, url: "/preview-b.mp4" };
    const first = { ...task, id: "child-a", batchId: task.id, generationId: "generation-a",
      generation: { status: "ready", error: null }, variant: { ...task.variant, runs: [queuedRun] } };
    const second = { ...task, id: "child-b", sellingPoint: "易用", batchId: task.id, generationId: "generation-a",
      generation: { status: "ready", error: null }, variant: { ...task.variant, runs: [completedRun] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task, first, second], assets: [] }))
      .mockResolvedValueOnce(response({ variant: { ...first.variant, runs: [{ ...queuedRun, status: "cancelled", error: "预览已取消。" }] } }))
      .mockResolvedValueOnce(response({ variant: { ...first.variant, runs: [
        { ...queuedRun, status: "cancelled", error: "预览已取消。" }, { id: "run-c", revision: 0, status: "queued", error: null },
      ] } }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    expect(screen.getByLabelText("易用 预览")).toHaveAttribute("src", "/preview-b.mp4");
    await userEvent.click(screen.getByRole("button", { name: "取消预览 省时" }));
    expect(await screen.findByRole("button", { name: "重试预览 省时" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "重试预览 省时" }));
    expect(await screen.findByRole("button", { name: "取消预览 省时" })).toBeVisible();
    expect(screen.getByLabelText("易用 预览")).toHaveAttribute("src", "/preview-b.mp4");
    expect(vi.mocked(fetch).mock.calls[1][0]).toContain("/child-a/variant/previews/run-a/cancel");
    expect(vi.mocked(fetch).mock.calls[2][0]).toContain("/child-a/variant/previews");
  });

  it("集中列表区分当前镜头、初始推荐和旧版预览，并按最新状态互斥统计", async () => {
    const clip = { id: "manual", assetId: "new", start: 0, inPoint: 0, duration: 2,
      speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 };
    const oldRun = { id: "old-run", revision: 0, status: "completed", error: null, url: "/old.mp4" };
    const child = { ...task, id: "child-a", batchId: task.id, generationId: "generation-a",
      generation: { status: "ready", error: null }, variant: { ...task.variant, revision: 1,
        tracks: [{ ...task.variant.tracks[0], clips: [clip] }, task.variant.tracks[1]],
        runs: [oldRun, { id: "failed-run", revision: 1, status: "failed", error: "编码失败" }] },
      proposal: { baseRevision: 0, method: "asset-metadata-keywords", assetIds: ["old"], clips: [],
        matches: [{ clipId: "old-clip", scriptSegment: "展示效果", assetId: "old", assetName: "旧素材",
          matchedTerms: ["效果"], matchedSellingPointTerms: [] }] } };
    const cancelled = { ...task, id: "child-b", sellingPoint: "易用", batchId: task.id, generationId: "generation-a",
      generation: { status: "ready", error: null }, variant: { ...task.variant, runs: [
        { id: "cancelled-run", revision: 0, status: "cancelled", error: "预览已取消。" },
      ] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task, child, cancelled], assets: [
      { id: "old", name: "旧素材", kind: "video", duration: 2 },
      { id: "new", name: "当前素材", kind: "video", duration: 2 },
    ] }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    expect(screen.getByText("当前镜头：当前素材")).toBeVisible();
    expect(screen.getByText(/初始推荐依据.*展示效果.*旧素材/)).toBeVisible();
    expect(screen.getByText("预览对应保存版本 0（当前版本已变化）")).toBeVisible();
    expect(screen.getByLabelText("省时 预览")).toHaveAttribute("src", "/old.mp4");
    expect(screen.getByText("0 条预览完成 · 0 条排队中 · 1 条失败 · 1 条已取消 · 0 条待预览")).toBeVisible();
  });

  it("目标数量必须是五到二十之间的整数", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [task], assets: [
      { id: "shot", name: "画面甲", kind: "video", duration: 3, url: "/a.mp4" },
      { id: "shot2", name: "画面乙", kind: "video", duration: 3, url: "/b.mp4" },
    ] }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择推荐素材 画面甲" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择推荐素材 画面乙" }));
    for (let index = 0; index < 5; index++) {
      await userEvent.type(screen.getByLabelText(`第 ${index + 1} 条卖点`), `卖点${index + 1}`);
      await userEvent.type(screen.getByLabelText(`第 ${index + 1} 条脚本`), `脚本${index + 1}`);
    }
    expect(screen.getByRole("button", { name: "创建 5 条变体" })).toBeEnabled();
    await userEvent.clear(screen.getByLabelText("目标数量"));
    await userEvent.type(screen.getByLabelText("目标数量"), "5.5");
    expect(screen.getByRole("button", { name: "创建 5.5 条变体" })).toBeDisabled();
  });

  it("逐句编辑字幕后保存，识别候选需明确采用且旧预览标为过期", async () => {
    const cue = { id: "line-1", start: 0.2, end: 1.4, text: "人工原稿" };
    const candidate = { ...cue, text: "识别候选" };
    const saved = { ...task, variant: { ...task.variant, revision: 1, tracks: [
      { ...task.variant.tracks[0], clips: [{ id: "clip", assetId: "shot", start: 0, inPoint: 0, duration: 2,
        speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 }] }, task.variant.tracks[1],
    ], subtitles: { revision: 1, cues: [cue], recognitions: [{ id: "rec-1", status: "completed", error: null,
      timelineRevision: 1, subtitleRevision: 1, language: "zh", cues: [candidate] }] },
    runs: [{ id: "run-1", revision: 1, subtitleRevision: 0, status: "completed", error: null, url: "/old.mp4" }] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [saved], assets: [
      { id: "shot", name: "演示素材", kind: "video", duration: 2, url: "/shot.mp4" },
    ] })).mockResolvedValueOnce(response({ subtitles: { ...saved.variant.subtitles, revision: 2, cues: [candidate] } }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    expect(screen.getByLabelText("第 1 条字幕文字")).toHaveValue("人工原稿");
    expect(screen.getByText("预览对应保存版本 1／字幕版本 0（当前编辑已变化）")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "采用识别候选" }));
    expect(screen.getByLabelText("第 1 条字幕文字")).toHaveValue("识别候选");
    expect(screen.getByRole("button", { name: "生成预览" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存字幕" }));
    expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string)).toEqual({ revision: 1, timelineRevision: 1, cues: [candidate] });
  });

  it("逐条审核当前预览，显示历史，并在内容改动时阻止批准", async () => {
    const clip = { id: "clip", assetId: "shot", start: 0, inPoint: 0, duration: 2,
      speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 };
    const ready = { ...task, contentRevision: 0, reviewStatus: "pending", reviews: [], variant: { ...task.variant,
      revision: 1, tracks: [{ ...task.variant.tracks[0], clips: [clip] }, task.variant.tracks[1]],
      subtitles: { revision: 0, cues: [], recognitions: [] },
      runs: [{ id: "run-1", revision: 1, contentRevision: 0, subtitleRevision: 0, status: "completed", error: null, url: "/preview.mp4" }] } };
    const rejected = { ...ready, reviewStatus: "rejected", reviews: [{ id: "review-1", runId: "run-1", decision: "rejected",
      reason: "镜头不合适", contentRevision: 0, variantRevision: 1, subtitleRevision: 0 }] };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [ready], assets: [{ id: "shot", name: "演示素材", kind: "video", duration: 2 }] }))
      .mockResolvedValueOnce(response({ task: rejected }))
      .mockResolvedValueOnce(response({ task: { ...rejected, contentRevision: 1, script: "新脚本", reviewStatus: "stale" } }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    expect(screen.getByText("审核状态：待审核")).toBeVisible();
    expect(screen.getByText("审核依据：脚本版本 0／变体版本 1／字幕版本 0")).toBeVisible();
    expect(screen.getByText("预览脚本版本 0")).toBeVisible();
    await userEvent.type(screen.getByLabelText("审核原因"), "镜头不合适");
    await userEvent.click(screen.getByRole("button", { name: "退回变体" }));
    expect(await screen.findByText("审核状态：已退回")).toBeVisible();
    expect(screen.getByText(/退回：镜头不合适/)).toBeVisible();
    await userEvent.clear(screen.getByLabelText("当前脚本"));
    await userEvent.type(screen.getByLabelText("当前脚本"), "新脚本");
    expect(screen.getByRole("button", { name: "通过变体" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存卖点与脚本" }));
    expect(await screen.findByText("审核状态：旧审核已失效")).toBeVisible();
    expect(screen.getByText("预览脚本版本 0")).toBeVisible();
    expect(screen.getByRole("button", { name: "通过变体" })).toBeDisabled();
    expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string)).toEqual({ runId: "run-1", decision: "rejected", reason: "镜头不合适" });
    expect(JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string)).toEqual({ revision: 0, sellingPoint: "省时", script: "新脚本" });
  });

  it("保存镜头修改后立即显示旧审核失效", async () => {
    const clip = { id: "clip", assetId: "shot", start: 0, inPoint: 0, duration: 2,
      speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 };
    const approved = { ...task, reviewStatus: "approved", reviews: [{ id: "review-1", runId: "run-1", decision: "approved",
      reason: "可交付", contentRevision: 0, variantRevision: 1, subtitleRevision: 0 }],
    variant: { ...task.variant, revision: 1, tracks: [{ ...task.variant.tracks[0], clips: [clip] }, task.variant.tracks[1]],
      runs: [{ id: "run-1", revision: 1, status: "completed", error: null, url: "/preview.mp4" }] } };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [approved], assets: [
      { id: "shot", name: "演示素材", kind: "video", duration: 4 },
    ] })).mockResolvedValueOnce(response({ variant: { ...approved.variant, revision: 2 } }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    expect(screen.getByText("审核状态：已通过")).toBeVisible();
    await userEvent.clear(screen.getByLabelText("演示素材 入点（秒）"));
    await userEvent.type(screen.getByLabelText("演示素材 入点（秒）"), "0.5");
    await userEvent.click(screen.getByRole("button", { name: "保存变体" }));
    expect(await screen.findByText("审核状态：旧审核已失效")).toBeVisible();
  });

  it("仅允许选择当前批准的变体导出，并展示逐条交付结果", async () => {
    const root = { ...task, id: "root", sellingPoint: "批量" };
    const approved = { ...task, id: "child-a", batchId: "root", generationId: "gen-a",
      generation: { status: "ready", error: null }, reviewStatus: "approved", reviews: [{ id: "review-a", runId: "preview-a",
        decision: "approved", reason: "确认", contentRevision: 0, variantRevision: 1, subtitleRevision: 0 }],
      variant: { ...task.variant, revision: 1, runs: [{ id: "preview-a", revision: 1, contentRevision: 0,
        subtitleRevision: 0, status: "completed", error: null, url: "/preview-a.mp4" }], exports: [] } };
    const pending = { ...approved, id: "child-b", sellingPoint: "易用", reviewStatus: "pending", reviews: [],
      variant: { ...approved.variant, id: "variant-b" } };
    const delivery = { id: "export-a", status: "completed", error: null, contentRevision: 0, variantRevision: 1,
      subtitleRevision: 0, previewRunId: "preview-a", review: approved.reviews[0], sellingPoint: "省时",
      script: "先展示操作，再展示结果", assets: [{ id: "shot", name: "演示素材", kind: "video" }],
      snapshot: { settings: { width: 720, height: 1280, fps: 30 }, tracks: approved.variant.tracks },
      subtitles: [{ id: "cue", start: 0, end: 1, text: "已确认字幕" }], url: "/delivery-a.mp4" };
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [root, approved, pending], assets: [] }))
      .mockResolvedValueOnce(response({ results: [{ taskId: "child-a", runId: "export-a", status: "queued" }] }))
      .mockResolvedValueOnce(response({ tasks: [root, { ...approved, variant: { ...approved.variant, exports: [delivery] } }, pending], assets: [] }));
    render(<BatchEditor projectId="p1" />);
    await userEvent.click(await screen.findByRole("button", { name: "编辑 批量" }));
    expect(screen.getByRole("checkbox", { name: "选择导出 省时" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "选择导出 易用" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "选择导出 省时" }));
    await userEvent.click(screen.getByRole("button", { name: "导出已选变体" }));
    expect(await screen.findByRole("link", { name: "下载成片 省时" })).toHaveAttribute("href", "/delivery-a.mp4");
    await userEvent.click(screen.getByText("交付历史"));
    expect(screen.getByText(/字幕：已确认字幕/)).toBeVisible();
    expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string)).toEqual({ taskIds: ["child-a"] });
  });
});

it('已保存任务可进入正式音色选择', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(response({tasks:[task],assets:[]}))
    .mockResolvedValueOnce(response({available:true,message:'本地配音就绪',voices:[{id:'serena',name:'Serena',description:'温暖女声',useCases:'咖啡美食',sampleUrl:'/api/voices/serena/sample'}]})));
  render(<BatchEditor projectId="p1"/>);
  await userEvent.click(await screen.findByRole('button',{name:'编辑 省时'}));
  await userEvent.click(screen.getByRole('button',{name:'查看音色与本地配音'}));
  expect(await screen.findByText('温暖女声')).toBeVisible();
});


it("正式审片筛选作品并在切换后保留各自意见与精修对象", async () => {
  vi.stubGlobal("fetch", vi.fn());
  const variants = ["pending", "approved", "rejected"].map((reviewStatus, index) => ({ ...task,
    id: `child-${index}`, batchId: task.id, generationId: "g1", reviewStatus, sellingPoint: `作品${index + 1}`,
    variant: { ...task.variant, id: `v${index}`, runs: [{ id: `run${index}`, revision: 0, contentRevision: 0,
      status: "completed", url: `/result${index}.mp4`, error: null }] } }));
  vi.mocked(fetch).mockResolvedValue(response({ tasks: [task, ...variants], assets: [] }));
  const onOpenEdit = vi.fn();
  render(<BatchEditor projectId="p1" presentation="review" onOpenEdit={onOpenEdit} />);
  await userEvent.click(await screen.findByRole("button", { name: "查看作品 作品1" }));
  await userEvent.type(screen.getByLabelText("审核原因"), "请核对第一条字幕");
  await userEvent.click(screen.getByRole("button", { name: "查看作品 作品2" }));
  expect(screen.getByLabelText("审核原因")).toHaveValue("");
  await userEvent.click(screen.getByRole("button", { name: "查看作品 作品1" }));
  expect(screen.getByLabelText("审核原因")).toHaveValue("请核对第一条字幕");
  await userEvent.click(screen.getByRole("button", { name: "修改这条" }));
  expect(onOpenEdit).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole("button", { name: "已通过 1" }));
  expect(screen.getByRole("button", { name: "查看作品 作品2" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "查看作品 作品1" })).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "审核原因" })).toHaveValue("");
  expect(screen.getByLabelText("短视频变体预览")).toHaveAttribute("src", "/result1.mp4");
});

it("正式审片可查看所选作品的真实导出结果并下载", async () => {
  const delivered = { ...task, id: "delivered", batchId: task.id, sellingPoint: "已交付作品", generationId: "g1",
    reviewStatus: "approved", variant: { ...task.variant, runs: [{ id: "r", revision: 0, status: "completed", url: "/preview.mp4", error: null }],
      exports: [{ id: "e", status: "completed", url: "/delivery.mp4", error: null }] } };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ tasks: [task, delivered], assets: [] })));
  render(<BatchEditor projectId="p1" presentation="review" />);
  await userEvent.click(await screen.findByRole("button", { name: "查看作品 已交付作品" }));
  expect(screen.getByRole("link", { name: "下载成片 已交付作品" })).toHaveAttribute("href", "/delivery.mp4");
  expect(screen.getByText("成片导出：已完成")).toBeVisible();
});

it("同一任务超过二十条通过作品时按接口上限提交导出", async () => {
  const children = Array.from({ length: 21 }, (_, index) => ({ ...task, id: `export-${index}`, batchId: task.id,
    generationId: index < 20 ? "g1" : "g2", reviewStatus: "approved", sellingPoint: `可交付${index}`,
    variant: { ...task.variant, runs: [{ id: `r${index}`, revision: 0, status: "completed", url: "/preview.mp4", error: null }] } }));
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url, init) => Promise.resolve(response(String(url).endsWith("/exports") ? {} : { tasks: [task, ...children], assets: [] }))));
  render(<BatchEditor projectId="p1" presentation="review" />);
  await userEvent.click(await screen.findByRole("button", { name: "导出已通过项" }));
  await screen.findByRole("button", { name: "查看作品 可交付20" });
  expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith("/exports"))
    .map(([, init]) => JSON.parse(String(init?.body)).taskIds.length)).toEqual([20, 1]);
});

it("从素材页返回精修后可使用新增素材且不丢脚本草稿", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ tasks: [task], assets: [] })));
  const view = render(<BatchEditor projectId="p1" presentation="edit" projectAssets={[]} />);
  await userEvent.type(await screen.findByLabelText("当前脚本"), "补充文案");
  view.rerender(<BatchEditor projectId="p1" presentation="edit" projectAssets={[
    { id: "new", name: "新增画面", kind: "video", duration: 3, url: "/new.mp4" },
  ]} />);
  expect(screen.getByRole("button", { name: "添加素材 新增画面" })).toBeVisible();
  expect(screen.getByLabelText("当前脚本")).toHaveValue("先展示操作，再展示结果补充文案");
});

it("选声进入审片再返回后保留音色选择与未制作提示", async () => {
  const child = { ...task, id: "voice-child", batchId: task.id, generationId: "g1", sellingPoint: "第一条" };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(url => Promise.resolve(response(String(url) === "/api/voices"
    ? { available: true, message: "就绪", voices: [{ id: "serena", name: "Serena", description: "温暖女声", useCases: "美食", sampleUrl: "/serena.wav" }] }
    : { tasks: [task, child], assets: [] }))));
  const onDraftChange = vi.fn();
  const view = render(<BatchEditor projectId="p1" presentation="voice" onDraftChange={onDraftChange} />);
  await userEvent.click(await screen.findByRole("radio", { name: "选择 Serena" }));
  view.rerender(<BatchEditor projectId="p1" presentation="review" onDraftChange={onDraftChange} />);
  await screen.findByRole("button", { name: "查看作品 第一条" });
  expect(onDraftChange).toHaveBeenLastCalledWith(true);
  view.rerender(<BatchEditor projectId="p1" presentation="voice" onDraftChange={onDraftChange} />);
  expect(await screen.findByRole("radio", { name: "选择 Serena" })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("button", { name: "生成当前配音" })).toBeDisabled();
});

it("新批次选声草稿只使目标作品失效，旧批次通过作品仍可导出", async () => {
  const completed = { ...task, id: "old", batchId: task.id, generationId: "g1", sellingPoint: "旧批作品", reviewStatus: "approved",
    variant: { ...task.variant, runs: [{ id: "r", revision: 0, status: "completed", url: "/preview.mp4", error: null }] } };
  const current = { ...completed, id: "new", generationId: "g2", sellingPoint: "新批作品" };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(url => Promise.resolve(response(String(url) === "/api/voices"
    ? { available: true, message: "就绪", voices: [{ id: "serena", name: "Serena", description: "女声", useCases: "生活", sampleUrl: null }] }
    : { tasks: [task, completed, current], assets: [] }))));
  const view = render(<BatchEditor projectId="p1" presentation="voice" />);
  await userEvent.click(await screen.findByRole("radio", { name: "选择 Serena" }));
  view.rerender(<BatchEditor projectId="p1" presentation="review" />);
  await userEvent.click(await screen.findByRole("button", { name: "已通过 1" }));
  expect(screen.getByRole("button", { name: "查看作品 旧批作品" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "查看作品 新批作品" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出已通过项" })).toBeEnabled();
});

it("配音提交中跨页返回仍锁定目标音色，迟到回包完成后解除草稿状态", async () => {
  const pending = deferred<Response>();
  const child = { ...task, id: "voice-child", batchId: task.id, generationId: "g1", sellingPoint: "当前作品" };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(url => String(url).endsWith("/voiceovers") ? pending.promise : Promise.resolve(response(String(url) === "/api/voices"
    ? { available: true, message: "就绪", voices: ["Serena", "Ryan"].map(name => ({ id: name.toLowerCase(), name, description: "音色描述", useCases: "生活", sampleUrl: null })) }
    : { tasks: [task, child], assets: [] }))));
  const onDraftChange = vi.fn();
  const view = render(<BatchEditor projectId="p1" presentation="voice" onDraftChange={onDraftChange} />);
  await userEvent.click(await screen.findByRole("radio", { name: "选择 Serena" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "我已确认下列已保存脚本，按所选音色生成配音" }));
  await userEvent.click(screen.getByRole("button", { name: "生成当前配音" }));
  view.rerender(<BatchEditor projectId="p1" presentation="review" onDraftChange={onDraftChange} />);
  await screen.findByRole("button", { name: "查看作品 当前作品" });
  view.rerender(<BatchEditor projectId="p1" presentation="voice" onDraftChange={onDraftChange} />);
  expect(await screen.findByRole("radio", { name: "选择 Ryan" })).toBeDisabled();
  await act(async () => pending.resolve(response({ tasks: [{ ...child, voiceover: { voiceId: "serena", status: "completed", contentRevision: 0 } }] })));
  expect(screen.getByRole("radio", { name: "选择 Ryan" })).toBeEnabled();
  expect(onDraftChange).toHaveBeenLastCalledWith(false);
});
