# Ticket 02 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在桌面端为复刻项目选择或拖放一个 MP4/MOV 参考视频，由本地 FastAPI 服务完成有界上传、ffprobe 校验、原子持久化和安全替换，并在刷新后恢复明确的成功或错误状态。

**Architecture:** 浏览器通过同步 `PUT multipart/form-data` 请求上传单个文件；FastAPI 使用 `UploadFile` 的磁盘缓冲、可选 Content-Length 快速拒绝和应用数据目录内的分块硬计数，不实现自定义 multipart parser。后端把媒体领域校验、托管文件操作和项目元数据事务分成清晰模块；React 把共享类型、上传 API 和可访问的上传面板分开，并由 `App` 负责项目集合的一致更新。

**Tech Stack:** Python 3.9+、FastAPI 0.115.12、Pydantic 2、python-multipart 0.0.20、ffprobe 8.x、pytest 8.3.5、React 18.3.1、TypeScript 5.7.3、Vitest 3.0.8、Testing Library、Vite 6.1.0。

**Spec:** [`.scratch/ai-video-reverse-engineer/02-upload-reference-video-design.md`](./02-upload-reference-video-design.md)

**Requirements:** [Ticket 02](./issues/02-upload-and-validate-reference-video.md)；[产品总规格](./spec.md)

## Global Constraints

- 输入容器仅允许 MP4 或 MOV；客户端扩展名与 ffprobe 探测到的 QuickTime/ISO Base Media 容器族必须同时成立，MIME 不作为可信依据。
- 不设置 codec 白名单；ffprobe 必须确认至少一个非封面视频流和至少一个有效可读视频帧。
- 时长端点包含在内：`2.0 <= durationSeconds <= 10.0`。
- 文件硬上限为 `200_000_000` 字节；Content-Length 只用于 `201_000_000` 字节的明显超限快速拒绝，复制阶段的实际字节计数始终是最终依据。
- 分辨率按旋转元数据换算后的显示尺寸校验：短边至少 480；横屏不超过 3840×2160，竖屏不超过 2160×3840；方形任一边不得超过 2160。
- 只有宽度至少 1024px 的桌面界面提供选择、拖放和替换；1023px 及以下只读展示已有元数据或桌面操作提示。
- 合法视频原始字节复制到应用管理的数据目录；`projects.json` 只保存媒体元数据与内部 ID，不保存来源路径、临时路径或绝对托管路径。
- 替换必须先页面内确认；新文件和新项目元数据均提交成功后才能删除旧文件，任何失败均保留旧值。
- `Project.referenceVideo` 为可空字段且默认 `null`，必须继续读取 Ticket 01 已产生的无该字段项目数据。
- 错误响应使用稳定 `code` 和具体中文 `message`；存在实测值时必须写入 message，前端兼容字符串型 `detail` 与结构化 `detail`。
- 核心操作支持键盘、可见焦点、文字与图标联合传意、`role="status"`/`role="alert"`，并沿用 `DESIGN.md` 的“胶片检测台”视觉约束。
- Ticket 01 的项目创建、列表、重新打开、环境状态、损坏存储提示和并发创建测试必须持续通过。
- 不实现播放、视频预览、分析代理、关键帧、镜头检测、运动计算、分析按钮、分析服务调用或 ComfyUI 能力。
- 当前目录不是 Git 仓库；禁止初始化 Git，所有任务使用验证检查点而不是提交步骤。

## File Structure

### Create

- `backend/app/reference_video.py`：参考视频领域模型、错误类型、ffprobe 探测与全部媒体边界校验。
- `backend/app/reference_video_storage.py`：UploadFile 分块落盘、实际大小硬计数、托管路径推导、文件提升/回滚/清理。
- `backend/tests/test_video_probe.py`：探测与校验边界测试及真实 ffprobe 小样本测试。
- `backend/tests/test_reference_video_upload_api.py`：HTTP 上传、持久化、替换事务、并发和故障回滚测试。
- `frontend/src/models.ts`：`Project`、`ReferenceVideo` 与现有环境状态共享类型。
- `frontend/src/referenceVideoApi.ts`：multipart PUT 与兼容两种 detail 形状的错误解析。
- `frontend/src/referenceVideoApi.test.ts`：上传请求和错误解析测试。
- `frontend/src/ReferenceVideoPanel.tsx`：上传/替换状态机、桌面能力检测、拖放、焦点和元数据展示。
- `frontend/src/ReferenceVideoPanel.test.tsx`：面板用户行为、键盘、焦点和 1024px 边界测试。

### Modify

- `backend/app/main.py`：导入新模型，扩展 `Project`，注入 ffprobe/超时/上限，增加 Content-Length 中间件和 PUT 路由，编排项目原子替换。
- `backend/tests/test_projects_api.py`：锁定 `referenceVideo: null` 与旧 JSON 向后兼容。
- `backend/pyproject.toml`：声明 `python-multipart==0.0.20`。
- `backend/requirements.lock`：锁定 `python-multipart==0.0.20`。
- `frontend/src/App.tsx`：改用共享类型、挂载面板、上传后同步当前项目与首页集合。
- `frontend/src/App.test.tsx`：补充恢复、状态同步和窄屏只读的应用级测试，并更新 Ticket 01 初始页断言。
- `frontend/src/styles.css`：实现胶片检测台上传区、状态、确认、错误和 1024px 响应式样式。
- `README.md`：记录 ffprobe 前置条件、精确限制、托管位置和验证命令。

---

### Task 1: 参考视频领域模型与 Ticket 01 向后兼容

