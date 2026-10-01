import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadPreproductionPackage,
  getCanvasLayout,
  getPreproductionWorkspace,
  importPreparationShots,
  probeYoutubeVideo,
  importYoutubeVideo,
  runPreproductionNode,
  savePreproductionWorkspace,
  saveCanvasLayout,
  uploadPreproductionAsset,
  type PreproductionWorkspace,
} from "./preproductionApi";

const workspace: PreproductionWorkspace = {
  schemaVersion: 2,
  revision: 3,
  brief: { theme: "雨夜追踪", purpose: "预告片", style: "电影感", duration: 12, aspect: "16:9", mustPreserve: "人物服装" },
  assets: [], scenes: [{ id: "scene-default", title: "未分场", rank: "00000001", description: "" }], shots: [],
  workflow: { nodes: [], edges: [] },
  canvasLayout: { scope: { type: "project", id: "project-001" }, layoutRevision: 0, nodes: {} },
  checks: [], nodeCatalog: [{ kind: "reference", label: "引用素材" }],
};

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
});

describe("preproductionApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("以项目编码和当前 revision 保存可编辑工作台状态", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace));

    await expect(savePreproductionWorkspace("project/001", workspace)).resolves.toEqual(workspace);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2F001/preproduction",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ revision: 3, brief: workspace.brief, scenes: workspace.scenes, shots: [] }) }),
    );
  });

  it("读取 v2 工作区时保留场景、只读关系和独立布局契约", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({
      ...workspace,
      schemaVersion: 2,
      scenes: [{ id: "scene-default", title: "未分场", rank: "00000001", description: "" }],
      shots: [],
      workflow: { nodes: [{ id: "shot:shot-a", type: "shot", shotId: "shot-a" }], edges: [] },
      canvasLayout: {
        scope: { type: "project", id: "project-001" }, layoutRevision: 0, nodes: {},
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    }));

    const loaded = await getPreproductionWorkspace("project-001");

    expect(loaded.schemaVersion).toBe(2);
    expect(loaded.scenes[0].id).toBe("scene-default");
    expect(loaded.workflow.nodes[0]).toMatchObject({ type: "shot", shotId: "shot-a" });
    expect(loaded.canvasLayout.layoutRevision).toBe(0);
  });

  it("按范围读取和以独立 revision 保存画布布局", async () => {
    const layout = {
      scope: { type: "shot" as const, id: "shot/001" },
      layoutRevision: 4,
      nodes: { "process:shot%2F001:trim": { x: 120, y: 80, width: 220, height: 104 } },
      viewport: { x: -20, y: 10, zoom: 1.4 },
    };
    vi.mocked(fetch).mockResolvedValueOnce(response(layout)).mockResolvedValueOnce(response({ ...layout, layoutRevision: 5 }));

    await expect(getCanvasLayout("project/001", layout.scope)).resolves.toEqual(layout);
    await expect(saveCanvasLayout("project/001", layout)).resolves.toEqual({ ...layout, layoutRevision: 5 });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/projects/project%2F001/preproduction/layouts/shot/shot%2F001", {});
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/projects/project%2F001/preproduction/layouts/shot/shot%2F001", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ layoutRevision: 4, nodes: layout.nodes, viewport: layout.viewport }),
    }));
  });

  it("从现有分镜导入时携带当前 revision", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace));

    await importPreparationShots("project-001", 3);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project-001/preproduction/import-shots",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 3 }) }),
    );
  });

  it("运行节点使用保存后的 revision，不隐式运行其他节点", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace, 202));

    await runPreproductionNode("project-001", "shot/001", "node/001", 3);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project-001/preproduction/shots/shot%2F001/nodes/node%2F001/run",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 3 }) }),
    );
  });

  it("所有 JSON 变更请求声明语义分析意图，供本地服务校验来源", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace));

    await importPreparationShots("project-001", 3);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project-001/preproduction/import-shots",
      expect.objectContaining({ headers: { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" } }),
    );
  });

  it("上传几何处理结果时随真实文件提交可验证的来源操作记录", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace));
    const file = new File(["png"], "产品图-裁切.png", { type: "image/png" });
    const derivation = {
      sourceAssetId: "asset-source",
      operation: "crop" as const,
      params: { x: 0, y: 0, width: 0.5, height: 1 },
      operationId: "geometry-op-001",
      index: 0,
      count: 1,
    };

    await uploadPreproductionAsset("project-001", "reference", file, derivation);

    const body = vi.mocked(fetch).mock.calls[0][1]?.body as FormData;
    expect(body.get("file")).toBe(file);
    expect(body.get("derivation")).toBe(JSON.stringify(derivation));
  });

  it("本地视频上传仅在确认使用权后声明确认字段", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace));
    const file = new File(["video"], "素材.mp4", { type: "video/mp4" });

    await uploadPreproductionAsset("project-001", "reference", file, undefined, true);

    const body = vi.mocked(fetch).mock.calls[0][1]?.body as FormData;
    expect(body.get("rightsConfirmed")).toBe("true");
  });

  it("YouTube 探测与确认导入使用明确的外发披露和权利字段", async () => {
    const probe = { canonicalUrl: "https://www.youtube.com/watch?v=YE7VzlLtp-4", videoId: "YE7VzlLtp-4", title: "授权样片", durationSeconds: 1, uploader: "owner", status: "available" as const, adapter: { id: "yt-dlp", version: "2026.08.19" }, externalRequest: { probeHosts: ["www.youtube.com"], downloadHosts: ["*.googlevideo.com"], purpose: "读取公开视频元数据；确认后下载", sessionPolicy: "默认不读取会话" }, lastRealVerificationAt: null };
    vi.mocked(fetch).mockResolvedValueOnce(response(probe)).mockResolvedValueOnce(response({ run: { id: "youtube-import-1", status: "queued", error: null, createdAt: "2026-10-02T00:00:00Z", inputFingerprint: "hash", idempotencyKey: "key", retryOf: null, uploadedAssetId: null } }));

    await expect(probeYoutubeVideo("project-001", "https://youtu.be/YE7VzlLtp-4", true)).resolves.toEqual(probe);
    await importYoutubeVideo("project-001", { url: "https://youtu.be/YE7VzlLtp-4", role: "reference", rightsConfirmed: true, externalRequestAccepted: true, useSession: true });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/projects/project-001/preproduction/youtube/probe", expect.objectContaining({ method: "POST", body: JSON.stringify({ url: "https://youtu.be/YE7VzlLtp-4", externalRequestAccepted: true, useSession: true }) }));
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/projects/project-001/preproduction/youtube/import", expect.objectContaining({ method: "POST", body: JSON.stringify({ url: "https://youtu.be/YE7VzlLtp-4", role: "reference", rightsConfirmed: true, externalRequestAccepted: true, useSession: true }) }));
  });

  it("打包失败时读取后端检查错误而不下载旧结果", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({ detail: "存在过期节点" }, 409));

    await expect(downloadPreproductionPackage("project-001", 3)).rejects.toThrow("存在过期节点");
  });
});
