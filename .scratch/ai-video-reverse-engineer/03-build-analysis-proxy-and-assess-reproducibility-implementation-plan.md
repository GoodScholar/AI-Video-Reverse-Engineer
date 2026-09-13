# Ticket 03 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在项目准备页手动启动可恢复的本地视频预处理，生成不含完整视频的分析代理，并依据固定镜头与运动信号输出“超出可复刻范围”或“待语义分析确认”。

**Architecture:** FastAPI 把任务状态持久化到现有 `Project`，再交给应用内单工作线程顺序执行 FFmpeg 阶段；每阶段通过版本目录和原子文件提交保留安全恢复点。React 以一秒轮询读取同一项目，展示可访问的阶段状态与初步判断；参考视频替换与预处理状态通过现有项目写锁保持一致。

**Tech Stack:** Python 3.9+、FastAPI 0.115.12、Pydantic 2、Python 标准库 `ThreadPoolExecutor`、FFmpeg/ffprobe 8.x、pytest 8.3.5、React 18.3.1、TypeScript 5.7.3、Vitest 3.0.8、Testing Library、Vite 6.1.0。

**Spec:** [`.scratch/ai-video-reverse-engineer/03-build-analysis-proxy-and-assess-reproducibility-design.md`](./03-build-analysis-proxy-and-assess-reproducibility-design.md)

**Requirements:** [Ticket 03](./issues/03-build-analysis-proxy-and-assess-reproducibility.md)；[产品总规格](./spec.md)

## Global Constraints

- 参考视频上传或替换后不自动预处理；只有宽度至少 1024px 的桌面界面提供手动启动和失败重试。
- 本 Ticket 只本地确认多镜头和运动强度；主要主体数量与复杂交互固定为待 Ticket 04 确认。
- 本地结论只有 `out_of_scope` 和 `pending_semantic_confirmation`，不得返回最终 `in_scope`，不得计算总体相似度百分比。
- 分析采样固定为 8fps、最长边 320px；不把低分辨率分析流编码成视频文件。
- 场景阈值固定为 `10.0`；忽略前 `0.5` 秒，`0.5` 秒内候选合并；运动统计排除切换点前后各 `0.25` 秒。
- 运动 P90 边界固定为 `2.5` 和 `8.0`；少于 8 个有效样本时必须返回 `unavailable`，不能降级为低运动。
- 关键帧目标为 `min(12, max(4, ceil(durationSeconds)))`，最长边不超过 768px；联系表每格最长边不超过 384px、最多三列并显示时间码。
- 分析代理严格由 `contact-sheet.jpg` 和 `analysis-proxy.json` 组成，不得包含完整视频、低清视频、绝对路径、原文件名、项目名、密钥或供应商信息。
- FFmpeg 必须使用参数数组和首个非封面视频流映射 `0:V:0`；每个子进程超时 120 秒，当前构建必须具有 `scdet`、`scale`、`metadata`、`drawtext` 和 `tile` 滤镜。
- 应用内只允许一个 FFmpeg 任务运行；不同项目按启动顺序排队，同一项目重复启动返回 `409 preprocessing_in_progress`。
- 阶段状态必须原子持久化；失败清理当前阶段半成品并保留已完成阶段，重试从第一个未完成阶段继续。
- 遗留 `queued/running` 状态在服务重启后转为 `failed/preprocessing_interrupted`，不得假装后台任务仍在运行。
- 预处理运行或排队期间禁止替换参考视频；替换失败保留旧视频与旧结果，成功提交后才清空引用并清理旧产物。
- 窄屏只读；状态使用文字与图标联合传意，提供可见焦点、`aria-live="polite"` 和 `role="alert"`，不依赖颜色或动画。
- 不引入 OpenCV、NumPy、PyAV、本地视觉模型、外部任务系统、WebSocket、分析服务、播放器、Prompt、Workflow 或 ComfyUI 能力。
- Ticket 01/02 的项目、上传、替换、存储、无障碍、对比度和构建行为必须持续通过。
- 执行阶段使用 `gpt-5.6-terra / high` 负责代码、测试和修复；任务规格审查与最终审查使用 `gpt-5.6-sol / high`。
- 当前本地目录没有 `.git`，远端仓库为空；本计划不得初始化 Git、提交或推送，以每项任务的验证检查点代替提交步骤。

## File Structure

### Create

- `backend/app/local_preprocessing.py`：领域模型、固定常量、FFmpeg 元数据解析、镜头筛选、百分位、运动摘要、关键帧时间与初步判断。
- `backend/app/local_preprocessing_storage.py`：安全版本路径、原子 JSON、关键帧阶段提交、阶段有效性、失效清理。
- `backend/app/local_preprocessing_runner.py`：FFmpeg 能力检查和五阶段执行编排。
- `backend/app/local_preprocessing_jobs.py`：单工作线程队列、同项目运行期去重和关闭。
- `backend/tests/test_local_preprocessing.py`：纯领域算法与边界测试。
- `backend/tests/test_local_preprocessing_storage.py`：路径、原子提交、恢复和清理测试。
- `backend/tests/test_local_preprocessing_runner.py`：替身命令、真实 FFmpeg 产物和失败映射测试。
- `backend/tests/test_local_preprocessing_jobs.py`：队列顺序与同项目去重测试。
- `backend/tests/test_local_preprocessing_api.py`：项目兼容、启动、状态、重启、重试和替换事务测试。
- `frontend/src/localPreprocessingApi.ts`：启动请求、项目读取和统一错误转换。
- `frontend/src/localPreprocessingApi.test.ts`：URL、方法、响应与网络错误测试。
- `frontend/src/LocalPreprocessingPanel.tsx`：桌面启动、轮询、阶段、摘要、错误和只读状态。
- `frontend/src/LocalPreprocessingPanel.test.tsx`：完整前端状态机、轮询、焦点与响应式测试。

### Modify

- `backend/app/main.py`：扩展 `Project`、项目原子更新帮助函数、启动接口、任务处理回调、重启协调和替换失效。
- `backend/tests/test_projects_api.py`：锁定 `localPreprocessing: null` 与旧 JSON 兼容。
- `frontend/src/models.ts`：增加预处理共享类型及 `Project.localPreprocessing`。
- `frontend/src/App.tsx`：挂载面板、同步轮询项目、向参考视频面板传递处理锁定状态。
- `frontend/src/App.test.tsx`：恢复、轮询更新和项目切换回归。
- `frontend/src/ReferenceVideoPanel.tsx`：运行期禁用替换，完成结果替换确认说明失效影响。
- `frontend/src/ReferenceVideoPanel.test.tsx`：锁定状态与替换成功/失败行为。
- `frontend/src/styles.css`：阶段列表、摘要、范围、错误及 1024px 响应式样式。
- `README.md`：预处理前置条件、产物位置、隐私边界和验证命令。

---

### Task 1: 本地预处理领域模型与纯算法

**Files:**
- Create: `backend/app/local_preprocessing.py`
- Create: `backend/tests/test_local_preprocessing.py`

**Interfaces:**
- Consumes: `ReferenceVideo.id/durationSeconds/width/height/frameRate`，以及 FFmpeg `metadata=mode=print` 的文本输出。
- Produces: `LocalPreprocessing` 及其嵌套 Pydantic 模型；`parse_scdet_metadata()`、`detect_scene_changes()`、`summarize_motion()`、`select_keyframe_times()`、`assess_reproducibility()`、`new_local_preprocessing()`。后续任务必须原样使用这些名称和 camelCase API 字段。

- [ ] **Step 1: 写入镜头、运动与结论的失败测试**

在 `backend/tests/test_local_preprocessing.py` 中先写公开行为测试：

```python
from datetime import datetime, timezone

from app.local_preprocessing import (
    FrameMetric,
    MotionSummary,
    detect_scene_changes,
    new_local_preprocessing,
    parse_scdet_metadata,
    select_keyframe_times,
    summarize_motion,
    assess_reproducibility,
)


def metric(time: float, mafd: float, score: float = 0.0) -> FrameMetric:
    return FrameMetric(timeSeconds=time, mafd=mafd, sceneScore=score)


def test_scene_changes_ignore_start_merge_neighbors_and_include_threshold():
    changes = detect_scene_changes([
        metric(0.25, 30.0, 30.0),
        metric(1.00, 12.0, 10.0),
        metric(1.25, 20.0, 16.0),
        metric(2.00, 9.9, 9.9),
    ])
    assert [(item.timeSeconds, item.score) for item in changes] == [(1.25, 16.0)]


def test_motion_boundaries_and_insufficient_samples_are_explicit():
    light = summarize_motion([metric(index / 8, 2.5) for index in range(8)], [])
    moderate = summarize_motion([metric(index / 8, 8.0) for index in range(8)], [])
    high = summarize_motion([metric(index / 8, 8.01) for index in range(8)], [])
    unavailable = summarize_motion([metric(index / 8, 1.0) for index in range(7)], [])
    assert (light.level, moderate.level, high.level) == ("light", "moderate", "high")
    assert unavailable.model_dump() == {
        "p50": None, "p90": None, "peak": None, "level": "unavailable",
        "validSampleCount": 7, "excludedSampleCount": 0,
    }


def test_assessment_never_claims_final_in_scope():
    result = assess_reproducibility([], MotionSummary(
        p50=2.0, p90=3.0, peak=4.0, level="moderate",
        validSampleCount=16, excludedSampleCount=0,
    ))
    assert result.status == "pending_semantic_confirmation"
    assert [(item.criterion, item.status) for item in result.checks] == [
        ("single_shot", "passed"),
        ("motion_range", "passed"),
        ("primary_subject_count", "pending"),
        ("complex_interaction", "pending"),
    ]


def test_new_task_has_all_five_ordered_pending_stages():
    task = new_local_preprocessing(
        preprocessing_id="prep-001",
        reference_video_id="video-001",
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    assert task.status == "queued"
    assert [stage.name for stage in task.stages] == [
        "decoding", "sceneDetection", "keyframeExtraction",
        "motionAnalysis", "reproducibilityAssessment",
    ]
    assert {stage.status for stage in task.stages} == {"pending"}
```