**Files:**
- Create: `backend/app/reference_video.py`
- Modify: `backend/app/main.py:1-55`
- Modify: `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: 现有 `Project` Pydantic 模型、`_read_projects()`、`_write_projects()` 及 Ticket 01 的 `projects.json` 列表格式。
- Produces: `ReferenceVideo`、`ReferenceVideoError`、体积/探测默认常量，以及 `Project.referenceVideo: Optional[ReferenceVideo]`；后续后端任务必须原样复用这些名称和字段。

- [x] **Step 1: 写入失败的向后兼容测试**

在 `backend/tests/test_projects_api.py` 中扩展创建测试，并新增旧 JSON 测试：

```python
def test_new_project_has_no_reference_video(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/api/projects", json={"name": "雨夜人像复刻"})

    assert response.status_code == 201
    assert response.json()["referenceVideo"] is None


def test_project_without_reference_video_field_remains_readable(tmp_path):
    (tmp_path / "projects.json").write_text(
        '[{"id":"project-001","name":"旧项目","createdAt":"2026-09-10T10:00:00+00:00","updatedAt":"2026-09-10T10:00:00+00:00"}]',
        encoding="utf-8",
    )
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/projects/project-001")

    assert response.status_code == 200
    assert response.json()["referenceVideo"] is None
```

- [x] **Step 2: 运行定向测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_projects_api.py::test_new_project_has_no_reference_video \
  backend/tests/test_projects_api.py::test_project_without_reference_video_field_remains_readable
```

Expected: 两个测试因响应缺少 `referenceVideo` 而失败；不得出现导入错误或现有存储异常。

- [x] **Step 3: 创建最小领域模型和稳定错误类型**

在 `backend/app/reference_video.py` 中实现以下公开接口：

```python
from typing import Literal

from pydantic import BaseModel, Field

MAX_REFERENCE_VIDEO_BYTES = 200_000_000
MAX_MULTIPART_BODY_BYTES = 201_000_000
DEFAULT_FFPROBE_TIMEOUT_SECONDS = 30.0


class ReferenceVideo(BaseModel):
    id: str = Field(min_length=1)
    originalName: str = Field(min_length=1)
    format: Literal["mp4", "mov"]
    sizeBytes: int = Field(gt=0)
    durationSeconds: float = Field(ge=2.0, le=10.0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frameRate: float = Field(gt=0)


class ReferenceVideoError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}
```

在 `backend/app/main.py` 中导入 `ReferenceVideo` 并将字段加入现有模型：

```python
from typing import Optional


class Project(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    createdAt: str
    updatedAt: str
    referenceVideo: Optional[ReferenceVideo] = None
```

- [x] **Step 4: 运行兼容测试并确认通过**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_projects_api.py
```

Expected: Ticket 01 的全部测试和两个新增兼容测试均通过；旧 JSON 读取后 API 显式返回 `referenceVideo: null`。

- [x] **Step 5: 验证检查点**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
```

Expected: 后端测试全绿；`projects.json` 写入包含可空字段，现有损坏数据与并发创建行为不回归。

---

### Task 2: ffprobe 探测、旋转处理与硬性媒体校验

**Files:**
- Modify: `backend/app/reference_video.py`
- Create: `backend/tests/test_video_probe.py`

**Interfaces:**
- Consumes: Task 1 的 `ReferenceVideoError` 和常量；输入为数据目录中的完整临时文件、原文件名、实测字节数、可注入的 ffprobe 路径和超时。
- Produces: `ProbedReferenceVideo`、`reference_video_format_from_name(original_name)` 与 `probe_reference_video(path, original_name, size_bytes, *, ffprobe_path, timeout_seconds) -> ProbedReferenceVideo`；Task 3 在落盘前使用文件名校验，并用探测返回值创建持久化 `ReferenceVideo`。

- [x] **Step 1: 为探测成功、容器和数值解析编写失败测试**

在 `backend/tests/test_video_probe.py` 中用 `monkeypatch` 替换 `subprocess.run`，返回真实形状的 JSON：

```python
import json
import subprocess

import pytest

from app.reference_video import probe_reference_video


def completed_probe(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )


def valid_probe_payload(
    *,
    width: int = 854,
    height: int = 480,
    duration: str = "2.500000",
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
    frames: str = "60",
    avg_frame_rate: str = "24/1",
    r_frame_rate: str = "24/1",
) -> dict:
    return {
        "format": {"format_name": format_name, "duration": duration},
        "streams": [{
            "codec_type": "video",
            "width": width,
            "height": height,
            "duration": duration,
            "avg_frame_rate": avg_frame_rate,
            "r_frame_rate": r_frame_rate,
            "nb_read_frames": frames,
            "disposition": {"attached_pic": 0},
            "side_data_list": [],
        }],
    }


def test_probe_returns_rotated_display_size_and_fractional_frame_rate(tmp_path, monkeypatch):
    path = tmp_path / "portrait.mov"
    path.write_bytes(b"video")
    payload = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.500000"},
        "streams": [{
            "codec_type": "video",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30/1",
            "nb_read_frames": "75",
            "disposition": {"attached_pic": 0},
            "side_data_list": [{"rotation": -90}],
        }],
    }
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(
        path,
        "portrait.MOV",
        5,
        ffprobe_path="ffprobe",
        timeout_seconds=30.0,
    )

    assert facts.format == "mov"
    assert (facts.width, facts.height) == (1080, 1920)
    assert facts.duration_seconds == 2.5
    assert facts.frame_rate == pytest.approx(29.97002997)
```

再参数化以下成功边界，断言返回相同的显示尺寸和时长：

```python
@pytest.mark.parametrize(
    ("width", "height", "duration"),
    [
        (854, 480, "2.000000"),
        (3840, 2160, "10.000000"),
        (2160, 3840, "2.000000"),
        (2160, 2160, "2.000000"),
    ],
)
def test_probe_accepts_inclusive_duration_and_resolution_edges(
    tmp_path, monkeypatch, width, height, duration
):
    path = tmp_path / "edge.mp4"
    path.write_bytes(b"video")
    payload = valid_probe_payload(width=width, height=height, duration=duration)
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    facts = probe_reference_video(
        path,
        "edge.mp4",
        5,
        ffprobe_path="ffprobe",
        timeout_seconds=30.0,
    )

    assert (facts.width, facts.height) == (width, height)
    assert facts.duration_seconds == float(duration)
```

`valid_probe_payload()` 必须在测试文件中返回完整字段：合法 MP4 容器、非封面视频流、`24/1` 帧率、至少 48 个读取帧。

- [x] **Step 2: 运行成功路径测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_probe.py -k 'returns_rotated or accepts_inclusive'
```

Expected: 因 `probe_reference_video` 与 `ProbedReferenceVideo` 尚未定义而失败。

- [x] **Step 3: 实现探测命令、容器识别、旋转和帧率解析**

在 `backend/app/reference_video.py` 中增加：

```python
import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class ProbedReferenceVideo:
    format: Literal["mp4", "mov"]
    duration_seconds: float
    width: int
    height: int
    frame_rate: float


def reference_video_format_from_name(original_name: str) -> Literal["mp4", "mov"]:
    extension = Path(original_name).suffix.lower()
    if extension not in {".mp4", ".mov"}:
        raise ReferenceVideoError(
            415,
            "unsupported_video_format",
            f"仅支持 MP4 或 MOV，当前文件扩展名为 {extension or '无扩展名'}。",
        )
    return "mov" if extension == ".mov" else "mp4"


def probe_reference_video(
    path: Path,
    original_name: str,
    size_bytes: int,
    *,
    ffprobe_path: str,
    timeout_seconds: float,
) -> ProbedReferenceVideo:
    expected_format = reference_video_format_from_name(original_name)
    if size_bytes > MAX_REFERENCE_VIDEO_BYTES:
        raise ReferenceVideoError(
            413,
            "video_too_large",
            f"参考视频实测为 {size_bytes:,} 字节，最大允许 200,000,000 字节。",
        )
    command = [
        ffprobe_path,
        "-v", "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        raise ReferenceVideoError(
            503,
            "ffprobe_unavailable",
            "本地未找到 ffprobe，请安装 FFmpeg 并确认 ffprobe -version 可运行。",
        ) from error
    except OSError as error:
        raise ReferenceVideoError(
            503,
            "ffprobe_unavailable",
            "本地无法启动 ffprobe，请检查 FFmpeg 安装和执行权限。",
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ReferenceVideoError(
            422,
            "video_unreadable",
            f"参考视频在 {timeout_seconds:g} 秒内未能完成读取，请检查文件是否损坏。",
        ) from error
    return _validate_probe_result(result, expected_format, size_bytes)
```

`_validate_probe_result()` 按设计顺序执行以下确定逻辑：

- 非零退出码、无 JSON、缺失必需字段、无非封面视频流、`nb_read_frames` 非正整数均抛出 `video_unreadable`。
- `format.format_name.split(",")` 必须与 `{"mov", "mp4"}` 有交集，否则抛出 `unsupported_video_format`。
- 时长优先 `format.duration`，缺失时读取目标视频流的 `duration`。
- 帧率优先 `avg_frame_rate`，`0/0`、`N/A`、零值或非有限值时回退 `r_frame_rate`；仍无效则抛出 `video_frame_rate_invalid`。
- rotation 优先 `side_data_list[*].rotation`，回退 `tags.rotate`；`abs(round(rotation)) % 180 == 90` 时交换宽高。
- `min(width, height) < 480` 抛出 `video_resolution_too_low`。
- 横屏 `(width > 3840 or height > 2160)`、竖屏 `(width > 2160 or height > 3840)`、方形任一边大于 2160 均抛出 `video_resolution_too_high`。
- 时长小于 2 抛出 `video_too_short`，大于 10 抛出 `video_too_long`；message 保留两位实测秒数。

- [x] **Step 4: 为所有拒绝原因编写失败测试**

在同一测试文件增加参数化用例，逐项断言 `status_code`、`code` 和 message 中的实测值：

```python
@pytest.mark.parametrize(
    ("filename", "payload", "expected_code", "measured"),
    [
        ("clip.avi", valid_probe_payload(), "unsupported_video_format", ".avi"),
        ("clip.mp4", valid_probe_payload(format_name="matroska,webm"), "unsupported_video_format", "matroska"),
        ("clip.mp4", valid_probe_payload(duration="1.99"), "video_too_short", "1.99"),
        ("clip.mp4", valid_probe_payload(duration="10.01"), "video_too_long", "10.01"),
        ("clip.mp4", valid_probe_payload(width=854, height=479), "video_resolution_too_low", "854×479"),
        ("clip.mp4", valid_probe_payload(width=3841, height=2160), "video_resolution_too_high", "3841×2160"),
        ("clip.mp4", valid_probe_payload(width=2160, height=3841), "video_resolution_too_high", "2160×3841"),
        ("clip.mp4", valid_probe_payload(width=2161, height=2161), "video_resolution_too_high", "2161×2161"),
        ("clip.mp4", valid_probe_payload(frames="0"), "video_unreadable", "有效视频帧"),
        ("clip.mp4", valid_probe_payload(avg_frame_rate="0/0", r_frame_rate="0/0"), "video_frame_rate_invalid", "帧率"),
    ],
)
def test_probe_rejects_each_hard_limit(
    tmp_path, monkeypatch, filename, payload, expected_code, measured
):
    path = tmp_path / "upload.part"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        "app.reference_video.subprocess.run",
        lambda *args, **kwargs: completed_probe(payload),
    )

    with pytest.raises(ReferenceVideoError) as captured:
        probe_reference_video(
            path,
            filename,
            5,
            ffprobe_path="ffprobe",
            timeout_seconds=30.0,
        )

    assert captured.value.code == expected_code
    assert measured in captured.value.message
```

另加独立测试覆盖：ffprobe 非零退出、无效 JSON、只有 attached picture、`FileNotFoundError` 为 503、`TimeoutExpired` 为 422，以及 `avg_frame_rate` 无效时正确回退 `r_frame_rate`。

- [x] **Step 5: 运行探测测试并修正最小实现至全绿**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_probe.py
```

Expected: 所有成功和拒绝边界通过；每个异常只映射到设计规定的稳定 code。

- [x] **Step 6: 验证检查点**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_probe.py backend/tests/test_projects_api.py
```

Expected: 探测模块和 Ticket 01 项目模型测试同时通过；探测函数不创建项目文件、不修改 `projects.json`。

---

### Task 3: 托管文件、上传 API 与原子替换事务

**Files:**
- Create: `backend/app/reference_video_storage.py`
- Create: `backend/tests/test_reference_video_upload_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.lock`

**Interfaces:**
- Consumes: Task 1 的 `ReferenceVideo`、`ReferenceVideoError`、体积常量和现有 `_read_projects()`/`_write_projects()`；Task 2 的 `reference_video_format_from_name()`、`probe_reference_video()` 与 `ProbedReferenceVideo`。
- Produces: `StagedReferenceVideo`、`stage_reference_video()`、`managed_reference_video_path()`、`promote_staged_reference_video()`、`discard_managed_file()`，以及 `PUT /api/projects/{project_id}/reference-video`；前端依赖完整 Project 响应与结构化错误体。

- [x] **Step 1: 锁定 multipart 依赖并安装当前环境**

在 `backend/pyproject.toml` 的 dependencies 和 `backend/requirements.lock` 中都加入：

```text
python-multipart==0.0.20
```

Run:

```sh
.venv/bin/pip install python-multipart==0.0.20
```

Expected: 安装成功，且 `.venv/bin/python -c "import multipart; print(multipart.__version__)"` 输出 `0.0.20`。

- [x] **Step 2: 为分块硬计数和临时文件清理编写失败测试**

在 `backend/tests/test_reference_video_upload_api.py` 中先直接测试存储边界：

```python
import asyncio
from io import BytesIO

import pytest
from fastapi import UploadFile

from app.reference_video import ReferenceVideoError
from app.reference_video_storage import stage_reference_video


def test_staging_accepts_exact_byte_limit(tmp_path):
    upload = UploadFile(filename="edge.mp4", file=BytesIO(b"12345678"))

    staged = asyncio.run(
        stage_reference_video(upload, tmp_path, max_bytes=8, chunk_bytes=3)
    )

    assert staged.size_bytes == 8
    assert staged.path.read_bytes() == b"12345678"


def test_staging_rejects_one_byte_over_and_removes_partial_file(tmp_path):
    upload = UploadFile(filename="large.mp4", file=BytesIO(b"123456789"))

    with pytest.raises(ReferenceVideoError) as captured:
        asyncio.run(
            stage_reference_video(upload, tmp_path, max_bytes=8, chunk_bytes=3)
        )

    assert captured.value.code == "video_too_large"
    assert "9" in captured.value.message
    assert list(tmp_path.glob(".reference-*.part")) == []
```

- [x] **Step 3: 运行存储测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_reference_video_upload_api.py -k staging
```

Expected: 因 `reference_video_storage` 尚不存在而失败。

- [x] **Step 4: 实现分块落盘与托管路径函数**

在 `backend/app/reference_video_storage.py` 中实现以下接口：

```python
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from .reference_video import ReferenceVideo, ReferenceVideoError


@dataclass(frozen=True)
class StagedReferenceVideo:
    path: Path
    size_bytes: int


async def stage_reference_video(
    upload: UploadFile,
    data_dir: Path,
    *,
    max_bytes: int,
    chunk_bytes: int = 1024 * 1024,
) -> StagedReferenceVideo:
    data_dir.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        dir=data_dir,
        prefix=".reference-",
        suffix=".part",
    )
    path = Path(raw_path)
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while True:
                chunk = await upload.read(chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ReferenceVideoError(
                        413,
                        "video_too_large",
                        f"参考视频实测为 {total:,} 字节，最大允许 {max_bytes:,} 字节。",
                    )
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        return StagedReferenceVideo(path=path, size_bytes=total)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def managed_reference_video_path(
    data_dir: Path,
    project_id: str,
    video: ReferenceVideo,
) -> Path:
    return data_dir / "project-files" / project_id / "reference-videos" / f"{video.id}.{video.format}"


def promote_staged_reference_video(staged_path: Path, final_path: Path) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_path, final_path)


def discard_managed_file(path: Optional[Path]) -> None:
    if path is not None:
        path.unlink(missing_ok=True)
```

硬计数错误 message 中的实测值是首次越界时已经读取的实际字节数；最终业务上限仍由注入的 `max_bytes` 决定，生产值固定为 `200_000_000`。

- [x] **Step 5: 为上传成功、恢复和错误契约编写失败 API 测试**

在测试文件增加可控探测替身与应用工厂：

```python
from fastapi.testclient import TestClient

from app import main
from app.main import create_app
from app.reference_video import ProbedReferenceVideo


def valid_probe(path, original_name, size_bytes, *, ffprobe_path, timeout_seconds):
    assert path.exists()
    detected_format = "mov" if original_name.lower().endswith(".mov") else "mp4"
    return ProbedReferenceVideo(
        format=detected_format,
        duration_seconds=2.5,
        width=854,
        height=480,
        frame_rate=24.0,
    )


def test_user_uploads_video_and_reopens_project_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    first = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=16))
    project = first.post("/api/projects", json={"name": "上传测试"}).json()

    uploaded = first.put(
        f"/api/projects/{project['id']}/reference-video",
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )

    assert uploaded.status_code == 200
    reference = uploaded.json()["referenceVideo"]
    assert reference["originalName"] == "clip.mp4"
    assert reference["sizeBytes"] == 11
    assert reference["durationSeconds"] == 2.5
    assert uploaded.json()["updatedAt"] != project["updatedAt"]

    restarted = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=16))
    reopened = restarted.get(f"/api/projects/{project['id']}")
    assert reopened.json()["referenceVideo"] == reference
    stored = tmp_path / "project-files" / project["id"] / "reference-videos" / f"{reference['id']}.mp4"
    assert stored.read_bytes() == b"video-bytes"
```

为结构化错误增加断言：

```python
def assert_video_error(response, status, code, message_fragment):
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert message_fragment in response.json()["detail"]["message"]


def test_upload_rejects_actual_bytes_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(
        create_app(
            data_dir=tmp_path,
            max_reference_video_bytes=8,
            max_multipart_body_bytes=1_000_000,
        )
    )
    project = client.post("/api/projects", json={"name": "体积边界"}).json()

    response = client.put(
        f"/api/projects/{project['id']}/reference-video",
        files={"file": ("clip.mp4", b"123456789", "video/mp4")},
    )

    assert_video_error(response, 413, "video_too_large", "9")
    assert client.get(f"/api/projects/{project['id']}").json()["referenceVideo"] is None
```

再增加：不存在项目 404、缺文件 400、明显超 Content-Length 的中间件快速 413、伪造较小 Content-Length 仍被硬计数拒绝、探测错误原样映射、运行时 ffprobe 路径缺失返回 503。

- [x] **Step 6: 运行 API 测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_reference_video_upload_api.py -k 'uploads_video or actual_bytes or project_not_found or content_length'
```

Expected: 存储函数测试通过；HTTP 用例因 PUT 路由尚不存在而返回 404 或因应用参数尚未定义而失败。

- [x] **Step 7: 实现可注入应用参数、快速拒绝和 PUT 路由**

将 `create_app` 签名扩展为：

```python
def create_app(
    data_dir: Path,
    *,
    ffprobe_path: str = "ffprobe",
    ffprobe_timeout_seconds: float = DEFAULT_FFPROBE_TIMEOUT_SECONDS,
    max_reference_video_bytes: int = MAX_REFERENCE_VIDEO_BYTES,
    max_multipart_body_bytes: int = MAX_MULTIPART_BODY_BYTES,
) -> FastAPI:
```

在 `create_app` 内增加路径限定中间件。只匹配正则 `^/api/projects/[^/]+/reference-video$` 的 PUT 请求，在调用下游前解析 Content-Length；非整数或负数返回 `invalid_multipart`，大于注入阈值返回：

```python
JSONResponse(
    status_code=413,
    content={
        "detail": {
            "code": "video_too_large",
            "message": "上传请求体明显超过 200,000,000 字节文件上限，请选择更小的参考视频。",
        }
    },
)
```

PUT 路由使用 FastAPI `UploadFile`，不读取 `request.stream()`，不创建 multipart parser：

```python
@app.put("/api/projects/{project_id}/reference-video", response_model=Project)
async def put_reference_video(
    project_id: str,
    file: Optional[UploadFile] = File(default=None),
) -> Project:
    if file is None or not file.filename:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_multipart", "message": "请选择一个参考视频文件。"},
        )
    staged = None
    try:
        reference_video_format_from_name(file.filename)
        with project_write_lock:
            existing_projects = _read_projects(data_dir)
            if not any(project.id == project_id for project in existing_projects):
                raise HTTPException(
                    status_code=404,
                    detail={"code": "project_not_found", "message": "复刻项目不存在。"},
                )
        staged = await stage_reference_video(
            file,
            data_dir,
            max_bytes=max_reference_video_bytes,
        )
        facts = probe_reference_video(
            staged.path,
            file.filename,
            staged.size_bytes,
            ffprobe_path=ffprobe_path,
            timeout_seconds=ffprobe_timeout_seconds,
        )
        reference = ReferenceVideo(
            id=str(uuid4()),
            originalName=Path(file.filename).name,
            format=facts.format,
            sizeBytes=staged.size_bytes,
            durationSeconds=facts.duration_seconds,
            width=facts.width,
            height=facts.height,
            frameRate=facts.frame_rate,
        )
        return commit_project_reference_video(project_id, staged.path, reference)
    except ReferenceVideoError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail(),
        ) from error
    except OSError as error:
        message = (
            CORRUPT_PROJECT_DATA_MESSAGE
            if isinstance(error, CorruptProjectDataError)
            else STORAGE_UNAVAILABLE_MESSAGE
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "storage_unavailable", "message": message},
        ) from error
    finally:
        if staged is not None:
            staged.path.unlink(missing_ok=True)
        await file.close()
