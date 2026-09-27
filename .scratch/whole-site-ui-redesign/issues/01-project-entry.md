# 01: 项目中心与真实项目导航

Status: resolved
Type: task
Blocked by: None（可立即开始）

## What to build

项目中心与真实项目导航，使内部交付团队能够在已确认视觉体系内完成对应真实流程。规格见 [正式迁入规格](../production-spec.md)。

## Acceptance criteria

- [x] 默认入口读取真实项目，新建失败保留输入并可重试
- [x] 当前项目导航明确，项目内切换保留草稿，跨项目修改隔离
- [x] 没有项目时说明入口所需条件，项目读取失败可重试

## Comments

根据已有迁入实现继续补齐；完成条件以测试与真实浏览器证据为准。

## Answer

真实项目与错误恢复由 ContentStudioApp 4 项行为测试验证；跨页草稿、读取失败、新建失败保留名称已覆盖。八页导航与实际项目读取见验收记录。

详细证据与验收范围见 [正式迁入验收](../production-acceptance.md)。
