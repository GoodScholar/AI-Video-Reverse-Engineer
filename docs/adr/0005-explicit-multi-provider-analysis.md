# 使用用户显式选择的多供应商分析服务

Status: accepted

Date: 2026-09-12

Supersedes: ADR 0004

MVP 的分析服务边界支持阿里云百炼、本地 OpenAI 兼容服务、OpenAI、Google Gemini、火山方舟豆包、xAI Grok 和 Anthropic Claude。用户在分析前明确选择目录中的供应商和模型；系统只把分析代理发送给该接收方，失败时不得自动切换。各供应商通过统一 `AnalysisProvider` 契约转换为稳定领域结构，云端与百炼 API Key 使用系统安全存储，本地 Key 可选且 Base URL 限回环，项目仅记录供应商、模型和分析版本。连接验证只记录非敏感配置修订、目录版本和稳定状态码；“可用”只对该配置修订的固定探针成立。豆包的图片/结构化契约仍须由真实密钥冒烟确认，模拟测试不构成可用性声明。