新增以下具名参数化测试：`test_parse_rejects_missing_required_metric` 分别删除 `pts_time/mafd/score` 并断言 `ValueError`；`test_motion_excludes_inclusive_cut_window` 断言切换点前后恰好 0.25 秒被排除；`test_linear_percentile_interpolates_fixed_examples` 断言 `[0, 10]` 的 P90 为 9；`test_assessment_keeps_both_local_failure_reasons` 同时断言多镜头与高运动；`test_unavailable_motion_is_not_assessed` 断言不显示低运动；`test_keyframes_keep_ends_and_target_count` 覆盖 2 秒和 10 秒素材。

- [ ] **Step 2: 运行定向测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing.py
```

Expected: 因 `app.local_preprocessing` 尚不存在而在收集阶段失败；不得修改现有测试来绕过失败。

- [ ] **Step 3: 实现稳定模型与固定常量**

在 `backend/app/local_preprocessing.py` 中定义以下公开契约：

```python
import math
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ALGORITHM_VERSION = 1
ANALYSIS_SAMPLE_RATE = 8.0
ANALYSIS_LONG_EDGE = 320
KEYFRAME_LONG_EDGE = 768
CONTACT_SHEET_CELL_LONG_EDGE = 384
MAX_KEYFRAMES = 12
MIN_KEYFRAMES = 4
SCENE_SCORE_THRESHOLD = 10.0
SCENE_START_IGNORE_SECONDS = 0.5
SCENE_MERGE_WINDOW_SECONDS = 0.5
MOTION_EXCLUSION_SECONDS = 0.25
MIN_MOTION_SAMPLES = 8
LIGHT_MOTION_P90_MAX = 2.5
MODERATE_MOTION_P90_MAX = 8.0
FFMPEG_TIMEOUT_SECONDS = 120.0

StageName = Literal[
    "decoding", "sceneDetection", "keyframeExtraction",
    "motionAnalysis", "reproducibilityAssessment",
]
STAGE_ORDER: tuple[StageName, StageName, StageName, StageName, StageName] = (
    "decoding", "sceneDetection", "keyframeExtraction",
    "motionAnalysis", "reproducibilityAssessment",
)


class FrameMetric(BaseModel):
    timeSeconds: float = Field(ge=0)
    mafd: float = Field(ge=0)
    sceneScore: float = Field(ge=0)


class SceneChange(BaseModel):
    timeSeconds: float = Field(ge=0)
    score: float = Field(ge=SCENE_SCORE_THRESHOLD)


class MotionSummary(BaseModel):
    p50: Optional[float]
    p90: Optional[float]
    peak: Optional[float]
    level: Literal["light", "moderate", "high", "unavailable"]
    validSampleCount: int = Field(ge=0)
    excludedSampleCount: int = Field(ge=0)


class AnalysisProxySummary(BaseModel):
    keyframeCount: int = Field(ge=MIN_KEYFRAMES, le=MAX_KEYFRAMES)
    contactSheetCount: Literal[1] = 1
    sceneChangeCount: int = Field(ge=0)
    motionP50: Optional[float]
    motionP90: Optional[float]
    motionPeak: Optional[float]
    motionLevel: Literal["light", "moderate", "high", "unavailable"]


class ReproducibilityCheck(BaseModel):
    criterion: Literal[
        "single_shot", "motion_range",
        "primary_subject_count", "complex_interaction",
    ]
    status: Literal["passed", "failed", "pending", "not_assessed"]
    message: str
    evidence: str


class ReproducibilityAssessment(BaseModel):
    status: Literal["out_of_scope", "pending_semantic_confirmation"]
    checks: list[ReproducibilityCheck]


class PreprocessingStageState(BaseModel):
    name: StageName
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None


class LocalPreprocessingError(BaseModel):
    code: str
    message: str
    stage: StageName
    retryable: Literal[True] = True


class LocalPreprocessing(BaseModel):
    id: str
    sourceReferenceVideoId: str
    algorithmVersion: Literal[1] = ALGORITHM_VERSION
    status: Literal["queued", "running", "completed", "failed"]
    currentStage: Optional[StageName] = None
    stages: list[PreprocessingStageState]
    queuedAt: str
    startedAt: Optional[str] = None
    updatedAt: str
    completedAt: Optional[str] = None
    proxySummary: Optional[AnalysisProxySummary] = None
    reproducibilityAssessment: Optional[ReproducibilityAssessment] = None
    error: Optional[LocalPreprocessingError] = None
```

所有 ID 使用 `validate_storage_id()` 校验；所有时间戳使用与 `Project` 相同的带时区 ISO-8601 校验器。

- [ ] **Step 4: 实现解析、镜头与运动算法**

实现公开函数并保持无 I/O：

```python
def parse_scdet_metadata(output: str) -> list[FrameMetric]:
    blocks: list[dict[str, float]] = []
    current: dict[str, float] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("frame:"):
            if current:
                blocks.append(current)
            current = {}
            match = re.search(r"(?:^|\s)pts_time:([^\s]+)", line)
            if match:
                current["timeSeconds"] = float(match.group(1))
        elif line.startswith("lavfi.scd.mafd="):
            current["mafd"] = float(line.split("=", 1)[1])
        elif line.startswith("lavfi.scd.score="):
            current["sceneScore"] = float(line.split("=", 1)[1])
    if current:
        blocks.append(current)
    required = {"timeSeconds", "mafd", "sceneScore"}
    if not blocks or any(set(block) != required for block in blocks):
        raise ValueError("镜头检测元数据不完整")
    return [FrameMetric(**block) for block in blocks]


def detect_scene_changes(metrics: list[FrameMetric]) -> list[SceneChange]:
    candidates = [
        SceneChange(timeSeconds=item.timeSeconds, score=item.sceneScore)
        for item in sorted(metrics, key=lambda value: value.timeSeconds)
        if item.timeSeconds >= SCENE_START_IGNORE_SECONDS
        and item.sceneScore >= SCENE_SCORE_THRESHOLD
    ]
    groups: list[list[SceneChange]] = []
    for candidate in candidates:
        if not groups or candidate.timeSeconds - groups[-1][0].timeSeconds > SCENE_MERGE_WINDOW_SECONDS:
            groups.append([candidate])
        else:
            groups[-1].append(candidate)
    return [max(group, key=lambda item: (item.score, -item.timeSeconds)) for group in groups]


def summarize_motion(
    metrics: list[FrameMetric], changes: list[SceneChange]
) -> MotionSummary:
    values = [
        item.mafd for item in metrics
        if not any(abs(item.timeSeconds - change.timeSeconds) <= MOTION_EXCLUSION_SECONDS for change in changes)
    ]
    excluded = len(metrics) - len(values)
    if len(values) < MIN_MOTION_SAMPLES:
        return MotionSummary(
            p50=None, p90=None, peak=None, level="unavailable",
            validSampleCount=len(values), excludedSampleCount=excluded,
        )
    p50 = round(linear_percentile(values, 0.5), 3)
    p90 = round(linear_percentile(values, 0.9), 3)
    level = "light" if p90 <= 2.5 else "moderate" if p90 <= 8.0 else "high"
    return MotionSummary(
        p50=p50, p90=p90, peak=round(max(values), 3), level=level,
        validSampleCount=len(values), excludedSampleCount=excluded,
    )
```

实现细节必须是：按 `frame:` 行切分元数据块；每块同时具有有限非负的 `pts_time`、`lavfi.scd.mafd` 和 `lavfi.scd.score` 才能生成 `FrameMetric`，否则抛出 `ValueError("镜头检测元数据不完整")`。镜头候选先按时间排序、过滤 `<0.5` 秒和 `<10.0` 分数，再把与当前组首项距离 `<=0.5` 秒的候选归为一组并保留最高分；同分保留较早时间。

百分位使用下面的固定线性插值：

```python
def linear_percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("百分位至少需要一个样本")
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
```

运动样本排除条件为 `abs(metric.timeSeconds - change.timeSeconds) <= 0.25`。少于 8 个有效样本返回空统计和 `unavailable`；否则以 P90 的包含边界选择等级，统计值统一四舍五入到 3 位小数。

- [ ] **Step 5: 实现关键帧时间和初步判断**

公开签名固定为：

```python
def select_keyframe_times(
    duration_seconds: float,
    frame_rate: float,
    changes: list[SceneChange],
) -> list[float]:
    end = max(0.0, duration_seconds - 1 / frame_rate)
    target = min(MAX_KEYFRAMES, max(MIN_KEYFRAMES, math.ceil(duration_seconds)))
    boundaries = [0.0] + [min(end, item.timeSeconds) for item in changes] + [end]
    midpoints = [(left + right) / 2 for left, right in zip(boundaries, boundaries[1:])]
    uniform = [end * index / (target - 1) for index in range(target)]
    selected = [0.0, end]

    def is_new(value: float) -> bool:
        return all(abs(value - existing) > 1 / ANALYSIS_SAMPLE_RATE for existing in selected)

    for pool in (midpoints, uniform):
        remaining = [value for value in pool if is_new(value)]
        while remaining and len(selected) < target:
            chosen = max(
                remaining,
                key=lambda value: (min(abs(value - existing) for existing in selected), -value),
            )
            selected.append(chosen)
            remaining = [value for value in remaining if is_new(value)]
    return sorted({round(value, 3) for value in selected})


