import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Project } from "./models";
import { ReproductionPanel } from "./ReproductionPanel";
import {
  checkComfy,
  downloadReproductionPackage,
  generateReproductionPrompts,
  getReproduction,
  refreshReproductionRun,
  resolveReproductionRun,
  saveReproduction,
  startReproductionRun,
  type ReproductionState,
} from "./reproductionApi";

vi.mock("./reproductionApi", () => ({
  getReproduction: vi.fn(), generateReproductionPrompts: vi.fn(), saveReproduction: vi.fn(), checkComfy: vi.fn(), startReproductionRun: vi.fn(), refreshReproductionRun: vi.fn(), resolveReproductionRun: vi.fn(), downloadReproductionPackage: vi.fn(),
}));

const mockedGet = vi.mocked(getReproduction);
const mockedSave = vi.mocked(saveReproduction);
const mockedCheck = vi.mocked(checkComfy);
const mockedStart = vi.mocked(startReproductionRun);
const mockedRefresh = vi.mocked(refreshReproductionRun);
const mockedResolve = vi.mocked(resolveReproductionRun);
const mockedDownload = vi.mocked(downloadReproductionPackage);

function project(id = "project-001"): Project {
  return { id, name: "雨夜人像复刻", createdAt: "2026-09-12T10:00:00Z", updatedAt: "2026-09-12T10:00:00Z", referenceMedia: null, localPreprocessing: null, semanticAnalysis: { status: "completed" } as Project["semanticAnalysis"] };
}

function state(overrides: Partial<ReproductionState> = {}): ReproductionState {
  return {
    prompts: { positiveZh: "雨夜人像", negativeZh: "过曝", positiveEn: "portrait in rain", negativeEn: "overexposed" }, revision: 3, sourceHash: "hash-1", stale: false,
    settings: { strategy: "wan22_i2v", aspectMode: "smart", width: 832, height: 480, frames: 81, fps: 24, seed: 7 }, comfyUrl: "http://127.0.0.1:8188", runs: [], canGeneratePrompts: true, hasDepth: false, analysisReady: true, adjustments: [], templates: [{ strategy: "wan22_i2v", label: "Wan2.2 I2V", status: "candidate" }, { strategy: "wan22_fun_control", label: "Wan2.2 Fun Control", status: "candidate" }], ...overrides,
  };
}

function desktop() {
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
}

