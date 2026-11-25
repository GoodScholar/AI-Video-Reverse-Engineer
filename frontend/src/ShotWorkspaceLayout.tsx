import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { Columns3, List, Network, PanelRightClose, PanelRightOpen, Redo2, Undo2, X } from "lucide-react";
import { useStore } from "zustand";

import { SceneShotList } from "./SceneShotList";
import type { PreproductionWorkspaceStore, WorkspaceViewMode } from "./preproductionWorkspaceStore";
import { WorkflowCanvas } from "./WorkflowCanvas";

type Props = {
  store: PreproductionWorkspaceStore;
  projectId?: string;
  children: ReactNode;
  toolbarActions?: ReactNode;
  onCreateShot?: (position?: { x: number; y: number }) => void;
};

const viewOptions: Array<{ mode: WorkspaceViewMode; label: string; icon: typeof Columns3 }> = [
  { mode: "split", label: "分屏视图", icon: Columns3 },
  { mode: "canvas", label: "画布视图", icon: Network },
  { mode: "list", label: "列表视图", icon: List },
];

export function ShotWorkspaceLayout({ store, projectId, children, onCreateShot, toolbarActions }: Props) {
  const inspectorToggleRef = useRef<HTMLButtonElement>(null);
  const inspectorTriggerRef = useRef<HTMLElement | null>(null);
  const mode = useStore(store, (state) => state.view.mode);
  const inspectorOpen = useStore(store, (state) => state.view.inspectorOpen);
  const organizationHistory = useStore(store, (state) => state.organizationHistory.history.length);
  const organizationFuture = useStore(store, (state) => state.organizationHistory.future.length);

  useEffect(() => {
    const narrow = window.matchMedia?.("(max-width: 899px)");
    if (narrow?.matches && store.getState().view.mode === "split") {
      store.getState().actions.setViewMode("list");
      store.getState().actions.setInspectorOpen(false);
    }
  }, [store]);

  useEffect(() => {
    if (!inspectorOpen) return;
    inspectorTriggerRef.current = document.activeElement instanceof HTMLElement && document.activeElement !== document.body
      ? document.activeElement
      : inspectorToggleRef.current;
  }, [inspectorOpen]);

  const closeInspector = useCallback(() => {
    const trigger = inspectorTriggerRef.current;
    store.getState().actions.setInspectorOpen(false);
    requestAnimationFrame(() => {
      (trigger?.isConnected ? trigger : inspectorToggleRef.current)?.focus();
    });
  }, [store]);

  return <section className={`shot-workspace shot-workspace--${mode}${inspectorOpen ? " has-inspector" : ""}`} data-view-mode={mode}>
    <header className="shot-workspace__toolbar">
      <div role="group" aria-label="镜头工作区视图">
        {viewOptions.map(({ mode: option, label, icon: Icon }) => <button
          key={option}
          type="button"
          aria-label={label}
          aria-pressed={mode === option}
          onClick={() => store.getState().actions.setViewMode(option)}
        ><Icon size={16} aria-hidden="true" /><span>{label.replace("视图", "")}</span></button>)}
      </div>
      <button
        ref={inspectorToggleRef}
        type="button"
        className="shot-workspace__inspector-toggle"
        aria-label={inspectorOpen ? "关闭检查器" : "打开检查器"}
        aria-expanded={inspectorOpen}
        onClick={() => store.getState().actions.setInspectorOpen(!inspectorOpen)}
      >{inspectorOpen ? <PanelRightClose size={17} aria-hidden="true" /> : <PanelRightOpen size={17} aria-hidden="true" />}<span>检查器</span></button>
      <div className="shot-workspace__organization-history" role="group" aria-label="镜头组织历史">
        <button type="button" aria-label="撤销镜头组织" disabled={!organizationHistory} onClick={() => store.getState().actions.undoOrganization()}><Undo2 size={16} aria-hidden="true" /></button>
        <button type="button" aria-label="重做镜头组织" disabled={!organizationFuture} onClick={() => store.getState().actions.redoOrganization()}><Redo2 size={16} aria-hidden="true" /></button>
      </div>
      {toolbarActions}
    </header>
    <div className="shot-workspace__body">
      {mode !== "canvas" && <SceneShotList store={store} onCreateShot={() => onCreateShot?.()} />}
      {mode !== "list" && <WorkflowCanvas projectId={projectId} store={store} onCreateShot={onCreateShot} />}
      {inspectorOpen && <>
        <button type="button" className="shot-workspace__backdrop" aria-label="收起检查器遮罩" onClick={closeInspector} />
        <aside className="shot-workspace__inspector" aria-label="工作区检查器" onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          event.stopPropagation();
          closeInspector();
        }}>
          <header><strong>检查器</strong><button type="button" className="icon-action" aria-label="收起检查器" onClick={closeInspector}><X size={17} aria-hidden="true" /></button></header>
          <div className="shot-workspace__inspector-content">{children}</div>
        </aside>
      </>}
    </div>
  </section>;
}
