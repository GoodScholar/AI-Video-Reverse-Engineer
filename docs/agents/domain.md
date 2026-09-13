# 领域文档规则

本项目采用 single-context 结构。

## 开始工作前

- 读取根目录 `CONTEXT.md`
- 读取 `docs/adr/` 中与当前工作相关的决策记录
- 如果文件尚不存在，继续工作，不提前创建空文档
- 仅在术语或架构决策真正确定后，由领域建模流程创建或更新

## 文件结构

```text
/
├── AGENTS.md
├── CONTEXT.md
├── docs/
│   ├── agents/
│   │   ├── issue-tracker.md
│   │   ├── triage-labels.md
│   │   └── domain.md
│   └── adr/
└── .scratch/
```

## 术语要求

需求、任务、测试和代码应使用 `CONTEXT.md` 中定义的统一术语。若新概念尚未定义，应先确认它是真实的领域概念，再补充领域文档。

## ADR 冲突

如果新方案与现有 ADR 冲突，必须明确指出冲突，不得静默覆盖原决策。
