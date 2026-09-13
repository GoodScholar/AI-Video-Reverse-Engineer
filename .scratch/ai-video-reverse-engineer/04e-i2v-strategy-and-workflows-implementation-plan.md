# I2V Strategy and ComfyUI Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为参考图片生成可解释的 Wan2.2/MiniMax H3 推荐、可编辑参数和受控 ComfyUI 工作流，并以真实 Queue 结果决定模板是否可执行。

**Architecture:** 策略选择器是本地纯函数，只读取结构化生成需求、用户设置、环境能力和模板清单。工作流模板从固定上游版本导入，以绑定清单注入首帧、提示词和参数；生成记录通过输入版本哈希判断新鲜度，ComfyUI 客户端负责环境检查、上传和 Queue。

**Tech Stack:** Python 3.9、Pydantic、FastAPI、httpx、React 18、Vitest、ComfyUI HTTP API。

**Spec:** `.scratch/ai-video-reverse-engineer/04-image-reference-and-multi-provider-analysis-design.md`

## Global Constraints

- 本计划的硬前置是 Ticket 07 已按统一 `ReferenceMedia` 与新版 `StructuredVisualAnalysis` 完成；工作流只消费 `Project.promptGeneration.prompts`，不得新增第二套提示词状态。
- 图片智能模式只在 `wan22_i2v` 与 `minimax_h3_i2v` 中推荐；用户可在高级设置切换。
- 默认比例跟随参考图片，默认时长 5 秒，H3 音频默认关闭，Wan2.2 始终无音频生成节点。
- 推荐理由只来自确定性能力规则，不接受供应商返回的模型排名或主观画质结论。
- 修改素材、分析、Prompt、模型或关键参数必须使旧工作流过期。
- 模板未经真实导入和 Queue 时只能是 `candidate`，不得称为可执行工作流。
- 不自动下载或安装 ComfyUI、模型、节点或模板依赖。
- 上游模板固定到 Comfy-Org/workflow_templates commit `aaac56dd5cc5497533d92cbe50edc35ea660e587`。
- 实现与测试使用 `gpt-5.6-terra/high`；阶段审查使用 `gpt-6 Astra/medium`。

---

### Task 1: 定义输出设置、模板能力和确定性策略规则

**Files:**
- Create: `backend/app/generation_strategy.py`
- Create: `backend/app/workflow_models.py`
- Test: `backend/tests/test_generation_strategy.py`
- Modify: `backend/app/main.py`, `frontend/src/models.ts`

**Interfaces:**
- Produces: `OutputSettings`, `TemplateCapability`, `EnvironmentCapability`, `StrategyRecommendation`。
- Produces: `recommend_strategy(media, analysis, settings, environment, templates) -> StrategyRecommendation`。

- [ ] **Step 1: 写默认值、音频约束和不可用原因失败测试**

```python
def test_image_defaults_match_source_and_disable_h3_audio():
    settings = default_output_settings(ReferenceImage(width=1200, height=1600, **BASE))
    assert settings.durationSeconds == 5
    assert settings.aspectRatio == '3:4'
    assert settings.generateAudio is False

def test_audio_requirement_excludes_wan():
    result = recommend_strategy(image, analysis, settings(generateAudio=True), env_both, templates)
    assert result.recommendedStrategy == 'minimax_h3_i2v'
    assert any('音频' in reason for reason in result.incompatibilityReasons['wan22_i2v'])
```

- [ ] **Step 2: 运行测试并确认策略模块缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_generation_strategy.py`

Expected: FAIL。

- [ ] **Step 3: 实现模板能力清单和版本化规则**

```python
class TemplateCapability(BaseModel):
    strategy: Literal['wan22_i2v', 'minimax_h3_i2v']
    templateVersion: int
    status: Literal['candidate', 'executable']
    durationsSeconds: tuple[int, ...]
    supportsAudio: bool
    dimensionMultiple: int
