import { useEffect, useMemo, useRef, useState } from "react";

import {
  cancelVideoGenerationRemotely,
  listVideoGenerationModels,
  listVideoGenerationRuns,
  refreshVideoGeneration,
  retryVideoGenerationDownload,
  stopVideoGenerationLocally,
  submitVideoGeneration,
  type VideoGenerationCombination,
  type VideoGenerationModel,
  type VideoGenerationRun,
  type VideoReferenceType,
} from "./videoGenerationApi";
import { GenerationRoutingPolicyControl } from "./GenerationRoutingPolicyControl";
import { GenerationRouteAudit } from "./GenerationRouteAudit";
import type { GenerationRoutingPolicyRef } from "./generationRoutingApi";


type ImageAsset = { id: string; assetVersionId: string; name: string; kind: "image"; url: string };
type Props = { projectId: string; imageAssets: ImageAsset[]; onInstalled?: () => void };

const referenceLabels: Record<VideoReferenceType, string> = {
  "first-frame": "首帧",
  "last-frame": "尾帧",
  reference: "参考图",
};

const statusLabels: Record<string, string> = {
  submitting: "正在提交远端任务",
  queued: "远端等待中",
  running: "远端处理中",
  completed: "已完成",
  partial_success: "部分成功",
  failed: "失败",
  cancelled: "远端已取消",
  unknown: "状态未知",
};

