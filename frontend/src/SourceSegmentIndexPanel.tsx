import { useCallback, useEffect, useMemo, useState } from "react";
import { Film, RefreshCw, Square, WandSparkles } from "lucide-react";

import type { PreproductionAsset } from "./preproductionApi";
import { SegmentRetrievalPanel } from "./SegmentRetrievalPanel";
import { formatTime, SegmentPreview } from "./SegmentPreview";
import {
  cancelSourceSegmentIndex,
  listSourceSegmentIndexes,
  retrySourceSegmentIndex,
  startSourceSegmentIndexes,
  type SourceSegmentIndex,
  type SourceSegmentStaleReason,
} from "./sourceSegmentApi";


const statusLabels: Record<SourceSegmentIndex["status"], string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const staleMessages: Record<SourceSegmentStaleReason, string> = {
  source_missing: "源文件不可用，旧候选不可用于后续选片。",
  source_changed: "源文件内容已变化，旧候选不可用于后续选片。",
  algorithm_changed: "场景检测算法版本已变化，旧候选不可用于后续选片。",
  keyframe_model_changed: "关键帧版本已变化，旧候选不可用于后续选片。",
};

function mergeIndexes(current: SourceSegmentIndex[], updates: SourceSegmentIndex[]) {
  const byAsset = new Map(current.map((item) => [item.assetId, item]));
  for (const item of updates) byAsset.set(item.assetId, item);
  return [...byAsset.values()].sort((left, right) => left.assetId.localeCompare(right.assetId));
}

