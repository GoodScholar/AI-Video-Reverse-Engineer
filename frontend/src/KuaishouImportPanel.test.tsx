import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { KuaishouImportPanel } from "./KuaishouImportPanel";

vi.mock("./KuaishouSessionSettings", () => ({ KuaishouSessionSettings: () => null }));

const response = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { "Content-Type": "application/json" },
});

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    probeHosts: ["www.kuaishou.com"],
    downloadHosts: ["快手授权媒体 CDN（*.yximgs.com / *.kwaicdn.com）"],
    purpose: "读取公开快手页面的元数据；确认权利后下载到本项目受管暂存区。",
    sessionPolicy: "默认不读取或发送会话。",
    shortLinkPolicy: "v.kuaishou.com 短链受限解析。",
  })));
});

it("在展示快手外发披露后，要求用户独立确认外发才允许探测", async () => {
  render(<KuaishouImportPanel projectId="project-001" role="reference" disabled={false} />);

  expect(await screen.findByText("读取公开快手页面的元数据；确认权利后下载到本项目受管暂存区。")).toBeVisible();
  await userEvent.type(screen.getByLabelText("快手链接"), "https://www.kuaishou.com/short-video/3x9abcDEF");
  await userEvent.click(screen.getByLabelText("我确认拥有该视频的使用权"));

  const disclosure = screen.getByLabelText("我已阅读并同意外发范围");
  expect(disclosure).toBeEnabled();
  expect(disclosure).not.toBeChecked();
  expect(screen.getByRole("button", { name: "读取链接" })).toBeDisabled();

  await userEvent.click(disclosure);
  expect(screen.getByRole("button", { name: "读取链接" })).toBeEnabled();
});

it("在工作区存在未保存编辑时，不启动下载，也不以完成状态自动刷新素材", async () => {
  const onInstalled = vi.fn();
  sessionStorage.setItem("aivre:kuaishou-import:project-001", "run-001");
  vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string) => Promise.resolve(response(
    input.includes("/disclosure")
      ? { probeHosts: ["www.kuaishou.com"], downloadHosts: ["*.yximgs.com"], purpose: "披露", sessionPolicy: "默认不读取会话", shortLinkPolicy: "三跳" }
      : { run: { id: "run-001", status: "completed", error: null, createdAt: "2026-10-02T00:00:00Z", inputFingerprint: "fingerprint", idempotencyKey: "key", retryOf: null, uploadedAssetId: "asset-001" } },
  ))));

  render(<KuaishouImportPanel projectId="project-001" role="reference" disabled onInstalled={onInstalled} />);

  await screen.findByText("导入运行：completed");
  await userEvent.click(screen.getByRole("button", { name: "刷新导入状态" }));
  expect(onInstalled).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "读取链接" })).toBeDisabled();
});
