import { beforeEach, describe, expect, it, vi } from "vitest";

import { getProject, startLocalPreprocessing } from "./localPreprocessingApi";

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("localPreprocessingApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("以无正文 POST 启动编码后的项目", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ id: "project/001" }, 202));

    await startLocalPreprocessing("project/001");

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2F001/local-preprocessing",
      { method: "POST" },
    );
  });

  it("读取编码后的项目并返回后端响应", async () => {
    const project = { id: "project/001", localPreprocessing: null };
    vi.mocked(fetch).mockResolvedValue(response(project));

    await expect(getProject("project/001")).resolves.toEqual(project);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/projects/project%2F001");
  });

  it("原样返回结构化后端错误", async () => {
    vi.mocked(fetch).mockResolvedValue(response({
      detail: { code: "reference_video_required", message: "请先添加并校验参考素材。" },
    }, 409));

    await expect(startLocalPreprocessing("project-001"))
      .rejects.toThrow("请先添加并校验参考素材。");
  });

  it("原样返回字符串后端错误", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ detail: "本地项目存储不可用" }, 503));

    await expect(getProject("project-001")).rejects.toThrow("本地项目存储不可用");
  });

  it("连接失败时给出本地服务说明", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));

    await expect(getProject("project-001"))
      .rejects.toThrow("无法连接本地服务，请确认应用服务正在运行后重试。");
  });
});
