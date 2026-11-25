/**
 * 图片裁切与切分编辑器，交互结构与坐标算法改编自
 * basketikun/infinite-canvas@dab19adc 的 crop/split dialog 与 viewport hook。
 * MIT Copyright (c) 2026 basketikun，完整许可证见 vendor 参考项目 LICENSE。
 */
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  uploadPreproductionAsset,
  type PreproductionAsset,
} from "./preproductionApi";
import {
  buildSplitPieces,
  clamp,
  normalizeCrop,
  operationLabel,
  renderCropFile,
  renderSplitFiles,
  type ImageCropRect,
  type ImageGeometryOperation,
  type ImageSplitParams,
} from "./creativeCanvasImageGeometry";
import { useCreativeCanvasImageEditorViewport } from "./useCreativeCanvasImageEditorViewport";
import "./creativeCanvasImageEditor.css";

type Source = Pick<
  PreproductionAsset,
  "id" | "name" | "url" | "kind" | "width" | "height"
>;
type Created = {
  asset: PreproductionAsset;
  derivation: ImageGeometryOperation;
};
type Mode = "crop" | "split";
type Handle = "n" | "e" | "s" | "w" | "ne" | "nw" | "se" | "sw";
const defaultCrop = { x: 0.12, y: 0.12, width: 0.76, height: 0.76 };
const defaultSplit: ImageSplitParams = {
  rows: 2,
  columns: 2,
  horizontalLines: [0.5],
  verticalLines: [0.5],
};
const handles: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

