# 13: 真实导入抖音视频为项目素材

**What to build:** 通过固定版本、许可已核查且真实验证的来源适配器，把用户有权使用的抖音公开页面、正常分享链接或合法会话可访问内容导入现有项目素材，正确处理短链解析、登录要求和站点变化。

**Blocked by:** 10: 建立统一视频来源合同并贯通本地导入

**Status:** blocked

## Scope

- 识别正常分享文本中的链接与短链，限制重定向并探测最终来源。
- 权利确认后下载、校验、哈希、登记来源并入库；合法会话只从安全存储读取。
- 保存适配器版本、最近真实验证时间、重定向来源和四态状态。

## Source reuse prerequisite

- 阅读所选 yt-dlp 固定版本的 Douyin extractor、短链重定向、Cookie、签名依赖和错误分类源码。
- 核对任何额外运行时、许可证和维护风险；无法固定和真实验证时明确记录阻塞。

## Acceptance criteria

- [x] E2：正常分享链接和短链被识别；非抖音、私网/DNS 混合、危险重定向被拒绝。
- [x] E2：下载前完成权利确认，不自动输入密码、扫码或绕过私密/受限访问。
- [x] E2：会话秘密不进入项目、日志或诊断，失效会话显示需要登录。
- [x] E2：受控媒体完成探测、哈希、原子入库和预览，下游使用项目素材身份。
- [x] E2：站点变化、风控、链接失效、取消、网络中断和重试有四态诊断。
- [x] UI 显示适配器版本、四态和空的最近真实验证时间。

## Verification evidence

- E2 覆盖短链、重定向限制、登录要求、取消、错误分类和脱敏。
- E3 使用一份授权抖音金标 URL 完成真实导入、哈希、来源登记和预览。
- 人工核对真实媒体时长、画面、音频和来源记录一致。

## Implementation evidence

- 源码核查：固定依赖 `yt-dlp==2026.08.19`（`backend/uv.lock` 的 PyPI wheel 哈希），实际载入版本同为 `2026.08.19`；`yt_dlp/extractor/tiktok.py` 的 `DouyinIE` 仅接受 `https://(www.)?douyin.com/video/<数字>`，调用 `/aweme/v1/web/aweme/detail/`，缺少 `s_v_web_id` 时明确要求 fresh cookies。PyPI wheel/source 为 Unlicense；ffmpeg/ffprobe 为既有媒体依赖。该提取器未要求 EJS，但适配器显式禁用 JS runtime、远端组件、插件、代理、Geo bypass、浏览器 Cookie 与 Generic extractor。
- E2：`PYTHONPATH=. uv run pytest tests/test_douyin_import.py tests/test_youtube_import.py tests/test_bilibili_import.py tests/test_local_video_source_contract.py tests/test_preproduction_api.py -q` 通过（94 项）。受控测试覆盖分享文本、三跳最终地址的 DNS 公网/混合解析拒绝、DNS 断网分类、短链 token/host 哈希事实、会话脱敏和生产配置 API、权限披露、原子入库、取消/重试/崩溃补偿、错误分类、固定依赖与 YouTube/B站回归；未发起任何第三方请求。
- UI/API：`npm test -- --run src/DouyinSessionSettings.test.tsx src/preproductionApi.test.ts src/PreproductionWorkspace.test.tsx src/ContentStudioApp.test.tsx` 通过（54 项），`npm test` 通过（71 文件、665 项），`npm run build` 通过。页面显示适配器 `yt-dlp 2026.08.19`、四态与 `lastRealVerificationAt: null`；服务设置提供独立的抖音会话安全存储配置，不将受控测试表述为真实验证。

## Blocker

- E3/E4 仍阻塞：尚未提供用户明确授权的抖音金标 URL，故未执行真实探测或下载，未记录真实验证时间，也未对真实媒体的时长、画面、音频和来源人工验收。不得在缺少该授权前标记本票完成。

## Out of scope

- 绕过平台风控、私密权限或下载限制。
- 自动抓取账号主页和平台发布。