```

`commit_project_reference_video()` 留在 `main.py`，因为它需要现有项目锁和 JSON 函数；其精确签名为：

```python
def commit_project_reference_video(
    project_id: str,
    staged_path: Path,
    reference: ReferenceVideo,
) -> Project:
```

函数在 `project_write_lock` 内重新读取项目、查找索引、记录旧引用、提升唯一新文件、构造 `model_copy(update={"referenceVideo": reference, "updatedAt": now})` 并调用 `_write_projects()`。若 JSON 写入抛出 `OSError`，立即删除新文件并继续抛出，由 PUT 路由转换为结构化 `storage_unavailable` 503；JSON 成功后才删除旧引用推导出的文件。旧文件删除失败只写 `logging.getLogger(__name__).warning(...)`，不得回滚有效新引用。

首次检查项目是否存在应在复制前执行以避免无意义写入；锁内必须再次检查以保证事务时点正确。文件名只通过 `Path(file.filename).name` 进入展示字段，托管路径只使用服务端生成 ID。

- [x] **Step 8: 为替换事务、并发和回滚编写失败测试**

增加以下可观察行为测试：

```python
def test_failed_replacement_keeps_previous_reference_and_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "probe_reference_video", valid_probe)
    client = TestClient(create_app(data_dir=tmp_path, max_reference_video_bytes=32))
    project = client.post("/api/projects", json={"name": "替换测试"}).json()
    first = client.put(
        f"/api/projects/{project['id']}/reference-video",
        files={"file": ("clip.mp4", b"old-video", "video/mp4")},
    ).json()
    old_reference = first["referenceVideo"]
    old_path = tmp_path / "project-files" / project["id"] / "reference-videos" / f"{old_reference['id']}.mp4"

    def invalid_probe(path, original_name, size_bytes, *, ffprobe_path, timeout_seconds):
        raise ReferenceVideoError(422, "video_unreadable", "参考视频无法读取有效视频帧。")

    monkeypatch.setattr(main, "probe_reference_video", invalid_probe)
    failed = client.put(
        f"/api/projects/{project['id']}/reference-video",
        files={"file": ("broken.mp4", b"broken", "video/mp4")},
    )

    assert_video_error(failed, 422, "video_unreadable", "有效视频帧")
    assert client.get(f"/api/projects/{project['id']}").json()["referenceVideo"] == old_reference
    assert old_path.read_bytes() == b"old-video"
