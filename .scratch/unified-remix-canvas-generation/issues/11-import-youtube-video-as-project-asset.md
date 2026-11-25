# 11: 真实导入 YouTube 视频为项目素材

**What to build:** 通过固定版本、许可已核查的来源适配器，把用户有权使用的 YouTube 公开页面、正常分享链接或合法会话可访问内容导入现有项目素材，并提供真实状态、诊断和预览。

**Blocked by:** 10: 建立统一视频来源合同并贯通本地导入

**Status:** claimed

## Scope

- 识别 YouTube URL、探测元数据、显示权利确认与可能外部请求，下载到受管临时区后校验、哈希并入库。
- 合法会话仅从系统安全存储读取；项目数据、日志和错误不保存 Cookie 或令牌。
- 保存适配器版本、最近真实验证时间和四态平台状态。
- 导入后所有下游只引用项目素材身份。

## Source reuse prerequisite

- 阅读所选 yt-dlp 固定版本对应 YouTube extractor、下载、Cookie 和错误分类源码，核对许可与分发方式。
- 记录直接使用的版本、配置边界和平台变化风险，不以一次偶然成功宣称长期兼容。

### 2026-10-02 固定源码审计与适配记录

- 直接分发：PyPI `yt-dlp==2026.8.19` 的纯 Python wheel（SHA-256 `1d57897e94c6665a0a6f9bc54b34e584284e32c034ffab3a7df25d8f7b24eedf`）；上游 tag `2026.08.19` / commit `3a08beaf031ab68f966401ead017ac81fe8486cf`，Unlicense。正式安装入口 `backend/requirements.lock`、`backend/pyproject.toml` 与 `backend/uv.lock` 均固定此版本和 wheel hash。
- 实际核查源码：`yt_dlp/extractor/youtube/_video.py`、`_base.py`（识别/播放器元数据），`YoutubeDL.py`（extract/download 参数和插件加载），`downloader/common.py`、`downloader/http.py`（进度/取消），`cookies.py`（cookiefile），`utils/_utils.py` 的 `DownloadError`。只嵌入其公开 YouTube extractor；不复制其源码。
- 适配边界：仅 HTTPS `youtube.com`/`youtu.be` 的 watch/short 分享链接；固定 `allowed_extractors=["youtube"]`、`YTDLP_NO_PLUGINS=1`、`plugin_dirs=[]`、`remote_components=set()`；禁用代理、文件 URL、账号/浏览器 Cookie 提取、地理绕过、播放列表和附属写出。Cookie 仅在用户再次明确勾选后从系统 keyring 读取为内存流，绝不写入项目、运行记录、日志或响应。
- 平台风险：YouTube 页面、Innertube/player、JS 签名或下载 host 变更会归为可诊断的暂时不可用；DRM、地理限制、直播、私密/会员内容不尝试绕过。下载限制为 200 MB、12 小时、短 socket/retry 边界，并在取消/失败时删除受管暂存文件。

## Acceptance criteria

- [x] 公开页面和正常分享链接可被正确识别，不支持的 URL 返回明确状态（E2）。
- [x] 用户确认权利后才下载；取消、网络中断、站点变化和内容不可用有可诊断结果（E2）。
- [x] 合法会话从安全存储读取且不回显，不自动登录或绕过会员、私密访问和 DRM（E2）。
- [x] 真实文件经探测和哈希后入库，可预览并被下游读取（E2 受控媒体）。
- [x] 重复提交、重复重试和恢复遵守幂等，不产生重复下载或覆盖既有素材（E2）。
- [ ] UI 显示当前适配器版本、最近真实验证时间和实际状态（E2 已显示；E3 未授权，不能填写真实验证时间）。

## Verification evidence

- E2 使用受控响应覆盖 URL 识别、错误分类、取消、重试和秘密脱敏。
- E3 使用一份授权金标 URL 完成真实下载、哈希、来源登记、素材预览。
- 第三方站点变化导致失败时保存诊断并把矩阵项标为失败或阻塞，而非通过。

### 当前验证与阻塞

- E2：`backend/.venv/bin/python -m pytest -q tests/test_youtube_import.py`（15 passed）；`frontend npm test -- --run src/preproductionApi.test.ts src/PreproductionWorkspace.test.tsx`（44 passed）；`npm run build` 通过。锁定环境中实际载入 `yt-dlp=2026.08.19`，`YTDLP_NO_PLUGINS=1`，并成功构造受限 `YoutubeDL` 配置。
- E3/E4：**blocked**，尚未收到用户授权的金标公开视频 URL、权利确认与真实外发许可；未向第三方站点发起测试请求，也未伪造 `lastRealVerificationAt`。Ticket 30 继续依赖此阻塞项。

## Out of scope

- 绕过地区、会员、私密或 DRM 限制。
- 转录、高光和发布回 YouTube。
