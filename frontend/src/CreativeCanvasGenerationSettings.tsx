/*
 * Adapted from Toonflow `generationSettings.vue` at d86cce6b689916ab73f75bc4c1f42a2a7424516c (MIT).
 * The source receives a selected model's sizes/ratios; this adapter also keeps
 * the project configuration version and provider credentials explicit.
 */
import * as React from "react";
import { createPortal } from "react-dom";
import { Settings2 } from "lucide-react";
import type { CreativeCanvasNode } from "./creativeCanvasApi";
import type { ImageGenerationModelCatalogEntry, ImageGenerationProviderEntry } from "./imageGenerationApi";
import "./creativeCanvasGenerationSettings.css";

type CanvasParams = NonNullable<CreativeCanvasNode["params"]>;
type ProviderCredential = Pick<ImageGenerationProviderEntry, "providerId" | "credentialState"> & Partial<Pick<ImageGenerationProviderEntry, "protocol" | "defaultModelId" | "models">>;
export type GenerationCapabilityInput = {
  params?: CanvasParams; catalog?: ImageGenerationModelCatalogEntry[]; providers?: ProviderCredential[];
  configurationVersion?: string | null; activeProviderId?: string | null; referenceCount: number;
  missingImageReference?: { index: number; sourceLabel: string };
};
export type GenerationCapabilityStatus = {
  runnable: boolean; reason?: string; notice?: string; legacy?: boolean; model?: ImageGenerationModelCatalogEntry; size: string;
};

function effectiveSize(params?: CanvasParams) {
  return params?.size || `${params?.width ?? 1024}x${params?.height ?? 1024}`;
}
function effectiveCount(params?: CanvasParams) { return params?.count ?? 1; }
function ratioStyle(value: string) {
  const [width = 1, height = 1] = value.split(":").map(Number);
  return { width: `${16 * Math.min(width / height, 1)}px`, height: `${16 * Math.min(height / width, 1)}px` };
}
function sizeMatchesRatio(size: string, ratio?: string) {
  if (!ratio) return true;
  const sizeParts = /^(\d+)x(\d+)$/.exec(size); const ratioParts = /^(\d+):(\d+)$/.exec(ratio);
  if (!sizeParts || !ratioParts) return true;
  const [width, height, ratioWidth, ratioHeight] = [Number(sizeParts[1]), Number(sizeParts[2]), Number(ratioParts[1]), Number(ratioParts[2])];
  return width > 0 && height > 0 && ratioWidth > 0 && ratioHeight > 0 && width * ratioHeight === height * ratioWidth;
}
function validCanvasSize(size: string) {
  const parts = /^(\d+)x(\d+)$/.exec(size);
  if (!parts) return false;
  const width = Number(parts[1]); const height = Number(parts[2]);
  return Number.isInteger(width) && Number.isInteger(height) && width >= 64 && height >= 64 && width <= 8192 && height <= 8192 && width * height <= 64_000_000;
}
function validRatio(value: string) { return /^[1-9]\d{0,3}:[1-9]\d{0,3}$/.test(value); }
function compatibleSize(ratio: string, sizes: string[], preferred?: string) {
  return sizes.find(size => size === preferred && sizeMatchesRatio(size, ratio)) ?? sizes.find(size => sizeMatchesRatio(size, ratio));
}
function effectiveReferenceLimit(model: ImageGenerationModelCatalogEntry, provider?: ProviderCredential) {
  const declared = typeof model.capabilities.maxReferences === "number" ? model.capabilities.maxReferences : Infinity;
  return Math.min(declared, provider?.protocol === "openai-compatible" ? 1 : Infinity);
}