```

另用 `monkeypatch` 让第二次 `_write_projects()` 抛出 `OSError`，断言旧引用/旧字节仍存在且新最终文件被删除；成功替换断言新字节存在且旧路径已删除。并发测试使用 `ThreadPoolExecutor(max_workers=2)` 同时替换同一项目，断言最终 JSON 引用的唯一文件存在、其字节等于两个请求之一、项目目录不存在被 JSON 引用但缺失的文件。不同项目替换测试断言互不影响。

- [x] **Step 9: 运行后端上传与回归测试**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_reference_video_upload_api.py \
  backend/tests/test_video_probe.py \
  backend/tests/test_projects_api.py
```

Expected: 上传、硬计数、恢复、替换、回滚、并发和 Ticket 01 测试全部通过；测试临时目录中没有 `.part` 残留。

- [x] **Step 10: 验证检查点**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
```

Expected: 后端测试全绿；路由只接受 PUT，错误体符合 `{detail:{code,message}}`，不出现后台任务或自制 multipart 解析代码。

---

### Task 4: 前端共享模型、上传 API 与错误解析

**Files:**
- Create: `frontend/src/models.ts`
- Create: `frontend/src/referenceVideoApi.ts`
- Create: `frontend/src/referenceVideoApi.test.ts`
- Modify: `frontend/src/App.tsx:15-68`

**Interfaces:**
- Consumes: Task 3 的完整 Project JSON、`PUT /api/projects/{project_id}/reference-video`、字符串或 `{code,message}` 两种 `detail`。
- Produces: `ReferenceVideo`、`Project`、`Capability`、`Capabilities` TypeScript 类型，`readApiError(response, fallback) -> Promise<string>`，`uploadReferenceVideo(projectId, file) -> Promise<Project>`；Task 5/6 原样使用。

- [x] **Step 1: 为 multipart 请求和两种错误体编写失败测试**

创建 `frontend/src/referenceVideoApi.test.ts`：

```typescript
import { beforeEach, expect, it, vi } from "vitest";

