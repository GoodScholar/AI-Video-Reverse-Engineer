import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DouyinSessionSettings } from "./DouyinSessionSettings";

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => vi.unstubAllGlobals());

it("抖音会话设置只读取配置状态，并可保存和删除不回显的安全存储会话", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response({ configured: false })).mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  render(<DouyinSessionSettings />);
  expect(await screen.findByText("当前状态：未配置")).toBeVisible();
  await userEvent.type(screen.getByLabelText("抖音会话 Cookie"), "session-secret");
  await userEvent.click(screen.getByRole("button", { name: "保存到系统安全存储" }));
  expect(fetchMock.mock.calls[1][0]).toBe("/api/source-sessions/douyin");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ cookie: "session-secret" });
  expect(screen.getByLabelText("抖音会话 Cookie")).toHaveValue("");
  await userEvent.click(screen.getByRole("button", { name: "删除已保存会话" }));
  expect(fetchMock.mock.calls[2][1].method).toBe("DELETE");
});
