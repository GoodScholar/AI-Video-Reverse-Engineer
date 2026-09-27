# 内置浏览器原生视频控件崩溃诊断

日期：2026-09-26。结论：已定位触发路径，并验证稳定替代操作；没有修改产品播放器或媒体文件。浏览器自动化提供方内部故障尚未修复，不能将替代操作记为上游修复。

## 精确症状与最小复现

正式工作台的已完成预览在 Codex 内置浏览器加载正常。通过 `cua` 的无障碍元素编号点击原生播放按钮时，工具返回 `Inspected target navigated or closed`，随后页面显示 `This page crashed`。上次验收与本轮正式页面均复现。

去掉 React、批量任务、配音选择、轮询和样式，仅保留 `repro.html` 的一个原生 `<video controls>` 与同一视频接口，仍在相同操作下崩溃。单页没有脚本逻辑。

从仓库根目录启动只暴露诊断目录的临时服务器：

```sh
.venv/bin/python -m http.server 5190 --bind 127.0.0.1 --directory .scratch/browser-video-crash
```

另需工作台后端 8000 已运行，演示项目与预览仍存在。复现文件仅引用本机公开咖啡技术样例。重建项目后需更新该文件中的预览 URL。

在 `cua_repl` 中执行如下反馈循环，读取当前无障碍树后再点击，不复用旧编号：

```js
let reproTab = await cua.createBrowserTab('iab', 'http://127.0.0.1:5190/repro.html', {visible:false});
let state = await reproTab.getAXState({emit:false});
let play = state.match(/\n\s*(\d+) button/);
try {
  await reproTab.click(Number(play[1]));
  await reproTab.getAXState({emit:false});
  nodeRepl.write('NO_CRASH');
} catch (error) {
  nodeRepl.write({verdict:'REPRODUCED_CRASH', error:String(error).split('data:')[0].slice(0,180)});
}
```

本轮最小页面播放按钮编号为 6，输出：

```text
verdict: REPRODUCED_CRASH
error: Error: Inspected target navigated or closed
```

## 对照结果

| 场景 | 操作 | 结果 |
| --- | --- | --- |
| 正式工作台，内置浏览器 | 无障碍编号点击原生播放按钮 | 页面崩溃 |
| 单视频页面，内置浏览器 | 无障碍编号点击原生播放按钮 | 页面崩溃 |
| 同一单视频页面，内置浏览器 | 点击视频使其获得焦点，再按空格 | 完整播放到 4.166667 秒，ended=true，媒体 error=null |
| 同一单视频页面，内置浏览器 | 按截图坐标点击原生播放按钮 | 正常开始播放，paused=false，媒体 error=null |
| 正式工作台，内置浏览器 | 视频聚焦后按空格 | 完整播放到 4.166667 秒，ended=true，muted=false，媒体 error=null |
| 正式工作台，Chrome（上一轮） | 视频聚焦后按空格 | 完整播放到 4.166667 秒，无媒体错误 |

同一内置浏览器中的成功对照使用同一份 MP4、同一后端接口，排除了“该视频不能解码”和“该接口不能播放”这两个解释。证据定位到原生控件的无障碍自动化点击路径；没有内部崩溃栈，不能进一步断言提供方哪一行代码或哪一个原生模块出错。官方 [Browser 文档](https://learn.chatgpt.com/docs/browser?surface=app) 未给出此原生控件崩溃的专门修复办法。

## 处理与回归边界

自动化验收使用已验证的聚焦视频后空格播放，或按新截图中的按钮坐标点击；避免对原生媒体控件调用无障碍编号点击。正常页面按钮仍可按元素操作。没有重编码视频、重写播放器、安装依赖、修改浏览器设置或开启额外开发者权限。

该故障发生在项目之外的原生控件自动化路径，现有 Python/Vitest 无法执行这个路径，加入模拟播放器测试不能覆盖实际崩溃；回归保留上述最小页面和真实 `cua_repl` 对照。若自动化提供方更新，先跑该最小复现，再恢复原生控件编号操作。

临时诊断服务器和测试标签页在验证后关闭。README、配音验收记录和完成清单同步这个更精确的结论；真实客户听感与商业交付状态没有变化。
