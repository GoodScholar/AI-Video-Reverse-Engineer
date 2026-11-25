import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", () => ({
  Handle: ({ children, type, id, className }: { children?: React.ReactNode; type: string; id: string; className?: string }) => <div data-testid={`handle-${type}-${id}`} className={className}>{children}</div>,
  NodeToolbar: ({ children, isVisible }: { children: React.ReactNode; isVisible?: boolean }) => isVisible ? <div>{children}</div> : null,
  Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
  useViewport: () => ({ x: 0, y: 0, zoom: 1 }),
}));
vi.mock("./CreativeCanvasPrompt", () => ({
  CreativeCanvasPrompt: ({ value, label, onChange }: { value: string; label: string; onChange: (value: string) => void }) => <textarea aria-label={label} value={value} onChange={event => onChange(event.target.value)} />,
}));

import { CanvasGenerateNode, CanvasMediaNode, CanvasTextNode, type CanvasNodeData } from "./CreativeCanvasNodes";

function mediaData(): CanvasNodeData {
  return {
    id: "media-1", kind: "media", position: { x: 0, y: 0 }, label: "商品图", modelLabel: "", modelOptions: [], selected: true,
    asset: { id: "asset-1", name: "商品.png", kind: "image", url: "/product.png" },
    onEdit: vi.fn(), onRun: vi.fn(), onCancel: vi.fn(), onOpenSettings: vi.fn(), onDelete: vi.fn(), onDuplicate: vi.fn(), onOpenInfo: vi.fn(),
    onPreview: vi.fn(), onDownload: vi.fn(), onReplace: vi.fn(), onBranch: vi.fn(), onCopyPrompt: vi.fn(), onReorderReferences: vi.fn(), onRemoveReference: vi.fn(),
  };
}
function mediaNodeProps(data: CanvasNodeData): React.ComponentProps<typeof CanvasMediaNode> {
  return { id: "media-1", data } as unknown as React.ComponentProps<typeof CanvasMediaNode>;
}
function textNodeProps(data: CanvasNodeData): React.ComponentProps<typeof CanvasTextNode> {
  return { id: "text-1", data } as unknown as React.ComponentProps<typeof CanvasTextNode>;
}
function generateNodeProps(data: CanvasNodeData): React.ComponentProps<typeof CanvasGenerateNode> {
  return { id: "generate-1", data } as unknown as React.ComponentProps<typeof CanvasGenerateNode>;
}

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
});
afterEach(() => vi.restoreAllMocks());

