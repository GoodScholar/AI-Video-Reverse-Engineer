import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { AnalysisProviderSettings } from "./AnalysisProviderSettings";

const providers = [
  { provider: "bailian" as const, model: "qwen3.7-flash", baseUrl: null, credentialState: "configured" as const, selectedProvider: "bailian" as const },
  { provider: "local_openai_compatible" as const, model: "vision-local", baseUrl: "http://127.0.0.1:8080", credentialState: "unconfigured" as const, selectedProvider: "bailian" as const },
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
