# 02: 资料、脚本确认与真实交接

Status: resolved
Type: task
Blocked by: 01

## What to build

资料、脚本确认与真实交接，使内部交付团队能够在已确认视觉体系内完成对应真实流程。规格见 [正式迁入规格](../production-spec.md)。

## Acceptance criteria

- [x] 生成前保存有效资料，保存失败不预览或发送旧内容
- [x] 发送内容与目标服务可核对，显式确认后生成
- [x] 脚本逐条修改保存确认，五条当前版本确认后交接
- [x] 素材变化与脚本页预览对应真实当前项目

## Comments

根据已有迁入实现继续补齐；完成条件以测试与真实浏览器证据为准。

## Answer

AigcCreator 13 项及 ContentStudioApp 预览测试通过：资料独立保存、服务刷新、发送确认、候选保存确认交接、逐镜头预览及无候选说明。未调用付费脚本服务。

详细证据与验收范围见 [正式迁入验收](../production-acceptance.md)。
