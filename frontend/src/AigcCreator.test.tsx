import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { AigcCreator } from "./AigcCreator";

const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const emptyBrief = { revision: 0, productName: "", facts: [], audience: "", sellingPoints: [],
  callToAction: "", forbiddenPhrases: [], assetIds: [] };
const assets = [
  { id: "front", name: "杯盖展示", notes: "防泼溅", kind: "image", width: 1920, height: 1080 },
  { id: "use", name: "通勤演示", notes: "背包收纳", kind: "video", width: 1280, height: 720 },
];
const provider = { provider: "local_openai_compatible", label: "本地 AI 服务", models: [], model: "local-model",
  baseUrl: "http://127.0.0.1:8188", credentialState: "unconfigured", selectedProvider: "local_openai_compatible",
  configurationRevision: 1, catalogVersion: 1, verificationState: "available", verifiedAt: "2026-09-25T00:00:00Z" };

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

it("准备资料可选择七种视频比例并保存恢复实际尺寸", async () => {
  const brief = { ...emptyBrief, aspectMode: "smart" as const, assetIds: ["front", "use"] };
  const saved = { ...brief, revision: 1, aspectMode: "16:9" as const };
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates: [], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({ brief: saved }));
  render(<AigcCreator projectId="p1" guidedStep={0} />);

  expect(await screen.findByRole("radio", { name: "智能" })).toBeChecked();
  expect(screen.getByText("实际输出 16:9 · 1280×720")).toBeVisible();
  await userEvent.click(screen.getByRole("radio", { name: "16:9" }));
  await userEvent.click(screen.getByRole("button", { name: "保存资料" }));

  const payload = JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string);
  expect(payload.brief.aspectMode).toBe("16:9");
  expect(screen.getByRole("radio", { name: "16:9" })).toBeChecked();
});

it("四步制作的生成按钮先保存新资料并展示发送内容，未经确认不生成", async () => {
  const brief = { ...emptyBrief, revision: 1, productName: "晴雨杯", facts: [{ id: "f1", text: "杯盖防泼溅" }], assetIds: ["front", "use"] };
  const saved = { ...brief, revision: 2, audience: "通勤者" };
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates: [], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({ brief: saved }))
    .mockResolvedValueOnce(response({ briefRevision: 2, provider: provider.provider, model: provider.model, prompt: "已保存新资料：通勤者", digest: "d2" }));
  render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.type(await screen.findByLabelText("目标受众"), "通勤者");
  await userEvent.click(screen.getByRole("button", { name: "生成 5 条脚本" }));
  expect(await screen.findByText("已保存新资料：通勤者")).toBeVisible();
  expect(screen.getByRole("button", { name: "确认发送并生成 5 条候选" })).toBeDisabled();
  expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual([
    "/api/projects/p1/aigc-content", "/api/analysis-providers", "/api/projects/p1/aigc-content/brief", "/api/projects/p1/aigc-content/disclosure",
  ]);
});

it("四步制作保存资料失败时保留输入且不展示旧发送内容", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: { ...emptyBrief, productName: "杯", facts: [{ id: "f1", text: "事实" }], assetIds: ["front", "use"] }, candidates: [], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "版本冲突" }), { status: 409 }));
  render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.type(await screen.findByLabelText("目标受众"), "新的受众");
  await userEvent.click(screen.getByRole("button", { name: "生成 5 条脚本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("版本冲突");
  expect(screen.getByLabelText("目标受众")).toHaveValue("新的受众");
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3);
});

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

it("尚未通过连接测试的 AI 服务不能用于脚本生成", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: emptyBrief, candidates: [], assets }))
    .mockResolvedValueOnce(response([{ ...provider, verificationState: "unverified" }]));
  render(<AigcCreator projectId="p1" />);

  expect(await screen.findByRole("button", { name: "预览发送内容" })).toBeDisabled();
});