export function CreativeCanvasImageEditor({
  open,
  projectId,
  source,
  initialMode = "crop",
  onClose,
  onAssetCreated,
  onBatchComplete,
  onError,
}: {
  open: boolean;
  projectId: string;
  source: Source;
  initialMode?: Mode;
  onClose: () => void;
  onAssetCreated: (created: Created) => void;
  onBatchComplete?: (result: {
    succeeded: Created[];
    failed: Array<{ index: number; error: Error }>;
  }) => void;
  onError?: (error: Error) => void;
}) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [image, setImage] = useState<{ width: number; height: number } | null>(
    source.width && source.height
      ? { width: source.width, height: source.height }
      : null,
  );
  const [crop, setCrop] = useState<ImageCropRect>(defaultCrop);
  const [ratio, setRatio] = useState("free");
  const [fixedRatio, setFixedRatio] = useState<number | null>(null);
  const [split, setSplit] = useState<ImageSplitParams>(defaultSplit);
  const [active, setActive] = useState<{
    axis: "horizontal" | "vertical";
    index: number;
  }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const splitHistory = useRef<ImageSplitParams[]>([]);
  const splitRedo = useRef<ImageSplitParams[]>([]);
  const [historySize, setHistorySize] = useState(0);
  const [redoSize, setRedoSize] = useState(0);
  const operationIds = useRef<
    Partial<Record<Mode, { key: string; id: string }>>
  >({});
  const dragAbort = useRef<AbortController>();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const sessionRef = useRef(0);
  const closingRef = useRef(false);
  const identityRef = useRef({ open, projectId, sourceId: source.id });
  identityRef.current = { open, projectId, sourceId: source.id };
  const viewport = useCreativeCanvasImageEditorViewport(image, open);
  const rows = (split.horizontalLines?.length ?? 0) + 1;
  const columns = (split.verticalLines?.length ?? 0) + 1;
  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setCrop(defaultCrop);
    setSplit(defaultSplit);
    setRatio("free");
    setFixedRatio(null);
    setError("");
    splitHistory.current = [];
    splitRedo.current = [];
    setHistorySize(0);
    setRedoSize(0);
    operationIds.current = {};
    closingRef.current = false;
  }, [initialMode, open, source.id]);
  useEffect(() => {
    if (!open) return;
    sessionRef.current += 1;
    const session = sessionRef.current;
    return () => {
      if (sessionRef.current === session) sessionRef.current += 1;
    };
  }, [open, projectId, source.id]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    dialog.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const target = new Image();
    target.onload = () =>
      setImage({ width: target.naturalWidth, height: target.naturalHeight });
    target.onerror = () => setError("无法读取图片，原素材保持不变。");
    target.src = source.url;
  }, [open, source.url]);
  useEffect(() => () => dragAbort.current?.abort(), []);
  const closeEditor = () => {
    if (closingRef.current) return;
    closingRef.current = true;
    dragAbort.current?.abort();
    sessionRef.current += 1;
    onClose();
  };
  const isCurrent = (session: number) =>
    sessionRef.current === session &&
    identityRef.current.open &&
    identityRef.current.projectId === projectId &&
    identityRef.current.sourceId === source.id;
  const cropSize = image
    ? {
        width: Math.max(1, Math.round(crop.width * image.width)),
        height: Math.max(1, Math.round(crop.height * image.height)),
      }
    : undefined;
  const pushSplit = () => {
    if (busy) return;
    splitHistory.current.push(cloneSplit(split));
    if (splitHistory.current.length > 50) splitHistory.current.shift();
    splitRedo.current = [];
    setHistorySize(splitHistory.current.length);
    setRedoSize(0);
    operationIds.current.split = undefined;
  };
  const setLines = (axis: "horizontal" | "vertical", lines: number[]) => {
    setSplit((current) => ({
      ...current,
      [axis === "horizontal" ? "horizontalLines" : "verticalLines"]: lines,
      rows: axis === "horizontal" ? lines.length + 1 : current.rows,
      columns: axis === "vertical" ? lines.length + 1 : current.columns,
    }));
  };
  const undo = () => {
    if (busy) return;
    const previous = splitHistory.current.pop();
    if (!previous) return;
    splitRedo.current.push(cloneSplit(split));
    setSplit(previous);
    setActive(undefined);
    setHistorySize(splitHistory.current.length);
    setRedoSize(splitRedo.current.length);
    operationIds.current.split = undefined;
  };
  const redo = () => {
    if (busy) return;
    const next = splitRedo.current.pop();
    if (!next) return;
    splitHistory.current.push(cloneSplit(split));
    setSplit(next);
    setActive(undefined);
    setHistorySize(splitHistory.current.length);
    setRedoSize(splitRedo.current.length);
    operationIds.current.split = undefined;
  };
  const changeGrid = (axis: "horizontal" | "vertical", value: number) => {
    if (busy) return;
    pushSplit();
    const count = clamp(Math.round(value || 1), 1, 12);
    setLines(axis, gridLines(count));
    setActive(undefined);
  };
  const addLine = (axis: "horizontal" | "vertical") => {
    if (busy) return;
    pushSplit();
    const old =
      axis === "horizontal"
        ? (split.horizontalLines ?? [])
        : (split.verticalLines ?? []);
    const line = largestGapMidpoint(old);
    const lines = [...old, line].sort((a, b) => a - b);
    setLines(axis, lines);
    setActive({ axis, index: lines.indexOf(line) });
  };
  const deleteLine = () => {
    if (busy || !active) return;
    pushSplit();
    const old =
      active.axis === "horizontal"
        ? (split.horizontalLines ?? [])
        : (split.verticalLines ?? []);
    setLines(
      active.axis,
      old.filter((_, index) => index !== active.index),
    );
    setActive(undefined);
  };
  const resetLines = () => {
    if (busy) return;
    pushSplit();
    setSplit((current) => ({
      ...current,
      horizontalLines: gridLines(rows),
      verticalLines: gridLines(columns),
    }));
    setActive(undefined);
  };
  const dragCrop = (
    move: boolean,
    event: ReactPointerEvent,
    handle?: Handle,
  ) => {
    if (busy) return;
    const rect = viewport.stageRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    event.stopPropagation();
    const initial = crop;
    dragAbort.current?.abort();
    const controller = new AbortController();
    dragAbort.current = controller;
    const pointerMove = (next: PointerEvent) => {
      const dx = (next.clientX - event.clientX) / rect.width;
      const dy = (next.clientY - event.clientY) / rect.height;
      setCrop(
        move
          ? moveCrop(initial, dx, dy)
          : resizeCrop(
              initial,
              dx,
              dy,
              handle ?? "se",
              resolveRatio(ratio, image, fixedRatio),
              rect,
            ),
      );
    };
    document.addEventListener("pointermove", pointerMove, {
      signal: controller.signal,
    });
    document.addEventListener("pointerup", () => controller.abort(), {
      signal: controller.signal,
    });
  };
  const dragLine = (
    axis: "horizontal" | "vertical",
    index: number,
    event: ReactPointerEvent,
  ) => {
    if (busy) return;
    const rect = viewport.stageRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    pushSplit();
    setActive({ axis, index });
    dragAbort.current?.abort();
    const controller = new AbortController();
    dragAbort.current = controller;
    const move = (next: PointerEvent) => {
      const old =
        axis === "horizontal"
          ? (split.horizontalLines ?? [])
          : (split.verticalLines ?? []);
      const lines = [...old];
      lines[index] = clamp(
        axis === "horizontal"
          ? (next.clientY - rect.top) / rect.height
          : (next.clientX - rect.left) / rect.width,
        (lines[index - 1] ?? 0) + 0.01,
        (lines[index + 1] ?? 1) - 0.01,
      );
      setLines(axis, lines);
    };
    document.addEventListener("pointermove", move, {
      signal: controller.signal,
    });
    document.addEventListener("pointerup", () => controller.abort(), {
      signal: controller.signal,
    });
  };
  const install = async (
    file: File,
    derivation: Omit<ImageGeometryOperation, "createdAt">,
    session: number,
  ): Promise<Created> => {
    const workspace = await uploadPreproductionAsset(
      projectId,
      "reference",
      file,
      derivation,
    );
    if (!isCurrent(session))
      throw new Error("图片编辑已关闭或已切换来源，未将结果写入当前画布。");
    const asset = workspace.assets.find(
      (item) =>
        item.derivation?.operationId === derivation.operationId &&
        item.derivation.index === derivation.index,
    );
    if (!asset) throw new Error("服务没有返回已安装的派生素材。");
    const created = { asset, derivation: asset.derivation ?? derivation };
    onAssetCreated(created);
    return created;
  };
  const confirmCrop = async () => {
    if (busy) return;
    const session = sessionRef.current;
    setBusy(true);
    setError("");
    try {
      const normalized = normalizeCrop(
        crop,
        ratio === "free" ? undefined : Number.EPSILON,
      );
      const file = await renderCropFile(source.url, source.name, normalized);
      if (!isCurrent(session)) return;
      const key = derivationKey(source.id, "crop", normalized);
      const id =
        operationIds.current.crop?.key === key
          ? operationIds.current.crop.id
          : crypto.randomUUID();
      operationIds.current.crop = { key, id };
      const created = await install(
        file,
        {
          sourceAssetId: source.id,
          operation: "crop",
          params: normalized,
          operationId: id,
          index: 0,
          count: 1,
        },
        session,
      );
      if (!isCurrent(session)) return;
      onBatchComplete?.({ succeeded: [created], failed: [] });
      closeEditor();
    } catch (reason) {
      if (!isCurrent(session)) return;
      const next = asError(reason);
      setError(next.message);
      onError?.(next);
    } finally {
      if (sessionRef.current === session) setBusy(false);
    }
  };
  const confirmSplit = async () => {
    if (busy) return;
    const session = sessionRef.current;
    setBusy(true);
    setError("");
    const params = {
      ...split,
      rows,
      columns,
      horizontalLines: [...(split.horizontalLines ?? [])],
      verticalLines: [...(split.verticalLines ?? [])],
    };
    const key = derivationKey(source.id, "split", params);
    const id =
      operationIds.current.split?.key === key
        ? operationIds.current.split.id
        : crypto.randomUUID();
    operationIds.current.split = { key, id };
    const succeeded: Created[] = [];
    const failed: Array<{ index: number; error: Error }> = [];
    try {
      const files = await renderSplitFiles(source.url, source.name, params);
      for (const { file, piece } of files) {
        if (!isCurrent(session)) return;
        try {
          succeeded.push(
            await install(
              file,
              {
                sourceAssetId: source.id,
                operation: "split",
                params,
                operationId: id,
                index: piece.row * columns + piece.column,
                count: files.length,
              },
              session,
            ),
          );
        } catch (reason) {
          if (!isCurrent(session)) return;
          failed.push({
            index: piece.row * columns + piece.column,
            error: asError(reason),
          });
        }
      }
      if (!isCurrent(session)) return;
      if (failed.length) {
        const next = new Error(
          `已保存 ${succeeded.length} 个切分素材，${failed.length} 个未保存；可重试同一切分。`,
        );
        setError(next.message);
        onError?.(next);
        onBatchComplete?.({ succeeded, failed });
        return;
      }
      onBatchComplete?.({ succeeded, failed });
      closeEditor();
    } catch (reason) {
      if (!isCurrent(session)) return;
      const next = asError(reason);
      setError(next.message);
      onError?.(next);
      onBatchComplete?.({
        succeeded,
        failed: [...failed, { index: -1, error: next }],
      });
    } finally {
      if (sessionRef.current === session) setBusy(false);
    }
  };
  const switchRatio = (next: string) => {
    if (busy) return;
    setRatio(next);
    const current = image
      ? (crop.width * image.width) / Math.max(1, crop.height * image.height)
      : 1;
    const ratioValue =
      next === "fixed" ? current : resolveRatio(next, image, null);
    setFixedRatio(next === "fixed" ? current : null);
    if (ratioValue && image)
      setCrop((currentCrop) => fitCropToRatio(currentCrop, ratioValue, image));
  };
  const pieces = useMemo(
    () => (image ? buildSplitPieces(image, { ...split, rows, columns }) : []),
    [columns, image, rows, split],
  );
  if (!open) return null;
  return (
    <dialog
      ref={dialogRef}
      className="creative-image-editor"
      aria-label="图片几何编辑"
      onCancel={(event) => {
        event.preventDefault();
        closeEditor();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          closeEditor();
        }
      }}
    >
      <div className="creative-image-editor__panel">
        <header>
          <div>
            <strong>图片编辑</strong>
            <span>来源：{source.name}</span>
          </div>
          <button type="button" aria-label="关闭图片编辑" onClick={closeEditor}>
            ×
          </button>
        </header>
        <nav aria-label="图片编辑模式">
          <button
            type="button"
            disabled={busy}
            aria-pressed={mode === "crop"}
            onClick={() => setMode("crop")}
          >
            裁切
          </button>
          <button
            type="button"
            disabled={busy}
            aria-pressed={mode === "split"}
            onClick={() => setMode("split")}
          >
            切分
          </button>
        </nav>
        <div
          className="creative-image-editor__viewport"
          ref={viewport.viewportRef}
          {...viewport.panHandlers}
        >
          <div style={viewport.contentStyle}>
            <div
              className="creative-image-editor__stage"
              ref={viewport.stageRef}
              style={viewport.stageStyle}
            >
              <div style={viewport.mediaStyle}>
                <img src={source.url} alt="" draggable={false} />
              </div>
              {mode === "crop" ? (
                <>
                  <CropMask crop={crop} />
                  <div
                    className="creative-image-editor__crop"
                    style={cropStyle(crop)}
                    onPointerDown={(event) => dragCrop(true, event)}
                  >
                    {handles.map((handle) => (
                      <button
                        key={handle}
                        type="button"
                        disabled={busy}
                        aria-label="调整裁切区域"
                        style={handleStyle(handle)}
                        onPointerDown={(event) =>
                          dragCrop(false, event, handle)
                        }
                      />
                    ))}
                  </div>
                </>
              ) : (
                <SplitGrid
                  horizontal={split.horizontalLines ?? []}
                  vertical={split.verticalLines ?? []}
                  active={active}
                  onDrag={dragLine}
                  disabled={busy}
                />
              )}
            </div>
          </div>
        </div>
        <div className="creative-image-editor__zoom">
          <button
            type="button"
            aria-label="缩小图片"
            disabled={busy || !viewport.canZoomOut}
            onClick={viewport.zoomOut}
          >
            −
          </button>
          <button
            type="button"
            aria-label="重置缩放"
            disabled={busy}
            onClick={viewport.resetZoom}
          >
            {Math.round(viewport.zoom * 100)}%
          </button>
          <button
            type="button"
            aria-label="放大图片"
            disabled={busy || !viewport.canZoomIn}
            onClick={viewport.zoomIn}
          >
            +
          </button>
          <span>
            {viewport.panning
              ? "正在平移"
              : viewport.space
                ? "按住空格拖动画面"
                : "滚轮缩放，中键或空格拖动"}
          </span>
        </div>
        {mode === "crop" ? (
          <section className="creative-image-editor__controls">
            <div className="creative-image-editor__stats">
              <span>
                {cropSize
                  ? `${cropSize.width} × ${cropSize.height} px`
                  : "读取尺寸中…"}
              </span>
              <span>
                {image ? `原图 ${image.width} × ${image.height}` : ""}
              </span>
            </div>
            <div
              className="creative-image-editor__ratios"
              aria-label="裁切比例"
            >
              {[
                ["free", "自由比例"],
                ["fixed", "锁定当前比例"],
                ["original", "原图比例"],
                ["1:1", "1:1"],
                ["4:3", "4:3"],
                ["16:9", "16:9"],
                ["9:16", "9:16"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  disabled={busy}
                  aria-label={label}
                  aria-pressed={ratio === value}
                  onClick={() => switchRatio(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <footer>
              <button
                type="button"
                disabled={busy}
                onClick={() => setCrop(defaultCrop)}
              >
                重置
              </button>
              <button type="button" onClick={closeEditor}>
                取消
              </button>
              <button
                type="button"
                className="is-primary"
                disabled={busy}
                onClick={() => void confirmCrop()}
              >
                {busy ? "正在保存…" : "确认裁切"}
              </button>
            </footer>
          </section>
        ) : (
          <section className="creative-image-editor__split-controls">
            <div className="creative-image-editor__split-fields">
              <label>
                行
                <input
                  disabled={busy}
                  aria-label="切分行数"
                  type="number"
                  min="1"
                  max="12"
                  value={rows}
                  onChange={(event) =>
                    changeGrid("horizontal", Number(event.target.value))
                  }
                />
              </label>
              <label>
                列
                <input
                  disabled={busy}
                  aria-label="切分列数"
                  type="number"
                  min="1"
                  max="12"
                  value={columns}
                  onChange={(event) =>
                    changeGrid("vertical", Number(event.target.value))
                  }
                />
              </label>
            </div>
            <div className="creative-image-editor__actions">
              <button
                type="button"
                disabled={busy}
                aria-label="添加横向切线"
                onClick={() => addLine("horizontal")}
              >
                添加横线
              </button>
              <button
                type="button"
                disabled={busy}
                aria-label="添加纵向切线"
                onClick={() => addLine("vertical")}
              >
                添加竖线
              </button>
              <button
                type="button"
                aria-label="删除切线"
                disabled={busy || !active}
                onClick={deleteLine}
              >
                删除切线
              </button>
              <button
                type="button"
                disabled={busy}
                aria-label="重置切线"
                onClick={resetLines}
              >
                重置切线
              </button>
              <button
                type="button"
                aria-label="撤销切线"
                disabled={busy || !historySize}
                onClick={undo}
              >
                撤销
              </button>
              <button
                type="button"
                aria-label="重做切线"
                disabled={busy || !redoSize}
                onClick={redo}
              >
                重做
              </button>
            </div>
            <p>
              {pieces.length} 块 · 平均约{" "}
              {image
                ? `${Math.floor(image.width / columns)} × ${Math.floor(image.height / rows)} px`
                : "读取尺寸中"}
            </p>
            <footer>
              <button type="button" onClick={closeEditor}>
                取消
              </button>
              <button
                type="button"
                className="is-primary"
                disabled={busy}
                onClick={() => void confirmSplit()}
              >
                {busy
                  ? "正在保存…"
                  : `生成 ${pieces.length || rows * columns} 个新素材`}
              </button>
            </footer>
          </section>
        )}
        {error && (
          <p role="alert" className="creative-image-editor__error">
            {error}
          </p>
        )}
      </div>
    </dialog>
  );
}

function CropMask({ crop }: { crop: ImageCropRect }) {
  return (
    <>
      <i
        className="creative-image-editor__mask"
        style={{ top: 0, left: 0, width: "100%", height: `${crop.y * 100}%` }}
      />
      <i
        className="creative-image-editor__mask"
        style={{
          bottom: 0,
          left: 0,
          width: "100%",
          height: `${(1 - crop.y - crop.height) * 100}%`,
        }}
      />
      <i
        className="creative-image-editor__mask"
        style={{
          top: `${crop.y * 100}%`,
          left: 0,
          width: `${crop.x * 100}%`,
          height: `${crop.height * 100}%`,
        }}
      />
      <i
        className="creative-image-editor__mask"
        style={{
          top: `${crop.y * 100}%`,
          right: 0,
          width: `${(1 - crop.x - crop.width) * 100}%`,
          height: `${crop.height * 100}%`,
        }}
      />
    </>
  );
}
function SplitGrid({
  horizontal,
  vertical,
  active,
  disabled,
  onDrag,
}: {
  horizontal: number[];
  vertical: number[];
  active?: { axis: "horizontal" | "vertical"; index: number };
  disabled: boolean;
  onDrag: (
    axis: "horizontal" | "vertical",
    index: number,
    event: ReactPointerEvent,
  ) => void;
}) {
  return (
    <div className="creative-image-editor__grid">
      {vertical.map((line, index) => (
        <button
          key={`v${index}`}
          type="button"
          disabled={disabled}
          aria-label={`纵向切线 ${index + 1}`}
          className={
            active?.axis === "vertical" && active.index === index
              ? "is-active"
              : ""
          }
          style={{ left: `${line * 100}%` }}
          onPointerDown={(event) => onDrag("vertical", index, event)}
        />
      ))}
      {horizontal.map((line, index) => (
        <button
          key={`h${index}`}
          type="button"
          disabled={disabled}
          aria-label={`横向切线 ${index + 1}`}
          className={
            active?.axis === "horizontal" && active.index === index
              ? "is-active"
              : ""
          }
          style={{ top: `${line * 100}%` }}
          onPointerDown={(event) => onDrag("horizontal", index, event)}
        />
      ))}
    </div>
  );
}
function moveCrop(crop: ImageCropRect, dx: number, dy: number) {
  return {
    ...crop,
    x: clamp(crop.x + dx, 0, 1 - crop.width),
    y: clamp(crop.y + dy, 0, 1 - crop.height),
  };
}
export function resizeCrop(
  crop: ImageCropRect,
  dx: number,
  dy: number,
  handle: Handle,
  ratio: number | null,
  box: DOMRect,
): ImageCropRect {
  let next = { ...crop };
  if (handle.includes("e")) next.width += dx;
  if (handle.includes("s")) next.height += dy;
  if (handle.includes("w")) {
    next.x += dx;
    next.width -= dx;
  }
  if (handle.includes("n")) {
    next.y += dy;
    next.height -= dy;
  }
  if (!ratio)
    return {
      width: clamp(next.width, 0.06, 1),
      height: clamp(next.height, 0.06, 1),
      x: clamp(next.x, 0, 1 - clamp(next.width, 0.06, 1)),
      y: clamp(next.y, 0, 1 - clamp(next.height, 0.06, 1)),
    };
  const normalized = (ratio * box.height) / box.width;
  const widthDriven =
    ((handle.includes("e") || handle.includes("w")) &&
      !handle.includes("n") &&
      !handle.includes("s")) ||
    (handle.length > 1 &&
      Math.abs(dx * box.width) >= Math.abs(dy * box.height));
  let width = Math.max(
    0.000001,
    widthDriven ? next.width : next.height * normalized,
  );
  let height = width / normalized;
  const minScale = Math.max(0.06 / width, 0.06 / height, 1);
  width *= minScale;
  height *= minScale;
  const anchorX = handle.includes("w") ? crop.x + crop.width : crop.x;
  const anchorY = handle.includes("n") ? crop.y + crop.height : crop.y;
  const maxWidth = handle.includes("w") ? anchorX : 1 - anchorX;
  const maxHeight = handle.includes("n") ? anchorY : 1 - anchorY;
  const maxScale = Math.min(1, maxWidth / width, maxHeight / height);
  width *= maxScale;
  height *= maxScale;
  return {
    x: handle.includes("w") ? anchorX - width : anchorX,
    y: handle.includes("n") ? anchorY - height : anchorY,
    width,
    height,
  };
}
export function derivationKey(
  sourceAssetId: string,
  operation: Mode,
  params: ImageCropRect | ImageSplitParams,
) {
  return JSON.stringify({ sourceAssetId, operation, params });
}
function resolveRatio(
  value: string,
  image: { width: number; height: number } | null,
  fixed: number | null,
) {
  if (value === "free" || !image) return null;
  if (value === "fixed") return fixed;
  if (value === "original") return image.width / image.height;
  const [w, h] = value.split(":").map(Number);
  return w > 0 && h > 0 ? w / h : null;
}
function fitCropToRatio(
  crop: ImageCropRect,
  ratio: number,
  image: { width: number; height: number },
) {
  const normalized = (ratio * image.height) / image.width;
  let width = crop.width;
  let height = width / normalized;
  if (height > crop.height) {
    height = crop.height;
    width = height * normalized;
  }
  if (width > 1) {
    width = 1;
    height = width / normalized;
  }
  if (height > 1) {
    height = 1;
    width = height * normalized;
  }
  return {
    x: clamp(crop.x + (crop.width - width) / 2, 0, 1 - width),
    y: clamp(crop.y + (crop.height - height) / 2, 0, 1 - height),
    width,
    height,
  };
}
function cropStyle(crop: ImageCropRect) {
  return {
    left: `${crop.x * 100}%`,
    top: `${crop.y * 100}%`,
    width: `${crop.width * 100}%`,
    height: `${crop.height * 100}%`,
  };
}
function handleStyle(handle: Handle) {
  return {
    top: handle.includes("n")
      ? "-6px"
      : handle.includes("s")
        ? "calc(100% - 6px)"
        : "calc(50% - 6px)",
    left: handle.includes("w")
      ? "-6px"
      : handle.includes("e")
        ? "calc(100% - 6px)"
        : "calc(50% - 6px)",
    cursor: `${handle}-resize`,
  };
}
function gridLines(count: number) {
  return Array.from(
    { length: Math.max(1, count) - 1 },
    (_, index) => (index + 1) / count,
  );
}
function largestGapMidpoint(lines: number[]) {
  const cuts = [0, ...lines, 1].sort((a, b) => a - b);
  return cuts.slice(0, -1).reduce(
    (best, start, index) =>
      cuts[index + 1] - start > best.size
        ? {
            value: start + (cuts[index + 1] - start) / 2,
            size: cuts[index + 1] - start,
          }
        : best,
    { value: 0.5, size: 0 },
  ).value;
}
function cloneSplit(value: ImageSplitParams): ImageSplitParams {
  return {
    ...value,
    horizontalLines: [...(value.horizontalLines ?? [])],
    verticalLines: [...(value.verticalLines ?? [])],
  };
}
function asError(value: unknown) {
  return value instanceof Error ? value : new Error("图片处理失败，请重试。");
}
