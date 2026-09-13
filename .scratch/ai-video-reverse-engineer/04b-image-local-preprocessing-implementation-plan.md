# Image Local Preprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为参考图片提供可恢复的四阶段本地预处理，生成标准化首帧、脱敏分析代理和可验证清单。

**Architecture:** 保留现有视频 FFmpeg runner，并增加独立的 Pillow 图片 runner。`LocalPreprocessing` 使用媒体类型选择固定阶段表，存储层通过阶段定义映射验证和清理产物，主任务队列继续串行执行项目任务。

**Tech Stack:** Python 3.9、Pillow 11.3.0、Pydantic、FastAPI、React 18、Vitest。

**Spec:** `.scratch/ai-video-reverse-engineer/04-image-reference-and-multi-provider-analysis-design.md`

## Global Constraints

- 图片阶段固定为 `imageDecoding`、`imageNormalization`、`proxyGeneration`、`reproducibilityAssessment`。
- 原图不得覆盖；`normalized.png` 必须应用方向、转换 sRGB、丢弃来源元数据，透明像素以白底合成。
- `analysis-proxy.jpg` 最长边不超过 2048px、文件不超过 8,000,000 字节，且不得放大小图。
- 图片摘要不得出现镜头、关键帧、运动强度字段。
- 视频现有五阶段、产物目录和恢复行为必须保持。
- 实现与测试使用 `gpt-5.6-terra/high`；阶段审查使用 `gpt-6 Astra/medium`。

---

### Task 1: 将本地预处理领域模型改为媒体感知

**Files:**
- Modify: `backend/app/local_preprocessing.py`
- Modify: `backend/app/local_preprocessing_storage.py`
- Test: `backend/tests/test_local_preprocessing.py`
- Test: `backend/tests/test_local_preprocessing_storage.py`

**Interfaces:**
- Consumes: `LocalPreprocessing.mediaType` 与 `sourceReferenceMediaId`。
- Produces: `stage_order_for(media_type) -> tuple[StageName, ...]`。
- Produces: `ImageProxySummary` 与 `VideoProxySummary` 可辨识联合。

- [ ] **Step 1: 写媒体阶段表和图片摘要失败测试**

```python
def test_image_preprocessing_uses_only_image_stages():
    task = new_local_preprocessing('prep-1', 'image-1', 'image', NOW)
    assert [stage.name for stage in task.stages] == [
        'imageDecoding', 'imageNormalization',
        'proxyGeneration', 'reproducibilityAssessment',
    ]
    assert task.mediaType == 'image'
```

- [ ] **Step 2: 运行测试并确认旧固定五阶段导致失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py`

Expected: FAIL，构造器不接受媒体类型。

- [ ] **Step 3: 实现阶段联合和媒体专属摘要**

```python
VIDEO_STAGE_ORDER = ('decoding', 'sceneDetection', 'keyframeExtraction', 'motionAnalysis', 'reproducibilityAssessment')
IMAGE_STAGE_ORDER = ('imageDecoding', 'imageNormalization', 'proxyGeneration', 'reproducibilityAssessment')

def stage_order_for(media_type: MediaType) -> tuple[StageName, ...]:
    return IMAGE_STAGE_ORDER if media_type == 'image' else VIDEO_STAGE_ORDER
```

把所有 `STAGE_ORDER.index(...)` 调用改为当前任务的阶段表；视频默认值不得掩盖缺失的 `mediaType`，旧数据由 04a 的迁移函数负责补齐。

- [ ] **Step 4: 将存储层阶段文件映射按媒体类型拆分**

图片映射为：标准化阶段 `normalized.png`，代理阶段 `analysis-proxy.jpg`，适用性阶段 `manifest.json`。验证函数必须同时校验 `mediaType`、算法版本和来源素材 ID。

- [ ] **Step 5: 运行模型和存储测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`refactor: make preprocessing media aware`

### Task 2: 实现图片纯算法与产物编码

**Files:**
- Create: `backend/app/image_preprocessing.py`
- Test: `backend/tests/test_image_preprocessing.py`
- Fixtures: `backend/tests/fixtures/images/opaque.jpg`, `transparent.png`, `oriented.jpg`, `sample.webp`

**Interfaces:**
- Produces: `inspect_image(path: Path) -> DecodedImageFacts`。
- Produces: `normalize_image(source: Path, destination: Path) -> NormalizedImageFacts`。
- Produces: `write_analysis_proxy(source: Path, destination: Path) -> ProxyImageFacts`。
- Produces: `assess_image_reproducibility(...) -> ReproducibilityAssessment`。

- [ ] **Step 1: 写方向、色彩、透明度和代理上限失败测试**

```python
def test_normalize_applies_orientation_flattens_alpha_and_removes_metadata(tmp_path):
    output = tmp_path / 'normalized.png'
    facts = normalize_image(FIXTURES / 'transparent-oriented.png', output)
    with Image.open(output) as image:
        assert image.mode == 'RGB'
        assert image.getexif() == {}
        assert image.size == facts.displaySize
        assert image.getpixel((0, 0)) == (255, 255, 255)
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_image_preprocessing.py`

Expected: FAIL。

- [ ] **Step 3: 实现标准化算法**

使用 `ImageOps.exif_transpose`；若存在有效 ICC，使用 `ImageCms.profileToProfile` 转换到 sRGB，然后转成 RGB。Alpha 合成必须基于实际透明像素，背景固定 `(255, 255, 255)`。保存 PNG 时不传入源 `exif`、`icc_profile` 或文本块。

- [ ] **Step 4: 实现受体积限制的代理编码**

```python
for quality in (90, 85, 80, 75, 70, 65, 60):
    image.save(candidate, format='JPEG', quality=quality, optimize=True)
    if candidate.stat().st_size <= 8_000_000:
        os.replace(candidate, destination)
        break