def assess_reproducibility(
    changes: list[SceneChange],
    motion: MotionSummary,
) -> ReproducibilityAssessment:
    shot_failed = bool(changes)
    motion_status = (
        "not_assessed" if motion.level == "unavailable"
        else "failed" if motion.level == "high" else "passed"
    )
    checks = [
        ReproducibilityCheck(
            criterion="single_shot", status="failed" if shot_failed else "passed",
            message="检测到多个镜头。" if shot_failed else "未检测到镜头切换。",
            evidence=f"有效镜头切换点 {len(changes)} 个。",
        ),
        ReproducibilityCheck(
            criterion="motion_range", status=motion_status,
            message=(
                "运动样本不足，无法独立评估。" if motion.level == "unavailable"
                else "运动强度超出轻中度范围。" if motion.level == "high"
                else "运动强度处于轻中度范围。"
            ),
            evidence=("有效连续运动样本少于 8 个。" if motion.p90 is None else f"运动强度 P90 为 {motion.p90:.3f}。"),
        ),
        ReproducibilityCheck(
            criterion="primary_subject_count", status="pending",
            message="主要主体数量待语义分析确认。", evidence="本地预处理不执行主体识别。",
        ),
        ReproducibilityCheck(
            criterion="complex_interaction", status="pending",
            message="复杂交互待语义分析确认。", evidence="本地预处理不执行交互识别。",
        ),
    ]
    failed = any(item.status == "failed" for item in checks[:2])
    return ReproducibilityAssessment(
        status="out_of_scope" if failed else "pending_semantic_confirmation",
        checks=checks,
    )


def new_local_preprocessing(
    preprocessing_id: str,
    reference_video_id: str,
    now: datetime,
) -> LocalPreprocessing:
    timestamp = now.isoformat()
    stages = [PreprocessingStageState(name=name) for name in STAGE_ORDER]
    return LocalPreprocessing(
        id=preprocessing_id, sourceReferenceVideoId=reference_video_id,
        status="queued", stages=stages, queuedAt=timestamp, updatedAt=timestamp,
    )
```

`select_keyframe_times()` 以 `max(0, durationSeconds - 1 / frameRate)` 作为末帧时间；镜头边界把时长切成区间并生成中点；均匀候选用目标数量在首尾间等距生成。先保留首尾，再按“与已选时间的最小距离最大”选择镜头中点，最后用相同规则选择均匀候选；相距 `<=1/8` 秒视为同一时间，末帧优先保留。结果升序并四舍五入到 3 位小数。

`assess_reproducibility()` 始终按四项固定顺序输出；多镜头或 `high` 任一存在即为 `out_of_scope`。`unavailable` 映射到 `motion_range/not_assessed`，但它只与已失败的密集多镜头情况共同出现；主体与交互始终为 `pending`。

- [ ] **Step 6: 运行算法测试并做后端回归**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing.py
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_projects_api.py backend/tests/test_video_probe.py
```

Expected: 新算法测试全绿；Ticket 01/02 的项目和媒体探测测试全绿。

- [ ] **Step 7: 验证检查点**

Run:

```sh
PYTHONPATH=backend .venv/bin/python -m compileall -q backend/app
```

Expected: 无语法错误；`backend/pyproject.toml` 和 `requirements.lock` 没有新增依赖。

---

### Task 2: 版本目录、原子产物与阶段恢复

**Files:**
- Create: `backend/app/local_preprocessing_storage.py`
- Create: `backend/tests/test_local_preprocessing_storage.py`

**Interfaces:**
- Consumes: Task 1 的 `StageName`、`LocalPreprocessing`、`ALGORITHM_VERSION` 和 `validate_storage_id()`。
- Produces: `preprocessing_directory()`、`atomic_write_json()`、`write_stage_json()`、`commit_keyframe_stage()`、`validate_completed_stages()`、`reset_stage_artifacts()`、`discard_preprocessing()`；Task 3 只通过这些函数写产物，Task 5 使用清理接口完成参考视频替换。

- [ ] **Step 1: 写入路径安全、原子提交和恢复失败测试**

创建 `backend/tests/test_local_preprocessing_storage.py`：

```python
import json

import pytest

from app.local_preprocessing_storage import (
    atomic_write_json,
    commit_keyframe_stage,
    preprocessing_directory,
    reset_stage_artifacts,
    validate_completed_stages,
)


def test_preprocessing_path_stays_below_data_directory(tmp_path):
    path = preprocessing_directory(tmp_path, "project-001", "prep-001")
    assert path == tmp_path / "project-files/project-001/local-preprocessing/prep-001"
    with pytest.raises(ValueError):
        preprocessing_directory(tmp_path, "../outside", "prep-001")


def test_atomic_json_never_leaves_part_file(tmp_path):
    target = tmp_path / "prep/decode.json"
    atomic_write_json(target, {"sourceReferenceVideoId": "video-001"})
    assert json.loads(target.read_text(encoding="utf-8"))["sourceReferenceVideoId"] == "video-001"
    assert list(tmp_path.rglob("*.part")) == []


def test_completed_stage_with_missing_artifact_resets_it_and_followers(preprocessing):
    valid = validate_completed_stages(preprocessing, preprocessing.output_dir)
    assert valid.firstInvalidStage == "keyframeExtraction"
    assert [stage.status for stage in valid.preprocessing.stages] == [
        "completed", "completed", "pending", "pending", "pending",
    ]


def test_keyframe_stage_is_all_or_nothing(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "keyframes").mkdir(parents=True)
    (workspace / "keyframes/frame-0001.jpg").write_bytes(b"jpeg")
    (workspace / "contact-sheet.jpg").write_bytes(b"sheet")
    destination = tmp_path / "prep"
    commit_keyframe_stage(workspace, destination, ["frame-0001.jpg"])
    assert (destination / "keyframes/frame-0001.jpg").read_bytes() == b"jpeg"
    assert (destination / "contact-sheet.jpg").read_bytes() == b"sheet"
```

再覆盖父目录符号链接逃逸、`manifest.json` 算法版本不符、清理当前及后续阶段、关键帧清单缺帧、清理失败不越界和 JSON `os.replace` 失败。

- [ ] **Step 2: 运行定向测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing_storage.py
```

Expected: 因存储模块不存在失败。

- [ ] **Step 3: 实现安全路径和原子 JSON**

使用与参考视频托管相同的“验证 ID + resolve 后确认仍在根目录”规则：

```python
def preprocessing_directory(data_dir: Path, project_id: str, preprocessing_id: str) -> Path:
    validate_storage_id(project_id)
    validate_storage_id(preprocessing_id)
    root = data_dir.resolve()
    candidate = root / "project-files" / project_id / "local-preprocessing" / preprocessing_id
    candidate.resolve(strict=False).relative_to(root)
    return candidate


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".part")
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
```

把 `ValueError` 和 `relative_to()` 的路径逃逸统一转换为 `OSError("本地预处理路径超出数据目录")`，并拒绝父目录为符号链接且解析后离开 `data_dir` 的情况。

- [ ] **Step 4: 实现阶段产物表和关键帧提交**

固定完成产物：

```python
STAGE_FILES = {
    "decoding": ("decode.json",),
    "sceneDetection": ("scene-changes.json",),
    "keyframeExtraction": ("contact-sheet.jpg", "keyframes"),
    "motionAnalysis": ("motion.json",),
    "reproducibilityAssessment": ("analysis-proxy.json", "manifest.json"),
}
```

`commit_keyframe_stage(workspace, destination, frame_names)` 必须验证帧名严格匹配 `frame-0001.jpg` 形式、清单非空、每个文件非空且联系表非空。目标存在时先只清理当前阶段产物；以 `os.replace(workspace / "keyframes", destination / "keyframes")` 提交帧目录，再以 `os.replace()` 提交联系表。任何异常都删除已出现的当前阶段最终产物，不触碰前序 JSON。

- [ ] **Step 5: 实现阶段验证、回退与受限清理**

定义：

```python
class StageValidation(BaseModel):
    preprocessing: LocalPreprocessing
    firstInvalidStage: Optional[StageName]


def validate_completed_stages(
    preprocessing: LocalPreprocessing,
    directory: Path,
) -> StageValidation:
    updated = preprocessing.model_copy(deep=True)
    first_invalid = None
    for index, state in enumerate(updated.stages):
        if state.status != "completed":
            first_invalid = state.name
            break
        if not stage_artifacts_are_valid(state.name, directory, updated):
            first_invalid = state.name
            break
    if first_invalid is not None:
        start = STAGE_ORDER.index(first_invalid)
        reset_stage_artifacts(directory, first_invalid)
        for state in updated.stages[start:]:
            state.status = "pending"
            state.startedAt = None
            state.completedAt = None
    return StageValidation(preprocessing=updated, firstInvalidStage=first_invalid)