describe("CanvasMediaNode", () => {
  it("将媒体的常用操作置于 Toonflow 顶部工具栏", () => {
    render(<CanvasMediaNode {...mediaNodeProps(mediaData())} />);

    expect(screen.getByRole("button", { name: "替换图片" })).toBeVisible();
    expect(screen.getByRole("button", { name: "下载素材" })).toBeVisible();
    expect(screen.getByRole("button", { name: "全屏" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "节点信息" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建副本" })).not.toBeInTheDocument();
  });

  it("选中图片节点提供四角缩放和锁定比例切换，松手时只持久化一次", () => {
    const data = { ...mediaData(), width: 300, height: 200 };
    const view = render(<CanvasMediaNode {...mediaNodeProps(data)} />);
    const node = screen.getByRole("article", { name: "素材节点 商品图" });
    vi.spyOn(node, "getBoundingClientRect").mockReturnValue({ width: 300, height: 200 } as DOMRect);

    const handle = screen.getByRole("button", { name: "从右下角缩放图片" });
    expect(screen.getByRole("button", { name: "启用自由缩放" })).toBeVisible();
    fireEvent(handle, new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 100, clientY: 100 }));
    fireEvent(window, new MouseEvent("pointermove", { clientX: 160, clientY: 130 }));
    expect(data.onEdit).not.toHaveBeenCalled();
    fireEvent(window, new MouseEvent("pointerup", { clientX: 160, clientY: 130 }));
    expect(data.onEdit).toHaveBeenCalledTimes(1);
    expect(data.onEdit).toHaveBeenLastCalledWith("media-1", expect.objectContaining({ width: expect.any(Number), height: expect.any(Number), position: { x: 0, y: 0 } }));
    view.unmount();
  });

  it("尺寸测量导致父组件重渲染后仍在松手时提交缩放", () => {
    const data = { ...mediaData(), width: 300, height: 200 };
    const view = render(<CanvasMediaNode {...mediaNodeProps(data)} />);
    fireEvent(screen.getByRole("button", { name: "从右下角缩放图片" }), new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 100, clientY: 100 }));
    fireEvent(window, new MouseEvent("pointermove", { clientX: 160, clientY: 130 }));
    view.rerender(<CanvasMediaNode {...mediaNodeProps({ ...data })} />);
    fireEvent(window, new MouseEvent("pointerup", { clientX: 160, clientY: 130 }));
    expect(data.onEdit).toHaveBeenCalledTimes(1);
    expect(data.onEdit).toHaveBeenCalledWith("media-1", expect.objectContaining({ width: 360, height: 240 }));
  });

  it("缩放越过对角锚点时保持正尺寸和原比例", () => {
    const data = { ...mediaData(), width: 300, height: 200 };
    render(<CanvasMediaNode {...mediaNodeProps(data)} />);
    fireEvent(screen.getByRole("button", { name: "从右下角缩放图片" }), new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 300, clientY: 200 }));
    fireEvent(window, new MouseEvent("pointermove", { clientX: -500, clientY: -500 }));
    fireEvent(window, new MouseEvent("pointerup", { clientX: -500, clientY: -500 }));
    expect(data.onEdit).toHaveBeenCalledWith("media-1", expect.objectContaining({ width: 240, height: 160 }));
  });

  it("从反向角拖拽时固定右下锚点并提交修正后的位置", () => {
    const data = { ...mediaData(), width: 300, height: 200 };
    render(<CanvasMediaNode {...mediaNodeProps(data)} />);
    const node = screen.getByRole("article", { name: "素材节点 商品图" });
    vi.spyOn(node, "getBoundingClientRect").mockReturnValue({ width: 300, height: 200 } as DOMRect);

    fireEvent(screen.getByRole("button", { name: "从左上角缩放图片" }), new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 100, clientY: 100 }));
    fireEvent(window, new MouseEvent("pointermove", { clientX: 50, clientY: 90 }));
    fireEvent(window, new MouseEvent("pointerup", { clientX: 50, clientY: 90 }));

    const patch = (data.onEdit as ReturnType<typeof vi.fn>).mock.calls[0]?.[1];
    expect(patch.position.x + patch.width).toBeCloseTo(300);
    expect(patch.position.y + patch.height).toBeCloseTo(200);
  });

  it("卸载时取消进行中的缩放，不遗留全局监听或提交半成品", () => {
    const data = { ...mediaData(), width: 300, height: 200 };
    const view = render(<CanvasMediaNode {...mediaNodeProps(data)} />);
    const node = screen.getByRole("article", { name: "素材节点 商品图" });
    vi.spyOn(node, "getBoundingClientRect").mockReturnValue({ width: 300, height: 200 } as DOMRect);
    fireEvent(screen.getByRole("button", { name: "从右下角缩放图片" }), new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 10, clientY: 10 }));
    view.unmount();
    fireEvent(window, new MouseEvent("pointermove", { clientX: 50, clientY: 50 }));
    fireEvent(window, new MouseEvent("pointerup", { clientX: 50, clientY: 50 }));
    expect(data.onEdit).not.toHaveBeenCalled();
  });

  it("将全屏操作连接到现有图片预览回调", async () => {
    const data = mediaData();
    const user = userEvent.setup();
    render(<CanvasMediaNode {...mediaNodeProps(data)} />);

    await user.click(screen.getByRole("button", { name: "全屏" }));
    expect(data.onPreview).toHaveBeenCalledWith(data.asset);
  });

  it("视频和音频沿用 Toonflow 的替换、下载、全屏离线操作", async () => {
    const video = { ...mediaData(), asset: { id: "video-1", name: "商品.mp4", kind: "video" as const, url: "/product.mp4" } };
    const videoFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLVideoElement.prototype, "requestFullscreen", { configurable: true, value: videoFullscreen });
    vi.spyOn(HTMLVideoElement.prototype, "requestFullscreen").mockImplementation(videoFullscreen);
    const videoView = render(<CanvasMediaNode {...mediaNodeProps(video)} />);
    expect(screen.getByRole("button", { name: "替换视频" })).toBeVisible();
    expect(screen.getByRole("button", { name: "下载素材" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "全屏" }));
    expect(videoFullscreen).toHaveBeenCalled();
    videoView.unmount();

    const audio = { ...mediaData(), asset: { id: "audio-1", name: "旁白.mp3", kind: "audio" as const, url: "/voice.mp3" } };
    const audioFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLDivElement.prototype, "requestFullscreen", { configurable: true, value: audioFullscreen });
    vi.spyOn(HTMLDivElement.prototype, "requestFullscreen").mockImplementation(audioFullscreen);
    render(<CanvasMediaNode {...mediaNodeProps(audio)} />);
    expect(screen.getByLabelText("旁白.mp3")).toHaveAttribute("controls");
    expect(screen.getByRole("button", { name: "替换音频" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "全屏" }));
    expect(audioFullscreen).toHaveBeenCalled();
  });
});