it("保存简报后逐次展示发送文字并由交付人员确认生成五条候选", async () => {
  const savedBrief = { ...emptyBrief, revision: 1, productName: "晴雨杯", facts: [{ id: "fact-1", text: "杯盖防泼溅" },
    { id: "fact-2", text: "杯身可重复使用" }],
    audience: "通勤者", sellingPoints: ["携带方便"], callToAction: "查看详情", forbiddenPhrases: ["绝对防水"],
    assetIds: ["front", "use"] };
  const disclosure = { briefRevision: 1, provider: "local_openai_compatible", model: "local-model",
    prompt: "将发送：杯盖防泼溅、杯盖展示、通勤演示", digest: "digest-1" };
  const candidates = Array.from({ length: 5 }, (_, index) => ({ id: `candidate-${index}`, generationId: "g1",
    revision: 0, briefRevision: 1, confirmedRevision: null, sellingPoint: `卖点 ${index + 1}`,
    beats: [{ text: `杯盖防泼溅 ${index + 1}`, factIds: ["fact-1"], assetId: "front" },
      { text: "通勤随手带", factIds: ["fact-1"], assetId: "use" }] }));
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: emptyBrief, candidates: [], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({ brief: savedBrief }))
    .mockResolvedValueOnce(response(disclosure))
    .mockResolvedValueOnce(response({ candidates }));
  render(<AigcCreator projectId="p1" />);

  await userEvent.type(await screen.findByLabelText("商品名称"), "晴雨杯");
  await userEvent.type(screen.getByLabelText("商品事实（每行一条）"), "杯盖防泼溅{enter}杯身可重复使用");
  await userEvent.type(screen.getByLabelText("目标受众"), "通勤者");
  await userEvent.type(screen.getByLabelText("卖点（每行一条）"), "携带方便");
  await userEvent.type(screen.getByLabelText("行动引导"), "查看详情");
  await userEvent.type(screen.getByLabelText("禁用表达（每行一条）"), "绝对防水");
  await userEvent.click(screen.getByLabelText("选择素材 杯盖展示"));
  await userEvent.click(screen.getByLabelText("选择素材 通勤演示"));
  await userEvent.click(screen.getByRole("button", { name: "保存创作简报" }));

  await userEvent.click(await screen.findByRole("button", { name: "预览发送内容" }));
  expect(await screen.findByText(disclosure.prompt)).toBeVisible();
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(4);
  expect(screen.getByRole("button", { name: "确认发送并生成 5 条候选" })).toBeDisabled();
  await userEvent.click(screen.getByLabelText("我已核对本次发送内容"));
  await userEvent.click(screen.getByRole("button", { name: "确认发送并生成 5 条候选" }));

  expect(await screen.findByText("卖点 5")).toBeVisible();
  const request = vi.mocked(fetch).mock.calls[4];
  expect(request[0]).toContain("/api/projects/p1/aigc-content/candidates");
  expect(JSON.parse(request[1]?.body as string)).toEqual({ briefRevision: 1, provider: "local_openai_compatible",
    model: "local-model", digest: "digest-1", disclosureAccepted: true });
  expect(JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string).brief.facts).toEqual(savedBrief.facts);
});

it("修改脚本候选后必须保存新版本再确认", async () => {
  const brief = { ...emptyBrief, revision: 1, productName: "晴雨杯", facts: [{ id: "f1", text: "杯盖防泼溅" }],
    assetIds: ["front", "use"] };
  const original = { id: "c1", generationId: "g1", revision: 0, briefRevision: 1, confirmedRevision: null,
    sellingPoint: "防泼溅", beats: [{ text: "杯盖防泼溅", factIds: ["f1"], assetId: "front" },
      { text: "通勤使用", factIds: ["f1"], assetId: "use" }] };
  const updated = { ...original, revision: 1, sellingPoint: "轻松通勤" };
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates: [original], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({ candidate: updated }))
    .mockResolvedValueOnce(response({ candidate: { ...updated, confirmedRevision: 1 } }));
  render(<AigcCreator projectId="p1" />);

  const sellingPoint = await screen.findByLabelText("候选 1 卖点");
  await userEvent.clear(sellingPoint);
  await userEvent.type(sellingPoint, "轻松通勤");
  await userEvent.click(screen.getByRole("button", { name: "上移候选 1 镜头 2" }));
  expect(screen.getByRole("button", { name: "确认候选 1" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "保存候选 1" }));
  await userEvent.click(await screen.findByRole("button", { name: "确认候选 1" }));

  expect(await screen.findByText("已确认版本 1")).toBeVisible();
  expect(JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string).sellingPoint).toBe("轻松通勤");
  expect(JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string).beats[0].assetId).toBe("use");
  expect(JSON.parse(vi.mocked(fetch).mock.calls[3][1]?.body as string)).toEqual({ revision: 1 });
});

