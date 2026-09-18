import { beforeEach, describe, expect, it, vi } from "vitest";

import { getVideoUpscaleState, startVideoUpscale, upscaleVideoUrl } from "./videoUpscaleApi";

const run = {
  id: "upscale-001",
  sourceId: "video-001",
  scale: 2 as const,
  status: "completed" as const,
  stage: "完成",
  progress: 100,
  error: null,
  output: { width: 3840, height: 2160, frameRate: 24, durationSeconds: 5 },
  createdAt: "2026-09-16T10:00:00Z",
};

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
});

describe("videoUpscaleApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("以既有敏感请求意图提交当前视频和目标清晰度", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(run, 202));

    await expect(startVideoUpscale("project/001", "video-001", "1080p")).resolves.toEqual(run);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2F001/upscale",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" },
        body: JSON.stringify({ sourceId: "video-001", outputResolution: "1080p" }),
      },
    );
  });

  it("读取环境与任务，并构造编码后的预览和下载地址", async () => {
    const state = { environment: { available: true, message: "Real-ESRGAN 已就绪" }, runs: [run] };
    vi.mocked(fetch).mockResolvedValueOnce(response(state));

    await expect(getVideoUpscaleState("project/001")).resolves.toEqual(state);
    expect(fetch).toHaveBeenCalledWith("/api/projects/project%2F001/upscale");
    expect(upscaleVideoUrl("project/001", "upscale/001")).toBe(
      "/api/projects/project%2F001/upscale/upscale%2F001/video",
    );
    expect(upscaleVideoUrl("project/001", "upscale/001", true)).toBe(
      "/api/projects/project%2F001/upscale/upscale%2F001/video?download=true",
    );
  });
});
