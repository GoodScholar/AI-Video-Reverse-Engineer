import { beforeEach, describe, expect, it, vi } from "vitest";

import { cancelTimelineRun, getTimeline, saveTimeline, startTimelineRun, timelineOutputUrl, uploadTimelineAudio, type TimelineWorkspace } from "./timelineApi";

const workspace: TimelineWorkspace = {
  revision: 3,
  settings: { width: 1280, height: 720, fps: 30 },
  tracks: [{ id: "video-1", name: "画面", kind: "video", muted: false, hidden: false, clips: [] }],
  assets: [],
  runs: [],
};

describe("timelineApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("读取、保存和提交渲染时使用项目隔离地址、版本与本地编辑意图", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(workspace), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...workspace, revision: 4 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...workspace, runs: [{ id: "run-1", revision: 4, format: "preview", status: "queued", error: null }] }), { status: 202 }));

    await expect(getTimeline("project/001")).resolves.toEqual(workspace);
    await expect(saveTimeline("project/001", workspace)).resolves.toMatchObject({ revision: 4 });
    await expect(startTimelineRun("project/001", 4, "preview")).resolves.toMatchObject({ runs: [{ id: "run-1" }] });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/projects/project%2F001/timeline", {});
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/projects/project%2F001/timeline", {
      method: "PUT", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" },
      body: JSON.stringify({ revision: 3, settings: workspace.settings, tracks: workspace.tracks }),
    });
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/projects/project%2F001/timeline/runs", {
      method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" },
      body: JSON.stringify({ revision: 4, format: "preview" }),
    });
  });

  it("取消任务、音频上传和完成输出地址不会混用素材或任务路径", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(workspace), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(workspace), { status: 200 }));
    const file = new File(["voice"], "voice.webm", { type: "audio/webm" });

    await cancelTimelineRun("project/001", "run/001");
    await uploadTimelineAudio("project/001", file);

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/projects/project%2F001/timeline/runs/run%2F001/cancel", {
      method: "POST", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" }, body: "{}",
    });
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/projects/project%2F001/preproduction/assets?role=audio", { method: "POST", body: expect.any(FormData) });
    expect(timelineOutputUrl("project/001", "run/001")).toBe("/api/projects/project%2F001/timeline/runs/run%2F001/output");
  });
});
