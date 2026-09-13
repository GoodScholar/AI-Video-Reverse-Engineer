import { beforeEach, expect, it, vi } from "vitest";

import { readApiError, referenceMediaContentUrl, uploadReferenceMedia } from "./referenceMediaApi";

const project = {
  id: "project-001",
  name: "雨夜人像复刻",
  createdAt: "2026-09-10T10:00:00+00:00",
  updatedAt: "2026-09-11T10:00:00+00:00",
  referenceMedia: {
    type: "image" as const,
    id: "image-001",
    originalName: "hero.png",
    format: "png" as const,
    sizeBytes: 11,
    width: 1200,
    height: 1600,
    hasTransparency: true,
  },
  localPreprocessing: null,
};

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

it("将文件上传到统一参考素材路由并返回完整项目", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(project), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  const file = new File(["image-bytes"], "hero.png", { type: "image/png" });

  await expect(uploadReferenceMedia("project/id", file)).resolves.toEqual(project);

  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe("/api/projects/project%2Fid/reference-media");
  expect(init?.method).toBe("PUT");
  expect(init?.body).toBeInstanceOf(FormData);
  expect([...((init?.body as FormData).entries())]).toEqual([["file", file]]);
  expect(init?.headers).toBeUndefined();
});

it("为图片静态预览生成统一内容地址", () => {
  expect(referenceMediaContentUrl("project/id")).toBe("/api/projects/project%2Fid/reference-media/content");
});

it("以素材 ID 为同项目静态预览提供稳定版本", () => {
  expect(referenceMediaContentUrl("project/id", "image/002"))
    .toBe("/api/projects/project%2Fid/reference-media/content?version=image%2F002");
});

it("为失败上传保留服务端错误文案", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({
    detail: { code: "image_too_large", message: "参考图片不能超过 30 MB。" },
  }), { status: 422, headers: { "Content-Type": "application/json" } }));

  await expect(uploadReferenceMedia("project-001", new File(["image"], "hero.png")))
    .rejects.toThrow("参考图片不能超过 30 MB。");
});

it("沿用结构化 API 错误解析", async () => {
  const response = new Response(JSON.stringify({ detail: "本地项目存储不可用" }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });

  await expect(readApiError(response, "无法上传并校验参考素材，请检查文件后重试。"))
    .resolves.toBe("本地项目存储不可用");
});

it("保留对象格式的服务端错误说明", async () => {
  const response = new Response(JSON.stringify({ detail: { code: "image_invalid", message: "参考图片无法读取。" } }), {
    status: 422,
    headers: { "Content-Type": "application/json" },
  });

  await expect(readApiError(response, "无法上传并校验参考素材，请检查文件后重试。"))
    .resolves.toBe("参考图片无法读取。");
});

it("非 JSON 错误使用稳定上传兜底", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response("服务暂不可用", { status: 503 }));

  await expect(uploadReferenceMedia("project-001", new File(["image"], "hero.png")))
    .rejects.toThrow("无法上传并校验参考素材，请检查文件后重试。");
});

it("网络失败转换为稳定中文连接错误", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new TypeError("network failed"));

  await expect(uploadReferenceMedia("project-001", new File(["image"], "hero.png")))
    .rejects.toThrow("无法连接本地服务，请确认应用服务正在运行后重试。");
});

it("缺少可读 detail 时使用调用方兜底", async () => {
  const response = new Response(JSON.stringify({ detail: { code: "image_invalid" } }), {
    status: 422,
    headers: { "Content-Type": "application/json" },
  });

  await expect(readApiError(response, "无法上传并校验参考素材，请检查文件后重试。"))
    .resolves.toBe("无法上传并校验参考素材，请检查文件后重试。");
});
