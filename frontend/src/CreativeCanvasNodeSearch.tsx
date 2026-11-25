/* Adapted from Toonflow nodeSearch.vue at d86cce6b (MIT, HBAI-Ltd).
 * Vue Flow selection is supplied by the React canvas host. */
import { useEffect, useRef, useState } from "react";
import type { CreativeCanvasGroup, CreativeCanvasNode } from "./creativeCanvasApi";
import "./creativeCanvasNodeSearch.css";

export function CreativeCanvasNodeSearch({ nodes, groups = [], onSelect, onClose }: { nodes: CreativeCanvasNode[]; groups?: CreativeCanvasGroup[]; onSelect: (id: string) => void; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const names = { text: "文本", media: "素材", generate: "图片生成", group: "分组" };
  const results = [...nodes.map(node => ({ ...node, resultKind: node.kind })), ...groups.map(group => ({ ...group, resultKind: "group" as const }))]
    .filter(item => `${item.label ?? ""} ${names[item.resultKind]} ${item.resultKind} ${item.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) {
      if (typeof element.showModal === "function") element.showModal();
      else element.setAttribute("open", "");
    }
    return () => {
      if (!element) return;
      if (typeof element.close === "function") element.close();
      else element.removeAttribute("open");
    };
  }, []);
  useEffect(() => { dialog.current?.querySelector(`[data-result="${index}"]`)?.scrollIntoView?.({ block: "nearest" }); }, [index]);
  const select = (position: number) => { const item = results[position]; if (item) onSelect(item.id); };
  return <dialog ref={dialog} className="creative-canvas-search" aria-label="画布节点搜索" onCancel={event => { event.preventDefault(); onClose(); }} onKeyDown={event => event.stopPropagation()}>
    <header><strong>画布节点搜索</strong><button type="button" aria-label="关闭节点搜索" onClick={onClose}>×</button></header>
    <input autoFocus aria-label="搜索画布节点" placeholder="搜索节点名称或类型" value={query} aria-controls="canvas-search-results" aria-activedescendant={results[index] ? `canvas-search-result-${index}` : undefined} onChange={event => { setQuery(event.target.value); setIndex(0); }} onKeyDown={event => { if (event.nativeEvent.isComposing) return; if (event.key === "ArrowDown") { event.preventDefault(); setIndex(value => Math.min(value + 1, results.length - 1)); } else if (event.key === "ArrowUp") { event.preventDefault(); setIndex(value => Math.max(0, value - 1)); } else if (event.key === "Enter") { event.preventDefault(); select(index); } }} />
    <div id="canvas-search-results" role="listbox" aria-label="画布节点">{results.map((item, position) => <button key={item.id} id={`canvas-search-result-${position}`} data-result={position} role="option" aria-selected={position === index} tabIndex={-1} onMouseEnter={() => setIndex(position)} onClick={() => select(position)}><span>{item.label || "未命名分组"}</span><small>{names[item.resultKind]}</small></button>)}{!results.length && <p>没有匹配的节点</p>}</div>
  </dialog>;
}
