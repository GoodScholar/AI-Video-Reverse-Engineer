import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CreativeCanvasHistory } from "./CreativeCanvasHistory";
import type { CreativeCanvasAsset, CreativeCanvasBatch, CreativeCanvasDocument, CreativeCanvasRun } from "./creativeCanvasApi";

const assets: CreativeCanvasAsset[] = [{ id: "asset-a", name: "候选 A", kind: "image", url: "/a.png" }, { id: "asset-b", name: "候选 B", kind: "image", url: "/b.png" }];
const document: CreativeCanvasDocument = { revision: 8, nodes: [{ id: "node-a", kind: "generate", position: { x: 0, y: 0 }, label: "海报", currentOutputRef: { runId: "run-a", resultId: "result-a" }, outputAssetId: "asset-a" }], edges: [], viewport: { x: 0, y: 0, zoom: 1 }, settings: {} };
const run: CreativeCanvasRun = { id: "run-a", nodeId: "node-a", status: "completed", error: null, promptId: null, createdAt: "2026-09-30T00:00:00Z", inputAssetIds: [], outputAssetIds: ["asset-a", "asset-b"], snapshot: {}, outputItems: [
  { resultId: "result-a", assetId: "asset-a", mediaType: "image", status: "installed", sourceRunId: "run-a", createdAt: "2026-09-30T00:00:00Z" },
  { resultId: "result-b", assetId: "asset-b", mediaType: "image", status: "installed", sourceRunId: "run-a", createdAt: "2026-09-30T00:01:00Z" },
  { resultId: "result-failed", mediaType: "image", status: "failed", sourceRunId: "run-a", createdAt: "2026-09-30T00:02:00Z", error: "供应商超时" },
  { resultId: "result-running", mediaType: "image", status: "running", sourceRunId: "run-a", createdAt: "2026-09-30T00:02:30Z" },
  { resultId: "result-stopping", mediaType: "image", status: "cancel_requested", sourceRunId: "run-a", createdAt: "2026-09-30T00:02:45Z", cancellationRequested: true, localDisposition: "none", providerDisposition: "unsupported", billingUnknown: true },
  { resultId: "result-deleted", assetId: "asset-a", mediaType: "image", status: "deleted", sourceRunId: "run-a", createdAt: "2026-09-30T00:03:00Z" },
] };
const batch: CreativeCanvasBatch = { id: "batch-a", canvasId: "canvas-a", status: "running", createdAt: "2026-09-30T00:00:00Z", documentRevision: 8, documentSnapshot: document, steps: [{ nodeId: "node-a", dependsOn: [], idempotencyKey: "step-a", status: "running", runId: "run-a" }] };

function props() {
  return { document, runs: [run], assets, batches: [batch], onClose: vi.fn(), onRefreshRun: vi.fn(), onSelectOutput: vi.fn(), onDeleteOutput: vi.fn(), onRetryOutput: vi.fn(), onCancelOutput: vi.fn(), onBranchOutput: vi.fn(), onCancelBatch: vi.fn(), onPreviewAsset: vi.fn(), onDownloadAsset: vi.fn() };
}

