/* React adapter of Toonflow promptInput.vue at d86cce6b689916ab73f75bc4c1f42a2a7424516c.
 * MIT Copyright 2026 HBAI-Ltd; vendor/Toonflow/LICENSE.
 * Uses the identical x-sender 1.4.6 package (MIT, Copyright 2025-present jianfv).
 * Project persistence stays plain text with stable edge identities remapped by the canvas host.
 */
import { useEffect, useRef } from "react";
import xSender, { type AnyTagProps, type MentionItem } from "x-sender";
import "x-sender/lib/XSender.css";
import "./creativeCanvasPrompt.css";
type Reference = MentionItem & { value: string };
type Props = { value: string; label: string; references: Reference[]; onChange: (value: string) => void; onPreview: (id: string) => void };
function mountPrompt(element: HTMLElement, initial: Props) {
let props = initial;
const model: { value: AnyTagProps[][] } = { value: [] };
let sender: xSender | undefined;
let resetTask: Promise<void> | undefined;
let insertingReference = false;
let disposed = false;
let lastPublishedValue = props.value;
function previewReference(event: MouseEvent) {
  const tag = event.target instanceof Element ? event.target.closest<HTMLElement>(".imageReference[data-reference-id]") : null;
  const reference = props.references.find(item => item.id === tag?.dataset.referenceId);
  if (!reference?.avatar) return;
  event.preventDefault(); event.stopPropagation();
  sender?.bus.emit(xSender.EventSet.EVENT_COMMON_DIALOG_CLOSE);
  props.onPreview(reference.id);
}
function referenceHtml(reference: MentionItem & { value: string }) {
  const tag = document.createElement("span");
  tag.className = "imageReference";
  tag.dataset.referenceId = reference.id;
  tag.dataset.value = reference.value;
  if (reference.avatar) {
    const image = document.createElement("img");
    image.src = String(reference.avatar);
    image.alt = "";
    image.draggable = false;
    tag.append(image);
  }
  tag.append(document.createTextNode(reference.name));
  return tag.outerHTML;
}

function normalizeModel(value: AnyTagProps[][]): AnyTagProps[][] {
  if (!value.length) return [[{ type: "Write", text: "" }]];
  return value.map(line => {
    const tags = line.flatMap((tag): AnyTagProps[] => {
      if (tag.type === "Write") return tag.text.split(/(\{\{ref \d+\}\})/g).map(text => {
        const reference = props.references.find(item => item.value === text);
        return reference
          ? { type: "Custom", html: `<span style="display: inline-block;">${referenceHtml(reference)}</span>` }
          : { type: "Write", text };
      });
      if (tag.type !== "Custom") return [tag];
      const template = document.createElement("template");
      template.innerHTML = tag.html;
      const id = template.content.querySelector<HTMLElement>(".imageReference[data-reference-id]")?.dataset.referenceId;
      if (!id) return [{ type: "Write", text: template.content.textContent ?? "" }];
      const reference = props.references.find(item => item.id === id);
      if (!reference) return [];
      return [{ type: "Custom", html: `<span style="display: inline-block;">${referenceHtml(reference)}</span>` }];
    });
    const next: AnyTagProps[] = [{ type: "Write", text: "" }];
    for (const tag of tags) {
      const previous = next[next.length - 1];
      if (tag.type === "Write" && previous.type === "Write") previous.text += tag.text;
      else {
        if (tag.type !== "Write" && previous.type !== "Write") next.push({ type: "Write", text: "" });
        next.push(tag);
      }
    }
    if (next[next.length - 1].type !== "Write") next.push({ type: "Write", text: "" });
    return next;
  });
}

function releaseNodeFocus(instance: xSender) {
  // ACT: 1.4.6 的排队输入回调仍会聚焦旧 Write；仅让即将移除的节点失效，库支持取消回调后可移除。
  for (const grid of instance.chatEditor.NODES) {
    for (const node of grid.children) {
      if (node.type === "Write" || node.type === "Input") node.focus = () => {};
    }
  }
}

function publishModel(next: AnyTagProps[][]) {
  model.value = next;
  const promptText = getPromptText(next);
  lastPublishedValue = promptText;
  if (promptText !== props.value) props.onChange(promptText);
}

function syncModel(value = sender?.getModel() ?? model.value) {
  // 输入法组字时只同步文本，等 compositionend 后再替换参考标签，保留正在编辑的 DOM。
  const next = sender?.chatEditor.isComposition ? value : normalizeModel(value);
  if (sender && JSON.stringify(sender.getModel()) !== JSON.stringify(next)) {
    const instance = sender;
    const editor = instance.chatElement.richText;
    const selection = instance.getSelection();
    const focused = document.activeElement === editor;
    const endpoints = focused ? [selection.anchorNode, selection.focusNode].map((node, index) => {
      const gridIndex = instance.chatEditor.NODES.findIndex(grid => grid.$el.contains(node));
      const grid = instance.chatEditor.NODES[gridIndex];
      const childIndex = grid?.children.findIndex(child => child.$el.contains(node)) ?? -1;
      const child = grid?.children[childIndex];
      return {
        gridIndex, childIndex,
        text: child?.type === "Write" || child?.type === "Input" ? child.text : undefined,
        offset: index ? selection.focusOffset : selection.anchorOffset,
      };
    }) : [];
    releaseNodeFocus(instance);
    const task = instance.reset({ chatNode: next, clearHistory: false });
    resetTask = task;
    void task.finally(() => { if (resetTask === task) resetTask = undefined; });
    // ACT: reset 同步重建 DOM，但下一帧会强制移到末尾；在此恢复选区，并在 reset 完成前抑制该次聚焦。
    const lastGrid = instance.chatEditor.NODES[instance.chatEditor.NODES.length - 1];
    const last = lastGrid?.children[lastGrid.children.length - 1];
    if (last?.type === "Write") {
      Object.assign(instance.getCurrentNode(), { instance: last, node: last.$el.children[0].firstChild, offset: last.text.length || 1 });
    }
    const restored = endpoints.map(endpoint => {
      const target = instance.chatEditor.NODES[endpoint.gridIndex]?.children[endpoint.childIndex];
      if ((target?.type !== "Write" && target?.type !== "Input") || target.text !== endpoint.text) return;
      const node = target.type === "Write" ? target.$el.children[0].firstChild : target.$el.children[0].children[0].firstChild;
      if (node?.nodeType !== Node.TEXT_NODE || endpoint.offset > (node.textContent?.length ?? 0)) return;
      return { target, node, offset: endpoint.offset };
    });
    const [anchor, focus] = restored;
    if (anchor && focus) {
      focus.target.focus(focus.offset);
      selection.setBaseAndExtent(anchor.node, anchor.offset, focus.node, focus.offset);
    } else if (focused && last?.type === "Write") last.focus(-1);
  }
  publishModel(next);
}

function getPromptText(value: AnyTagProps[][]) {
  return value.map(line => line.map(tag => {
    if (tag.type === "Write") return tag.text;
    if (tag.type === "Input") return tag.text || tag.placeholder;
    if (tag.type !== "Custom") {
      const prefix = tag.type === "Mention" ? "@" : tag.type === "Trigger" ? tag.key : "";
      return prefix + tag.name;
    }
    const content = document.createElement("template");
    content.innerHTML = tag.html;
    for (const reference of content.content.querySelectorAll<HTMLElement>(".imageReference[data-value]")) {
      reference.replaceWith(reference.dataset.value ?? "");
    }
    return content.content.textContent ?? "";
  }).join("")).join("\n");
}

  const popup = document.createElement("div");
  popup.className = "textNodeMentions nodrag nopan nowheel";
  popup.style.zIndex = "1000";
  document.body.appendChild(popup);
  const initialModel: AnyTagProps[][] = props.value.split("\n").map(text => [{ type: "Write" as const, text }]);
  const instance = new xSender(element, {
    autoFocus: false,
    placeholder: "描述一下生成风格提示词，输入 @ 引用参考",
    chatStyle: { minHeight: "70px", maxHeight: "180px", fontSize: "16px", lineHeight: "1.6" },
    getPopupContainer: () => popup,
    mentionConfig: { dialogTitle: "选择参考", callEvery: false, options: props.references },
    keyboardSendFun: () => false,
    keyboardWrapFun: event => event.key === "Enter" && !event.isComposing,
  });
  sender = instance;
  const editor = instance.chatElement.richText;
  // ACT: 1.4.6 未检查卸载节点和空 Range 矩形；只适配当前实例，升级到库内修复后可移除。
  const chatEditor = instance.chatEditor as typeof instance.chatEditor & {
    focusFirst(): void;
    focusLast(): void;
    focusMark(): void;
    cursorView(): void;
    insertNodes(nodes: AnyTagProps[][]): Promise<void>;
  };
  for (const method of ["focusFirst", "focusLast", "focusMark"] as const) {
    const focus = chatEditor[method].bind(chatEditor);
    chatEditor[method] = () => {
      if (sender !== instance || !editor.isConnected || !editor.getClientRects().length) return;
      if (method === "focusLast" && resetTask) return;
      if (method === "focusMark" && !editor.contains(instance.getCurrentNode().node)) return chatEditor.focusLast();
      focus();
    };
  }
  const cursorView = chatEditor.cursorView.bind(chatEditor);
  chatEditor.cursorView = () => {
    const selection = instance.getSelection();
    if (sender !== instance || !editor.isConnected || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer) || !range.getClientRects().length || !editor.parentElement?.getClientRects().length) return;
    cursorView();
  };
  const insertNodes = chatEditor.insertNodes.bind(chatEditor);
  chatEditor.insertNodes = async nodes => {
    // 选区粘贴会先异步删除，再插入；关闭输入框后不再创建并聚焦新节点。
    if (sender === instance && editor.isConnected) await insertNodes(nodes);
  };
  editor.setAttribute("role", "textbox");
  editor.setAttribute("aria-label", props.label);
  editor.setAttribute("aria-multiline", "true");
  instance.bus.on("textPrompt", xSender.EventSet.EVENT_COMMON_CHANGE, () => { if (!disposed && !insertingReference) syncModel(); });
  syncModel(initialModel);
  // ACT: XSender 1.4.6 默认复制标签名称；复用库截取的选区模型，仅覆盖纯文本，保留富文本粘贴。
  const copyText = (event: ClipboardEvent) => {
    const copied = event.clipboardData?.getData("application/chat-nodes");
    if (!copied) return;
    try {
      // 剪贴板可能来自重排前的同一画布；先按当前参考身份重建 Custom，
      // 不让旧 chip 的展示名泄漏进持久化提示词。
      event.clipboardData?.setData("text/plain", getPromptText(normalizeModel(JSON.parse(copied))));
    } catch {
      // 库外的同名 MIME 不可信，保留浏览器原始 text/plain 行为。
    }
  };
  editor.addEventListener("copy", copyText);
  editor.addEventListener("cut", copyText);
  const selectReference = (event: MouseEvent) => {
    const item = (event.target as Element).closest<HTMLElement>(".chat-mention-dialog-item");
    if (!item) return;
    event.preventDefault();
    event.stopPropagation();
    const reference = props.references.find(reference => reference.id === item.dataset.id);
    if (reference) void insertReference(reference);
  };
  const closeOutside = (event: PointerEvent) => {
    if (!element.contains(event.target as Node) && !popup.contains(event.target as Node)) {
      instance.bus.emit(xSender.EventSet.EVENT_COMMON_DIALOG_CLOSE);
    }
  };
  // ACT: XSender 1.4.6 的候选默认插入 Mention；捕获选择事件改用公开 setHtml，库支持自定义渲染后可替换。
  popup.addEventListener("click", selectReference, true);
  document.addEventListener("pointerdown", closeOutside, true);
  const cleanup = () => {
    model.value = instance.getModel();
    releaseNodeFocus(instance);
    disposed = true;
    sender = undefined;
    editor.removeEventListener("copy", copyText);
    editor.removeEventListener("cut", copyText);
    document.removeEventListener("pointerdown", closeOutside, true);
    popup.removeEventListener("click", selectReference, true);
    // ACT: 1.4.6 的 destroy 会删除排队回调仍需用到的字段；先走公开清理事件，库修复后可直接 destroy。
    instance.bus.offKeyEvent("textPrompt");
    instance.bus.emit(xSender.EventSet.EVENT_COMMON_DIALOG_CLOSE);
    instance.updateConfig({ mentionConfig: { dialogTitle: "选择参考", callEvery: false, options: [] } });
    instance.bus.emit(xSender.EventSet.EVENT_COMMON_DESTROY);
    popup.remove();
    element.replaceChildren();
  };
