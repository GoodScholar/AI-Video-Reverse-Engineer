import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Project, VideoLocalPreprocessing } from "./models";
import { ShotPreparationPanel } from "./ShotPreparationPanel";
import {
  applyToolkitTimeline,
  analyzePreparationShot,
  downloadShotPreparationPackage,
  extractShotPersonControl,
  getShotPreparation,
  restoreDetectedTimeline,
  saveShotPreparation,
  type ShotPreparationState,
} from "./shotPreparationApi";

vi.mock("./shotPreparationApi", () => ({
  getShotPreparation: vi.fn(), saveShotPreparation: vi.fn(), downloadShotPreparationPackage: vi.fn(), analyzePreparationShot: vi.fn(), extractShotPersonControl: vi.fn(), applyToolkitTimeline: vi.fn(), restoreDetectedTimeline: vi.fn(), shotRepresentativeFrameUrl: vi.fn(() => "/frame.jpg"),
}));

const mockedGet = vi.mocked(getShotPreparation);
const mockedSave = vi.mocked(saveShotPreparation);
const mockedDownload = vi.mocked(downloadShotPreparationPackage);
const mockedAnalyze = vi.mocked(analyzePreparationShot);
const mockedExtractPersonControl = vi.mocked(extractShotPersonControl);
const mockedApplyTimeline = vi.mocked(applyToolkitTimeline);
const mockedRestoreTimeline = vi.mocked(restoreDetectedTimeline);

function desktop() {
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
}

function project(id = "project-001"): Project {
  const localPreprocessing: VideoLocalPreprocessing = {
    id: `pre-${id}`, sourceReferenceMediaId: `video-${id}`, mediaType: "video", algorithmVersion: 1, status: "completed", currentStage: null, stages: [], queuedAt: "2026-09-15T10:00:00Z", startedAt: null, updatedAt: "2026-09-15T10:00:00Z", completedAt: "2026-09-15T10:00:00Z", proxySummary: null, reproducibilityAssessment: null, error: null,
  };
  return { id, name: "雨夜人像", createdAt: "2026-09-15T10:00:00Z", updatedAt: "2026-09-15T10:00:00Z", referenceMedia: { type: "video", id: `video-${id}`, originalName: "clip.mp4", format: "mp4", sizeBytes: 11, durationSeconds: 12, width: 854, height: 480, frameRate: 24 }, localPreprocessing };
}

function state(overrides: Partial<ShotPreparationState> = {}): ShotPreparationState {
  return {
    sourceId: "video-project-001", preprocessingId: "pre-project-001", revision: 3, canAnalyze: true,
    timelineOverride: null, toolkitScenes: [],
    shots: [{ id: "shot-01", startSeconds: 1, endSeconds: 4.2, representativeSeconds: 2.5, notes: "", prompts: { positiveZh: "", negativeZh: "", positiveEn: "", negativeEn: "" }, controls: { depth: "review_required", pose: "unavailable", mask: "unavailable" } }],
    ...overrides,
  };
}

