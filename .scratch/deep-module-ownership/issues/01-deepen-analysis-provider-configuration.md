# 01: 提深分析服务配置与验证生命周期

Status: resolved
Type: task
Blocked by: none

## What to build

把分析服务配置的读取、保存、凭据协调、验证归属和并发 CAS 收进一个深模块。`main.py` 中的 HTTP 处理只负责请求解析与稳定错误翻译；供应商连接探针继续通过可替换适配器执行。

## Current Evidence

- `backend/app/main.py` 同时实现配置快照、凭据写入与回退、连接验证和迟到结果检查。
- `backend/app/analysis_settings.py` 暴露 `prepare_save`、`commit`、`record_verification`，调用者必须掌握正确事务顺序。
- 现有测试需要覆盖交错写入、凭据回退、配置竞争和迟到验证结果。

## Acceptance Criteria

- [x] 一个业务模块完整拥有配置身份、非敏感设置、凭据协调、验证结果与 CAS 规则。
- [x] 调用方不再自行编排 `prepare → credential write → commit → rollback` 顺序。
- [x] 连接验证只更新发起时的配置修订；配置变化后的迟到成功或失败不得覆盖当前状态。
- [x] 连接测试与语义分析从同一权威快照取得供应商、模型、地址和凭据状态。
- [x] 文件持久化、系统安全存储和供应商探针作为内部可替换适配器，竞争测试通过模块接口验证，不继承或改写私有读取实现。
- [x] 保持分析供应商 HTTP 结构、错误码、配置 JSON、凭据位置、模型目录和无静默切换规则兼容。
- [x] 不新增依赖，不改变供应商网络协议或语义分析结果结构。

## Likely Scope

- `backend/app/main.py`
- `backend/app/analysis_settings.py`
- `backend/app/credential_store.py`
- `backend/app/analysis_providers/`
- `backend/tests/test_analysis_settings.py`
- `backend/tests/test_semantic_analysis_api.py`

## Answer

- `AnalysisProviderConfigurations` 通过 `list`、`save`、`verify`、`require_configured` 和 `use_provider` 统一拥有配置快照、凭据协调、供应商运行时绑定、验证结果及修订 CAS。
- `main.py` 不再编排 `prepare → credential write → commit → rollback`，仅解析 HTTP 请求并把模块异常翻译为既有稳定错误码。
- 连接探针在模块锁外运行，成功与失败结果都只通过发起时的修订 CAS 持久化；语义分析与连接测试通过同一模块快照绑定供应商、模型、地址和凭据。
- 竞争测试改为经模块接口验证并发保存、迟到成功、迟到失败和凭据回退，不再继承或改写私有读取实现。
- 验证：`PYTHONPATH=. uv run --with pytest pytest -q` → `1320 passed, 10 skipped`；独立审查复核后无 Critical、Important 或 Minor 问题。

## Comments

2026-09-27：架构体检首选项。用户确认按推荐方案完整拥有配置快照、凭据协调、验证结果和并发 CAS，同时保持所有外部契约兼容。