export function SourceSegmentIndexPanel({ projectId, assets }: { projectId: string; assets?: PreproductionAsset[] }) {
  const videos = useMemo(() => (assets ?? []).filter((asset) => asset.kind === "video"), [assets]);
  const names = useMemo(() => new Map(videos.map((asset) => [asset.id, asset.name])), [videos]);
  const [indexes, setIndexes] = useState<SourceSegmentIndex[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyAsset, setBusyAsset] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const indexesByAsset = useMemo(() => new Map(indexes.map((item) => [item.assetId, item])), [indexes]);
  const retrievalEnabled = indexes.some((item) => item.status === "completed"
    && item.validity.status === "current" && item.candidates.length > 0);

  const load = useCallback(async () => {
    try {
      setIndexes(await listSourceSegmentIndexes(projectId));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取片段索引。");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    setIndexes([]); setSelected([]); setLoading(true); setError("");
    void load();
  }, [load]);

  const active = indexes.some((item) => item.status === "queued" || item.status === "running");
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => void load(), 1000);
    return () => window.clearTimeout(timer);
  }, [active, indexes, load]);

  async function start() {
    if (!selected.length || starting) return;
    setStarting(true); setError("");
    try {
      const created = await startSourceSegmentIndexes(projectId, selected);
      setIndexes((current) => mergeIndexes(current, created));
      setSelected([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法启动片段索引。");
    } finally {
      setStarting(false);
    }
  }

  async function mutate(assetId: string, action: "cancel" | "retry") {
    if (busyAsset) return;
    setBusyAsset(assetId); setError("");
    try {
      const updated = action === "cancel"
        ? await cancelSourceSegmentIndex(projectId, assetId)
        : await retrySourceSegmentIndex(projectId, assetId);
      setIndexes((current) => mergeIndexes(current, [updated]));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "片段索引操作失败。");
    } finally {
      setBusyAsset(null);
    }
  }

  return <section className="source-segment-index" aria-labelledby="source-segment-title">
    <div className="source-segment-heading"><div><span className="sp-tag">可重建派生数据</span><h3 id="source-segment-title">素材片段候选</h3><p>为获准使用的项目视频建立场景索引，核对来源、入点、出点与关键帧。候选不会改变项目素材身份。</p></div><Film aria-hidden="true" size={25} /></div>
    {loading ? <p role="status">正在读取片段索引…</p> : videos.length ? <>
      <fieldset className="source-segment-selection"><legend>选择要建立索引的视频素材</legend>{videos.map((asset) => {
        const record = indexesByAsset.get(asset.id);
        const unavailable = record?.status === "queued" || record?.status === "running";
        return <label key={asset.id}><input type="checkbox" aria-label={`选择 ${asset.name}`} checked={selected.includes(asset.id)} disabled={unavailable || starting}
          onChange={(event) => setSelected((current) => event.target.checked ? [...current, asset.id] : current.filter((id) => id !== asset.id))} />
          <span><strong>{asset.name}</strong><small>{asset.duration ? `${formatTime(asset.duration)} · ` : ""}{record ? statusLabels[record.status] : "未索引"}</small></span></label>;
      })}</fieldset>
      <button type="button" className="primary-action" disabled={!selected.length || starting} onClick={() => void start()}><WandSparkles size={16} aria-hidden="true" />{starting ? "正在提交…" : `为 ${selected.length} 个视频建立索引`}</button>
    </> : <p>项目中还没有可建立索引的视频素材。</p>}
    {error && <p role="alert" className="source-segment-error">{error}</p>}
    {!loading && !indexes.length && <p className="source-segment-empty">尚未建立片段索引</p>}
    <div className="source-segment-runs">{indexes.map((record) => {
      const assetName = names.get(record.assetId) ?? `项目素材 ${record.assetId}`;
      const stale = record.validity.status === "stale" && record.validity.reason;
      const canRetry = stale || record.status === "failed" || record.status === "cancelled";
      return <article key={record.assetId} className="source-segment-run">
        <header><div><h4>{assetName}</h4><p>项目素材 · {record.assetId}</p></div><span role="status" aria-label="索引状态" className={`source-segment-status source-segment-status--${record.status}`}>{stale ? "已过期" : statusLabels[record.status]}</span></header>
        {stale && <p role="status" className="source-segment-stale">{staleMessages[stale]}</p>}
        {record.error?.message && <p role="alert" className="source-segment-error">{record.error.message}</p>}
        {(record.status === "queued" || record.status === "running") && <button type="button" className="secondary-action" disabled={Boolean(busyAsset)} aria-label={`取消 ${assetName}`} onClick={() => void mutate(record.assetId, "cancel")}><Square size={14} aria-hidden="true" />取消索引</button>}
        {canRetry && <button type="button" className="secondary-action" disabled={Boolean(busyAsset)} aria-label={`重试 ${assetName}`} onClick={() => void mutate(record.assetId, "retry")}><RefreshCw size={14} aria-hidden="true" />重新建立索引</button>}
        {record.status === "completed" && stale && <p className="source-segment-stale-note">旧候选已停用；重新建立索引后才能预览。</p>}
        {record.status === "completed" && !stale && <section className="source-segment-candidates" aria-label={`${assetName} 的素材片段候选`}>
          <div><strong>{record.candidates.length} 个素材片段候选</strong><span>{record.algorithmVersion ?? "场景检测"} · {record.keyframeModelVersion ?? "关键帧"}</span></div>
          <p>候选尚未进入变体；采用后才会成为正式剪辑片段。</p>
          <ol>{record.candidates.map((candidate) => <li key={candidate.id}>
            <img src={candidate.keyframe.url} alt={`${assetName} 关键帧 ${formatTime(candidate.keyframe.timeSeconds)}`} />
            <div><strong>{formatTime(candidate.inPointSeconds)} → {formatTime(candidate.outPointSeconds)}</strong><p>来源：{assetName}</p><p>源素材身份：{candidate.assetId}</p></div>
            <SegmentPreview assetName={assetName} candidate={candidate} />
          </li>)}</ol>
        </section>}
      </article>;
    })}</div>
    {!loading && <SegmentRetrievalPanel projectId={projectId} assets={assets ?? []} enabled={retrievalEnabled} />}
  </section>;
}
