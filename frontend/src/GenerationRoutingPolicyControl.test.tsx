import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GenerationRoutingPolicyControl } from "./GenerationRoutingPolicyControl";

const list = vi.fn();
const save = vi.fn();
vi.mock("./generationRoutingApi", async () => {
  const actual = await vi.importActual<typeof import("./generationRoutingApi")>("./generationRoutingApi");
  return { ...actual, listGenerationRoutingPolicies: (...args: unknown[]) => list(...args), saveGenerationRoutingPolicy: (...args: unknown[]) => save(...args) };
});

describe("GenerationRoutingPolicyControl", () => {
  beforeEach(() => {
    list.mockResolvedValue({ revision: 3, policies: [] });
    save.mockResolvedValue({ policy: { id: "text-routing-policy", name: "text", modality: "text", routes: [], revision: 4 } });
  });

  it("只显示已验证候选，CAS 保存后把策略引用交给提交方", async () => {
    const changed = vi.fn();
    render(<GenerationRoutingPolicyControl modality="text" currentRoute={{ provider: "verified-a", model: "a" }} verifiedRoutes={[
      { provider: "verified-a", model: "a" }, { provider: "verified-b", model: "b" },
    ]} onChange={changed} />);

    expect(screen.getByText(/连接成功本身不能作为生成或备用路由授权/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "读取已保存策略" }));
    await userEvent.selectOptions(screen.getByLabelText("备用路由（可选）"), "verified-b\u0000b");
    await userEvent.click(screen.getByRole("button", { name: "保存当前顺序" }));

    expect(save).toHaveBeenCalledWith(expect.objectContaining({ routes: [
      { provider: "verified-a", model: "a" }, { provider: "verified-b", model: "b" },
    ] }), 3);
    expect(changed).toHaveBeenCalledWith({ id: "text-routing-policy", revision: 4 });
  });

  it("刷新后只保留所有候选仍可用的策略，并更新 CAS 引用", async () => {
    const changed = vi.fn();
    list.mockResolvedValue({ revision: 5, policies: [{ id: "p", name: "可用", modality: "text", revision: 5,
      routes: [{ provider: "verified-a", model: "a" }, { provider: "verified-b", model: "b" }] },
      { id: "stale", name: "失效", modality: "text", revision: 5, routes: [{ provider: "verified-a", model: "a" }, { provider: "gone", model: "x" }] }] });
    render(<GenerationRoutingPolicyControl modality="text" currentRoute={{ provider: "verified-a", model: "a" }} verifiedRoutes={[
      { provider: "verified-a", model: "a" }, { provider: "verified-b", model: "b" },
    ]} value={{ id: "p", revision: 3 }} onChange={changed} />);
    await userEvent.click(screen.getByRole("button", { name: "读取已保存策略" }));
    expect(await screen.findByRole("option", { name: /可用/ })).toBeVisible();
    expect(screen.queryByRole("option", { name: /失效/ })).toBeNull();
    expect(changed).toHaveBeenCalledWith({ id: "p", revision: 5 });
  });

  it("路由或已验证候选变化会立即清空失效策略", async () => {
    const changed = vi.fn();
    const view = render(<GenerationRoutingPolicyControl modality="text" currentRoute={{ provider: "verified-a", model: "a" }} verifiedRoutes={[
      { provider: "verified-a", model: "a" }, { provider: "verified-b", model: "b" },
    ]} value={{ id: "p", revision: 1 }} onChange={changed} />);
    list.mockResolvedValue({ revision: 1, policies: [{ id: "p", name: "p", modality: "text", revision: 1,
      routes: [{ provider: "verified-a", model: "a" }, { provider: "verified-b", model: "b" }] }] });
    await userEvent.click(screen.getByRole("button", { name: "读取已保存策略" }));
    view.rerender(<GenerationRoutingPolicyControl modality="text" currentRoute={{ provider: "verified-a", model: "a" }} verifiedRoutes={[
      { provider: "verified-a", model: "a" },
    ]} value={{ id: "p", revision: 1 }} onChange={changed} />);
    expect(changed).toHaveBeenCalledWith(undefined);
  });
});
