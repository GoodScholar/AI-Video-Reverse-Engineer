import { useEffect, useMemo, useState, type MouseEvent } from "react";
import { measureElement, observeElementRect, useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronRight, Copy, FolderOpen, Image, LocateFixed, Plus } from "lucide-react";
import { useStore } from "zustand";

import type { PreproductionWorkspaceStore } from "./preproductionWorkspaceStore";
import { getShotPreparationStatus } from "./shotPreparationStatus";

type Props = { store: PreproductionWorkspaceStore; onCreateShot?: () => void };
type ListRow = { type: "scene"; id: string } | { type: "shot"; id: string };

function SceneRow({ store, sceneId }: Props & { sceneId: string }) {
  const scene = useStore(store, (state) => state.entities.scenesById[sceneId]);
  const shotIds = useStore(store, (state) => state.order.shotIdsByScene[sceneId] ?? []);
  const expanded = useStore(store, (state) => state.view.expandedSceneIds.has(sceneId));
  const selected = useStore(store, (state) => state.selection.primaryEntity?.type === "scene" && state.selection.primaryEntity.id === sceneId);
  const hovered = useStore(store, (state) => state.selection.hoveredEntity?.type === "scene" && state.selection.hoveredEntity.id === sceneId);
  const focused = useStore(store, (state) => state.selection.focusedEntity?.type === "scene" && state.selection.focusedEntity.id === sceneId);
  const totalDuration = useStore(store, (state) => shotIds.reduce((total, id) => total + (state.entities.shotsById[id]?.duration ?? 0), 0));
  if (!scene) return null;
  return <header className={`scene-shot-list__scene${selected ? " is-selected" : ""}${hovered ? " is-hovered" : ""}${focused ? " is-focused" : ""}`}>
    <button type="button" aria-expanded={expanded} aria-pressed={selected} aria-label={`${expanded ? "折叠" : "展开"}场景${scene.title}`}
      onMouseEnter={() => store.getState().actions.setHoveredEntity({ type: "scene", id: sceneId })}
      onMouseLeave={() => store.getState().actions.setHoveredEntity(null)}
      onFocus={() => store.getState().actions.setFocusedEntity({ type: "scene", id: sceneId })}
      onBlur={() => store.getState().actions.setFocusedEntity(null)}
      onClick={() => { store.getState().actions.toggleScene(sceneId); store.getState().actions.selectScene(sceneId, "list"); }}>
      {expanded ? <ChevronDown size={17} aria-hidden="true" /> : <ChevronRight size={17} aria-hidden="true" />}
      <span><h3>{scene.title}</h3><small>{shotIds.length} 个镜头 · {totalDuration} 秒</small></span>
    </button>
  </header>;
}

function ShotCard({ store, shotId }: Props & { shotId: string }) {
  const shot = useStore(store, (state) => state.entities.shotsById[shotId]);
  const selected = useStore(store, (state) => state.selection.selectedShotIds.has(shotId));
  const context = useStore(store, (state) => state.selection.primaryEntity?.type === "processNode"
    && state.selection.primaryEntity.shotId === shotId);
  const hovered = useStore(store, (state) => state.selection.hoveredEntity?.type === "shot" && state.selection.hoveredEntity.id === shotId);
  const focused = useStore(store, (state) => state.selection.focusedEntity?.type === "shot" && state.selection.focusedEntity.id === shotId);
  const thumbnail = useStore(store, (state) => {
    const currentShot = state.entities.shotsById[shotId];
    return state.assets.find((asset) => asset.kind === "image" && currentShot?.assetIds.includes(asset.id));
  });
  const firstIssue = useStore(store, (state) => state.checks.find((check) => check.shotId === shotId));
  const issueCount = useStore(store, (state) => state.checks.reduce((count, check) => count + Number(check.shotId === shotId), 0));
  const position = useStore(store, (state) => state.order.sceneIds.flatMap((sceneId) => state.order.shotIdsByScene[sceneId] ?? []).indexOf(shotId) + 1);
  if (!shot) return null;
  const candidateCount = shot.resultVersions?.length ?? (shot.resultAssetId ? 1 : 0);
  const status = getShotPreparationStatus(shot);
  const select = (event: MouseEvent<HTMLButtonElement>) => store.getState().actions.selectShot(shotId, {
    mode: event.shiftKey ? "range" : event.metaKey || event.ctrlKey ? "toggle" : "replace",
    source: "list",
  });
  return <article
    className={`scene-shot-card${selected ? " is-selected" : ""}${context ? " is-context" : ""}${hovered ? " is-hovered" : ""}${focused ? " is-focused" : ""}`}
    aria-label={shot.title}
    onMouseEnter={() => store.getState().actions.setHoveredEntity({ type: "shot", id: shotId })}
    onMouseLeave={() => store.getState().actions.setHoveredEntity(null)}
    onFocus={() => store.getState().actions.setFocusedEntity({ type: "shot", id: shotId })}
    onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) store.getState().actions.setFocusedEntity(null); }}
  >
    <button type="button" className="scene-shot-card__open" aria-label={`${position} ${shot.title} ${shot.duration} 秒`} aria-pressed={selected} onClick={select}>
      <span className="scene-shot-card__thumb">
        {thumbnail ? <img src={thumbnail.url} alt={`${shot.title}缩略图`} loading="lazy" /> : <Image size={22} aria-hidden="true" />}
      </span>
      <span className="scene-shot-card__body">
        <strong>{shot.title}</strong>
        <span className="scene-shot-card__meta"><span>{shot.duration} 秒</span><span className={`scene-shot-card__status scene-shot-card__status--${status}`}>{status}</span><span>{candidateCount} 个候选</span></span>
        <small className={issueCount ? "scene-shot-card__issue" : "scene-shot-card__issue is-clear"}>
          {firstIssue ? `${firstIssue.message}${issueCount > 1 ? `等 ${issueCount} 项` : ""}` : "暂无问题"}
        </small>
      </span>
    </button>
    <div className="scene-shot-card__actions" aria-label={`${shot.title}摘要操作`}>
      <button type="button" aria-label={`打开${shot.title}`} onClick={() => store.getState().actions.selectShot(shotId, { source: "list" })}><FolderOpen size={15} aria-hidden="true" /></button>
      <button type="button" aria-label={`复制${shot.title}`} onClick={() => store.getState().actions.duplicateShot(shotId)}><Copy size={15} aria-hidden="true" /></button>
      <button type="button" aria-label={`定位${shot.title}`} onClick={() => store.getState().actions.selectShot(shotId, { source: "list" })}><LocateFixed size={15} aria-hidden="true" /></button>
      <details>
        <summary>更多{shot.title}</summary>
        <div>
          <button type="button" onClick={() => store.getState().actions.moveShot(shotId, -1)}>上移</button>
          <button type="button" onClick={() => store.getState().actions.moveShot(shotId, 1)}>下移</button>
        </div>
      </details>
    </div>
  </article>;
}

