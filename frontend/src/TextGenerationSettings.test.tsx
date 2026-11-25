import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TextGenerationSettings } from "./TextGenerationSettings";

const configuration = { providerId: "default", baseUrl: "https://text.example/v1", models: [{ modelId: "text-model" }], revision: "r1", credentialState: "configured", modelCatalog: [{ providerId: "default", providerLabel: "文本服务", modelId: "text-model", label: "text-model", configured: false, configurationVersion: "r1", textVerified: false, imageVerified: false, maxImageReferences: 0 }] };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => vi.unstubAllGlobals());

it("StrictMode 的旧读取晚到时不覆盖当前表单草稿", async () => {
  let completeOld!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { completeOld = resolve; })).mockResolvedValueOnce(response(configuration)));
  render(<StrictMode><TextGenerationSettings /></StrictMode>);
  await screen.findByLabelText("文本模型（每行一个）");
  await userEvent.type(screen.getByLabelText("文本模型（每行一个）"), "\ndraft-model");
  await act(async () => completeOld(response({ ...configuration, revision: "older" })));
  expect(screen.getByLabelText("文本模型（每行一个）")).toHaveValue("text-model\ndraft-model");
});

it("保存文本配置保留旧密钥，不自动发起计费验证", async () => {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(response(configuration)));
  vi.stubGlobal("fetch", fetchMock);
  render(<TextGenerationSettings />);
  expect(await screen.findByLabelText("文本服务 Base URL")).toHaveValue(configuration.baseUrl);
  expect(screen.getByLabelText("文本服务 API Key")).toHaveValue("");
  await userEvent.click(screen.getByRole("button", { name: "保存文本生成配置" }));
  await screen.findByText(/配置已保存/);
  const [, request] = fetchMock.mock.calls[1];
  expect(JSON.parse(request.body)).toEqual({ baseUrl: configuration.baseUrl, models: [{ modelId: "text-model" }], revision: "r1" });
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual(["/api/text-generation/configuration", "/api/text-generation/configuration"]);
});

it("文本验证由明确点击触发并显示真实验证状态，改配置后阻止验证旧内容", async () => {
  const verified = { ...configuration, modelCatalog: [{ ...configuration.modelCatalog[0], configured: true, textVerified: true }] };
  const fetchMock = vi.fn().mockResolvedValueOnce(response(configuration)).mockResolvedValueOnce(response(verified));
  vi.stubGlobal("fetch", fetchMock);
  render(<TextGenerationSettings />);
  await screen.findByLabelText("文本服务 Base URL");
  await userEvent.click(screen.getByRole("button", { name: "验证 text-model 文本（可能计费）" }));
  await waitFor(() => expect(screen.getByText("文本已验证；图片未验证")).toBeVisible());
  expect(fetchMock.mock.calls[1][0]).toBe("/api/text-generation/test-connection");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ revision: "r1", modelId: "text-model", mode: "text" });
  await userEvent.type(screen.getByLabelText("文本服务 Base URL"), "/changed");
  expect(screen.getByRole("button", { name: "验证 text-model 文本（可能计费）" })).toBeDisabled();
});

it("保存冲突保留输入并提供明确的重新读取入口", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(configuration)).mockResolvedValueOnce(response({ detail: "配置已更新" }, 409)));
  render(<TextGenerationSettings />);
  await screen.findByLabelText("文本服务 Base URL");
  await userEvent.type(screen.getByLabelText("文本模型（每行一个）"), "\nsecond-model");
  await userEvent.click(screen.getByRole("button", { name: "保存文本生成配置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("配置已更新");
  expect(screen.getByLabelText("文本模型（每行一个）")).toHaveValue("text-model\nsecond-model");
  expect(screen.getByRole("button", { name: /重新读取/ })).toBeVisible();
});
