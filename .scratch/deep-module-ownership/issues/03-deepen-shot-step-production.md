# 03: 收拢镜头步骤编辑与失效传播

Status: resolved
Type: task
Blocked by: 02

## What to build

把制作镜头与镜头步骤的编辑校验、依赖传播、候选关联、采用有效性和交付检查收进一个深业务模块。HTTP 路由负责输入输出与错误翻译，运行队列和 FFmpeg 适配器负责执行，但不拥有业务决定。

## Current Evidence

- `preproduction_api.py` 同时包含镜头校验、编辑派生、节点下游失效、候选历史、交付检查、队列提交和产物发布。
- 保存编辑与重跑节点分别解释下游失效，规则需要同步维护。
- 纯领域行为主要通过完整 HTTP 路由测试，真实测试表面与领域接口不一致。

## Acceptance Criteria

- [x] 一个业务模块拥有制作镜头和镜头步骤的编辑校验、依赖关系与失效传播。
- [x] 创作需求、提示词、节点输入或参数变化后，所有受影响下游节点由同一规则变为待生成或失效。
- [x] 候选结果关联、人工检查、采用理由和方案变化有效性由同一模块派生。
- [x] 交付检查消费当前镜头、步骤产物和候选事实，不在 HTTP 路由中重复解释状态。
- [x] FFmpeg 执行、文件发布和本地队列保留为适配器；运行完成不自动采用候选或通过检查。
- [x] 领域规则通过模块接口直接测试；HTTP 测试集中验证契约、持久化和适配器接线。
- [x] 保持前置工作台 HTTP、状态 JSON、错误码、历史产物和最终 AI 视频仍在外部完成的范围兼容。

## Likely Scope

- `backend/app/preproduction_api.py`
- `backend/app/preproduction.py`
- `backend/app/shot_results.py`
- `backend/app/preproduction_nodes.py`
- `backend/tests/test_preproduction_api.py`
- 新的业务模块直接测试

## Comments

2026-09-27：被项目素材模块阻塞，避免在提取过程中继续依赖前置工作台私有的素材状态形状。

## Answer

- 新增 `ShotProduction` 深模块，统一拥有镜头与步骤编辑校验、依赖失效、运行状态、候选历史和交付检查。
- HTTP 路由仅保留请求校验、错误翻译、持久化与执行适配；FFmpeg、队列和产物发布边界保持不变。
- 候选上传恢复原有前置校验顺序，运行中写入限制由模块接口自身保证。
- 领域与 HTTP 回归测试通过；Standards 和 Spec 双轨审查均无剩余发现。
