/*
 * Result cards follow the candidate-result interaction documented for issue 09.
 * Toonflow's fixed d86 image node only consumes its first generated asset, so
 * durable candidate identity and batch state are project-specific adapters.
 */
import { RefreshCw, Trash2, X } from "lucide-react";
import type { CreativeCanvasAsset, CreativeCanvasBatch, CreativeCanvasDocument, CreativeCanvasOutputItem, CreativeCanvasOutputRef, CreativeCanvasRun } from "./creativeCanvasApi";
import { runErrorMessage, runStatusMessage } from "./CreativeCanvasNodes";
import "./creativeCanvasHistory.css";

type Props = {
  document: CreativeCanvasDocument; runs: CreativeCanvasRun[]; assets: CreativeCanvasAsset[]; batches?: CreativeCanvasBatch[];
  onClose: () => void; onRefreshRun: (run: CreativeCanvasRun) => void;
  onSelectOutput: (runId: string, resultId: string) => void; onDeleteOutput: (runId: string, resultId: string) => void;
  onRetryOutput: (runId: string, resultId: string) => void; onCancelOutput: (runId: string, resultId: string) => void; onBranchOutput: (runId: string, resultId: string, assetId: string) => void;
  onCancelBatch: (batchId: string) => void; onPreviewAsset?: (asset: CreativeCanvasAsset) => void; onDownloadAsset?: (asset: CreativeCanvasAsset) => void;
  busy?: boolean;
};

const outputStatus: Record<CreativeCanvasOutputItem["status"], string> = { pending: "等待提交", running: "正在生成", cancel_requested: "已请求停止，远端尚未确认", installed: "已安装", failed: "失败", cancelled: "已取消", unknown: "状态未知", deleted: "已删除" };
const batchStatus: Record<CreativeCanvasBatch["status"], string> = { queued: "等待执行", running: "执行中", completed: "已完成", failed: "失败", blocked: "已阻止", cancelled: "已取消", unknown: "状态未知" };
const stepStatus: Record<CreativeCanvasBatch["steps"][number]["status"], string> = { queued: "等待执行", running: "执行中", completed: "已完成", failed: "失败", blocked: "已阻止", cancelled: "已取消", unknown: "状态未知" };

/** Old run payloads are displayed without rewriting them. New payloads always win, including an empty list. */
export function canvasRunOutputItems(run: CreativeCanvasRun): CreativeCanvasOutputItem[] {
  if (run.outputItems) return run.outputItems;
  return run.outputAssetIds.map((assetId, index) => ({ resultId: `${run.id}:legacy:${index}`, assetId, mediaType: "image", status: "installed", sourceRunId: run.id, createdAt: run.createdAt }));
}
function outputIsCurrent(ref: CreativeCanvasOutputRef | undefined, nodeOutputAssetId: string | undefined, runId: string, item: CreativeCanvasOutputItem) {
  if (ref) return ref.runId === runId && ref.resultId === item.resultId;
  return Boolean(nodeOutputAssetId && item.assetId === nodeOutputAssetId);
}
function runTitle(run: CreativeCanvasRun) {
  if (run.status === "completed") {
    const items = canvasRunOutputItems(run);
    if (run.billingUnknown || items.some(item => item.status === "unknown" || item.billingUnknown)) return "部分状态未知";
    if (items.some(item => item.status === "failed" || item.status === "cancelled" || item.cancellationRequested)) return "部分完成";
    return "已完成";
  }
  return run.status === "failed" ? "失败" : run.status === "submitting" || run.status === "queued" || run.status === "running" ? "生成中" : runStatusMessage(run) || run.status;
}