import { readApiError, uploadReferenceVideo } from "./referenceVideoApi";

const project = {
  id: "project-001",
  name: "雨夜人像复刻",
  createdAt: "2026-09-10T10:00:00+00:00",
  updatedAt: "2026-09-11T10:00:00+00:00",
  referenceVideo: {
    id: "video-001",
    originalName: "clip.mp4",
    format: "mp4" as const,
    sizeBytes: 11,
    durationSeconds: 2.5,
    width: 854,
    height: 480,
    frameRate: 24,
  },
};

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

it("uploads one file with PUT multipart and returns the updated project", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(project), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  const file = new File(["video-bytes"], "clip.mp4", { type: "video/mp4" });

  await expect(uploadReferenceVideo("project-001", file)).resolves.toEqual(project);

  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe("/api/projects/project-001/reference-video");
  expect(init?.method).toBe("PUT");
  expect(init?.body).toBeInstanceOf(FormData);
  expect((init?.body as FormData).get("file")).toBe(file);
  expect(init?.headers).toBeUndefined();
});

it("reads structured and legacy string error details", async () => {
  const structured = new Response(JSON.stringify({
    detail: { code: "video_too_short", message: "参考视频时长为 1.42 秒，至少需要 2 秒。" },
  }), { status: 422, headers: { "Content-Type": "application/json" } });
  const legacy = new Response(JSON.stringify({ detail: "本地项目存储不可用" }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });

  await expect(readApiError(structured, "无法上传")).resolves.toBe("参考视频时长为 1.42 秒，至少需要 2 秒。");
  await expect(readApiError(legacy, "无法上传")).resolves.toBe("本地项目存储不可用");
});
```

再测试无 JSON、结构化 detail 缺少字符串 message 时返回传入的具体兜底文案。

- [x] **Step 2: 运行 API 测试并确认预期失败**

Run:

```sh
npm --prefix frontend test -- src/referenceVideoApi.test.ts
```

Expected: 因 `models.ts` 和 `referenceVideoApi.ts` 尚不存在而失败。

- [x] **Step 3: 实现共享模型与上传 API**

`frontend/src/models.ts` 使用与后端完全一致的字段：

```typescript
export type ReferenceVideo = {
  id: string;
  originalName: string;
  format: "mp4" | "mov";
  sizeBytes: number;
  durationSeconds: number;
  width: number;
  height: number;
  frameRate: number;
};

export type Project = {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  referenceVideo: ReferenceVideo | null;
};

export type Capability = {
  state: "checking" | "unavailable" | "unconfigured" | "disconnected";
  label: string;
};

export type Capabilities = {
  analysisService: Capability;
  localComfyui: Capability;
};
```

`frontend/src/referenceVideoApi.ts` 实现：

```typescript
import type { Project } from "./models";

type ErrorBody = {
  detail?: string | { code?: unknown; message?: unknown };
};

export async function readApiError(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null) as ErrorBody | null;
  if (typeof body?.detail === "string") return body.detail;
  if (
    body?.detail &&
    typeof body.detail === "object" &&
    typeof body.detail.message === "string"
  ) {
    return body.detail.message;
  }
  return fallback;
}

export async function uploadReferenceVideo(projectId: string, file: File): Promise<Project> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/reference-video`, {
    method: "PUT",
    body: form,
  });
  if (!response.ok) {
    throw new Error(await readApiError(response, "无法上传并校验参考视频，请检查文件后重试。"));
  }
  return response.json() as Promise<Project>;
}
```

不要手动设置 multipart `Content-Type`，让浏览器生成 boundary。

- [x] **Step 4: 改造 App 使用共享类型且保持现有 API 行为**

从 `App.tsx` 删除本地 `Project`、`Capability`、`Capabilities` 声明，改为：

```typescript
import type { Capabilities, Capability, Project } from "./models";
```

现有项目列表、创建和环境状态 API 的请求/错误行为保持不变；新建项目响应按后端契约包含 `referenceVideo: null`。

- [x] **Step 5: 运行前端 API 与 Ticket 01 测试**

Run:

```sh
npm --prefix frontend test -- src/referenceVideoApi.test.ts src/App.test.tsx
```

Expected: 新 API 测试和现有 App 测试全绿；TypeScript 类型字段与后端 camelCase JSON 完全一致。

- [x] **Step 6: 验证检查点**

Run:

```sh
npm --prefix frontend run build
```

Expected: `tsc -b` 与 Vite 构建成功；未引入第三方上传库或客户端媒体解析库。

---

### Task 5: ReferenceVideoPanel 上传、拖放与替换状态机

**Files:**
- Create: `frontend/src/ReferenceVideoPanel.tsx`
- Create: `frontend/src/ReferenceVideoPanel.test.tsx`
- Read before implementation: `DESIGN.md`
- Read before implementation: 当前环境中 Impeccable 的 `craft-floor` 指令

**Interfaces:**
- Consumes: Task 4 的 `Project`、`ReferenceVideo`、`uploadReferenceVideo(projectId, file) -> Promise<Project>`；设计中的 `empty | uploading | ready | error | confirmingReplacement | replacementError` 状态。
- Produces: `ReferenceVideoPanel({ project, onProjectUpdated, upload })`；Task 6 在项目页挂载并用更新后的完整 Project 同步应用状态。

- [x] **Step 1: 读取视觉实施约束，不改代码**

完整读取 `DESIGN.md` 和当前环境提供的 Impeccable `craft-floor` 指令。实施记录必须明确遵循：暖灰台面、细线分区、稀疏锈红、方正容器、无阴影、44px 操作目标、蓝灰可见焦点、状态不只靠颜色。此步骤不得运行 Impeccable detector；detector 保留到 Task 7 且全程只运行一次。

