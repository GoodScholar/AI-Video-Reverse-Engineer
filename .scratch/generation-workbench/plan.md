# 复刻方案与本地生成链路实施计划

目标：在现有深度捕捉之后接上可编辑中英文提示词、模板参数、复刻包导出、ComfyUI 检查/提交/结果预览。以已有 07/08/10/14 规格和当前用户授权为依据。不安装 ComfyUI、节点或生成模型，不自动调用付费分析，不自动开始视频生成。

1. 提示词适配：ProviderRequest 支持明确文本请求与自定义 schema；generate_prompts 仅发送结构化分析，返回 positiveZh/negativeZh/positiveEn/negativeEn。验证：provider 现有契约与新增文本请求测试。
2. ComfyUI 适配：固定来源模板、API 图绑定、object_info/system_stats检查、输入上传、prompt提交、history轮询与结果下载。没有真实Queue前标为候选模板。验证：MockTransport契约和模板结构；等待可用环境进行真实验收。
3. 本地方案路由：独立 reproduction.json 保存在 project-files/<id>/reproduction 下，原项目不增加全局耦合。通过素材/分析/depth hash与编辑revision绑定方案和生成运行。原子保存，过期响应不覆盖，失败保留旧成功结果。验证：API生命周期、重复请求、过期和路径错误。
4. 工作台界面：沿用DESIGN.md，桌面编辑/生成、移动只读；分区显示提示词、参数/下载、连接检查/生成记录/预览。运行和请求都有动画及明确状态；项目切换丢弃迟到响应。验证：Vitest、构建、浏览器桌面/窄屏检查。

接口：GET /api/projects/{id}/reproduction -> {prompts, revision, sourceHash, stale, settings, comfyUrl, runs, canGeneratePrompts, hasDepth, analysisReady, adjustments, templates}; POST /prompts {disclosureAccepted:true, revision}; PUT /reproduction {revision,prompts,settings,comfyUrl}; GET /package 下载zip；POST /check 检查当前已保存方案；POST /runs {revision,disclosureAccepted:true}; POST /runs/{runId}/refresh 更新队列结果；GET /runs/{runId}/output/{index} 本地结果预览。
settings: {strategy:'wan22_i2v'|'wan22_fun_control',width,height,frames,fps,seed}; prompts nullable 4文本字段；runs {id,promptId,status,createdAt,error,outputs:[{filename,url}],revision}。

分工：prompt_generation agent 拥有prompt模块和provider修改；comfy_adapter agent 拥有模板/client；root负责存储/路由/整合；frontend agent 拥有新工作台文件及App挂载。