describe("CanvasTextNode", () => {
  it("文本生成引用支持图片预览、排序和移除", async () => {
    const data: CanvasNodeData = { ...mediaData(), id: "text-1", kind: "text", text: " ", references: [
      { id: "ref-text", sourceLabel: "文案", text: "参考正文", dataType: "text" },
      { id: "ref-image", sourceLabel: "图片", asset: mediaData().asset, dataType: "image" },
    ], onRunText: vi.fn(), onCancelText: vi.fn(), onSelectTextVersion: vi.fn(), onInstallTextVersion: vi.fn() };
    render(<CanvasTextNode {...textNodeProps(data)} />);
    await userEvent.click(screen.getByRole("button", { name: "预览参考 2" }));
    expect(data.onPreview).toHaveBeenCalledWith(data.references?.[1].asset);
    fireEvent.keyDown(screen.getByLabelText("引用 1：参考正文").parentElement!, { key: "ArrowRight", altKey: true });
    expect(data.onReorderReferences).toHaveBeenCalledWith("text-1", ["ref-image", "ref-text"]);
    await userEvent.click(screen.getByRole("button", { name: "移除参考 1" }));
    expect(data.onRemoveReference).toHaveBeenCalledWith("ref-text");
  });

  it("下载保留文本文件名且卸载时释放 Blob URL，支持全屏编辑", async () => {
    const createUrl = vi.fn(() => "blob:text-download"); const revokeUrl = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }));
    const data = { ...mediaData(), id: "text-1", kind: "text" as const, label: "海报脚本", text: "中文内容", asset: undefined, selected: true };
    const view = render(<CanvasTextNode {...textNodeProps(data)} />);
    const link = screen.getByRole("link", { name: "下载文本" });
    expect(link).toHaveAttribute("download", "海报脚本.txt");
    expect(link).toHaveAttribute("href", "blob:text-download");
    await userEvent.click(screen.getByRole("button", { name: "全屏编辑文本" }));
    expect(screen.getByRole("dialog", { name: "编辑文本" })).toHaveClass("is-fullscreen");
    view.unmount(); expect(revokeUrl).toHaveBeenCalledWith("blob:text-download");
    vi.unstubAllGlobals();
  });

  it("用预览卡和受控文本对话框编辑内容", async () => {
    const data = { ...mediaData(), id: "text-1", kind: "text" as const, label: "文本", text: "夏日饮品", asset: undefined, selected: false };
    const user = userEvent.setup();
    render(<CanvasTextNode {...textNodeProps(data)} />);

    expect(screen.getByLabelText("文本内容")).toHaveTextContent("夏日饮品");
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.type(screen.getByRole("textbox", { name: "编辑文本内容" }), "，");
    expect(data.onEdit).toHaveBeenLastCalledWith("text-1", { text: "夏日饮品，" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "编辑文本" })).not.toBeInTheDocument();
  });

  it("保留文本输出并提供单一 in 输入和可撤销的字号编辑", async () => {
    const data = { ...mediaData(), id: "text-1", kind: "text" as const, label: "文本", text: "夏日饮品", asset: undefined, selected: true, fontSize: 16 };
    render(<CanvasTextNode {...textNodeProps(data)} />);

    expect(screen.getByTestId("handle-target-in")).toBeInTheDocument();
    expect(screen.getByTestId("handle-source-text")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "增大字号" }));
    expect(data.onEdit).toHaveBeenCalledWith("text-1", { fontSize: 18 });
  });
});