- [x] **Step 2: 为桌面空状态、限制前置和首次上传编写失败测试**

创建 `frontend/src/ReferenceVideoPanel.test.tsx`，先提供稳定 matchMedia 替身：

```typescript
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { Project, ReferenceVideo } from "./models";
import { ReferenceVideoPanel } from "./ReferenceVideoPanel";


function stubDesktop(matches = true) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

const emptyProject: Project = {
  id: "project-001",
  name: "雨夜人像复刻",
  createdAt: "2026-09-10T10:00:00+00:00",
  updatedAt: "2026-09-10T10:00:00+00:00",
  referenceVideo: null,
};

const referenceVideo: ReferenceVideo = {
  id: "video-001",
  originalName: "clip.mp4",
  format: "mp4",
  sizeBytes: 11,
  durationSeconds: 2.5,
  width: 854,
  height: 480,
  frameRate: 24,
};

it("shows every hard limit before selecting and uploads the chosen file", async () => {
  stubDesktop();
  const updated = { ...emptyProject, updatedAt: "2026-09-11T10:00:00+00:00", referenceVideo };
  const upload = vi.fn().mockResolvedValue(updated);
  const onProjectUpdated = vi.fn();
  render(<ReferenceVideoPanel project={emptyProject} onProjectUpdated={onProjectUpdated} upload={upload} />);

  expect(screen.getByText(/MP4 或 MOV/)).toBeVisible();
  expect(screen.getByText(/2～10 秒/)).toBeVisible();
  expect(screen.getByText(/200 MB/)).toBeVisible();
  expect(screen.getByText(/最低 480P/)).toBeVisible();
  expect(screen.getByText(/最高 UHD 4K/)).toBeVisible();

  const file = new File(["video"], "clip.mp4", { type: "video/mp4" });
  await userEvent.upload(screen.getByLabelText("参考视频文件"), file);

  expect(upload).toHaveBeenCalledWith("project-001", file);
  expect(await screen.findByRole("status")).toHaveTextContent("参考视频已通过校验");
  expect(onProjectUpdated).toHaveBeenCalledWith(updated);
});
```

用 deferred Promise 增加上传中测试，断言状态文字为“正在上传并校验参考视频…”，文件选择和 drop 不会发起第二个请求。

- [x] **Step 3: 运行首次上传测试并确认预期失败**

Run:

```sh
npm --prefix frontend test -- src/ReferenceVideoPanel.test.tsx -t 'shows every hard limit|uploading'
```

Expected: 因组件尚不存在而失败。

- [x] **Step 4: 实现组件公开签名、桌面检测和首次上传状态**

组件签名和状态联合类型必须为：

```typescript
type UploadOperation =
  | { kind: "idle" }
  | { kind: "uploading"; replacing: boolean }
  | { kind: "error"; message: string }
  | { kind: "confirmingReplacement"; file: File }
  | { kind: "replacementError"; message: string };

type Props = {
  project: Project;
  onProjectUpdated: (project: Project) => void;
  upload?: typeof uploadReferenceVideo;
};

export function ReferenceVideoPanel({
  project,
  onProjectUpdated,
  upload = uploadReferenceVideo,
}: Props) {
```

实现 `useDesktopUpload()`，初始值和 change 监听都使用：

```typescript
const DESKTOP_UPLOAD_QUERY = "(min-width: 1024px)";
```

1023px 及以下不挂载 input、选择按钮或 drop handlers。桌面文件 input 使用：

```tsx
<input
  ref={fileInputRef}
  className="visually-hidden"
  id="reference-video-file"
  type="file"
  accept=".mp4,.mov,video/mp4,video/quicktime"
  aria-label="参考视频文件"
  onChange={(event) => {
    const selected = event.currentTarget.files?.[0];
    if (selected) selectFile(selected);
    event.currentTarget.value = "";
  }}
/>
```

没有旧视频时 `selectFile(file)` 立即调用 `performUpload(file, false)`；请求期间状态为 `uploading`，界面只显示统一的上传并校验文案，不显示虚构进度百分比。成功后先调用 `onProjectUpdated(updatedProject)`，再通过状态 live region 宣告成功。

- [x] **Step 5: 为拖放、元数据和具体错误编写失败测试**

增加测试：

```typescript
it("accepts a dropped file and shows returned metadata", async () => {
  stubDesktop();
  const updated = { ...emptyProject, referenceVideo };
  const upload = vi.fn().mockResolvedValue(updated);
  render(<ReferenceVideoPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);
  const file = new File(["video"], "clip.mov", { type: "video/quicktime" });

  fireEvent.drop(screen.getByTestId("reference-video-dropzone"), {
    dataTransfer: { files: [file] },
  });

  expect(upload).toHaveBeenCalledWith("project-001", file);
  expect(await screen.findByText("854×480")).toBeVisible();
  expect(screen.getByText("2.50 秒")).toBeVisible();
  expect(screen.getByText("24.00 fps")).toBeVisible();
});

it("shows the server message and restores focus after a first upload failure", async () => {
  stubDesktop();
  const upload = vi.fn().mockRejectedValue(new Error("参考视频时长为 1.42 秒，至少需要 2 秒。"));
  render(<ReferenceVideoPanel project={emptyProject} onProjectUpdated={vi.fn()} upload={upload} />);
  const file = new File(["short"], "short.mp4", { type: "video/mp4" });

  await userEvent.upload(screen.getByLabelText("参考视频文件"), file);

  expect(await screen.findByRole("alert")).toHaveTextContent("参考视频时长为 1.42 秒，至少需要 2 秒。");
  expect(screen.getByRole("button", { name: "选择参考视频" })).toHaveFocus();
});
```

元数据格式固定为：格式大写、体积使用中文可读单位并保留原始字节 title、时长两位小数、`width×height`、帧率两位小数。

- [x] **Step 6: 为替换确认、失败保旧值和焦点恢复编写失败测试**

使用带 `referenceVideo` 的项目覆盖：

```typescript
it("requires inline confirmation and keeps the old value when replacement fails", async () => {
  stubDesktop();
  const readyProject = { ...emptyProject, referenceVideo };
  const upload = vi.fn().mockRejectedValue(new Error("参考视频无法读取有效视频帧。"));
  render(<ReferenceVideoPanel project={readyProject} onProjectUpdated={vi.fn()} upload={upload} />);
  const replacement = new File(["broken"], "broken.mov", { type: "video/quicktime" });

  await userEvent.upload(screen.getByLabelText("参考视频文件"), replacement);

  expect(screen.getByText(/broken.mov/)).toBeVisible();
  expect(upload).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "替换参考视频" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("参考视频无法读取有效视频帧。");
  expect(screen.getByText("clip.mp4")).toBeVisible();
  expect(screen.getByRole("button", { name: "更换参考视频" })).toHaveFocus();
});
```

另测“取消”不调用 upload、清除待选文件并恢复焦点；成功替换只在 Promise resolve 后调用 `onProjectUpdated`；拖放新文件也必须进入同一确认状态。

- [x] **Step 7: 完成状态机、焦点和无障碍语义**

实现以下确定行为：

- `project.referenceVideo === null` 且 idle 时渲染空状态；非空时渲染 ready 摘要。
- `confirmingReplacement` 同时显示旧文件名和待选文件名，按钮仅为“取消”和“替换参考视频”。
- 失败状态使用 `role="alert"`；请求和成功宣告使用 `role="status" aria-live="polite"`。
- 成功摘要标题设置 `tabIndex={-1}` 和 ref；成功后在 effect 中聚焦。
- 首次失败聚焦“选择参考视频”；取消或替换失败聚焦“更换参考视频”。
- dropzone 仅处理 `dragover`、`dragleave`、`drop`，并始终保留原生按钮作为键盘等价入口。
- 窄屏只读文案不包含可触发上传的元素。

