import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("./CreativeCanvasPrompt", () => ({
  CreativeCanvasPrompt: ({ value, label, onChange }: { value: string; label: string; onChange: (value: string) => void }) => <textarea aria-label={label} value={value} onChange={event => onChange(event.target.value)} />,
}));

import { CreativeCanvasTextGeneration, type CanvasTextGeneration } from "./CreativeCanvasTextGeneration";

function data(overrides: Partial<CanvasTextGeneration> = {}): CanvasTextGeneration {
  return {
    prompt: "写一段商品介绍", selectedModel: { providerId: "openai", modelId: "gpt-text", configurationVersion: "v1" },
    models: [
      { providerId: "openai", providerLabel: "OpenAI", modelId: "gpt-text", label: "GPT Text", configured: true, configurationVersion: "v1" },
      { providerId: "local", providerLabel: "本地", modelId: "offline", label: "Offline", configured: false },
    ],
    references: [{ id: "edge-text", name: "参考 1", value: "{{ref 1}}" }],
    versions: [
      { versionId: "version-current", runId: "run-1", status: "installed", text: "第一版正文", createdAt: "2026-09-30T00:00:00Z" },
      { versionId: "version-failed", runId: "run-2", status: "failed", text: "部分正文", createdAt: "2026-09-30T00:01:00Z", error: "供应商超时" },
      { versionId: "version-late", runId: "run-3", status: "installed", text: "晚到正文", createdAt: "2026-09-30T00:02:00Z", cancellationRequested: true },
    ],
    currentVersionId: "version-current", runStatus: "running", count: 1, onCountChange: vi.fn(), onPromptChange: vi.fn(), onModelChange: vi.fn(), onRun: vi.fn(), onCancel: vi.fn(), onSelectVersion: vi.fn(), onInstallVersion: vi.fn(),
    ...overrides,
  };
}

describe("CreativeCanvasTextGeneration", () => {
  it("配置版本变化后要求重新选择模型，并写入新版本", async () => {
    const value = data({ runStatus: undefined, selectedModel: { providerId: "openai", modelId: "gpt-text", configurationVersion: "old" } });
    const view = render(<CreativeCanvasTextGeneration label="文案" value={value} />);
    expect(screen.getByRole("button", { name: "生成文本" })).toBeDisabled();
    expect(screen.getByText("模型配置已变化，请重新选择模型。")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "文本模型" })).not.toHaveValue(JSON.stringify(["openai", "gpt-text"]));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "文本模型" }), JSON.stringify(["openai", "gpt-text"]));
    expect(value.onModelChange).toHaveBeenCalledWith({ providerId: "openai", modelId: "gpt-text", configurationVersion: "v1" });
    view.rerender(<CreativeCanvasTextGeneration label="文案" value={data({ runStatus: undefined })} />);
    expect(screen.getByRole("button", { name: "生成文本" })).toBeEnabled();
  });

  it("按供应商分组模型，保留稳定选择并将提示词编辑交给宿主", async () => {
    const value = data({ runStatus: undefined });
    render(<div className="creative-canvas"><CreativeCanvasTextGeneration label="文案" value={value} /></div>);
    expect(screen.getByRole("group", { name: "OpenAI" })).toBeVisible();
    expect(screen.getByRole("group", { name: "本地" })).toBeVisible();
    await userEvent.type(screen.getByRole("textbox", { name: "文案生成提示词" }), "！");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "文本模型" }), JSON.stringify(["openai", "gpt-text"]));
    expect(value.onPromptChange).toHaveBeenLastCalledWith("写一段商品介绍！");
    expect(value.onModelChange).toHaveBeenCalledWith({ providerId: "openai", modelId: "gpt-text", configurationVersion: "v1" });
  });

  it("失败片段可下载但不作为成功版本采用，取消后晚到版本仅显式采用", async () => {
    const value = data();
    render(<div className="creative-canvas"><CreativeCanvasTextGeneration label="文案" value={value} /></div>);
    expect(screen.getByText("正在生成文本…")).toBeVisible();
    expect(screen.getByText("取消后晚到，未自动采用")).toBeVisible();
    expect(screen.queryByRole("button", { name: "采用文本版本 version-failed" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载文本版本 version-failed" })).toHaveAttribute("download");
    expect(value.onSelectVersion).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "采用文本版本 version-late" }));
    await userEvent.click(screen.getByRole("button", { name: "取消文本生成" }));
    expect(value.onSelectVersion).toHaveBeenCalledWith("run-3", "version-late");
    expect(value.onCancel).toHaveBeenCalledTimes(1);
  });

  it("没有已配置模型时明确禁用提交，不伪造可执行文本服务", () => {
    const value = data({ selectedModel: undefined, models: [{ providerId: "local", providerLabel: "本地", modelId: "offline", label: "Offline", configured: false }], runStatus: undefined });
    render(<div className="creative-canvas"><CreativeCanvasTextGeneration label="文案" value={value} /></div>);
    expect(screen.getByText("请先配置可用的文本模型。")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成文本" })).toBeDisabled();
  });

  it("只为可入库的真实版本展示入库动作，已入库版本不重复操作", async () => {
    const value = data({ runStatus: undefined, versions: [
      { versionId: "version-install", runId: "run-1", status: "installed", text: "可入库正文", createdAt: "2026-09-30T00:00:00Z" },
      { versionId: "version-asset", runId: "run-2", status: "installed", text: "已有素材", createdAt: "2026-09-30T00:01:00Z", assetId: "canvas-text-1" },
      { versionId: "version-old", runId: "run-3", status: "completed", text: "旧记录", createdAt: "2026-09-30T00:02:00Z" },
    ] });
    render(<div className="creative-canvas"><CreativeCanvasTextGeneration label="文案" value={value} /></div>);

    await userEvent.click(screen.getByRole("button", { name: "将文本版本 version-install 加入素材库" }));
    expect(value.onInstallVersion).toHaveBeenCalledWith("run-1", "version-install");
    expect(screen.getByText("已入库")).toBeVisible();
    expect(screen.queryByRole("button", { name: "将文本版本 version-asset 加入素材库" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "将文本版本 version-old 加入素材库" })).not.toBeInTheDocument();
  });

  it("状态未知时禁止重新提交并解释原因", () => {
    render(<CreativeCanvasTextGeneration label="文案" value={data({ runStatus: "unknown" })} />);
    expect(screen.getByRole("button", { name: "生成文本" })).toBeDisabled();
    expect(screen.getByText(/状态未知.*重复/)).toBeVisible();
  });

  it("明确失败但计费未知时保留提示，允许用户显式重新生成", () => {
    const value = data({ runStatus: "failed", versions: [{ versionId: "too-long", runId: "run-long", status: "failed", text: "超限前的片段", createdAt: "2026-09-30T00:00:00Z", billingUnknown: true }] });
    render(<CreativeCanvasTextGeneration label="文案" value={value} />);
    expect(screen.getByText("本次请求可能已产生费用；再次生成会提交新请求。")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成文本" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "采用文本版本 too-long" })).not.toBeInTheDocument();
  });
});