def reset_stage_artifacts(directory: Path, from_stage: StageName) -> None:
    for stage in STAGE_ORDER[STAGE_ORDER.index(from_stage):]:
        for relative in STAGE_FILES[stage]:
            discard_path(directory / relative)


def discard_preprocessing(data_dir: Path, project_id: str, preprocessing_id: str) -> None:
    discard_path(preprocessing_directory(data_dir, project_id, preprocessing_id))
```

同时实现 `stage_artifacts_are_valid()`：普通 JSON 必须为非空可解析对象；关键帧阶段要求非空联系表、4～12 个连续 `frame-NNNN.jpg` 且每个非空；最终阶段要求两个 JSON 非空可解析，并验证 manifest 的 `schemaVersion == 1`、`algorithmVersion == 1`、`sourceReferenceVideoId` 与任务一致。实现 `write_stage_json(directory, filename, payload)` 为只允许 `decode.json`、`scene-changes.json`、`motion.json`、`analysis-proxy.json`、`manifest.json` 的 `atomic_write_json()` 包装。

`discard_path()` 对不存在路径无操作；符号链接或普通文件只 `unlink()`；普通目录才 `shutil.rmtree()`。清理只能作用于已经过 `preprocessing_directory()` 验证的单个版本目录，不接受调用方提供的任意绝对目录。

- [ ] **Step 6: 运行存储测试和安全回归**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_local_preprocessing_storage.py \
  backend/tests/test_reference_video_upload_api.py::test_managed_path_rejects_a_parent_symlink_that_escapes_data_directory \
  backend/tests/test_reference_video_upload_api.py::test_unsafe_persisted_id_cannot_delete_an_external_sentinel
```

Expected: 新存储测试及 Ticket 02 路径安全测试全绿。

- [ ] **Step 7: 验证检查点**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py
```

Expected: 领域与存储层测试全绿，无 `.part` 或阶段 workspace 遗留在 pytest 临时目录。

---

### Task 3: FFmpeg 五阶段执行器与分析代理

**Files:**
- Create: `backend/app/local_preprocessing_runner.py`
- Create: `backend/tests/test_local_preprocessing_runner.py`

**Interfaces:**
- Consumes: Task 1 的算法和模型、Task 2 的存储函数、Ticket 02 的 `ReferenceVideo` 与托管视频路径。
- Produces: `LocalPreprocessingFailure`、`PreprocessingRunResult`、`run_local_preprocessing`。Task 5 通过阶段回调持久化状态，不直接解析 FFmpeg 输出。

- [ ] **Step 1: 写入能力检查、命令边界和失败映射测试**

创建 `backend/tests/test_local_preprocessing_runner.py`，以可注入命令执行函数捕获参数：

```python
import subprocess

import pytest

from app.local_preprocessing_runner import (
    LocalPreprocessingFailure,
    check_ffmpeg_capabilities,
    decode_reference_video,
)


def completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ffmpeg"], 0, stdout=stdout, stderr="")


def test_decode_uses_non_attached_video_map_and_no_shell(tmp_path):
    commands = []
    source = tmp_path / "clip with spaces.mp4"
    source.write_bytes(b"video")

    def capture(command, **kwargs):
        commands.append((command, kwargs))
        return completed()

    decode_reference_video(source, ffmpeg_path="/custom/ffmpeg", run=capture)

    command, kwargs = commands[0]
    assert command == [
        "/custom/ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(source), "-map", "0:V:0", "-an", "-f", "null", "-",
    ]
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 120.0


def test_missing_required_filter_is_a_stable_decoding_error():
    def no_drawtext(*args, **kwargs):
        return completed(" .. scdet V->V\n .. scale V->V\n .. metadata V->V\n .. tile V->V")

    with pytest.raises(LocalPreprocessingFailure) as captured:
        check_ffmpeg_capabilities("ffmpeg", run=no_drawtext)

    assert captured.value.code == "ffmpeg_filters_unavailable"
    assert captured.value.stage == "decoding"
    assert "drawtext" in captured.value.message
```

加入 `FileNotFoundError`、`PermissionError`、`TimeoutExpired`、非零退出、无效 `scdet` 元数据、空 JPEG、联系表失败和写盘错误到稳定 code/stage 的测试。断言错误消息不包含输入绝对路径或原始 stderr。

- [ ] **Step 2: 运行定向测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing_runner.py
```

Expected: 因 runner 模块不存在失败。

- [ ] **Step 3: 实现命令执行边界和能力检查**

公开错误和执行包装：

```python
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Callable

from app.local_preprocessing import StageName

CommandRunner = Callable
REQUIRED_FILTERS = {"scdet", "scale", "metadata", "drawtext", "tile"}


class LocalPreprocessingFailure(Exception):
    def __init__(self, code: str, message: str, stage: StageName) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = True


def run_command(
    command: list[str], *, stage: StageName, code: str,
    message: str, run: CommandRunner,
) -> CompletedProcess[str]:
    try:
        result = run(
            command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False, shell=False,
        )
    except (FileNotFoundError, PermissionError) as error:
        raise LocalPreprocessingFailure(
            "ffmpeg_unavailable",
            "本地未找到或无法启动 FFmpeg，请确认 ffmpeg -version 可运行。",
            stage,
        ) from error
    except subprocess.TimeoutExpired as error:
        raise LocalPreprocessingFailure(code, message, stage) from error
    if result.returncode != 0:
        logging.getLogger(__name__).warning("FFmpeg 阶段失败：%s", result.stderr[-2000:])
        raise LocalPreprocessingFailure(code, message, stage)
    return result
```

`run_command()` 固定传入 `capture_output=True`、`text=True`、`timeout=120.0`、`check=False`、`shell=False`。`FileNotFoundError/PermissionError` 在解码能力检查中映射为 `ffmpeg_unavailable`；超时和非零退出使用调用方传入的阶段 code。日志中的 stderr 最多保留 2,000 个字符并通过 `logging` 写入，不拼入用户消息。

能力检查运行：

```python
[ffmpeg_path, "-hide_banner", "-filters"]
```

按每行滤镜名称列解析，不使用任意子串匹配；缺失项排序后写入 `ffmpeg_filters_unavailable` 消息。

- [ ] **Step 4: 实现解码和镜头数据阶段**

解码命令严格为 Step 1 的数组；成功后写 `decode.json`：

```json
{
  "schemaVersion": 1,
  "algorithmVersion": 1,
  "sourceReferenceVideoId": "video-001",
  "expectedFrameCount": 60,
  "completedAt": "2026-09-11T10:00:00+00:00",
  "ffmpegVersion": "8.1.1"
}
```

帧数使用 `round(durationSeconds * frameRate)` 作为已校验元数据摘要，不声称是第二次精确探测值。镜头分析命令：

```python
analysis_filter = (
    "fps=8,"
    "scale=w='if(gte(iw,ih),320,-2)':h='if(gte(iw,ih),-2,320)',"
    "format=gray,scdet=t=10,metadata=mode=print:file=-"
)
command = [
    ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error",
    "-i", str(source_path), "-map", "0:V:0", "-an",
    "-vf", analysis_filter, "-f", "null", "-",
]
```

用 Task 1 解析器生成 `scene-changes.json`，字段固定为 `schemaVersion`、`algorithmVersion`、`sampleRate`、`samples` 和 `sceneChanges`；数值保留 3 位小数。

- [ ] **Step 5: 实现关键帧和联系表阶段**

对 Task 1 选出的每个时间点运行独立、安全的抽帧命令：

```python
[
    ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y",
    "-ss", f"{time_seconds:.3f}", "-i", str(source_path),
    "-map", "0:V:0", "-frames:v", "1", "-an",
    "-vf", "scale=w='if(gte(iw,ih),min(768,iw),-2)':h='if(gte(iw,ih),-2,min(768,ih))'",
    "-q:v", "3", str(workspace / f"keyframes/frame-{index:04d}.jpg"),
]
```

每个输出必须存在且非空。随后为联系表生成临时标注帧，时间码格式固定为 `MM:SS.mmm`；传给 `drawtext` 前转义反斜线、单引号、冒号和百分号。标注滤镜使用 `font=monospace`、字号 22、白字、半透明深炭底框。最终按连续编号读取标注帧并运行：

```python
rows = math.ceil(keyframe_count / 3)
contact_filter = (
    "scale=w='if(gte(iw,ih),min(384,iw),-2)':h='if(gte(iw,ih),-2,min(384,ih))',"
    f"tile=3x{rows}:padding=8:margin=8:color=0x202427"
)
```

输出固定为 `contact-sheet.jpg`；不足三列的最后一行由 `tile` 背景填充。所有临时标注帧留在 workspace，提交时只移动干净关键帧与联系表。

- [ ] **Step 6: 实现运动、判断、代理和 manifest 阶段**

从 `scene-changes.json` 读取 Task 1 模型，生成 `motion.json`。判断阶段写入字段固定的 `analysis-proxy.json`：

```json
{
  "schemaVersion": 1,
  "source": {"durationSeconds": 2.5, "width": 854, "height": 480, "frameRate": 24.0},
  "keyframes": [{"index": 1, "timeSeconds": 0.0}],
  "scene": {"changeCount": 0, "changeTimesSeconds": []},
  "motion": {
    "samples": [{"timeSeconds": 0.0, "mafd": 0.0}],
    "p50": 0.0, "p90": 0.0, "peak": 0.0, "level": "light"
  },
  "contactSheetFile": "contact-sheet.jpg"
}
```