export function SceneShotList({ store, onCreateShot }: Props) {
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(null);
  const order = useStore(store, (state) => state.order);
  const expandedSceneIds = useStore(store, (state) => state.view.expandedSceneIds);
  const locateRequest = useStore(store, (state) => state.view.locateRequest);
  const rows = useMemo(() => order.sceneIds.flatMap<ListRow>((sceneId) => [
    { type: "scene", id: sceneId },
    ...(expandedSceneIds.has(sceneId) ? (order.shotIdsByScene[sceneId] ?? []).map((id): ListRow => ({ type: "shot", id })) : []),
  ]), [expandedSceneIds, order]);
  const estimateSize = (index: number) => rows[index]?.type === "scene" ? 64 : 164;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollElement,
    estimateSize,
    getItemKey: (index) => `${rows[index]?.type}:${rows[index]?.id}`,
    observeElementRect: (instance, callback) => observeElementRect(instance, (rect) => callback({
      width: rect.width || 320,
      height: rect.height || 680,
    })),
    measureElement: (element, entry, instance) => {
      const measured = measureElement(element, entry, instance);
      return measured || estimateSize(Number(element.getAttribute("data-index") ?? 0));
    },
    overscan: 6,
    initialRect: { width: 320, height: 680 },
  });

  useEffect(() => {
    if (!locateRequest || locateRequest.source === "list") return;
    const target = locateRequest.entity.type === "processNode"
      ? { type: "shot" as const, id: locateRequest.entity.shotId }
      : locateRequest.entity;
    const index = rows.findIndex((row) => row.type === target.type && row.id === target.id);
    if (index < 0) return;
    virtualizer.scrollToIndex(index, { align: "auto" });
  }, [locateRequest, rows, virtualizer]);

  return <aside className="scene-shot-list" aria-label="按场景组织的镜头列表">
    <div className="scene-shot-list__heading"><div><h2>镜头列表</h2><p>按场景与管理顺序浏览</p></div>{onCreateShot && <button type="button" className="icon-action" aria-label="新建镜头" onClick={onCreateShot}><Plus size={17} aria-hidden="true" /></button>}</div>
    <div ref={setScrollElement} className="scene-shot-list__scroll" data-virtualized="true">
      <div className="scene-shot-list__canvas" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((item) => {
          const row = rows[item.index];
          return <div key={item.key} ref={virtualizer.measureElement} data-index={item.index} className={`scene-shot-list__row scene-shot-list__row--${row.type}`} style={{ transform: `translateY(${item.start}px)` }}>
            {row.type === "scene" ? <SceneRow store={store} sceneId={row.id} /> : <ShotCard store={store} shotId={row.id} />}
          </div>;
        })}
      </div>
    </div>
  </aside>;
}
