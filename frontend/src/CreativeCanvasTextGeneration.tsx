/*
 * Adapted from Toonflow `packages/nodes/textNode/src/index.vue` at
 * d86cce6b689916ab73f75bc4c1f42a2a7424516c (MIT). This keeps its prompt,
 * grouped-model and incremental-version interaction while the durable text-run
 * transport remains supplied by the canvas host.
 */
import { ArrowUp, LoaderCircle, Square } from "lucide-react";
import type { ReactNode } from "react";
import { CreativeCanvasPrompt } from "./CreativeCanvasPrompt";
import "./creativeCanvasTextGeneration.css";

export type CanvasTextModel = { providerId: string; providerLabel: string; modelId: string; label: string; configured: boolean; configurationVersion?: string };
export type CanvasTextVersion = {
  versionId: string; runId: string; status: "pending" | "running" | "installed" | "completed" | "failed" | "cancel_requested" | "cancelled" | "unknown";
  text: string; createdAt: string; error?: string; assetId?: string; cancellationRequested?: boolean; billingUnknown?: boolean;
};
export type CanvasTextGeneration = {
  prompt: string; selectedModel?: { providerId: string; modelId: string; configurationVersion?: string };
  models: CanvasTextModel[]; references: Array<{ id: string; name: string; value: string; avatar?: string }>;
  versions: CanvasTextVersion[]; currentVersionId?: string; runStatus?: CanvasTextVersion["status"] | "submitting" | "queued"; count: number;
  onPromptChange: (prompt: string) => void; onModelChange: (selection: { providerId: string; modelId: string; configurationVersion?: string }) => void;
  onCountChange: (count: number) => void; onRun: () => void; onCancel: () => void; onSelectVersion: (runId: string, versionId: string) => void;
  onInstallVersion: (runId: string, versionId: string) => void;
  onPreviewReference?: (edgeId: string) => void;
};

const labels: Record<CanvasTextVersion["status"], string> = { pending: "等待提交", running: "正在生成", installed: "已完成", completed: "已完成（旧记录）", failed: "失败", cancel_requested: "已请求停止", cancelled: "已取消", unknown: "状态未知" };
function groups(models: CanvasTextModel[]) {
  const result = new Map<string, CanvasTextModel[]>();
  for (const model of models) result.set(model.providerId, [...(result.get(model.providerId) ?? []), model]);
  return [...result.entries()].map(([providerId, values]) => ({ providerId, label: values[0]?.providerLabel || providerId, values }));
}