else:
    raise ImagePreprocessingFailure('proxy_generation_failed', '分析代理无法压缩到安全上限。')
```

缩放使用保持比例的高质量重采样；最长边已小于 2048px 时保持尺寸。

- [ ] **Step 5: 验证输出可重新打开且不含来源信息**

测试读取 PNG/JPEG info、EXIF、像素尺寸和字节数；扫描 `manifest.json` 不得出现原文件名或绝对路径。

- [ ] **Step 6: 运行图片算法测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_image_preprocessing.py`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: normalize reference images locally`

### Task 3: 接入图片 runner、恢复与 API 任务分发

**Files:**
- Create: `backend/app/image_preprocessing_runner.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/local_preprocessing_runner.py`
- Modify: `backend/app/local_preprocessing_storage.py`
- Test: `backend/tests/test_image_preprocessing_runner.py`
- Modify tests: `backend/tests/test_local_preprocessing_api.py`, `backend/tests/test_local_preprocessing_runner.py`

**Interfaces:**
- Consumes: Task 2 的四个纯函数。
- Produces: `run_image_preprocessing(source_path, reference, preprocessing, output_directory, on_stage_started, on_stage_completed)`。
- Keeps: `POST /api/projects/{project_id}/local-preprocessing`。

- [ ] **Step 1: 写图片任务完成和失败恢复测试**

```python
def test_image_job_persists_four_completed_stages(client, uploaded_image):
    response = client.post(f"/api/projects/{uploaded_image['id']}/local-preprocessing")
    assert response.status_code == 202
    project = wait_for_terminal_project(client, uploaded_image['id'])
    assert project['localPreprocessing']['status'] == 'completed'
    assert project['localPreprocessing']['proxySummary']['mediaType'] == 'image'
```

- [ ] **Step 2: 运行测试并确认视频 runner 无法处理图片**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_image_preprocessing_runner.py backend/tests/test_local_preprocessing_api.py`

Expected: FAIL。

- [ ] **Step 3: 实现阶段 runner 和原子提交**

每个阶段调用 `on_stage_started`，写临时产物，校验后 `os.replace`，再调用 `on_stage_completed`。任何 Pillow/OSError 映射到当前阶段的稳定错误码，不把路径写入消息。

- [ ] **Step 4: 在主任务中按 `referenceMedia.type` 分发 runner**

```python
runner = run_image_preprocessing if project.referenceMedia.type == 'image' else run_local_preprocessing
result = runner(
    source_path=source_path,
    reference=project.referenceMedia,
    preprocessing=task,
    output_directory=output_directory,
    on_stage_started=stage_started,
    on_stage_completed=stage_completed,
)
```

所有重启协调、幂等复用、失败重试和替换锁定必须使用 `stage_order_for(task.mediaType)`。

- [ ] **Step 5: 运行图片、视频与恢复测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_image_preprocessing_runner.py backend/tests/test_local_preprocessing_api.py backend/tests/test_local_preprocessing_runner.py backend/tests/test_local_preprocessing_storage.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: run recoverable image preprocessing`

### Task 4: 前端展示图片专属阶段与摘要

**Files:**
- Modify: `frontend/src/models.ts`
- Modify: `frontend/src/LocalPreprocessingPanel.tsx`
- Modify: `frontend/src/LocalPreprocessingPanel.test.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: 媒体感知 `LocalPreprocessing` 和 `Project.referenceMedia`。
- Produces: 图片四阶段、代理尺寸、白底处理说明与待语义确认状态。

- [ ] **Step 1: 写图片阶段和禁止视频摘要失败测试**

```ts
expect(screen.getByText('图像解码')).toBeInTheDocument();
expect(screen.getByText('方向与色彩标准化')).toBeInTheDocument();
expect(screen.queryByText(/镜头/)).not.toBeInTheDocument();
expect(screen.queryByText(/运动强度/)).not.toBeInTheDocument();
```

- [ ] **Step 2: 运行组件测试并确认失败**

Run: `npm --prefix frontend test -- LocalPreprocessingPanel.test.tsx App.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 实现按媒体类型分支的阶段映射和摘要组件**

不要在一个 JSX 区块里用大量字段存在性判断；建立 `ImagePreprocessingConclusion` 和 `VideoPreprocessingConclusion` 两个小组件，共享任务状态、轮询和重试外壳。

- [ ] **Step 4: 验证窄屏只读、焦点和替换锁定**

图片任务排队/运行时替换按钮禁用；失败时焦点回到重试按钮；完成后状态标题获得焦点。

- [ ] **Step 5: 完成增量全量验收**

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 全部通过。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: present image preprocessing progress`
