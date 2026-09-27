import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const project = { id: "studio-p1", name: "商品测试项目", createdAt: "2026-09-26T00:00:00Z", updatedAt: "2026-09-26T00:00:00Z", referenceMedia: null, localPreprocessing: null };
const brief = { revision: 0, productName: "", facts: [], audience: "", sellingPoints: [], callToAction: "", forbiddenPhrases: [], assetIds: [] };
const workspace = { revision: 0, brief: { theme: "", purpose: "", style: "", duration: 12, aspect: "9:16", mustPreserve: "" }, assets: [], shots: [], checks: [], nodeCatalog: [] };
const workflow = { stage: "draft", currentStep: 0, completedSteps: [false, false, false, false], activeGenerationId: null, activeBatchId: null,
  counts: { total: 0, pending: 0, approved: 0, rejected: 0, delivered: 0, failed: 0 }, variants: [], issues: [] };
beforeEach(() => {
  history.replaceState(null,"","/");
  vi.stubGlobal("fetch",vi.fn((url) => {
    const path = String(url);
    if(path==="/api/projects") return Promise.resolve(response([project]));
    if(path==="/api/analysis-providers") return Promise.resolve(response([]));
    if(path.endsWith("/preproduction")) return Promise.resolve(response(workspace));
    if(path.endsWith("/aigc-content")) return Promise.resolve(response({brief,assets:[],candidates:[]}));
    if(path.endsWith("/batch-edits")) return Promise.resolve(response({tasks:[],assets:[]}));
    if(path.endsWith("/content-workflow")) return Promise.resolve(response(workflow));
    if(path.endsWith("/capabilities")) return Promise.resolve(response({analysisService:{state:"unconfigured",label:"未配置"},localComfyui:{state:"disconnected",label:"未连接"}}));
    return Promise.reject(new Error(`unexpected ${path}`));
  }));
});
afterEach(()=>vi.unstubAllGlobals());

it("将历史入口明确标为旧版复刻工作台", async () => {
  render(<App />);
  expect(await screen.findByRole("link", { name: "旧版复刻工作台（兼容）" })).toHaveAttribute("href", "?workspace=legacy");
});

it("正式首页读真实项目并进入四步制作，页面切换保留商品草稿",async()=>{
  render(<App/>);
  await userEvent.click(await screen.findByRole("button",{name:/打开项目 商品测试项目/}));
  expect(await screen.findByRole("button",{name:"生成 5 条脚本"})).toBeVisible();
  await userEvent.type(screen.getByLabelText("商品名称"),"保留的商品资料");
  await userEvent.click(within(screen.getByRole("navigation",{name:"项目导航"})).getByRole("button",{name:"项目素材"}));
  expect(await screen.findByRole("heading",{name:"素材库"})).toBeVisible();
  await userEvent.click(within(screen.getByRole("navigation",{name:"项目导航"})).getByRole("button",{name:"商品视频制作"}));
  expect(screen.getByLabelText("商品名称")).toHaveValue("保留的商品资料");
  expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init||!("method" in init)||init.method==="GET")).toBe(true);
});

it("重新打开项目后仍从服务端恢复审核导出步骤", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/content-workflow")
    ? Promise.resolve(response({ ...workflow, stage: "review_pending", currentStep: 3, completedSteps: [true, true, true, false],
      activeGenerationId: "g1", activeBatchId: "batch-1" }))
    : original(url, init));
  const first = render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /打开项目 商品测试项目/ }));
  expect(await screen.findByRole("button", { name: "4 审核导出" })).toHaveAttribute("aria-current", "step");

  first.unmount();
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /打开项目 商品测试项目/ }));
  expect(await screen.findByRole("button", { name: "4 审核导出" })).toHaveAttribute("aria-current", "step");
});

it("读取失败不伪装演示项目，可重试获得真实列表",async()=>{
  vi.mocked(fetch).mockImplementationOnce(()=>Promise.resolve(response({detail:"项目目录不可读"},500)));
  render(<App/>);
  expect(await screen.findByRole("alert")).toHaveTextContent("项目目录不可读");
  expect(screen.queryByRole("button",{name:/打开项目/})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button",{name:"重新读取项目"}));
  expect(await screen.findByRole("button",{name:/打开项目 商品测试项目/})).toBeVisible();
});

it("脚本草稿切换画面素材后，正式预览立即跟随当前选择", async () => {
  const media = [{ id: "front", name: "商品正面", kind: "image", url: "/front.png", duration: null },
    { id: "detail", name: "商品细节", kind: "image", url: "/detail.png", duration: null }];
  const candidate = { id: "c1", generationId: "g1", revision: 0, briefRevision: 1, confirmedRevision: null,
    sellingPoint: "细节展示", beats: [{ text: "核对商品细节", assetId: "front", factIds: ["f1"] }, { text: "核对第二镜头", assetId: "detail", factIds: ["f1"] }] };
  vi.mocked(fetch).mockImplementation((url) => {
    const path = String(url);
    if (path === "/api/projects") return Promise.resolve(response([project]));
    if (path === "/api/analysis-providers") return Promise.resolve(response([]));
    if (path.endsWith("/preproduction")) return Promise.resolve(response({ ...workspace, assets: media }));
    if (path.endsWith("/aigc-content")) return Promise.resolve(response({ brief: { ...brief, revision: 1,
      productName: "商品", facts: [{ id: "f1", text: "商品事实" }], assetIds: ["front", "detail"] }, assets: media, candidates: [candidate] }));
    if (path.endsWith("/batch-edits")) return Promise.resolve(response({ tasks: [], assets: media }));
    if (path.endsWith("/content-workflow")) return Promise.resolve(response({ ...workflow, stage: "brief_ready", currentStep: 1,
      completedSteps: [true, false, false, false], activeGenerationId: "g1" }));
    return Promise.reject(new Error(`unexpected ${path}`));
  });
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /打开项目 商品测试项目/ }));
  await userEvent.click(await screen.findByRole("button", { name: "2 确认脚本" }));
  await userEvent.selectOptions(screen.getByLabelText("候选 1 镜头 1 画面素材"), "detail");
  const preview = within(screen.getByRole("complementary", { name: "项目素材预览" }));
  expect(preview.getByAltText("商品细节")).toBeVisible();
  expect(preview.getByAltText("商品细节")).toHaveAttribute("src", "/detail.png");
  await userEvent.selectOptions(screen.getByLabelText("候选 1 镜头 2 画面素材"), "front");
  expect(preview.getByAltText("商品正面")).toBeVisible();
});

it("新建项目失败保留名称并允许重试，成功后打开真实项目", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let attempts = 0;
  vi.mocked(fetch).mockImplementation((url, init) => String(url) === "/api/projects" && init?.method === "POST"
    ? Promise.resolve(++attempts === 1 ? response({ detail: "项目目录暂不可写" }, 500) : response({ ...project, id: "created", name: "新品制作" }))
    : original(url, init));
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "新建视频" }));
  await userEvent.type(screen.getByLabelText("项目名称"), "新品制作");
  await userEvent.click(screen.getByRole("button", { name: "创建并开始制作" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("项目目录暂不可写");
  expect(screen.getByLabelText("项目名称")).toHaveValue("新品制作");
  await userEvent.click(screen.getByRole("button", { name: "创建并开始制作" }));
  expect(await screen.findByRole("heading", { level: 2, name: "新品制作" })).toBeVisible();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("商品视频制作");
});
