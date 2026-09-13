# 参考图片、多供应商分析与 MiniMax H3：实施计划索引

> 本规格跨越五个可独立验收的子系统。按顺序执行以下计划；不得并行修改共享的 `Project`、`LocalPreprocessing` 或前端项目状态模型。

**总目标：** 在不破坏既有视频项目的前提下，支持参考图片、七种语义分析方式、Wan2.2/MiniMax H3 智能推荐和受控 ComfyUI 工作流。

**设计规格：** `.scratch/ai-video-reverse-engineer/04-image-reference-and-multi-provider-analysis-design.md`

**任务归属：** 计划 04a～04d 完成 Ticket 04；计划 04e 在 Ticket 05～07 前置完成后扩展 Ticket 08～10，避免形成 Ticket 04 → 05 → 06 → 07 → 04 的循环依赖。

## 执行顺序

1. `04a-unify-reference-media-implementation-plan.md`
   - 交付统一 `ReferenceMedia`、旧项目迁移、图片上传和参考素材界面。
2. `04b-image-local-preprocessing-implementation-plan.md`
   - 交付图片标准化、分析代理、可恢复阶段任务和前端状态。
3. `04c-semantic-analysis-core-implementation-plan.md`
   - 交付统一分析协议、安全配置、百炼/本地兼容基准适配器和分析界面。
4. `04d-cloud-analysis-providers-implementation-plan.md`
   - 交付 OpenAI、豆包、Gemini、Grok、Claude 适配器及契约验证。
5. `04e-i2v-strategy-and-workflows-implementation-plan.md`
   - 交付确定性策略推荐、Wan2.2/MiniMax H3 模板、高级设置和 Queue 验证。

## 全局阶段门

- 每份计划必须先读设计规格和前一份计划的完成记录。
- 每项实现使用测试先行：先看到目标测试因缺失行为失败，再写最小实现。
- 每份计划结束时执行后端全量测试、前端全量测试、颜色对比校验和生产构建。
- 当前目录没有 `.git` 元数据，计划中的提交点改为“验证检查点”；若执行环境恢复 Git，则按每个任务给出的建议提交信息提交。
- 第 4 份计划需要真实供应商密钥才能完成“可用”标记；没有密钥时可以完成代码和模拟契约测试，但对应供应商必须保持“未验证”。
- 进入第 5 份计划前，必须先完成现有 Ticket 05、06、07 的前置链路；其中 `07-generate-prompts-on-demand-implementation-plan.md` 必须先适配统一 `ReferenceMedia` 和新版 `StructuredVisualAnalysis`。第 5 份计划复用 `Project.promptGeneration.prompts`，不得另建平行 Prompt 字段。
- 第 5 份计划需要真实本地 ComfyUI 环境才能把模板从 `candidate` 升为 `executable`；没有该环境时不得绕过验收。

## 全量验证命令

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```
