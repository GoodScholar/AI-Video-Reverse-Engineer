import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CharacterMotionPanel } from "./CharacterMotionPanel";
import { checkCharacterMotion, getCharacterMotion, refreshCharacterMotion, resolveCharacterMotion, saveCharacterMotion, uploadCharacterMotionImage } from "./characterMotionApi";
import type { CharacterMotionState } from "./characterMotionApi";
import type { Project } from "./models";

vi.mock("./characterMotionApi", () => ({
  checkCharacterMotion: vi.fn(), downloadCharacterMotionPackage: vi.fn(), getCharacterMotion: vi.fn(), refreshCharacterMotion: vi.fn(), resolveCharacterMotion: vi.fn(), saveCharacterMotion: vi.fn(), startCharacterMotion: vi.fn(), uploadCharacterMotionImage: vi.fn(),
}));

const project = (id: string, referenceId = "driver-1"): Project => ({ id, name: id, createdAt: "2026-09-16T00:00:00Z", updatedAt: "2026-09-16T00:00:00Z", referenceMedia: { id: referenceId, type: "video", originalName: `${referenceId}.mp4`, format: "mp4", sizeBytes: 1, durationSeconds: 1, width: 512, height: 512, frameRate: 24 }, localPreprocessing: null, semanticAnalysis: null });
const motion = (overrides: Partial<CharacterMotionState> = {}): CharacterMotionState => ({
  revision: 4, prompt: "walk", settings: { width: 512, height: 512, frames: 81, fps: 16, seed: 42 }, comfyUrl: "http://127.0.0.1:8188", character: { id: "character-1", originalName: "hero.png", width: 512, height: 512, sizeBytes: 1 }, driver: { id: "driver-1", originalName: "driver.mp4", width: 512, height: 512 }, sourceHash: "saved-source", stale: false, runs: [], template: { status: "candidate", requiredNodes: [], requiredModels: [] }, ...overrides,
});
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((next, fail) => { resolve = next; reject = fail; }); return { promise, resolve, reject }; };

beforeEach(() => {
  vi.clearAllMocks(); vi.useRealTimers();
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.mocked(getCharacterMotion).mockResolvedValue(motion());
});