最多保留已有 80 个按时间排序样本；不得加入项目名、`originalName`、本地路径或供应商字段。最终 `manifest.json` 包含 `schemaVersion`、`algorithmVersion`、`sourceReferenceVideoId`、`generatedAt`、全部固定参数、关键帧清单、相对产物名、`proxySummary` 和 `reproducibilityAssessment`，使完成结果可以不重新读取视频而恢复为领域模型。

公开总入口：

```python
@dataclass(frozen=True)
class PreprocessingRunResult:
    proxy_summary: AnalysisProxySummary
    assessment: ReproducibilityAssessment


def run_local_preprocessing(
    *,
    source_path: Path,
    reference: ReferenceVideo,
    preprocessing: LocalPreprocessing,
    output_directory: Path,
    ffmpeg_path: str,
    on_stage_started: Callable[[StageName], None],
    on_stage_completed: Callable[[StageName], None],
    run: CommandRunner = subprocess.run,
) -> PreprocessingRunResult:
    validated = validate_completed_stages(preprocessing, output_directory).preprocessing
    handlers = {
        "decoding": lambda: run_decoding_stage(
            source_path, reference, output_directory, ffmpeg_path, run,
        ),
        "sceneDetection": lambda: run_scene_detection_stage(
            source_path, output_directory, ffmpeg_path, run,
        ),
        "keyframeExtraction": lambda: run_keyframe_stage(
            source_path, reference, output_directory, ffmpeg_path, run,
        ),
        "motionAnalysis": lambda: run_motion_stage(output_directory),
        "reproducibilityAssessment": lambda: run_assessment_stage(
            reference, validated, output_directory,
        ),
    }
    for state in validated.stages:
        if state.status == "completed":
            continue
        on_stage_started(state.name)
        try:
            handlers[state.name]()
        except LocalPreprocessingFailure:
            reset_stage_artifacts(output_directory, state.name)
            raise
        except (OSError, ValueError, ValidationError) as error:
            reset_stage_artifacts(output_directory, state.name)
            raise stage_failure_for(state.name, error) from error
        on_stage_completed(state.name)
    return load_preprocessing_result(output_directory)
```

`run_decoding_stage()` 先执行能力检查和完整解码，再写 `decode.json`；其余四个 stage 函数严格执行 Steps 4-6 的输入输出。`stage_failure_for()` 使用当前阶段映射到设计规格中的稳定 code/message；`load_preprocessing_result()` 从 `motion.json` 和 `manifest.json` 验证并构造 `AnalysisProxySummary` 与 `ReproducibilityAssessment`。总入口不自行修改项目 JSON。

- [ ] **Step 7: 添加真实 FFmpeg 确定性集成测试**

测试沿用 `shutil.which("ffmpeg")` 的现有跳过模式，通过 `lavfi` 生成：

```sh
ffmpeg -y -f lavfi -i color=c=0x343938:s=854x480:r=24 -t 2 -c:v libx264 -pix_fmt yuv420p static.mp4
ffmpeg -y -f lavfi -i testsrc2=s=854x480:r=24 -t 3 -c:v libx264 -pix_fmt yuv420p moving.mp4
```

另用黑/白/黑 concat 生成两次硬切，用固定 seed 的 `life` 源生成高运动，用 3840×2160 的 2 秒纯色源验证缩放。断言：静态/常规运动无切换且待语义确认；硬切至少一个有效切换且超范围；高运动 P90 大于 8；4K 原文件 SHA-256 前后相同；关键帧最长边、联系表、代理字段和无视频副本满足规格。

- [ ] **Step 8: 运行 runner 测试和后端回归**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_local_preprocessing.py \
  backend/tests/test_local_preprocessing_storage.py \
  backend/tests/test_local_preprocessing_runner.py
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_probe.py
```

Expected: runner 的替身测试与本机可用的真实 FFmpeg 测试通过；Ticket 02 探测测试不回归。

---

### Task 4: 单工作线程队列与同项目去重

**Files:**
- Create: `backend/app/local_preprocessing_jobs.py`
- Create: `backend/tests/test_local_preprocessing_jobs.py`

**Interfaces:**
- Consumes: 一个 `Callable[[str], None]` 项目处理函数。
- Produces: `LocalPreprocessingJobQueue.submit(project_id) -> bool`、`is_active(project_id) -> bool`、`shutdown()`；Task 5 负责持久化 `queued` 后调用队列。

- [ ] **Step 1: 写入串行、FIFO 和去重失败测试**

```python
from threading import Event, Lock

from app.local_preprocessing_jobs import LocalPreprocessingJobQueue


def test_queue_runs_projects_one_at_a_time_in_submission_order():
    release = Event()
    started = []
    lock = Lock()

    def handle(project_id: str):
        with lock:
            started.append(project_id)
        if project_id == "project-001":
            release.wait(timeout=2)

    queue = LocalPreprocessingJobQueue(handle)
    assert queue.submit("project-001") is True
    assert queue.submit("project-002") is True
    wait_until(lambda: started == ["project-001"])
    release.set()
    wait_until(lambda: started == ["project-001", "project-002"])
    queue.shutdown()


def test_queue_rejects_same_project_until_handler_finishes():
    release = Event()
    queue = LocalPreprocessingJobQueue(lambda _: release.wait(timeout=2))
    assert queue.submit("project-001") is True
    assert queue.submit("project-001") is False
    release.set()
    wait_until(lambda: not queue.is_active("project-001"))
    queue.shutdown()
```

`wait_until()` 使用 `time.monotonic()` 的 2 秒上限和 0.01 秒短轮询，失败时给出当前状态；测试 teardown 必须释放事件并关闭队列。

- [ ] **Step 2: 运行测试并确认模块缺失失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing_jobs.py
```

Expected: 收集阶段失败。

- [ ] **Step 3: 实现最小单线程队列**

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Callable


