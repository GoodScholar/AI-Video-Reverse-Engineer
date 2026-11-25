import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import type { Project } from "./models";
import { PreproductionWorkspace } from "./PreproductionWorkspace";


afterEach(() => vi.unstubAllGlobals());

it("素材库提供独立视频生成入口且生产目录为空时保持诚实禁用", async () => {
  const project: Project = {
    id: "project-video-entry", name: "视频入口", createdAt: "2026-10-01T00:00:00Z", updatedAt: "2026-10-01T00:00:00Z",
    referenceMedia: null, localPreprocessing: null,
  };
  const workspace = {
    schemaVersion: 2, revision: 1,
    brief: { theme: "", purpose: "", style: "", duration: 5, aspect: "16:9", mustPreserve: "" },
    assets: [{ id: "asset-image", assetVersionId: "asset-image-v1", name: "首帧.png", kind: "image", role: "reference", url: "/image" }],
    scenes: [{ id: "scene-default", title: "未分场", rank: "00000001", description: "" }],
    shots: [], workflow: { nodes: [], edges: [] },
    canvasLayout: { scope: { type: "project", id: project.id }, layoutRevision: 0, nodes: {} },
    checks: [], nodeCatalog: [],
  };
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/preproduction")) return new Response(JSON.stringify(workspace));
    if (url.endsWith("/video-generation/models")) return new Response(JSON.stringify({ models: [] }));
    if (url.endsWith("/video-generation/runs")) return new Response(JSON.stringify({ runs: [] }));
    throw new Error(`unexpected ${url}`);
  }));

  render(<PreproductionWorkspace project={project} tools={null} sectionOverride="assets" studioMode />);
  await userEvent.click(await screen.findByRole("button", { name: "视频生成" }));

  expect(await screen.findByRole("heading", { name: "视频生成" })).toBeVisible();
  expect(screen.getByText("当前没有已通过异步能力验证的视频模型。连接 Provider 不等于该模型组合已验证。")).toBeVisible();
  expect(screen.queryByRole("button", { name: "提交视频生成" })).not.toBeInTheDocument();
});
