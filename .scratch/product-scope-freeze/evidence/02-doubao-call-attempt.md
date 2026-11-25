# 票据 02：豆包正式生成调用结果

- 日期：2026-09-29
- 项目：`大窑 · 网络图片验收`
- 内容项目 ID：`697b8d71-b525-4011-ab90-b7f4563bc8f2`

## 调用前确认

- 供应商：火山方舟豆包（`doubao`）
- 模型：`doubao-seed-2.1-pro`
- 创作简报 revision：4
- 披露摘要：`48750a59d0a3b1ab8c605bf0eb3d52ca4a97f283241453fd6a79c2f7e7a8892c`
- 发送范围：5 条商品事实、2 项允许素材引用；不发送原始图片、视频或本地文件
- 用户已在对话中明确确认执行，正式界面中也勾选了“我已核对本次发送内容”
- [确认提交前的正式界面](./02-approved-disclosure-before-call.png)

## 实际调用结果

- 正式界面只提交了一次 `POST /api/projects/697b8d71-b525-4011-ab90-b7f4563bc8f2/aigc-content/candidates`。
- 后端向豆包 Coding Plan Responses 地址 `https://ark.cn-beijing.volces.com/api/coding/v3/responses` 发起请求。
- 供应商传输层在收到可用响应前进入 `timeout`，本地接口返回 `422 Unprocessable Content`。
- 用户可见错误为：`分析服务响应超时，请稍后重试。`
- [正式界面的超时结果](./02-doubao-timeout.png)

## 持久化核对

- `backend/data/project-files/697b8d71-b525-4011-ab90-b7f4563bc8f2/aigc-content/state.json` 未因本次失败更新。
- 创作简报仍为 revision 4。
- 脚本候选仍为 0 条。
- 未形成 generation ID、候选版本或批量制作交接快照。
- 本地传输边界的总截止时间上限为 120 秒；超时前没有获得供应商 request ID，因此无法从本地证明外部请求是否已计费或稍后完成。

## 重试门禁

本次没有自动重试。再次调用可能产生第二次外部调用或费用，应先由用户决定是否重试，以及是否先调整超时/调用方式。
