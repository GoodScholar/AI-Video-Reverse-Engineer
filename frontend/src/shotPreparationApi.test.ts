import { beforeEach, describe, expect, it, vi } from "vitest";

import { applyToolkitTimeline, extractShotPersonControl, getShotPreparation, shotRepresentativeFrameUrl } from "./shotPreparationApi";

describe("shotPreparationApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("使用版本来源和本地人物控制意图提交当前镜头", async () => {
    const response = { sourceId: "video-001", preprocessingId: "pre-001", revision: 4, shots: [], canAnalyze: false };
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 202, headers: { "Content-Type": "application/json" } }));

    await expect(extractShotPersonControl("project/001", "shot/001", {
      sourceId: "video-001", preprocessingId: "pre-001", revision: 3,
    })).resolves.toEqual(response);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2F001/preparation/shots/shot%2F001/person-control",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" },
        body: JSON.stringify({ sourceId: "video-001", preprocessingId: "pre-001", revision: 3 }),
      },
    );
  });

  it("以当前草稿版本应用已保存的视频工具切点", async () => {
    const response = { sourceId: "video-001", preprocessingId: "pre-001", revision: 5, shots: [], canAnalyze: false, timelineOverride: { toolkitRunId: "run-001", cutRevision: 2 }, toolkitScenes: [] };
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(applyToolkitTimeline("project/001", {
      sourceId: "video-001", preprocessingId: "pre-001", revision: 4, toolkitRunId: "run/001", cutRevision: 2,
    })).resolves.toEqual(response);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2F001/preparation/timeline/apply",
      expect.objectContaining({ body: JSON.stringify({ sourceId: "video-001", preprocessingId: "pre-001", revision: 4, toolkitRunId: "run/001", cutRevision: 2 }) }),
    );
  });

  it("代表帧地址带时间线修订，避免旧镜头图继续显示", () => {
    expect(shotRepresentativeFrameUrl("project/001", "shot/001", "video-001", "pre-001", 7))
      .toBe("/api/projects/project%2F001/preparation/shots/shot%2F001/frame?sourceId=video-001&preprocessingId=pre-001&revision=7");
  });

  it("兼容尚未写入时间线联动字段的已保存镜头准备状态", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ sourceId: "video-001", preprocessingId: "pre-001", revision: 1, shots: [], canAnalyze: false }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(getShotPreparation("project-001")).resolves.toMatchObject({ timelineOverride: null, toolkitScenes: [] });
  });
});