```

H3 初始能力：5 秒默认、尺寸为 32 倍数、音频可选；Wan 初始能力：5 秒默认、音频固定关闭。更宽的时长/分辨率只有真实模板验证后才能加入清单。

- [ ] **Step 4: 实现稳定推荐优先级**

先排除环境或参数不兼容项；只有一个可用时推荐它；两者都可用且用户需要音频时推荐 H3；两者都可用且无独占需求时优先现有 Wan2.2 已验证模板，并把 H3 列为可切换方案。若 H3 后续先达到 `executable` 而 Wan 未达到，则优先 H3。每条决定返回中文理由。

- [ ] **Step 5: 运行策略边界测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_generation_strategy.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: recommend image to video strategies`

### Task 2: 固定上游模板与本地绑定清单

**Files:**
- Create: `backend/app/workflow_templates/wan22_i2v/v1/source-workflow.json`
- Create: `backend/app/workflow_templates/wan22_i2v/v1/api-workflow.json`
- Create: `backend/app/workflow_templates/wan22_i2v/v1/bindings.json`
- Create: `backend/app/workflow_templates/minimax_h3_i2v/v1/source-workflow.json`
- Create: `backend/app/workflow_templates/minimax_h3_i2v/v1/api-workflow.json`
- Create: `backend/app/workflow_templates/minimax_h3_i2v/v1/bindings.json`
- Create: `backend/app/workflow_templates/catalog.json`
- Create: `backend/app/workflow_template_registry.py`
- Test: `backend/tests/test_workflow_template_registry.py`

**Interfaces:**
- Produces: `load_template(strategy, version) -> WorkflowTemplate`。
- Produces: `template_requirements(strategy, version) -> TemplateRequirements`。

- [ ] **Step 1: 写上游哈希、绑定目标和候选状态失败测试**

```python
def test_h3_template_has_required_bindings():
    template = load_template('minimax_h3_i2v', 1)
    assert template.upstreamCommit == 'aaac56dd5cc5497533d92cbe50edc35ea660e587'
    assert set(template.bindings) >= {'inputImage', 'positivePrompt', 'width', 'height', 'durationSeconds', 'generateAudio'}
    assert template.status == 'candidate'
```

- [ ] **Step 2: 运行测试并确认模板注册表缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_workflow_template_registry.py`

Expected: FAIL。

- [ ] **Step 3: 导入固定上游工作流并记录来源**

Wan 来源：`templates/video_wan2_2_14B_i2v.json`；H3 来源：`templates/video_minimax_h3_i2v.json`。必须从固定 commit 获取并记录 SHA-256，不使用会变化的 `main` 内容作为构建输入。将两份 UI 工作流导入本地 ComfyUI 后导出 API 格式，保存为对应 `api-workflow.json`；没有可用 ComfyUI 时本任务明确阻塞，不手写猜测 API 图。

- [ ] **Step 4: 编写绑定清单而不依赖易变节点序号**

绑定清单针对 `api-workflow.json` 记录目标节点类型、唯一匹配约束、输入名称、允许值和预期匹配数量。加载时若节点结构与固定哈希或绑定约束不符立即失败，不猜测最近节点。

- [ ] **Step 5: 提取模型和节点依赖清单**

Wan 至少记录高/低噪声 diffusion model、UMT5 encoder、Wan VAE；H3 至少记录 FL2VA diffusion model、Qwen3VL text encoder、视频 VAE、音频 VAE和所用 Turbo LoRA。音频关闭时仍按模板真实节点要求报告依赖，不假设未使用节点无需模型。

- [ ] **Step 6: 运行模板注册表测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_workflow_template_registry.py`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: pin Wan and MiniMax H3 templates`

### Task 3: 生成版本化工作流并判断新鲜度

**Files:**
- Create: `backend/app/workflow_generation.py`
- Create: `backend/app/workflow_storage.py`
- Test: `backend/tests/test_workflow_generation.py`
- Test: `backend/tests/test_workflow_storage.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `Project.promptGeneration.prompts: PromptTextSet`，且 `promptGeneration.status == 'available'`。
- Produces: `generate_workflow(project, strategy, settings) -> GeneratedWorkflow`。
- Produces: `workflow_fingerprint(inputs) -> str`。
- Produces: `POST /api/projects/{project_id}/workflows`。

- [ ] **Step 1: 写首帧、提示词、参数和过期失败测试**

