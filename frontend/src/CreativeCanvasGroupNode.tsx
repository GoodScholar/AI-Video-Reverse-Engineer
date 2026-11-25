/* Adapted from Toonflow groupNode.vue at d86cce6 (MIT). */
import { NodeResizer, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";

type GroupData = {
  label: string;
  onRename: (id: string, label: string) => void;
  onResizeStart: (id: string) => void;
  onResize: (id: string, width: number, height: number, position?: { x: number; y: number }) => void;
};

export function CreativeCanvasGroupNode({ id, data, selected }: NodeProps) {
  const group = data as GroupData;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(group.label || "分组");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (!editing) setDraft(group.label || "分组"); }, [editing, group.label]);
  useEffect(() => { if (editing) input.current?.select(); }, [editing]);
  const saveLabel = () => {
    const label = draft.trim();
    if (label && label !== group.label) group.onRename(id, label);
    setEditing(false);
  };
  return <article className="creative-canvas__group-node" data-selected={selected || undefined}>
    <NodeResizer minWidth={120} minHeight={80} maxWidth={8192} maxHeight={8192} isVisible={selected} onResizeStart={() => group.onResizeStart(id)} onResizeEnd={(_event, params) => group.onResize(id, Math.min(8192, Math.max(120, params.width)), Math.min(8192, Math.max(80, params.height)), { x: params.x, y: params.y })} />
    {editing
      ? <input ref={input} className="creative-canvas__group-label-input nodrag nopan" aria-label="分组名称" value={draft} onChange={event => setDraft(event.target.value)} onPointerDown={event => event.stopPropagation()} onKeyDown={event => { if (event.nativeEvent.isComposing) return; if (event.key === "Enter") { event.preventDefault(); saveLabel(); } if (event.key === "Escape") { event.preventDefault(); setEditing(false); } }} onBlur={saveLabel} />
      : <span className="creative-canvas__group-label" tabIndex={0} title={`${group.label || "分组"}（双击编辑名称）`} onDoubleClick={event => { event.stopPropagation(); setEditing(true); }} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); setEditing(true); } }}>{group.label || "分组"}</span>}
  </article>;
}