- [x] **Step 8: 运行组件测试并确认通过**

Run:

```sh
npm --prefix frontend test -- src/ReferenceVideoPanel.test.tsx
```

Expected: 限制前置、选择、拖放、上传中、成功、错误、替换确认、失败保旧值、焦点和 1024px 能力边界全部通过。

- [x] **Step 9: 验证检查点**

Run:

```sh
npm --prefix frontend test -- src/referenceVideoApi.test.ts src/ReferenceVideoPanel.test.tsx
npm --prefix frontend run build
```

Expected: 新前端模块测试全绿且 TypeScript 构建通过；此时仍不运行 Impeccable detector。

---

### Task 6: App 项目页整合、胶片检测台样式与响应式行为

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/ReferenceVideoPanel.test.tsx`

**Interfaces:**
- Consumes: Task 4 的共享 Project 类型；Task 5 的 `ReferenceVideoPanel` 和 `onProjectUpdated(project)` 回调。
- Produces: 项目页完整上传入口、上传后当前项目/首页集合同步、1024px 桌面与窄屏只读布局；Task 7 对该完整 UI 只运行一次 detector。

- [x] **Step 1: 为项目恢复和 App 状态同步编写失败测试**

在 `frontend/src/App.test.tsx` 增加带参考视频的列表响应：

```typescript
it("reopens a project with its persisted reference video", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValueOnce(response([{
    id: "project-001",
    name: "雨夜人像复刻",
    createdAt: "2026-09-10T10:00:00+00:00",
    updatedAt: "2026-09-11T10:00:00+00:00",
    referenceVideo: {
      id: "video-001",
      originalName: "clip.mp4",
      format: "mp4",
      sizeBytes: 11,
      durationSeconds: 2.5,
      width: 854,
      height: 480,
      frameRate: 24,
    },
  }]));
  fetchMock.mockResolvedValueOnce(response(capabilities));

  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /雨夜人像复刻/ }));

  expect(screen.getByText("clip.mp4")).toBeVisible();
  expect(screen.getByText("854×480")).toBeVisible();
});
```

再新增上传返回更新 Project 后点击“返回项目首页”、重开同一项目的测试，断言新 `updatedAt` 排序与新 `referenceVideo` 都保留。所有项目/创建 mock 响应补上 `referenceVideo: null` 以匹配公开类型。

- [x] **Step 2: 运行 App 定向测试并确认预期失败**

Run:

```sh
npm --prefix frontend test -- src/App.test.tsx -t 'persisted reference video|updated project'
```

Expected: 项目页尚未挂载 `ReferenceVideoPanel`，元数据断言失败。

- [x] **Step 3: 在 App 挂载面板并同步两个项目状态源**

在 `App` 中实现：

```typescript
function updateProject(updated: Project) {
  setSelectedProject(updated);
  setProjects((current) => current
    .map((project) => project.id === updated.id ? updated : project)
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt)));
}
```

在项目页标题之后以以下接口挂载：

```tsx
<ReferenceVideoPanel
  project={selectedProject}
  onProjectUpdated={updateProject}
/>
```

用新面板替代原有“复刻项目已创建，可以继续添加参考视频”初始占位。保留项目标题、创建时间、返回首页和两个环境状态；分析服务和 ComfyUI 状态不得阻止上传。

- [x] **Step 4: 为 1024px 桌面和 1023px 只读编写失败测试**

在组件测试中让 matchMedia 分别返回 true/false，断言：

```typescript
expect(screen.queryByRole("button", { name: "选择参考视频" })).not.toBeInTheDocument();
expect(screen.queryByLabelText("参考视频文件")).not.toBeInTheDocument();
expect(screen.getByText("请在宽度至少 1024px 的桌面设备添加参考视频")).toBeVisible();
```

窄屏已有视频时断言元数据仍可见，但“更换参考视频”和 input 均不存在。桌面恰好 1024px 时断言完整控件存在。

- [x] **Step 5: 实现 DESIGN.md 约束下的样式**

在 `frontend/src/styles.css` 增加明确的样式单元，不改无关首页规则：

```css
.reference-video-panel {
  margin-top: 48px;
  border-top: 3px solid var(--primary-button);
  border-bottom: 1px solid #c7c6bc;
  background: #e9e7de;
}

.reference-video-dropzone {
  min-height: 190px;
  padding: 28px 32px;
  border: 1px dashed #8f938c;
  background: transparent;
}

.reference-video-dropzone.is-dragging {
  border-color: var(--primary-button);
  border-style: solid;
}

.reference-video-metadata {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border-top: 1px solid #c7c6bc;
}

.reference-video-metadata dt {
  font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: .08em;
  color: var(--metadata-text);
}

