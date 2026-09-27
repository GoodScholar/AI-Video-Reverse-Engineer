import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { ContentProductionWorkspace } from "./ContentProductionWorkspace";
import { getContentWorkflow, type ContentWorkflowProjection } from "./contentWorkflowApi";

vi.mock("./contentWorkflowApi", () => ({ getContentWorkflow: vi.fn() }));
vi.mock("./AigcCreator", () => ({ AigcCreator: () => <section aria-label="创作内容" /> }));
vi.mock("./BatchEditor", () => ({ BatchEditor: ({ onPersistedChange }: { onPersistedChange?: () => void }) =>
  <section aria-label="批量制作"><button type="button" onClick={onPersistedChange}>保存批量更改</button></section> }));
vi.mock("./PreproductionWorkspace", () => ({ PreproductionWorkspace: () => <section aria-label="项目素材" /> }));

const project = { id: "p1", name: "商品项目", createdAt: "2026-09-27T00:00:00Z", updatedAt: "2026-09-27T00:00:00Z",
  referenceMedia: null, localPreprocessing: null };

function projection(update: Partial<ContentWorkflowProjection> = {}): ContentWorkflowProjection {
  return {
    stage: "draft",
    currentStep: 0,
    completedSteps: [false, false, false, false],
    activeGenerationId: null,
    activeBatchId: null,
    counts: { total: 0, pending: 0, approved: 0, rejected: 0, delivered: 0, failed: 0 },
    variants: [],
    issues: [],
    ...update,
  };
}

function showWorkspace() {
  return render(<ContentProductionWorkspace project={project} page="create" tools={null}
    onNavigate={vi.fn()} onDirtyChange={vi.fn()} />);
}

beforeEach(() => { vi.mocked(getContentWorkflow).mockReset(); });

it("按服务端投影恢复选声步骤和活动批次", async () => {
  vi.mocked(getContentWorkflow).mockResolvedValue(projection({ stage: "scripts_confirmed", currentStep: 2,
    completedSteps: [true, true, false, false], activeGenerationId: "g1", activeBatchId: "batch-1" }));

  showWorkspace();

  expect(await screen.findByRole("button", { name: "3 选声制作" })).toHaveAttribute("aria-current", "step");
  expect(screen.getByRole("button", { name: "进入审核导出" })).toBeDisabled();
});

it("点击已完成步骤只改变当前视图而不改完成事实", async () => {
  vi.mocked(getContentWorkflow).mockResolvedValue(projection({ stage: "preview_ready", currentStep: 3,
    completedSteps: [true, true, true, false], activeGenerationId: "g1", activeBatchId: "batch-1" }));
  showWorkspace();
  await screen.findByRole("button", { name: "4 审核导出" });

  await userEvent.click(screen.getByRole("button", { name: "准备资料" }));

  expect(screen.getByRole("button", { name: "准备资料" })).toHaveAttribute("aria-current", "step");
  expect(screen.getByRole("button", { name: "选声制作" })).toHaveClass("is-done");
});

it("投影读取失败时显示真实错误而不伪装为第一步", async () => {
  vi.mocked(getContentWorkflow).mockRejectedValue(new Error("内容制作进度无法读取，请检查项目数据。"));

  showWorkspace();

  expect(await screen.findByRole("alert")).toHaveTextContent("内容制作进度无法读取，请检查项目数据。");
  expect(screen.queryByRole("button", { name: "1 准备资料" })).not.toBeInTheDocument();
});

it("持久化变化后刷新服务端投影并把过时审核视图退回选声制作", async () => {
  vi.mocked(getContentWorkflow)
    .mockResolvedValueOnce(projection({ stage: "review_pending", currentStep: 3,
      completedSteps: [true, true, true, false], activeGenerationId: "g1", activeBatchId: "batch-1" }))
    .mockResolvedValueOnce(projection({ stage: "scripts_confirmed", currentStep: 2,
      completedSteps: [true, true, false, false], activeGenerationId: "g1", activeBatchId: "batch-1" }));
  showWorkspace();
  expect(await screen.findByRole("button", { name: "4 审核导出" })).toHaveAttribute("aria-current", "step");

  await userEvent.click(screen.getByRole("button", { name: "保存批量更改" }));

  expect(await screen.findByRole("button", { name: "3 选声制作" })).toHaveAttribute("aria-current", "step");
  expect(getContentWorkflow).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", { name: "4 审核导出" })).not.toHaveClass("is-done");
});
