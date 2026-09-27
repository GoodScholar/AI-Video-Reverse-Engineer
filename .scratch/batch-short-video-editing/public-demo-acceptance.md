# 第一阶段公开素材演示验收

验收日期：2026-09-26  
结论：**公开素材技术演示通过；真实客户内容与商业效果尚未验收。**

用户确认暂无真实客户素材，同意先搜集公开素材验证第一阶段。本次仅以无品牌咖啡制作过程验证批量混剪、字幕、审核和导出。文案只描述可见动作，不涉及商品功效、价格或品牌，也不暗示画面人物为某商品背书。

## 素材与许可

[Pexels 许可](https://www.pexels.com/license/)允许下载、修改和营销使用视频，禁止暗示画面人物或品牌为产品背书。下列原片下载于 2026-09-25，保存在本机 `/Users/shen/.cache/aivre/phase1-demo/sources/`，用于验收的 3 秒竖版片段由原片裁切转码得到。

| 素材 | 作者 | 页面 | 原片 SHA-256 |
| --- | --- | --- | --- |
| 咖啡豆入磨豆机 | Tim Douglas | [Pexels 6202080](https://www.pexels.com/video/man-pouring-coffee-beans-6202080/) | `61a1694771cbda1e7c6dc9a02dbd43770f9a1324b3231f0a1b2f014d049259a1` |
| 热水注入滤杯 | Nicola Barts | [Pexels 7930822](https://www.pexels.com/video/a-person-pouring-hot-water-in-a-coffee-filter-cup-7930822/) | `9b0729dc8809ea1051af9c8a02ef3244cc4923a4dce8eaada0fb3cc418cd5019` |
| 近景冲泡咖啡粉 | Michael Burrows | [Pexels 7118149](https://www.pexels.com/video/close-up-video-of-a-drip-coffee-7118149/) | `75e2216f3b368769725d93ebb5fce20fff5c455bb2d84cbe54dcabc2cb3f4346` |
| 黑咖啡入杯 | Michael Burrows | [Pexels 7118100](https://www.pexels.com/video/pouring-black-coffee-into-a-cup-7118100/) | `f2ca77f0df0a2efc534e262380d9c4b8e8fe08a69d50329d04244da44f969dcd` |
| 意式咖啡机萃取 | Thomas Windisch | [Pexels 4074925](https://www.pexels.com/video/brewing-of-coffee-pouring-into-a-cup-4074925/) | `4619fca2db34d71ce9a0a30add296613057f1089e764c9afc84dbdb4b24870cd` |

真人普通话识别样本来自 Wikimedia Commons 的 [Introduction In Chinese.ogg](https://commons.wikimedia.org/wiki/File:Introduction_In_Chinese.ogg)，作者 Germartin1，页面声明为 CC0 1.0；原片 SHA-256：`fd6cd39bdf6f725f0adac3f17d31428ea601b569e1b6a615623b03312f16b083`。语音内容是自我介绍，与咖啡无关，因此只作为独立字幕技术检验，不计作咖啡营销成片。

## 实际执行

使用真实批量编辑 API、FFmpeg 渲染器与本机多语言 Whisper 模型。运行记录和输出位于 `/Users/shen/.cache/aivre/phase1-demo/`，结构化结果见 `result.json`。每条咖啡片段的屏幕文案均按画面人工核对后保存，再生成预览和审核。

| 候选 | 镜头组合 | 预览 | 审核 | 导出 |
| --- | --- | --- | --- | --- |
| 备豆与手冲 | 咖啡豆 → 热水注入滤杯 | 成功 | 新版重新通过 | [声画字幕完整 MP4](</Users/shen/.cache/aivre/phase1-demo/voiced-approved-备豆与手冲.mp4>) |
| 手冲萃取 | 热水注入滤杯 → 近景冲泡咖啡粉 | 成功 | 新版重新通过 | [声画字幕完整 MP4](</Users/shen/.cache/aivre/phase1-demo/voiced-approved-手冲萃取.mp4>) |
| 出杯过程 | 近景冲泡咖啡粉 → 黑咖啡入杯 | 成功 | 通过 | 未导出 |
| 意式制作 | 咖啡豆 → 意式萃取 | 成功 | 通过 | 未导出 |
| 浓缩到成杯 | 意式萃取 → 黑咖啡入杯 | 成功 | 通过 | 未导出 |

五条均为两个不同画面片段组成的 6 秒短片；预览输出为 640×1138、30 fps，两个交付 MP4 为 720×1280、30 fps。最初两个咖啡输出为静音，不能单凭音轨存在认定声画完整。用户指出后，已给这两条增加与画面对应的本机合成测试配音，并重新识别、修订字幕、预览、审核和导出；新版作为交付演示证据。

独立语音变体使用 8.5 秒真人普通话片段。Whisper 实际识别出“我的新曲爱好”，人工修订为“我的兴趣爱好”，同时修订姓名用字和标点；随后完成[带字幕预览](</Users/shen/.cache/aivre/phase1-demo/speech-caption-preview.mp4>)、审核及[带字幕最终 MP4](</Users/shen/.cache/aivre/phase1-demo/speech-caption-approved.mp4>)。最终 MP4 的 SRT 与画面抽帧均显示修订后的文字，音轨为 AAC 且有非零音量。

相关自动检查：后端此前完整回归 `1230 passed, 2 skipped`；本次 `BatchEditor.test.tsx` 为 `23 passed`。本次真实媒体 API 运行、视频探针、字幕文件和抽帧检查均通过。

## 声画完整性整改

用户指出前次展示的一条无声、一条只有黑底语音。确认原因是两种独立测试输入：咖啡素材经转码移除原音频且未补旁白；语音样本使用黑底包装。此前将两者展示为交付演示不充分。

新版在已有变体时间线添加两个 3 秒音频片段，与两个画面段落一一对应。配音由 macOS Tingting 生成，仅作内部技术演示，未核验客户营销的商用授权，不表示第二阶段的自动配音服务已完成。Whisper 识别第二条时将“滤杯”误写为“绿杯”，已按测试配音原文修订。时间线修改后旧审核变为 `stale`，旧批准导出返回 409；重新预览、审核后成功导出新版。

两条最终 MP4 的前后半段语音平均音量分别为 -21.7/-21.6 dB 与 -21.9/-21.7 dB，均为非静音；1 秒和 4 秒画面有实际咖啡镜头及对应字幕；导出字幕与已保存修订文字一致。新版记录见本机 `av-result.json`。原黑底普通话片保留为独立识别样本，不再作为声画完整成片展示。

## 边界

- 本次验证的是功能闭环及公开素材上的镜头相关性；尚无客户商品事实、客户授权素材、目标受众、真实投放反馈或客户签收。不能称为真实客户交付验收通过。
- 镜头匹配依据素材名称和备注，人工确认画面；不表示系统已自动理解视频内容。
- 普通话测试样本为独立技术片，未把与咖啡无关的语音放入咖啡成片。
- 本机运行识别需设置 `WHISPER_MODEL=/Users/shen/.cache/aivre/ggml-small.bin`。换机后仍须配置可用的多语言模型。

结论仅作为允许推进第二阶段实施的第一阶段**公开素材演示门槛**。日后有客户素材时，仍按[真实客户验收清单](./acceptance.md)补做商业交付验收。
