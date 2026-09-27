import { expect, it } from "vitest";
import type { AnalysisProviderConfiguration, Project } from "./models";
import { promptConfigurationIssue } from "./promptAvailability";

const project = { semanticAnalysis: { provider: "bailian", model: "original" } } as Project;
const provider: AnalysisProviderConfiguration = { provider: "bailian", model: "original", credentialState: "configured", baseUrl: null, selectedProvider: "bailian" };
it("提示词沿用原分析模型，配置变化时说明需重新分析", () => {
  expect(promptConfigurationIssue(project, [provider])).toBe("");
  expect(promptConfigurationIssue(project, [{ ...provider, model: "changed" }])).toContain("已变更");
  expect(promptConfigurationIssue(project, [{ ...provider, credentialState: "unconfigured" }])).toContain("未配置");
});
it("本地兼容服务不强制要求 API 密钥", () => {
  const local: AnalysisProviderConfiguration = { ...provider, provider: "local_openai_compatible", credentialState: "unconfigured" };
  expect(promptConfigurationIssue({ ...project, semanticAnalysis: { ...project.semanticAnalysis!, provider: local.provider } }, [local])).toBe("");
});