it("确认脚本候选成功后通知刷新服务端阶段", async () => {
  const brief = { ...emptyBrief, revision: 1, productName: "晴雨杯", facts: [{ id: "f1", text: "杯盖防泼溅" }],
    assetIds: ["front", "use"] };
  const candidate = { id: "c1", generationId: "g1", revision: 0, briefRevision: 1, confirmedRevision: null,
    sellingPoint: "防泼溅", beats: [{ text: "杯盖防泼溅", factIds: ["f1"], assetId: "front" }] };
  const onPersistedChange = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates: [candidate], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({ candidate: { ...candidate, confirmedRevision: 0 } }));
  render(<AigcCreator projectId="p1" onPersistedChange={onPersistedChange} />);

  await userEvent.click(await screen.findByRole("button", { name: "确认候选 1" }));

  expect(await screen.findByText("已确认版本 0")).toBeVisible();
  expect(onPersistedChange).toHaveBeenCalledTimes(1);
});

it("五条当前已确认候选可交给批量混剪", async () => {
  const brief = { ...emptyBrief, revision: 1, productName: "晴雨杯", facts: [{ id: "f1", text: "杯盖防泼溅" }],
    assetIds: ["front", "use"] };
  const candidates = Array.from({ length: 5 }, (_, index) => ({ id: `c${index}`, generationId: "g1", revision: 0,
    briefRevision: 1, confirmedRevision: 0, sellingPoint: `卖点 ${index}`, beats: [
      { text: "杯盖防泼溅", factIds: ["f1"], assetId: "front" },
      { text: "通勤使用", factIds: ["f1"], assetId: "use" }] }));
  const onBatchCreated = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates, assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({ task: { id: "batch-parent" }, skippedTaskIds: ["edited-child"] }));
  render(<AigcCreator projectId="p1" onBatchCreated={onBatchCreated} />);

  await userEvent.click(await screen.findByRole("button", { name: "将 5 条已确认候选交给批量混剪" }));

  expect(onBatchCreated).toHaveBeenCalledWith("batch-parent");
  expect(await screen.findByText("已更新批量任务；1 条失败变体已有人工修改，未自动覆盖，请在批量任务中继续修复。")).toBeVisible();
  expect(JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string)).toEqual({ generationId: "g1" });
});

it("切换项目后忽略旧项目迟到的发送内容预览", async () => {
  const pending = deferred<Response>();
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: emptyBrief, candidates: [], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(response({ brief: { ...emptyBrief, productName: "另一商品" }, candidates: [], assets }))
    .mockResolvedValueOnce(response([provider]));
  const view = render(<AigcCreator projectId="p1" />);
  await userEvent.click(await screen.findByRole("button", { name: "预览发送内容" }));
  view.rerender(<AigcCreator projectId="p2" />);
  await screen.findByDisplayValue("另一商品");
  await act(async () => pending.resolve(response({ briefRevision: 0, provider: "local_openai_compatible",
    model: "local-model", prompt: "旧项目私有商品信息", digest: "old" })));

  expect(screen.queryByText("旧项目私有商品信息")).not.toBeInTheDocument();
});

