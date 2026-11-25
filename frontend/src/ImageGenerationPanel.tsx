import { useEffect, useMemo, useState } from "react";

import {
  cancelImageGeneration,
  getImageGenerationConfiguration,
  getVerifiedImageCapabilities,
  listImageGenerationRuns,
  retryImageGeneration,
  submitImageGeneration,
  type ImageCapabilityCatalog,
  type ImageGenerationModelCatalogEntry,
  type ImageGenerationRun,
} from "./imageGenerationApi";
import { GenerationRoutingPolicyControl } from "./GenerationRoutingPolicyControl";
import { GenerationRouteAudit } from "./GenerationRouteAudit";
import type { GenerationRoutingPolicyRef } from "./generationRoutingApi";

type ImageAsset = {
  id: string;
  assetVersionId: string;
  name: string;
  kind: "image";
  url: string;
};

type Props = {
  projectId: string;
  imageAssets: ImageAsset[];
  onInstalled?: () => void;
};

type Operation = "generate" | "edit" | "super-resolution";

const referenceTypeLabels = {
  reference: "普通参考",
  "edit-source": "编辑源",
  "upscale-source": "超分源",
} as const;

const statusLabels: Record<string, string> = {
  queued: "等待中",
  running: "生成中",
  cancel_requested: "正在取消",
  completed: "已完成",
  partial_success: "部分成功",
  failed: "失败",
  cancelled: "已取消",
  timed_out: "已超时",
};