beforeEach(() => { desktop(); vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } }); });
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe("ReproductionPanel", () => {
  it("复刻默认智能比例并可选择具体比例保存", async () => {
    const initial = state();
    const saved = state({ revision: 4, settings: { ...initial.settings, aspectMode: "3:4", width: 720, height: 960 } });
    mockedGet.mockResolvedValue(initial);
    mockedSave.mockResolvedValue(saved);
    render(<ReproductionPanel project={project()} />);

    expect(await screen.findByRole("radio", { name: "智能" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "3:4" }));
    expect(screen.getByLabelText("宽度")).toHaveValue(720);
    expect(screen.getByLabelText("高度")).toHaveValue(960);
    await userEvent.click(screen.getByRole("button", { name: "保存复刻方案" }));
    expect(mockedSave).toHaveBeenCalledWith("project-001", expect.objectContaining({
      settings: expect.objectContaining({ aspectMode: "3:4", width: 720, height: 960 }),
    }));
  });
  it("手动分辨率变化后摘要显示真实输出尺寸", async () => {
    mockedGet.mockResolvedValue(state());
    render(<ReproductionPanel project={project()} />);

    await screen.findByText("实际输出 26:15 · 832×480");
    await userEvent.clear(screen.getByLabelText("宽度"));
    await userEvent.type(screen.getByLabelText("宽度"), "800");

    expect(screen.getByText("实际输出 5:3 · 800×480")).toBeVisible();
  });
  it("已有分析但配置缺失时禁用提示词生成并说明原因", async () => {
    mockedGet.mockResolvedValue(state());
    render(<ReproductionPanel project={project()} analysisProviders={[]} preparationOnly />);
    expect(await screen.findByRole("button", { name: "重新生成提示词" })).toBeDisabled();
    expect(screen.getByText(/分析服务未配置/)).toBeVisible();
    expect(generateReproductionPrompts).not.toHaveBeenCalled();
  });

  it("无分析时保留完整前置路径，且不提供生成提示词动作", async () => {
    mockedGet.mockResolvedValue(state({ analysisReady: false, prompts: null, canGeneratePrompts: false }));
    render(<ReproductionPanel project={{ ...project(), semanticAnalysis: null }} />);
    expect(screen.getByText("尚未具备生成前置")).toBeVisible();
    expect(screen.getByText(/上传参考素材 → 本地预处理 → 语义分析/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "生成提示词" })).not.toBeInTheDocument();
  });

  it("编辑后锁定导出和远程检查，保存携带当前 revision 并解除锁定", async () => {
    const initial = state();
    const saved = state({ revision: 4, settings: { ...initial.settings, frames: 97 } });
    mockedGet.mockResolvedValue(initial);
    mockedSave.mockResolvedValue(saved);
    render(<ReproductionPanel project={project()} />);
    await screen.findByDisplayValue("81");
    await userEvent.clear(screen.getByLabelText("帧数"));
    await userEvent.type(screen.getByLabelText("帧数"), "97");
    expect(screen.getByText("有未保存修改。保存前不能导出、检测或提交生成。")).toBeVisible();
    expect(screen.getByRole("button", { name: "导出复刻包" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存复刻方案" }));
    expect(mockedSave).toHaveBeenCalledWith("project-001", expect.objectContaining({ revision: 3, settings: expect.objectContaining({ frames: 97 }) }));
    expect(screen.getByRole("button", { name: "导出复刻包" })).toBeEnabled();
    mockedDownload.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole("button", { name: "导出复刻包" }));
    expect(mockedDownload).toHaveBeenCalledWith("project-001");
  });

  it("未手动检查连接时不允许提交，检查就绪后允许提交", async () => {
    mockedGet.mockResolvedValue(state());
    mockedCheck.mockResolvedValue({ connected: true, ready: true, version: "0.3", missingNodes: [], missingModels: [], message: "连接就绪" });
    mockedStart.mockResolvedValue(state({ runs: [{ id: "run-2", promptId: "p-2", status: "completed", createdAt: "2026-09-14T10:00:00Z", error: null, outputs: [{ filename: "result.mp4", url: "/result.mp4" }], revision: 3, width: 720, height: 960 }] }));
    render(<ReproductionPanel project={project()} />);
    await screen.findByText("中英文提示词");
    await userEvent.click(screen.getByText("可选：连接本地 ComfyUI 后继续实验"));
    expect(screen.getByRole("button", { name: "提交实验性生成" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "检查 ComfyUI" }));
    expect(await screen.findByText("连接就绪")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "提交实验性生成" }));
    expect(mockedStart).toHaveBeenCalledWith("project-001", 3);
    expect(await screen.findByLabelText("生成结果 result.mp4")).toHaveStyle({ aspectRatio: "720 / 960" });
  });

  it("项目切换时忽略旧项目的迟到读取响应", async () => {
    let resolveOld: (value: ReproductionState) => void = () => undefined;
    const oldResponse = new Promise<ReproductionState>((resolve) => { resolveOld = resolve; });
    mockedGet.mockImplementation((id) => id === "project-001" ? oldResponse : Promise.resolve(state({ revision: 8, prompts: { positiveZh: "新项目提示词", negativeZh: "", positiveEn: "new", negativeEn: "" } })));
    const view = render(<ReproductionPanel project={project()} />);
    view.rerender(<ReproductionPanel project={project("project-002")} />);
    expect(await screen.findByDisplayValue("新项目提示词")).toBeVisible();
    await act(async () => { resolveOld(state({ prompts: { positiveZh: "旧项目提示词", negativeZh: "", positiveEn: "old", negativeEn: "" } })); });
    expect(screen.queryByDisplayValue("旧项目提示词")).not.toBeInTheDocument();
  });

  it("项目切换后忽略旧保存响应，且不会让旧操作停留在界面上", async () => {
    let resolveSave: (value: ReproductionState) => void = () => undefined;
    mockedGet.mockImplementation((id) => Promise.resolve(state({ revision: id === "project-001" ? 3 : 9, prompts: { positiveZh: id === "project-001" ? "旧方案" : "新方案", negativeZh: "", positiveEn: "", negativeEn: "" } })));
    mockedSave.mockReturnValue(new Promise<ReproductionState>((resolve) => { resolveSave = resolve; }));
    const view = render(<ReproductionPanel project={project()} />);
    await screen.findByDisplayValue("旧方案");
    await userEvent.type(screen.getByDisplayValue("旧方案"), " 已编辑");
    await userEvent.click(screen.getByRole("button", { name: "保存复刻方案" }));
    view.rerender(<ReproductionPanel project={project("project-002")} />);
    expect(await screen.findByDisplayValue("新方案")).toBeVisible();
    await act(async () => { resolveSave(state({ prompts: { positiveZh: "迟到旧保存", negativeZh: "", positiveEn: "", negativeEn: "" } })); });
    expect(screen.queryByDisplayValue("迟到旧保存")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存复刻方案" })).not.toHaveTextContent("正在保存");
  });

  it("编辑地址会使已通过的连接检查失效，直到同一方案重新检查", async () => {
    mockedGet.mockResolvedValue(state());
    mockedCheck.mockResolvedValue({ connected: true, ready: true, version: "0.3", missingNodes: [], missingModels: [], message: "连接就绪" });
    render(<ReproductionPanel project={project()} />);
    await screen.findByText("中英文提示词");
    await userEvent.click(screen.getByText("可选：连接本地 ComfyUI 后继续实验"));
    await userEvent.click(screen.getByRole("button", { name: "检查 ComfyUI" }));
    expect(await screen.findByText("连接就绪")).toBeVisible();
    await userEvent.clear(screen.getByLabelText("本地 ComfyUI 地址"));
    await userEvent.type(screen.getByLabelText("本地 ComfyUI 地址"), "http://127.0.0.1:8190");
    expect(screen.queryByText("连接就绪")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交实验性生成" })).toBeDisabled();
  });

  it("参考素材身份变化时刷新方案状态，但保留未保存编辑", async () => {
    mockedGet.mockResolvedValueOnce(state()).mockResolvedValueOnce(state({ stale: true, prompts: { positiveZh: "服务端新建议", negativeZh: "", positiveEn: "", negativeEn: "" } }));
    const view = render(<ReproductionPanel project={project()} />);
    const prompt = await screen.findByDisplayValue("雨夜人像");
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "保留的本地编辑");
    view.rerender(<ReproductionPanel project={{ ...project(), referenceMedia: { id: "media-002" } as Project["referenceMedia"] }} />);
    expect(await screen.findByText(/参考素材或分析已更新/)).toBeVisible();
    expect(screen.getByDisplayValue("保留的本地编辑")).toBeVisible();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it("复制不可用时给出明确反馈，而不是报告成功", async () => {
    vi.stubGlobal("navigator", {});
    mockedGet.mockResolvedValue(state());
    render(<ReproductionPanel project={project()} />);
    await screen.findByText("中英文提示词");
    await userEvent.click(screen.getAllByRole("button", { name: "复制" })[0]);
    expect(await screen.findByText("当前浏览器不支持剪贴板写入。")).toBeVisible();
  });

  it("未知运行只在异常记录中提供人工恢复，并要求 prompt ID 或明确确认", async () => {
    const unknown = state({ runs: [{ id: "run-unknown", promptId: null, status: "unknown", createdAt: "2026-09-14T10:00:00Z", error: "连接中断", outputs: [], revision: 3 }] });
    mockedGet.mockResolvedValue(unknown);
    mockedResolve.mockResolvedValue(state());
    render(<ReproductionPanel project={project()} />);
    expect(await screen.findByText(/无法确认这次请求是否进入/)).toBeVisible();
    expect(screen.getByRole("button", { name: "继续跟踪" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认未排队并恢复提交" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText("我已在 ComfyUI 确认未排队，允许重新提交"));
    await userEvent.click(screen.getByRole("button", { name: "确认未排队并恢复提交" }));
    expect(mockedResolve).toHaveBeenCalledWith("project-001", "run-unknown", { promptId: null, confirmedNotQueued: true });
  });

  it("分析前置失效时保留草稿但暂停生成相关操作", async () => {
    mockedGet.mockResolvedValue(state());
    const view = render(<ReproductionPanel project={project()} />);
    await screen.findByText("中英文提示词");
    view.rerender(<ReproductionPanel project={{ ...project(), semanticAnalysis: null }} />);
    expect(await screen.findByText(/参考素材或分析已更新/)).toBeVisible();
    expect(screen.getByRole("button", { name: "导出复刻包" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新生成提示词" })).toBeDisabled();
  });

  it("提交失败后重新读取持久化运行，以显示未知状态恢复入口", async () => {
    const unknown = state({ runs: [{ id: "run-after-failure", promptId: null, status: "unknown", createdAt: "2026-09-14T10:00:00Z", error: "连接中断", outputs: [], revision: 3 }] });
    mockedGet.mockResolvedValueOnce(state()).mockResolvedValueOnce(unknown);
    mockedCheck.mockResolvedValue({ connected: true, ready: true, version: "0.3", missingNodes: [], missingModels: [], message: "连接就绪" });
    mockedStart.mockRejectedValue(new Error("提交连接中断"));
    render(<ReproductionPanel project={project()} />);
    await screen.findByText("中英文提示词");
    await userEvent.click(screen.getByText("可选：连接本地 ComfyUI 后继续实验"));
    await userEvent.click(screen.getByRole("button", { name: "检查 ComfyUI" }));
    await userEvent.click(screen.getByRole("button", { name: "提交实验性生成" }));
    expect(await screen.findByText(/无法确认这次请求是否进入/)).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("提交连接中断");
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it("完成检查后恢复运行轮询，而不会被检查的 mutation token 停止", async () => {
    vi.useFakeTimers();
    const running = state({ runs: [{ id: "run-poll", promptId: "p-1", status: "running", createdAt: "2026-09-14T10:00:00Z", error: null, outputs: [], revision: 3 }] });
    mockedGet.mockResolvedValue(running);
    mockedCheck.mockResolvedValue({ connected: true, ready: true, version: "0.3", missingNodes: [], missingModels: [], message: "连接就绪" });
    mockedRefresh.mockResolvedValue(state({ runs: [{ id: "run-poll", promptId: "p-1", status: "completed", createdAt: "2026-09-14T10:00:00Z", error: null, outputs: [], revision: 3 }] }));
    render(<ReproductionPanel project={project()} />);
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByRole("button", { name: "检查 ComfyUI" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(mockedRefresh).toHaveBeenCalledWith("project-001", "run-poll");
    vi.useRealTimers();
  });

  it("运行中的任务轮询刷新后展示新的已完成结果", async () => {
    vi.useFakeTimers();
    mockedGet.mockResolvedValue(state({ runs: [{ id: "run-1", promptId: "p-1", status: "running", createdAt: "2026-09-14T10:00:00Z", error: null, outputs: [], revision: 3 }] }));
    mockedRefresh.mockResolvedValue(state({ runs: [{ id: "run-1", promptId: "p-1", status: "completed", createdAt: "2026-09-14T10:00:00Z", error: null, outputs: [{ filename: "fresh.mp4", url: "/fresh.mp4" }], revision: 3 }] }));
    render(<ReproductionPanel project={project()} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("生成中")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(mockedRefresh).toHaveBeenCalledWith("project-001", "run-1");
    expect(screen.getByLabelText("生成结果 fresh.mp4")).toBeVisible();
    vi.useRealTimers();
  });
});

it("前置模式只保留提示词与离线工作流，不显示最终生成入口", async () => {
  mockedGet.mockResolvedValue(state());
  render(<ReproductionPanel project={project()} preparationOnly />);
  expect(await screen.findByRole("button", { name: "导出复刻包" })).toBeEnabled();
  expect(screen.getByRole("heading", { name: "提示词与工作流准备" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "提交实验性生成" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "检查 ComfyUI" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("本地 ComfyUI 地址")).not.toBeInTheDocument();
  expect(mockedCheck).not.toHaveBeenCalled();
  expect(mockedStart).not.toHaveBeenCalled();
});
