import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { SemanticAnalysisPanel } from "./SemanticAnalysisPanel";
import type { Project } from "./models";

function project(): Project {
  return {
    id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-12T10:00:00+00:00", updatedAt: "2026-09-12T10:01:00+00:00",
    referenceMedia: { type: "image", id: "image-001", originalName: "rain.png", format: "png", sizeBytes: 11, width: 1200, height: 1600, hasTransparency: false },
    localPreprocessing: {
      id: "pre-001", sourceReferenceMediaId: "image-001", mediaType: "image", algorithmVersion: 1, status: "completed", currentStage: null, stages: [],
      queuedAt: "2026-09-12T10:00:00+00:00", startedAt: null, updatedAt: "2026-09-12T10:01:00+00:00", completedAt: "2026-09-12T10:01:00+00:00",
      proxySummary: { mediaType: "image", originalDisplaySize: { width: 1200, height: 1600 }, normalizedSize: { width: 1200, height: 1600 }, proxySize: { width: 900, height: 1200 }, transparencyFlattened: false, applicabilityStatus: "pending_semantic_confirmation" },
      reproducibilityAssessment: { status: "pending_semantic_confirmation", checks: [] }, error: null,
    },
    semanticAnalysis: {
      id: "analysis-001", sourceReferenceMediaId: "image-001", sourcePreprocessingId: "pre-001", provider: "bailian", model: "qwen3.7-flash", promptVersion: 1, schemaVersion: 1, status: "completed",
      createdAt: "2026-09-12T10:00:00+00:00", startedAt: "2026-09-12T10:00:01+00:00", updatedAt: "2026-09-12T10:01:00+00:00", completedAt: "2026-09-12T10:01:00+00:00", error: null,
      result: {
        version: 1,
        observedFacts: { staticVisual: { subject: "人物", scene: "雨夜街道", composition: "中景", viewpoint: "平视", lighting: "路灯侧光", color: "冷暖对比", visualStyle: "写实" }, temporal: null },
        generationSuggestions: { subjectMotion: "轻微转头", environmentalMotion: "雨滴", cameraMotion: "固定", rhythm: "平稳", suggestedDuration: 3, audio: "保留环境声" },
      },
    },
  };
}

it("将完成结果分为可观察事实和生成建议，并在首次开始时只展开披露", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const startAnalysis = vi.fn();
  render(<SemanticAnalysisPanel project={project()} provider={{ provider: "bailian", label: "百炼目录标签", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} start={startAnalysis} />);

  expect(screen.getByRole("heading", { name: "可观察事实" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "生成建议" })).toBeInTheDocument();
  expect(screen.getByText("百炼目录标签 · qwen3.7-flash")).toBeVisible();
  expect(screen.getByText("建议时长")).toBeVisible();
  expect(screen.getByText("音频建议")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "开始语义分析" }));

  expect(screen.getByText(/不会发送原始素材/)).toBeInTheDocument();
  expect(startAnalysis).not.toHaveBeenCalled();
});

it("API 未提供供应商标签时仅回退到供应商 id", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  render(<SemanticAnalysisPanel project={{ ...project(), semanticAnalysis: null }} provider={{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} />);

  await user.click(screen.getByRole("button", { name: "开始语义分析" }));

  expect(screen.getByText("将使用 bailian 的 qwen3.7-flash 进行本次语义分析。")).toBeVisible();
});

it("取消披露后把焦点交还给开始按钮", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  render(<SemanticAnalysisPanel project={{ ...project(), semanticAnalysis: null }} provider={{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} />);

  await user.click(screen.getByRole("button", { name: "开始语义分析" }));
  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.getByRole("button", { name: "开始语义分析" })).toHaveFocus();
});

it("披露冻结本次素材与预处理身份，并在身份失效时不提交", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const start = vi.fn();
  const view = render(<SemanticAnalysisPanel project={{ ...project(), semanticAnalysis: null }} provider={{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} start={start} />);

  await user.click(screen.getByRole("button", { name: "开始语义分析" }));
  expect(screen.getByRole("heading", { name: "确认发送分析代理" })).toHaveFocus();
  view.rerender(<SemanticAnalysisPanel project={{ ...project(), referenceMedia: { ...project().referenceMedia!, id: "image-002", originalName: "replacement.png" }, localPreprocessing: null, semanticAnalysis: null }} provider={{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} start={start} />);

  expect(screen.getByText("参考素材或本地预处理已变化，请重新确认发送内容。")).toBeVisible();
  expect(screen.queryByRole("button", { name: "确认并开始语义分析" })).not.toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
});

it("素材替换并完成新预处理后可重新确认并提交新身份", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const start = vi.fn();
  const initial = { ...project(), semanticAnalysis: null };
  const completedImagePreprocessing = initial.localPreprocessing;
  if (completedImagePreprocessing?.mediaType !== "image") throw new Error("测试素材应为图片预处理");
  const replacementWithoutPreprocessing = {
    ...initial,
    referenceMedia: { ...initial.referenceMedia!, id: "image-002", originalName: "replacement.png" },
    localPreprocessing: null,
  };
  const replacementReady: Project = {
    ...replacementWithoutPreprocessing,
    localPreprocessing: {
      ...completedImagePreprocessing,
      id: "pre-002",
      sourceReferenceMediaId: "image-002",
      proxySummary: { ...completedImagePreprocessing.proxySummary!, proxySize: { width: 800, height: 1200 } },
    },
  };
  start.mockResolvedValue(replacementReady);
  const view = render(<SemanticAnalysisPanel project={initial} provider={{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} start={start} />);

  await user.click(screen.getByRole("button", { name: "开始语义分析" }));
  view.rerender(<SemanticAnalysisPanel project={replacementWithoutPreprocessing} provider={{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} start={start} />);
  expect(screen.getByRole("alert")).toHaveTextContent("参考素材或本地预处理已变化");

  view.rerender(<SemanticAnalysisPanel project={replacementReady} provider={{ provider: "bailian", model: "qwen3.7-flash", baseUrl: null, credentialState: "configured", selectedProvider: "bailian" }} onProjectUpdated={vi.fn()} start={start} />);
  await user.click(screen.getByRole("button", { name: "开始语义分析" }));
  expect(screen.getByText("analysis-proxy.jpg（800×1200，比例 800:1200）")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "确认并开始语义分析" }));

  expect(start).toHaveBeenCalledWith("project-001", "bailian", "qwen3.7-flash");
});
