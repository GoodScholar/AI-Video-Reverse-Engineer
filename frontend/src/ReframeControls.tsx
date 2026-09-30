/*
 * The video stage and crop overlay are adapted from OpenShorts
 * dashboard/src/components/ReframeEditor.jsx at 89f030934388d5167b3b0ede164ef893dad09ae9 (MIT).
 * See studioPrototype.LICENSE.md for the upstream license notice.
 */
import { useMemo, useRef, useState } from "react";
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
function formatTime(value: number) { return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`; }
function keyframeAt(override: ReframeOverride, time: number) { return [...override.keyframes].sort((left, right) => Math.abs(left.timeSeconds - time) - Math.abs(right.timeSeconds - time))[0]; }

export function ReframeControls({ clipId, assetUrl, reframe, analysis, disabled, onChange, onAnalyze, onCancel, candidates = [], onReplace }: {
  clipId: string; assetUrl: string; reframe?: Reframe; analysis?: ReframeAnalysis; disabled: boolean;
  onChange: (value: Reframe) => void; onAnalyze: () => void; onCancel: () => void; candidates?: Array<{ id: string; name: string }>; onReplace?: (assetId: string) => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [selectedTime, setSelectedTime] = useState<number | null>(null);
  const currentTime = selectedTime ?? reframe?.samples[0]?.timeSeconds ?? 0;
  const override = reframe?.override ?? { mode: "auto" as const, keyframes: [] };
  const active = keyframeAt(override, currentTime) ?? { timeSeconds: currentTime, x: .5, y: .5 };
  const cropFraction = reframe ? clamp((reframe.targetAspect.width / reframe.targetAspect.height) / (reframe.sourceWidth / reframe.sourceHeight), .12, 1) : .5625;
  const reviewReason = reframe?.uncertaintyReasons?.join("；");
  const setOverride = (next: ReframeOverride) => { if (reframe) onChange({ ...reframe, override: next }); };
  const move = (clientX: number) => {
    if (!reframe || disabled) return;
    const rect = stage.current?.getBoundingClientRect();
    if (!rect?.width) return;
    const x = clamp((clientX - rect.left) / rect.width, cropFraction / 2, 1 - cropFraction / 2);
    const keyframes = [...override.keyframes.filter((item) => item.timeSeconds !== currentTime), { timeSeconds: currentTime, x: Number(x.toFixed(3)), y: .5 }]
      .sort((left, right) => left.timeSeconds - right.timeSeconds);
    setOverride({ mode: "manual", keyframes });
  };

  return <section className="reframe-controls" aria-label="主体裁剪">
    <header><div><h5>主体裁剪与满幅构图</h5><p>播放原片并拖动明亮裁剪框；框外区域会变暗。人工关键点只保存在草稿中，分析完成不会覆盖它。</p></div>
      <div className="reframe-actions">{analysis?.status === "queued" || analysis?.status === "running"
        ? <button type="button" className="secondary-action" disabled={disabled} onClick={onCancel}>取消主体分析</button>
        : <button type="button" className="secondary-action" disabled={disabled} onClick={onAnalyze}>{analysis?.status === "failed" ? "重新分析主体" : "分析主体"}</button>}</div></header>
    {analysis?.status === "failed" && <p className="reframe-error" role="alert">主体分析失败：{analysis.error ?? "请检查本地模型和原片后重试。"}</p>}
    {analysis && (analysis.status === "queued" || analysis.status === "running") && <p role="status">主体分析{analysis.status === "queued" ? "排队中" : "进行中"}；可以取消，当前人工草稿不会丢失。</p>}
    {reviewReason && <p className="reframe-review" role="status">需人工确认：{reviewReason}</p>}
    {!reframe && assetUrl && <><p role="status">原片已就绪；完成主体分析后即可拖动裁剪框并保存人工关键点。</p><div ref={stage} className="reframe-stage" aria-label={`${clipId} 主体裁剪原片`}>
      <video controls playsInline preload="metadata" src={assetUrl} aria-label={`${clipId} 原片`} />
      <div className="reframe-dim reframe-dim-left" style={{ width: `${Math.max(0, active.x - cropFraction / 2) * 100}%` }} />
      <div className="reframe-dim reframe-dim-right" style={{ width: `${Math.max(0, 1 - active.x - cropFraction / 2) * 100}%` }} />
      <div className="reframe-window" role="slider" aria-label={`${clipId} 裁剪框`} aria-disabled="true" aria-valuemin={0} aria-valuemax={1} aria-valuenow={active.x} style={{ left: `${(active.x - cropFraction / 2) * 100}%`, width: `${cropFraction * 100}%` }} />
    </div></>}
    {reframe ? <>
      <div className="reframe-keyframes" aria-label="源时间关键点">{reframe.samples.map((sample) => <button key={sample.timeSeconds} type="button" className="secondary-action" disabled={disabled}
        aria-pressed={sample.timeSeconds === currentTime} onClick={() => setSelectedTime(sample.timeSeconds)}>{formatTime(sample.timeSeconds)}</button>)}<label>关键点秒<input aria-label="关键点秒" type="number" min="0" step=".01" value={active.timeSeconds} disabled={disabled}
          onChange={(event) => { const timeSeconds = Number(event.target.value); if (!Number.isFinite(timeSeconds)) return; setSelectedTime(timeSeconds); setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds), { ...active, timeSeconds }].sort((a, b) => a.timeSeconds - b.timeSeconds) }); }} /></label>
        <button type="button" className="secondary-action" disabled={disabled} onClick={() => setOverride({ mode: "manual", keyframes: [...override.keyframes, { ...active, timeSeconds: currentTime }].sort((a, b) => a.timeSeconds - b.timeSeconds) })}>新增关键点</button>
        <button type="button" className="secondary-action" disabled={disabled || !override.keyframes.length} onClick={() => setOverride({ ...override, keyframes: override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds) })}>删除关键点</button></div>
      <div ref={stage} className="reframe-stage" aria-label={`${clipId} 主体裁剪原片`} onPointerMove={(event) => { if (dragging.current) move(event.clientX); }} onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }}>
        <video controls playsInline preload="metadata" src={assetUrl} aria-label={`${clipId} 原片`} />
        <div className="reframe-dim reframe-dim-left" style={{ width: `${Math.max(0, active.x - cropFraction / 2) * 100}%` }} />
        <div className="reframe-dim reframe-dim-right" style={{ width: `${Math.max(0, 1 - active.x - cropFraction / 2) * 100}%` }} />
        <div className="reframe-window" role="slider" aria-label={`${clipId} 裁剪框`} aria-valuemin={0} aria-valuemax={1} aria-valuenow={active.x} tabIndex={0}
          style={{ left: `${(active.x - cropFraction / 2) * 100}%`, width: `${cropFraction * 100}%` }} onPointerDown={(event) => { dragging.current = true; event.currentTarget.setPointerCapture?.(event.pointerId); move(event.clientX); }}
          onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); const x = clamp(active.x + (event.key === "ArrowLeft" ? -.02 : .02), cropFraction / 2, 1 - cropFraction / 2); setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== currentTime), { timeSeconds: currentTime, x: Number(x.toFixed(3)), y: .5 }].sort((a, b) => a.timeSeconds - b.timeSeconds) }); } }} />
      </div>
      <div className="reframe-modes" role="group" aria-label="裁剪模式"><button type="button" className="secondary-action" aria-pressed={override.mode === "auto"} disabled={disabled} onClick={() => setOverride({ mode: "auto", keyframes: [] })}>自动</button>
        <button type="button" className="secondary-action" aria-pressed={override.mode === "center"} disabled={disabled} onClick={() => setOverride({ mode: "center", keyframes: [] })}>中心裁剪</button>
        <button type="button" className="secondary-action" aria-pressed={override.mode === "manual"} disabled={disabled} onClick={() => setOverride({ ...override, mode: "manual", keyframes: override.keyframes.length ? override.keyframes : [{ ...active }] })}>人工关键点</button>
        {onReplace && candidates.length > 0 && <label>候选素材<select aria-label={`${clipId} 候选素材`} defaultValue="" disabled={disabled} onChange={(event) => { if (event.target.value) onReplace(event.target.value); }}><option value="" disabled>更换候选素材</option>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label>}</div>
      {override.mode === "manual" && <label className="reframe-number">当前关键点位置<input aria-label="当前关键点位置" type="number" min="0" max="1" step=".01" value={active.x.toFixed(2)} disabled={disabled}
        onChange={(event) => { const x = clamp(Number(event.target.value)); if (Number.isFinite(x)) setOverride({ mode: "manual", keyframes: [...override.keyframes.filter((item) => item.timeSeconds !== active.timeSeconds), { ...active, x }].sort((a, b) => a.timeSeconds - b.timeSeconds) }); }} /></label>}
    </> : !assetUrl ? <p>请选择可用视频素材后再执行主体分析。</p> : null}
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
    const id = `${kind}-${value.elements.length + 1}`;
    const base = { id, kind, enabled: true, position: kind === "subtitle" ? "bottom" : "top", style: kind === "title" ? "banner" : "plain" } as const;
    update({ elements: [...value.elements, kind === "subtitle" ? { ...base, cueId: cues[0]?.id ?? "" } : { ...base, start: 0, end: 2, text: kind === "cta" ? "立即了解" : "" }] });
  };
  return <section className="packaging-controls" aria-label="商业包装">
    <header><div><h5>商业包装</h5><p>模板、五类图文元素和安全区只修改草稿；保存后旧预览与审核会失效，最终以真实渲染预览核对。</p></div><span>模板：commercial-clean</span></header>
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

/** Server analysis facts win; a local non-auto override remains the user's draft. */
export function mergeServerReframeFacts(draftTracks: TimelineTrack[], serverTracks: TimelineTrack[]): TimelineTrack[] {
  const serverById = new Map(serverTracks.flatMap((track) => track.clips).map((clip) => [clip.id, clip]));
  return draftTracks.map((track) => ({ ...track, clips: track.clips.map((clip) => {
    const server = serverById.get(clip.id);
    if (!server?.reframe) return clip;
    const override = clip.reframe?.override;
    return { ...clip, reframe: { ...server.reframe, ...(override && override.mode !== "auto" ? { override } : {}) } };
  }) }));
}