beforeEach(desktop);
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe("ShotPreparationPanel", () => {
  it("加载中显示检片状态，随后展示控制素材的真实状态", async () => {
    let resolve: (value: ShotPreparationState) => void = () => undefined;
    mockedGet.mockReturnValue(new Promise<ShotPreparationState>((done) => { resolve = done; }));
    render(<ShotPreparationPanel project={project()} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在读取逐镜头准备数据");
    await act(async () => { resolve(state()); });
    expect(await screen.findByText("深度：需要人工确认")).toBeVisible();
    expect(screen.getByText("姿态：不可用")).toBeVisible();
  });

  it("读取失败显示原始错误并可重试", async () => {
    mockedGet.mockRejectedValueOnce(new Error("本地服务暂不可用")).mockResolvedValueOnce(state());
    render(<ShotPreparationPanel project={project()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("本地服务暂不可用");
    await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("heading", { name: "镜头 01" })).toBeVisible();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it("保存当前镜头草稿，保存前不能导出", async () => {
    mockedGet.mockResolvedValue(state());
    mockedSave.mockResolvedValue(state({ revision: 4, shots: [{ ...state().shots[0], notes: "镜头从左向右平移" }] }));
    render(<ShotPreparationPanel project={project()} />);
    const notes = await screen.findByLabelText("镜头备注");
    await userEvent.type(notes, "镜头从左向右平移");
    expect(screen.getByText("有未保存修改。保存后才可导出独立准备包。")).toBeVisible();
    expect(screen.getByRole("button", { name: "导出逐镜头准备包" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存逐镜头准备" }));
    expect(mockedSave).toHaveBeenCalledWith("project-001", expect.objectContaining({ revision: 3, sourceId: "video-project-001", preprocessingId: "pre-project-001", shots: [expect.objectContaining({ id: "shot-01", notes: "镜头从左向右平移" })] }));
    expect(screen.getByRole("button", { name: "导出逐镜头准备包" })).toBeEnabled();
  });

  it("选择当前参考视频已完成的视频工具分镜，并可恢复本地检测切点", async () => {
    const available = state({ toolkitScenes: [{ toolkitRunId: "scene-run-001", cutRevision: 2, cuts: [2, 6], createdAt: "2026-09-18T00:00:00Z", label: "视频工具分镜 · scene-ru · 3 镜头" }] });
    const applied = state({ revision: 4, timelineOverride: { toolkitRunId: "scene-run-001", cutRevision: 2 }, toolkitScenes: available.toolkitScenes });
    mockedGet.mockResolvedValue(available);
    mockedApplyTimeline.mockResolvedValue(applied);
    mockedRestoreTimeline.mockResolvedValue(state({ revision: 5 }));
    render(<ShotPreparationPanel project={project()} />);

    await userEvent.click(await screen.findByRole("button", { name: "应用选中切点" }));
    expect(mockedApplyTimeline).toHaveBeenCalledWith("project-001", {
      sourceId: "video-project-001", preprocessingId: "pre-project-001", revision: 3, toolkitRunId: "scene-run-001", cutRevision: 2,
    });
    expect(await screen.findByText("已应用视频工具分镜：视频工具分镜 · scene-ru · 3 镜头 · 切点版本 2")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "恢复本地检测切点" }));
    expect(mockedRestoreTimeline).toHaveBeenCalledWith("project-001", { sourceId: "video-project-001", preprocessingId: "pre-project-001", revision: 4 });
  });

  it("人物控制运行期间锁定时间线联动，避免旧轮询结果与新镜头范围交错", async () => {
    mockedGet.mockResolvedValue(state({
      toolkitScenes: [{ toolkitRunId: "scene-run-001", cutRevision: 0, cuts: [2], label: "视频工具分镜 · scene-ru · 2 镜头" }],
      personControlEnvironment: { ready: true, message: "本地模型已就绪" },
      shots: [{ ...state().shots[0], controls: { depth: "review_required", pose: "running", mask: "running" }, personControl: { status: "running", error: null, quality: null, runId: "person-run", outputs: {} } }],
    }));
    render(<ShotPreparationPanel project={project()} />);
    expect(await screen.findByRole("button", { name: "应用选中切点" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "刷新分镜结果" })).toBeDisabled();
  });

  it("项目切换时忽略旧项目的迟到读取结果", async () => {
    let resolveOld: (value: ShotPreparationState) => void = () => undefined;
    const old = new Promise<ShotPreparationState>((done) => { resolveOld = done; });
    mockedGet.mockImplementation((id) => id === "project-001" ? old : Promise.resolve(state({ sourceId: "video-project-002", preprocessingId: "pre-project-002", shots: [{ ...state().shots[0], notes: "新项目镜头" }] })));
    const view = render(<ShotPreparationPanel project={project()} />);
    view.rerender(<ShotPreparationPanel project={project("project-002")} />);
    expect(await screen.findByDisplayValue("新项目镜头")).toBeVisible();
    await act(async () => { resolveOld(state({ shots: [{ ...state().shots[0], notes: "旧项目镜头" }] })); });
    expect(screen.queryByDisplayValue("旧项目镜头")).not.toBeInTheDocument();
  });

  it("深度状态更新会刷新控制项，同时保留未保存的镜头草稿", async () => {
    mockedGet.mockResolvedValueOnce(state()).mockResolvedValueOnce(state({
      revision: 4,
      shots: [{ ...state().shots[0], notes: "服务端旧备注", controls: { depth: "available", pose: "unavailable", mask: "unavailable" } }],
    }));
    mockedSave.mockRejectedValue(new Error("版本已更新"));
    const view = render(<ShotPreparationPanel project={project()} />);
    const notes = await screen.findByLabelText("镜头备注");
    await userEvent.type(notes, "本地草稿");
    view.rerender(<ShotPreparationPanel project={{
      ...project(),
      activeDepthCaptureId: "depth-001",
      depthCaptures: [{ id: "depth-001", status: "completed", updatedAt: "2026-09-15T10:02:00Z", qualityAssessment: { status: "passed" } } as NonNullable<Project["depthCaptures"]>[number]],
    }} />);
    expect(await screen.findByText("深度：可用")).toBeVisible();
    expect(screen.getByDisplayValue("本地草稿")).toBeVisible();
    expect(mockedGet).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole("button", { name: "保存逐镜头准备" }));
    expect(mockedSave).toHaveBeenCalledWith("project-001", expect.objectContaining({ revision: 3 }));
    expect(await screen.findByRole("alert")).toHaveTextContent("版本已更新");
  });

  it("语义分析状态变化会重新读取可分析状态，且不覆盖本地草稿", async () => {
    mockedGet.mockResolvedValueOnce(state({ canAnalyze: false })).mockResolvedValueOnce(state({ canAnalyze: true }));
    const view = render(<ShotPreparationPanel project={project()} />);
    const notes = await screen.findByLabelText("镜头备注");
    await userEvent.type(notes, "保留本地备注");
    view.rerender(<ShotPreparationPanel project={{ ...project(), semanticAnalysis: { id: "analysis-001", status: "completed", updatedAt: "2026-09-15T10:03:00Z" } as Project["semanticAnalysis"] }} />);
    expect(await screen.findByRole("button", { name: "分析当前镜头并生成提示词" })).toBeEnabled();
    expect(screen.getByDisplayValue("保留本地备注")).toBeVisible();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it("导出调用独立包接口，分析必须由用户显式触发", async () => {
    mockedGet.mockResolvedValue(state());
    mockedDownload.mockResolvedValue(undefined);
    mockedAnalyze.mockResolvedValue(state({ revision: 4, shots: [{ ...state().shots[0], prompts: { positiveZh: "雨夜肖像", negativeZh: "", positiveEn: "rain portrait", negativeEn: "" } }] }));
    render(<ShotPreparationPanel project={project()} />);
    await screen.findByRole("heading", { name: "镜头 01" });
    await userEvent.click(screen.getByRole("button", { name: "导出逐镜头准备包" }));
    expect(mockedDownload).toHaveBeenCalledWith("project-001", expect.objectContaining({ revision: 3, sourceId: "video-project-001", preprocessingId: "pre-project-001" }));
    await userEvent.click(screen.getByRole("button", { name: "分析当前镜头并生成提示词" }));
    expect(mockedAnalyze).toHaveBeenCalledWith("project-001", "shot-01", expect.objectContaining({ revision: 3, disclosureAccepted: true }));
    expect(await screen.findByDisplayValue("雨夜肖像")).toBeVisible();
  });

  it("本地环境就绪时提交人物控制提取并显示运行状态", async () => {
    const queued = state({
      personControlEnvironment: { ready: true, message: "本地模型已就绪" },
      shots: [{ ...state().shots[0], controls: { depth: "review_required", pose: "running", mask: "running" }, personControl: { status: "queued", error: null, quality: null, runId: "run-01", outputs: {} } }],
    });
    mockedGet.mockResolvedValueOnce(state({ personControlEnvironment: { ready: true, message: "本地模型已就绪" } }));
    mockedExtractPersonControl.mockResolvedValue(queued);
    render(<ShotPreparationPanel project={project()} />);
    await act(async () => undefined);
    await act(async () => { screen.getByRole("button", { name: "提取当前镜头的人物控制素材" }).click(); });
    expect(mockedExtractPersonControl).toHaveBeenCalledWith("project-001", "shot-01", { revision: 3, sourceId: "video-project-001", preprocessingId: "pre-project-001" });
    expect(await screen.findByText("姿态：正在提取")).toBeVisible();
  });

  it("运行中的轮询刷新不会丢失草稿，并以草稿原 revision 保存", async () => {
    const running = state({
      personControlEnvironment: { ready: true, message: "本地模型已就绪" },
      shots: [{ ...state().shots[0], controls: { depth: "review_required", pose: "running", mask: "running" }, personControl: { status: "running", error: null, quality: null, runId: "run-01", outputs: {} } }],
    });
    const refreshed = state({
      revision: 4,
      personControlEnvironment: { ready: true, message: "本地模型已就绪" },
      shots: [{ ...running.shots[0], notes: "服务端旧备注", controls: { depth: "review_required", pose: "available", mask: "available" }, personControl: { status: "completed", error: null, quality: null, runId: "run-01", outputs: { pose: "/pose.mp4", mask: "/mask.mp4", overlay: "/overlay.mp4" } } }],
    });
    mockedGet.mockResolvedValueOnce(running).mockResolvedValueOnce(refreshed);
    mockedSave.mockResolvedValue(refreshed);
    render(<ShotPreparationPanel project={project()} />);
    const notes = await screen.findByLabelText("镜头备注");
    await userEvent.type(notes, "本地草稿");

    await new Promise<void>((resolve) => window.setTimeout(resolve, 1100));
    expect(await screen.findByText("姿态：可用")).toBeVisible();
    expect(screen.getByDisplayValue("本地草稿")).toBeVisible();
    await act(async () => { screen.getByRole("button", { name: "保存逐镜头准备" }).click(); });
    expect(mockedSave).toHaveBeenCalledWith("project-001", expect.objectContaining({ revision: 3, shots: [expect.objectContaining({ notes: "本地草稿" })] }));
  });

  it("相同时间的程序定位不会反复写入控制视频", async () => {
    const completed = state({
      shots: [{ ...state().shots[0], controls: { depth: "review_required", pose: "available", mask: "available" }, personControl: { status: "completed", error: null, quality: null, runId: "run-01", outputs: { pose: "/pose.mp4", mask: "/mask.mp4", overlay: "/overlay.mp4" } } }],
    });
    mockedGet.mockResolvedValue(completed);
    render(<ShotPreparationPanel project={project()} />);
    const reference = await screen.findByLabelText("参考视频定位") as HTMLVideoElement;
    const pose = screen.getByLabelText("33 点身体骨架") as HTMLVideoElement;
    let referenceTime = 2;
    let poseTime = 1;
    const poseWrite = vi.fn((value: number) => { poseTime = value; });
    Object.defineProperty(reference, "currentTime", { configurable: true, get: () => referenceTime, set: (value: number) => { referenceTime = value; } });
    Object.defineProperty(pose, "currentTime", { configurable: true, get: () => poseTime, set: poseWrite });

    await act(async () => { reference.dispatchEvent(new Event("seeking", { bubbles: true })); });
    expect(poseWrite).not.toHaveBeenCalled();
  });

  it("暂停控制视频会暂停参考视频", async () => {
    const completed = state({
      shots: [{ ...state().shots[0], controls: { depth: "review_required", pose: "available", mask: "available" }, personControl: { status: "completed", error: null, quality: null, runId: "run-01", outputs: { pose: "/pose.mp4", mask: "/mask.mp4", overlay: "/overlay.mp4" } } }],
    });
    mockedGet.mockResolvedValue(completed);
    render(<ShotPreparationPanel project={project()} />);
    const reference = await screen.findByLabelText("参考视频定位") as HTMLVideoElement;
    const pose = screen.getByLabelText("33 点身体骨架") as HTMLVideoElement;
    const pauseReference = vi.fn();
    Object.defineProperty(reference, "paused", { configurable: true, get: () => false });
    Object.defineProperty(reference, "pause", { configurable: true, value: pauseReference });
    Object.defineProperty(pose, "paused", { configurable: true, get: () => true });

    await act(async () => { pose.dispatchEvent(new Event("pause", { bubbles: true })); });
    expect(pauseReference).toHaveBeenCalledOnce();
  });
});
