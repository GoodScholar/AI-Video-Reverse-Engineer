import { afterEach, expect, it, vi } from "vitest";

import {
  cancelCreativeCanvasRun,
  cancelCreativeCanvasBatch,
  cancelCreativeCanvasOutput,
  cancelCreativeCanvasTextRun,
  installCreativeCanvasTextVersion,
  refreshCreativeCanvasBatch,
  runCreativeCanvasTextNode,
  selectCreativeCanvasTextVersion,
  createCreativeCanvas,
  deleteCreativeCanvas,
  getCreativeCanvas,
  listCreativeCanvases,
  refreshCreativeCanvasRun,
  renameCreativeCanvas,
  restoreCreativeCanvas,
  runCreativeCanvasNode,
  deleteCreativeCanvasOutput,
  retryCreativeCanvasOutput,
  saveCreativeCanvas,
  selectCreativeCanvasOutput,
  runCreativeCanvasBatch,
  type CreativeCanvasDocument,
} from "./creativeCanvasApi";

const document: CreativeCanvasDocument = { revision: 3, nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 }, settings: {} };
const workspace = { document, runs: [], assets: [] };

function fetchOk(payload: unknown = workspace) {
  return vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })));
}

afterEach(() => vi.unstubAllGlobals());

it("不传 canvasId 时保留旧画布请求 URL 与载荷", async () => {
  const fetcher = fetchOk();
  vi.stubGlobal("fetch", fetcher);

  await getCreativeCanvas("project / 1");
  await saveCreativeCanvas("project / 1", document);
  await runCreativeCanvasNode("project / 1", "node / 1", 3, "attempt-1", undefined, 3);
  await refreshCreativeCanvasRun("project / 1", "run / 1");
  await cancelCreativeCanvasRun("project / 1", "run / 1");

  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/projects/project%20%2F%201/creative-canvas",
    "/api/projects/project%20%2F%201/creative-canvas",
    "/api/projects/project%20%2F%201/creative-canvas/nodes/node%20%2F%201/runs",
    "/api/projects/project%20%2F%201/creative-canvas/runs/run%20%2F%201/refresh",
    "/api/projects/project%20%2F%201/creative-canvas/runs/run%20%2F%201/cancel",
  ]);
  expect(fetcher.mock.calls[2][1]).toEqual(expect.objectContaining({ body: JSON.stringify({ revision: 3, idempotencyKey: "attempt-1", count: 3 }) }));
});

it("目录 CRUD 与所有画布工作区请求都显式携带 canvasId", async () => {
  const canvas = { id: "canvas-a", name: "草稿 A", createdAt: "2026-09-30T00:00:00Z", updatedAt: "2026-09-30T00:00:00Z", deletedAt: null };
  const fetcher = fetchOk({ canvases: [canvas] });
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ canvases: [canvas] }), { status: 200 }));
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ canvas, ...workspace }), { status: 201 }));
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ canvas }), { status: 200 }));
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ canvas }), { status: 200 }));
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ canvas }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);

  await listCreativeCanvases("p1", true);
  await createCreativeCanvas("p1", "草稿 A");
  await renameCreativeCanvas("p1", "canvas-a", "草稿 B");
  await deleteCreativeCanvas("p1", "canvas-a");
  await restoreCreativeCanvas("p1", "canvas-a");
  await getCreativeCanvas("p1", "canvas a");
  await saveCreativeCanvas("p1", document, "canvas a");
  await runCreativeCanvasNode("p1", "node-1", 3, undefined, "canvas a");
  await refreshCreativeCanvasRun("p1", "run-1", "canvas a");
  await cancelCreativeCanvasRun("p1", "run-1", "canvas a");

  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/projects/p1/creative-canvas/canvases?includeDeleted=true",
    "/api/projects/p1/creative-canvas/canvases",
    "/api/projects/p1/creative-canvas/canvases/canvas-a",
    "/api/projects/p1/creative-canvas/canvases/canvas-a",
    "/api/projects/p1/creative-canvas/canvases/canvas-a/restore",
    "/api/projects/p1/creative-canvas?canvasId=canvas%20a",
    "/api/projects/p1/creative-canvas?canvasId=canvas%20a",
    "/api/projects/p1/creative-canvas/nodes/node-1/runs?canvasId=canvas%20a",
    "/api/projects/p1/creative-canvas/runs/run-1/refresh?canvasId=canvas%20a",
    "/api/projects/p1/creative-canvas/runs/run-1/cancel?canvasId=canvas%20a",
  ]);
  expect(fetcher.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "草稿 A" }) }));
  expect(fetcher.mock.calls[2][1]).toEqual(expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "草稿 B" }) }));
  expect(fetcher.mock.calls[3][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  expect(fetcher.mock.calls[7][1]).toEqual(expect.objectContaining({ body: JSON.stringify({ revision: 3 }) }));
});

