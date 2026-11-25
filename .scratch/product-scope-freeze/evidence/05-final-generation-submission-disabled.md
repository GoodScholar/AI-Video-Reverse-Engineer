# 05：最终 AI 视频生成提交已关闭

日期：2026-09-29

范围冻结要求工作台仅保留方案、素材和工作流准备；最终 AI 视频生成必须在外部工具执行。

## 写入口清单与处理结果

| 模块 | 原写入口 | 旧行为 | 当前行为 |
| --- | --- | --- | --- |
| 参考复刻 | `POST /api/projects/{id}/reproduction/runs` | 创建并提交 ComfyUI 运行 | `410 final_generation_disabled` |
| 参考复刻 | `POST /api/projects/{id}/reproduction/runs/{runId}/refresh` | 查询外部运行并写回状态/输出 | `410 final_generation_disabled` |
| 参考复刻 | `POST /api/projects/{id}/reproduction/runs/{runId}/resolve` | 恢复未知运行或允许重试 | `410 final_generation_disabled` |
| 角色动作 | `POST /api/projects/{id}/character-motion/runs` | 创建并提交 ComfyUI 运行 | `410 final_generation_disabled` |
| 角色动作 | `POST /api/projects/{id}/character-motion/runs/{runId}/refresh` | 查询外部运行并写回状态/输出 | `410 final_generation_disabled` |
| 角色动作 | `POST /api/projects/{id}/character-motion/runs/{runId}/resolve` | 恢复未知运行或允许重试 | `410 final_generation_disabled` |

统一响应：`{"code":"final_generation_disabled","message":"当前工作台只准备素材与方案，请导出后在外部工具执行视频生成。"}`。应用中间件在请求解析和来源校验之前拦截上述路径；各模块路由也执行同一策略，防止测试或嵌入式路由绕过应用层。定向测试确认被拒绝时没有运行记录，也没有创建 ComfyUI 客户端实例。

## 保留能力

- `GET /reproduction`、`GET /character-motion` 继续返回历史运行状态、错误和输出元数据。
- 两组 `GET .../runs/{runId}/output/{index}` 继续读取既有输出文件。
- 两组 `GET .../package` 继续导出候选工作流与准备素材，不触发最终生成。
- 正式内容工作室和兼容工作台都以 `preparationOnly` 挂载复刻与角色动作面板；界面只保留准备/导出，并将历史记录和结果以只读方式展示。

## 验证

- 后端定向：`26 passed`（复刻、角色动作及范围边界）。
- 后端完整：`1393 passed, 9 skipped`。
- 前端相关：`28 passed`（复刻与角色动作面板）。
- 前端完整：`455 passed`。
- 前端构建：`npm run build` 成功；仅有既有单包超过 500 kB 的 Vite 提示。
