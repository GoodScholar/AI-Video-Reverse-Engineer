import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TimelineEditor } from "./TimelineEditor";
import { preflightTimeline, startTimelineRun, cancelTimelineRun, getTimeline, importShotResults, saveTimeline, validateTimelineDraft, type TimelineWorkspace } from "./timelineApi";

vi.mock("./timelineApi", () => ({
  preflightTimeline: vi.fn(), validateTimelineDraft: vi.fn(), importShotResults: vi.fn(), getTimeline: vi.fn(), saveTimeline: vi.fn(), startTimelineRun: vi.fn(), cancelTimelineRun: vi.fn(), uploadTimelineAudio: vi.fn(),
  timelineOutputUrl: (projectId: string, runId: string) => `/api/projects/${projectId}/timeline/runs/${runId}/output`,
}));

const mockedGet = vi.mocked(getTimeline);
const mockedSave = vi.mocked(saveTimeline);
const mockedCancel = vi.mocked(cancelTimelineRun);

function workspace(revision = 2): TimelineWorkspace {
  return {
    revision, settings: { width: 1280, height: 720, fps: 30 },
    tracks: [
      { id: "video-1", name: "主画面", kind: "video", muted: false, hidden: false, clips: [{ id: "clip-1", assetId: "image-1", start: 1, inPoint: 0, duration: 3, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 }] },
      { id: "audio-1", name: "旁白", kind: "audio", muted: false, hidden: false, clips: [] },
    ],
    assets: [{ id: "image-1", name: "开场.jpg", kind: "image", url: "/image.jpg", duration: 5 }],
    runs: [{ id: "preview-old", revision: 1, format: "preview", status: "completed", error: null, url: "/old-preview.mp4" }],
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("TimelineEditor", () => {
  afterEach(() => vi.useRealTimers());
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); mockedGet.mockReset(); mockedSave.mockReset(); mockedCancel.mockReset(); });

  it("从候选引用定位已保存时间线片段", async () => {
    mockedGet.mockResolvedValue(workspace());
    render(<TimelineEditor projectId="focus-from-candidate" focusClip={{ trackId: "video-1", clipId: "clip-1" }} />);
    expect(await screen.findByLabelText("时间线起点")).toHaveValue(1);
    expect(screen.getByLabelText("播放头（秒）")).toHaveValue(1);
  });

  it("向项目备份报告内存草稿状态，保存后解除保护", async () => {
    mockedGet.mockResolvedValue(workspace());
    const onDraftChange = vi.fn();
    mockedSave.mockImplementation(async (_id, draft) => ({ ...workspace(3), ...draft, revision: 3 }));
    render(<TimelineEditor projectId="memory-dirty" onDraftChange={onDraftChange} />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    expect(onDraftChange).toHaveBeenLastCalledWith(false);
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "7" } });
    expect(onDraftChange).toHaveBeenLastCalledWith(true);
    localStorage.clear();
    expect(onDraftChange).toHaveBeenLastCalledWith(true);
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    expect(onDraftChange).toHaveBeenLastCalledWith(false);
  });

  it("导出先预检，发现问题时不提交并可定位片段", async () => {
    mockedGet.mockResolvedValue(workspace());
    vi.mocked(startTimelineRun).mockReset();
    vi.mocked(preflightTimeline).mockResolvedValue({ revision: 2, format: "mp4", ready: false, issues: [{ level: "error", label: "主画面 · 片段 1", message: "素材文件缺失", trackId: "video-1", clipId: "clip-1", assetId: "image-1" }] });
    render(<TimelineEditor projectId="preflight" />);
    await userEvent.click(await screen.findByRole("button", { name: "导出 MP4" }));
    expect(await screen.findByText("素材文件缺失")).toBeVisible();
    expect(startTimelineRun).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "定位主画面 · 片段 1" }));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(1);
  });

  it("预检通过后提交导出，并保留静音警告", async () => {
    mockedGet.mockResolvedValue(workspace());
    vi.mocked(preflightTimeline).mockResolvedValue({ revision: 2, format: "mp4", ready: true, issues: [{ level: "warning", label: "输出", message: "视频将为静音" }] });
    vi.mocked(startTimelineRun).mockResolvedValue(workspace());
    render(<TimelineEditor projectId="preflight-ok" />);
    await userEvent.click(await screen.findByRole("button", { name: "导出 MP4" }));
    expect(await screen.findByText("视频将为静音")).toBeVisible();
    expect(startTimelineRun).toHaveBeenCalledWith("preflight-ok", 2, "mp4");
  });

  it("素材版本在预检期间变化时不使用晚到的成功结果导出", async () => {
    mockedGet.mockResolvedValue(workspace());
    vi.mocked(startTimelineRun).mockReset();
    const pending = deferred<Awaited<ReturnType<typeof preflightTimeline>>>();
    vi.mocked(preflightTimeline).mockReturnValue(pending.promise);
    const view = render(<TimelineEditor projectId="preflight-stale" preproductionRevision={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "导出 MP4" }));
    view.rerender(<TimelineEditor projectId="preflight-stale" preproductionRevision={2} />);
    await act(async () => pending.resolve({ revision: 2, format: "mp4", ready: true, issues: [] }));
    expect(startTimelineRun).not.toHaveBeenCalled();
    expect(await screen.findByText(/预检期间.*变化/)).toBeVisible();
  });

  it("时间线快捷键分割、撤销、重做，输入框不触发删除", async () => {
    mockedGet.mockResolvedValue(workspace());
    render(<TimelineEditor projectId="keys" />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    fireEvent.change(screen.getByLabelText("播放头（秒）"), { target: { value: "2" } });
    const editor = screen.getByRole("region", { name: "复刻剪辑时间线" });
    fireEvent.keyDown(editor, { key: "s" });
    expect(screen.getAllByRole("button", { name: "开场.jpg" })).toHaveLength(2);
    fireEvent.keyDown(editor, { key: "z", metaKey: true });
    expect(screen.getAllByRole("button", { name: "开场.jpg" })).toHaveLength(1);
    fireEvent.keyDown(editor, { key: "z", metaKey: true, shiftKey: true });
    expect(screen.getAllByRole("button", { name: "开场.jpg" })).toHaveLength(2);
    fireEvent.keyDown(screen.getByLabelText("播放头（秒）"), { key: "Delete" });
    expect(screen.getAllByRole("button", { name: "开场.jpg" })).toHaveLength(2);
  });

  it("方向键按项目帧率移动播放头，Shift 移动十帧", async () => {
    mockedGet.mockResolvedValue(workspace());
    render(<TimelineEditor projectId="frame-keys" />);
    const editor = await screen.findByRole("region", { name: "复刻剪辑时间线" });
    fireEvent.keyDown(editor, { key: "ArrowRight" });
    expect(Number((screen.getByLabelText("播放头（秒）") as HTMLInputElement).value)).toBeCloseTo(1 / 30);
    fireEvent.keyDown(editor, { key: "ArrowRight", shiftKey: true });
    expect(Number((screen.getByLabelText("播放头（秒）") as HTMLInputElement).value)).toBeCloseTo(11 / 30);
  });

  it("拖动吸附播放头，Alt 绕过吸附，一次撤销还原一次拖动", async () => {
    mockedGet.mockResolvedValue(workspace());
    render(<TimelineEditor projectId="drag-snap" />);
    const clip = await screen.findByRole("button", { name: "开场.jpg" });
    fireEvent.change(screen.getByLabelText("播放头（秒）"), { target: { value: "6" } });
    const pointer = (type: string, clientX: number, altKey = false) => fireEvent(clip, Object.assign(new Event(type, { bubbles: true }), { button: 0, clientX, pointerId: 1, altKey }));
    pointer("pointerdown", 100); pointer("pointermove", 604); pointer("pointerup", 604);
    expect(screen.getByLabelText("时间线起点")).toHaveValue(6);
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(1);
    pointer("pointerdown", 100); pointer("pointermove", 604, true); pointer("pointerup", 604, true);
    expect(screen.getByLabelText("时间线起点")).toHaveValue(6.04);
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    await userEvent.click(screen.getByRole("button", { name: "吸附：开" }));
    pointer("pointerdown", 100); pointer("pointermove", 604); pointer("pointerup", 604);
    expect(screen.getByLabelText("时间线起点")).toHaveValue(6.04);
  });

  it("组合输入、可编辑区域、播放器、拖动及保存中不会触发快捷键删除", async () => {
    mockedGet.mockResolvedValue(workspace());
    const pending = deferred<TimelineWorkspace>();
    mockedSave.mockReturnValue(pending.promise);
    render(<TimelineEditor projectId="key-guards" />);
    const clip = await screen.findByRole("button", { name: "开场.jpg" });
    await userEvent.click(clip);
    const editor = screen.getByRole("region", { name: "复刻剪辑时间线" });
    fireEvent.keyDown(editor, { key: "Delete", isComposing: true });
    const editable = document.createElement("div"); editable.setAttribute("contenteditable", "true"); editor.append(editable);
    fireEvent.keyDown(editable, { key: "Delete" }); editable.remove();
    fireEvent.keyDown(editor.querySelector("video")!, { key: "Delete" });
    fireEvent(clip, Object.assign(new Event("pointerdown", { bubbles: true }), { button: 0, clientX: 100, pointerId: 1 }));
    fireEvent.keyDown(editor, { key: "Delete" });
    fireEvent.pointerUp(clip);
    expect(screen.getByRole("button", { name: "开场.jpg" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "2" } });
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    fireEvent.keyDown(editor, { key: "Delete" });
    expect(screen.getByRole("button", { name: "开场.jpg" })).toBeVisible();
    await act(async () => pending.resolve(workspace(3)));
  });

  it("波形失败原因直接可见且仍可编辑片段", async () => {
    const state = workspace(); state.assets[0].kind = "audio"; state.tracks[0].kind = "audio";
    mockedGet.mockResolvedValue(state);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { message: "波形暂支持 300 秒以内的素材，请先裁切。" } }), { status: 422 })));
    try {
      render(<TimelineEditor projectId="wave-error" />);
      expect(await screen.findByText(/300 秒以内.*剪辑仍可继续/)).toBeVisible();
      await userEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
      expect(screen.getByLabelText("时间线起点")).toBeEnabled();
    } finally { vi.unstubAllGlobals(); }
  });

  it("清理记录后仍明确显示未释放文件的警告", async () => {
    mockedGet.mockResolvedValue(workspace());
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ revision: 2, bytes: 2048, fileCount: 2, description: "删除历史输出" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...workspace(3), runs: [], cleanupWarning: "记录已移除，但部分暂存文件未能释放。" }))));
    try {
      render(<TimelineEditor projectId="cleanup-warning" />);
      await userEvent.click(await screen.findByRole("button", { name: "清理记录" }));
      await userEvent.click(await screen.findByRole("button", { name: "确认清理记录" }));
      expect(await screen.findByText("记录已移除，但部分暂存文件未能释放。")).toBeVisible();
      expect(screen.queryByRole("button", { name: "清理记录" })).not.toBeInTheDocument();
    } finally { vi.unstubAllGlobals(); }
  });

  it("清除会话后仍恢复长期保存的草稿", async () => {
    mockedGet.mockResolvedValue(workspace());
    const first = render(<TimelineEditor projectId="durable" />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "7" } });
    first.unmount(); sessionStorage.clear();
    render(<TimelineEditor projectId="durable" />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(7);
  });

  it("JSON 先校验再明确恢复，进入可撤销草稿且不自动保存", async () => {
    mockedGet.mockResolvedValue(workspace());
    const draft = workspace(1); draft.tracks[0].clips[0].start = 8;
    const payload = { revision: 1, settings: draft.settings, tracks: draft.tracks };
    vi.mocked(validateTimelineDraft).mockResolvedValue(payload);
    render(<TimelineEditor projectId="json" />);
    await screen.findByRole("button", { name: "开场.jpg" });
    const file = new File([JSON.stringify({ projectId: "json", ...payload })], "draft.json", { type: "application/json" });
    await userEvent.upload(screen.getByLabelText("导入时间线草稿文件"), file);
    await userEvent.click(await screen.findByRole("button", { name: "恢复导入的草稿" }));
    await userEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(8);
    expect(mockedSave).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(1);
  });

  it("拒绝其他项目 JSON，校验等待期间的新编辑不会被替换", async () => {
    mockedGet.mockResolvedValue(workspace());
    const pending = deferred<Pick<TimelineWorkspace, "revision" | "settings" | "tracks">>();
    vi.mocked(validateTimelineDraft).mockReturnValue(pending.promise);
    render(<TimelineEditor projectId="json-race" />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    const file = (projectId: string) => new File([JSON.stringify({ projectId, ...workspace() })], "draft.json", { type: "application/json" });
    await userEvent.upload(screen.getByLabelText("导入时间线草稿文件"), file("other"));
    expect(await screen.findByRole("alert")).toHaveTextContent("不属于当前项目");
    await userEvent.upload(screen.getByLabelText("导入时间线草稿文件"), file("json-race"));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "9" } });
    await act(async () => pending.resolve(workspace()));
    expect(screen.getByRole("alert")).toHaveTextContent("编辑已变化");
    expect(screen.queryByRole("button", { name: "恢复导入的草稿" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("时间线起点")).toHaveValue(9);
  });

  it("单片段替换保留剪辑参数和其他轨道，可撤销并显式保存", async () => {
    const original = workspace();
    Object.assign(original.tracks[0].clips[0], { inPoint: 1, duration: 2, speed: 1.5, volume: 0.6, fadeIn: 0.2, fadeOut: 0.3 });
    original.assets.push({ id: "result", name: "新版.mp4", kind: "video", url: "/result", duration: 5 });
    mockedGet.mockResolvedValue(original); mockedSave.mockResolvedValue({ ...original, revision: 3 });
    render(<TimelineEditor projectId="replace" shotResults={[{ id: "shot", title: "镜头一", resultAssetId: "result" }]} />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    await userEvent.selectOptions(screen.getByLabelText("替换为镜头结果"), "shot");
    await userEvent.click(screen.getByRole("button", { name: "替换选中片段" }));
    expect(screen.getByRole("button", { name: "新版.mp4" })).toBeVisible();
    expect(mockedSave).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(screen.getByRole("button", { name: "开场.jpg" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    const saved = mockedSave.mock.calls[0][1];
    expect(saved.tracks[0].clips[0]).toEqual({ ...original.tracks[0].clips[0], assetId: "result" });
    expect(saved.tracks[1]).toEqual(original.tracks[1]);
  });

  it("短结果和未保存镜头方案不能替换片段", async () => {
    const original = workspace();
    original.assets.push({ id: "short", name: "短.mp4", kind: "video", url: "/short", duration: 1 });
    mockedGet.mockResolvedValue(original);
    const shots = [{ id: "shot", title: "短镜头", resultAssetId: "short" }];
    const view = render(<TimelineEditor projectId="short" shotResults={shots} />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    await userEvent.selectOptions(screen.getByLabelText("替换为镜头结果"), "shot");
    expect(screen.getByRole("button", { name: "替换选中片段" })).toBeDisabled();
    expect(screen.getByText(/结果时长不足/)).toBeVisible();
    view.rerender(<TimelineEditor projectId="short" shotResults={shots} preparationDirty />);
    expect(screen.getByLabelText("替换为镜头结果")).toBeDisabled();
    expect(screen.getByRole("button", { name: "开场.jpg" })).toBeVisible();
  });

  it("携带双版本导入结果并保护未保存的剪辑和方案", async () => {
    mockedGet.mockResolvedValue(workspace());
    vi.mocked(importShotResults).mockResolvedValue(workspace(3));
    const view = render(<TimelineEditor projectId="flow" preproductionRevision={7} preparationDirty />);
    const button = await screen.findByRole("button", { name: "按镜头导入结果" });
    expect(button).toBeDisabled();
    view.rerender(<TimelineEditor projectId="flow" preproductionRevision={7} />);
    await userEvent.click(button);
    expect(importShotResults).toHaveBeenCalledWith("flow", 2, 7);
    await userEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "2" } });
    expect(button).toBeDisabled();
  });

  it("版本冲突保留缓存，明确恢复后使用最新版本且不自动保存", async () => {
    const draft = workspace(2);
    draft.tracks[0].clips[0].start = 9;
    const key = "aivre:timeline-draft:conflict";
    const cached = JSON.stringify({ revision: 2, draft: { revision: 2, settings: draft.settings, tracks: draft.tracks } });
    sessionStorage.setItem(key, cached);
    mockedGet.mockResolvedValue(workspace(5));
    mockedSave.mockResolvedValue(workspace(6));
    const view = render(<TimelineEditor projectId="conflict" />);
    expect(await screen.findByRole("heading", { name: "发现版本冲突草稿" })).toBeVisible();
    expect(sessionStorage.getItem(key)).toBe(cached);
    expect(screen.queryByRole("button", { name: "保存时间线" })).not.toBeInTheDocument();
    view.unmount();
    expect(sessionStorage.getItem(key)).toBe(cached);
    render(<TimelineEditor projectId="conflict" />);
    await userEvent.click(await screen.findByRole("button", { name: "恢复草稿并继续编辑" }));
    expect(mockedSave).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(9);
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    expect(mockedSave.mock.calls[0][1]).toMatchObject({ revision: 5, tracks: [{ clips: [{ start: 9 }] }, {}] });
  });

  it("放弃冲突草稿才清除缓存并使用服务端内容", async () => {
    const key = "aivre:timeline-draft:discard";
    sessionStorage.setItem(key, JSON.stringify({ revision: 1, draft: workspace(1) }));
    mockedGet.mockResolvedValue(workspace(5));
    render(<TimelineEditor projectId="discard" />);
    await userEvent.click(await screen.findByRole("button", { name: "放弃草稿，使用已保存版本" }));
    expect(sessionStorage.getItem(key)).toBeNull();
    expect(screen.getByRole("button", { name: "保存时间线" })).toBeDisabled();
    expect(mockedSave).not.toHaveBeenCalled();
  });

  it("另存冲突草稿不会清除缓存或写入服务端", async () => {
    const key = "aivre:timeline-draft:export";
    const cached = JSON.stringify({ revision: 1, draft: workspace(1) });
    sessionStorage.setItem(key, cached);
    mockedGet.mockResolvedValue(workspace(3));
    const create = vi.fn((_blob: Blob) => "blob:draft");
    const revoke = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    try {
      render(<TimelineEditor projectId="export" />);
      await userEvent.click(await screen.findByRole("button", { name: "另存草稿 JSON" }));
      expect(create.mock.calls[0][0]).toBeInstanceOf(Blob);
      expect(click).toHaveBeenCalledOnce();
      expect(revoke).toHaveBeenCalledWith("blob:draft");
      expect(sessionStorage.getItem(key)).toBe(cached);
      expect(mockedSave).not.toHaveBeenCalled();
      expect(screen.getByRole("heading", { name: "发现版本冲突草稿" })).toBeVisible();
    } finally { click.mockRestore(); vi.unstubAllGlobals(); }
  });

  it("保存失败后重新读取会保留当前草稿并进入冲突处理", async () => {
    mockedGet.mockResolvedValueOnce(workspace(2)).mockResolvedValueOnce(workspace(4));
    mockedSave.mockRejectedValue(new Error("时间线已更新"));
    render(<TimelineEditor projectId="save-conflict" />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "8" } });
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    await userEvent.click(await screen.findByRole("button", { name: "重新读取并检查草稿" }));
    expect(await screen.findByRole("heading", { name: "发现版本冲突草稿" })).toBeVisible();
    expect(JSON.parse(localStorage.getItem("aivre:timeline-draft:save-conflict")!).draft.tracks[0].clips[0].start).toBe(8);
  });

  it("导入后提示数量与区间，选中首片段并定位播放头", async () => {
    const initial = workspace();
    const next = workspace(3);
    next.assets.push({ id: "result", name: "结果.mp4", kind: "video", url: "/result.mp4", duration: 3 });
    next.tracks.push({ id: "shot-results-test", name: "镜头结果", kind: "video", muted: false, hidden: false, clips: [
      { id: "result1", assetId: "result", start: 4, inPoint: 0, duration: 2.5, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
      { id: "result2", assetId: "result", start: 6.5, inPoint: 0, duration: 2, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
    ] });
    mockedGet.mockResolvedValue(initial);
    vi.mocked(importShotResults).mockResolvedValue(next);
    render(<TimelineEditor projectId="import-focus" preproductionRevision={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "按镜头导入结果" }));
    expect(await screen.findByText(/已导入 2 个片段，时间范围 4–8.5 秒/)).toBeVisible();
    expect(screen.getByLabelText("播放头（秒）")).toHaveValue(4);
    expect(screen.getByLabelText("时间线起点")).toHaveValue(4);
    expect(screen.getAllByRole("button", { name: "结果.mp4" })[0]).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "按镜头导入结果" }));
    expect(screen.getByText(/未重复添加/)).toBeVisible();
  });

  it("导入等待期间产生新草稿时不改变当前编辑内容或定位", async () => {
    const pending = deferred<TimelineWorkspace>();
    mockedGet.mockResolvedValue(workspace());
    vi.mocked(importShotResults).mockReturnValue(pending.promise);
    render(<TimelineEditor projectId="import-race" preproductionRevision={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "开场.jpg" }));
    await userEvent.click(screen.getByRole("button", { name: "按镜头导入结果" }));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "8" } });
    await act(async () => pending.resolve(workspace(3)));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(8);
    expect(screen.getByText(/当前草稿已保留/)).toBeVisible();
  });

  it("音频导入按钮可以通过键盘触发文件选择", async () => {
    mockedGet.mockResolvedValue(workspace());
    render(<TimelineEditor projectId="audio-keyboard" />);
    const button = await screen.findByRole("button", { name: "导入音频" });
    const input = screen.getByLabelText("导入音频", { selector: "input" });
    const click = vi.spyOn(input, "click");
    button.focus();
    await userEvent.keyboard(" ");
    expect(click).toHaveBeenCalledOnce();
  });

  it("展示多轨片段，精确编辑后保存当前草稿", async () => {
    mockedGet.mockResolvedValue(workspace());
    mockedSave.mockResolvedValue(workspace(3));
    render(<TimelineEditor projectId="project-001" />);

    expect(await screen.findByText("主画面")).toBeVisible();
    expect(screen.getByRole("button", { name: "开场.jpg" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    await userEvent.clear(screen.getByLabelText("时间线起点"));
    await userEvent.type(screen.getByLabelText("时间线起点"), "2.5");
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));

    expect(mockedSave).toHaveBeenCalledWith("project-001", expect.objectContaining({ revision: 2 }));
    expect(mockedSave.mock.calls[0]?.[1].tracks[0]?.clips[0]?.start).toBe(2.5);
  });

  it("切换项目时忽略旧项目迟到的读取结果", async () => {
    const old = deferred<TimelineWorkspace>();
    mockedGet.mockReturnValueOnce(old.promise).mockResolvedValueOnce(workspace(8));
    const view = render(<TimelineEditor projectId="old-project" />);
    view.rerender(<TimelineEditor projectId="new-project" />);
    await screen.findByText("主画面");

    await act(async () => { old.resolve({ ...workspace(), tracks: [{ id: "old", name: "旧轨道", kind: "video", muted: false, hidden: false, clips: [] }] }); });
    expect(screen.queryByText("旧轨道")).not.toBeInTheDocument();
  });

  it("保存后的撤销只回退内容，后续保存仍提交当前修订", async () => {
    mockedGet.mockResolvedValue(workspace());
    mockedSave.mockResolvedValueOnce(workspace(3)).mockResolvedValueOnce(workspace(4));
    render(<TimelineEditor projectId="project-001" />);
    await screen.findByText("主画面");
    await userEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    await userEvent.clear(screen.getByLabelText("时间线起点"));
    await userEvent.type(screen.getByLabelText("时间线起点"), "2");
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));

    expect(mockedSave.mock.calls[1]?.[1].revision).toBe(3);
  });

  it("取消任务的迟到回包不覆盖本地草稿", async () => {
    const pending = deferred<TimelineWorkspace>();
    mockedGet.mockResolvedValue({ ...workspace(), runs: [{ id: "run-1", revision: 2, format: "preview", status: "running", error: null }] });
    mockedCancel.mockReturnValue(pending.promise);
    render(<TimelineEditor projectId="project-001" />);
    await screen.findByRole("button", { name: "取消" });
    await userEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    await userEvent.clear(screen.getByLabelText("时间线起点"));
    await userEvent.type(screen.getByLabelText("时间线起点"), "2.5");
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    await act(async () => { pending.resolve(workspace()); });

    expect(screen.getByLabelText("时间线起点")).toHaveValue(2.5);
  });

  it("保存期间继续编辑时保留新草稿，并使用保存返回的新版本", async () => {
    const pending = deferred<TimelineWorkspace>();
    mockedGet.mockResolvedValue(workspace());
    mockedSave.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(workspace(4));
    render(<TimelineEditor projectId="save-in-flight" />);
    await screen.findByText("主画面");
    fireEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "2" } });
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "2.5" } });
    const saved = workspace(3);
    saved.tracks[0].clips[0].start = 2;
    await act(async () => pending.resolve(saved));
    expect(screen.getByLabelText("时间线起点")).toHaveValue(2.5);
    await userEvent.click(screen.getByRole("button", { name: "保存时间线" }));
    expect(mockedSave.mock.calls[1]?.[1]).toMatchObject({ revision: 3, tracks: [{ clips: [{ start: 2.5 }] }, {}] });
  });

  it("轮询迟到时不会用旧读取覆盖新草稿", async () => {
    vi.useFakeTimers();
    const pending = deferred<TimelineWorkspace>();
    mockedGet.mockResolvedValueOnce({ ...workspace(), runs: [{ id: "run-1", revision: 2, format: "preview", status: "running", error: null }] }).mockReturnValueOnce(pending.promise);
    render(<TimelineEditor projectId="project-001" />);
    await act(async () => undefined);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    fireEvent.click(screen.getByRole("button", { name: "开场.jpg" }));
    fireEvent.change(screen.getByLabelText("时间线起点"), { target: { value: "2.5" } });
    await act(async () => { pending.resolve(workspace()); });

    expect(screen.getByLabelText("时间线起点")).toHaveValue(2.5);
    vi.useRealTimers();
  });
});