it("逐项结果动作与批量执行带稳定结果身份、版本和画布范围", async () => {
  const fetcher = fetchOk();
  vi.stubGlobal("fetch", fetcher);

  await selectCreativeCanvasOutput("p / 1", "run / 1", "result / 1", 7, "canvas / 1");
  await deleteCreativeCanvasOutput("p / 1", "run / 1", "result / 1", 8, "canvas / 1");
  await retryCreativeCanvasOutput("p / 1", "run / 1", "result / 1", 9, "canvas / 1");
  await cancelCreativeCanvasOutput("p / 1", "run / 1", "result / 1", "canvas / 1");
  await runCreativeCanvasBatch("p / 1", ["node-1", "node / 2"], 10, "batch-1", "canvas / 1");
  await cancelCreativeCanvasBatch("p / 1", "batch / 1", "canvas / 1");
  await refreshCreativeCanvasBatch("p / 1", "batch / 1", "canvas / 1");

  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/projects/p%20%2F%201/creative-canvas/runs/run%20%2F%201/outputs/result%20%2F%201/select?canvasId=canvas%20%2F%201",
    "/api/projects/p%20%2F%201/creative-canvas/runs/run%20%2F%201/outputs/result%20%2F%201?canvasId=canvas%20%2F%201",
    "/api/projects/p%20%2F%201/creative-canvas/runs/run%20%2F%201/outputs/result%20%2F%201/retry?canvasId=canvas%20%2F%201",
    "/api/projects/p%20%2F%201/creative-canvas/runs/run%20%2F%201/outputs/result%20%2F%201/cancel?canvasId=canvas%20%2F%201",
    "/api/projects/p%20%2F%201/creative-canvas/batches?canvasId=canvas%20%2F%201",
    "/api/projects/p%20%2F%201/creative-canvas/batches/batch%20%2F%201/cancel?canvasId=canvas%20%2F%201",
    "/api/projects/p%20%2F%201/creative-canvas/batches/batch%20%2F%201/refresh?canvasId=canvas%20%2F%201",
  ]);
  expect(fetcher.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 7 }) }));
  expect(fetcher.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "DELETE", body: JSON.stringify({ revision: 8 }) }));
  expect(fetcher.mock.calls[2][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 9 }) }));
  expect(fetcher.mock.calls[3][1]).toEqual(expect.objectContaining({ method: "POST" }));
  expect(fetcher.mock.calls[3][1]).not.toHaveProperty("body");
  expect(fetcher.mock.calls[4][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 10, nodeIds: ["node-1", "node / 2"], idempotencyKey: "batch-1" }) }));
  expect(fetcher.mock.calls[5][1]).toEqual(expect.objectContaining({ method: "POST", body: "{}" }));
  expect(fetcher.mock.calls[6][1]).toEqual(expect.objectContaining({ method: "POST", body: "{}" }));
});

it("文本运行使用独立的持久端点，保留 revision 与幂等键", async () => {
  const fetcher = fetchOk();
  vi.stubGlobal("fetch", fetcher);

  await runCreativeCanvasTextNode("p / 1", "text / 1", 11, "text-attempt", "canvas / 1");

  expect(fetcher).toHaveBeenCalledWith(
    "/api/projects/p%20%2F%201/creative-canvas/nodes/text%20%2F%201/text-runs?canvasId=canvas%20%2F%201",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 11, idempotencyKey: "text-attempt" }) }),
  );
});

it("文本版本采用与取消使用运行及版本的稳定身份", async () => {
  const fetcher = fetchOk();
  vi.stubGlobal("fetch", fetcher);

  await selectCreativeCanvasTextVersion("p / 1", "run / 1", "version / 1", 12, "canvas / 1");
  await cancelCreativeCanvasTextRun("p / 1", "run / 1", "canvas / 1");

  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/projects/p%20%2F%201/creative-canvas/text-runs/run%20%2F%201/versions/version%20%2F%201/select?canvasId=canvas%20%2F%201",
    "/api/projects/p%20%2F%201/creative-canvas/text-runs/run%20%2F%201/cancel?canvasId=canvas%20%2F%201",
  ]);
  expect(fetcher.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 12 }) }));
  expect(fetcher.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST", body: "{}" }));
});

it("文本版本入库不提交伪 revision，服务端以版本身份保证幂等", async () => {
  const fetcher = fetchOk();
  vi.stubGlobal("fetch", fetcher);

  await installCreativeCanvasTextVersion("p / 1", "run / 1", "version / 1", "canvas / 1");

  expect(fetcher).toHaveBeenCalledWith(
    "/api/projects/p%20%2F%201/creative-canvas/text-runs/run%20%2F%201/versions/version%20%2F%201/assets?canvasId=canvas%20%2F%201",
    expect.objectContaining({ method: "POST", body: "{}" }),
  );
});
