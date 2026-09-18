import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TimelineEditor } from "./TimelineEditor";
import { cancelTimelineRun, getTimeline, saveTimeline, type TimelineWorkspace } from "./timelineApi";

vi.mock("./timelineApi", () => ({
  getTimeline: vi.fn(), saveTimeline: vi.fn(), startTimelineRun: vi.fn(), cancelTimelineRun: vi.fn(), uploadTimelineAudio: vi.fn(),
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
  beforeEach(() => { mockedGet.mockReset(); mockedSave.mockReset(); mockedCancel.mockReset(); });

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

  it("轮询迟到时不会用旧读取覆盖新草稿", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const pending = deferred<TimelineWorkspace>();
    mockedGet.mockResolvedValueOnce({ ...workspace(), runs: [{ id: "run-1", revision: 2, format: "preview", status: "running", error: null }] }).mockReturnValueOnce(pending.promise);
    render(<TimelineEditor projectId="project-001" />);
    await act(async () => undefined);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    await user.click(screen.getByRole("button", { name: "开场.jpg" }));
    await user.clear(screen.getByLabelText("时间线起点"));
    await user.type(screen.getByLabelText("时间线起点"), "2.5");
    await act(async () => { pending.resolve(workspace()); });

    expect(screen.getByLabelText("时间线起点")).toHaveValue(2.5);
    vi.useRealTimers();
  });
});
