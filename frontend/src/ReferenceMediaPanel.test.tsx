import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import type { Project, ReferenceImage, ReferenceVideo } from "./models";
import { ReferenceMediaPanel } from "./ReferenceMediaPanel";

function stubDesktop(matches = true) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  let currentMatches = matches;
  const mediaQueryList = {
    get matches() { return currentMatches; },
    media: "",
    onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  };
  const matchMedia = vi.fn().mockImplementation((query: string) => {
    mediaQueryList.media = query;
    return mediaQueryList;
  });
  vi.stubGlobal("matchMedia", matchMedia);
  return {
    listeners,
    matchMedia,
    setMatches(next: boolean) {
      currentMatches = next;
      listeners.forEach((listener) => listener({ matches: next, media: mediaQueryList.media } as MediaQueryListEvent));
    },
  };
}

const image: ReferenceImage = {
  type: "image", id: "image-001", originalName: "hero.png", format: "png", sizeBytes: 1_024,
  width: 1200, height: 1600, hasTransparency: true,
};
const video: ReferenceVideo = {
  type: "video", id: "video-001", originalName: "clip.mp4", format: "mp4", sizeBytes: 11,
  durationSeconds: 2.5, width: 854, height: 480, frameRate: 24,
};
const emptyProject: Project = {
  id: "project-001", name: "雨夜人像复刻", createdAt: "2026-09-10T10:00:00+00:00",
  updatedAt: "2026-09-10T10:00:00+00:00", referenceMedia: null, localPreprocessing: null,
};

function fileList(file: File) {
  return { 0: file, length: 1, item: (index: number) => index === 0 ? file : null };
}

function fileWithSize(name: string, size: number) {
  const file = new File(["content"], name);
  Object.defineProperty(file, "size", { configurable: true, value: size });
  return file;
}

function selectFile(file: File) {
  fireEvent.change(screen.getByLabelText("参考素材文件"), { target: { files: fileList(file) } });
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason?: unknown) => void = () => undefined;
  const promise = new Promise<T>((nextResolve, nextReject) => { resolve = nextResolve; reject = nextReject; });
  return { promise, resolve, reject };
}

afterEach(() => vi.unstubAllGlobals());

it("展示图片类型化元数据、静态预览和透明区域处理说明", () => {
  stubDesktop();
  render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={vi.fn()} />);

  expect(screen.getByText("参考图片")).toBeVisible();
  expect(screen.getByText("1200×1600")).toBeVisible();
  expect(screen.getByText(/透明区域将在本地预处理时以白色背景处理/)).toBeVisible();
  expect(screen.getByRole("img", { name: "参考图片预览：hero.png" })).toHaveAttribute(
    "src",
    "/api/projects/project-001/reference-media/content?version=image-001",
  );
});

it("同项目图片替换后为新素材使用新的预览地址", async () => {
  stubDesktop();
  const replacement: ReferenceImage = { ...image, id: "image-002", originalName: "replacement.png" };
  const upload = vi.fn().mockResolvedValue({ ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: replacement });
  render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={upload} />);

  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["replacement"], "replacement.png", { type: "image/png" }));
  await userEvent.click(screen.getByRole("button", { name: "替换参考素材" }));

  expect(await screen.findByRole("img", { name: "参考图片预览：replacement.png" })).toHaveAttribute(
    "src",
    "/api/projects/project-001/reference-media/content?version=image-002",
  );
});

it("仅允许图片和视频扩展名，并按类型使用十进制体积边界", async () => {
  stubDesktop();
  const upload = vi.fn().mockResolvedValue({ ...emptyProject, referenceMedia: image });
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);
  const input = screen.getByLabelText("参考素材文件");

  fireEvent.change(input, { target: { files: fileList(new File(["x"], "clip.avi")) } });
  expect(await screen.findByRole("alert")).toHaveTextContent("仅支持 JPG、JPEG、PNG、WebP、MP4 或 MOV 格式的参考素材。");
  fireEvent.change(input, { target: { files: fileList(fileWithSize("hero.png", 30_000_001)) } });
  expect(await screen.findByRole("alert")).toHaveTextContent("参考图片不能超过 30 MB。");
  fireEvent.change(input, { target: { files: fileList(fileWithSize("clip.mp4", 200_000_001)) } });
  expect(await screen.findByRole("alert")).toHaveTextContent("参考视频不能超过 200 MB。");
  expect(upload).not.toHaveBeenCalled();
});

