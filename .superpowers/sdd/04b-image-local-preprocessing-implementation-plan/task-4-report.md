# Task 4 实施报告：图片本地预处理界面

## 状态

已完成。图片和视频本地预处理现在以 `mediaType` 为判别边界：共享启动、轮询、重试外壳，分别呈现各自的阶段与摘要。

## 实现

- 前端模型拆分图片/视频阶段、代理摘要和本地预处理判别联合；视频摘要必须声明 `mediaType: "video"`，图片摘要为 `mediaType: "image"`。
- 图片在桌面端可手动启动；窄屏继续只读，且不会显示启动或重试操作。
- 图片任务显示四个阶段：图像解码、方向与色彩标准化、分析代理生成、初步可复刻性判断。
- 图片完成摘要根据已校验图片尺寸和最长边 2048px 规则显示代理尺寸；透明图片明确说明已白底处理，并只展示待语义分析确认，不混入镜头或运动结论。
- 图片排队/运行时沿用项目页的替换锁定；图片任务进入失败时桌面端焦点移到重试按钮，启动成功后状态标题获得焦点。
- 保留原有防陈旧响应、单一轮询链与取消逻辑；仅按 `mediaType` 分支，不从可选字段推断素材类型。

## TDD 证据

先在 `LocalPreprocessingPanel.test.tsx` 写入图片启动、四阶段、图片摘要及禁止视频摘要的失败测试，再执行：

```sh
npm --prefix frontend test -- LocalPreprocessingPanel.test.tsx App.test.tsx
```

RED：2 个失败、35 个通过。失败分别证明旧界面仍显示“图片后续版本提供”，以及把图片摘要传给视频 `motionText()` 后读取不存在的运动字段并崩溃。

最小实现后，同一命令为 40 个测试通过；补充覆盖图片任务替换锁定、窄屏只读与失败焦点。

## 验证

```text
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests
645 passed, 8 skipped

npm --prefix frontend test
104 passed

node frontend/scripts/verify-color-contrast.mjs
颜色对比度验证通过

npm --prefix frontend run build
tsc -b && vite build 成功
```

工作树内没有 `.venv/bin/pytest`，因此后端验证使用仓库共享的 `../../.venv/bin/pytest`；其余命令与任务简报一致。

## Fix round 1：完成态焦点

### RED

先为初始 `queued` 和 `running` 的项目添加轮询完成后的焦点测试，并确认普通初始完成态不会抢占焦点：

```sh
npm --prefix frontend test -- LocalPreprocessingPanel.test.tsx App.test.tsx
```

结果：`41 passed, 2 failed`。两个失败均为预期行为：轮询返回 completed 后，焦点仍留在“其他控件”，未移动到“本地预处理已完成”标题。

### GREEN

状态引用现在同时记录项目 ID 与上一次状态；仅在同一项目从 `queued` 或 `running` 转为 `completed` 时聚焦状态标题。初始完成态、项目切换、卸载和被既有请求代次防护丢弃的旧响应均不触发该焦点转移；失败态原有的重试按钮焦点保持不变。

```text
npm --prefix frontend test -- LocalPreprocessingPanel.test.tsx App.test.tsx
43 passed

npm --prefix frontend run build
tsc -b && vite build 成功
```