function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `video-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const pendingKeys = new Map<string, { fingerprint: string; key: string }>();

function fingerprint(value: unknown) {
  const source = JSON.stringify(value);
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function pendingKey(scope: string, value: unknown) {
  const storageKey = `video-generation:retry-key:${scope}`;
  const expected = fingerprint(value);
  let pending = pendingKeys.get(storageKey);
  if (!pending) {
    try {
      const stored = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
      if (stored?.fingerprint === expected && typeof stored.key === "string") pending = stored;
    } catch { /* Session storage is only a retry aid. */ }
  }
  if (!pending || pending.fingerprint !== expected) pending = { fingerprint: expected, key: newIdempotencyKey() };
  pendingKeys.set(storageKey, pending);
  try { sessionStorage.setItem(storageKey, JSON.stringify(pending)); } catch { /* Keep the in-memory key. */ }
  return pending;
}

function clearPendingKey(scope: string, pending: { fingerprint: string; key: string }) {
  const storageKey = `video-generation:retry-key:${scope}`;
  if (pendingKeys.get(storageKey)?.key === pending.key) pendingKeys.delete(storageKey);
  try { if (JSON.parse(sessionStorage.getItem(storageKey) ?? "null")?.key === pending.key) sessionStorage.removeItem(storageKey); }
  catch { /* Storage failures do not change the accepted run. */ }
}

function modelKey(model: Pick<VideoGenerationModel, "provider" | "model">) {
  return JSON.stringify([model.provider, model.model]);
}

function combinationKey(value: VideoGenerationCombination) {
  return JSON.stringify(value);
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function isRemotePending(run: VideoGenerationRun) {
  return ["submitting", "queued", "running"].includes(run.status);
}

function shouldPoll(run: VideoGenerationRun) {
  return isRemotePending(run) && run.localDisposition !== "stopped";
}

function replaceRun(runs: VideoGenerationRun[], next: VideoGenerationRun) {
  return [next, ...runs.filter((run) => run.id !== next.id)];
}

export function VideoGenerationPanel({ projectId, imageAssets, onInstalled }: Props) {
  const [models, setModels] = useState<VideoGenerationModel[]>();
  const [selectedModelKey, setSelectedModelKey] = useState("");
  const [profileId, setProfileId] = useState("");
  const [patternKey, setPatternKey] = useState("[]");
  const [combinationValue, setCombinationValue] = useState("");
  const [referenceAssetIds, setReferenceAssetIds] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [resultCount, setResultCount] = useState(1);
  const [accepted, setAccepted] = useState(false);
  const [runs, setRuns] = useState<VideoGenerationRun[]>([]);
  const runsRef = useRef<VideoGenerationRun[]>([]);
  const refreshesRef = useRef(new Map<string, Promise<{ run: VideoGenerationRun }>>());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [routingPolicyRef, setRoutingPolicyRef] = useState<GenerationRoutingPolicyRef>();

  useEffect(() => { runsRef.current = runs; }, [runs]);

  function refreshOne(runId: string) {
    const existing = refreshesRef.current.get(runId);
    if (existing) return existing;
    const request = refreshVideoGeneration(projectId, runId);
    refreshesRef.current.set(runId, request);
    const cleanup = () => { if (refreshesRef.current.get(runId) === request) refreshesRef.current.delete(runId); };
    void request.then(cleanup, cleanup);
    return request;
  }

  async function refreshActive(current: VideoGenerationRun[]) {
    const active = current.filter(shouldPoll);
    if (!active.length) return;
    const refreshed = await Promise.all(active.map((run) => refreshOne(run.id)));
    setRuns((existing) => refreshed.reduce((next, response) => replaceRun(next, response.run), existing));
    if (refreshed.some(({ run }) => run.outputItems.some((item) => item.status === "completed"))) onInstalled?.();
  }

  useEffect(() => {
    let active = true;
    setModels(undefined); setError("");
    void Promise.all([listVideoGenerationModels(projectId), listVideoGenerationRuns(projectId)])
      .then(([catalog, history]) => {
        if (!active) return;
        setModels(catalog.models);
        setSelectedModelKey(catalog.models[0] ? modelKey(catalog.models[0]) : "");
        setRuns(history.runs);
        void refreshActive(history.runs).catch((reason) => { if (active) setError(errorMessage(reason, "无法恢复远端视频任务。")); });
      })
      .catch((reason) => { if (active) { setModels([]); setError(errorMessage(reason, "无法读取视频生成状态。")); } });
    const timer = window.setInterval(() => {
      void refreshActive(runsRef.current).catch((reason) => { if (active) setError(errorMessage(reason, "无法查询远端视频任务。")); });
    }, 3_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [projectId]);

  const selectedModel = useMemo(
    () => models?.find((entry) => modelKey(entry) === selectedModelKey),
    [models, selectedModelKey],
  );
  const profiles = selectedModel?.capabilities.profiles ?? [];
  const profile = profiles.find((entry) => entry.id === profileId);
  const pattern = useMemo(() => {
    try { return JSON.parse(patternKey) as VideoReferenceType[]; } catch { return []; }
  }, [patternKey]);
  const combination = useMemo(() => {
    try { return JSON.parse(combinationValue) as VideoGenerationCombination; } catch { return undefined; }
  }, [combinationValue]);

  useEffect(() => {
    const first = profiles[0];
    setProfileId(first?.id ?? "");
    setPatternKey(JSON.stringify(first?.referencePatterns[0] ?? []));
    setCombinationValue(first?.combinations[0] ? combinationKey(first.combinations[0]) : "");
    setReferenceAssetIds([]);
    setResultCount(first?.minResults ?? 1);
  }, [selectedModelKey]);

  function changeProfile(nextId: string) {
    const next = profiles.find((entry) => entry.id === nextId);
    setProfileId(nextId);
    setPatternKey(JSON.stringify(next?.referencePatterns[0] ?? []));
    setCombinationValue(next?.combinations[0] ? combinationKey(next.combinations[0]) : "");
    setReferenceAssetIds([]);
    setResultCount(next?.minResults ?? 1);
  }

  function changePattern(next: string) {
    setPatternKey(next);
    setReferenceAssetIds([]);
  }

  const referenceSelectionValid = pattern.length === referenceAssetIds.length
    && referenceAssetIds.every(Boolean)
    && pattern.every((type, index) => type !== "reference" || referenceAssetIds.indexOf(referenceAssetIds[index]) === index);
  const canSubmit = Boolean(
    selectedModel && profile && combination && prompt.trim() && accepted && referenceSelectionValid
    && resultCount >= profile.minResults && resultCount <= profile.maxResults,
  );
  const routingCandidates = useMemo(() => (models ?? []).filter((entry) => {
    const candidate = entry.capabilities.profiles.find((item) => item.id === profileId && item.mode === profile?.mode);
    return Boolean(candidate
      && candidate.referencePatterns.some((item) => JSON.stringify(item) === JSON.stringify(pattern))
      && candidate.combinations.some((item) => combination && combinationKey(item) === combinationKey(combination))
      && resultCount >= candidate.minResults && resultCount <= candidate.maxResults);
  }).map((entry) => ({ provider: entry.provider, model: entry.model })), [models, profileId, profile?.mode, patternKey, combinationValue, resultCount]);

  async function submit() {
    if (!selectedModel || !profile || !combination || !canSubmit || busy) return;
    const input = {
      disclosure: { provider: selectedModel.provider, model: selectedModel.model, prompt: prompt.trim(), accepted: true as const },
      videoParams: {
        profileId: profile.id,
        mode: profile.mode,
        references: pattern.map((referenceType, index) => {
          const asset = imageAssets.find((item) => item.id === referenceAssetIds[index])!;
          return { assetId: asset.id, assetVersionId: asset.assetVersionId, referenceType };
        }),
        ...combination,
        resultCount,
      },
      caller: "asset-library",
    };
    const scope = `${projectId}:submit`;
    const pending = pendingKey(scope, input);
    setBusy(true); setError("");
    try {
      const response = await submitVideoGeneration(projectId, { ...input, idempotencyKey: pending.key, routingPolicyRef });
      clearPendingKey(scope, pending);
      setRuns((current) => replaceRun(current, response.run));
    } catch (reason) { setError(errorMessage(reason, "无法提交视频生成。")); }
    finally { setBusy(false); }
  }

  async function mutateRun(action: () => Promise<{ run: VideoGenerationRun }>, fallback: string) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await action();
      setRuns((current) => replaceRun(current, response.run));
      if (response.run.outputItems.some((item) => item.status === "completed")) onInstalled?.();
    } catch (reason) { setError(errorMessage(reason, fallback)); }
    finally { setBusy(false); }
  }

  function retry(runId: string) {
    const scope = `${projectId}:retry:${runId}`;
    const pending = pendingKey(scope, { projectId, runId });
    void mutateRun(async () => {
      const response = await retryVideoGenerationDownload(projectId, runId, pending.key);
      clearPendingKey(scope, pending);
      return response;
    }, "无法重试视频结果下载。");
  }

  return <section className="image-generation-panel video-generation-panel" aria-labelledby="video-generation-title">
    <h4 id="video-generation-title">视频生成</h4>
    <p>只显示异步适配器已验证的精确能力组合；不会自动换模型、换 Provider 或重提远端计费任务。</p>
    {error && <p role="alert">{error}</p>}
    {models === undefined ? <p>正在读取视频模型能力…</p> : models.length === 0
      ? <p>当前没有已通过异步能力验证的视频模型。连接 Provider 不等于该模型组合已验证。</p>
      : <>
        <label>视频模型<select value={selectedModelKey} onChange={(event) => setSelectedModelKey(event.target.value)}>
          {models.map((entry) => <option key={modelKey(entry)} value={modelKey(entry)}>{entry.label}</option>)}
        </select></label>
        <label>生成档位<select value={profileId} onChange={(event) => changeProfile(event.target.value)}>
          {profiles.map((entry) => <option key={entry.id} value={entry.id}>{entry.mode === "text-to-video" ? "文生视频" : "图生视频"} · {entry.id}</option>)}
        </select></label>
        {profile && <>
          <label>参考图组合<select value={patternKey} onChange={(event) => changePattern(event.target.value)}>
            {profile.referencePatterns.map((entry) => <option key={JSON.stringify(entry)} value={JSON.stringify(entry)}>{entry.length ? entry.map((type) => referenceLabels[type]).join(" + ") : "不使用参考图"}</option>)}
          </select></label>
          <label>已验证参数组合<select value={combinationValue} onChange={(event) => setCombinationValue(event.target.value)}>
            {profile.combinations.map((entry) => <option key={combinationKey(entry)} value={combinationKey(entry)}>{entry.durationSeconds} 秒 · {entry.aspectRatio} · {entry.resolution} · {entry.audio ? "含音频" : "无音频"}</option>)}
          </select></label>
          {pattern.map((type, index) => {
            const count = pattern.slice(0, index + 1).filter((value) => value === type).length;
            const label = type === "reference" ? `${referenceLabels[type]} ${count}` : referenceLabels[type];
            return <label key={`${type}-${index}`}>{label}<select aria-label={label} value={referenceAssetIds[index] ?? ""} onChange={(event) => setReferenceAssetIds((current) => {
              const next = [...current]; next[index] = event.target.value; return next;
            })}>
              <option value="">请选择</option>
              {imageAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
            </select></label>;
          })}
          {combination && <p>{combination.durationSeconds} 秒 · {combination.aspectRatio} · {combination.resolution} · {combination.audio ? "含音频" : "无音频"}</p>}
          <label>视频提示词<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
          <label>结果数量<input aria-label="视频结果数量" type="number" min={profile.minResults} max={profile.maxResults} value={resultCount} onChange={(event) => setResultCount(Number(event.target.value))} /></label>
          <label><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />确认发送提示词和所选图片</label>
          {selectedModel && <GenerationRoutingPolicyControl modality="video" currentRoute={{ provider: selectedModel.provider, model: selectedModel.model }} verifiedRoutes={routingCandidates} value={routingPolicyRef} onChange={setRoutingPolicyRef} />}
          {!referenceSelectionValid && <p role="status">当前参考图片未满足所选的精确角色组合。</p>}
          <button type="button" className="primary-action" disabled={!canSubmit || busy} onClick={() => void submit()}>{busy ? "正在处理…" : "提交视频生成"}</button>
        </>}
      </>}

    {runs.length > 0 && <section aria-label="视频生成历史">
      <div><h5>生成历史</h5><button type="button" disabled={busy} onClick={() => void refreshActive(runs)}>刷新远端状态</button></div>
      {runs.map((run) => <article key={run.id}>
        <strong>{run.outcome === "late_success" ? "迟到成功（未自动采用）" : run.localDisposition === "stopped" ? statusLabels[run.status] ?? run.status : run.lastConfirmedRemoteStatus === "unknown" ? "远端状态未知" : run.lastConfirmedRemoteStatus === "queued" ? "远端等待中" : statusLabels[run.status] ?? run.status}</strong>
        <p>实际路由：{run.actualRoute?.provider ?? run.provider} · {run.actualRoute?.model ?? run.model}</p>
        <GenerationRouteAudit attempts={run.routeAttempts} />
        {run.executionSemantics?.stateConfidence === "unknown" && <p>运行状态未知，未自动切换 Provider。</p>}
        {run.localDisposition === "stopped" && run.outcome !== "late_success" && <p>本地已停止轮询，远端任务未被视为已取消</p>}
        {run.remoteCancelResult === "unsupported" && <p>远端取消不受支持</p>}
        {run.remoteCancelResult === "unknown" && <p>远端取消结果未知</p>}
        {run.billingUnknown && <p>计费状态未知</p>}
        {run.outputItems.map((item) => <div key={item.resultId}>
          {(item.status === "completed" || item.status === "reused") && item.url
            ? <><video controls preload="metadata" src={item.url} aria-label={`预览${item.name ?? `结果 ${item.index + 1}`}`} /><a href={item.url} download>下载 {item.name ?? `结果 ${item.index + 1}`}</a></>
            : item.status === "failed" ? <span>失败 · {item.reason ?? "generation_failed"}</span>
              : <span>{statusLabels[item.status] ?? item.status}</span>}
        </div>)}
        {isRemotePending(run) && <>
          <button type="button" disabled={busy} onClick={() => void mutateRun(() => refreshOne(run.id), "无法查询远端任务。")}>查询远端</button>
          {run.localDisposition !== "stopped" && <button type="button" disabled={busy} onClick={() => void mutateRun(() => stopVideoGenerationLocally(projectId, run.id), "无法停止本地轮询。")}>仅停止本地轮询</button>}
          <button type="button" disabled={busy} onClick={() => void mutateRun(() => cancelVideoGenerationRemotely(projectId, run.id), "无法请求远端取消。")}>请求远端取消</button>
        </>}
        {["failed", "partial_success"].includes(run.status) && run.outputItems.some((item) => item.retryable) && <button type="button" disabled={busy} onClick={() => retry(run.id)}>重试下载失败结果</button>}
      </article>)}
    </section>}
  </section>;
}
