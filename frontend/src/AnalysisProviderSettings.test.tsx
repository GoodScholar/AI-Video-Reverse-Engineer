import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it, vi } from "vitest";

import { AnalysisProviderSettings } from "./AnalysisProviderSettings";
import type { AnalysisProviderConfiguration } from "./models";

const providers: AnalysisProviderConfiguration[] = [
  { provider: "bailian" as const, label: "阿里云百炼", models: [{ id: "qwen3.7-flash", label: "Qwen 3.7 Flash" }], model: "qwen3.7-flash", baseUrl: null, credentialState: "configured" as const, selectedProvider: "bailian" as const },
  { provider: "local_openai_compatible" as const, label: "本地 OpenAI 兼容服务", models: [], model: "vision-local", baseUrl: "http://127.0.0.1:8080", credentialState: "unconfigured" as const, selectedProvider: "bailian" as const },
];

const providersWithUnimplementedCloud = [
  ...providers,
  { provider: "openai" as const, label: "OpenAI", models: [{ id: "gpt-5.6-luna", label: "GPT-5.6 Luna" }], model: "gpt-5.6-luna", baseUrl: null, credentialState: "unconfigured" as const, selectedProvider: "openai" as const },
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

it("连接测试失败后刷新配置并保留原始失败信息", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const load = vi.fn().mockResolvedValue([{ ...providers[0], verificationState: "failed" as const, failedAt: "2026-09-13T00:00:00+00:00", errorCode: "timeout" }]);
  function SettingsHarness() {
    const [currentProviders, setCurrentProviders] = useState(providers);
    return <AnalysisProviderSettings providers={currentProviders} onProvidersChanged={setCurrentProviders} save={vi.fn()} testConnection={vi.fn().mockRejectedValue(new Error("连接超时"))} load={load} />;
  }
  render(<SettingsHarness />);

  await user.click(screen.getByRole("button", { name: "测试连接" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("连接超时");
  expect(load).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: /验证失败/ })).toBeVisible();
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

it("API 未提供供应商标签时回退显示供应商 id", () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  render(<AnalysisProviderSettings providers={[{ ...providers[0], label: undefined }]} save={vi.fn()} testConnection={vi.fn()} />);

  expect(screen.getAllByRole("button", { name: /bailian/ })[0]).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("heading", { name: "bailian" })).toBeVisible();
});

it("云端模型下拉提交用户选择的目录模型", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const cloud = {
    provider: "openai" as const, label: "OpenAI", credentialState: "configured" as const,
    selectedProvider: "openai" as const, model: "gpt-5.6-luna", baseUrl: null,
    models: [{ id: "gpt-5.6-luna", label: "GPT-5.6 Luna" }, { id: "gpt-5.6-terra", label: "GPT-5.6 Terra" }],
  };
  const save = vi.fn().mockResolvedValue(cloud);
  render(<AnalysisProviderSettings providers={[cloud]} save={save} testConnection={vi.fn()} />);

  await user.selectOptions(screen.getByLabelText("内置模型"), "gpt-5.6-terra");
  await user.click(screen.getByRole("button", { name: "保存OpenAI配置" }));

  expect(save).toHaveBeenCalledWith("openai", { model: "gpt-5.6-terra" });
});

it("已配置的云端 Key 可空输入保存且不提交 apiKey", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const cloud = {
    provider: "openai" as const, label: "OpenAI", credentialState: "configured" as const,
    selectedProvider: "openai" as const, model: "gpt-5.6-luna", baseUrl: null,
    models: [{ id: "gpt-5.6-luna", label: "GPT-5.6 Luna" }],
  };
  const save = vi.fn().mockResolvedValue(cloud);
  render(<AnalysisProviderSettings providers={[cloud]} save={save} testConnection={vi.fn()} />);

  expect(screen.getByLabelText("OpenAI API Key")).not.toBeRequired();
  await user.click(screen.getByRole("button", { name: "保存OpenAI配置" }));

  expect(save).toHaveBeenCalledWith("openai", { model: "gpt-5.6-luna" });
});

it("从 API 目录渲染 ChatAnywhere 的固定模型并按云端供应商保存", async () => {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const user = userEvent.setup();
  const provider = {
    provider: "chatanywhere" as const, label: "ChatAnywhere", credentialState: "unconfigured" as const,
    selectedProvider: "chatanywhere" as const, model: "gpt-5.6-sol", baseUrl: null,
    models: [{ id: "gpt-5.6-sol", label: "GPT-5.6 Sol" }],
  };
  const save = vi.fn().mockResolvedValue({ ...provider, credentialState: "configured" as const });
  render(<AnalysisProviderSettings providers={[provider]} save={save} testConnection={vi.fn()} />);

  expect(screen.getByRole("option", { name: "GPT-5.6 Sol" })).toBeVisible();
  await user.type(screen.getByLabelText("ChatAnywhere API Key"), "one-shot-secret");
  await user.click(screen.getByRole("button", { name: "保存ChatAnywhere配置" }));

  expect(save).toHaveBeenCalledWith("chatanywhere", { model: "gpt-5.6-sol", apiKey: "one-shot-secret" });
  expect(screen.queryByText("one-shot-secret")).not.toBeInTheDocument();
});
