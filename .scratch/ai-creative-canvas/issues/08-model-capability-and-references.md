# 08: 模型能力与完整引用编辑

Status: ready-for-agent
Blocked by: 无

## What to build

每节点选择真实配置模型，用其能力选项设置生成并编辑多模态引用。

依据：../spec.md 和 ../parity-acceptance.md。继承源码复用优先、商用验收及已授权持续执行要求。完成本票不代表整个画布完成。

## Acceptance criteria

- [ ] 模型分组、分辨率/比例/输入上限来源可核验
- [ ] 切换模型处理不兼容参数，服务端拒绝非法组合
- [ ] 富文本引用随排序/删除保持素材身份，执行快照一致