async function insertReference(reference: MentionItem) {
  const instance = sender;
  if (!instance || insertingReference || disposed) return;
  instance.bus.emit(xSender.EventSet.EVENT_COMMON_DIALOG_CLOSE);
  insertingReference = true;
  try {
    // reset() 在库的下一帧会重置 markCursorWrite。等它结束后再读取 @ 的范围，
    // 否则 Enter 能删除 @，但 setHtml 会插入到已被 reset 丢弃的旧 Write。
    await resetTask?.catch(() => undefined);
    if (sender !== instance || disposed) return;
    const cursor = instance.getCurrentNode();
    const before = cursor.node.textContent?.slice(0, cursor.offset) ?? "";
    const start = before.lastIndexOf("@");
    if (start < 0) return;
    await instance.backspace(start - before.length);
    const current = props.references.find(item => item.id === reference.id);
    if (sender === instance && current && !disposed) {
      await instance.setHtml(referenceHtml(current));
      if (sender === instance && !disposed) publishModel(normalizeModel(instance.getModel()));
    }
  } finally {
    insertingReference = false;
  }
}

function handleMentionKey(event: KeyboardEvent) {
  if (!sender || event.isComposing || !["ArrowUp", "ArrowDown", "Enter", "Escape"].includes(event.key)) return;
  const dialog = sender.chatElement.dialogRoot.querySelector<HTMLElement>(".chat-mention-dialog-wrap");
  if (!dialog?.getClientRects().length) return;
  event.preventDefault();
  event.stopPropagation();
  if (event.key === "Escape") {
    sender.bus.emit(xSender.EventSet.EVENT_COMMON_DIALOG_CLOSE);
    return;
  }
  // ACT: 1.4.6 的候选键盘监听在 window，会被骨架截断；只适配当前实例，库支持局部键盘 API 后可替换。
  const items = Array.from(dialog.querySelectorAll<HTMLElement>(".chat-mention-dialog-item")).filter(item => item.getClientRects().length);
  if (!items.length) return;
  const active = Math.max(0, items.findIndex(item => item.classList.contains("active")));
  if (event.key === "Enter") {
    items[active]?.click();
    return;
  }
  const next = (active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
  items.forEach((item, index) => item.classList.toggle("active", index === next));
  items[next]?.scrollIntoView({ block: "nearest" });
}

  element.addEventListener("keydown", handleMentionKey, true);
  element.addEventListener("click", previewReference, true);
  // Accept only text and reference identities known to this editor; clipboard HTML is not executable content.
  const paste = (event: ClipboardEvent) => {
    event.preventDefault(); event.stopImmediatePropagation();
    const rich = event.clipboardData?.getData("application/chat-nodes");
    let value: AnyTagProps[][];
    try {
      const parsed: unknown = rich ? JSON.parse(rich) : undefined;
      if (!Array.isArray(parsed) || !parsed.every(line => Array.isArray(line))) throw new Error();
      value = parsed.map(line => line.flatMap((tag: unknown): AnyTagProps[] => {
        if (!tag || typeof tag !== "object") return [];
        const record = tag as Record<string, unknown>;
        if (record.type === "Write" && typeof record.text === "string") return [{ type:"Write", text: record.text }];
        if (record.type === "Custom" && typeof record.html === "string") {
          const template = document.createElement("template"); template.innerHTML = record.html;
          const id = template.content.querySelector<HTMLElement>("[data-reference-id]")?.dataset.referenceId;
          const reference = props.references.find(item => item.id === id);
          return reference ? [{ type:"Custom", html: referenceHtml(reference) }] : [];
        }
        return [];
      }));
    } catch { value = (event.clipboardData?.getData("text/plain") ?? "").split("\n").map(text => [{ type:"Write", text }]); }
    if (sender) void sender.setChatNode(value).then(() => { if (sender) syncModel(); });
  };
  element.addEventListener("paste", paste, true);
  return {
    update(next: Props) {
      const changedReferences = JSON.stringify(props.references) !== JSON.stringify(next.references);
      const externalValue = next.value !== lastPublishedValue;
      props = next;
      if (!sender || insertingReference) return;
      editor.setAttribute("aria-label", props.label);
      if (changedReferences) {
        const current = sender;
        current.bus.emit(xSender.EventSet.EVENT_COMMON_DIALOG_CLOSE);
        current.updateConfig({ mentionConfig: { dialogTitle:"选择参考", callEvery:false, options:props.references } });
        // 同源码：等本帧输入写回模型后才重绘标签，避免 @ 的匹配对话框被 React effect 抢先重置。
        void current.nextTick().then(() => { if (sender === current && !disposed && !insertingReference) syncModel(); });
      }
      if (externalValue && getPromptText(sender.getModel()) !== props.value) syncModel(props.value.split("\n").map(text => [{type:"Write",text}]));
    },
    destroy() { element.removeEventListener("keydown", handleMentionKey, true); element.removeEventListener("click", previewReference, true); element.removeEventListener("paste", paste, true); cleanup(); }
  };
}
export function CreativeCanvasPrompt(props: Props) {
  const element = useRef<HTMLDivElement>(null); const controller = useRef<ReturnType<typeof mountPrompt>>();
  const latest = useRef(props); latest.current = props;
  useEffect(() => { if (!element.current) return; controller.current = mountPrompt(element.current, latest.current); return () => { controller.current?.destroy(); controller.current = undefined; }; }, []);
  useEffect(() => controller.current?.update(props));
  return <div ref={element} className="creative-canvas-prompt nodrag nopan nowheel" />;
}
