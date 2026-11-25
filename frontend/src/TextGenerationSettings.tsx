import { type FormEvent, useEffect, useRef, useState } from "react";
import { getTextGenerationConfiguration, saveTextGenerationConfiguration, verifyTextGenerationModel, type TextGenerationConfiguration } from "./textGenerationApi";

// Uses the application's existing service-settings form and credential contract.
export function TextGenerationSettings() {
  const [configuration, setConfiguration] = useState<TextGenerationConfiguration>();
  const [baseUrl, setBaseUrl] = useState("");
  const [models, setModels] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const alive = useRef(true);
  const loadSequence = useRef(0);
  const apply = (value: TextGenerationConfiguration) => {
    setConfiguration(value); setBaseUrl(value.baseUrl ?? "");
    setModels(value.models.map(model => model.modelId).join("\n")); setApiKey("");
  };
  async function load() {
    const sequence = ++loadSequence.current;
    setBusy(true); setError(""); setNotice("");
    try { const value = await getTextGenerationConfiguration(); if (alive.current && sequence === loadSequence.current) apply(value); }
    catch (reason) { if (alive.current && sequence === loadSequence.current) setError(reason instanceof Error ? reason.message : "无法读取文本服务配置。"); }
    finally { if (alive.current && sequence === loadSequence.current) setBusy(false); }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, []);
  const dirty = baseUrl !== (configuration?.baseUrl ?? "") || models !== (configuration?.models ?? []).map(model => model.modelId).join("\n") || Boolean(apiKey);
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy || !configuration) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const saved = await saveTextGenerationConfiguration({ baseUrl: baseUrl.trim(), models: [...new Set(models.split("\n").map(value => value.trim()).filter(Boolean))].map(modelId => ({ modelId })), revision: configuration.revision, ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}) });
      if (alive.current) { apply(saved); setNotice("配置已保存；未自动发起模型验证。请验证后在画布选择模型。"); }
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "无法保存文本服务配置。"); }
    finally { if (alive.current) setBusy(false); }
  }
  async function verify(modelId: string, mode: "text" | "image") {
    if (busy || dirty || !configuration?.revision) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const verified = await verifyTextGenerationModel(configuration.revision, modelId, mode);
      if (alive.current) { apply(verified); setNotice("模型验证完成，能力状态已更新。"); }
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "模型验证失败。"); }
    finally { if (alive.current) setBusy(false); }
  }
  return <section className="analysis-provider-settings" aria-labelledby="text-generation-settings-title">
    <h2 id="text-generation-settings-title">文本生成服务</h2>
    <p>用于画布文案生成，支持 OpenAI 兼容 Chat Completions 流式接口。密钥保存在本机安全存储。</p>
    {error && <p className="analysis-provider-error" role="alert">{error}</p>}{notice && <p className="analysis-provider-success" role="status">{notice}</p>}
    {!configuration ? <button className="secondary-action" type="button" disabled={busy} onClick={() => void load()}>{busy ? "正在读取文本配置…" : "重新读取文本配置"}</button> : <>
      <form className="analysis-provider-form" onSubmit={event => void save(event)}><fieldset disabled={busy}>
        <legend>服务连接</legend>
        <label>文本服务 Base URL<input type="url" required value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://your-provider.example/v1" /></label>
        <label>文本模型（每行一个）<textarea required rows={3} value={models} onChange={event => setModels(event.target.value)} /></label>
        <label>文本服务 API Key<input type="password" autoComplete="off" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder={configuration.credentialState === "configured" ? "已保存，留空保留" : "请输入服务密钥"} /></label>
        <div className="analysis-provider-actions"><button className="primary-action" type="submit">保存文本生成配置</button></div>
      </fieldset></form>
      {error && <button className="secondary-action" type="button" disabled={busy} onClick={() => void load()}>重新读取（放弃此表单修改）</button>}
      <p>验证会向所选模型发送测试文字或两张测试图片，可能计费，不发送项目素材。修改配置后请先保存。</p>
      <ul>{configuration.modelCatalog.map(model => <li key={model.modelId}>
        <strong>{model.label}</strong><p>{model.textVerified ? model.imageVerified ? "文本与两图引用已验证" : "文本已验证；图片未验证" : "尚未验证，画布暂不可执行"}</p>
        <div className="analysis-provider-actions"><button className="secondary-action" type="button" disabled={busy || dirty || configuration.credentialState !== "configured"} aria-label={`验证 ${model.modelId} 文本（可能计费）`} onClick={() => void verify(model.modelId, "text")}>验证文本（可能计费）</button>
        <button className="secondary-action" type="button" disabled={busy || dirty || configuration.credentialState !== "configured"} aria-label={`验证 ${model.modelId} 图文（可能计费）`} onClick={() => void verify(model.modelId, "image")}>验证图文（可能计费）</button></div>
      </li>)}</ul>
    </>}
  </section>;
}