.reference-video-error {
  border-left: 3px solid var(--primary-button);
  color: #8a2c17;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 1023px) {
  .reference-video-metadata {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
```

按钮复用现有 primary/secondary action；确认区只能有一个锈红主操作。为摘要聚焦添加蓝灰 outline，保持无阴影、极小圆角和至少 44px 操作高度。已有 720px 布局继续负责手机间距，1024px 媒体查询只控制能力展示和元数据重排。

- [x] **Step 6: 运行 App、组件、对比度与构建检查**

Run:

```sh
npm --prefix frontend test -- src/App.test.tsx src/ReferenceVideoPanel.test.tsx src/referenceVideoApi.test.ts
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 所有前端测试通过；对比度脚本通过；构建无类型错误；1023px 测试中不存在交互上传控件。

- [x] **Step 7: 验证检查点**

人工审阅 `App.tsx` 和 `styles.css` 的 diff，确认每一行都可追溯到 Ticket 02，未引入播放器、分析按钮、进度百分比、卡片阴影、大圆角或额外动画。此检查点仍不运行 detector。

---

### Task 7: README、真实 ffprobe、全量验证与一次性视觉终审

**Files:**
- Modify: `README.md`
- Modify if the single review batch finds defects: `frontend/src/ReferenceVideoPanel.tsx`
- Modify if the single review batch finds defects: `frontend/src/App.tsx`
- Modify if the single review batch finds defects: `frontend/src/styles.css`
- Modify if behavior changes in that batch: `frontend/src/ReferenceVideoPanel.test.tsx`
- Modify if behavior changes in that batch: `frontend/src/App.test.tsx`
- Test: `backend/tests/test_video_probe.py`
- Test: `backend/tests/test_reference_video_upload_api.py`

**Interfaces:**
- Consumes: Tasks 1-6 的完整上传流程、README 现有启动命令、系统 ffprobe、Impeccable detector 和真实浏览器。
- Produces: 可复现安装说明、真实 ffprobe 证据、完整自动化结果、桌面/窄屏浏览器证据，以及 detector 后唯一一批修复的最终 UI。

- [x] **Step 1: 增加真实 ffprobe 集成测试**

在 `backend/tests/test_video_probe.py` 增加由 ffmpeg 创建极小确定性样本的测试；系统缺少 `ffmpeg` 或 `ffprobe` 时只跳过该集成测试，不能改变运行时 503 行为：

```python
import shutil


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="真实媒体集成测试需要 ffmpeg 和 ffprobe",
)
def test_real_ffprobe_accepts_a_two_second_480p_mp4(tmp_path):
    sample = tmp_path / "valid-2s-480p.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x343938:s=854x480:r=24",
            "-t", "2",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(sample),
        ],
        check=True,
        capture_output=True,
    )

    facts = probe_reference_video(
        sample,
        sample.name,
        sample.stat().st_size,
        ffprobe_path="ffprobe",
        timeout_seconds=30.0,
    )

    assert facts.format == "mp4"
    assert facts.duration_seconds == pytest.approx(2.0, abs=0.01)
    assert (facts.width, facts.height) == (854, 480)
    assert facts.frame_rate == pytest.approx(24.0)
```

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_probe.py -k real_ffprobe
```

Expected on the current machine: PASS with ffprobe 8.1.1；缺少二进制的其他环境显示单个明确 skip。

- [x] **Step 2: 更新安装、数据边界和限制文档**

在 `README.md` 增加以下事实：

- 启动前运行 `ffprobe -version`；缺失时安装 FFmpeg。
- 参考视频只接受 MP4/MOV、2～10 秒、最大 `200,000,000` 字节、旋转后短边至少 480、最高 UHD 3840×2160。
- 原视频字节复制到 `<data_dir>/project-files/<project-id>/reference-videos/`；`projects.json` 不记录来源路径。
- 完整参考视频当前不发送到外部服务；本 Ticket 尚不生成分析代理。
- 保留现有四条全量验证命令，并补充后端 multipart 依赖安装来自 `requirements.lock`。

Run:

```sh
rg -n 'ffprobe|200,000,000|project-files|不生成分析代理' README.md
```

Expected: 四项说明均可定位，且文档没有声称已经具备播放、分析或 ComfyUI 能力。

- [x] **Step 3: 运行一次完整自动化预检**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 后端和前端全绿，真实 ffprobe 测试在当前机器通过，对比度和生产构建成功。若失败，先修复功能或测试并重新运行这组命令；detector 仍未使用。

- [x] **Step 4: 生成真实浏览器验收媒体**

在显式临时目录生成小型样本，不写入仓库：

```sh
TICKET02_QA_DIR=/tmp/ai-video-ticket02-qa-20260911
mkdir "$TICKET02_QA_DIR"
ffmpeg -y -f lavfi -i color=c=0x343938:s=854x480:r=24 -t 2 -c:v libx264 -pix_fmt yuv420p "$TICKET02_QA_DIR/valid.mp4"
ffmpeg -y -f lavfi -i color=c=0xa83b20:s=640x480:r=24 -t 2.5 -c:v libx264 -pix_fmt yuv420p "$TICKET02_QA_DIR/replacement.mov"
ffmpeg -y -f lavfi -i color=c=0x343938:s=640x360:r=24 -t 2 -c:v libx264 -pix_fmt yuv420p "$TICKET02_QA_DIR/low-resolution.mp4"
ffmpeg -y -f lavfi -i color=c=0x343938:s=854x480:r=24 -t 1 -c:v libx264 -pix_fmt yuv420p "$TICKET02_QA_DIR/too-short.mp4"
ffmpeg -y -f lavfi -i color=c=0x343938:s=3842x2160:r=1 -t 2 -c:v libx264 -pix_fmt yuv420p "$TICKET02_QA_DIR/over-uhd.mp4"
cp "$TICKET02_QA_DIR/valid.mp4" "$TICKET02_QA_DIR/corrupt.mp4"
truncate -s 64 "$TICKET02_QA_DIR/corrupt.mp4"
find "$TICKET02_QA_DIR" -maxdepth 1 -type f -print | sort
```

Expected: 六个文件存在；合法 MP4 与 MOV 可由 ffprobe 读取，另外四个分别触发已知拒绝边界。

- [x] **Step 5: 启动隔离服务并执行第一轮真实浏览器验收**

在两个终端使用 Step 4 的固定临时目录，不复用开发数据：

```sh
AI_VIDEO_REVERSE_ENGINEER_DATA_DIR="/tmp/ai-video-ticket02-qa-20260911/data" \
  .venv/bin/uvicorn app.main:app --app-dir backend --port 8000
```

```sh
npm --prefix frontend run dev -- --host 127.0.0.1
```

使用 Playwright CLI 或当前浏览器自动化能力执行并记录以下结果：

1. 1280×900 创建项目；选择前能看到 MP4/MOV、2～10 秒、200 MB、480P、UHD 4K。
2. 用文件 input 上传 `valid.mp4`；看到统一上传校验状态和 MP4、2.00 秒、854×480、24.00 fps。
3. 刷新并重新进入项目；元数据仍存在，无需重选。
4. 用真实 `DataTransfer` drop `replacement.mov`；先出现页面内确认，取消后旧值和焦点正确，再次 drop 并确认后显示 MOV 新值。
5. 依次尝试 `low-resolution.mp4`、`too-short.mp4`、`over-uhd.mp4`、`corrupt.mp4`；每次显示具体原因且 MOV 旧值仍存在。
6. 仅用 Tab、Enter、Space 完成文件入口、取消和确认替换；所有焦点可见，状态由文字与图标联合表达。
7. 在 1024px 验证完整交互；在 1023px 和 390px 确认只读、无 input/添加/替换控件、无横向溢出。
8. 保存 1280px、1024px、1023px、390px 四张截图，并记录控制台无未处理异常。

Expected: 八项均通过或形成一份去重后的具体缺陷清单；此时不要零散修改 UI。

- [x] **Step 6: 仅运行一次 Impeccable detector**

在 Task 6 UI 完整且 Step 5 浏览器证据已取得后，调用当前环境中的 Impeccable detector 一次，覆盖项目页上传空态、成功态、替换确认态、错误态、1024px 和 390px。把 detector 发现与 Step 5 缺陷合并去重，按可访问性、功能、响应式、视觉一致性排序。整个 Ticket 后续不得再次运行 detector。

Expected: 得到一份单一、有限、可追溯到设计约束的修复清单；不采纳要求引入播放器、分析能力、装饰动画、阴影或范围外重构的建议。

- [x] **Step 7: 用一个 apply_patch 完成单批修复并同步测试**

只修改本 Task Files 中列出的相关 UI 文件；把 Step 5 与 Step 6 的有效问题一次性修复。任何行为变化必须在同一个 patch 中同步加入对应 Testing Library 断言。修复必须保持以下接口不变：

```typescript
ReferenceVideoPanel({ project, onProjectUpdated, upload })
uploadReferenceVideo(projectId, file): Promise<Project>
```

Expected: 一个 patch 完成全部有效修复；不运行第二次 detector，不扩展 Ticket 范围。

- [x] **Step 8: 运行最终自动化和真实浏览器终审**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

然后重复 Step 5 的八项真实浏览器行为检查，但不再调用 detector。

Expected: 全部自动化命令通过；真实浏览器功能、键盘、响应式、视觉和控制台检查通过；项目刷新恢复与失败保旧值均有最终证据。

- [x] **Step 9: 最终验证检查点**

逐项对照设计文档、Ticket 02 八项清单和本计划 Global Constraints，确认：

- 所有硬限制都有后端测试和用户可见 message。
- 合法视频、重启恢复、替换成功、替换失败回滚均有 HTTP 证据。
- 文件选择、拖放、键盘、焦点、状态语义和 1024px 边界均有前端或浏览器证据。
- Ticket 01 全部测试继续通过。
- detector 总调用次数为一次，修复批次为一次。
- 没有新增播放、代理、关键帧、镜头检测、运动计算、分析按钮、分析服务或 ComfyUI 代码。

Expected: 每项均能指向具体测试结果或浏览器记录，Ticket 02 可交付。