it("首次选择前保留视频分辨率边界说明", () => {
  stubDesktop();
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn()} />);

  expect(screen.getByText(/低分辨率可能影响分析细节/)).toBeVisible();
  expect(screen.getByText(/最高 UHD 4K/)).toBeVisible();
});

it("跨类型替换在页面内明确披露本地预处理及后续结果失效", async () => {
  stubDesktop();
  render(
    <ReferenceMediaPanel
      project={{ ...emptyProject, referenceMedia: video, localPreprocessing: { id: "pre-001" } as Project["localPreprocessing"] }}
      onProjectUpdated={vi.fn()}
      upload={vi.fn()}
      hasPreprocessingResult
    />,
  );

  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["image"], "hero.png", { type: "image/png" }));

  expect(screen.getByText(/从参考视频替换为参考图片/)).toBeVisible();
  expect(screen.getByText(/本地预处理及后续分析、方案、工作流结果会失效/)).toBeVisible();
  expect(screen.getByText(/从参考视频替换为参考图片/).closest(".reference-media-confirmation")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("视频继续显示既有元数据并通过统一入口上传", async () => {
  stubDesktop();
  const updated = { ...emptyProject, referenceMedia: video };
  const upload = vi.fn().mockResolvedValue(updated);
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);

  expect(screen.getByText(/MP4 或 MOV/)).toBeVisible();
  expect(screen.getByText(/2～300 秒/)).toBeVisible();
  expect(screen.getByText(/视频最大 200 MB/)).toBeVisible();
  expect(screen.getByText(/低分辨率可能影响分析细节/)).toBeVisible();
  expect(screen.getByText(/最高 UHD 4K/)).toBeVisible();
  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["video"], "clip.mp4", { type: "video/mp4" }));

  expect(upload).toHaveBeenCalledWith("project-001", expect.any(File));
  const successStatus = await screen.findByRole("status");
  expect(successStatus).toHaveTextContent("参考素材已通过校验");
  expect(successStatus.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByText("2.50 秒")).toBeVisible();
  expect(screen.getByText("24.00 fps")).toBeVisible();
  expect(screen.getByRole("heading", { name: "clip.mp4" })).toHaveFocus();
  expect(screen.getByText("clip.mp4").closest(".reference-media-summary")).toHaveClass("reference-media-summary--revealed");
});

it("上传期间锁定选择和拖放，且不会重复请求", async () => {
  stubDesktop();
  const pending = deferred<Project>();
  const upload = vi.fn().mockReturnValue(pending.promise);
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);
  const file = new File(["video"], "clip.mp4", { type: "video/mp4" });

  await userEvent.upload(screen.getByLabelText("参考素材文件"), file);
  expect(screen.getByLabelText("参考素材文件")).toBeDisabled();
  expect(screen.getByRole("button", { name: "正在上传并校验参考素材…" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("正在上传并校验参考素材");
  fireEvent.drop(screen.getByTestId("reference-media-dropzone"), { dataTransfer: { files: [file] } });
  expect(upload).toHaveBeenCalledTimes(1);
  pending.resolve({ ...emptyProject, referenceMedia: video });
  expect(await screen.findByText("clip.mp4")).toBeVisible();
});

it("预处理锁定时同时阻止输入、按钮和拖放上传", () => {
  stubDesktop();
  const upload = vi.fn();
  render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={upload} preprocessingLocked />);

  expect(screen.getByText("本地预处理或深度捕捉运行时不能更换参考素材")).toBeVisible();
  expect(screen.getByLabelText("参考素材文件")).toBeDisabled();
  expect(screen.getByRole("button", { name: "更换参考素材" })).toBeDisabled();
  fireEvent.drop(screen.getByTestId("reference-media-dropzone"), { dataTransfer: { files: [new File(["new"], "new.png")] } });
  expect(screen.queryByRole("button", { name: "替换参考素材" })).not.toBeInTheDocument();
  expect(upload).not.toHaveBeenCalled();
});

it("首次失败保留服务端文案、选择按钮焦点与非 Error 兜底", async () => {
  stubDesktop();
  const upload = vi.fn().mockRejectedValueOnce(new Error("服务端拒绝该素材")).mockRejectedValueOnce("连接异常");
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);

  selectFile(new File(["bad"], "bad.png"));
  const serverError = await screen.findByRole("alert");
  expect(serverError).toHaveTextContent("服务端拒绝该素材");
  expect(serverError.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByRole("button", { name: "选择参考素材" })).toHaveFocus();
  selectFile(new File(["fallback"], "fallback.png"));
  expect(await screen.findByRole("alert")).toHaveTextContent("无法上传并校验参考素材，请检查文件后重试。");
  expect(screen.getByRole("button", { name: "选择参考素材" })).toHaveFocus();
});

