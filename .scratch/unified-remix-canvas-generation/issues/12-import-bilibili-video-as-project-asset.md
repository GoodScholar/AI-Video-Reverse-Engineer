# 12: 真实导入 B站视频为项目素材

**What to build:** 通过固定版本、许可已核查的来源适配器，把用户有权使用的 B站公开页面、正常分享链接或合法会话可访问内容导入现有项目素材，并提供平台特有的元数据、状态、诊断和预览。

**Blocked by:** 10: 建立统一视频来源合同并贯通本地导入

**Status:** blocked

## Scope

- 覆盖标准视频页与正常分享链接的识别、元数据探测、权利确认、下载、校验、哈希和入库。
- 合法会话进入系统安全存储，不自动登录或绕过大会员、私密和访问控制。
- 保存适配器版本、最近真实验证时间和四态状态，下游只引用项目素材身份。

## Source reuse prerequisite

- 阅读所选 yt-dlp 固定版本的 Bilibili extractor、分 P/音视频合并、Cookie 与错误分类源码，核对许可和 FFmpeg 依赖。
- 记录多分 P 的明确选择策略，不把默认抓取行为隐含为业务规则。

### 2026-10-02 固定源码审计与适配记录

- 直接分发复用与 Ticket 11 相同：PyPI `yt-dlp==2026.8.19`（上游 tag `2026.08.19` / commit `3a08beaf031ab68f966401ead017ac81fe8486cf`，Unlicense），不复制上游源码。实际核查 `yt_dlp/extractor/bilibili.py` 的 `BiliBiliIE`：未指定 `p` 的多分P会产出 playlist，`?p=N` 才抽取单项；DASH 格式由 `bv*+ba/b` 与 FFmpeg 合并为 MP4。
- 适配器只放行标准 HTTPS `bilibili.com/video/BV...` 页面。`b23.tv` 由独立的有限 HTTPS 重定向边界解析（最多三次，仅 b23 域中转，最终必须为规范 BV 页），不将短链交给 yt-dlp Generic extractor；失败不继续请求任意 URL。番剧、合集、互动分支、直播、legacy `multi_video`、会员/私密/DRM 与地区绕过均不支持。
- yt-dlp 固定 `allowed_extractors=["bilibili"]`、`YTDLP_NO_PLUGINS=1`、`plugin_dirs=[]`、`remote_components=set()`，关闭代理、文件 URL、浏览器 Cookie/自动登录与 geo bypass。会话仅在用户显式勾选时从专用 OS keyring 项读取；显式请求但无安全会话即报需要登录，绝不静默降级。
- 选择策略：探测完整显示分P；多分P必须明确选择，直链 `?p=N` 视为明确选择；不自动下载合集或默认 P1。下载在共享的持久运行、受管暂存、取消/重试/重启恢复和原子安装能力中执行，FFprobe 要求视频+音频流，FFmpeg 解码二者，再哈希、原子安装为项目素材。

## Acceptance criteria

- [ ] 标准视频页和正常分享链接被正确识别，多分 P 输入要求用户明确选择或使用书面默认。
- [ ] 权利确认、合法会话、安全存储和禁止绕过规则在界面与服务端生效。
- [ ] 音视频流合并后经过真实探测和哈希，失败不留下可用状态的半成品素材。
- [ ] 真实素材可预览并被下游稳定引用，外部 URL 不成为运行依赖。
- [ ] 平台失败映射到可用、需要登录、暂时失效或不支持该链接，并保留脱敏诊断。
- [ ] UI 显示适配器版本和最近真实验证时间。

## Verification evidence

- E2 覆盖 URL、分 P、音视频合并、登录要求、取消和失败回滚。
- E3 使用一份授权 B站金标 URL 完成真实导入、哈希、来源登记和预览。
- 人工核对音画同步、标题/时长等元数据与来源一致。

### 当前验证与阻塞

- E2：`backend/.venv/bin/python -m pytest -q tests/test_bilibili_import.py tests/test_youtube_import.py`（32 passed）；覆盖标准 BV、受限 b23 解析、多分P强制选择、MP4 音视频校验、会话安全存储与脱敏、失败回滚、取消、重试、重启恢复及 YouTube 回归。`frontend npm test -- --run src/preproductionApi.test.ts`（11 passed）；`npm run build` 通过。
- E3/E4：**blocked**。尚未收到用户授权的 B站金标 URL、权利确认与真实外发许可；未向第三方 B站页面发起测试请求，未伪造 `lastRealVerificationAt`。真实导入、哈希、预览和音画人工核对待该授权后执行。

## Out of scope

- 自动批量抓取合集或绕过大会员权限。
- 转录、高光和平台再发布。
