import { useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, Search, ShieldCheck, Sparkles } from "lucide-react";

import type { PreproductionAsset } from "./preproductionApi";
import {
  listSegmentSearches,
  prepareSegmentRerank,
  runSegmentRerank,
  searchSegments,
  type SegmentRerankDisclosure,
  type SegmentSearch,
} from "./segmentRetrievalApi";
import { formatTime, SegmentPreview } from "./SegmentPreview";


function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

const staleMessages = {
  segment_index_changed: "片段索引已变化，这次检索和复排仅作为历史证据保留。",
};

const rerankStaleMessages = {
  segment_index_changed: "片段索引已变化",
  model_changed: "复排模型已变化",
  provider_configuration_changed: "供应商配置已变化",
};

export function SegmentRetrievalPanel({
  projectId,
  assets,
  enabled,
}: {
  projectId: string;
  assets: PreproductionAsset[];
  enabled: boolean;
}) {
  const names = useMemo(() => new Map(assets.map((asset) => [asset.id, asset.name])), [assets]);
  const [history, setHistory] = useState<SegmentSearch[]>([]);
  const [current, setCurrent] = useState<SegmentSearch | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [disclosure, setDisclosure] = useState<SegmentRerankDisclosure | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"search" | "disclosure" | "rerank" | null>(null);
  const [error, setError] = useState("");
  const [cloudError, setCloudError] = useState("");
  const projectGeneration = useRef(0);

  useEffect(() => {
    const generation = ++projectGeneration.current;
    let active = true;
    setLoading(true); setError(""); setCloudError(""); setDisclosure(null); setSelected([]);
    setHistory([]); setCurrent(null); setQuery(""); setBusy(null);
    void listSegmentSearches(projectId).then((searches) => {
      if (!active || generation !== projectGeneration.current) return;
      setHistory(searches);
      setCurrent(searches[0] ?? null);
      setQuery(searches[0]?.query ?? "");
    }).catch((reason) => {
      if (active && generation === projectGeneration.current) setError(errorMessage(reason, "无法读取片段检索历史。"));
    }).finally(() => { if (active && generation === projectGeneration.current) setLoading(false); });
    return () => { active = false; };
  }, [projectId]);

  function selectSearch(searchId: string) {
    const found = history.find((item) => item.id === searchId) ?? null;
    setCurrent(found); setQuery(found?.query ?? ""); setSelected([]); setDisclosure(null);
    setAccepted(false); setError(""); setCloudError("");
  }

  async function search() {
    const value = query.trim();
    if (!enabled || !value || busy) return;
    const generation = projectGeneration.current;
    setBusy("search"); setError(""); setCloudError(""); setDisclosure(null); setSelected([]);
    try {
      const created = await searchSegments(projectId, value);
      if (generation !== projectGeneration.current) return;
      setCurrent(created);
      setHistory((items) => [created, ...items.filter((item) => item.id !== created.id)]);
    } catch (reason) {
      if (generation === projectGeneration.current) setError(errorMessage(reason, "本地视觉检索失败，请重试。"));
    } finally {
      if (generation === projectGeneration.current) setBusy(null);
    }
  }

  async function previewDisclosure() {
    if (!current || !selected.length || busy || current.validity.status !== "current") return;
    const generation = projectGeneration.current;
    setBusy("disclosure"); setError(""); setCloudError("");
    try {
      const prepared = await prepareSegmentRerank(projectId, current.id, selected);
      if (generation !== projectGeneration.current) return;
      setDisclosure(prepared);
      setAccepted(false);
    } catch (reason) {
      if (generation === projectGeneration.current) setError(errorMessage(reason, "无法预览豆包发送内容，请重试。"));
    } finally {
      if (generation === projectGeneration.current) setBusy(null);
    }
  }

  async function confirmRerank() {
    if (!current || !disclosure || !accepted || busy) return;
    const generation = projectGeneration.current;
    setBusy("rerank"); setError(""); setCloudError("");
    try {
      const updated = await runSegmentRerank(
        projectId, current.id, disclosure.candidates.map((item) => item.candidateId), disclosure.digest,
      );
      if (generation !== projectGeneration.current) return;
      setCurrent(updated);
      setHistory((items) => items.map((item) => item.id === updated.id ? updated : item));
      setDisclosure(null); setAccepted(false);
    } catch (reason) {
      if (generation === projectGeneration.current) {
        setCloudError(errorMessage(reason, "豆包视觉复排失败；本地候选仍可使用。"));
        setDisclosure(null); setAccepted(false);
      }
    } finally {
      if (generation === projectGeneration.current) setBusy(null);
    }
  }

  const stale = current?.validity.status === "stale" ? current.validity.reason : null;
  const rerank = current?.rerank;
  const progress = busy === "search" ? "正在执行本地片段检索。"
    : busy === "disclosure" ? "正在准备豆包发送内容。"
      : busy === "rerank" ? "正在执行豆包视觉复排。" : "";

  return <section className="segment-retrieval" aria-labelledby="segment-retrieval-title">
    {progress && <span className="visually-hidden" role="status">{progress}</span>}
    <div className="segment-retrieval-heading">
      <div><h3 id="segment-retrieval-title">按画面查找片段</h3><p>先在本地关键帧索引中召回候选；只有核对发送范围后，才会用豆包复排所选画面。</p></div>
      <Search aria-hidden="true" size={24} />
    </div>
    <div className="segment-retrieval-query">
      <label htmlFor="segment-retrieval-query">脚本段落、卖点或动作描述</label>
      <div><input id="segment-retrieval-query" aria-label="片段检索文字" value={query} disabled={!enabled || Boolean(busy)}
        onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void search(); }}
        placeholder="例如：展示咖啡粉冲泡和蒸汽上升" />
        <button type="button" className="primary-action" disabled={!enabled || !query.trim() || Boolean(busy)} onClick={() => void search()}>
          <Search aria-hidden="true" size={16} />{busy === "search" ? "正在检索…" : "本地检索"}
        </button></div>
      {!enabled && <p>请先完成至少一个当前有效的素材片段索引。</p>}
    </div>
    {history.length > 1 && <label className="segment-retrieval-history">检索历史<select aria-label="检索历史" value={current?.id ?? ""} onChange={(event) => selectSearch(event.target.value)}>
      {history.map((item) => <option value={item.id} key={item.id}>{item.query} · {new Date(item.createdAt).toLocaleString("zh-CN")}</option>)}
    </select></label>}
    {loading && <p role="status">正在读取片段检索历史…</p>}
    {error && <p className="segment-retrieval-error" role="alert"><CircleAlert aria-hidden="true" size={16} />{error}</p>}
    {stale && <p className="segment-retrieval-stale" role="status">{staleMessages[stale]}</p>}
    {current && <section className="segment-local-results" aria-label="本地视觉候选">
      <header><div><h4>{current.localCandidates.length} 个本地候选</h4><p>{current.query}</p></div><span>{current.vectorizerVersion}</span></header>
      <ol>{current.localCandidates.map((candidate, index) => {
        const assetName = names.get(candidate.assetId) ?? `项目素材 ${candidate.assetId}`;
        const checked = selected.includes(candidate.candidateId);
        return <li key={candidate.candidateId}>
          <div className="segment-result-rank" aria-label={`本地排序 ${index + 1}`}>{index + 1}</div>
          {current.validity.status === "current" ? <img src={candidate.keyframe.url} alt={`${assetName} 检索关键帧 ${formatTime(candidate.keyframe.timeSeconds)}`} /> : <div className="segment-result-media-disabled">旧关键帧已停用</div>}
          <div className="segment-result-copy"><div><strong>{formatTime(candidate.inPointSeconds)} → {formatTime(candidate.outPointSeconds)}</strong><span>来源：{assetName}</span></div>
            <div className="segment-result-scores"><span>视觉相关性 {percent(candidate.visualRelevance)}</span><span>基础质量 {percent(candidate.quality.score)}</span><span className={candidate.duplicateRisk.level === "high" ? "is-warning" : ""}>重复风险 {candidate.duplicateRisk.level === "high" ? "高" : "低"}</span></div>
            <p>{candidate.evidence.basis}</p>
            <label><input type="checkbox" aria-label={`选择复排候选 ${candidate.candidateId}`} checked={checked}
              disabled={current.validity.status !== "current" || Boolean(busy)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, candidate.candidateId] : items.filter((id) => id !== candidate.candidateId))} />加入豆包复排</label>
          </div>
          {current.validity.status === "current" && <SegmentPreview assetName={assetName} candidate={candidate} kind="检索片段预览" />}
        </li>;
      })}</ol>
      <button type="button" className="secondary-action" disabled={!selected.length || Boolean(busy) || current.validity.status !== "current"} onClick={() => void previewDisclosure()}>
        <ShieldCheck aria-hidden="true" size={16} />{busy === "disclosure" ? "正在准备…" : "预览豆包发送内容"}
      </button>
    </section>}
    {disclosure && <section className="segment-rerank-disclosure" role="region" aria-label="豆包发送前核对">
      <header><div><h4>豆包发送前核对</h4><p>火山方舟豆包 · {disclosure.model}</p></div><span>{disclosure.candidates.length} 张关键帧</span></header>
      <dl><div><dt>实际发送文字</dt><dd><pre>{disclosure.sentContent.text}</pre></dd></div><div><dt>所选关键帧范围</dt><dd><ul>{disclosure.candidates.map((item) => <li key={item.candidateId}>{formatTime(item.inPointSeconds)} → {formatTime(item.outPointSeconds)}</li>)}</ul></dd></div></dl>
      <p>不会发送：{disclosure.notSent.join("、")}</p>
      <label><input type="checkbox" checked={accepted} disabled={busy === "rerank"} onChange={(event) => setAccepted(event.target.checked)} />我已核对本次发送内容</label>
      <div><button type="button" className="secondary-action" disabled={busy === "rerank"} onClick={() => { setDisclosure(null); setAccepted(false); }}>取消</button>
        <button type="button" className="primary-action" disabled={!accepted || busy === "rerank"} onClick={() => void confirmRerank()}><Sparkles aria-hidden="true" size={16} />{busy === "rerank" ? "正在复排…" : "确认并开始豆包复排"}</button></div>
    </section>}
    {cloudError && <><p className="segment-retrieval-error" role="alert"><CircleAlert aria-hidden="true" size={16} />{cloudError}</p><p className="segment-cloud-fallback">云端复排未完成；本地候选仍可浏览和人工采用。</p></>}
    {rerank && <section className={`segment-rerank-result segment-rerank-result--${rerank.status}`} aria-label="豆包复排结果">
      <header><div><h4>{rerank.status === "completed" ? "豆包复排已完成" : "云端复排未完成"}</h4><p>火山方舟豆包 · {rerank.model}</p></div>{rerank.validity.status === "stale" && <span>已过期：{rerankStaleMessages[rerank.validity.reason!]}</span>}</header>
      {rerank.error && <p className="segment-retrieval-error" role="alert">{rerank.error.message}</p>}
      {rerank.ranking.length > 0 && <ol>{rerank.ranking.map((item, index) => <li key={item.candidateId}><strong>{index + 1}. {item.candidateId}</strong><p>{item.reason}</p><span>置信度 {percent(item.confidence)}</span></li>)}</ol>}
    </section>}
  </section>;
}