describe("CreativeCanvasHistory", () => {
  it("逐项以稳定 resultId 动作，当前选择优先 currentOutputRef，已删除项不回退", async () => {
    const input = props();
    render(<div className="cs-root"><CreativeCanvasHistory {...input} /></div>);
    expect(screen.getByText("当前输出")).toBeVisible();
    expect(screen.getByRole("button", { name: "设为当前输出 候选 B" })).toBeVisible();
    expect(screen.getByLabelText("已删除")).toBeVisible();
    expect(screen.queryByRole("button", { name: "设为当前输出 候选 A" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "设为当前输出 候选 B" }));
    await userEvent.click(screen.getByRole("button", { name: "重试结果 result-failed" }));
    await userEvent.click(screen.getByRole("button", { name: "取消结果 result-running" }));
    await userEvent.click(screen.getByRole("button", { name: "预览结果 候选 A" }));
    await userEvent.click(screen.getByRole("button", { name: "下载结果 候选 A" }));
    expect(input.onSelectOutput).toHaveBeenCalledWith("run-a", "result-b");
    expect(input.onRetryOutput).toHaveBeenCalledWith("run-a", "result-failed");
    expect(input.onCancelOutput).toHaveBeenCalledWith("run-a", "result-running");
    expect(input.onPreviewAsset).toHaveBeenCalledWith(assets[0]);
    expect(input.onDownloadAsset).toHaveBeenCalledWith(assets[0]);
  });

  it("旧 outputAssetIds 只在没有 outputItems 时投影为稳定 legacy resultId", async () => {
    const input = props();
    input.runs = [{ ...run, outputItems: undefined }];
    render(<div className="cs-root"><CreativeCanvasHistory {...input} /></div>);
    await userEvent.click(screen.getByRole("button", { name: "设为当前输出 候选 B" }));
    expect(input.onSelectOutput).toHaveBeenCalledWith("run-a", "run-a:legacy:1");
  });

  it("选中的已删除结果保持其身份，不回退为同 run 的其他图片", () => {
    const input = props();
    input.document = { ...document, nodes: [{ ...document.nodes[0], currentOutputRef: { runId: "run-a", resultId: "result-deleted" } }] };
    render(<div className="cs-root"><CreativeCanvasHistory {...input} /></div>);
    expect(screen.getByText("当前输出（已删除）")).toBeVisible();
    expect(screen.getByRole("button", { name: "设为当前输出 候选 A" })).toBeVisible();
  });

  it("展示批次步骤的节点名称与状态，并只为可取消批次请求取消", async () => {
    const input = props();
    render(<div className="cs-root"><CreativeCanvasHistory {...input} /></div>);
    expect(screen.getByText("海报 · 执行中")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "取消批次 batch-a" }));
    expect(input.onCancelBatch).toHaveBeenCalledWith("batch-a");
  });

  it("批次被依赖阻断时显示明确状态", () => {
    const input = props();
    input.batches = [{ ...batch, status: "blocked", steps: [{ ...batch.steps[0], status: "blocked", error: "prior_result_unknown" }] }];
    render(<div className="cs-root"><CreativeCanvasHistory {...input} /></div>);
    expect(screen.getByText("批量生成 · 已阻止")).toBeVisible();
    expect(screen.getByText("海报 · 已阻止")).toBeVisible();
  });

  it("逐项写操作繁忙时禁用，预览和下载仍保持本地可达", () => {
    const input = props();
    render(<div className="cs-root"><CreativeCanvasHistory {...input} busy /></div>);
    expect(screen.getByRole("button", { name: "设为当前输出 候选 B" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "删除结果 result-a" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重试结果 result-failed" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消结果 result-running" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "预览结果 候选 A" })).toBeEnabled();
  });

  it("候选取消状态如实呈现远端未确认和未知不可重试", () => {
    const input = props();
    input.runs = [{ ...run, outputItems: [...run.outputItems!, { resultId: "result-unknown", mediaType: "image", status: "unknown", sourceRunId: "run-a", createdAt: "2026-09-30T00:04:00Z" }] }];
    render(<div className="cs-root"><CreativeCanvasHistory {...input} /></div>);
    expect(screen.getByText("停止已请求；供应商未确认停止，计费状态未知。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "重试结果 result-unknown" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消结果 result-stopping" })).not.toBeInTheDocument();
  });

  it("部分成功与取消后晚到结果不会显示为完整成功", () => {
    const input = props();
    input.runs = [{ ...run, outputItems: [
      run.outputItems![0],
      { resultId: "result-late", assetId: "asset-b", mediaType: "image", status: "installed", sourceRunId: "run-a", createdAt: "2026-09-30T00:05:00Z", cancellationRequested: true, billingUnknown: true },
      run.outputItems![2],
    ] }];
    render(<div className="cs-root"><CreativeCanvasHistory {...input} /></div>);
    expect(screen.getByText("部分状态未知")).toBeVisible();
    expect(screen.getByText("已安装（取消后晚到，未自动采用）")).toBeVisible();
  });
});