function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `image-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const pendingKeys = new Map<string, { fingerprint: string; key: string }>();

function requestFingerprint(value: unknown) {
  const source = JSON.stringify(value);
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function pendingKey(scope: string, fingerprint: string) {
  const storageKey = `image-generation:retry-key:${scope}`;
  let pending = pendingKeys.get(storageKey);
  if (!pending) {
    try {
      const stored = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
      if (stored?.fingerprint === fingerprint && typeof stored.key === "string") pending = stored;
    } catch { /* Session storage is an optional persistence aid. */ }
  }
  if (!pending || pending.fingerprint !== fingerprint) pending = { fingerprint, key: newIdempotencyKey() };
  pendingKeys.set(storageKey, pending);
  try { sessionStorage.setItem(storageKey, JSON.stringify(pending)); } catch { /* Keep the in-memory value. */ }
  return pending;
}

function clearPendingKey(scope: string, pending: { fingerprint: string; key: string }) {
  const storageKey = `image-generation:retry-key:${scope}`;
  if (pendingKeys.get(storageKey)?.key === pending.key) pendingKeys.delete(storageKey);
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
    if (stored?.key === pending.key) sessionStorage.removeItem(storageKey);
  } catch { /* Keep storage failures isolated from the accepted run. */ }
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function referenceType(operation: Operation) {
  return operation === "edit" ? "edit-source" as const
    : operation === "super-resolution" ? "upscale-source" as const
      : "reference" as const;
}

function modelValue(model: Pick<ImageGenerationModelCatalogEntry, "providerId" | "modelId">) {
  return JSON.stringify([model.providerId, model.modelId]);
}

export function ImageGenerationPanel({ projectId, imageAssets, onInstalled }: Props) {
  const [models, setModels] = useState<ImageGenerationModelCatalogEntry[]>();
  const [modelKey, setModelKey] = useState("");
  const [catalog, setCatalog] = useState<ImageCapabilityCatalog>();
  const [routingCatalogs, setRoutingCatalogs] = useState<Record<string, ImageCapabilityCatalog>>({});
  const [runs, setRuns] = useState<ImageGenerationRun[]>([]);
  const [prompt, setPrompt] = useState("");
  const [operation, setOperation] = useState<Operation>("generate");
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [aspectRatio, setAspectRatio] = useState("");
  const [resolution, setResolution] = useState("");
  const [resultCount, setResultCount] = useState(1);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [routingPolicyRef, setRoutingPolicyRef] = useState<GenerationRoutingPolicyRef>();

  useEffect(() => {
    let active = true;
    setModels(undefined);
    setCatalog(undefined);
    setError("");
    void Promise.all([getImageGenerationConfiguration(), listImageGenerationRuns(projectId)])
      .then(([configuration, runList]) => {
        if (!active) return;
        const verified = (configuration.modelCatalog ?? []).filter((entry) =>
          entry.mediaType === "image" && entry.configured && entry.capabilitySource === "verified",
        );
        setModels(verified);
        void Promise.all(verified.map(async (entry) => [modelValue(entry), await getVerifiedImageCapabilities(projectId, entry.providerId, entry.modelId)] as const))
          .then((items) => { if (active) setRoutingCatalogs(Object.fromEntries(items)); })
          .catch(() => { if (active) setRoutingCatalogs({}); });
        setRuns(runList.runs);
        setModelKey(verified[0] ? modelValue(verified[0]) : "");
      })
      .catch((reason) => { if (active) { setModels([]); setError(errorMessage(reason, "无法读取图片生成状态。")); } });
    return () => { active = false; };
  }, [projectId]);

  const selectedModel = useMemo(() => models?.find((entry) =>
    modelValue(entry) === modelKey,
  ), [modelKey, models]);

  useEffect(() => {
    let active = true;
    setCatalog(undefined);
    if (!selectedModel) return () => { active = false; };
    void getVerifiedImageCapabilities(projectId, selectedModel.providerId, selectedModel.modelId)
      .then((next) => {
        if (!active) return;
        setCatalog(next);
        setOperation("generate");
        setSelectedAssetIds([]);
        setAspectRatio(next.capabilities.aspectRatios[0] ?? "");
        setResolution(next.capabilities.resolutions[0] ?? "");
        setResultCount(next.capabilities.minResults);
      })
      .catch((reason) => { if (active) setError(errorMessage(reason, "无法读取图片模型能力。")); });
    return () => { active = false; };
  }, [projectId, selectedModel]);

  const capabilities = catalog?.capabilities;
  const routingCandidates = useMemo(() => (models ?? []).filter((entry) => {
    const candidate = routingCatalogs[modelValue(entry)]?.capabilities;
    return Boolean(candidate
      && (operation === "generate" || operation === "edit" && candidate.imageEditing || operation === "super-resolution" && candidate.superResolution)
      && selectedAssetIds.length >= candidate.minReferences && selectedAssetIds.length <= candidate.maxReferences
      && (selectedAssetIds.length === 0 ? operation === "generate" : candidate.referenceTypes.includes(referenceType(operation)))
      && candidate.aspectRatios.includes(aspectRatio) && candidate.resolutions.includes(resolution)
      && resultCount >= candidate.minResults && resultCount <= candidate.maxResults);
  }).map((entry) => ({ provider: entry.providerId, model: entry.modelId })), [models, routingCatalogs, operation, selectedAssetIds.length, aspectRatio, resolution, resultCount]);
  const activeReferenceType = referenceType(operation);
  const selectionValid = Boolean(capabilities)
    && selectedAssetIds.length >= (capabilities?.minReferences ?? 0)
    && selectedAssetIds.length <= (capabilities?.maxReferences ?? -1)
    && (selectedAssetIds.length === 0 ? operation === "generate" : capabilities?.referenceTypes.includes(activeReferenceType))
    && (operation !== "super-resolution" || selectedAssetIds.length === 1);
  const canSubmit = Boolean(
    selectedModel && capabilities && prompt.trim() && accepted && selectionValid
    && capabilities.aspectRatios.includes(aspectRatio)
    && capabilities.resolutions.includes(resolution)
    && resultCount >= capabilities.minResults && resultCount <= capabilities.maxResults,
  );

  function changeOperation(next: Operation) {
    setOperation(next);
    if (next === "super-resolution") setSelectedAssetIds((current) => current.slice(0, 1));
  }

  async function submit() {
    if (!selectedModel || !capabilities || !canSubmit || busy) return;
    setBusy(true);
    setError("");
    try {
      const input = {
        disclosure: {
          provider: selectedModel.providerId,
          model: selectedModel.modelId,
          prompt: prompt.trim(),
          accepted: true as const,
        },
        imageParams: {
          operation,
          references: selectedAssetIds.map((assetId) => {
            const asset = imageAssets.find((candidate) => candidate.id === assetId)!;
            return { assetId, assetVersionId: asset.assetVersionId, referenceType: referenceType(operation) };
          }),
          aspectRatio,
          resolution,
          resultCount,
        },
        caller: "asset-library",
      };
      const pending = pendingKey(`${projectId}:submit`, requestFingerprint(input));
      const response = await submitImageGeneration(projectId, { ...input, idempotencyKey: pending.key, routingPolicyRef });
      clearPendingKey(`${projectId}:submit`, pending);
      setRuns((current) => [response.run, ...current.filter((run) => run.id !== response.run.id)]);
      if (response.run.outputItems.some((item) => item.status === "completed")) onInstalled?.();
    } catch (reason) {
      setError(errorMessage(reason, "无法提交图片生成。"));
    } finally {
      setBusy(false);
    }
  }

  async function cancel(runId: string) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await cancelImageGeneration(projectId, runId);
      setRuns((current) => current.map((run) => run.id === runId ? response.run : run));
    } catch (reason) { setError(errorMessage(reason, "无法取消图片生成。")); }
    finally { setBusy(false); }
  }

  async function retry(runId: string) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const scope = `${projectId}:retry:${runId}`;
      const pending = pendingKey(scope, requestFingerprint({ projectId, runId }));
      const response = await retryImageGeneration(projectId, runId, pending.key);
      clearPendingKey(scope, pending);
      setRuns((current) => [response.run, ...current.filter((run) => run.id !== response.run.id)]);
      if (response.run.outputItems.some((item) => item.status === "completed")) onInstalled?.();
    } catch (reason) { setError(errorMessage(reason, "无法重试图片生成。")); }
    finally { setBusy(false); }
  }

  async function refreshRuns() {
    if (busy) return;
    setBusy(true); setError("");
    try { setRuns((await listImageGenerationRuns(projectId)).runs); }
    catch (reason) { setError(errorMessage(reason, "无法刷新图片生成运行。")); }
    finally { setBusy(false); }
  }

  return <section className="image-generation-panel" aria-labelledby="image-generation-title">
    <h4 id="image-generation-title">图片生成</h4>
    <p>模型能力来自已验证的适配器目录；保存配置或连接成功不会自动开放未知能力。</p>
    {error && <p role="alert">{error}</p>}
    {models === undefined ? <p>正在读取图片模型能力…</p> : models.length === 0
      ? <p>当前没有已通过能力验证的图片模型。保存配置或连接成功不等于模型能力已验证。</p>
      : <>
        <label>图片模型
          <select value={modelKey} onChange={(event) => setModelKey(event.target.value)}>
            {models.map((model) => <option key={`${model.providerId}:${model.modelId}`} value={modelValue(model)}>{model.label}</option>)}
          </select>
        </label>
        {!capabilities ? <p>正在读取所选模型能力…</p> : <>
          <p>参考图片：{capabilities.minReferences}–{capabilities.maxReferences} 张；结果：{capabilities.minResults}–{capabilities.maxResults} 张。比例 {capabilities.aspectRatios.join("、")}；分辨率 {capabilities.resolutions.join("、")}。</p>
          {capabilities.referenceTypes.length > 0 && <p>已验证参考类型：{capabilities.referenceTypes.map((value) => referenceTypeLabels[value]).join("、")}。</p>}
          <label>生成方式
            <select value={operation} onChange={(event) => changeOperation(event.target.value as Operation)}>
              <option value="generate">文生图 / 参考图生成</option>
              {capabilities.imageEditing && <option value="edit">模型图片编辑</option>}
              {capabilities.superResolution && <option value="super-resolution">模型超分辨率</option>}
            </select>
          </label>
          <p>普通本地裁切/缩放不会调用模型，也不会显示为模型图片编辑或模型超分辨率。</p>
          <label>图片提示词<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
          {capabilities.maxReferences > 0 && <fieldset>
            <legend>参考图片</legend>
            {imageAssets.length ? imageAssets.map((asset) => <label key={asset.id}>
              <input
                type="checkbox"
                aria-label={`引用${asset.name}`}
                checked={selectedAssetIds.includes(asset.id)}
                disabled={!selectedAssetIds.includes(asset.id) && selectedAssetIds.length >= capabilities.maxReferences}
                onChange={(event) => setSelectedAssetIds((current) => event.target.checked ? [...current, asset.id] : current.filter((id) => id !== asset.id))}
              />
              {asset.name}
            </label>) : <p>项目中还没有可用图片素材。</p>}
          </fieldset>}
          <label>画面比例<select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}>{capabilities.aspectRatios.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>分辨率<select value={resolution} onChange={(event) => setResolution(event.target.value)}>{capabilities.resolutions.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>结果数量<input aria-label="结果数量" type="number" min={capabilities.minResults} max={capabilities.maxResults} value={resultCount} onChange={(event) => setResultCount(Number(event.target.value))} /></label>
          <label><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />确认发送提示词和所选图片</label>
          {selectedModel && <GenerationRoutingPolicyControl modality="image" currentRoute={{ provider: selectedModel.providerId, model: selectedModel.modelId }} verifiedRoutes={routingCandidates} value={routingPolicyRef} onChange={setRoutingPolicyRef} />}
          <button type="button" className="primary-action" disabled={!canSubmit || busy} onClick={() => void submit()}>{busy ? "正在处理…" : "提交图片生成"}</button>
          {!selectionValid && <p role="status">当前参考图片数量或类型不符合所选模型能力。</p>}
        </>}
      </>}

    {runs.length > 0 && <section aria-label="图片生成历史">
      <div><h5>生成历史</h5><button type="button" disabled={busy} onClick={() => void refreshRuns()}>刷新运行</button></div>
      {runs.map((run) => <article key={run.id}>
        <strong>{statusLabels[run.status] ?? run.status}</strong>
        <p>实际路由：{run.actualRoute?.provider ?? run.provider} · {run.actualRoute?.model ?? run.model}</p>
        <GenerationRouteAudit attempts={run.routeAttempts} />
        {run.executionSemantics?.stateConfidence === "unknown" && <p>运行状态未知，未自动切换 Provider。</p>}
        {run.executionSemantics?.cancelResult === "cancelled" && <p>已取消本地排队任务，未发起 Provider 调用。</p>}
        {(run.billingUnknown || run.executionSemantics?.billing === "unknown") && <p>计费状态未知</p>}
        {run.outputItems.map((item) => <div key={item.resultId}>
          {(item.status === "completed" || item.status === "reused") && item.url
            ? <><a href={item.url} target="_blank" rel="noreferrer" aria-label={`预览${item.name ?? `结果 ${item.index + 1}`}`}>预览 {item.name ?? `结果 ${item.index + 1}`}</a> <a href={item.url} download>下载</a></>
            : item.status === "failed" ? <span>失败 · {item.reason ?? "generation_failed"}</span>
              : <span>{statusLabels[item.status] ?? item.status}</span>}
        </div>)}
        {(run.status === "queued" || run.status === "running") && (run.executionSemantics?.cancel === "supported" || (!run.executionSemantics && run.capabilitySnapshot?.cancel)) && <button type="button" disabled={busy} onClick={() => void cancel(run.id)}>取消</button>}
        {(run.status === "queued" || run.status === "running") && (run.executionSemantics?.cancel === "unsupported" || (!run.executionSemantics && run.capabilitySnapshot && !run.capabilitySnapshot.cancel)) && <p>该运行的冻结能力不支持取消。</p>}
        {["failed", "partial_success", "cancelled", "timed_out"].includes(run.status) && <button type="button" disabled={busy} onClick={() => void retry(run.id)}>重试失败结果</button>}
      </article>)}
    </section>}
  </section>;
}
