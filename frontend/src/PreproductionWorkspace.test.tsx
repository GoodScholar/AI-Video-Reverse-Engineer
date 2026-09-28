import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    schemaVersion: 2,
    revision: 3,
    brief: { theme: "雨夜", purpose: "预告", style: "电影", duration: 12, aspect: "16:9", mustPreserve: "服装" },
    assets: [{ id: "asset-001", name: "主角.png", kind: "image", role: "character", url: "/asset.png" }],
    scenes: [{ id: "scene-default", title: "未分场", rank: "00000001", description: "" }],
    shots: [{
      id: "shot-001", sceneId: "scene-default", rank: "00000001", title: "镜头一", duration: 3, prompt: "雨夜街头", negativePrompt: "模糊", assetIds: [],
      nodes: [{ id: "node-001", kind: "reference", input: "asset:asset-001", params: {}, status: "pending", artifacts: [] }],
    }],
    workflow: { nodes: [], edges: [] },
    canvasLayout: { scope: { type: "project", id: project.id }, layoutRevision: 0, nodes: {} },
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
  beforeEach(() => {
    sessionStorage.clear(); localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("matchMedia", vi.fn((query: string) => ({ matches: false, media: query, onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn() })));
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  });

  it("镜头页接入分屏、画布和列表模式，项目切换后恢复新项目默认范围", async () => {
    const secondProject = { ...project, id: "project-002", name: "清晨街景" };
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(workspace()))
      .mockResolvedValueOnce(response(workspace({ scenes: [{ id: "scene-morning", title: "清晨", rank: "00000001", description: "" }], shots: [{ ...workspace().shots[0], id: "shot-morning", sceneId: "scene-morning", title: "清晨镜头" }] })));
    const view = render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);

    expect(await screen.findByRole("button", { name: "分屏视图" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "镜头关系图" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "画布视图" }));
    expect(screen.queryByRole("complementary", { name: "按场景组织的镜头列表" })).not.toBeInTheDocument();

    view.rerender(<PreproductionWorkspace project={secondProject} tools={<p>工具</p>} />);
    expect(await screen.findAllByText("清晨镜头")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "分屏视图" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "清晨" })).toBeVisible();
    expect(screen.getByLabelText("场景 清晨")).toBeVisible();
  });

  it("检查器随场景和多镜头选择切换，只呈现兼容内容", async () => {
    const second = {
      ...workspace().shots[0], id: "shot-002", rank: "00000002", title: "镜头二", nodes: [],
    };
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({
      scenes: [{ id: "scene-default", title: "雨夜相遇", rank: "00000001", description: "雨夜街头的完整段落" }],
      shots: [workspace().shots[0], second],
    })));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);

    const sceneButton = await screen.findByRole("button", { name: "折叠场景雨夜相遇" });
    await userEvent.click(sceneButton);
    const sceneSummary = screen.getByRole("region", { name: "雨夜相遇场景摘要" });
    expect(within(sceneSummary).getByRole("heading", { name: "场景摘要" })).toBeVisible();
    expect(within(sceneSummary).getByText("雨夜街头的完整段落")).toBeVisible();
    expect(within(sceneSummary).getByText("2 个镜头 · 6 秒")).toBeVisible();
    expect(screen.queryByLabelText("镜头名称")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "展开场景雨夜相遇" }));
    fireEvent.click(await screen.findByRole("button", { name: "1 镜头一 3 秒" }));
    fireEvent.click(screen.getByRole("button", { name: "2 镜头二 3 秒" }), { metaKey: true });
    expect(screen.getByRole("heading", { name: "已选择 2 个镜头" })).toBeVisible();
    expect(screen.queryByLabelText("镜头名称")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "步骤参数" })).not.toBeInTheDocument();
  });

  it("镜头顺序有空洞时新建镜头使用未占用的新 rank", async () => {
    const current = workspace({
      shots: [
        { ...workspace().shots[0], id: "shot-003", rank: "00000003" },
        { ...workspace().shots[0], id: "shot-004", rank: "00000004", title: "镜头四" },
      ],
    });
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(current))
      .mockImplementationOnce(async (_url, init) => response({
        ...current,
        ...JSON.parse(String(init?.body)),
        revision: current.revision + 1,
      }));
    render(<PreproductionWorkspace project={project} tools={null} />);

    await userEvent.click(await screen.findByRole("button", { name: "新建镜头" }));
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    const saved = JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body));
    expect(saved.shots).toHaveLength(3);
    expect(saved.shots.map((shot: Workspace["shots"][number]) => shot.rank)).toContain("00000005");
  });

  it("跨镜头同名流程节点仍打开所属镜头的检查器", async () => {
    const sharedNode = { id: "shared-node", kind: "prompt" as const, input: "", params: { text: "节点内容" }, status: "pending" as const, artifacts: [] };
    const first = { ...workspace().shots[0], nodes: [sharedNode] };
    const second = { ...first, id: "shot-002", rank: "00000002", title: "镜头二" };
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ shots: [first, second] })));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);

    await userEvent.click(await screen.findByRole("button", { name: "2 镜头二 3 秒" }));
    await userEvent.click(screen.getByRole("button", { name: "查看镜头二关系" }));
    await userEvent.click(await screen.findByRole("article", { name: "流程节点 提示词" }));

    expect(screen.getByLabelText("镜头名称")).toHaveValue("镜头二");
    expect(screen.getByRole("article", { name: "镜头二" })).toHaveClass("is-context");
    expect(screen.getByRole("button", { name: "2 镜头二 3 秒" })).toHaveAttribute("aria-pressed", "false");
  });

  it("正式全站导航能直接打开素材与工具，切换时保留未保存需求", async () => {
    vi.mocked(fetch).mockResolvedValue(response(workspace()));
    const view = render(<PreproductionWorkspace project={project} tools={<p>真实项目工具</p>} sectionOverride="brief" studioMode />);
    await userEvent.clear(await screen.findByLabelText("主题"));
    await userEvent.type(screen.getByLabelText("主题"), "保留的草稿");
    view.rerender(<PreproductionWorkspace project={project} tools={<p>真实项目工具</p>} sectionOverride="assets" studioMode />);
    expect(await screen.findByRole("heading", { name: "素材库" })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "工作台导航" })).not.toBeInTheDocument();
    view.rerender(<PreproductionWorkspace project={project} tools={<p>真实项目工具</p>} sectionOverride="brief" studioMode />);
    expect(await screen.findByLabelText("主题")).toHaveValue("保留的草稿");
  });

  it("网络图片导入同步素材并保留未保存需求，后续保存使用新版本", async () => {
    const current = workspace();
    const imported = workspace({revision:4,assets:[...current.assets,{id:"web-image",name:"商品正面",kind:"image",role:"reference",url:"/web.png"}]});
    vi.mocked(fetch).mockResolvedValueOnce(response(current))
      .mockImplementationOnce(async (_url,init)=>response({...imported,...JSON.parse(String(init?.body)),revision:5}));
    const onAssetsChanged=vi.fn();
    const view=render(<PreproductionWorkspace project={project} tools={null} sectionOverride="brief" studioMode onAssetsChanged={onAssetsChanged} />);
    await userEvent.clear(await screen.findByLabelText("主题"));
    await userEvent.type(screen.getByLabelText("主题"),"保留本地需求");
    view.rerender(<PreproductionWorkspace project={project} tools={null} sectionOverride="brief" studioMode onAssetsChanged={onAssetsChanged} importedWorkspace={{projectId:project.id,workspace:imported}} />);
    await waitFor(()=>expect(onAssetsChanged).toHaveBeenLastCalledWith(imported.assets));
    expect(screen.getByLabelText("主题")).toHaveValue("保留本地需求");
    await userEvent.click(screen.getByRole("button",{name:"保存更改"}));
    const saved=JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body));
    expect(saved.revision).toBe(4);expect(saved.brief.theme).toBe("保留本地需求");
  });

  it("迟到的旧保存响应不会覆盖新导入图片和版本", async () => {
    const pending=deferred<Response>();
    const current=workspace();
    vi.mocked(fetch).mockResolvedValueOnce(response(current)).mockReturnValueOnce(pending.promise);
    const onAssetsChanged=vi.fn();
    const view=render(<PreproductionWorkspace project={project} tools={null} sectionOverride="brief" studioMode onAssetsChanged={onAssetsChanged} />);
    await userEvent.clear(await screen.findByLabelText("主题"));
    await userEvent.type(screen.getByLabelText("主题"),"已保存新需求");
    await userEvent.click(screen.getByRole("button",{name:"保存更改"}));
    const saved=workspace({revision:4,brief:{...current.brief,theme:"已保存新需求"}});
    const imported={...saved,revision:5,assets:[...saved.assets,{id:"web-image",name:"商品正面",kind:"image" as const,role:"reference" as const,url:"/web.png"}]};
    view.rerender(<PreproductionWorkspace project={project} tools={null} sectionOverride="brief" studioMode onAssetsChanged={onAssetsChanged} importedWorkspace={{projectId:project.id,workspace:imported}} />);
    await waitFor(()=>expect(onAssetsChanged).toHaveBeenLastCalledWith(imported.assets));
    await act(async()=>pending.resolve(response(saved)));
    await waitFor(()=>expect(onAssetsChanged).toHaveBeenLastCalledWith(imported.assets));
    expect(screen.getByLabelText("主题")).toHaveValue("已保存新需求");
  });

  it("从现有工作台进入批量混剪任务", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [], assets: [] }));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    await userEvent.click(await screen.findByRole("button", { name: "批量混剪" }));
    expect(screen.getByRole("heading", { name: "批量混剪" })).toBeVisible();
  });

  it("切换工作台页面时保留未保存的混剪片段编辑", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    vi.mocked(fetch).mockResolvedValueOnce(response({ tasks: [{ id: "batch-a", sellingPoint: "省时", script: "操作演示",
      variant: { id: "variant-a", revision: 0, settings: { width: 720, height: 1280, fps: 30 },
        tracks: [{ id: "video", name: "画面", kind: "video", muted: false, hidden: false, clips: [] },
          { id: "audio", name: "声音", kind: "audio", muted: false, hidden: false, clips: [] }], runs: [] },
    }], assets: [{ id: "shot", name: "开场", kind: "video", duration: 4, url: "/shot.mp4" }] }));
    const onDraftChange = vi.fn();
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} onDraftChange={onDraftChange} />);
    await userEvent.click(await screen.findByRole("button", { name: "批量混剪" }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑 省时" }));
    await userEvent.click(screen.getByRole("button", { name: "添加素材 开场" }));
    expect(onDraftChange).toHaveBeenLastCalledWith(project.id, true);
    await userEvent.click(screen.getByRole("button", { name: "素材" }));
    await userEvent.click(screen.getByRole("button", { name: "批量混剪" }));
    expect(screen.getByLabelText("开场 入点（秒）")).toBeVisible();
  });

  it("离开后恢复未保存镜头，并保留原版本以防覆盖新版本", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    const first = render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    fireEvent.change(await screen.findByLabelText("镜头名称"), { target: { value: "未保存镜头" } });
    first.unmount(); sessionStorage.clear();
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ revision: 4 })));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    expect(await screen.findByDisplayValue("未保存镜头")).toBeVisible();
    expect(screen.getByText(/已保存版本已更新/)).toBeVisible();
    vi.mocked(fetch).mockResolvedValueOnce(response({ detail: "版本冲突" }, 409));
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));
    const body = JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string);
    expect(body.revision).toBe(3);
    expect(body.shots[0].title).toBe("未保存镜头");
  });

  it("恢复草稿按项目隔离，保存成功后清除缓存", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    const first = render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    fireEvent.change(await screen.findByLabelText("镜头名称"), { target: { value: "我的草稿" } });
    first.unmount();
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    const other = render(<PreproductionWorkspace project={{ ...project, id: "other" }} tools={<p>工具</p>} />);
    expect(await screen.findByLabelText("镜头名称")).toHaveValue("镜头一");
    other.unmount();
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    expect(await screen.findByLabelText("镜头名称")).toHaveValue("我的草稿");
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ revision: 4, shots: [{ ...workspace().shots[0], title: "我的草稿" }] })));
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));
    expect(sessionStorage.getItem("aivre:preproduction-draft:project-001")).toBeNull();
  });

  it("上传按钮支持键盘触发，导航和筛选暴露选中状态", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    await screen.findByLabelText("镜头名称");
    await userEvent.click(screen.getByRole("button", { name: "素材" }));
    expect(screen.getByRole("button", { name: "素材" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "全部" })).toHaveAttribute("aria-pressed", "true");
    const inputClick = vi.spyOn(screen.getByLabelText("上传素材文件"), "click");
    screen.getByRole("button", { name: "上传素材" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(inputClick).toHaveBeenCalledOnce();
  });

  it("保存三类输入选择，深度入口复用已有素材且白模显示渲染说明", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace()));
    render(<PreproductionWorkspace project={project} tools={<h2 id="reference-media-title">参考素材工具</h2>} />);
    await userEvent.click(await screen.findByRole("radio", { name: "灰度深度视频" }));
    expect(screen.getByText(/无需再次估计深度/)).toBeVisible();
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ revision: 4, brief: { ...workspace().brief, inputKind: "depth_video" } })));
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));
    expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string).brief.inputKind).toBe("depth_video");
    await userEvent.click(screen.getByRole("button", { name: "导入并绑定深度素材" }));
    expect(screen.getByRole("button", { name: "素材" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(screen.getByRole("radio", { name: "三维白模渲染视频" }));
    expect(screen.getByText(/这里不接收三维工程文件/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "上传或查看源视频" }));
    expect(screen.getByText("参考素材工具")).toHaveFocus();
  });

  it("将上传视频关联到指定镜头并携带方案版本，草稿未保存时禁用上传", async () => {
    const initial = workspace({ shots: [{ ...workspace().shots[0], nodes: [] }] });
    vi.mocked(fetch).mockResolvedValueOnce(response(initial));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    await screen.findByLabelText("镜头名称");
    const result = { id: "generated", name: "结果.mp4", kind: "video" as const, role: "motion" as const, url: "/result.mp4", duration: 4 };
    vi.mocked(fetch).mockResolvedValueOnce(response({ ...initial, revision: 4, assets: [...initial.assets, result], shots: [{ ...initial.shots[0], resultAssetId: "generated" }] }));
    fireEvent.change(screen.getByLabelText("上传本镜头结果文件"), { target: { files: [new File(["video"], "结果.mp4", { type: "video/mp4" })] } });
    expect(await screen.findByRole("link", { name: "查看结果视频" })).toHaveAttribute("href", "/result.mp4");
    expect(vi.mocked(fetch).mock.calls[1][0]).toContain("resultForShot=shot-001&revision=3");
    expect(screen.getByText(/已回传 1 \/ 1.*时长合格 1.*已人工复核 0/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("镜头名称"), { target: { value: "更改" } });
    expect(screen.getByRole("button", { name: "上传本镜头结果" })).toBeDisabled();
  });

  it("候选检查提交当前版本，切换采用后仍需保存方案", async () => {
    const versions = [{ assetId: "v1", reviewed: false, planChanged: false }, { assetId: "v2", reviewed: false, planChanged: true }];
    const initial = workspace({ assets: ["v1", "v2"].map((id) => ({ id, name: id + ".mp4", kind: "video", role: "motion", duration: 3, url: "/" + id })), shots: [{ ...workspace().shots[0], nodes: [], resultAssetId: "v2", resultVersions: versions }] });
    vi.mocked(fetch).mockResolvedValueOnce(response(initial));
    render(<PreproductionWorkspace project={project} tools={<p>工具</p>} />);
    const reviewed = { ...initial, revision: 4, shots: [{ ...initial.shots[0], resultVersions: [versions[0], { ...versions[1], reviewed: true, planChanged: false }] }] };
    vi.mocked(fetch).mockResolvedValueOnce(response(reviewed));
    await userEvent.click(await screen.findByRole("button", { name: "确认已检查候选 2" }));
    expect(await screen.findByText("已按当前方案人工检查")).toBeVisible();
    expect(vi.mocked(fetch).mock.calls[1][0]).toContain("/shots/shot-001/results/v2/review");
    expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string)).toEqual({ revision: 3 });
    await userEvent.click(screen.getByRole("button", { name: "采用候选 1" }));
    expect(screen.getByRole("button", { name: "确认已检查候选 1" })).toBeDisabled();
    vi.mocked(fetch).mockResolvedValueOnce(response({ ...reviewed, revision: 5, shots: [{ ...reviewed.shots[0], resultAssetId: "v1" }] }));
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));
    const body = JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string);
    expect(body.revision).toBe(4);
    expect(body.shots[0].resultAssetId).toBe("v1");
    expect(screen.getByText("候选版本 · 2")).toBeVisible();
  });

  it("初始空态提供导入和创建镜头入口", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(workspace({ assets: [], shots: [] })));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    expect(await screen.findByText("从已有参考或分镜开始")).toBeVisible();
    expect(screen.getByRole("button", { name: "导入参考素材" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: "新建镜头" })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "画布视图" }));
    expect(screen.getByText("还没有可查看的镜头")).toBeVisible();
  });

  it("绑定素材、保存后才允许运行当前节点", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(workspace()))
      .mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], assetIds: ["asset-001"] }] })))
      .mockResolvedValueOnce(response(workspace({ shots: [{ ...workspace().shots[0], assetIds: ["asset-001"], nodes: [{ ...workspace().shots[0].nodes[0], status: "queued" }] }] }), 202));
    render(<PreproductionWorkspace project={project} tools={<p>已有工具</p>} />);

    await screen.findByRole("button", { name: "打开镜头一" });
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

    expect(await screen.findByRole("button", { name: "打开新项目镜头" })).toBeVisible();
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

    await screen.findByRole("button", { name: "打开镜头一" });
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

it("正式素材页直接展示图片、视频与音频核对控件", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(workspace({ assets: [
    { id: "image", name: "商品图片", kind: "image", role: "reference", url: "/product.png" },
    { id: "video", name: "商品视频", kind: "video", role: "reference", url: "/product.mp4" },
    { id: "audio", name: "商品配音", kind: "audio", role: "audio", url: "/voice.wav" },
  ] }))));
  render(<PreproductionWorkspace project={project} tools={null} sectionOverride="assets" studioMode />);
  expect(await screen.findByAltText("商品图片 预览")).toBeVisible();
  expect(screen.getByLabelText("商品视频 预览")).toBeVisible();
  expect(screen.getByLabelText("商品配音 预览")).toBeVisible();
});