it("隐藏文件输入不进入 Tab 顺序，上传状态以文字和 aria-hidden 图标说明", async () => {
  stubDesktop();
  const pending = deferred<Project>();
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn().mockReturnValue(pending.promise)} />);

  selectFile(new File(["upload"], "upload.png"));
  expect(screen.getByLabelText("参考素材文件")).toHaveAttribute("tabindex", "-1");
  const status = screen.getByRole("status");
  expect(status).toHaveTextContent("正在上传并校验参考素材…");
  expect(status.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
});

it("替换失败保留旧素材并恢复更换按钮焦点", async () => {
  stubDesktop();
  render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={vi.fn().mockRejectedValue(new Error("图片无法读取"))} />);

  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["bad"], "bad.png"));
  await userEvent.click(screen.getByRole("button", { name: "替换参考素材" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("图片无法读取");
  expect(screen.getByText("hero.png")).toBeVisible();
  expect(screen.getByRole("button", { name: "更换参考素材" })).toHaveFocus();
});

it("取消替换不会上传并恢复更换按钮焦点", async () => {
  stubDesktop();
  const upload = vi.fn();
  render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={upload} />);

  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["next"], "next.png"));
  await userEvent.click(screen.getByRole("button", { name: "取消" }));

  expect(upload).not.toHaveBeenCalled();
  expect(screen.queryByText("next.png")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "更换参考素材" })).toHaveFocus();
});

it("拖放首个素材通过统一上传入口", async () => {
  stubDesktop();
  const upload = vi.fn().mockResolvedValue({ ...emptyProject, referenceMedia: video });
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);
  const file = new File(["video"], "clip.mp4", { type: "video/mp4" });

  fireEvent.drop(screen.getByTestId("reference-media-dropzone"), { dataTransfer: { files: [file] } });

  expect(upload).toHaveBeenCalledWith("project-001", file);
  expect(await screen.findByText("2.50 秒")).toBeVisible();
  expect(screen.getByText("854×480")).toBeVisible();
  expect(screen.getByText("24.00 fps")).toBeVisible();
  expect(screen.getByText("MP4")).toBeVisible();
  expect(screen.getByText("11 B")).toBeVisible();
});

it("在 1023px、1024px 与再次缩窄时切换入口并清理监听", () => {
  const desktop = stubDesktop(false);
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(desktop.matchMedia).toHaveBeenCalledWith("(min-width: 1024px)");
  expect(desktop.listeners.size).toBe(1);
  expect(screen.queryByLabelText("参考素材文件")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "选择参考素材" })).not.toBeInTheDocument();

  act(() => desktop.setMatches(true));
  expect(screen.getByLabelText("参考素材文件")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "选择参考素材" })).toBeVisible();
  expect(screen.getByTestId("reference-media-dropzone")).toBeVisible();
  act(() => desktop.setMatches(false));
  expect(screen.queryByLabelText("参考素材文件")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "选择参考素材" })).not.toBeInTheDocument();
  expect(screen.queryByTestId("reference-media-dropzone")).not.toBeInTheDocument();
  view.unmount();
  expect(desktop.listeners.size).toBe(0);
});

it("切换项目后忽略旧项目的成功响应", async () => {
  stubDesktop();
  const pending = deferred<Project>();
  const updated = vi.fn();
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={updated} upload={vi.fn().mockReturnValue(pending.promise)} />);
  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["old"], "old.png"));
  const projectB = { ...emptyProject, id: "project-002", name: "项目 B", updatedAt: "2026-09-11T10:00:00+00:00" };
  view.rerender(<ReferenceMediaPanel project={projectB} onProjectUpdated={updated} upload={vi.fn()} />);

  await act(async () => { pending.resolve({ ...emptyProject, referenceMedia: image }); });
  expect(updated).not.toHaveBeenCalled();
  expect(screen.queryByText("hero.png")).not.toBeInTheDocument();
});