it("简报更新后可不改写文案而重新核对候选", async () => {
  const brief = { ...emptyBrief, revision: 2, productName: "晴雨杯", facts: [{ id: "f1", text: "杯盖防泼溅" }],
    assetIds: ["front", "use"] };
  const stale = { id: "c1", generationId: "g1", revision: 0, briefRevision: 1, confirmedRevision: 0,
    sellingPoint: "防泼溅", beats: [{ text: "杯盖防泼溅", factIds: ["f1"], assetId: "front" },
      { text: "通勤使用", factIds: ["f1"], assetId: "use" }] };
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates: [stale], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({ candidate: { ...stale, revision: 1, briefRevision: 2, confirmedRevision: null } }));
  render(<AigcCreator projectId="p1" />);

  const save = await screen.findByRole("button", { name: "保存候选 1" });
  expect(save).toBeEnabled();
  await userEvent.click(save);

  expect(JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string).sellingPoint).toBe("防泼溅");
  expect(await screen.findByRole("button", { name: "确认候选 1" })).toBeEnabled();
});


it("引导制作可先保存资料，无需配置服务或发送脚本", async () => {
  const saved = { ...emptyBrief, revision: 1, productName: "稍后制作的商品" };
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: emptyBrief, candidates: [], assets }))
    .mockResolvedValueOnce(response([])).mockResolvedValueOnce(response({ brief: saved }));
  render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.type(await screen.findByLabelText("商品名称"), saved.productName);
  await userEvent.click(screen.getByRole("button", { name: "保存资料" }));
  expect(await screen.findByRole("button", { name: "保存资料" })).toBeDisabled();
  expect(screen.getByLabelText("商品名称")).toHaveValue(saved.productName);
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PUT").map(([url]) => url)).toEqual(["/api/projects/p1/aigc-content/brief"]);
  expect(vi.mocked(fetch).mock.calls.some(([url]) => /disclosure|candidates/.test(String(url)))).toBe(false);
});

it("引导制作重新读取已验证脚本服务时保留资料草稿", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: emptyBrief, candidates: [], assets }))
    .mockResolvedValueOnce(response([])).mockResolvedValueOnce(response([provider]));
  render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.type(await screen.findByLabelText("商品名称"), "保留资料");
  await userEvent.click(screen.getByRole("button", { name: "重新读取脚本服务" }));
  expect(await screen.findByRole("option", { name: "本地 AI 服务 · local-model" })).toBeInTheDocument();
  expect(screen.getByLabelText("商品名称")).toHaveValue("保留资料");
  expect(screen.getByLabelText("脚本生成服务")).toHaveValue(provider.provider);
});

it("未保存候选时阻止重新生成并提供返回脚本的入口", async () => {
  const candidate = { id: "c1", generationId: "g1", revision: 0, briefRevision: 1, confirmedRevision: null,
    sellingPoint: "原卖点", beats: [{ text: "第一镜头", factIds: ["f1"], assetId: "front" }] };
  const brief = { ...emptyBrief, revision: 1, productName: "商品", facts: [{ id: "f1", text: "事实" }], assetIds: ["front", "use"] };
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates: [candidate], assets })).mockResolvedValueOnce(response([provider]));
  const onStepChange = vi.fn();
  const view = render(<AigcCreator projectId="p1" guidedStep={1} onStepChange={onStepChange} />);
  await userEvent.type(await screen.findByLabelText("候选 1 卖点"), "修改");
  view.rerender(<AigcCreator projectId="p1" guidedStep={0} onStepChange={onStepChange} />);
  expect(screen.getByRole("button", { name: "生成 5 条脚本" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "返回未保存脚本" }));
  expect(onStepChange).toHaveBeenCalledWith(1);
});

