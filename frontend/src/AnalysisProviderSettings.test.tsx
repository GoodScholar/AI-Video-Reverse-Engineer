import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it, vi } from "vitest";

import { AnalysisProviderSettings } from "./AnalysisProviderSettings";
import type { AnalysisProviderConfiguration } from "./models";

const providers: AnalysisProviderConfiguration[] = [
  { provider: "bailian" as const, model: "qwen3.7-flash", baseUrl: null, credentialState: "configured" as const, selectedProvider: "bailian" as const },
  { provider: "local_openai_compatible" as const, model: "vision-local", baseUrl: "http://127.0.0.1:8080", credentialState: "unconfigured" as const, selectedProvider: "bailian" as const },
];

const providersWithUnimplementedCloud = [
  ...providers,
  { provider: "openai" as const, model: null, baseUrl: null, credentialState: "unconfigured" as const, selectedProvider: "openai" as const },
];

it("保存密钥后立即清空密码输入且只显示配置状态", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue({ ...providers[0], selectedProvider: "bailian" as const });
  render(<AnalysisProviderSettings providers={providers} save={save} testConnection={vi.fn()} />);

  const password = screen.getByLabelText("百炼 API Key");
  expect(password).toHaveAttribute("type", "password");
  expect(password).toHaveAttribute("autocomplete", "new-password");
  await user.type(password, "one-shot-secret");
  await user.click(screen.getByRole("button", { name: "保存百炼配置" }));

  expect(save).toHaveBeenCalledWith("bailian", expect.objectContaining({ apiKey: "one-shot-secret" }));
  expect(password).toHaveValue("");
  expect(screen.getAllByText("密钥已配置").length).toBeGreaterThan(0);
  expect(screen.queryByText("one-shot-secret")).not.toBeInTheDocument();
});

it("在连接测试前后都说明固定探针不会上传项目素材", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  render(<AnalysisProviderSettings providers={providers} save={vi.fn()} testConnection={vi.fn().mockRejectedValue(new Error("服务未响应"))} />);

  expect(screen.getByText("连接测试只发送固定探针，不上传项目素材。")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "测试连接" }));
  expect(screen.getByText("连接测试只发送固定探针，不上传项目素材。")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("服务未响应");
});

it("保存无密钥的本地兼容服务后标为已配置且已选择", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const saved = { ...providers[1], selectedProvider: "local_openai_compatible" as const };
  const save = vi.fn().mockResolvedValue(saved);
  function SettingsHarness() {
    const [currentProviders, setCurrentProviders] = useState(providers);
    return <AnalysisProviderSettings providers={currentProviders} onProvidersChanged={setCurrentProviders} save={save} testConnection={vi.fn()} />;
  }
  render(<SettingsHarness />);

  await user.click(screen.getByRole("button", { name: /本地 OpenAI 兼容服务/ }));
  await user.click(screen.getByRole("button", { name: "保存本地服务配置" }));

  const localProviderCard = screen.getByRole("button", { name: /本地 OpenAI 兼容服务/ });
  expect(save).toHaveBeenCalledWith("local_openai_compatible", { model: "vision-local", baseUrl: "http://127.0.0.1:8080" });
  expect(localProviderCard).toHaveAttribute("aria-pressed", "true");
  expect(within(localProviderCard).getByText("已配置")).toBeVisible();
});

it("只允许配置已实现的百炼和本地服务，不把未实现云端显示为本地表单", () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  render(<AnalysisProviderSettings providers={providersWithUnimplementedCloud} save={vi.fn()} testConnection={vi.fn()} />);

  expect(screen.getByRole("button", { name: /阿里云百炼/ })).toBeVisible();
  expect(screen.getAllByRole("button", { name: /本地 OpenAI 兼容服务/ })).toHaveLength(1);
  expect(screen.getByRole("heading", { name: "阿里云百炼" })).toBeVisible();
  expect(screen.queryByText("本地服务 API Key（可选）")).not.toBeInTheDocument();
});

it("展示目录中的云端供应商，并将尚未验证与已配置分开说明", () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  render(<AnalysisProviderSettings providers={providersWithUnimplementedCloud} save={vi.fn()} testConnection={vi.fn()} />);

  const openai = screen.getByRole("button", { name: /^OpenAI/ });
  expect(openai).toBeVisible();
  expect(within(openai).getByText(/未验证/)).toBeVisible();
  expect(screen.queryByText("可用")).not.toBeInTheDocument();
});
