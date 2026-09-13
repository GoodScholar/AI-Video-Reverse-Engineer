import { type FormEvent, useEffect, useState } from "react";
import { Check, CircleAlert, PlugZap, Save } from "lucide-react";

import type { AnalysisProviderConfiguration, AnalysisProviderId } from "./models";
import {
  type AnalysisProviderConfigurationInput,
  saveAnalysisProviderConfiguration,
  testAnalysisProviderConnection,
} from "./analysisProviderApi";

const DESKTOP_QUERY = "(min-width: 1024px)";
const BAILIAN_MODEL = "qwen3.7-flash";

type Props = {
  providers: AnalysisProviderConfiguration[];
  onProvidersChanged?: (providers: AnalysisProviderConfiguration[]) => void;
  save?: typeof saveAnalysisProviderConfiguration;
  testConnection?: typeof testAnalysisProviderConnection;
};

function useDesktop() {
  const [isDesktop, setIsDesktop] = useState(() => (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(DESKTOP_QUERY).matches
      : false
  ));
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const query = window.matchMedia(DESKTOP_QUERY);
    const update = () => setIsDesktop(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return isDesktop;
}

function providerLabel(provider: AnalysisProviderId) {
  return provider === "bailian" ? "阿里云百炼" : "本地 OpenAI 兼容服务";
}

function initialProvider(providers: AnalysisProviderConfiguration[]): AnalysisProviderId | null {
  return providers.find((provider) => provider.selectedProvider === provider.provider)?.provider
    ?? providers[0]?.provider
    ?? null;
}

function messageFor(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export function AnalysisProviderSettings({
  providers,
  onProvidersChanged = () => undefined,
  save = saveAnalysisProviderConfiguration,
  testConnection = testAnalysisProviderConnection,
}: Props) {
  const isDesktop = useDesktop();
  const [selectedProvider, setSelectedProvider] = useState<AnalysisProviderId | null>(() => initialProvider(providers));
  const selected = providers.find((provider) => provider.provider === selectedProvider) ?? null;
  const [model, setModel] = useState(selected?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(selected?.baseUrl ?? "");
  const [apiKey, setApiKey] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState("");
  const [connectionStatus, setConnectionStatus] = useState("");

  useEffect(() => {
    const next = initialProvider(providers);
    setSelectedProvider((current) => providers.some((provider) => provider.provider === current) ? current : next);
  }, [providers]);

  useEffect(() => {
    const next = providers.find((provider) => provider.provider === selectedProvider);
    setModel(next?.model ?? (selectedProvider === "bailian" ? BAILIAN_MODEL : ""));
    setBaseUrl(next?.baseUrl ?? "");
    setApiKey("");
    setError("");
    setConnectionStatus("");
  }, [providers, selectedProvider]);

  function choose(provider: AnalysisProviderId) {
    if (!isDesktop) return;
    setSelectedProvider(provider);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !isDesktop || isSaving) return;
    setIsSaving(true);
    setError("");
    setConnectionStatus("");
    const input: AnalysisProviderConfigurationInput = {
      model: selected.provider === "bailian" ? BAILIAN_MODEL : model.trim(),
      ...(selected.provider === "local_openai_compatible" ? { baseUrl: baseUrl.trim() } : {}),
      ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
    };
    try {
      const saved = await save(selected.provider, input);
      onProvidersChanged(providers.map((provider) => provider.provider === saved.provider ? saved : {
        ...provider,
        selectedProvider: saved.selectedProvider,
      }));
      setConnectionStatus("配置已保存");
    } catch (saveError) {
      setError(messageFor(saveError, "无法保存分析服务设置，请重试。"));
    } finally {
      setApiKey("");
      setIsSaving(false);
    }
  }

  async function test() {
    if (!selected || !isDesktop || isTesting) return;
    setIsTesting(true);
    setError("");
    setConnectionStatus("");
    try {
      await testConnection(selected.provider);
      setConnectionStatus("连接正常；测试只发送固定探针，不上传项目素材。");
    } catch (testError) {
      setError(messageFor(testError, "无法测试分析服务连接，请重试。"));
    } finally {
      setIsTesting(false);
    }
  }

  return (
    <section className={`analysis-provider-settings${isDesktop ? "" : " analysis-provider-settings--readonly"}`} aria-labelledby="analysis-provider-settings-title">
      <div className="analysis-provider-settings-heading">
        <div>
          <h2 id="analysis-provider-settings-title">分析服务设置</h2>
          <p>密钥只交给本地系统安全存储；此页不会回显、记录或同步密钥。</p>
        </div>
        {!isDesktop && <p className="analysis-readonly">窄屏仅查看配置状态</p>}
      </div>
      {providers.length === 0 ? <p className="analysis-provider-empty">暂时无法读取可用分析服务。</p> : (
        <>
          <fieldset className="analysis-provider-choice" disabled={!isDesktop || isSaving || isTesting}>
            <legend>选择分析服务</legend>
            {providers.map((provider) => (
              <button key={provider.provider} className={provider.provider === selectedProvider ? "analysis-provider-option analysis-provider-option--selected" : "analysis-provider-option"} type="button" aria-pressed={provider.provider === selectedProvider} onClick={() => choose(provider.provider)}>
                <span>{providerLabel(provider.provider)}</span>
                <span>{provider.credentialState === "configured" ? "密钥已配置" : "尚未配置"}</span>
              </button>
            ))}
          </fieldset>
          {selected && (
            <form className="analysis-provider-form" onSubmit={(event) => void submit(event)}>
              <h3>{providerLabel(selected.provider)}</h3>
              {selected.provider === "local_openai_compatible" ? (
                <>
                  <label htmlFor="local-analysis-base-url">本地服务 Base URL</label>
                  <input id="local-analysis-base-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8080" autoComplete="url" disabled={!isDesktop} required />
                  <label htmlFor="local-analysis-model">本地服务模型</label>
                  <input id="local-analysis-model" value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如：vision-local" autoComplete="off" disabled={!isDesktop} required />
                </>
              ) : <p className="analysis-provider-model">已验证模型：<strong>{BAILIAN_MODEL}</strong></p>}
              <label htmlFor="analysis-api-key">{selected.provider === "bailian" ? "百炼 API Key" : "本地服务 API Key（可选）"}</label>
              <input id="analysis-api-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" disabled={!isDesktop} />
              <p className="analysis-provider-key-state">{selected.credentialState === "configured" ? "密钥已配置" : selected.provider === "bailian" ? "需要 API Key 后才能开始分析" : "本地服务允许不填写 API Key"}</p>
              {error && <p className="analysis-provider-error" role="alert"><CircleAlert aria-hidden="true" size={17} />{error}</p>}
              {connectionStatus && <p className="analysis-provider-success" role="status"><Check aria-hidden="true" size={17} />{connectionStatus}</p>}
              {isDesktop && <div className="analysis-provider-actions">
                <button className="secondary-action" type="button" disabled={isTesting || isSaving} onClick={() => void test()}><PlugZap aria-hidden="true" size={17} />{isTesting ? "正在测试…" : "测试连接"}</button>
                <button className="primary-action" type="submit" disabled={isSaving || isTesting}><Save aria-hidden="true" size={17} />{isSaving ? "正在保存…" : `保存${selected.provider === "bailian" ? "百炼" : "本地服务"}配置`}</button>
              </div>}
            </form>
          )}
        </>
      )}
    </section>
  );
}
