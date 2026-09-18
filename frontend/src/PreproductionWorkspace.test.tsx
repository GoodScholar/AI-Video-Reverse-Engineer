import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Project } from "./models";
import { PreproductionWorkspace } from "./PreproductionWorkspace";
import type { PreproductionWorkspace as Workspace } from "./preproductionApi";

const project: Project = {
  id: "project-001", name: "雨夜人像", createdAt: "2026-09-10T10:00:00Z", updatedAt: "2026-09-10T10:00:00Z",
  referenceMedia: null, localPreprocessing: null,
};

function workspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    revision: 3,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 12, aspect: "16:9", mustPreserve: "服装" },
    assets: [{ id: "asset-001", name: "主角.png", kind: "image", role: "character", url: "/asset.png" }],
    shots: [{
      id: "shot-001", title: "镜头一", duration: 3, prompt: "雨夜街头", negativePrompt: "模糊", assetIds: [],
      nodes: [{ id: "node-001", kind: "reference", input: "asset:asset-001", params: {}, status: "pending", artifacts: [] }],
    }],
    checks: [], nodeCatalog: [{ kind: "reference", label: "引用素材" }, { kind: "crop", label: "裁切" }, { kind: "prompt", label: "提示词" }],
    ...overrides,
  };
}

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

describe("PreproductionWorkspace", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("初始空态提供导入和创建镜头入口", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ assets: [], shots: [] })));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    expect(await screen.findByText("从已有参考或分镜开始")).toBeVisible();
    expect(screen.getByRole("button", { name: "导入参考素材" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: "新建镜头" })).toHaveLength(2);
  });

  it("绑定素材、保存后才允许运行当前节点", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(workspace()))
      .mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], assetIds: ["asset-001"] }] })))
      .mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], assetIds: ["asset-001"], nodes: [{ ...workspace().shots[0].nodes[0], status: "queued" }] }] }), 202));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await screen.findByText("镜头一");
    await user.click(screen.getByRole("button", { name: /绑定\s*主角\.png/ }));
    expect(screen.getByRole("button", { name: "运行当前节点" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await user.click(screen.getByRole("button", { name: "运行当前节点" }));

    expect(fetch).toHaveBeenNthCalledWith(2, "/api/projects/project-001/preproduction", expect.objectContaining({ method: "PUT" }));
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/projects/project-001/preproduction/shots/shot-001/nodes/node-001/run", expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: 3 }) }));
  });

  it("节点失败显示具体错误，并可在工具导航中显示传入工具", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], nodes: [{ ...workspace().shots[0].nodes[0], status: "failed", error: "输入文件已损坏" }] }] })));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具区</p>} />);

    expect(await screen.findByText(/输入文件已损坏/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "工具" }));
    expect(screen.getByText("已有工具区")).toBeVisible();
  });

  it("编辑节点参数后保留未保存提示，运行与导出保持禁用", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], nodes: [{ ...workspace().shots[0].nodes[0], kind: "crop", params: { x: 0.1, y: 0.1, width: 0.8, height: 0.8 } }] }] })));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    const x = await screen.findByLabelText("裁切 X");
    fireEvent.change(x, { target: { value: "0.2" } });

    expect(screen.getByDisplayValue("0.2")).toBeVisible();
    expect(screen.getByText("有未保存更改")).toBeVisible();
    expect(screen.getByRole("button", { name: "运行当前节点" })).toBeDisabled();
  });

  it("删除节点时清空后续节点对其产物的引用", async () => {
    const upstream = { ...workspace().shots[0].nodes[0], status: "completed" as const, artifacts: [{ name: "out.png", url: "/out.png" }] };
    const downstream = { id: "node-002", kind: "crop" as const, input: "node:node-001", params: { x: 0, y: 0, width: 1, height: 1 }, status: "pending" as const, artifacts: [] };
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], nodes: [upstream, downstream] }] })))
      .mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], nodes: [downstream] }] })));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await screen.findByRole("button", { name: "删除当前节点" });
    await userEvent.click(screen.getByRole("button", { name: "删除当前节点" }));

    expect(screen.getByLabelText("输入")).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));
    expect(fetch).toHaveBeenLastCalledWith("/api/projects/project-001/preproduction", expect.objectContaining({
      body: expect.stringContaining('"input":""'),
    }));
  });

  it("项目切换后忽略旧项目的延迟工作台回包", async () => {
    const old = deferred<Response>();
    const secondProject = { ...project, id: "project-002" };
    vi.mocked(fetch).mockImplementationOnce(() => old.promise).mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], title: "新项目镜头" }] })));
    const view = render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);
    view.rerender(<PreproductionWorkspace project={secondProject} tools={<p>已有工具</p>} />);

    expect(await screen.findByText("新项目镜头")).toBeVisible();
    old.resolve(response(workspace({ shots: [{ ...workspace().shots[0], title: "旧项目镜头" }] })));
    await Promise.resolve();
    expect(screen.queryByText("旧项目镜头")).not.toBeInTheDocument();
  });

  it("从素材页导入已有分镜，并在后端拒绝时保留明确错误", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(workspace()))
      .mockResolvedValueOnce(response(workspace({ revision: 4, shots: [{ ...workspace().shots[0], title: "已有分镜" }] })))
      .mockResolvedValueOnce(response({ detail: "前置工作台已被更新，请刷新后重试。" }, 409));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await screen.findByText("镜头一");
    await user.click(screen.getByRole("button", { name: "素材" }));
    await user.click(screen.getByRole("button", { name: "导入已有分镜" }));
    await user.click(screen.getByRole("button", { name: "导入已有分镜" }));

    expect(fetch).toHaveBeenNthCalledWith(2, "/api/projects/project-001/preproduction/import-shots", expect.objectContaining({ method: "POST" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("前置工作台已被更新，请刷新后重试。");
  });

  it("有未保存镜头草稿时禁用上传，并保留提示词", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    fireEvent.change(await screen.findByLabelText("画面提示词"), { target: { value: "尚未保存的新提示词" } });
    fireEvent.click(screen.getByRole("button", { name: "素材" }));

    expect(screen.getByLabelText("上传素材文件")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("上传素材文件"), { target: { files: [new File(["png"], "new.png", { type: "image/png" })] } });
    expect(fetch).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "镜头" }));
    expect(screen.getByLabelText("画面提示词")).toHaveValue("尚未保存的新提示词");
  });

  it("保存及晚到轮询回包不会覆盖新草稿或较高 revision", async () => {
    const save = deferred<Response>();
    const poll = deferred<Response>();
    const active = workspace({ shots: [{ ...workspace().shots[0], nodes: [{ ...workspace().shots[0].nodes[0], status: "running" }] }] });
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(active))
      .mockImplementationOnce(() => poll.promise)
      .mockImplementationOnce(() => save.promise);
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await screen.findByLabelText("画面提示词");
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2), { timeout: 3_000 });
    fireEvent.change(screen.getByLabelText("画面提示词"), { target: { value: "提交版本" } });
    fireEvent.click(screen.getByRole("button", { name: "保存更改" }));
    fireEvent.change(screen.getByLabelText("画面提示词"), { target: { value: "提交后继续编辑" } });
    save.resolve(response(workspace({ revision: 4, shots: [{ ...workspace().shots[0], prompt: "提交版本" }] })));
    await waitFor(() => expect(screen.queryByText("正在保存…")).not.toBeInTheDocument());
    expect(screen.getByLabelText("画面提示词")).toHaveValue("提交后继续编辑");
    await act(async () => { poll.resolve(response(active)); await poll.promise; });
    expect(screen.getByLabelText("画面提示词")).toHaveValue("提交后继续编辑");
  });

  it("节点运行时锁定编辑但保留导航，并允许预先配置更早节点输入", async () => {
    const upstream = { ...workspace().shots[0].nodes[0], status: "pending" as const };
    const active = { id: "node-002", kind: "crop" as const, input: "node:node-001", params: { x: 0, y: 0, width: 1, height: 1 }, status: "running" as const, artifacts: [] };
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], nodes: [upstream, active] }] })));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await screen.findByLabelText("镜头名称");
    expect(screen.getByLabelText("镜头名称")).toBeDisabled();
    expect(screen.getByRole("button", { name: "需求" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /2\s*画面裁切/ }));
    expect(screen.getByRole("option", { name: "前一步：引用素材（待运行）" })).toBeInTheDocument();
  });

  it("CAS 冲突后仅在用户确认时重新读取已保存版本", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(workspace()))
      .mockResolvedValueOnce(response({ detail: "前置工作台已被更新，请刷新后重试。" }, 409))
      .mockResolvedValueOnce(response(workspace({ revision: 4, shots: [{ ...workspace().shots[0], prompt: "服务器版本" }] })));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await user.clear(await screen.findByLabelText("画面提示词"));
    await user.type(screen.getByLabelText("画面提示词"), "本地草稿");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    expect(await screen.findByRole("button", { name: "放弃修改并重新读取" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "放弃修改并重新读取" }));

    expect(await screen.findByLabelText("画面提示词")).toHaveValue("服务器版本");
  });

  it("素材行提供可展开预览和文件下载", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await user.click(await screen.findByRole("button", { name: "素材" }));
    await user.click(screen.getByText("预览"));
    expect(screen.getByRole("img", { name: "主角.png 预览" })).toHaveAttribute("src", "/asset.png");
    expect(screen.getByRole("link", { name: "下载主角.png" })).toHaveAttribute("href", "/asset.png");
  });
  it("交付检查定位第二镜的具体节点并保留草稿", async () => {
    const user = userEvent.setup();
    const second = { ...workspace().shots[0], id: "shot-002", title: "镜头二", nodes: [
      { ...workspace().shots[0].nodes[0], id: "source" },
      { id: "cut", kind: "trim" as const, input: "node:source", params: { start: 1, end: 2 }, status: "stale" as const, artifacts: [] },
    ] };
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({
      shots: [workspace().shots[0], second],
      checks: [{ level: "error", shotId: "shot-002", nodeId: "cut", message: "步骤已过期" }],
    })));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    fireEvent.change(await screen.findByLabelText("镜头名称"), { target: { value: "保留这份草稿" } });
    await user.click(screen.getByRole("button", { name: "交付" }));
    await user.click(screen.getByRole("button", { name: "定位镜头二 · 裁切时段" }));
    expect(screen.getByLabelText("镜头名称")).toHaveValue("镜头二");
    expect(screen.getByLabelText("开始秒数")).toHaveValue(1);
    expect(screen.getByText("有未保存更改")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^1 保留这份草稿 3 秒$/ }));
    expect(screen.getByLabelText("镜头名称")).toHaveValue("保留这份草稿");
  });

  it("需求提醒可定位且不阻止已保存方案导出", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({
      checks: [{ level: "warning", message: "目标时长需要核对" }],
    })));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    await user.click(await screen.findByRole("button", { name: "交付" }));
    expect(screen.getByRole("button", { name: "下载 ZIP" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "查看需求" }));
    expect(screen.getByLabelText("主题")).toHaveValue("雨夜");
  });

});
