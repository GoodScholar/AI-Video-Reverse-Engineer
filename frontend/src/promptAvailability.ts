import type { AnalysisProviderConfiguration, Project } from "./models";

// Both prompt endpoints reuse the provider/model recorded on the analysis task.
export function promptConfigurationIssue(project: Project, providers?: AnalysisProviderConfiguration[]): string {
  if (!providers) return "";
  const task = project.semanticAnalysis;
  const provider = task
    ? providers.find((item) => item.provider === task.provider)
    : providers.find((item) => item.provider === item.selectedProvider);
  if (!provider?.model || (provider.provider !== "local_openai_compatible" && provider.credentialState !== "configured")) {
    return "分析服务未配置：请在工具中的「分析服务设置」保存模型与所需凭据。";
  }
  if (task && provider.model !== task.model) return "原分析模型配置已变更，请重新完成语义分析后生成提示词。";
  return "";
}