class LocalPreprocessingJobQueue:
    def __init__(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-preprocessing")
        self._active: set[str] = set()
        self._lock = Lock()
        self._closed = False

    def submit(self, project_id: str) -> bool:
        with self._lock:
            if self._closed or project_id in self._active:
                return False
            self._active.add(project_id)
        try:
            self._executor.submit(self._run, project_id)
        except RuntimeError:
            with self._lock:
                self._active.discard(project_id)
            return False
        return True

    def _run(self, project_id: str) -> None:
        try:
            self._handler(project_id)
        finally:
            with self._lock:
                self._active.discard(project_id)

    def is_active(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._active

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
```

不在队列中保存业务状态、结果或错误；handler 必须自行捕获错误，防止 Future 静默吞掉业务失败。

- [ ] **Step 4: 运行队列和线程泄漏测试**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing_jobs.py
```

Expected: FIFO、串行、去重和 shutdown 全绿；测试进程能立即退出，无悬挂工作线程。

- [ ] **Step 5: 验证检查点**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing*.py
```

Expected: 前四个新增测试文件全部通过。

---

### Task 5: 项目持久化、启动 API、重启协调与替换事务

**Files:**
- Modify: `backend/app/main.py:1-384`
- Modify: `backend/tests/test_projects_api.py`
- Create: `backend/tests/test_local_preprocessing_api.py`

**Interfaces:**
- Consumes: Tasks 1-4 的模型、runner、存储与队列；现有 `_read_projects()`、`_write_projects()`、`project_write_lock` 和参考视频提交事务。
- Produces: `Project.localPreprocessing`、`POST /api/projects/{id}/local-preprocessing`、后台阶段持久化、启动时中断协调，以及替换时的锁定/失效行为。前端任务从完整 Project 响应读取全部状态。

- [ ] **Step 1: 写入旧项目兼容和启动 API 失败测试**

在 `backend/tests/test_projects_api.py` 增加：

```python
def test_new_and_legacy_projects_have_no_local_preprocessing(tmp_path):
    legacy = [{
        "id": "project-001", "name": "旧项目",
        "createdAt": "2026-09-10T10:00:00+00:00",
        "updatedAt": "2026-09-10T10:00:00+00:00",
    }]
    (tmp_path / "projects.json").write_text(json.dumps(legacy), encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/api/projects/project-001").json()["localPreprocessing"] is None
    created = client.post("/api/projects", json={"name": "新项目"}).json()
    assert created["localPreprocessing"] is None
```

在新 API 测试中使用可控队列：

```python
class ManualQueue:
    def __init__(self, handler):
        self.handler = handler
        self.pending = []

    def submit(self, project_id):
        self.pending.append(project_id)
        return True

    def run_next(self):
        self.handler(self.pending.pop(0))

    def shutdown(self):
        return None


def test_start_persists_queued_state_before_dispatch(client_with_video_and_queue):
    client, project, queue = client_with_video_and_queue
    response = client.post(f"/api/projects/{project['id']}/local-preprocessing")
    assert response.status_code == 202
    assert response.json()["localPreprocessing"]["status"] == "queued"
    assert queue.pending == [project["id"]]
```

加入不存在项目、无参考视频、重复启动、完成结果 200 幂等、失败重试复用 ID、每阶段回调、runner 失败、项目写入失败和队列提交失败测试。

- [ ] **Step 2: 运行定向测试并确认缺少字段和路由而失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_projects_api.py::test_new_and_legacy_projects_have_no_local_preprocessing \
  backend/tests/test_local_preprocessing_api.py
```

Expected: 新字段断言或新路由失败；Ticket 01/02 原测试仍保持原始结果。

- [ ] **Step 3: 扩展 Project 并集中项目原子更新**

在 `Project` 增加：

```python
localPreprocessing: Optional[LocalPreprocessing] = None
```

在 `create_app()` 内新增唯一项目更新入口：

```python
def update_project(project_id: str, transform: Callable[[Project], Project]) -> Project:
    with project_write_lock:
        projects = _read_projects(data_dir)
        index = next((i for i, project in enumerate(projects) if project.id == project_id), None)
        if index is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found", "message": "复刻项目不存在。"},
            )
        updated = transform(projects[index])
        projects[index] = updated
        _write_projects(data_dir, projects)
        return updated
```

阶段回调、完成、失败和启动都必须通过该函数写项目，不能在 worker 持有项目锁期间运行 FFmpeg。

- [ ] **Step 4: 实现启动时中断协调和后台 handler**

`create_app()` 在创建队列前、注册路由前运行 `reconcile_interrupted_preprocessing()`：把所有 `queued/running` 改为 `failed`，失败 stage 取 `currentStage` 或第一个未完成阶段，写入 `preprocessing_interrupted`，清理该阶段及后续产物并保留已完成阶段。

为可控测试扩展工厂签名：

```python
def create_app(
    data_dir: Path,
    *,
    ffprobe_path: str = "ffprobe",
    ffprobe_timeout_seconds: float = DEFAULT_FFPROBE_TIMEOUT_SECONDS,
    max_reference_video_bytes: int = MAX_REFERENCE_VIDEO_BYTES,
    max_multipart_body_bytes: int = MAX_MULTIPART_BODY_BYTES,
    ffmpeg_path: str = "ffmpeg",
    preprocessing_runner: Callable = run_local_preprocessing,
    preprocessing_queue_factory: Callable = LocalPreprocessingJobQueue,
) -> FastAPI:
```

后台 handler 的顺序固定为：重新读取项目与当前参考视频；验证任务仍为 queued；验证/回退已有阶段产物；把总状态改为 running；runner 回调逐阶段更新 `currentStage`、stage 状态和时间；成功写 summary、assessment、completedAt 并清空 error；`LocalPreprocessingFailure` 写对应 stage/error；未预期异常映射为 `preprocessing_unexpected_error`、使用当时 `currentStage` 并把技术堆栈写入本地日志。项目状态本身无法写入时只记录 `preprocessing_storage_unavailable` 日志，不能伪称已成功持久化失败状态。

队列创建后通过 `app.add_event_handler("shutdown", preprocessing_jobs.shutdown)` 注册关闭；测试工厂的 `ManualQueue.shutdown()` 保持无阻塞。

- [ ] **Step 5: 实现 POST 的新建、重试与幂等语义**

```python
@app.post("/api/projects/{project_id}/local-preprocessing", response_model=Project)
def start_local_preprocessing(project_id: str, response: Response) -> Project:
    # update_project 的 transform 内完成所有状态判断与 queued 持久化
    project = update_project(project_id, prepare_for_start)
    if project.localPreprocessing.status == "completed":
        response.status_code = 200
        return project
    if not preprocessing_jobs.submit(project_id):
        mark_dispatch_failure(project_id)
        raise HTTPException(503, detail={
            "code": "preprocessing_queue_unavailable",
            "message": "本地预处理队列暂时不可用，请重试。",
        })
    response.status_code = 202
    return project
```

`prepare_for_start()` 在同一项目写锁内执行：无视频返回 `409 reference_video_required`；queued/running 返回 `409 preprocessing_in_progress`；有效 completed 原样返回；failed 通过 Task 2 验证后复用 ID、清错、重置第一个无效阶段并 queued；无结果或结果源 ID/算法版本不匹配则以 UUID 建新任务。

- [ ] **Step 6: 实现运行期替换拒绝和成功失效**

在参考视频请求中间件的项目存在检查后读取当前项目；若 `localPreprocessing.status` 为 queued/running，在解析 multipart 前返回：

```json
{
  "detail": {
    "code": "preprocessing_in_progress",
    "message": "本地预处理正在排队或运行，请完成后再更换参考视频。"
  }
}
```

扩展 `commit_project_reference_video()`：缓存旧 `localPreprocessing` 与其安全目录；新项目更新同时写入 `"localPreprocessing": None`。只有 `_write_projects()` 成功后才删除旧视频和旧预处理目录；任一清理失败只写 warning，不回滚新引用。上传、探测或元数据提交失败时两项旧引用与文件均不变。

- [ ] **Step 7: 添加重启、并发和故障恢复测试**

新增以下互相独立的重启与并发测试：

```python
def test_restart_turns_running_task_into_retryable_interruption(tmp_path):
    seed_running_project(tmp_path, completed_stages=["decoding", "sceneDetection"])
    client = TestClient(create_app(data_dir=tmp_path, preprocessing_queue_factory=ManualQueue))
    task = client.get("/api/projects/project-001").json()["localPreprocessing"]
    assert task["status"] == "failed"
    assert task["error"] == {
        "code": "preprocessing_interrupted",
        "message": "本地服务曾退出，已保留完成阶段，可从关键帧提取继续重试。",
        "stage": "keyframeExtraction",
        "retryable": True,
    }
```

再验证不同项目 FIFO、同项目并发 POST 只有一个 202、处理中替换不读取上传流、完成替换清理、失败替换保留、清理失败不回滚、算法版本变化新建 ID、缺少产物从首个无效阶段恢复。

- [ ] **Step 8: 运行后端 API 和全量回归**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_local_preprocessing_api.py \
  backend/tests/test_projects_api.py \
  backend/tests/test_reference_video_upload_api.py
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
```

Expected: 新 API、重启、并发和替换事务测试全绿；所有旧后端测试全绿。

- [ ] **Step 9: 验证检查点**

Run:

```sh
PYTHONPATH=backend .venv/bin/python -m compileall -q backend/app
```

Expected: 编译无错误；服务关闭时队列被调用 `shutdown()`，测试进程没有悬挂线程。

---

### Task 6: 前端共享类型与本地预处理 API

**Files:**
- Modify: `frontend/src/models.ts`
- Create: `frontend/src/localPreprocessingApi.ts`
- Create: `frontend/src/localPreprocessingApi.test.ts`

**Interfaces:**
- Consumes: Task 5 的完整 Project JSON、POST 启动接口和现有 `readApiError()`。
- Produces: 与后端 camelCase 完全一致的 TypeScript 类型；`startLocalPreprocessing(projectId) -> Promise<Project>` 与 `getProject(projectId) -> Promise<Project>`。Task 7 只通过这两个函数与后端交互。

- [ ] **Step 1: 写入 POST、GET、结构化错误和网络错误失败测试**

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getProject, startLocalPreprocessing } from "./localPreprocessingApi";

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("localPreprocessingApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("以无正文 POST 启动编码后的项目", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ id: "project/001" }, 202));
    await startLocalPreprocessing("project/001");
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2F001/local-preprocessing",
      { method: "POST" },
    );
  });

  it("原样返回具体后端错误", async () => {
    vi.mocked(fetch).mockResolvedValue(response({
      detail: { code: "reference_video_required", message: "请先添加并校验参考视频。" },
    }, 409));
    await expect(startLocalPreprocessing("project-001"))
      .rejects.toThrow("请先添加并校验参考视频。");
  });

  it("连接失败时给出本地服务说明", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    await expect(getProject("project-001"))
      .rejects.toThrow("无法连接本地服务，请确认应用服务正在运行后重试。");
  });
});
```

- [ ] **Step 2: 运行测试并确认模块缺失失败**

Run:

```sh
npm --prefix frontend test -- localPreprocessingApi.test.ts
```

Expected: 因模块不存在失败。

- [ ] **Step 3: 扩展共享类型**

在 `frontend/src/models.ts` 中定义与后端一一对应的联合类型：

```typescript
export type PreprocessingStageName =
  | "decoding" | "sceneDetection" | "keyframeExtraction"
  | "motionAnalysis" | "reproducibilityAssessment";

export type PreprocessingStageState = {
  name: PreprocessingStageName;
  status: "pending" | "running" | "completed" | "failed";
  startedAt: string | null;
  completedAt: string | null;
};

export type LocalPreprocessingError = {
  code: string;
  message: string;
  stage: PreprocessingStageName;
  retryable: true;
};

export type ReproducibilityCheck = {
  criterion: "single_shot" | "motion_range" | "primary_subject_count" | "complex_interaction";
  status: "passed" | "failed" | "pending" | "not_assessed";
  message: string;
  evidence: string;
};

export type LocalPreprocessing = {
  id: string;
  sourceReferenceVideoId: string;
  algorithmVersion: 1;
  status: "queued" | "running" | "completed" | "failed";
  currentStage: PreprocessingStageName | null;
  stages: PreprocessingStageState[];
  queuedAt: string;
  startedAt: string | null;
  updatedAt: string;
  completedAt: string | null;
  proxySummary: {
    keyframeCount: number;
    contactSheetCount: 1;
    sceneChangeCount: number;
    motionP50: number | null;
    motionP90: number | null;
    motionPeak: number | null;
    motionLevel: "light" | "moderate" | "high" | "unavailable";
  } | null;
  reproducibilityAssessment: {
    status: "out_of_scope" | "pending_semantic_confirmation";
    checks: ReproducibilityCheck[];
  } | null;
  error: LocalPreprocessingError | null;
};
```

把 `localPreprocessing: LocalPreprocessing | null` 加入现有 `Project`。

- [ ] **Step 4: 实现 API 函数并复用错误解析**

```typescript
import type { Project } from "./models";
import { readApiError } from "./referenceVideoApi";

async function requestProject(url: string, init?: RequestInit): Promise<Project> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。");
  }
  if (!response.ok) {
    throw new Error(await readApiError(response, "无法读取本地预处理状态，请重试。"));
  }
  return response.json() as Promise<Project>;
}

export function startLocalPreprocessing(projectId: string): Promise<Project> {
  return requestProject(
    `/api/projects/${encodeURIComponent(projectId)}/local-preprocessing`,
    { method: "POST" },
  );
}

export function getProject(projectId: string): Promise<Project> {
  return requestProject(`/api/projects/${encodeURIComponent(projectId)}`);
}
```

不要在浏览器持久化任何状态，也不要在 API 层自行合并 Project。

- [ ] **Step 5: 运行前端 API 测试和类型构建**

Run:

```sh
npm --prefix frontend test -- localPreprocessingApi.test.ts referenceVideoApi.test.ts
npm --prefix frontend run build
```

Expected: 新旧 API 测试通过，TypeScript 能发现后续尚未补齐的 Project fixtures；只在相关测试 fixture 中显式补 `localPreprocessing: null`，不使用可选字段隐藏契约变化。

- [ ] **Step 6: 验证检查点**

Run:

```sh
npm --prefix frontend test
```

Expected: 所有现有前端测试在补齐共享类型后全绿。

---

### Task 7: LocalPreprocessingPanel 状态机、轮询与无障碍

**Files:**
- Create: `frontend/src/LocalPreprocessingPanel.tsx`
- Create: `frontend/src/LocalPreprocessingPanel.test.tsx`

**Interfaces:**
- Consumes: Task 6 的 `Project`、`startLocalPreprocessing()` 和 `getProject()`。
- Produces: `LocalPreprocessingPanel({ project, onProjectUpdated, start?, load?, pollIntervalMs? })`。Task 8 在项目页挂载，并继续由 `App.updateProject()` 维护唯一项目副本。

- [ ] **Step 1: 写入未开始、手动启动和不自动执行失败测试**

使用完整 Project 工厂，避免每个测试复制长对象：

```typescript
const project = (localPreprocessing: LocalPreprocessing | null = null): Project => ({
  id: "project-001",
  name: "雨夜人像复刻",
  createdAt: "2026-09-11T10:00:00+00:00",
  updatedAt: "2026-09-11T10:00:00+00:00",
  referenceVideo: {
    id: "video-001", originalName: "clip.mp4", format: "mp4", sizeBytes: 11,
    durationSeconds: 2.5, width: 854, height: 480, frameRate: 24,
  },
  localPreprocessing,
});

it("有视频时等待用户手动开始", async () => {
  const start = vi.fn();
  render(<LocalPreprocessingPanel project={project()} onProjectUpdated={vi.fn()} start={start} />);
  expect(screen.getByText("此步骤只在本机处理，尚不会发送分析代理。"))
    .toBeVisible();
  expect(screen.getByRole("button", { name: "开始本地预处理" })).toBeVisible();
  expect(start).not.toHaveBeenCalled();
});

it("启动成功后更新项目并把焦点移到状态标题", async () => {
  const updated = project(queuedTask());
  const onProjectUpdated = vi.fn();
  render(<LocalPreprocessingPanel
    project={project()} onProjectUpdated={onProjectUpdated}
    start={vi.fn().mockResolvedValue(updated)}
  />);
  await userEvent.click(screen.getByRole("button", { name: "开始本地预处理" }));
  expect(onProjectUpdated).toHaveBeenCalledWith(updated);
  expect(screen.getByRole("heading", { name: "本地预处理正在排队" })).toHaveFocus();
});
```

无参考视频时断言没有按钮；POST 失败时断言 `role=alert` 显示具体错误且按钮保持焦点。

- [ ] **Step 2: 写入阶段、完成、超范围和失败失败测试**

覆盖固定五阶段的中文标签：解码、镜头检测、关键帧提取、运动分析、初步可复刻性判断。断言图标设置 `aria-hidden`，每项有文字状态“已完成/进行中/等待中/失败”。

完成摘要断言：

```typescript
expect(screen.getByText("8 张关键帧")).toBeVisible();
expect(screen.getByText("1 个镜头")).toBeVisible();
expect(screen.getByText("中度运动 · P90 3.200")).toBeVisible();
expect(screen.getByText("待语义分析确认")).toBeVisible();
expect(screen.getByText(/主要主体数量和复杂交互仍待确认/)).toBeVisible();
expect(screen.queryByText("处于可复刻范围")).not.toBeInTheDocument();
```

超范围断言完整失败 check 的 `message + evidence` 和“不承诺生成可靠的可执行工作流”；`motionLevel=unavailable` 显示“运动强度无法独立评估”，不显示低运动。后台失败显示 error.message、失败阶段与“从失败阶段重试”。

- [ ] **Step 3: 写入无重叠轮询、停止和刷新失败测试**

使用 `vi.useFakeTimers()`：queued/running 每 1,000ms 调一次 `load(project.id)`；上一次请求未完成时不发第二次；收到 completed/failed 后停止；切换项目或卸载后忽略旧响应并清除 timer。读取失败保留最后状态，显示“暂时无法刷新状态”和“重新读取”，不得构造后台失败。

```typescript
await act(async () => vi.advanceTimersByTimeAsync(1_000));
expect(load).toHaveBeenCalledTimes(1);
pending.resolve(project(completedTask()));
await act(async () => pending.promise);
await act(async () => vi.advanceTimersByTimeAsync(5_000));
expect(load).toHaveBeenCalledTimes(1);
```

- [ ] **Step 4: 写入 1024px、焦点和 live region 测试**

复用 Ticket 02 的 `matchMedia` 替身：1024px 有启动/重试，1023px 只读且显示桌面提示。断言运行容器使用 `role=status` 与 `aria-live=polite`；后台失败使用 `role=alert`；完成状态的可访问文本包含初步结论。

- [ ] **Step 5: 运行组件测试并确认模块缺失失败**

Run:

```sh
npm --prefix frontend test -- LocalPreprocessingPanel.test.tsx
```

Expected: 因组件不存在失败。

- [ ] **Step 6: 实现最小组件状态与轮询**

公开 Props：

```typescript
type Props = {
  project: Project;
  onProjectUpdated: (project: Project) => void;
  start?: typeof startLocalPreprocessing;
  load?: typeof getProject;
  pollIntervalMs?: number;
};
```

组件只保存 `isSubmitting`、`submitError`、`refreshError` 和请求 generation；真实任务状态始终来自 `project.localPreprocessing`。轮询使用递归 `setTimeout`，在请求完成后才安排下一次，避免重叠；cleanup 增加 generation 并清除 timer。启动和轮询成功都调用 `onProjectUpdated(updated)`，不在组件内维护第二份 Project。

状态标题文案固定：

```typescript
const stageLabels = {
  decoding: "解码",
  sceneDetection: "镜头检测",
  keyframeExtraction: "关键帧提取",
  motionAnalysis: "运动分析",
  reproducibilityAssessment: "初步可复刻性判断",
} satisfies Record<PreprocessingStageName, string>;
```

完成摘要只读取结构化字段；`evidence` 按纯文本渲染，不使用 `dangerouslySetInnerHTML`。

- [ ] **Step 7: 运行组件、API 和类型验证**

Run:

```sh
npm --prefix frontend test -- LocalPreprocessingPanel.test.tsx localPreprocessingApi.test.ts
npm --prefix frontend run build
```

Expected: 新组件全部状态、轮询停止、窄屏和无障碍测试通过；构建无类型错误。

---

### Task 8: App 整合、参考视频替换锁定与胶片检测台样式

**Files:**
- Modify: `frontend/src/App.tsx:13-207`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/ReferenceVideoPanel.tsx:17-283`
- Modify: `frontend/src/ReferenceVideoPanel.test.tsx`
- Modify: `frontend/src/styles.css:1-147`

**Interfaces:**
- Consumes: Task 7 的面板和现有 `App.updateProject()`；Task 5 返回的替换后 `localPreprocessing: null`。
- Produces: 单一项目页数据流；ReferenceVideoPanel 的 `preprocessingLocked` 和 `hasPreprocessingResult` 输入；完成可用的桌面/窄屏视觉状态。

- [ ] **Step 1: 写入 App 挂载和项目同步失败测试**

在 `App.test.tsx` 补 `localPreprocessing: null`，并加入：

```typescript
it("在参考视频下方显示本地预处理且不会自动启动", async () => {
  fetchMock.mockResolvedValueOnce(response([readyProject]));
  fetchMock.mockResolvedValueOnce(response(capabilities));
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));
  expect(screen.getByRole("heading", { name: "本地预处理" })).toBeVisible();
  expect(screen.getByRole("button", { name: "开始本地预处理" })).toBeVisible();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
```

再测试轮询返回新 Project 后当前页和首页集合都更新；切换项目后旧轮询响应不能覆盖当前项目；重新打开 completed 项目直接显示摘要且不 POST。

- [ ] **Step 2: 写入替换锁定和失效文案失败测试**

给 `ReferenceVideoPanel` 增加 Props 预期：

```typescript
preprocessingLocked?: boolean;
hasPreprocessingResult?: boolean;
```

断言 locked 时文件 input、选择按钮和 drop 都不能启动文件选择，显示“本地预处理完成后才能更换参考视频”。已有 completed/failed 结果时，替换确认包含“成功替换后，已有本地预处理结果将失效”；取消与替换失败仍保留旧摘要，替换成功只接受 API 返回的新 Project。

- [ ] **Step 3: 挂载面板并复用 App 唯一更新函数**

在项目页形成：

```tsx
<ReferenceVideoPanel
  project={selectedProject}
  onProjectUpdated={updateProject}
  upload={uploadAndMergeProject}
  preprocessingLocked={
    selectedProject.localPreprocessing?.status === "queued"
    || selectedProject.localPreprocessing?.status === "running"
  }
  hasPreprocessingResult={selectedProject.localPreprocessing !== null}
/>
<LocalPreprocessingPanel
  project={selectedProject}
  onProjectUpdated={updateProject}
/>
```

`updateProject()` 继续同时更新 selectedProject 与 projects 并按 `updatedAt` 排序。不要把轮询移入 App，也不要发起额外首页列表请求。

- [ ] **Step 4: 实现 ReferenceVideoPanel 锁定边界**

`isLocked = isUploading || preprocessingLocked`；input 与按钮使用该值禁用，drop handler 在锁定时立即返回。locked 提示是可见纯文本。替换确认文案依据 `hasPreprocessingResult` 增补影响说明，但首次上传文案不变。

当 props 中 `project.localPreprocessing` 因轮询变化而更新时，不重置当前上传错误；只有项目 ID 或 referenceVideo ID 改变才重置上传状态，避免每秒 `updatedAt` 变化破坏操作。

- [ ] **Step 5: 实现样式与 1024px 边界**

增加以下结构类，不改现有颜色变量：

```css
.local-preprocessing-panel { margin-top: 28px; padding: 28px 32px; border-top: 1px solid #c7c6bc; background: #eeece4; }
.preprocessing-stages { margin: 22px 0; padding: 0; list-style: none; border-top: 1px solid #c7c6bc; }
.preprocessing-stage { min-height: 48px; display: grid; grid-template-columns: 24px 1fr auto; align-items: center; gap: 10px; border-bottom: 1px solid #c7c6bc; }
.preprocessing-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-top: 1px solid #c7c6bc; }
.preprocessing-assessment--out-of-scope { border-left: 3px solid var(--primary-button); padding-left: 14px; }
.preprocessing-refresh-error { color: #8a2c17; }
```

使用现有蓝灰轮廓；按钮至少 44px。`@media (max-width: 1023px)` 把摘要改为单列并隐藏操作容器，但组件本身已经不挂载按钮。`prefers-reduced-motion: reduce` 下不添加动画；默认实现也不使用旋转图标。

- [ ] **Step 6: 运行组件和应用级测试**

Run:

```sh
npm --prefix frontend test -- \
  LocalPreprocessingPanel.test.tsx \
  ReferenceVideoPanel.test.tsx \
  App.test.tsx
```

Expected: 手动启动、轮询同步、替换锁定、失效说明、焦点和 1024/1023 边界全部通过。

- [ ] **Step 7: 运行前端全量与视觉静态检查**

Run:

```sh
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 全部前端测试、对比度脚本和生产构建通过；无横向溢出或 TypeScript 错误。

---

### Task 9: README、真实端到端样本、性能记录与最终验证

**Files:**
- Modify: `README.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/03-build-analysis-proxy-and-assess-reproducibility.md`
- Verify only: all Ticket 01-03 source and test files

**Interfaces:**
- Consumes: Tasks 1-8 的完整后端与前端功能。
- Produces: 可复现的运行说明、Ticket 评论记录、全量自动验证结果、真实浏览器验收和性能数据；不引入新产品行为。

- [ ] **Step 1: 更新 README 的本地预处理说明**

在现有“参考视频前置条件与数据边界”后追加：

```markdown
## 本地预处理与分析代理

参考视频上传成功后不会自动处理。在宽度至少 1024px 的项目页点击“开始本地预处理”，本地服务会依次完成解码、镜头检测、关键帧提取、运动分析和初步可复刻性判断。

当前 FFmpeg 构建必须包含 `scdet`、`scale`、`metadata`、`drawtext` 和 `tile` 滤镜。可以运行 `ffmpeg -filters` 检查。

版本化产物保存在 `<data_dir>/project-files/<project-id>/local-preprocessing/<preprocessing-id>/`。可发送给后续语义分析的分析代理只有 `contact-sheet.jpg` 和 `analysis-proxy.json`；完整参考视频、独立关键帧和本地路径不会进入分析代理。

本地阶段只判断多镜头和运动强度。显示“待语义分析确认”不代表已经处于可复刻范围；主要主体数量和复杂交互需要后续语义分析确认。
```

保留现有安装、上传限制和验证命令，不改写 Ticket 02 已确认事实。

- [ ] **Step 2: 运行后端全量验证**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
```

Expected: Ticket 01-03 全部后端测试通过；本机有 FFmpeg/ffprobe 时真实媒体测试不得跳过。记录通过、跳过和总耗时数字。

- [ ] **Step 3: 运行前端全量验证**

Run:

```sh
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 所有 Vitest 测试通过，对比度脚本通过，Vite 生产构建成功。

- [ ] **Step 4: 执行真实浏览器桌面主路径**

使用独立临时数据目录启动后端与 Vite，在 1280px 视口：

1. 新建复刻项目并上传合法单镜头常规运动 MP4，确认不自动开始。
2. 点击开始，观察 queued 与五阶段，不出现百分比。
3. 完成后核对关键帧数、一个镜头、中度或轻度运动和“待语义分析确认”。
4. 刷新、返回首页再打开，确认结果复用；再次请求接口返回 200 且不创建新版本。
5. 只用 Tab、Enter、Space 重复失败重试路径，核对可见焦点与 live region。

Expected: 项目可观察行为与设计规格一致，浏览器控制台无错误。

- [ ] **Step 5: 执行超范围、恢复与替换浏览器路径**

1. 使用硬切双镜头样本，确认显示多镜头实测原因与 Workflow 不承诺说明。
2. 使用固定高运动样本，确认显示高运动 P90 原因。
3. 让 runner 在关键帧阶段返回可控失败，确认解码和镜头阶段仍完成；恢复 runner 后从关键帧重试。
4. 运行期间确认更换入口禁用；直接调用上传接口得到 409。
5. 完成后选择替换，确认失效文案；失败替换保留旧结果，成功替换清空结果。

Expected: 不存在旧任务写入新视频、失败误删旧产物或把超范围误称为不可分析。

- [ ] **Step 6: 执行 1024px、1023px 和移动只读验收**

在 1024px 确认启动与重试可用；在 1023px 和 390px 确认状态与摘要可读，但不存在启动、重试或替换控件，无横向滚动。使用辅助功能树确认阶段、失败和结论均有完整文字，不依赖图标名称或颜色。

- [ ] **Step 7: 记录 4K 性能验收**

在 Apple Silicon M1、8GB 或不低于该性能的设备，以固定 10 秒 UHD 4K 30fps 单视频流样本运行三次，记录每次：完整解码、镜头/运动数据、关键帧/联系表、判断和总耗时。以中位数作为结果；目标总耗时不超过 30 秒。共享 CI 不加入绝对耗时断言。

- [ ] **Step 8: 检查分析代理和原视频不变性**

对真实完成目录检查：

```sh
find <temporary-data-dir>/project-files/<project-id>/local-preprocessing/<preprocessing-id> -maxdepth 2 -type f -print | sort
```

Expected: 只有设计规定的 JSON、联系表和关键帧；不存在 `.mp4`、`.mov`、低清视频、`.part` 或临时标注帧。比较处理前后原视频 SHA-256 必须相同；检查 `analysis-proxy.json` 不含项目名、原文件名、绝对路径、`api`、`key` 或供应商字段。

- [ ] **Step 9: 追加 Ticket 评论与最终检查点**

在 Ticket 03 的 `## Comments` 追加日期、设计文档路径、实施计划路径、自动验证摘要、浏览器验收摘要和 4K 性能结果；所有八项原验收框只在证据已经产生后勾选。当前项目没有“已完成”标准分流状态，实施结束后仍保留 `Status: ready-for-agent`，以全部勾选项和 Comments 验收记录表达完成，不发明新状态。

最终 Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 四条命令全部成功，Ticket 01/02 无回归，Ticket 03 的文档、自动测试、浏览器和性能证据完整。

## Execution Handoff

计划执行时有两个选项：

1. **Subagent-Driven（推荐）**：用户明确选择后，每个 Task 由 `gpt-5.6-terra / high` 实现 Agent 执行，任务间由 `gpt-5.6-sol / high` 做规格与代码质量审查。
2. **Inline Execution**：在同一会话中使用 executing-plans，按批次实施并在检查点复核，同时遵守项目规定的实现与审查模型分工。

无论采用哪一种，都必须先读取本计划与设计规格，并遵守当前“不得初始化 Git 或推送空远端”的约束；只有用户另行明确授权后才能建立首次仓库历史。