it("卸载后忽略旧上传响应", async () => {
  stubDesktop();
  const pending = deferred<Project>();
  const updated = vi.fn();
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={updated} upload={vi.fn().mockReturnValue(pending.promise)} />);
  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["late"], "late.png"));
  view.unmount();

  await act(async () => { pending.resolve({ ...emptyProject, referenceMedia: image }); });
  expect(updated).not.toHaveBeenCalled();
});

it("延迟 prop 回显不会清除已接纳上传的成功状态", async () => {
  stubDesktop();
  const accepted = { ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: image };
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn().mockResolvedValue(accepted)} />);
  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["image"], "hero.png"));
  expect(await screen.findByText("参考素材已通过校验")).toBeVisible();

  view.rerender(<ReferenceMediaPanel project={accepted} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.getByText("参考素材已通过校验")).toBeVisible();
});

it("onProjectUpdated 同步 rerender 到项目 B 时只回调一次且不采纳 A 的成功状态", async () => {
  stubDesktop();
  const projectB = { ...emptyProject, id: "project-002", name: "项目 B", updatedAt: "2026-09-11T10:00:00+00:00" };
  const upload = vi.fn().mockResolvedValue({ ...emptyProject, referenceMedia: image });
  let view: ReturnType<typeof render>;
  const onProjectUpdated = vi.fn();
  onProjectUpdated.mockImplementation(() => {
    view.rerender(<ReferenceMediaPanel project={projectB} onProjectUpdated={onProjectUpdated} upload={upload} />);
  });
  view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={onProjectUpdated} upload={upload} />);
  selectFile(new File(["image"], "hero.png"));

  expect(await screen.findByRole("button", { name: "选择参考素材" })).toBeVisible();
  expect(screen.queryByText("hero.png")).not.toBeInTheDocument();
  expect(onProjectUpdated).toHaveBeenCalledTimes(1);
});

it("onProjectUpdated 同步 unmount 时只回调一次且没有 React 警告", async () => {
  stubDesktop();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const upload = vi.fn().mockResolvedValue({ ...emptyProject, referenceMedia: image });
  let view: ReturnType<typeof render>;
  const onProjectUpdated = vi.fn();
  onProjectUpdated.mockImplementation(() => view.unmount());
  view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={onProjectUpdated} upload={upload} />);
  selectFile(new File(["image"], "hero.png"));
  await act(async () => { await Promise.resolve(); });

  expect(onProjectUpdated).toHaveBeenCalledTimes(1);
  expect(consoleError).not.toHaveBeenCalled();
  consoleError.mockRestore();
});

it("updatedAt-only prop 变化保留当前替换错误", async () => {
  stubDesktop();
  const ready = { ...emptyProject, referenceMedia: image };
  const view = render(<ReferenceMediaPanel project={ready} onProjectUpdated={vi.fn()} upload={vi.fn().mockRejectedValue(new Error("上传失败"))} />);
  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["bad"], "bad.png"));
  await userEvent.click(screen.getByRole("button", { name: "替换参考素材" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("上传失败");

  view.rerender(<ReferenceMediaPanel project={{ ...ready, updatedAt: "2026-09-12T10:00:00+00:00" }} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.getByRole("alert")).toHaveTextContent("上传失败");
});

it("同类型替换在已有本地预处理结果时披露失效", async () => {
  stubDesktop();
  render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={vi.fn()} hasPreprocessingResult />);
  await userEvent.upload(screen.getByLabelText("参考素材文件"), new File(["new"], "new.png"));

  expect(screen.getByText("成功替换后，已有本地预处理结果将失效。")).toBeVisible();
});

it("拖放替换先确认，成功后才更新完整项目", async () => {
  stubDesktop();
  const updated = { ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: { ...image, id: "image-002", originalName: "new.png" } };
  const upload = vi.fn().mockResolvedValue(updated);
  const onProjectUpdated = vi.fn();
  render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={onProjectUpdated} upload={upload} />);
  const replacement = new File(["new"], "new.png");
  fireEvent.drop(screen.getByTestId("reference-media-dropzone"), { dataTransfer: { files: [replacement] } });
  expect(upload).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "替换参考素材" }));

  expect(await screen.findByText("new.png")).toBeVisible();
  expect(onProjectUpdated).toHaveBeenCalledWith(updated);
});

