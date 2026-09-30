/*
 * The video stage and crop overlay are adapted from OpenShorts
 * dashboard/src/components/ReframeEditor.jsx at 89f030934388d5167b3b0ede164ef893dad09ae9 (MIT).
 * See studioPrototype.LICENSE.md for the upstream license notice.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { BatchCue } from "./batchEditingApi";
import type { TimelineClip, TimelineTrack } from "./timelineApi";
import "./reframeControls.css";

export type ReframeOverride = { mode: "auto" | "center" | "manual" | "replace"; keyframes: Array<{ timeSeconds: number; x: number; y: number }> };
export type Reframe = {
  schemaVersion: number; sourceFingerprint: string; sourceWidth: number; sourceHeight: number;
  targetAspect: { width: number; height: number }; detectorVersion: string; modelSha256: string;
  samples: Array<{ timeSeconds: number; boxes: Array<{ x: number; y: number; width: number; height: number; label?: string; confidence?: number }>; center?: { x: number; y: number }; confidence?: number }>;
  uncertaintyReasons: string[]; status: "ready" | "review_required"; override: ReframeOverride;
  frameDurationSeconds?: number; samplingIntervalSeconds?: number;
};
export type ReframeAnalysis = { id?: string; status: "queued" | "running" | "completed" | "failed" | "cancelled"; error: string | null };
export type PackagingElement = { id: string; kind: "subtitle" | "keyword" | "title" | "selling-point" | "cta"; enabled: boolean; position: "auto" | "top" | "middle" | "bottom"; style: "plain" | "emphasis" | "banner"; start?: number; end?: number; text?: string; cueId?: string };
export type Packaging = { schemaVersion: 1; templateId: "commercial-clean"; templateVersion: 1; font: { id: string; version: string; sha256: string; source: string; license: "OFL-1.1" };
  safeArea: { left: number; right: number; top: number; bottom: number }; elements: PackagingElement[] };

const FONT = { id: "noto-sans-cjk-sc-regular", version: "Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603", sha256: "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b", source: "Noto Sans CJK SC", license: "OFL-1.1" as const };
const kinds: PackagingElement["kind"][] = ["title", "keyword", "selling-point", "subtitle", "cta"];

export function defaultPackaging(): Packaging {
  return { schemaVersion: 1, templateId: "commercial-clean", templateVersion: 1, font: { ...FONT }, safeArea: { left: .05, right: .05, top: .05, bottom: .05 }, elements: [] };
}

function clamp(value: number, minimum = 0, maximum = 1) { return Math.min(maximum, Math.max(minimum, value)); }
function formatTime(value: number) {
  const seconds = value % 60;
  const precision = Number.isInteger(value) ? 1 : Math.min(6, Math.max(1, String(value).split(".")[1]?.length ?? 1));
  return `${Math.floor(value / 60)}:${seconds.toFixed(precision).padStart(3 + precision, "0")}`;
}
function interpolate(points: Array<{ timeSeconds: number; x: number; y: number }>, time: number) {
  if (!points.length) return { x: .5, y: .5 };
  const ordered = [...points].sort((left, right) => left.timeSeconds - right.timeSeconds);
  if (time <= ordered[0].timeSeconds) return ordered[0];
  if (time >= ordered[ordered.length - 1].timeSeconds) return ordered[ordered.length - 1];
  const right = ordered.find((item) => item.timeSeconds >= time)!;
  const left = ordered[ordered.indexOf(right) - 1];
  const fraction = (time - left.timeSeconds) / (right.timeSeconds - left.timeSeconds);
  return { x: left.x + (right.x - left.x) * fraction, y: left.y + (right.y - left.y) * fraction };
}

function contentRect(stageRect: DOMRect, videoRect: DOMRect, sourceWidth: number, sourceHeight: number) {
  const boxWidth = videoRect.width, boxHeight = videoRect.height;
  if (!boxWidth || !boxHeight || !sourceWidth || !sourceHeight) return { left: videoRect.left - stageRect.left, top: videoRect.top - stageRect.top, width: boxWidth, height: boxHeight };
  const scale = Math.min(boxWidth / sourceWidth, boxHeight / sourceHeight);
  const width = sourceWidth * scale, height = sourceHeight * scale;
  return { left: videoRect.left - stageRect.left + (boxWidth - width) / 2, top: videoRect.top - stageRect.top + (boxHeight - height) / 2, width, height };
}

export function ReframeControls({ clipId, assetUrl, mediaKind = "video", sourceStart = 0, sourceEnd, reframe, analysis, disabled, onChange, onAnalyze, onCancel, candidates = [], onReplace }: {
  clipId: string; assetUrl: string; reframe?: Reframe; analysis?: ReframeAnalysis; disabled: boolean;
  mediaKind?: "video" | "image"; sourceStart?: number; sourceEnd?: number;
  onChange: (value: Reframe) => void; onAnalyze: () => void; onCancel: () => void; candidates?: Array<{ id: string; name: string }>; onReplace?: (assetId: string) => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const source = useRef<HTMLVideoElement>(null);
  const still = useRef<HTMLImageElement>(null);
  const dragging = useRef(false);
  const [selectedTime, setSelectedTime] = useState<number | null>(null);
  const [visibleFrame, setVisibleFrame] = useState({ left: 0, top: 0, width: 0, height: 0 });
  const isVideo = mediaKind === "video";
  const maximumSourceTime = isVideo ? sourceEnd ?? Math.max(sourceStart, ...(reframe?.samples.map((sample) => sample.timeSeconds) ?? [sourceStart])) : 0;
  const currentTime = isVideo ? Math.min(maximumSourceTime, Math.max(sourceStart, selectedTime ?? reframe?.samples[0]?.timeSeconds ?? sourceStart)) : 0;
  const override = reframe?.override ?? { mode: "auto" as const, keyframes: [] };
  const active = { timeSeconds: currentTime, ...(override.mode === "center" ? { x: .5, y: .5 } : interpolate(override.mode === "manual" || override.mode === "replace" ? override.keyframes : reframe?.samples.map((sample) => ({ timeSeconds: sample.timeSeconds, x: sample.center?.x ?? .5, y: sample.center?.y ?? .5 })) ?? [], currentTime)) };
  const targetRatio = reframe ? reframe.targetAspect.width / reframe.targetAspect.height : .5625;
  const sourceRatio = reframe?.sourceWidth && reframe.sourceHeight ? reframe.sourceWidth / reframe.sourceHeight : 1;
  const cropWidthFraction = Math.min(1, targetRatio / sourceRatio);
  const cropHeightFraction = Math.min(1, sourceRatio / targetRatio);
  const reviewReason = reframe?.uncertaintyReasons?.join("；");
  const setOverride = (next: ReframeOverride) => { if (reframe) onChange({ ...reframe, override: next }); };
  const measureVisibleFrame = () => {
    const stageRect = stage.current?.getBoundingClientRect();
    const node = isVideo ? source.current : still.current;
    const sourceRect = node?.getBoundingClientRect();
    if (!stageRect || !sourceRect || !sourceRect.width || !sourceRect.height) return;
    const width = isVideo ? source.current?.videoWidth : still.current?.naturalWidth;
    const height = isVideo ? source.current?.videoHeight : still.current?.naturalHeight;
    setVisibleFrame(contentRect(stageRect, sourceRect, width || reframe?.sourceWidth || 0, height || reframe?.sourceHeight || 0));
  };
  useEffect(() => {
    const node = source.current;
    if (!node) return;
    const sync = () => { measureVisibleFrame(); if (node.currentTime < sourceStart) node.currentTime = sourceStart; };
    node.addEventListener("loadedmetadata", sync);
    window.addEventListener("resize", measureVisibleFrame);
    sync();
    return () => { node.removeEventListener("loadedmetadata", sync); window.removeEventListener("resize", measureVisibleFrame); };
  }, [assetUrl, isVideo, sourceStart, reframe?.sourceWidth, reframe?.sourceHeight]);
  useEffect(() => {
    if (isVideo || !still.current) return;
    const node = still.current;
    node.addEventListener("load", measureVisibleFrame);
    window.addEventListener("resize", measureVisibleFrame);
    measureVisibleFrame();
    return () => { node.removeEventListener("load", measureVisibleFrame); window.removeEventListener("resize", measureVisibleFrame); };
  }, [assetUrl, isVideo, reframe?.sourceWidth, reframe?.sourceHeight]);
  const setSourceTime = (timeSeconds: number) => {
    const next = Math.min(maximumSourceTime, Math.max(sourceStart, timeSeconds));
    setSelectedTime(next);
    if (source.current && isVideo) source.current.currentTime = next;
  };
  const move = (clientX: number, clientY: number) => {
    if (!reframe || disabled) return;
    const stageRect = stage.current?.getBoundingClientRect();
    const node = isVideo ? source.current : still.current;
    const sourceRect = node?.getBoundingClientRect();
    if (!stageRect || !sourceRect?.width) return;
    const frame = contentRect(stageRect, sourceRect, (isVideo ? source.current?.videoWidth : still.current?.naturalWidth) || reframe.sourceWidth, (isVideo ? source.current?.videoHeight : still.current?.naturalHeight) || reframe.sourceHeight);
    if (!frame.width || !frame.height) return;
    const x = clamp((clientX - stageRect.left - frame.left) / frame.width, cropWidthFraction / 2, 1 - cropWidthFraction / 2);
    const y = Number.isFinite(clientY) ? clamp((clientY - stageRect.top - frame.top) / frame.height, cropHeightFraction / 2, 1 - cropHeightFraction / 2) : active.y;
    const keyframes = [...override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds), { timeSeconds: currentTime, x: Number(x.toFixed(3)), y: Number(y.toFixed(3)) }]
      .sort((left, right) => left.timeSeconds - right.timeSeconds);
    setOverride({ mode: "manual", keyframes });
  };
  const selectPlaybackTime = () => {
    const value = source.current?.currentTime;
    if (!Number.isFinite(value)) return;
    setSourceTime(value!);
  };
  const overlay = (interactive: boolean) => {
    const frame = visibleFrame.width ? visibleFrame : { left: 0, top: 0, width: stage.current?.getBoundingClientRect().width ?? 0, height: stage.current?.getBoundingClientRect().height ?? 0 };
    const left = frame.left + Math.max(0, active.x - cropWidthFraction / 2) * frame.width;
    const top = frame.top + Math.max(0, active.y - cropHeightFraction / 2) * frame.height;
    const width = cropWidthFraction * frame.width, height = cropHeightFraction * frame.height;
    const right = left + width, bottom = top + height;
    return <><div className="reframe-dim reframe-dim-top" style={{ left: frame.left, top: frame.top, width: frame.width, height: Math.max(0, top - frame.top) }} />
      <div className="reframe-dim reframe-dim-bottom" style={{ left: frame.left, top: bottom, width: frame.width, height: Math.max(0, frame.top + frame.height - bottom) }} />
      <div className="reframe-dim reframe-dim-left" style={{ left: frame.left, top, width: Math.max(0, left - frame.left), height }} />
      <div className="reframe-dim reframe-dim-right" style={{ left: right, top, width: Math.max(0, frame.left + frame.width - right), height }} />
      <div className="reframe-window" role="slider" aria-label={`${clipId} 裁剪框`} aria-disabled={interactive ? undefined : "true"} aria-valuemin={0} aria-valuemax={1} aria-valuenow={active.x} tabIndex={interactive ? 0 : undefined}
        style={{ left, top, width, height }} onPointerDown={interactive ? (event) => { dragging.current = true; event.currentTarget.setPointerCapture?.(event.pointerId); move(event.clientX, event.clientY); } : undefined}
        onKeyDown={interactive ? (event) => { const horizontal = event.key === "ArrowLeft" || event.key === "ArrowRight"; const vertical = event.key === "ArrowUp" || event.key === "ArrowDown"; if (horizontal || vertical) { event.preventDefault(); const x = clamp(active.x + (horizontal ? (event.key === "ArrowLeft" ? -.02 : .02) : 0), cropWidthFraction / 2, 1 - cropWidthFraction / 2); const y = clamp(active.y + (vertical ? (event.key === "ArrowUp" ? -.02 : .02) : 0), cropHeightFraction / 2, 1 - cropHeightFraction / 2); setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds), { timeSeconds: currentTime, x: Number(x.toFixed(3)), y: Number(y.toFixed(3)) }].sort((a, b) => a.timeSeconds - b.timeSeconds) }); } } : undefined} />
    </>;
  };

  return <section className="reframe-controls" aria-label="主体裁剪">
    <header><div><h5>主体裁剪与满幅构图</h5><p>播放原片并拖动明亮裁剪框；框外区域会变暗。人工关键点只保存在草稿中，分析完成不会覆盖它。</p></div>
      <div className="reframe-actions">{analysis?.status === "queued" || analysis?.status === "running"
        ? <button type="button" className="secondary-action" disabled={disabled} onClick={onCancel}>取消主体分析</button>
        : <button type="button" className="secondary-action" disabled={disabled} onClick={onAnalyze}>{analysis?.status === "failed" ? "重新分析主体" : "分析主体"}</button>}</div></header>
    {analysis?.status === "failed" && <p className="reframe-error" role="alert">主体分析失败：{analysis.error ?? "请检查本地模型和原片后重试。"}</p>}
    {analysis && (analysis.status === "queued" || analysis.status === "running") && <p role="status">主体分析{analysis.status === "queued" ? "排队中" : "进行中"}；可以取消，当前人工草稿不会丢失。</p>}
    {reviewReason && <p className="reframe-review" role="status">需人工确认：{reviewReason}</p>}
    {!reframe && assetUrl && isVideo && <><p role="status">原片已就绪；完成主体分析后即可拖动裁剪框并保存人工关键点。</p><div ref={stage} className="reframe-stage" aria-label={`${clipId} 主体裁剪原片`}>
      <video ref={source} controls playsInline preload="metadata" src={assetUrl} aria-label={`${clipId} 原片`} onTimeUpdate={selectPlaybackTime} />{overlay(false)}
    </div></>}
    {reframe ? <>
      {isVideo && <div className="reframe-keyframes" aria-label="源时间关键点">{reframe.samples.map((sample) => <button key={sample.timeSeconds} type="button" className="secondary-action" disabled={disabled}
        aria-label={`源时间 ${formatTime(sample.timeSeconds)} 的检测样本`} aria-pressed={sample.timeSeconds === currentTime} onClick={() => setSourceTime(sample.timeSeconds)}>{formatTime(sample.timeSeconds)}</button>)}<label>关键点秒<input aria-label="关键点秒" type="number" min="0" step=".01" value={active.timeSeconds} disabled={disabled}
          onChange={(event) => { const input = Number(event.target.value); if (!Number.isFinite(input)) return; const timeSeconds = Math.min(maximumSourceTime, Math.max(sourceStart, input)); setSourceTime(timeSeconds); setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds && item.timeSeconds !== timeSeconds), { ...active, timeSeconds }].sort((a, b) => a.timeSeconds - b.timeSeconds) }); }} /></label>
        <button type="button" className="secondary-action" disabled={disabled} onClick={() => setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== currentTime), { ...active, timeSeconds: currentTime }].sort((a, b) => a.timeSeconds - b.timeSeconds) })}>新增关键点</button>
        <button type="button" className="secondary-action" disabled={disabled || override.keyframes.length <= 1} onClick={() => setOverride({ ...override, keyframes: override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds) })}>删除关键点</button></div>}
      <div ref={stage} className="reframe-stage" aria-label={`${clipId} 主体裁剪原片`} onPointerMove={(event) => { if (dragging.current) move(event.clientX, event.clientY); }} onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }}>
        {isVideo ? <video ref={source} controls playsInline preload="metadata" src={assetUrl} aria-label={`${clipId} 原片`} onLoadedMetadata={measureVisibleFrame} onTimeUpdate={selectPlaybackTime} /> : <img ref={still} src={assetUrl} alt={`${clipId} 原片`} onLoad={measureVisibleFrame} />}{overlay(true)}
      </div>
      <div className="reframe-modes" role="group" aria-label="裁剪模式"><button type="button" className="secondary-action" aria-pressed={override.mode === "auto"} disabled={disabled} onClick={() => setOverride({ mode: "auto", keyframes: [] })}>自动</button>
        <button type="button" className="secondary-action" aria-pressed={override.mode === "center"} disabled={disabled} onClick={() => setOverride({ mode: "center", keyframes: [] })}>中心裁剪</button>
        <button type="button" className="secondary-action" aria-pressed={override.mode === "manual"} disabled={disabled} onClick={() => setOverride({ ...override, mode: "manual", keyframes: override.keyframes.length ? override.keyframes : [{ ...active }] })}>人工关键点</button>
        {onReplace && candidates.length > 0 && <label>候选素材<select aria-label={`${clipId} 候选素材`} defaultValue="" disabled={disabled} onChange={(event) => { if (event.target.value) onReplace(event.target.value); }}><option value="" disabled>更换候选素材</option>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label>}</div>
      {override.mode === "manual" && <div className="reframe-number"><label>当前关键点横向位置<input aria-label="当前关键点横向位置" type="number" min="0" max="1" step=".01" value={active.x.toFixed(2)} disabled={disabled}
        onChange={(event) => { const x = clamp(Number(event.target.value)); if (Number.isFinite(x)) setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds), { ...active, x }].sort((a, b) => a.timeSeconds - b.timeSeconds) }); }} /></label><label>当前关键点纵向位置<input aria-label="当前关键点纵向位置" type="number" min="0" max="1" step=".01" value={active.y.toFixed(2)} disabled={disabled}
        onChange={(event) => { const y = clamp(Number(event.target.value)); if (Number.isFinite(y)) setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds), { ...active, y }].sort((a, b) => a.timeSeconds - b.timeSeconds) }); }} /></label></div>}
    </> : !assetUrl ? <p>请选择可用图片或视频素材后再执行主体分析。</p> : null}
  </section>;
}

function packagingErrors(value: Packaging) {
  const errors: string[] = [];
  if (JSON.stringify(value.font) !== JSON.stringify(FONT)) errors.push("受控字体引用无效，无法渲染或交付。");
  const area = value.safeArea;
  if (area.left + area.right >= .9 || area.top + area.bottom >= .9) errors.push("安全区没有可用空间，请缩小四边留白。");
  return errors;
}

export function PackagingControls({ value, cues, onChange, disabled }: { value: Packaging; cues: BatchCue[]; onChange: (next: Packaging) => void; disabled: boolean }) {
  const errors = useMemo(() => packagingErrors(value), [value]);
  const update = (change: Partial<Packaging>) => onChange({ ...value, ...change });
  const updateElement = (id: string, change: Partial<PackagingElement>) => update({ elements: value.elements.map((item) => item.id === id ? { ...item, ...change } as PackagingElement : item) });
  const add = (kind: PackagingElement["kind"]) => {
    let serial = 1;
    while (value.elements.some((item) => item.id === `${kind}-${serial}`)) serial += 1;
    const id = `${kind}-${serial}`;
    const base = { id, kind, enabled: true, position: kind === "subtitle" ? "bottom" : "top", style: kind === "title" ? "banner" : "plain" } as const;
    update({ elements: [...value.elements, kind === "subtitle" ? { ...base, cueId: cues[0]?.id ?? "" } : { ...base, start: 0, end: 2, text: kind === "cta" ? "立即了解" : "" }] });
  };
  return <section className="packaging-controls" aria-label="商业包装">
    <header><div><h5>商业包装</h5><p>模板、五类图文元素和安全区只修改草稿；保存后旧预览与审核会失效，最终以真实渲染预览核对。</p></div><label>模板<select aria-label="包装模板" value={value.templateId} disabled={disabled} onChange={() => update({ templateId: "commercial-clean", templateVersion: 1 })}><option value="commercial-clean">商业清爽</option></select></label><button type="button" className="secondary-action" disabled={disabled || !value.elements.some((item) => item.enabled)} onClick={() => update({ elements: value.elements.map((item) => ({ ...item, enabled: false })) })}>关闭已启用包装</button></header>
    {errors.map((error) => <p key={error} className="reframe-error" role="alert">{error}</p>)}
    <div className="packaging-safe-area"><strong>安全区</strong>{(["left", "right", "top", "bottom"] as const).map((side) => <label key={side}>{side}<input aria-label={`安全区 ${side}`} type="number" min="0" max=".45" step=".01" value={value.safeArea[side]} disabled={disabled}
      onChange={(event) => update({ safeArea: { ...value.safeArea, [side]: Number(event.target.value) } })} /></label>)}</div>
    <div className="packaging-add">{kinds.map((kind) => <button key={kind} type="button" className="secondary-action" disabled={disabled || (kind === "subtitle" && !cues.length)} onClick={() => add(kind)}>添加{kind}</button>)}</div>
    {value.elements.map((element) => <article key={element.id} className="packaging-element"><header><strong>{element.kind}</strong><label><input aria-label={`${element.id} 启用`} type="checkbox" checked={element.enabled} disabled={disabled} onChange={(event) => updateElement(element.id, { enabled: event.target.checked })} />启用</label>
      <button type="button" className="secondary-action" disabled={disabled} onClick={() => update({ elements: value.elements.filter((item) => item.id !== element.id) })}>删除</button></header>
      {element.kind === "subtitle" ? <label>字幕正本<select aria-label={`${element.id} 字幕正本`} value={element.cueId ?? ""} disabled={disabled} onChange={(event) => updateElement(element.id, { cueId: event.target.value })}>{cues.map((cue) => <option key={cue.id} value={cue.id}>{cue.text || cue.id}</option>)}</select></label>
        : <><label>文字<input aria-label={`${element.id} 文字`} value={element.text ?? ""} disabled={disabled} onChange={(event) => updateElement(element.id, { text: event.target.value })} /></label><label>开始<input aria-label={`${element.id} 开始`} type="number" min="0" step=".1" value={element.start ?? 0} disabled={disabled} onChange={(event) => updateElement(element.id, { start: Number(event.target.value) })} /></label><label>结束<input aria-label={`${element.id} 结束`} type="number" min=".1" step=".1" value={element.end ?? 2} disabled={disabled} onChange={(event) => updateElement(element.id, { end: Number(event.target.value) })} /></label></>}
      <label>位置<select aria-label={`${element.id} 位置`} value={element.position} disabled={disabled} onChange={(event) => updateElement(element.id, { position: event.target.value as PackagingElement["position"] })}><option value="auto">自动避让</option><option value="top">顶部</option><option value="middle">中部</option><option value="bottom">底部</option></select></label>
      <label>样式<select aria-label={`${element.id} 样式`} value={element.style} disabled={disabled} onChange={(event) => updateElement(element.id, { style: event.target.value as PackagingElement["style"] })}><option value="plain">普通</option><option value="emphasis">强调</option><option value="banner">横幅</option></select></label></article>)}
  </section>;
}

export function reframeForClip(clip: TimelineClip) { return clip.reframe as Reframe | undefined; }

function sameReframeIdentity(left: TimelineClip, right: TimelineClip) {
  return ["assetId", "inPoint", "duration", "speed"].every((key) => left[key as keyof TimelineClip] === right[key as keyof TimelineClip]);
}

/** Server analysis facts win only for the same source identity; a local non-auto override remains the user's draft. */
export function mergeServerReframeFacts(draftTracks: TimelineTrack[], serverTracks: TimelineTrack[]): TimelineTrack[] {
  const serverById = new Map(serverTracks.flatMap((track) => track.clips).map((clip) => [clip.id, clip]));
  return draftTracks.map((track) => ({ ...track, clips: track.clips.map((clip) => {
    const server = serverById.get(clip.id);
    if (!server?.reframe || !sameReframeIdentity(clip, server)) return clip;
    const override = clip.reframe?.override;
    return { ...clip, reframe: { ...server.reframe, ...(override && override.mode !== "auto" ? { override } : {}) } };
  }) }));
}