it("发送核对打开后修改候选，旧确认不能绕过草稿保护", async () => {
  const candidate = { id: "c1", generationId: "g1", revision: 0, briefRevision: 1, confirmedRevision: null,
    sellingPoint: "原卖点", beats: [{ text: "第一镜头", factIds: ["f1"], assetId: "front" }] };
  const brief = { ...emptyBrief, revision: 1, productName: "商品", facts: [{ id: "f1", text: "事实" }], assetIds: ["front", "use"] };
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief, candidates: [candidate], assets }))
    .mockResolvedValueOnce(response([provider])).mockResolvedValueOnce(response({ briefRevision: 1,
      provider: provider.provider, model: provider.model, prompt: "发送核对", digest: "d1" }));
  const view = render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.click(await screen.findByRole("button", { name: "生成 5 条脚本" }));
  await userEvent.click(await screen.findByRole("checkbox", { name: "我已核对本次发送内容" }));
  view.rerender(<AigcCreator projectId="p1" guidedStep={1} />);
  await userEvent.type(screen.getByLabelText("候选 1 卖点"), "修改");
  view.rerender(<AigcCreator projectId="p1" guidedStep={0} />);
  expect(screen.getByRole("button", { name: "确认发送并生成 5 条候选" })).toBeDisabled();
});

it("确认脚本步骤没有候选时说明原因并可返回准备资料", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: emptyBrief, candidates: [], assets })).mockResolvedValueOnce(response([]));
  const onStepChange = vi.fn();
  render(<AigcCreator projectId="p1" guidedStep={1} onStepChange={onStepChange} />);
  await userEvent.click(await screen.findByRole("button", { name: "返回准备资料" }));
  expect(onStepChange).toHaveBeenCalledWith(0);
});

it("只填商品名时沿用已有画面并补保守事实，不发起网络检索", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ brief: emptyBrief, candidates: [], assets }))
    .mockResolvedValueOnce(response([provider]))
    .mockImplementationOnce(async (_url, init) => response({brief:{...JSON.parse(String(init?.body)).brief,revision:1}}))
    .mockResolvedValueOnce(response({briefRevision:1,provider:provider.provider,model:provider.model,prompt:"商品名称预览",digest:"d"}));
  render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.type(await screen.findByLabelText("商品名称"), "大窑果味汽水");
  await userEvent.click(screen.getByRole("button",{name:"生成 5 条脚本"}));
  expect(await screen.findByText("商品名称预览")).toBeVisible();
  const saved=JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body)).brief;
  expect(saved.assetIds).toEqual(["front","use"]);
  expect(saved.facts[0].text).toContain("商品名称：大窑果味汽水");
  expect(saved.facts[0].text).not.toContain("5至8元");
  expect(vi.mocked(fetch).mock.calls.some(([url])=>String(url).includes("product-images"))).toBe(false);
});

it("没有素材时按商品名找图，核对后导入并继续准备脚本", async () => {
  const candidates=[{id:"pic1",title:"正面",pageUrl:"https://brand.example/product",imageUrl:"https://brand.example/1.png",previewUrl:"/preview/1"},
    {id:"pic2",title:"背面",pageUrl:"https://brand.example/product",imageUrl:"https://brand.example/2.png",previewUrl:"/preview/2"}];
  const imported=vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(response({brief:emptyBrief,candidates:[],assets:[]}))
    .mockResolvedValueOnce(response([provider]))
    .mockResolvedValueOnce(response({searchId:"s1",productName:"大窑",candidates}))
    .mockResolvedValueOnce(response({importedAssetIds:["front","use"]}))
    .mockResolvedValueOnce(response({revision:1,brief:{},shots:[],assets,checks:[],nodeCatalog:[]}))
    .mockImplementationOnce(async (_url,init)=>response({brief:{...JSON.parse(String(init?.body)).brief,revision:1}}))
    .mockResolvedValueOnce(response({prompt:"导入后的发送预览",provider:provider.provider,model:provider.model}));
  render(<AigcCreator projectId="p1" guidedStep={0} onWorkspaceImported={imported} />);
  await userEvent.type(await screen.findByLabelText("商品名称"),"大窑");
  await userEvent.click(screen.getByRole("button",{name:"生成 5 条脚本"}));
  await userEvent.click(await screen.findByLabelText("选择网络图片 正面"));
  await userEvent.click(screen.getByLabelText("选择网络图片 背面"));
  expect(screen.getByRole("button",{name:"导入选中图片并继续"})).toBeDisabled();
  await userEvent.click(screen.getByLabelText("我已核对商品、规格与图片来源"));
  await userEvent.click(screen.getByRole("button",{name:"导入选中图片并继续"}));
  expect(await screen.findByText("导入后的发送预览")).toBeVisible();
  expect(imported).toHaveBeenCalledWith(expect.objectContaining({revision:1}));
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[3][1]?.body))).toEqual({searchId:"s1",candidateIds:["pic1","pic2"],confirmed:true});
});