it("prop 素材变化时回归 ready 或 empty 且清除陈旧错误", async () => {
  stubDesktop();
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn().mockRejectedValue(new Error("旧错误"))} />);
  selectFile(new File(["bad"], "bad.png"));
  expect(await screen.findByRole("alert")).toHaveTextContent("旧错误");

  view.rerender(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.getByText("hero.png")).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  view.rerender(<ReferenceMediaPanel project={{ ...emptyProject, updatedAt: "2026-09-12T10:00:00+00:00" }} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.getByRole("button", { name: "选择参考素材" })).toBeVisible();
});

it("切到不匹配项目会清除已接纳素材，随后相同三元组仍可重新同步", async () => {
  stubDesktop();
  const accepted = { ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: image };
  const projectB = { ...emptyProject, id: "project-002", name: "项目 B", updatedAt: "2026-09-12T10:00:00+00:00" };
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn().mockResolvedValue(accepted)} />);
  selectFile(new File(["image"], "hero.png"));
  await screen.findByText("hero.png");

  view.rerender(<ReferenceMediaPanel project={projectB} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.queryByText("hero.png")).not.toBeInTheDocument();
  view.rerender(<ReferenceMediaPanel project={accepted} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.getByText("hero.png")).toBeVisible();
});

it("视频恰好 200 MB 时仍通过客户端边界校验", async () => {
  stubDesktop();
  const upload = vi.fn().mockResolvedValue({ ...emptyProject, referenceMedia: video });
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);

  selectFile(fileWithSize("clip.mp4", 200_000_000));

  expect(upload).toHaveBeenCalledWith("project-001", expect.any(File));
  expect(await screen.findByText("clip.mp4")).toBeVisible();
});

it("窄屏只显示素材或桌面提示，不提供上传入口", () => {
  stubDesktop(false);
  const view = render(<ReferenceMediaPanel project={{ ...emptyProject, referenceMedia: image }} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.getByText("hero.png")).toBeVisible();
  expect(screen.queryByLabelText("参考素材文件")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /选择|更换|替换/ })).not.toBeInTheDocument();
  view.rerender(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  expect(screen.getByText("请在宽度至少 1024px 的桌面设备添加参考素材")).toBeVisible();
});

it("A 的旧成功响应被忽略，B 的上传继续运行并最终接纳", async () => {
  stubDesktop();
  const pendingA = deferred<Project>();
  const pendingB = deferred<Project>();
  const projectB = { ...emptyProject, id: "project-002", name: "项目 B", updatedAt: "2026-09-11T10:00:00+00:00" };
  const updatedA = { ...emptyProject, referenceMedia: { ...image, originalName: "a.png" } };
  const updatedB = { ...projectB, referenceMedia: { ...image, id: "image-002", originalName: "b.png" } };
  const upload = vi.fn().mockReturnValueOnce(pendingA.promise).mockReturnValueOnce(pendingB.promise);
  const onProjectUpdated = vi.fn();
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={onProjectUpdated} upload={upload} />);
  const aFile = new File(["a"], "a.png");
  const bFile = new File(["b"], "b.png");
  selectFile(aFile);
  view.rerender(<ReferenceMediaPanel project={projectB} onProjectUpdated={onProjectUpdated} upload={upload} />);
  selectFile(bFile);
  expect(upload).toHaveBeenNthCalledWith(1, "project-001", aFile);
  expect(upload).toHaveBeenNthCalledWith(2, "project-002", bFile);
  await act(async () => { pendingA.resolve(updatedA); });
  expect(onProjectUpdated).not.toHaveBeenCalled();
  expect(screen.queryByText("a.png")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("正在上传并校验参考素材");
  await act(async () => { pendingB.resolve(updatedB); });
  expect(onProjectUpdated).toHaveBeenCalledTimes(1);
  expect(onProjectUpdated).toHaveBeenLastCalledWith(updatedB);
  expect(screen.getByText("b.png")).toBeVisible();
});

it("A 的旧失败响应被忽略", async () => {
  stubDesktop();
  const pendingA = deferred<Project>();
  const projectB = { ...emptyProject, id: "project-002", name: "项目 B", updatedAt: "2026-09-11T10:00:00+00:00" };
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={vi.fn().mockReturnValue(pendingA.promise)} />);
  selectFile(new File(["a"], "a.png"));
  view.rerender(<ReferenceMediaPanel project={projectB} onProjectUpdated={vi.fn()} upload={vi.fn()} />);
  await act(async () => { pendingA.reject(new Error("项目 A 旧错误")); });

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "选择参考素材" })).toBeVisible();
});

