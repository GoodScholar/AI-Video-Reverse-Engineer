# 数字人直播试点：视频号、淘宝、快手官方资料核验

核验日期：2026-09-25（中国大陆公开资料）。本页只讨论平台公开规则与开放能力，不代表本项目或客户账号已获直播、应用、数字人或数据权限。平台规则会动态更新；正式试播前需用试点主体的后台权限和平台答复复核。范围仅含视频号、淘宝、快手，不含抖音。

## 先看结论

| 平台 | 公开资料支持到什么程度 | 对试点的判断 |
| --- | --- | --- |
| 视频号 | 认证账号可从视频号直播助手取得推流地址与密钥；但《微信视频号直播行为规范》10.3 将无人直播、录播、非真实开播列为不倡导模式，10.4 明列非真人把预录视频/图片充作直播内容。[腾讯会议接入指南](https://meeting.tencent.com/support/topic/1731/)、[视频号直播行为规范（腾讯官方）](https://support.weixin.qq.com/cgi-bin/mmtemplatepage-bin/pages/agreement/QP3B5JAHbc1MzpPm) | **不宜把 7×24 无人数字人直播作为首个默认试点。** 推流技术可用不等于内容模式获准；若拟尝试实时生成且有人值守的数字人直播，须先取得平台对具体方案的明确认可。此为基于条款的风险判断，不是对所有数字人形式的一概禁令。 |
| 淘宝 | 官方有数字人直播服务商准入规则，以及数字人直播专用 API/消息。规则要求服务商资质、生成合成类算法备案、至少 20 名技术人员、工具与案例材料，并要求显著标识虚拟主播。[服务商规则](https://developer.alibaba.com/docs/doc.htm?articleId=122141&docType=1&treeId=23)、[数字人直播类目入驻规则](https://developer.alibaba.com/docs/doc.htm?articleId=121494&docType=1&treeId=782)、[数字人直播 API](https://developer.alibaba.com/docs/api.htm?apiId=68922) | **有专门生态，但准入门槛高。** 上述规则适用于“入驻开放平台并将应用发布至服务市场”的服务商；若仅做代运营并使用已获准的第三方产品，适用路径需向平台核实，不能直接把服务商规则等同于所有商家的开播条件。 |
| 快手 | 官方“女娲”数字人平台公开宣传 24 小时开播与自动互动；开放平台另有需 `user_video_live` 权限的 RTMP 推流接口，账号自身还需直播权限。官方文档标明“直播托管模式”暂停接入。[女娲平台](https://nvwa.e.kuaishou.com/)、[快手开放平台 SDK](https://open.kuaishou.com/platform/openApi?menu=55)、[应用注册](https://open.kuaishou.com/platformDocs/newGuide/login)、[直播托管模式](https://open.kuaishou.com/platform/openApi?menu=57) | **可优先询问官方女娲/已获准合作路径，适合作为候选首站。** 女娲的 24 小时能力不等于任意第三方无人推流都被允许；开放平台直播托管入口暂停接入，需官方确认实际可用的接入方式。 |

## 分平台证据

### 视频号

- **开播与推流（明确支持，但依赖账号认证）**：腾讯会议官方指引说明，完成视频号认证后可从“视频号直播助手”获取推流地址和密钥并推流；未核实到面向任意第三方的公开视频号开播/评论/复盘 API。[腾讯会议指引](https://meeting.tencent.com/support/topic/1731/)
- **内容模式（明确限制）**：腾讯官方《微信视频号直播行为规范》（正文更新日期 2026-07-20）10.3 提到无人直播、录播、非真实开播；10.4 提到非真人将事先录制好的视频/图片作为直播内容；10.8 涉及长时间黑屏挂机或无互动的定格画面。条款文字在腾讯官方页面的页面数据中可读取；网页正文需 JavaScript 才显示。[官方规范](https://support.weixin.qq.com/cgi-bin/mmtemplatepage-bin/pages/agreement/QP3B5JAHbc1MzpPm)
- **实时数字人尚未核实**：腾讯云提供数字人推流工具，并允许配置目标推流地址；这是云直播产品能力，不能推导为视频号对数字人直播场景的批准。官方视频号规范并未在本次可读文本里给出实时数字人直播的明确豁免。[腾讯云数字人直播文档](https://cloud.tencent.com/document/product/267/126574)、[视频号官方规范](https://support.weixin.qq.com/cgi-bin/mmtemplatepage-bin/pages/agreement/QP3B5JAHbc1MzpPm)
- **运营责任**：官方规范要求主播实名认证，直播内容与行为受规范约束；代运营团队与客户需要明确由谁持号、值守、审核话术、处理投诉，不能从“可获得推流地址”推导出代运营账号权限。[视频号官方规范](https://support.weixin.qq.com/cgi-bin/mmtemplatepage-bin/pages/agreement/QP3B5JAHbc1MzpPm)

### 淘宝

- **数字人服务商准入（明确要求，适用范围有限）**：2025-07-04 更新的规则针对入驻淘宝开放平台、将应用发布至服务市场的数字人服务商。要求公司注册满 6 个月、注册资本至少 100 万元、对应经营范围、生成合成类算法备案、至少 20 名技术人员、成熟工具与成功案例及 MRD/PRD，并通过安全风控校验；未显著标识虚拟主播、未备案等可被处置。[服务商管理规则](https://developer.alibaba.com/docs/doc.htm?articleId=122141&docType=1&treeId=23)
- **数字人直播形式（接口有明确模型，但并非自动授权）**：淘宝数字人直播 ISV 获取直播间信息 API 的 `type` 字段区分“无人开播”和“真人开播（阿凡达模式）”；该 API 标注“需要授权”。这说明平台存在相应产品模型，但并不能推出任何代运营商均可开通、无限时长或无需人工管理。[淘宝数字人直播 API](https://developer.alibaba.com/docs/api.htm?apiId=68922)
- **互动与状态接口（存在，不等于已获调用权）**：官方消息目录列有“淘宝数字人评论推送”和直播上下播消息；AI 开放 API 中有数字人场次计划获取、商品弹卡、状态上报等。状态上报接口要求每 5 分钟上报运行状态，标注“需要授权”；这是向平台**上报**，不是获取成交/观看复盘数据的证据。具体 API 可见性、授权和服务市场入驻需账号级核实。[评论消息](https://developer.alibaba.com/docs/topicDetail.htm?apiId=2664)、[上下播消息](https://developer.alibaba.com/docs/topicDetail.htm?apiId=2344)、[状态上报 API](https://developer.alibaba.com/docs/api.htm?apiId=70592)
- **时长与责任（待核实）**：已找到的平台文档没有给出适用于此业务的“可无条件 7×24 连续直播”承诺；服务商仍对虚拟主播标识、内容、权益与风控负责。[服务商管理规则](https://developer.alibaba.com/docs/doc.htm?articleId=122141&docType=1&treeId=23)

### 快手

- **官方数字人路线（产品宣称，需确认客户准入）**：快手“女娲”官网称提供数字人直播、24 小时开播及自动互动；快手 2024 年度体验报告也将女娲称为面向广告主的官方 AI 数字人无人直播方案。该表述属于官方产品能力，不等于第三方自建推流都获准。[女娲官网](https://nvwa.e.kuaishou.com/)、[快手年度体验报告](https://ir.kuaishou.com/static-files/b298b1a5-657d-4302-89da-127eeff04dcc)
- **开放平台推流（接口存在，申请与用户授权有前置）**：官方 SDK 文档的 `getPushUrl` 返回推流地址与流名称，要求 `user_video_live` scope，取得地址后自行用 RTMP 推流；应用须申请接口权限并获审核，用户授权不能替代账号自身直播权限。2026-03-06 的应用注册文档标明移动应用暂不支持申请直播权限，网站应用路径仍须实际审核。[SDK 文档](https://open.kuaishou.com/platform/openApi?menu=55)、[应用注册](https://open.kuaishou.com/platformDocs/newGuide/login)、[权限常见问题](https://open.kuaishou.com/platformDocs/other/commonProblem)
- **托管接入（明确暂停）**：开放平台“直播托管模式”页面写明“暂停接入，如有相关需求请联系平台客服”。不能把 SDK 所列推流接口直接当作已开放的全托管数字人服务。[直播托管模式](https://open.kuaishou.com/platform/openApi?menu=57)
- **互动、数据与运营责任**：小程序直播挂载能力须申请并绑定账号；官方说明直播作者可在直播后查看观看人次、最高在线人数等基础数据，后链路转化可通过直播进入小程序的场景值区分。这不等于有通用的第三方直播评论或完整成交复盘 API。快手直播管理规范要求主播对直播内容与公屏言论负责，且把虚假宣传、无主播出镜或主播行为与商品无实质关系的电商直播列为违规情形。女娲产品的具体互动接口对第三方是否开放未核实。[直播挂载能力](https://open.kuaishou.com/docs/develop/functionAccessGuide/liveRoomMount/liveRoomMount)、[直播管理规范](https://www1.kuaishou.com/norm?tab=live)、[女娲官网](https://nvwa.e.kuaishou.com/)
- **虚拟形象授权**：快手小程序实物交易规则要求使用他人肖像作为虚拟形象从事直播营销时取得肖像权人同意；自然人声音参照适用。该规则适用于相应小程序实物交易场景；其他商品渠道仍需核对其具体规则。[实物交易类小程序管理规范](https://open.kuaishou.com/docs/operate/specification/physicalTransaction)

## 试点前必须实证的事项

1. **场景与主体**：客户是否已有目标平台店铺、完成实名/认证的直播账号、可授权的商品及可合法使用的形象、声音、图片和话术。平台账号与直播间实际控制人应由客户指定。上述平台规则均不能代替账号后台的实际状态核验。[视频号规范](https://support.weixin.qq.com/cgi-bin/mmtemplatepage-bin/pages/agreement/QP3B5JAHbc1MzpPm)、[淘宝规则](https://developer.alibaba.com/docs/doc.htm?articleId=122141&docType=1&treeId=23)、[快手权限常见问题](https://open.kuaishou.com/platformDocs/other/commonProblem)
2. **平台书面确认**：将具体模式写清（实时合成还是预录循环、每日时长、真人值守、评论自动回复与人工接管、商品类目），向目标平台或其已获准服务商确认可开展范围。尤其视频号不能以云直播推流能力代替合规确认；快手应确认女娲/合作接入路径与托管模式暂停接入的关系。[视频号规范](https://support.weixin.qq.com/cgi-bin/mmtemplatepage-bin/pages/agreement/QP3B5JAHbc1MzpPm)、[快手托管模式](https://open.kuaishou.com/platform/openApi?menu=57)、[淘宝规则](https://developer.alibaba.com/docs/doc.htm?articleId=122141&docType=1&treeId=23)
3. **小场次验证**：获得权限后先以单账号、单商品组和有人值守的短场测试开播、断流、审核、评论接管与平台后台数据导出；再决定是否延长时段。公开资料不足以为三平台统一承诺“7×24 不间断”或同一套 API 驱动全流程。[腾讯会议视频号指引](https://meeting.tencent.com/support/topic/1731/)、[淘宝数字人 API](https://developer.alibaba.com/docs/api.htm?apiId=68922)、[快手开放平台 SDK](https://open.kuaishou.com/platform/openApi?menu=55)

**研究性推荐**：若客户已有快手经营账号且能取得女娲或官方认可的合作路径，快手可优先作为首个候选；若团队满足淘宝服务商准入或能与已入驻服务商合作，淘宝可作为另一候选。视频号当前不宜承诺无人、录播循环或 7×24 形态。此排序仍取决于实际客户账号、商品行业与平台答复。[快手女娲](https://nvwa.e.kuaishou.com/)、[淘宝服务商规则](https://developer.alibaba.com/docs/doc.htm?articleId=122141&docType=1&treeId=23)、[视频号规范](https://support.weixin.qq.com/cgi-bin/mmtemplatepage-bin/pages/agreement/QP3B5JAHbc1MzpPm)
