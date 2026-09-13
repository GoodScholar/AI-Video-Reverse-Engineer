# 任务系统：本地 Markdown

本项目的产品规格和任务保存在 `.scratch/` 目录。

## 目录约定

- 每项功能一个目录：`.scratch/<feature-slug>/`
- 产品规格：`.scratch/<feature-slug>/spec.md`
- 实施任务：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- 每个任务必须是独立文件，并从 `01` 开始编号
- 任务状态记录在文件顶部的 `Status:` 字段
- 讨论记录追加在文件底部的 `## Comments` 下

## 发布需求

当技能要求“发布到任务系统”时，在 `.scratch/<feature-slug>/` 下创建对应 Markdown 文件。

## 获取任务

读取用户指定的任务路径或编号。

## Wayfinder 约定

- 决策地图：`.scratch/<effort>/map.md`
- 决策票据：`.scratch/<effort>/issues/<NN>-<slug>.md`
- 类型字段：`Type: research|prototype|grilling|task`
- 状态字段：`Status: claimed|resolved`
- 依赖字段：`Blocked by: NN, NN`
- 领取任务时先写入 `Status: claimed`
- 完成后在 `## Answer` 下记录结论，并改为 `Status: resolved`