/** Matches the server's stable provider/model validation before enabling Run. */
export function generationCapabilityStatus(input: GenerationCapabilityInput): GenerationCapabilityStatus {
  const { params, catalog = [], providers = [], configurationVersion, referenceCount, missingImageReference } = input;
  const size = effectiveSize(params);
  const count = effectiveCount(params);
  if (missingImageReference) return { runnable: false, size, reason: `第 ${missingImageReference.index} 个图片参考（${missingImageReference.sourceLabel}）不可用，请恢复素材或移除引用。` };
  if (!Number.isInteger(count) || count < 1 || count > 15) return { runnable: false, size, reason: "生成数量必须为 1–15 张。" };
  if (!params?.providerId || !params.modelId) {
    if (!params?.model) return { runnable: false, size, reason: "请选择图片生成模型。" };
    // The legacy backend always resolves `model` through its stable default account.
    // activeProviderId belongs only to the new providerId/modelId selection path.
    const provider = providers.find(item => item.providerId === "default");
    if (!provider?.defaultModelId || provider.credentialState !== "configured" || !provider.models?.some(item => item.modelId === params.model)) return { runnable: false, size, reason: "旧模型当前不可用，请在生成服务设置中检查默认模型、白名单和密钥。" };
    if (!validCanvasSize(size)) return { runnable: false, size, reason: "图片尺寸必须为 64–8192 像素且总像素不超过 6400 万。" };
    if (params.ratio && !validRatio(params.ratio)) return { runnable: false, size, reason: "图片比例格式无效，请明确选择兼容比例。" };
    if (provider.protocol === "openai-compatible" && referenceCount > 1) return { runnable: false, size, reason: "当前旧协议最多支持 1 张图片参考。" };
    if (!sizeMatchesRatio(size, params.ratio)) return { runnable: false, size, reason: `当前尺寸 ${size} 与比例 ${params.ratio} 不一致，请明确选择兼容尺寸或比例。` };
    return { runnable: true, legacy: true, size, notice: "旧模型，能力未核验，仅按协议及应用边界运行。" };
  }
  if (params.configurationVersion && configurationVersion && params.configurationVersion !== configurationVersion) return { runnable: false, size, reason: "生成服务配置已更新，请重新选择模型。" };
  const model = catalog.find(item => item.mediaType === "image" && item.providerId === params.providerId && item.modelId === params.modelId);
  if (!model || !model.configured) return { runnable: false, size, reason: "所选模型已不在当前生成服务配置中，请重新选择模型。" };
  const provider = providers.find(item => item.providerId === params.providerId);
  if (!provider || provider.credentialState !== "configured") return { runnable: false, size, model, reason: "请先为所选供应商配置 API Key。" };
  if (model.capabilitySource !== "user-configured" || !model.capabilities || Object.keys(model.capabilities).length === 0) return { runnable: false, size, model, reason: "所选模型的能力未知，无法验证尺寸、比例和图片参考。" };
  if (!validCanvasSize(size)) return { runnable: false, size, model, reason: "图片尺寸必须为 64–8192 像素且总像素不超过 6400 万。" };
  if (params.ratio && !validRatio(params.ratio)) return { runnable: false, size, model, reason: "图片比例格式无效，请明确选择兼容比例。" };
  const sizes = model.capabilities.imageSizes;
  if (Array.isArray(sizes) && !sizes.includes(size)) return { runnable: false, size, model, reason: `当前尺寸 ${size} 不受所选模型支持，请明确选择兼容尺寸。` };
  const ratios = model.capabilities.imageRatios;
  if (params.ratio && Array.isArray(ratios) && !ratios.includes(params.ratio)) return { runnable: false, size, model, reason: `当前比例 ${params.ratio} 不受所选模型支持，请明确选择兼容比例。` };
  if (!sizeMatchesRatio(size, params.ratio)) return { runnable: false, size, model, reason: `当前尺寸 ${size} 与比例 ${params.ratio} 不一致，请明确选择兼容尺寸或比例。` };
  const limit = effectiveReferenceLimit(model, provider);
  if (Number.isFinite(limit) && referenceCount > limit) return { runnable: false, size, model, reason: `图片参考数量超过所选模型上限（${limit} 张）。` };
  return { runnable: true, size, model };
}

type Props = GenerationCapabilityInput & { onEdit: (params: Partial<CanvasParams>) => void };

