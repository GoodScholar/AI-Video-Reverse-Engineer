export type AspectMode = "21:9" | "16:9" | "4:3" | "1:1" | "3:4" | "9:16" | "smart";

export type AspectResolution = { resolvedAspect: string; width: number; height: number; reason: string };

export const ASPECT_OPTIONS: Array<{ value: AspectMode; label: string; ratio?: number }> = [
  { value: "21:9", label: "21:9", ratio: 21 / 9 },
  { value: "16:9", label: "16:9", ratio: 16 / 9 },
  { value: "4:3", label: "4:3", ratio: 4 / 3 },
  { value: "1:1", label: "1:1", ratio: 1 },
  { value: "3:4", label: "3:4", ratio: 3 / 4 },
  { value: "9:16", label: "9:16", ratio: 9 / 16 },
  { value: "smart", label: "智能" },
];

export const PRODUCT_ASPECT_PRESETS: Record<Exclude<AspectMode, "smart">, [number, number]> = {
  "21:9": [1680, 720], "16:9": [1280, 720], "4:3": [960, 720],
  "1:1": [720, 720], "3:4": [720, 960], "9:16": [720, 1280],
};

export const REPRODUCTION_ASPECT_PRESETS: Record<Exclude<AspectMode, "smart">, [number, number]> = {
  "21:9": [1120, 480], "16:9": [1024, 576], "4:3": [960, 720],
  "1:1": [720, 720], "3:4": [720, 960], "9:16": [576, 1024],
};

export function ratioLabel(width: number, height: number): string {
  const divisor = gcd(Math.round(width), Math.round(height));
  return `${Math.round(width) / divisor}:${Math.round(height) / divisor}`;
}

export function resolveProductAspect(mode: AspectMode, assets: Array<{ id?: string; width?: number; height?: number }>): AspectResolution {
  if (mode !== "smart") {
    const [width, height] = PRODUCT_ASPECT_PRESETS[mode];
    return { resolvedAspect: mode, width, height, reason: `使用项目选择的 ${mode} 比例。` };
  }
  const dimensions = [...assets].sort((left, right) => (left.id ?? "").localeCompare(right.id ?? "") ||
    (left.width ?? 0) - (right.width ?? 0) || (left.height ?? 0) - (right.height ?? 0))
    .filter((asset): asset is { id?: string; width: number; height: number } =>
      Number.isFinite(asset.width) && Number.isFinite(asset.height) && (asset.width ?? 0) > 0 && (asset.height ?? 0) > 0);
  if (!dimensions.length) return { resolvedAspect: "9:16", width: 720, height: 1280, reason: "素材没有可读尺寸，智能比例回退到 9:16。" };
  const ratios = dimensions.map((asset) => asset.width / asset.height);
  if (Math.max(...ratios) / Math.min(...ratios) > 1.03) {
    return { resolvedAspect: "9:16", width: 720, height: 1280, reason: "素材比例不一致，智能比例回退到 9:16。" };
  }
  const ratio = ratios[0];
  const closest = (Object.keys(PRODUCT_ASPECT_PRESETS) as Array<Exclude<AspectMode, "smart">>)
    .reduce((best, candidate) => Math.abs(PRODUCT_ASPECT_PRESETS[candidate][0] / PRODUCT_ASPECT_PRESETS[candidate][1] - ratio)
      < Math.abs(PRODUCT_ASPECT_PRESETS[best][0] / PRODUCT_ASPECT_PRESETS[best][1] - ratio) ? candidate : best, "9:16");
  const preset = PRODUCT_ASPECT_PRESETS[closest];
  if (Math.abs(preset[0] / preset[1] - ratio) / ratio <= 0.02) {
    return { resolvedAspect: closest, width: preset[0], height: preset[1], reason: "智能比例沿用所选主视觉素材的可读比例。" };
  }
  let width = ratio >= 1 ? even(720 * ratio) : 720;
  let height = ratio >= 1 ? 720 : even(720 / ratio);
  if (Math.max(width, height) > 1920) {
    const scale = 1920 / Math.max(width, height);
    width = even(width * scale); height = even(height * scale);
  }
  return { resolvedAspect: ratioLabel(width, height), width, height, reason: "智能比例沿用所选主视觉素材的可读比例。" };
}

export function resolveReproductionAspect(mode: AspectMode, current: { width: number; height: number }, source?: { width?: number; height?: number } | null): AspectResolution {
  if (mode !== "smart") {
    const [width, height] = REPRODUCTION_ASPECT_PRESETS[mode];
    return { resolvedAspect: mode, width, height, reason: `使用复刻方案选择的 ${mode} 比例。` };
  }
  if (!source?.width || !source.height) {
    return { resolvedAspect: ratioLabel(current.width, current.height), width: current.width, height: current.height,
      reason: "参考素材尺寸不可读，保留当前智能输出尺寸。" };
  }
  const scale = Math.min(480 / Math.min(source.width, source.height), 1280 / Math.max(source.width, source.height));
  const width = Math.max(256, Math.round(source.width * scale / 16) * 16);
  const height = Math.max(256, Math.round(source.height * scale / 16) * 16);
  return { resolvedAspect: ratioLabel(width, height), width, height, reason: "智能比例沿用参考素材比例，并按生成模板对齐尺寸。" };
}

function gcd(left: number, right: number): number {
  let a = Math.abs(left); let b = Math.abs(right);
  while (b) [a, b] = [b, a % b];
  return a || 1;
}

function even(value: number): number {
  const rounded = Math.round(value);
  return Math.max(64, rounded - rounded % 2);
}
