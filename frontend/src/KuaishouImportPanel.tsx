import { useEffect, useState } from "react";
import { cancelKuaishouImportRun, getKuaishouDisclosure, getKuaishouImportRun, importKuaishouVideo, probeKuaishouVideo, retryKuaishouImportRun, type AssetRole, type KuaishouDisclosure, type KuaishouImportRun, type KuaishouProbe } from "./preproductionApi";

export function KuaishouImportPanel({ projectId, role, disabled, onInstalled }: { projectId: string; role: AssetRole; disabled: boolean; onInstalled?: () => void }) {
  const [url, setUrl] = useState("");
  const [rights, setRights] = useState(false);
  const [externalRequestAccepted, setExternalRequestAccepted] = useState(false);
  const [disclosure, setDisclosure] = useState<KuaishouDisclosure | null>(null);
  const [session, setSession] = useState(false);
  const [probe, setProbe] = useState<KuaishouProbe | null>(null);
  const [run, setRun] = useState<KuaishouImportRun | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    void getKuaishouDisclosure(projectId).then(setDisclosure).catch(() => setDisclosure(null));
    const id = sessionStorage.getItem(`aivre:kuaishou-import:${projectId}`);
    if (id) void getKuaishouImportRun(projectId, id).then(result => setRun(result.run)).catch(() => sessionStorage.removeItem(`aivre:kuaishou-import:${projectId}`));
  }, [projectId]);

  async function inspect() {
    if (pending || disabled) return;
    setPending(true);
    try { setError(""); setProbe(await probeKuaishouVideo(projectId, url, rights, session)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取快手链接。"); }
    finally { setPending(false); }
  }

  async function start() {
    if (pending || disabled || !probe || !externalRequestAccepted || !confirm(`即将向 ${probe.externalRequest.downloadHosts.join("、")} 发起下载请求`)) return;
    setPending(true);
    try {
      setError("");
      const result = await importKuaishouVideo(projectId, { url, role, rightsConfirmed: rights, externalRequestAccepted, useSession: session });
      setRun(result.run); sessionStorage.setItem(`aivre:kuaishou-import:${projectId}`, result.run.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法导入快手视频。"); }
    finally { setPending(false); }
  }

  async function refresh() {
    if (!run || pending) return;
    setPending(true);
    try {
      const result = await getKuaishouImportRun(projectId, run.id);
      setRun(result.run);
      if (["completed", "failed", "cancelled"].includes(result.run.status)) {
        sessionStorage.removeItem(`aivre:kuaishou-import:${projectId}`);
        if (result.run.status === "completed" && result.run.uploadedAssetId && !disabled) onInstalled?.();
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取快手导入状态。"); }
    finally { setPending(false); }
  }

  async function changeRun(action: "cancel" | "retry") {
    if (!run || pending || disabled) return;
    setPending(true);
    try {
      setError("");
      const result = action === "cancel"
        ? await cancelKuaishouImportRun(projectId, run.id)
        : await retryKuaishouImportRun(projectId, run.id);
      setRun(result.run);
      if (action === "retry") sessionStorage.setItem(`aivre:kuaishou-import:${projectId}`, result.run.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法更新快手导入状态。"); }
    finally { setPending(false); }
  }

  return <section className="preproduction-platform-import" aria-label="快手视频导入">
    <h4>导入快手视频</h4>
    <p>支持标准页面、分享文本和 v.kuaishou.com 短链；不会绕过验证码、风控、私密、地区或下载限制。</p>
    <small>适配器：kuaishou-public-page 1.0.0 · 状态：可用 / 需要登录 / 暂时失效 / 不支持该链接 · 最近真实验证：尚无真实验证（受控测试不计入）</small>
    {disclosure ? <p>{disclosure.purpose}</p> : <p>正在读取外发说明…</p>}
    <label>快手链接<input aria-label="快手链接" value={url} onChange={event => { setUrl(event.target.value); setProbe(null); }} /></label>
    <label><input type="checkbox" checked={rights} onChange={event => setRights(event.target.checked)} />我确认拥有该视频的使用权</label>
    <label><input type="checkbox" disabled={!disclosure} checked={externalRequestAccepted} onChange={event => setExternalRequestAccepted(event.target.checked)} />我已阅读并同意外发范围</label>
    <label><input type="checkbox" checked={session} onChange={event => setSession(event.target.checked)} />使用系统安全存储中的合法快手会话</label>
    <button type="button" className="secondary-action" disabled={pending || disabled || !disclosure || !rights || !externalRequestAccepted || !url.trim()} onClick={() => void inspect()}>读取链接</button>
    {probe && <div role="status"><strong>{probe.title}</strong><small>适配器：{probe.adapter.id} {probe.adapter.version} · 状态：{probe.status} · 最近真实验证：{probe.lastRealVerificationAt ?? "尚无真实验证（受控测试不计入）"}</small><button type="button" className="primary-action" disabled={pending || disabled} onClick={() => void start()}>确认权利并下载</button></div>}
    {run && <div role="status"><strong>导入运行：{run.status}</strong><small>{run.error ? `诊断：${run.error}` : `运行标识：${run.id}`}</small><button type="button" disabled={pending} onClick={() => void refresh()}>刷新导入状态</button>{["queued", "running", "cancel_requested"].includes(run.status) && <button type="button" disabled={pending || disabled} onClick={() => void changeRun("cancel")}>取消导入</button>}{["failed", "cancelled"].includes(run.status) && <button type="button" disabled={pending || disabled} onClick={() => void changeRun("retry")}>重试导入</button>}</div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