it("卸载后 resolve 与 reject 都被忽略且没有 React 警告", async () => {
  stubDesktop();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const resolved = deferred<Project>();
  const resolvedCallback = vi.fn();
  const resolvedView = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={resolvedCallback} upload={vi.fn().mockReturnValue(resolved.promise)} />);
  selectFile(new File(["resolve"], "resolve.png"));
  resolvedView.unmount();
  await act(async () => { resolved.resolve({ ...emptyProject, referenceMedia: image }); });
  const rejected = deferred<Project>();
  const rejectedCallback = vi.fn();
  const rejectedView = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={rejectedCallback} upload={vi.fn().mockReturnValue(rejected.promise)} />);
  selectFile(new File(["reject"], "reject.png"));
  rejectedView.unmount();
  await act(async () => { rejected.reject(new Error("卸载后的错误")); });

  expect(resolvedCallback).not.toHaveBeenCalled();
  expect(rejectedCallback).not.toHaveBeenCalled();
  expect(consoleError).not.toHaveBeenCalled();
  consoleError.mockRestore();
});

it("P1 延迟回显期间 P2 成功仍接纳 P2", async () => {
  stubDesktop();
  const p1 = deferred<Project>();
  const p2 = deferred<Project>();
  const updatedP1 = { ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: { ...image, originalName: "p1.png" } };
  const updatedP2 = { ...updatedP1, updatedAt: "2026-09-12T10:00:00+00:00", referenceMedia: { ...image, id: "image-002", originalName: "p2.png" } };
  const upload = vi.fn().mockReturnValueOnce(p1.promise).mockReturnValueOnce(p2.promise);
  const callback = vi.fn();
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={callback} upload={upload} />);
  selectFile(new File(["p1"], "p1.png"));
  await act(async () => { p1.resolve(updatedP1); });
  await screen.findByText("p1.png");
  selectFile(new File(["p2"], "p2.png"));
  await userEvent.click(screen.getByRole("button", { name: "替换参考素材" }));
  view.rerender(<ReferenceMediaPanel project={updatedP1} onProjectUpdated={callback} upload={upload} />);
  expect(screen.getByRole("status")).toHaveTextContent("正在上传并校验参考素材");
  await act(async () => { p2.resolve(updatedP2); });

  expect(callback).toHaveBeenCalledTimes(2);
  expect(callback).toHaveBeenLastCalledWith(updatedP2);
  expect(screen.getByText("p2.png")).toBeVisible();
  expect(screen.queryByText("正在上传并校验参考素材…")).not.toBeInTheDocument();
});

it("默认上传函数在网络失败后显示稳定中文错误，并可重试成功", async () => {
  stubDesktop();
  const updated = { ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: image };
  const fetchMock = vi.fn()
    .mockRejectedValueOnce(new TypeError("network failed"))
    .mockResolvedValueOnce(new Response(JSON.stringify(updated), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} />);

  selectFile(new File(["retry"], "retry.png", { type: "image/png" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("无法连接本地服务，请确认应用服务正在运行后重试。");
  expect(screen.getByRole("button", { name: "选择参考素材" })).toHaveFocus();
  selectFile(new File(["retry"], "retry.png", { type: "image/png" }));

  expect(await screen.findByText("参考素材已通过校验")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("P1 延迟回显期间 P2 失败保留 P1 并恢复更换焦点", async () => {
  stubDesktop();
  const p1 = deferred<Project>();
  const p2 = deferred<Project>();
  const updatedP1 = { ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceMedia: { ...image, originalName: "p1.png" } };
  const upload = vi.fn().mockReturnValueOnce(p1.promise).mockReturnValueOnce(p2.promise);
  const view = render(<ReferenceMediaPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);
  selectFile(new File(["p1"], "p1.png"));
  await act(async () => { p1.resolve(updatedP1); });
  await screen.findByText("p1.png");
  selectFile(new File(["p2"], "p2.png"));
  await userEvent.click(screen.getByRole("button", { name: "替换参考素材" }));
  view.rerender(<ReferenceMediaPanel project={updatedP1} onProjectUpdated={vi.fn()} upload={upload} />);
  await act(async () => { p2.reject(new Error("P2 校验失败")); });

  expect(await screen.findByRole("alert")).toHaveTextContent("P2 校验失败");
  expect(screen.getByText("p1.png")).toBeVisible();
  expect(screen.getByRole("button", { name: "更换参考素材" })).toHaveFocus();
});