export function CreativeCanvasTextGeneration({ label, value, referenceList }: { label: string; value: CanvasTextGeneration; referenceList?: ReactNode }) {
  const running = value.runStatus === "pending" || value.runStatus === "submitting" || value.runStatus === "queued" || value.runStatus === "running" || value.runStatus === "cancel_requested";
  const selected = value.models.find(model => model.providerId === value.selectedModel?.providerId && model.modelId === value.selectedModel?.modelId);
  const staleModel = Boolean(selected && selected.configurationVersion !== value.selectedModel?.configurationVersion);
  const selectedValue = value.selectedModel ? JSON.stringify(staleModel ? [value.selectedModel.providerId, value.selectedModel.modelId, value.selectedModel.configurationVersion] : [value.selectedModel.providerId, value.selectedModel.modelId]) : "";
  const canRun = Boolean(value.prompt.trim() && selected?.configured && !staleModel && !running && value.runStatus !== "unknown");
  const choose = (raw: string) => {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (!Array.isArray(parsed) || parsed.length !== 2 || typeof parsed[0] !== "string" || typeof parsed[1] !== "string") return;
      const model = value.models.find(item => item.providerId === parsed[0] && item.modelId === parsed[1]);
      if (model?.configured) value.onModelChange({ providerId: model.providerId, modelId: model.modelId, configurationVersion: model.configurationVersion });
    } catch { /* A malformed select value is not a model identity. */ }
  };
  return <section className="creative-node__text-generation nodrag nopan nowheel" aria-label={`${label}生成设置`}>
    {referenceList}
    <CreativeCanvasPrompt value={value.prompt} label={`${label}生成提示词`} references={value.references} onChange={value.onPromptChange} onPreview={edgeId => value.onPreviewReference?.(edgeId)} />
    <div className="creative-node__text-generation-footer">
      <label className="creative-node__text-model">文本模型<select aria-label="文本模型" value={selectedValue} onChange={event => choose(event.target.value)} disabled={running}><option value="">选择模型</option>{selectedValue && (!selected || staleModel) && <option value={selectedValue} disabled>历史模型（未验证）</option>}{groups(value.models).map(group => <optgroup key={group.providerId} label={group.label}>{group.values.map(model => <option key={`${model.providerId}:${model.modelId}`} value={JSON.stringify([model.providerId, model.modelId])} disabled={!model.configured}>{model.label}{model.configured ? "" : "（未配置）"}</option>)}</optgroup>)}</select></label>
      <label className="creative-node__text-count">候选<select aria-label="文本候选数" value={value.count} disabled={running} onChange={event => value.onCountChange(Number(event.target.value))}>{Array.from({ length: 15 }, (_, index) => index + 1).map(count => <option key={count} value={count}>{count}</option>)}</select></label>
      {running ? <button type="button" className="creative-node__text-send" aria-label="取消文本生成" disabled={value.runStatus === "cancel_requested"} onClick={value.onCancel}><Square size={14} />{value.runStatus === "cancel_requested" ? "停止请求中" : "停止"}</button> : <button type="button" className="creative-node__text-send" aria-label="生成文本" disabled={!canRun} onClick={value.onRun}><ArrowUp size={16} /><span className="creative-canvas__sr-only">生成文本</span></button>}
    </div>
    {staleModel && <p className="creative-node__text-generation-note">模型配置已变化，请重新选择模型。</p>}
    {!selected?.configured && <p className="creative-node__text-generation-note">请先配置可用的文本模型。</p>}
    {["submitting", "queued", "running"].includes(value.runStatus ?? "") && <p className="creative-node__text-generation-note"><LoaderCircle size={13} aria-hidden="true" />正在生成文本…</p>}
    {value.runStatus === "cancel_requested" && <p className="creative-node__text-generation-note">已请求停止；供应商未确认停止前不会自动重试。</p>}
    {value.runStatus === "unknown" && <p className="creative-node__text-generation-note">生成状态未知，请先核实原请求结果，避免重复提交。</p>}
    {value.versions.length > 0 && <div className="creative-node__text-versions" aria-label="文本版本历史">{value.versions.map(version => {
      const current = version.versionId === value.currentVersionId;
      const succeeded = version.status === "installed" || version.status === "completed";
      const late = succeeded && version.cancellationRequested;
      const file = `data:text/plain;charset=utf-8,${encodeURIComponent(version.text)}`;
      return <article key={`${version.runId}:${version.versionId}`}><header><strong>{late ? "取消后晚到，未自动采用" : current ? "当前文本" : labels[version.status]}</strong><time>{new Date(version.createdAt).toLocaleString("zh-CN")}</time></header><p>{version.text || (version.status === "running" ? "正在接收内容…" : "未产生正文")}</p>{version.error && <p role="alert">{version.error}</p>}{version.billingUnknown && <p>本次请求可能已产生费用；再次生成会提交新请求。</p>}{version.text.trim() && <a href={file} download={`${label}-${version.versionId}.txt`} aria-label={`下载文本版本 ${version.versionId}`}>下载 .txt</a>}{succeeded && version.text.trim() && !current && <button type="button" aria-label={`采用文本版本 ${version.versionId}`} onClick={() => value.onSelectVersion(version.runId, version.versionId)}>采用</button>}{version.assetId ? <span className="creative-node__text-version-asset">已入库</span> : (version.status === "installed" || version.status === "failed") && version.text.trim() && <button type="button" aria-label={`将文本版本 ${version.versionId} 加入素材库`} onClick={() => value.onInstallVersion(version.runId, version.versionId)}>入库</button>}</article>;
    })}</div>}
  </section>;
}
