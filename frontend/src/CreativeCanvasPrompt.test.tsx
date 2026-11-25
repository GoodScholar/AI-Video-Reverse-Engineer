import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

type Tag = { type: "Write"; text: string } | { type: "Custom"; html: string };
let latestSender: MockSender | undefined;

class MockBus {
  listeners = new Map<string, Function>();
  on(key: string, event: string, callback: Function) { this.listeners.set(`${key}:${event}`, callback); }
  emit(event: string) { for (const [key, callback] of this.listeners) if (key.endsWith(`:${event}`)) callback(); }
  offKeyEvent(key: string) { for (const name of [...this.listeners.keys()]) if (name.startsWith(`${key}:`)) this.listeners.delete(name); }
}

function visible(element: HTMLElement) { Object.defineProperty(element, "getClientRects", { configurable: true, value: () => [{}] }); return element; }

class MockSender {
  static EventSet = { EVENT_COMMON_CHANGE: "change", EVENT_COMMON_DIALOG_CLOSE: "close", EVENT_COMMON_DESTROY: "destroy", EVENT_EDITOR_INSERT_CUSTOM: "custom" };
  static deferCustom = false;
  static pendingCustom: (() => void) | undefined;
  bus = new MockBus();
  model: Tag[][] = [[{ type: "Write", text: "" }]];
  chatElement: { richText: HTMLElement; dialogRoot: HTMLElement };
  chatEditor: any;
  current: any;
  constructor(container: HTMLElement, options: { getPopupContainer: () => HTMLElement }) {
    const richText = visible(document.createElement("div"));
    richText.setAttribute("contenteditable", "true");
    const dialogRoot = document.createElement("div");
    const dialog = visible(document.createElement("div"));
    dialog.className = "chat-mention-dialog-wrap";
    const item = visible(document.createElement("button"));
    item.className = "chat-mention-dialog-item active";
    item.dataset.id = "ref-a";
    dialog.append(item); dialogRoot.append(dialog); options.getPopupContainer().append(dialogRoot);
    container.append(richText);
    this.chatElement = { richText, dialogRoot };
    this.chatEditor = { isComposition: false, NODES: [], focusFirst() {}, focusLast() {}, focusMark() {}, cursorView() {}, insertNodes: async () => undefined };
    this.renderModel(); latestSender = this;
  }
  renderModel() {
    this.chatElement.richText.replaceChildren();
    const gridElement = document.createElement("div");
    const children = this.model[0].map(tag => {
      if (tag.type === "Custom") { const custom = document.createElement("span"); custom.innerHTML = tag.html; gridElement.append(custom); return { type: "Custom", html: tag.html, $el: custom, context: undefined }; }
      const wrap = document.createElement("span"); const content = document.createElement("span"); content.textContent = tag.text; wrap.append(content); gridElement.append(wrap);
      const write = { type: "Write", text: tag.text, $el: wrap, context: undefined as any, focus() {}, syncNode() {} }; return write;
    });
    const grid: any = { $el: gridElement, children };
    children.forEach(child => { child.context = grid; });
    this.chatElement.richText.append(gridElement);
    this.chatEditor.NODES = [grid];
    this.current = [...children].reverse().find(child => child.type === "Write") ?? children[0];
  }
  getModel() { return JSON.parse(JSON.stringify(this.model)) as Tag[][]; }
  getSelection() { return window.getSelection()!; }
  getCurrentNode() { const node = this.current?.$el?.children[0]?.firstChild ?? this.chatElement.richText.firstChild; return { instance: this.current, node, offset: this.current?.text?.length ?? 0 }; }
  updateConfig() {}
  nextTick() { return Promise.resolve(); }
  async reset(config: { chatNode: Tag[][] }) { this.model = JSON.parse(JSON.stringify(config.chatNode)); this.renderModel(); this.bus.emit("change"); }
  async backspace(length: number) { if (this.current?.type === "Write" && length < 0) { this.current.text = this.current.text.slice(0, length); this.model = [[{ type: "Write", text: this.current.text }]]; this.renderModel(); this.bus.emit("change"); } }
  async setHtml(html: string) {
    const apply = () => { this.model = [[{ type: "Write", text: "" }, { type: "Custom", html }, { type: "Write", text: "" }]]; this.renderModel(); this.bus.emit("change"); };
    if (MockSender.deferCustom) return new Promise<void>(resolve => { MockSender.pendingCustom = () => { apply(); resolve(); }; });
    apply();
  }
  async setChatNode(value: Tag[][]) { this.model = value; this.renderModel(); this.bus.emit("change"); }
  setPlain(text: string) { this.model = [[{ type: "Write", text }]]; this.renderModel(); this.bus.emit("change"); }
}

