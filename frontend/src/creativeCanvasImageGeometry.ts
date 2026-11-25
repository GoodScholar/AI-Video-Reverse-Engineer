/**
 * 图片几何导出工具。
 *
 * 交互与像素切分规则改编自 basketikun/infinite-canvas@dab19adc
 * 的 web/src/lib/canvas/canvas-image-data.ts（MIT，Copyright 2026 basketikun）。
 * 这里保留项目素材安装前所需的浏览器 Canvas 导出边界；不修改源文件。
 */

export type ImageSize = { width: number; height: number };
export type ImageCropRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};
export type ImageSplitParams = {
  rows: number;
  columns: number;
  horizontalLines?: number[];
  verticalLines?: number[];
};
export type ImageSplitPiece = ImageSize & {
  row: number;
  column: number;
  x: number;
  y: number;
};
export type ImageGeometryKind = "crop" | "split";
export type ImageGeometryOperation = {
  sourceAssetId: string;
  operation: ImageGeometryKind;
  params: ImageCropRect | ImageSplitParams;
  operationId: string;
  index: number;
  count: number;
  createdAt?: string;
};

const minCropSize = 0.06;

export function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function normalizeCrop(
  crop: ImageCropRect,
  minSize = minCropSize,
): ImageCropRect {
  const safeMinSize = clamp(finite(minSize, minCropSize), Number.EPSILON, 1);
  const width = clamp(finite(crop.width, safeMinSize), safeMinSize, 1);
  const height = clamp(finite(crop.height, safeMinSize), safeMinSize, 1);
  return {
    x: clamp(finite(crop.x, 0), 0, 1 - width),
    y: clamp(finite(crop.y, 0), 0, 1 - height),
    width,
    height,
  };
}

export function buildSplitPieces(
  image: ImageSize,
  params: ImageSplitParams,
): ImageSplitPiece[] {
  const width = Math.max(1, Math.floor(image.width));
  const height = Math.max(1, Math.floor(image.height));
  const xCuts = buildSplitCuts(params.verticalLines, width, params.columns);
  const yCuts = buildSplitCuts(params.horizontalLines, height, params.rows);
  const pieces: ImageSplitPiece[] = [];
  for (let row = 0; row < yCuts.length - 1; row += 1) {
    for (let column = 0; column < xCuts.length - 1; column += 1) {
      const x = xCuts[column];
      const y = yCuts[row];
      pieces.push({
        row,
        column,
        x,
        y,
        width: xCuts[column + 1] - x,
        height: yCuts[row + 1] - y,
      });
    }
  }
  return pieces;
}

export function operationLabel(
  sourceName: string,
  kind: ImageGeometryKind,
  piece?: Pick<ImageSplitPiece, "row" | "column">,
) {
  const base = sourceName.replace(/\.[^.]+$/, "") || "图片";
  if (kind === "split" && piece)
    return `${base}-切分-r${piece.row + 1}-c${piece.column + 1}.png`;
  return `${base}-${kind === "crop" ? "裁切" : "切分"}.png`;
}

export async function renderCropFile(
  sourceUrl: string,
  sourceName: string,
  crop: ImageCropRect,
): Promise<File> {
  const image = await loadImage(sourceUrl);
  const normalized = normalizeCrop(crop, Number.EPSILON);
  const x = Math.floor(normalized.x * image.naturalWidth);
  const y = Math.floor(normalized.y * image.naturalHeight);
  const width = Math.max(1, Math.ceil(normalized.width * image.naturalWidth));
  const height = Math.max(
    1,
    Math.ceil(normalized.height * image.naturalHeight),
  );
  return renderPieceFile(
    image,
    { row: 0, column: 0, x, y, width, height },
    operationLabel(sourceName, "crop"),
  );
}

export async function renderSplitFiles(
  sourceUrl: string,
  sourceName: string,
  params: ImageSplitParams,
): Promise<Array<{ file: File; piece: ImageSplitPiece }>> {
  const image = await loadImage(sourceUrl);
  const pieces = buildSplitPieces(
    { width: image.naturalWidth, height: image.naturalHeight },
    params,
  );
  return Promise.all(
    pieces.map(async (piece) => ({
      piece,
      file: await renderPieceFile(
        image,
        piece,
        operationLabel(sourceName, "split", piece),
      ),
    })),
  );
}

async function renderPieceFile(
  image: HTMLImageElement,
  piece: Omit<ImageSplitPiece, "row" | "column"> &
    Partial<Pick<ImageSplitPiece, "row" | "column">>,
  name: string,
) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, piece.width);
  canvas.height = Math.max(1, piece.height);
  const context = canvas.getContext("2d");
  if (!context)
    throw new Error("浏览器不支持图片导出，请使用支持 Canvas 的浏览器。");
  context.drawImage(
    image,
    piece.x,
    piece.y,
    piece.width,
    piece.height,
    0,
    0,
    canvas.width,
    canvas.height,
  );
  const blob = await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob(
      (value) =>
        value ? resolve(value) : reject(new Error("图片导出失败，请重试。")),
      "image/png",
    ),
  );
  return new File([blob], name, { type: "image/png" });
}

function buildSplitCuts(
  lines: number[] | undefined,
  size: number,
  count: number,
) {
  const safeCount = Math.max(1, Math.floor(finite(count, 1)));
  if (!lines?.length)
    return Array.from({ length: safeCount + 1 }, (_, index) =>
      Math.floor((index * size) / safeCount),
    );
  const inner = [
    ...new Set(
      lines
        .map((line) => Math.round(finite(line, -1) * size))
        .filter((line) => line > 0 && line < size),
    ),
  ].sort((a, b) => a - b);
  return [0, ...inner, size];
}

function finite(value: number, fallback: number) {
  return Number.isFinite(value) ? value : fallback;
}

function loadImage(sourceUrl: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("无法读取图片，原素材保持不变。"));
    image.src = sourceUrl;
  });
}