export function CreativeCanvasGenerationSettings({ params, catalog, providers, configurationVersion, activeProviderId, referenceCount, missingImageReference, onEdit }: Props) {
  const [open, setOpen] = React.useState(false);
  const [position, setPosition] = React.useState({ left: 16, top: 120 });
  const trigger = React.useRef<HTMLButtonElement>(null);
  const panel = React.useRef<HTMLDivElement>(null);
  const status = generationCapabilityStatus({ params, catalog, providers, configurationVersion, activeProviderId, referenceCount, missingImageReference });
  const model = status.model;
  const provider = model ? providers?.find(item => item.providerId === model.providerId) : undefined;
  const referenceLimit = model ? effectiveReferenceLimit(model, provider) : Infinity;
  const sizes = (model?.capabilities.imageSizes ?? []).filter(validCanvasSize);
  const ratios = (model?.capabilities.imageRatios ?? []).filter(validRatio);

  React.useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(300, window.innerWidth - 32);
      const height = panel.current?.offsetHeight || 150;
      setPosition({ left: Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)), top: Math.max(120, rect.top - height - 10) });
    };
    const outside = (event: PointerEvent) => { if (!trigger.current?.contains(event.target as globalThis.Node) && !panel.current?.contains(event.target as globalThis.Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); setOpen(false); trigger.current?.focus(); } };
    update(); window.addEventListener("resize", update); window.addEventListener("scroll", update, true); window.document.addEventListener("pointerdown", outside); window.document.addEventListener("keydown", escape, true);
    return () => { window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); window.document.removeEventListener("pointerdown", outside); window.document.removeEventListener("keydown", escape, true); };
  }, [open]);

  const host = trigger.current?.closest(".creative-canvas");
  const summary = `${params?.ratio || "未选比例"} · ${status.size} · ${effectiveCount(params)}张`;
  const panelContent = <div ref={panel} role="group" aria-label="图片尺寸与比例" className="creative-node__parameter-popover creative-node__generation-settings nodrag nopan nowheel" style={position}>
    {model ? <>
      <div className="creative-node__setting-label">分辨率</div><div className="creative-node__size-options" aria-label="图片分辨率">{sizes.length ? sizes.map(size => <button key={size} type="button" aria-label={size} aria-pressed={status.size === size} disabled={Boolean(params?.ratio && !sizeMatchesRatio(size, params.ratio))} title={params?.ratio && !sizeMatchesRatio(size, params.ratio) ? "请先选择与该尺寸一致的比例" : undefined} onClick={() => onEdit({ size })}>{size}</button>) : <span>未声明可选尺寸</span>}</div>
      <div className="creative-node__setting-label">比例</div><div className="creative-node__ratio-options" role="group" aria-label="图片比例">{ratios.length ? ratios.map(ratio => { const size = compatibleSize(ratio, sizes, status.size); return <button key={ratio} type="button" aria-label={`比例 ${ratio}`} aria-pressed={params?.ratio === ratio} disabled={!size} title={!size ? "目录中没有与该比例一致的尺寸" : undefined} onClick={() => size && onEdit({ ratio, size })}><span className="creative-node__ratio-content"><span className="creative-node__ratio-shape" style={ratioStyle(ratio)} aria-hidden="true" /><span>{ratio}</span></span></button>; }) : <span>未声明可选比例</span>}</div>
      <div className="creative-node__setting-label creative-node__count-label">生成数量<select aria-label="生成数量" value={effectiveCount(params)} onChange={event => onEdit({ count: Number(event.target.value) })}>{Array.from({ length: 15 }, (_, index) => index + 1).map(count => <option key={count} value={count}>{count} 张</option>)}</select></div>
      <p className="creative-node__capability-note">{Number.isFinite(referenceLimit) ? `最多 ${referenceLimit} 张图片参考` : "图片参考上限未声明，提交时由服务端复核。"}</p>
    </> : <p className="creative-node__capability-note">{status.reason ?? status.notice}</p>}
  </div>;
  return <><button ref={trigger} type="button" className="creative-node__parameters" aria-label="图片生成设置" aria-expanded={open} onClick={() => setOpen(value => !value)}><span className="creative-node__ratio-shape" style={ratioStyle(params?.ratio || "1:1")} aria-hidden="true" /><span>{summary}</span><Settings2 size={14} /></button>
    {(status.reason || status.notice) && <p className="creative-node__capability-note">{status.reason ?? status.notice}</p>}
    {open && host && createPortal(panelContent, host)}
  </>;
}