```python
def test_h3_generation_binds_normalized_image_and_disables_audio_by_default():
    workflow = generate_workflow(project, 'minimax_h3_i2v', default_settings(project))
    assert bound_value(workflow, 'inputImage') == 'normalized.png'
    assert bound_value(workflow, 'durationSeconds') == 5
    assert bound_value(workflow, 'generateAudio') is False

def test_changed_prompt_marks_workflow_stale(generated, project):
    project.promptGeneration.prompts.positiveEn += ' slow push-in'
    assert workflow_is_fresh(generated, project) is False
```

- [ ] **Step 2: 运行测试并确认生成器缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_workflow_generation.py backend/tests/test_workflow_storage.py`

Expected: FAIL。

- [ ] **Step 3: 实现白名单绑定和参数校验**

只允许修改 `bindings.json` 声明的值。尺寸按来源比例适配模板边界并取 `dimensionMultiple` 的倍数；任何调整写入 `adjustments[]` 及中文原因。Wan 绑定 `positiveEn` 与 `negativeEn`；H3 只绑定模板支持的 `positiveEn`，并在 manifest 记录未使用负向提示词。H3 开启音频时才写入音频提示和启用开关；Wan 收到 `generateAudio=true` 时拒绝。

- [ ] **Step 4: 实现输入指纹和原子产物**

指纹覆盖素材 ID、预处理 ID、分析 ID、Prompt 版本及内容哈希、策略、模板版本和全部输出设置。工作流 JSON、manifest 和输入素材副本先写临时目录再原子提交。参考素材替换或新语义分析开始时，保留历史工作流文件但把项目当前工作流引用标记为过期。

- [ ] **Step 5: 实现工作流生成 API**

拒绝缺少有效分析、Prompt、标准化图片、候选模板能力或非法参数的请求。`candidate` 模板可以生成和下载，但响应明确 `executable: false`，发送 API 必须拒绝。

- [ ] **Step 6: 运行工作流生成与存储测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_workflow_generation.py backend/tests/test_workflow_storage.py`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: generate versioned i2v workflows`

### Task 4: 增加策略、高级设置和工作流状态界面

**Files:**
- Create: `frontend/src/GenerationStrategyPanel.tsx`
- Create: `frontend/src/WorkflowPanel.tsx`
- Create: `frontend/src/workflowApi.ts`
- Test: `frontend/src/GenerationStrategyPanel.test.tsx`
- Test: `frontend/src/WorkflowPanel.test.tsx`
- Test: `frontend/src/workflowApi.test.ts`
- Modify: `frontend/src/models.ts`, `frontend/src/App.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `StrategyRecommendation`, `OutputSettings`, `GeneratedWorkflow`。
- Produces: 智能推荐、原因、模型切换、合法设置、H3 音频开关和候选/过期状态。

- [ ] **Step 1: 写推荐解释、H3 音频默认值和候选文案失败测试**

```ts
expect(screen.getByText('推荐：Wan2.2 14B I2V')).toBeInTheDocument();
expect(screen.getByRole('checkbox', { name: '生成环境音与音效' })).not.toBeChecked();
expect(screen.getByText('后续建议')).toBeInTheDocument();
expect(screen.queryByText('可执行工作流')).not.toBeInTheDocument();
```

- [ ] **Step 2: 运行组件测试并确认缺失**

Run: `npm --prefix frontend test -- GenerationStrategyPanel.test.tsx WorkflowPanel.test.tsx workflowApi.test.ts`

Expected: FAIL。

- [ ] **Step 3: 实现策略与高级设置界面**

智能模式展示本地返回的原因；切换模型后按该模板能力重校验时长和分辨率。用户调整导致的参数变化必须在提交前展示，不自动隐藏。

- [ ] **Step 4: 实现工作流新鲜度和状态界面**

区分 `candidate/executable` 与 `fresh/stale` 两个正交状态。过期工作流保留下载记录，但发送按钮禁用并提供“重新生成”。

- [ ] **Step 5: 验证窄屏只读和键盘交互**

窄屏允许查看推荐和状态，不渲染模型切换、参数修改或发送按钮。焦点在生成失败后回到触发按钮。