it("切换项目时旧图片检索结果不能进入新项目", async () => {
  const pending=deferred<Response>();
  vi.mocked(fetch).mockResolvedValueOnce(response({brief:{...emptyBrief,productName:"旧商品"},candidates:[],assets:[]}))
    .mockResolvedValueOnce(response([])).mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(response({brief:{...emptyBrief,productName:"新商品"},candidates:[],assets:[]}))
    .mockResolvedValueOnce(response([]));
  const view=render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.click(await screen.findByRole("button",{name:"自动查找商品图片"}));
  view.rerender(<AigcCreator projectId="p2" guidedStep={0} />);
  await screen.findByDisplayValue("新商品");
  await act(async()=>pending.resolve(response({searchId:"old",productName:"旧商品",candidates:[{id:"x",title:"旧图片",pageUrl:"https://old.example",previewUrl:"/old"}]})));
  expect(screen.queryByLabelText("网络商品图片")).not.toBeInTheDocument();
  expect(screen.getByLabelText("商品名称")).toHaveValue("新商品");
});

it("网络图片搜索失败保留商品名，允许重试", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({brief:{...emptyBrief,productName:"大窑"},candidates:[],assets:[]}))
    .mockResolvedValueOnce(response([])).mockResolvedValueOnce(new Response(JSON.stringify({detail:"网络检索暂时不可用"}),{status:503}));
  render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.click(await screen.findByRole("button",{name:"自动查找商品图片"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("网络检索暂时不可用");
  expect(screen.getByLabelText("商品名称")).toHaveValue("大窑");
  expect(screen.getByRole("button",{name:"自动查找商品图片"})).toBeEnabled();
});

it("只勾选一张已有素材时补另一张可用图片，跳过失效素材和网络检索", async () => {
  const liveAssets=assets.map(asset=>({...asset,url:"/asset",available:true}));
  const brief={...emptyBrief,productName:"大窑",assetIds:["front"]};
  vi.mocked(fetch).mockResolvedValueOnce(response({brief,candidates:[],assets}))
    .mockResolvedValueOnce(response([provider]))
    .mockImplementationOnce(async (_url,init)=>response({brief:{...JSON.parse(String(init?.body)).brief,revision:1}}))
    .mockResolvedValueOnce(response({prompt:"沿用已有图片",provider:provider.provider,model:provider.model}));
  render(<AigcCreator projectId="p1" guidedStep={0} mediaAssets={[{...liveAssets[0],id:"missing",available:false},...liveAssets] as never} />);
  await userEvent.click(await screen.findByRole("button",{name:"生成 5 条脚本"}));
  expect(await screen.findByText("沿用已有图片")).toBeVisible();
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body)).brief.assetIds).toEqual(["front","use"]);
  expect(screen.queryByLabelText("选择素材 missing")).not.toBeInTheDocument();
});

it("网络图片无法预览时标明失败并禁止选用", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({brief:{...emptyBrief,productName:"商品"},candidates:[],assets:[]}))
    .mockResolvedValueOnce(response([])).mockResolvedValueOnce(response({searchId:"s",productName:"商品",candidates:[{id:"bad",title:"失败图片",pageUrl:"https://brand.example",previewUrl:"/bad"}]}));
  render(<AigcCreator projectId="p1" guidedStep={0} />);
  await userEvent.click(await screen.findByRole("button",{name:"自动查找商品图片"}));
  const img=await screen.findByAltText("失败图片");
  await act(async()=>img.dispatchEvent(new Event("error")));
  expect(screen.getByLabelText("选择网络图片 失败图片")).toBeDisabled();
  expect(screen.getByText("图片无法读取，请选择其他图片")).toBeVisible();
});