export function CreativeCanvasHistory({ document, runs, assets, batches = [], onClose, onRefreshRun, onSelectOutput, onDeleteOutput, onRetryOutput, onCancelOutput, onBranchOutput, onCancelBatch, onPreviewAsset, onDownloadAsset, busy = false }: Props) {
  const assetsById = new Map(assets.map(asset => [asset.id, asset]));
  const nodesById = new Map(document.nodes.map(node => [node.id, node]));
  return <aside className="creative-canvas__history creative-canvas-history is-open" aria-label="生成历史" aria-busy={busy}>
    <div className="creative-canvas-history__heading"><h2>生成历史</h2><button type="button" aria-label="关闭生成历史" onClick={onClose}><X size={16} /></button></div>
    {!runs.length && !batches.length && <p>还没有生成记录。保存画布后可提交图片生成。</p>}
    {runs.map(run => {
      const node = nodesById.get(run.nodeId);
      return <article key={run.id} className="creative-canvas-history__run">
        <header><strong>{runTitle(run)}</strong><time>{new Date(run.createdAt).toLocaleString("zh-CN")}</time></header>
        {run.status === "unknown" && <button type="button" disabled={busy} onClick={() => onRefreshRun(run)}><RefreshCw size={13} />刷新状态</button>}
        {run.billingUnknown && <p>供应商计费状态未知。</p>}
        {run.error && runErrorMessage(run.error, run.status === "cancelled") !== runTitle(run) && <p role="alert">{runErrorMessage(run.error, run.status === "cancelled")}</p>}
        {canvasRunOutputItems(run).map(item => {
          const asset = item.assetId ? assetsById.get(item.assetId) : undefined;
          const current = outputIsCurrent(node?.currentOutputRef, node?.outputAssetId, run.id, item);
          const installed = item.status === "installed" && Boolean(asset?.available !== false && asset);
          const statusLabel = item.status === "installed" && item.cancellationRequested
            ? current ? "当前输出（取消后晚到）" : "已安装（取消后晚到，未自动采用）"
            : current ? item.status === "installed" ? "当前输出" : `当前输出（${outputStatus[item.status]}）` : outputStatus[item.status];
          return <section className="creative-canvas-history__result" key={item.resultId} aria-label={`结果 ${item.resultId}`}>
            {installed ? <img src={asset!.url} alt={asset!.name} /> : <div className="creative-canvas-history__placeholder" aria-label={outputStatus[item.status]}>{outputStatus[item.status]}</div>}
            <div><strong>{statusLabel}</strong>{item.error && <p role="alert">{item.error}</p>}{asset?.available === false && <p role="alert">素材已不可用</p>}</div>
            {item.status === "cancel_requested" && <p className="creative-canvas-history__disposition">停止已请求；供应商未确认停止，计费状态未知。</p>}
            {item.status === "cancelled" && item.localDisposition === "queued_cancelled" && <p className="creative-canvas-history__disposition">已取消（未发送）。</p>}
            {item.status === "installed" && item.cancellationRequested && <p className="creative-canvas-history__disposition">停止请求后结果仍返回；未自动设为当前输出，计费状态可能未知。</p>}
            <div className="creative-canvas-history__actions">
              {installed && onPreviewAsset && <button type="button" aria-label={`预览结果 ${asset!.name}`} onClick={() => onPreviewAsset(asset!)}>预览</button>}
              {installed && onDownloadAsset && <button type="button" aria-label={`下载结果 ${asset!.name}`} onClick={() => onDownloadAsset(asset!)}>下载</button>}
              {installed && !current && node && <button type="button" disabled={busy} aria-label={`设为当前输出 ${asset!.name}`} onClick={() => onSelectOutput(run.id, item.resultId)}>设为当前</button>}
              {installed && <button type="button" aria-label={`以结果继续创作 ${asset!.name}`} onClick={() => onBranchOutput(run.id, item.resultId, asset!.id)}>继续创作</button>}
              {item.status === "failed" && <button type="button" disabled={busy} aria-label={`重试结果 ${item.resultId}`} onClick={() => onRetryOutput(run.id, item.resultId)}>重试</button>}
              {(item.status === "pending" || item.status === "running") && <button type="button" disabled={busy} aria-label={`取消结果 ${item.resultId}`} onClick={() => onCancelOutput(run.id, item.resultId)}>取消</button>}
              {(item.status === "installed" || item.status === "failed") && <button type="button" disabled={busy} aria-label={`删除结果 ${item.resultId}`} onClick={() => onDeleteOutput(run.id, item.resultId)}><Trash2 size={13} />删除</button>}
            </div>
          </section>;
        })}
      </article>;
    })}
    {batches.map(batch => <article className="creative-canvas-history__batch" key={batch.id}>
      <header><strong>批量生成 · {batchStatus[batch.status]}</strong><time>{new Date(batch.createdAt).toLocaleString("zh-CN")}</time></header>
      {batch.steps.map(step => <p key={step.idempotencyKey}><strong>{nodesById.get(step.nodeId)?.label || step.nodeId} · {stepStatus[step.status]}</strong>{step.error ? `：${step.error}` : ""}</p>)}
      {(batch.status === "queued" || batch.status === "running") && <button type="button" disabled={busy} aria-label={`取消批次 ${batch.id}`} onClick={() => onCancelBatch(batch.id)}>取消批次</button>}
    </article>)}
  </aside>;
}