- [ ] **Step 6: 运行前端工作流测试**

Run: `npm --prefix frontend test -- GenerationStrategyPanel.test.tsx WorkflowPanel.test.tsx workflowApi.test.ts App.test.tsx`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: present i2v strategy and workflow state`

### Task 5: 检查 ComfyUI 环境、上传输入并真实 Queue

**Files:**
- Create: `backend/app/comfyui_client.py`
- Create: `backend/app/comfyui_environment.py`
- Create: `backend/app/comfyui_execution.py`
- Test: `backend/tests/test_comfyui_client.py`
- Test: `backend/tests/test_comfyui_environment.py`
- Test: `backend/tests/test_comfyui_execution.py`
- Modify: `backend/app/main.py`, `frontend/src/WorkflowPanel.tsx`, `README.md`
- Modify project docs: `PRODUCT.md`, `CONTEXT.md`, `README.md`
- Modify issue docs: `.scratch/ai-video-reverse-engineer/issues/08-generate-wan-i2v-reproduction-package.md`, `09-override-advanced-settings-and-track-workflow-freshness.md`, `10-check-environment-and-send-to-local-comfyui.md`
- Create ADRs: `docs/adr/0007-explicit-seven-provider-analysis.md`, `docs/adr/0008-minimax-h3-local-workflow.md`

**Interfaces:**
- Produces: `inspect_comfyui_environment(base_url, template) -> EnvironmentReport`。
- Produces: `queue_workflow(project, workflow) -> ExecutionRecord`。

- [ ] **Step 1: 写版本、节点、模型、候选模板和过期阻止测试**

```python
def test_candidate_template_cannot_be_queued(client, candidate_workflow):
    response = client.post(f'/api/projects/project-1/workflows/{candidate_workflow.id}/queue')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'workflow_not_verified'
```

- [ ] **Step 2: 运行 ComfyUI 测试并确认客户端缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_comfyui_*.py`

Expected: FAIL。

- [ ] **Step 3: 实现只读环境检查**

只连接用户配置的回环 ComfyUI 地址。分别报告连接、版本、节点类型和模型文件；不得自动安装或下载。网络响应先做大小和 JSON 结构限制，再转换为领域报告。

- [ ] **Step 4: 实现素材上传、Queue 和执行记录**

只上传当前工作流目录内的标准化图片，使用内部安全文件名；随后发送 API 格式工作流并保存 prompt ID。状态轮询映射为已发送、已 Queue、运行中、成功、失败，不保存绝对输出路径或密钥。

- [ ] **Step 5: 在真实本地 ComfyUI 中验证两份模板**

先检查固定模板所需模型和节点均存在，再导入 UI 工作流、导出 API 工作流并执行一次 Queue。记录 ComfyUI 精确版本、模板 SHA-256、模型文件名和成功 prompt ID 的脱敏摘要。

只有真实 Queue 成功后，才把对应 `catalog.json` 状态从 `candidate` 改为 `executable` 并新增回归固件；若环境缺失，保持 `candidate` 并结束在明确阻塞状态。

- [ ] **Step 6: 更新产品、领域、ADR 和 README**

ADR 0007 明确取代现有 `0005-explicit-multi-provider-analysis.md` 的五供应商范围，并保留其对 ADR 0004 的替代关系、用户自带密钥和系统安全存储决定。ADR 0008 记录 H3 候选/可执行验证门槛。CONTEXT 新增“参考素材、参考图片、可观察事实、生成建议、分析供应商”；README 写明图片边界、外发边界、七种配置和 H3 模型依赖。`0006-reference-media-and-media-specific-preprocessing.md` 已由计划 04a 创建，本任务只核对而不重复创建。同步扩展 Ticket 08～10 的图片、H3、音频和候选/可执行验收项，不改动它们原有的依赖顺序；Ticket 07 的适配已在进入本计划前完成。

- [ ] **Step 7: 完成最终全量验收**

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 全部通过；只有真实验证成功的模板显示“可执行工作流”。

- [ ] **Step 8: 验证检查点**

建议提交信息：`feat: queue verified Wan and MiniMax H3 workflows`
