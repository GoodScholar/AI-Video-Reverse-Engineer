/** Adapted from basketikun/infinite-canvas@dab19adc (MIT, Copyright 2026 basketikun). */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

type Size = { width: number; height: number };
const minZoom = 1; const maxZoom = 4; const zoomStep = 1.2; const padding = 16;

export function useCreativeCanvasImageEditorViewport(image: Size | null, open: boolean) {
  const viewportNodeRef = useRef<HTMLDivElement | null>(null); const stageRef = useRef<HTMLDivElement | null>(null);
  const panRef = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | undefined>(undefined);
  const anchorRef = useRef<{ zoom: number; ratioX: number; ratioY: number; x: number; y: number } | undefined>(undefined);
  const [element, setElement] = useState<HTMLDivElement | null>(null); const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const [zoom, setZoom] = useState(minZoom); const [panning, setPanning] = useState(false); const [space, setSpace] = useState(false);
  const spaceRef = useRef(false);
  const viewportRef = useCallback((node: HTMLDivElement | null) => { viewportNodeRef.current = node; setElement(node); }, []);
  useEffect(() => { if (open) { anchorRef.current = undefined; setZoom(minZoom); } }, [open, image?.width, image?.height]);
  useEffect(() => {
    if (!open) return; const release = () => { spaceRef.current = false; setSpace(false); };
    const down = (event: KeyboardEvent) => { if (event.code !== "Space" || event.repeat || (event.target instanceof Element && event.target.closest("input,textarea,button"))) return; event.preventDefault(); spaceRef.current = true; setSpace(true); };
    const up = (event: KeyboardEvent) => { if (event.code === "Space" && spaceRef.current) { event.preventDefault(); release(); } };
    window.addEventListener("keydown", down, true); window.addEventListener("keyup", up, true); window.addEventListener("blur", release);
    return () => { window.removeEventListener("keydown", down, true); window.removeEventListener("keyup", up, true); window.removeEventListener("blur", release); };
  }, [open]);
  useEffect(() => {
    if (!open || !element) return; const measure = () => setSize(current => current.width === element.clientWidth && current.height === element.clientHeight ? current : { width: element.clientWidth, height: element.clientHeight });
    measure(); if (typeof ResizeObserver === "undefined") return; const observer = new ResizeObserver(measure); observer.observe(element); return () => observer.disconnect();
  }, [element, open]);
  const base = fit(image, size); const stage = { width: base.width * zoom, height: base.height * zoom }; const content = { width: Math.max(size.width, stage.width), height: Math.max(size.height, stage.height) };
  const offset = { left: Math.max(0, Math.round((content.width - stage.width) / 2)), top: Math.max(0, Math.round((content.height - stage.height) / 2)) };
  useLayoutEffect(() => { const viewport = viewportNodeRef.current; const anchor = anchorRef.current; if (!viewport || !anchor || Math.abs(anchor.zoom - zoom) > .001) return; const left = Math.max(0, (Math.max(viewport.clientWidth, stage.width) - stage.width) / 2); const top = Math.max(0, (Math.max(viewport.clientHeight, stage.height) - stage.height) / 2); viewport.scrollLeft = left + anchor.ratioX * stage.width - anchor.x; viewport.scrollTop = top + anchor.ratioY * stage.height - anchor.y; anchorRef.current = undefined; }, [stage.height, stage.width, zoom]);
  const around = useCallback((next: number, clientX?: number, clientY?: number) => { const viewport = viewportNodeRef.current; const stageElement = stageRef.current; if (!viewport || !stageElement || !base.width || !base.height) return; const bounded = clamp(next, minZoom, maxZoom); if (Math.abs(bounded - zoom) < .001) return; const vr = viewport.getBoundingClientRect(); const sr = stageElement.getBoundingClientRect(); const px = clientX ?? vr.left + vr.width / 2; const py = clientY ?? vr.top + vr.height / 2; anchorRef.current = { zoom: bounded, ratioX: clamp((px - sr.left) / Math.max(1, sr.width), 0, 1), ratioY: clamp((py - sr.top) / Math.max(1, sr.height), 0, 1), x: px - vr.left, y: py - vr.top }; setZoom(bounded); }, [base.height, base.width, zoom]);
  useEffect(() => { if (!open || !element) return; const wheel = (event: WheelEvent) => { event.preventDefault(); around(event.deltaY < 0 ? zoom * zoomStep : zoom / zoomStep, event.clientX, event.clientY); }; element.addEventListener("wheel", wheel, { passive: false }); return () => element.removeEventListener("wheel", wheel); }, [around, element, open, zoom]);
  const startPan = useCallback((event: ReactPointerEvent<HTMLDivElement>) => { if (event.button !== 1 && !(event.button === 0 && spaceRef.current)) return; event.preventDefault(); panRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop }; event.currentTarget.setPointerCapture(event.pointerId); setPanning(true); }, []);
  const movePan = useCallback((event: ReactPointerEvent<HTMLDivElement>) => { const pan = panRef.current; if (!pan || pan.pointerId !== event.pointerId) return; event.preventDefault(); event.currentTarget.scrollLeft = pan.left - (event.clientX - pan.x); event.currentTarget.scrollTop = pan.top - (event.clientY - pan.y); }, []);
  const stopPan = useCallback((event: ReactPointerEvent<HTMLDivElement>) => { if (panRef.current?.pointerId !== event.pointerId) return; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); panRef.current = undefined; setPanning(false); }, []);
  return { viewportRef, stageRef, zoom, panning, space, canZoomIn: zoom < maxZoom, canZoomOut: zoom > minZoom, zoomIn: () => around(zoom * zoomStep), zoomOut: () => around(zoom / zoomStep), resetZoom: () => around(minZoom), panHandlers: { onPointerDownCapture: startPan, onPointerMoveCapture: movePan, onPointerUpCapture: stopPan, onPointerCancelCapture: stopPan }, contentStyle: { width: content.width, height: content.height } satisfies CSSProperties, stageStyle: { left: offset.left, top: offset.top, width: stage.width, height: stage.height } satisfies CSSProperties, mediaStyle: { width: base.width, height: base.height, transform: `translateZ(0) scale(${zoom})`, transformOrigin: "top left" } satisfies CSSProperties };
}
function fit(image: Size | null, viewport: Size): Size { if (!image || !viewport.width || !viewport.height) return { width: 0, height: 0 }; const scale = Math.min(Math.max(1, viewport.width - padding * 2) / image.width, Math.max(1, viewport.height - padding * 2) / image.height, 1); return { width: Math.max(1, Math.floor(image.width * scale)), height: Math.max(1, Math.floor(image.height * scale)) }; }
function clamp(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)); }
