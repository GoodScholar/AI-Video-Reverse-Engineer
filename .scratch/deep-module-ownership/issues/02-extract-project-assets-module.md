# 02: 提取项目素材模块

Status: resolved
Type: task
Blocked by: none

## What to build

把现有素材库背后的稳定身份、元数据、文件可用性、上传、引用查询和安全删除收进内容项目级“项目素材”深模块。继续使用现有状态文件与 `preproduction/assets` 磁盘目录，但时间线、批量混剪、商品脚本、内容制作阶段和备份不再直接学习该存储形状和路径。

## Current Evidence

- `PreproductionStore` 同时被时间线、批量混剪、商品脚本、内容制作阶段、商品图片和备份直接读取。
- 多个调用方自行遍历 `state["assets"]`、解析 `asset["file"]` 并拼接素材路径。
- 素材删除需要跨制作镜头、节点、候选、当前时间线和历史输出查询引用，但该规则目前由前置工作台路由协调。

## Acceptance Criteria

- [ ] 项目素材模块拥有素材目录、稳定身份、元数据、文件解析、可用性、上传、引用查询和安全删除。
- [ ] 项目素材仍属于内容项目；制作镜头、时间线和批量混剪只以稳定素材标识引用，不获得素材所有权。
- [ ] 各工作流通过窄接口贡献或查询自己的引用事实，不由项目素材模块解释镜头采用、时间线编辑或审核决定。
- [ ] 时间线、批量混剪、商品脚本、内容制作阶段和备份不再直接读取 `PreproductionStore(...).load()["assets"]` 或拼接素材文件路径。
- [ ] 素材上传、重命名、备注、引用预览和删除的现有 HTTP 行为与错误码保持兼容。
- [ ] 现有状态 JSON、素材 ID、`project-files/<project-id>/preproduction/assets` 目录和历史文件保持不变，不执行迁移。
- [ ] 不新增素材库页面、搜索能力、标签体系或其他用户功能。

## Likely Scope

- `backend/app/preproduction.py`
- `backend/app/preproduction_api.py`
- `backend/app/asset_references.py`
- `backend/app/timeline_api.py`
- `backend/app/batch_editing_api.py`
- `backend/app/aigc_content_api.py`
- `backend/app/content_workflow_api.py`
- `backend/app/project_backup.py`
- 对应素材、时间线、批量混剪与备份测试

## Comments

2026-09-27：用户确认这是现有素材库的内部所有权重构，不是新增素材库功能；首轮隐藏既有目录，不搬迁文件。

## Answer

新增 `ProjectAssets` 深模块，在保留既有 `preproduction/state.json`、素材 ID 和 `preproduction/assets` 目录的前提下，统一拥有素材身份、元数据、公开投影、文件解析与可用性、文件安装、引用汇总和安全删除。制作镜头与时间线分别通过窄函数贡献引用事实；时间线、批量混剪、商品脚本、内容制作阶段和备份不再读取素材存储形状或拼接素材路径。

素材上传、重命名、备注、引用预览和删除继续沿用原 HTTP 行为；审查中发现并修复了引用读取失败、不可用文件删除和既有响应字段三处兼容问题。新增模块级回滚与引用保护测试，全量后端回归为 1328 passed、10 skipped；Standards 与 Spec 双轴复核均无剩余发现。
