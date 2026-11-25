import { useEffect, useState } from "react";
import { readApiError } from "./referenceMediaApi";

const endpoint = "/api/source-sessions/douyin";

export function DouyinSessionSettings() {
  const [configured, setConfigured] = useState(false);
  const [cookie, setCookie] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async () => {
    try {
      const response = await fetch(endpoint);
      if (!response.ok) throw new Error(await readApiError(response, "无法读取抖音会话安全存储状态。"));
      setConfigured(Boolean((await response.json() as { configured?: boolean }).configured));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取抖音会话安全存储状态。"); }
  };
  useEffect(() => { void refresh(); }, []);
  const save = async () => {
    if (!cookie.trim() || busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(endpoint, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cookie }) });
      if (!response.ok) throw new Error(await readApiError(response, "无法保存抖音会话。"));
      setCookie(""); setConfigured(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法保存抖音会话。"); }
    finally { setBusy(false); }
  };
  const forget = async () => {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(endpoint, { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "无法删除抖音会话。"));
      setConfigured(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法删除抖音会话。"); }
    finally { setBusy(false); }
  };
  return <section aria-label="抖音会话安全存储"><h3>抖音会话（可选）</h3><p>仅在需要访问你有权使用的内容时保存 Netscape Cookie 文本到系统安全存储。不会写入项目、运行记录或返回给页面。</p><p>当前状态：{configured ? "已配置" : "未配置"}</p><label>会话 Cookie<textarea aria-label="抖音会话 Cookie" value={cookie} onChange={event => setCookie(event.target.value)} autoComplete="off" /></label><button type="button" className="sp-button" disabled={busy || !cookie.trim()} onClick={() => void save()}>{busy ? "正在保存…" : "保存到系统安全存储"}</button>{configured && <button type="button" className="sp-button" disabled={busy} onClick={() => void forget()}>删除已保存会话</button>}{error && <p role="alert">{error}</p>}</section>;
}
