import { describe, expect, it } from "vitest";
import { canvasNodeOutput } from "./creativeCanvasOutputs";
import type { CreativeCanvasAsset, CreativeCanvasNode, CreativeCanvasRun } from "./creativeCanvasApi";
const assets: CreativeCanvasAsset[] = ["old", "new"].map(id => ({ id, name: id, kind: "image", url: `/${id}` }));
const node: CreativeCanvasNode = { id: "generate-1", kind: "generate", position: { x: 0, y: 0 } };
const run: CreativeCanvasRun = { id: "run-1", nodeId: node.id, status: "completed", error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: ["old", "new"], snapshot: {} };
describe("画布当前输出身份", () => {
  it("旧运行投影结果 ID 可选第二张，后来的运行不替换明确选择", () => {
    expect(canvasNodeOutput({ ...node, currentOutputRef: { runId: run.id, resultId: "run-1:legacy:1" } }, [run, { ...run, id: "later", outputAssetIds: ["old"] }], assets)?.id).toBe("new");
  });
  it("删除的结果不因为 outputAssetIds 兼容字段仍存在而复活", () => {
    const deleted: CreativeCanvasRun = { ...run, outputItems: [{ resultId: "result-1", assetId: "new", mediaType: "image", status: "deleted", sourceRunId: run.id, createdAt: run.createdAt }] };
    expect(canvasNodeOutput({ ...node, outputAssetId: "new", currentOutputRef: { runId: run.id, resultId: "result-1" } }, [deleted], assets)).toBeUndefined();
    expect(canvasNodeOutput(node, [{ ...run, id: "older" }, deleted], assets)).toBeUndefined();
  });
  it("取消后晚到的已安装结果不会成为节点的隐式输出", () => {
    const cancelled: CreativeCanvasRun = { ...run, outputItems: [{ resultId: "result-1", assetId: "new", mediaType: "image", status: "installed", sourceRunId: run.id, createdAt: run.createdAt, cancellationRequested: true, billingUnknown: true }] };
    expect(canvasNodeOutput(node, [cancelled], assets)).toBeUndefined();
  });
  it("不接受其他节点运行或缺失素材，并保留旧显式素材选择", () => {
    expect(canvasNodeOutput({ ...node, currentOutputRef: { runId: run.id, resultId: "run-1:legacy:0" } }, [{ ...run, nodeId: "other" }], assets)).toBeUndefined();
    expect(canvasNodeOutput({ ...node, outputAssetId: "old" }, [run], assets)?.id).toBe("old");
    expect(canvasNodeOutput({ ...node, outputAssetId: "missing" }, [run], assets)).toBeUndefined();
  });
});