describe("CanvasGenerateNode", () => {
  it("模型切换只保存稳定身份，保留不兼容的历史尺寸直到用户明确改选", async () => {
    const onEdit = vi.fn(); const onRun = vi.fn();
    const data: CanvasNodeData = {
      ...mediaData(), id: "generate-1", kind: "generate", label: "海报", text: "商品静物", onEdit, onRun,
      params: { providerId: "ark", modelId: "seed-a", configurationVersion: "catalog-1", size: "1536x1024", ratio: "3:2" },
      imageProviders: [{ providerId: "ark", credentialState: "configured" }], configurationVersion: "catalog-1",
      imageModelCatalog: [
        { providerId: "ark", modelId: "seed-a", mediaType: "image", label: "Seed A", configured: true, capabilitySource: "user-configured", capabilityVersion: "catalog-1", capabilities: { imageSizes: ["1536x1024"], imageRatios: ["3:2"], maxReferences: 2 } },
        { providerId: "ark", modelId: "seed-b", mediaType: "image", label: "Seed B", configured: true, capabilitySource: "user-configured", capabilityVersion: "catalog-1", capabilities: { imageSizes: ["1024x1024"], imageRatios: ["1:1"], maxReferences: 2 } },
      ],
    };
    const view = render(<div className="creative-canvas"><CanvasGenerateNode {...generateNodeProps(data)} /></div>);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "图片模型" }), JSON.stringify(["ark", "seed-b"]));
    expect(onEdit).toHaveBeenCalledWith("generate-1", { params: { model: undefined, providerId: "ark", modelId: "seed-b", configurationVersion: "catalog-1" } });

    view.rerender(<div className="creative-canvas"><CanvasGenerateNode {...generateNodeProps({ ...data, params: { ...data.params, providerId: "ark", modelId: "seed-b" } })} /></div>);
    expect(screen.getByText("当前尺寸 1536x1024 不受所选模型支持，请明确选择兼容尺寸。")).toBeVisible();
    const generate = screen.getByRole("button", { name: "生成" });
    expect(generate).toBeDisabled();
    await userEvent.click(generate);
    expect(onRun).not.toHaveBeenCalled();
  });

  it("端口保留既有 ID，并按 Toonflow 的连接状态显示圆点或虚线圆", () => {
    const data: CanvasNodeData = { ...mediaData(), id: "generate-1", kind: "generate", text: "商品", connectedHandles: ["target:prompt", "source:image"] };
    render(<CanvasGenerateNode {...generateNodeProps(data)} />);
    expect(screen.getByTestId("handle-target-prompt")).toHaveClass("is-connected");
    expect(screen.getByTestId("handle-target-image")).not.toHaveClass("is-connected");
    expect(screen.getByTestId("handle-source-image")).toHaveClass("is-connected");
    expect(screen.getByTestId("handle-target-prompt").querySelector(".lucide-circle-dot")).toBeTruthy();
    expect(screen.getByTestId("handle-target-image").querySelector(".lucide-circle-dashed")).toBeTruthy();
  });

  it("缺失的已声明图片引用不会被误认为文本，并在生成前阻止提交", () => {
    const onRun = vi.fn();
    const data: CanvasNodeData = {
      ...mediaData(), id: "generate-1", kind: "generate", label: "海报", text: "商品静物", onRun,
      params: { providerId: "ark", modelId: "seed", configurationVersion: "catalog-1", size: "1024x1024", ratio: "1:1" },
      imageProviders: [{ providerId: "ark", credentialState: "configured" }], configurationVersion: "catalog-1",
      imageModelCatalog: [{ providerId: "ark", modelId: "seed", mediaType: "image", label: "Seed", configured: true, capabilitySource: "user-configured", capabilityVersion: "catalog-1", capabilities: { imageSizes: ["1024x1024"], imageRatios: ["1:1"], maxReferences: 1 } }],
      references: [{ id: "edge-image", sourceLabel: "商品主图", dataType: "image" }],
    };
    render(<div className="creative-canvas"><CanvasGenerateNode {...generateNodeProps(data)} /></div>);
    expect(screen.getByLabelText("引用 1：图片素材不可用")).toBeVisible();
    const generate = screen.getByRole("button", { name: "生成" });
    expect(generate).toBeDisabled();
    expect(generate).toHaveAttribute("title", "第 1 个图片参考（商品主图）不可用，请恢复素材或移除引用。");
    fireEvent.click(generate);
    expect(onRun).not.toHaveBeenCalled();
  });
});