let CreativeCanvasPrompt: typeof import("./CreativeCanvasPrompt").CreativeCanvasPrompt;
beforeAll(async () => {
  vi.doMock("x-sender", () => ({ default: MockSender }));
  ({ CreativeCanvasPrompt } = await import("./CreativeCanvasPrompt"));
});

const reference = (value = "{{ref 1}}") => [{ id: "ref-a", name: "参考一", value, avatar: "/reference.png" }];
function Harness({ references = reference(), onChange = vi.fn() }: { references?: ReturnType<typeof reference>; onChange?: (value: string) => void }) {
  const [value, setValue] = useState("");
  return <CreativeCanvasPrompt value={value} label="提示词" references={references} onChange={next => { setValue(next); onChange(next); }} onPreview={vi.fn()} />;
}

afterEach(() => { MockSender.deferCustom = false; MockSender.pendingCustom = undefined; latestSender = undefined; vi.restoreAllMocks(); });

describe("CreativeCanvasPrompt", () => {
  it("Enter 选择参考会原子替换 @ 并发布稳定引用令牌", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    latestSender!.setPlain("@");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "提示词" }), { key: "Enter" });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("{{ref 1}}"));
    expect(latestSender!.getModel()[0].some(tag => tag.type === "Custom")).toBe(true);
  });

  it("参考重排会在下一帧重绘标签并发布新的稳定令牌", async () => {
    const onChange = vi.fn();
    const view = render(<Harness onChange={onChange} />);
    latestSender!.setPlain("@");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "提示词" }), { key: "Enter" });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("{{ref 1}}"));
    view.rerender(<Harness references={reference("{{ref 2}}")} onChange={onChange} />);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("{{ref 2}}"));
  });

  it("卸载期间完成的异步标签插入不会再发布或重建编辑器", async () => {
    const onChange = vi.fn();
    MockSender.deferCustom = true;
    const view = render(<Harness onChange={onChange} />);
    latestSender!.setPlain("@");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "提示词" }), { key: "Enter" });
    view.unmount();
    MockSender.pendingCustom?.();
    await Promise.resolve();
    expect(onChange).toHaveBeenLastCalledWith("@");
  });

  it("粘贴只接受纯文本或当前编辑器已知的参考身份", async () => {
    render(<Harness />);
    const paste = new Event("paste", { bubbles: true, cancelable: true }) as ClipboardEvent;
    Object.defineProperty(paste, "clipboardData", { value: { getData: (type: string) => type === "application/chat-nodes" ? JSON.stringify([[{ type: "Custom", html: '<span data-reference-id="foreign">注入</span>' }]]) : "安全文本" } });
    screen.getByRole("textbox", { name: "提示词" }).dispatchEvent(paste);
    await waitFor(() => expect(latestSender!.getModel()[0].some(tag => tag.type === "Custom")).toBe(false));
  });

  it("输入法组字期间不截获 Enter，组字结束后仍可选择参考", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    latestSender!.setPlain("@");
    latestSender!.chatEditor.isComposition = true;
    const composingEnter = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true, isComposing: true });
    screen.getByRole("textbox", { name: "提示词" }).dispatchEvent(composingEnter);
    expect(composingEnter.defaultPrevented).toBe(false);
    expect(latestSender!.getModel()[0].some(tag => tag.type === "Custom")).toBe(false);

    latestSender!.chatEditor.isComposition = false;
    latestSender!.bus.emit("change");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "提示词" }), { key: "Enter" });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("{{ref 1}}"));
  });

  it("粘贴当前画布参考会重建 chip，复制时保留稳定令牌文本", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const editor = screen.getByRole("textbox", { name: "提示词" });
    const rich = JSON.stringify([[
      { type: "Write", text: "海报 " },
      { type: "Custom", html: '<span class="imageReference" data-reference-id="ref-a">旧名称</span>' },
    ]]);
    const paste = new Event("paste", { bubbles: true, cancelable: true }) as ClipboardEvent;
    Object.defineProperty(paste, "clipboardData", { value: { getData: (type: string) => type === "application/chat-nodes" ? rich : "海报 参考一" } });
    editor.dispatchEvent(paste);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("海报 {{ref 1}}"));
    expect(latestSender!.getModel()[0].some(tag => tag.type === "Custom")).toBe(true);

    const copied = new Map<string, string>([["application/chat-nodes", rich]]);
    const copy = new Event("copy", { bubbles: true, cancelable: true }) as ClipboardEvent;
    Object.defineProperty(copy, "clipboardData", { value: { getData: (type: string) => copied.get(type) ?? "", setData: (type: string, value: string) => copied.set(type, value) } });
    editor.dispatchEvent(copy);
    expect(copied.get("text/plain")).toBe("海报 {{ref 1}}");
  });
});