describe("CharacterMotionPanel", () => {
  it("服务不可用时仍保留可编辑方案，且不把生成显示为完成", async () => {
    vi.mocked(getCharacterMotion).mockRejectedValue(new Error("无法连接本地服务"));
    render(<CharacterMotionPanel project={project("p1")} />);
    expect(await screen.findByText("本地服务未连接：仍可编辑方案。保存、环境检查和生成将在服务恢复后可用。")).toBeVisible();
    expect(screen.getByLabelText("动作提示词")).toBeVisible();
    expect(screen.getByRole("button", { name: "提交本地生成" })).toBeDisabled();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
  });

  it("迟到的旧项目上传不会覆盖新项目", async () => {
    const pending = deferred<CharacterMotionState>();
    vi.mocked(uploadCharacterMotionImage).mockReturnValue(pending.promise);
    vi.mocked(getCharacterMotion).mockImplementation(async (id) => motion({ prompt: id === "b" ? "B 的方案" : "A 的方案", driver: { id: `${id}-driver`, originalName: `${id}.mp4`, width: 512, height: 512 } }));
    const view = render(<CharacterMotionPanel project={project("a")} />);
    await screen.findByDisplayValue("A 的方案");
    fireEvent.change(screen.getByLabelText("角色图片"), { target: { files: [new File(["x"], "hero.png", { type: "image/png" })] } });
    view.rerender(<CharacterMotionPanel project={project("b")} />);
    await screen.findByDisplayValue("B 的方案");
    await act(async () => { pending.resolve(motion({ prompt: "旧上传", driver: { id: "a-driver", originalName: "a.mp4", width: 512, height: 512 } })); await pending.promise; });
    expect(screen.getByLabelText("动作提示词")).toHaveValue("B 的方案");
  });

  it("同项目更换参考视频后忽略旧请求", async () => {
    const first = deferred<CharacterMotionState>(); const second = deferred<CharacterMotionState>();
    vi.mocked(getCharacterMotion).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const view = render(<CharacterMotionPanel project={project("a", "driver-1")} />);
    view.rerender(<CharacterMotionPanel project={project("a", "driver-2")} />);
    await act(async () => { first.resolve(motion({ prompt: "旧驱动", driver: { id: "driver-1", originalName: "one.mp4", width: 512, height: 512 } })); await first.promise; });
    expect(screen.queryByDisplayValue("旧驱动")).not.toBeInTheDocument();
    await act(async () => { second.resolve(motion({ prompt: "新驱动", driver: { id: "driver-2", originalName: "two.mp4", width: 512, height: 512 } })); await second.promise; });
    expect(await screen.findByDisplayValue("新驱动")).toBeVisible();
  });

  it("上传后 sourceHash 为空且已过期的方案仍可重新保存", async () => {
    vi.mocked(getCharacterMotion).mockResolvedValue(motion({ sourceHash: null, stale: true }));
    render(<CharacterMotionPanel project={project("p1")} />);
    expect(await screen.findByRole("button", { name: "保存方案" })).toBeEnabled();
  });

  it("检查响应迟到时，编辑后的草稿不会显示旧的 ready", async () => {
    const pending = deferred<Awaited<ReturnType<typeof checkCharacterMotion>>>();
    vi.mocked(checkCharacterMotion).mockReturnValue(pending.promise);
    render(<CharacterMotionPanel project={project("p1")} />);
    await screen.findByDisplayValue("walk");
    fireEvent.click(screen.getByRole("button", { name: "检查 ComfyUI" }));
    fireEvent.change(screen.getByLabelText("动作提示词"), { target: { value: "run" } });
    await act(async () => { pending.resolve({ connected: true, ready: true, version: "x", missingNodes: [], missingModels: [], message: "环境已就绪" }); await pending.promise; });
    expect(screen.queryByText("环境已就绪")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交本地生成" })).toBeDisabled();
  });

  it("轮询首次失败后仍会继续刷新", async () => {
    vi.useFakeTimers();
    vi.mocked(getCharacterMotion).mockResolvedValue(motion({ runs: [{ id: "run-1", promptId: "p", status: "running", createdAt: "now", error: null, outputs: [], revision: 4 }] }));
    vi.mocked(refreshCharacterMotion).mockRejectedValueOnce(new Error("暂时失败")).mockResolvedValue(motion({ runs: [{ id: "run-1", promptId: "p", status: "running", createdAt: "now", error: null, outputs: [], revision: 4 }] }));
    render(<CharacterMotionPanel project={project("p1")} />);
    await act(async () => { await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(1500); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(1500); await Promise.resolve(); });
    expect(refreshCharacterMotion).toHaveBeenCalledTimes(2);
  });

  it("手动刷新运行状态时保留未保存草稿", async () => {
    vi.mocked(getCharacterMotion).mockResolvedValue(motion({ runs: [{ id: "run-1", promptId: "p", status: "running", createdAt: "now", error: null, outputs: [], revision: 4 }] }));
    vi.mocked(refreshCharacterMotion).mockResolvedValue(motion({ revision: 5, prompt: "服务端修改", runs: [{ id: "run-1", promptId: "p", status: "running", createdAt: "now", error: null, outputs: [], revision: 5 }] }));
    render(<CharacterMotionPanel project={project("p1")} />);
    await screen.findByDisplayValue("walk");
    fireEvent.change(screen.getByLabelText("动作提示词"), { target: { value: "未保存提示词" } });
    fireEvent.click(screen.getByRole("button", { name: "手动刷新" }));
    await waitFor(() => expect(refreshCharacterMotion).toHaveBeenCalledWith("p1", "run-1"));
    expect(screen.getByLabelText("动作提示词")).toHaveValue("未保存提示词");
  });

  it("手动刷新后保存未保存草稿仍使用草稿的旧 revision", async () => {
    vi.mocked(getCharacterMotion).mockResolvedValue(motion({ revision: 1, runs: [{ id: "run-1", promptId: "p", status: "running", createdAt: "now", error: null, outputs: [], revision: 1 }] }));
    vi.mocked(refreshCharacterMotion).mockResolvedValue(motion({ revision: 2, prompt: "服务端修改", runs: [{ id: "run-1", promptId: "p", status: "running", createdAt: "now", error: null, outputs: [], revision: 2 }] }));
    vi.mocked(saveCharacterMotion).mockResolvedValue(motion({ revision: 2, prompt: "未保存提示词" }));
    render(<CharacterMotionPanel project={project("p1")} />);
    await screen.findByDisplayValue("walk");
    fireEvent.change(screen.getByLabelText("动作提示词"), { target: { value: "未保存提示词" } });
    fireEvent.click(screen.getByRole("button", { name: "手动刷新" }));
    await waitFor(() => expect(refreshCharacterMotion).toHaveBeenCalledWith("p1", "run-1"));
    fireEvent.click(screen.getByRole("button", { name: "保存方案" }));
    await waitFor(() => expect(saveCharacterMotion).toHaveBeenCalledWith("p1", expect.objectContaining({ revision: 1, prompt: "未保存提示词" })));
  });

  it("继续跟踪未知任务时保留未保存草稿", async () => {
    vi.mocked(getCharacterMotion).mockResolvedValue(motion({ runs: [{ id: "run-1", promptId: null, status: "unknown", createdAt: "now", error: "断线", outputs: [], revision: 4 }] }));
    vi.mocked(resolveCharacterMotion).mockResolvedValue(motion({ revision: 5, prompt: "服务端修改", runs: [{ id: "run-1", promptId: "prompt-9", status: "running", createdAt: "now", error: null, outputs: [], revision: 5 }] }));
    render(<CharacterMotionPanel project={project("p1")} />);
    await screen.findByDisplayValue("walk");
    fireEvent.change(screen.getByLabelText("动作提示词"), { target: { value: "未保存提示词" } });
    fireEvent.change(screen.getByLabelText("ComfyUI prompt ID"), { target: { value: "prompt-9" } });
    fireEvent.click(screen.getByRole("button", { name: "继续跟踪" }));
    await waitFor(() => expect(resolveCharacterMotion).toHaveBeenCalledWith("p1", "run-1", { promptId: "prompt-9", confirmedNotQueued: false }));
    expect(screen.getByLabelText("动作提示词")).toHaveValue("未保存提示词");
  });

  it("未知任务要求 prompt ID 或确认未排队后才允许恢复", async () => {
    vi.mocked(getCharacterMotion).mockResolvedValue(motion({ runs: [{ id: "run-1", promptId: null, status: "unknown", createdAt: "now", error: "断线", outputs: [], revision: 4 }] }));
    vi.mocked(resolveCharacterMotion).mockResolvedValue(motion({ runs: [{ id: "run-1", promptId: null, status: "unknown", createdAt: "now", error: "断线", outputs: [], revision: 4 }] }));
    render(<CharacterMotionPanel project={project("p1")} />);
    const recover = await screen.findByRole("button", { name: "确认未排队并恢复提交" });
    expect(recover).toBeDisabled();
    fireEvent.change(screen.getByLabelText("ComfyUI prompt ID"), { target: { value: "prompt-9" } });
    fireEvent.click(screen.getByRole("button", { name: "继续跟踪" }));
    await waitFor(() => expect(resolveCharacterMotion).toHaveBeenCalledWith("p1", "run-1", { promptId: "prompt-9", confirmedNotQueued: false }));
    fireEvent.click(screen.getByLabelText("确认 ComfyUI 中未排队此任务"));
    fireEvent.click(recover);
    await waitFor(() => expect(resolveCharacterMotion).toHaveBeenLastCalledWith("p1", "run-1", { promptId: null, confirmedNotQueued: true }));
  });
});

it("前置模式保留动作方案和导出，关闭生成与外部轮询", async () => {
  render(<CharacterMotionPanel project={project("p1")} preparationOnly />);
  await screen.findByDisplayValue("walk");
  expect(screen.getByRole("button", { name: "导出离线包" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "提交本地生成" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "检查 ComfyUI" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("本地 ComfyUI 地址")).not.toBeInTheDocument();
  expect(checkCharacterMotion).not.toHaveBeenCalled();
  expect(refreshCharacterMotion).not.toHaveBeenCalled();
});
