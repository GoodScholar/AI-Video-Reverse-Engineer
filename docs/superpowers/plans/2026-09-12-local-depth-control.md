# Local Depth Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本地生成、检查和预览时间连续的相对深度控制素材，并通过 Wan2.2 Fun Control 完成可下载、可环境检查和可 Queue 的正式工作流。

**Architecture:** 新增独立 `DepthCapture` 深模块，通过受控子进程调用固定版本的 Video Depth Anything Small；深度产物和质量结论使用独立存储边界，并复用现有单工作线程任务模式。Wan2.2 Fun Control 模板只消费通过质量门禁的深度控制素材，ComfyUI 传输细节继续封装在既有适配边界。

**Tech Stack:** Python 3.9+ 应用服务、Python 3.11 深度 worker、FastAPI、Pydantic、FFmpeg/ffprobe、Video Depth Anything Small、PyTorch、React 18、TypeScript 5.7、Vitest、pytest、ComfyUI HTTP API

**Spec:** `.scratch/ai-video-reverse-engineer/14-deep-motion-capture-and-generation-providers-design.md`

## Global Constraints

- 正式输入仍为单镜头、2～10 秒、一个清晰主要主体、轻度至中度运动。
- 完整参考视频只在本地读取，不发送到任何外部服务。
- Apple Silicon M1 / 8GB 是深度提取最低目标；CUDA 优先，Apple MPS 次之，CPU 为慢速回退。
- 默认模型固定为 Video Depth Anything Small 相对深度模型；不引入 Base、Large、度量深度、光流重投影、分割或三维重建。
- 近远方向、帧率、输出尺寸、模型提交、模型校验值和质量阈值全部版本化。
- `failed` 不得进入正式工作流；`review_required` 需用户明确确认，并把后续执行标记为实验性。
- Wan2.2 Fun Control 是本 Ticket 唯一正式原生深度控制路径。
- 不自动安装 ComfyUI、节点或模型，不显示未经真实导入与 Queue 验证的兼容版本。
- 移动端只读；完整操作面向宽度不低于 1024px，并满足 WCAG 2.1 AA。
- Task 8～10 在 Ticket 06、07、09、10 的工作台、复刻方案、Workflow 新鲜度和 ComfyUI 接缝完成前不得开始。
- 当前工作区没有 `.git`；各 Task 的 Commit 步骤只有在用户另行决定初始化或迁入 Git 仓库后执行，未获授权时将其作为阶段检查点并跳过，禁止计划执行者自行运行 `git init`。
- 上游锁定：Video Depth Anything commit `4f5ae23172ba60fd7bc11ef671cca678842c7072`；Small checkpoint SHA-256 `13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609`。
- 工作流来源锁定：VideoX-Fun commit `968f0e2192ba4c7a12868bf36d73260d135424ca` 的 `comfyui/wan2_2_fun/v1/wan2.2_fun_workflow_v2v_control.json`，导入仓库后记录原文件 SHA-256。

---

## File Map

### Backend

- `backend/app/depth_capture.py`：领域模型、阶段、固定算法版本、设备选择与质量结论。
- `backend/app/depth_capture_storage.py`：安全路径、阶段原子提交、产物校验和失效。
- `backend/app/depth_capture_runner.py`：FFmpeg 准备、受控 worker 子进程、产物编码和错误归一化。
- `backend/app/depth_quality.py`：纯函数质量指标与门禁决策。
- `backend/app/depth_capture_jobs.py`：单工作线程调度和同项目去重。
- `backend/depth_worker/run_depth.py`：最小模型进程入口，只读输入并写入指定临时目录。
- `backend/depth_worker/requirements.lock`：深度 worker 独立依赖；不污染 FastAPI 运行时。
- `backend/app/wan_fun_control_workflow.py`：模板绑定、输入校验和 Workflow 版本快照。
- `backend/app/workflow_templates/wan2.2_fun_workflow_v2v_control.json`：锁定的官方模板副本。
- `backend/tests/fixtures/depth/`：小型确定性帧序列、质量异常样本和假的 worker 输出。
- `backend/tests/test_depth_capture*.py`：领域、存储、runner、任务与 API 测试。
- `backend/tests/test_depth_quality.py`：质量纯函数测试。
- `backend/tests/test_wan_fun_control_workflow.py`：模板契约与输入绑定测试。
- `backend/tests/integration/test_wan_fun_control_comfyui.py`：真实 ComfyUI 导入与 Queue 测试，默认通过环境变量显式启用。

### Frontend

- `frontend/src/depthCaptureApi.ts`：启动、确认检查、读取媒体 URL。
- `frontend/src/DepthCapturePanel.tsx`：状态、设备、同步预览、质量检查和确认。
- `frontend/src/DepthCapturePanel.test.tsx`：用户可观察行为与可访问性测试。
- `frontend/src/models.ts`：深度捕捉与质量类型。
- `frontend/src/App.tsx`：把深度面板接入复刻项目并参与本地任务锁定。
- `frontend/src/styles.css`：同步预览、检查项和窄屏只读样式。

### Documentation

- `README.md`：深度 worker 安装、checkpoint、设备选择和验证命令。
- `backend/depth_worker/README.md`：固定上游、手动安装和校验步骤。

---

### Task 1: Define the Depth Capture Domain Contract

**Files:**
- Create: `backend/app/depth_capture.py`
- Create: `backend/tests/test_depth_capture.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `ReferenceVideo.id`, ISO-8601 时间戳和 `validate_storage_id()`。
- Produces: `DepthCapture`, `DepthCaptureStage`, `DepthQualityAssessment`, `new_depth_capture()` 和 `select_execution_device()`。

- [ ] **Step 1: Write failing model and device-selection tests**

```python
def test_new_depth_capture_has_fixed_stage_order():
    capture = new_depth_capture("video-1", "auto", "2026-09-12T00:00:00+00:00")
    assert [stage.name for stage in capture.stages] == [
        "preparing", "estimatingDepth", "encoding", "qualityAssessment"
    ]
    assert capture.status == "queued"

def test_auto_device_prefers_cuda_then_mps_then_cpu():
    assert select_execution_device("auto", cuda=True, mps=True) == "cuda"
    assert select_execution_device("auto", cuda=False, mps=True) == "mps"
    assert select_execution_device("auto", cuda=False, mps=False) == "cpu"

def test_explicit_unavailable_device_is_rejected():
    with pytest.raises(ValueError, match="所选深度计算设备不可用"):
        select_execution_device("cuda", cuda=False, mps=True)
```

- [ ] **Step 2: Run the focused tests and confirm the missing module failure**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.depth_capture'`.

- [ ] **Step 3: Implement the minimal versioned models**

```python
DEPTH_ALGORITHM_VERSION = 1
DEPTH_STAGE_ORDER = ("preparing", "estimatingDepth", "encoding", "qualityAssessment")

class DepthQualityAssessment(BaseModel):
    status: Literal["passed", "review_required", "failed"]
    checks: list[DepthQualityCheck]
    thresholdVersion: Literal[1] = 1

class DepthCapture(BaseModel):
    id: str
    sourceReferenceVideoId: str
    algorithmVersion: Literal[1] = DEPTH_ALGORITHM_VERSION
    status: Literal["queued", "running", "completed", "failed"]
    devicePreference: Literal["auto", "cuda", "mps", "cpu"]
    executionDevice: Optional[Literal["cuda", "mps", "cpu"]] = None
    currentStage: Optional[DepthStageName] = None
    stages: list[DepthStageState]
    qualityAssessment: Optional[DepthQualityAssessment] = None
    reviewConfirmedAt: Optional[str] = None
    error: Optional[DepthCaptureError] = None
    queuedAt: str
    startedAt: Optional[str] = None
    updatedAt: str
    completedAt: Optional[str] = None
```

Add `depthCaptures: list[DepthCapture] = Field(default_factory=list)` and `activeDepthCaptureId: Optional[str] = None` to `Project`. Defaults must preserve old project files.

- [ ] **Step 4: Run model and existing project compatibility tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture.py backend/tests/test_projects_api.py`

Expected: PASS.

- [ ] **Step 5: Commit the domain contract**

```bash
git add backend/app/depth_capture.py backend/app/main.py backend/tests/test_depth_capture.py
git commit -m "feat: define depth capture domain contract"
```

### Task 2: Add Safe, Atomic Depth Artifact Storage

**Files:**
- Create: `backend/app/depth_capture_storage.py`
- Create: `backend/tests/test_depth_capture_storage.py`

**Interfaces:**
- Consumes: `DepthCapture.id`, `Project.id`, `sourceReferenceVideoId` and `<data_dir>/project-files/`.
- Produces: `depth_capture_directory()`, `commit_depth_artifacts()`, `inspect_depth_artifacts()` and `discard_depth_capture()`.

- [ ] **Step 1: Write failing traversal, atomicity and manifest tests**

```python
def test_depth_capture_directory_rejects_traversal(tmp_path):
    with pytest.raises(OSError, match="深度捕捉路径超出数据目录"):
        depth_capture_directory(tmp_path, "../escape", "capture-1")

def test_commit_requires_all_five_artifacts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "depth-control.mp4").write_bytes(b"video")
    with pytest.raises(ValueError, match="深度捕捉产物不完整"):
        commit_depth_artifacts(workspace, canonical_directory(tmp_path))

def test_manifest_must_match_source_and_algorithm(tmp_path):
    create_valid_depth_artifacts(tmp_path, source="video-2", algorithm=1)
    assert inspect_depth_artifacts(tmp_path, source="video-1", algorithm=1).valid is False
```

- [ ] **Step 2: Run storage tests and confirm failure**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture_storage.py`

Expected: FAIL because the storage module does not exist.

- [ ] **Step 3: Implement the canonical layout and atomic directory promotion**

```python
DEPTH_ARTIFACTS = (
    "depth-control.mp4",
    "depth-preview.mp4",
    "depth-metadata.json",
    "depth-quality.json",
    "manifest.json",
)

def commit_depth_artifacts(workspace: Path, destination: Path) -> None:
    validate_workspace(workspace)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise OSError("深度捕捉目标已存在")
    os.replace(workspace, destination)
```

Manifest validation must require `schemaVersion == 1`, `algorithmVersion == 1`, matching `sourceReferenceVideoId`, relative filenames only, and non-empty regular files without symlinks.

- [ ] **Step 4: Run storage and preprocessing storage regression tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture_storage.py backend/tests/test_local_preprocessing_storage.py`

Expected: PASS.

- [ ] **Step 5: Commit storage**

```bash
git add backend/app/depth_capture_storage.py backend/tests/test_depth_capture_storage.py
git commit -m "feat: persist versioned depth artifacts"
```

### Task 3: Build the Isolated Video Depth Anything Worker

**Files:**
- Create: `backend/depth_worker/run_depth.py`
- Create: `backend/depth_worker/requirements.lock`
- Create: `backend/depth_worker/README.md`
- Create: `backend/app/depth_capture_runner.py`
- Create: `backend/tests/test_depth_capture_runner.py`
- Create: `backend/tests/fixtures/depth/fake_worker.py`

**Interfaces:**
- Consumes: managed reference video path, checkpoint path, `cuda|mps|cpu`, output directory, target FPS and long-edge limit.
- Produces: normalized `depths.npz`, `worker-metadata.json`, and `run_depth_capture()` that creates all five committed artifacts.

- [ ] **Step 1: Pin the isolated worker environment and upstream provenance**

`requirements.lock` must use the official Small-model dependency floor but omit unused metric/EXR packages:

```text
numpy==1.24.0
torch==2.1.1
torchvision==0.16.1
opencv-python==4.10.0.84
imageio==2.37.0
imageio-ffmpeg==0.4.7
einops==0.4.1
easydict==1.13
tqdm==4.67.1
```

The README must require Python 3.11 and give explicit commands to create `backend/depth_worker/.venv`, install the lock file, clone upstream commit `4f5ae23172ba60fd7bc11ef671cca678842c7072`, download the Small checkpoint, and verify SHA-256 `13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609`. Do not download or install automatically from the application. The worker reads frames through OpenCV instead of importing upstream `utils/dc_utils.py`, so `decord` is deliberately absent; `xformers` is also absent on the portable path and upstream's PyTorch attention fallback is exercised by the smoke test.

- [ ] **Step 2: Write failing subprocess and metadata tests**

```python
def test_runner_uses_argument_array_and_commits_expected_artifacts(tmp_path):
    result = run_depth_capture(
        request=fake_request(tmp_path, device="cpu"),
        worker_python=sys.executable,
        worker_script=FIXTURES / "fake_worker.py",
        ffmpeg_path="ffmpeg",
    )
    assert result.executionDevice == "cpu"
    assert set(path.name for path in result.directory.iterdir()) == set(DEPTH_ARTIFACTS)

def test_runner_converts_worker_oom_to_stable_error(tmp_path):
    with pytest.raises(DepthCaptureFailure) as error:
        run_failing_worker(tmp_path, stderr="MPS backend out of memory")
    assert error.value.code == "depth_device_out_of_memory"
```

- [ ] **Step 3: Run the runner tests and confirm failure**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture_runner.py`

Expected: FAIL because `run_depth_capture` is missing.

- [ ] **Step 4: Implement a strict worker CLI**

```python
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--checkpoint", required=True)
parser.add_argument("--upstream-root", required=True)
parser.add_argument("--device", choices=("cuda", "mps", "cpu"), required=True)
parser.add_argument("--target-fps", type=int, choices=(8, 16, 24), required=True)
parser.add_argument("--input-size", type=int, choices=(350, 518), required=True)
parser.add_argument("--max-res", type=int, choices=(640, 960, 1280), required=True)
```

The worker imports `VideoDepthAnything` from the configured pinned checkout, uses encoder `vits`, loads the verified checkpoint on CPU, moves the model to the selected device, runs relative-depth inference under `torch.no_grad()`, and writes only `depths.npz` plus JSON metadata to its assigned temporary output directory.

- [ ] **Step 5: Implement controlled runner execution and encoding**

Build an explicit `command: list[str]` containing the worker Python, script and every named argument, then call `subprocess.run(command, shell=False, timeout=DEPTH_TIMEOUT_SECONDS)`. For M1 / 8GB use `input-size=350`, `max-res=640`, target 8 FPS, batch behavior inherited from the upstream 32-frame window; for CUDA use 518/1280 and 16 FPS; CPU uses 350/640 and 8 FPS. Encode `depth-control.mp4` as H.264 `yuv420p` grayscale-expanded RGB for ComfyUI compatibility and `depth-preview.mp4` with a fixed Turbo color map. Both outputs must copy the worker FPS and have no audio.

- [ ] **Step 6: Run unit tests, then explicit hardware smoke tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture_runner.py`

Expected: PASS.

Run on each available target with a fixed 2-second fixture:

```bash
backend/depth_worker/.venv/bin/python backend/depth_worker/run_depth.py \
  --input backend/tests/fixtures/depth/two-second.mp4 \
  --output /tmp/ai-video-depth-smoke \
  --checkpoint backend/depth_worker/checkpoints/video_depth_anything_vits.pth \
  --upstream-root backend/depth_worker/vendor/Video-Depth-Anything \
  --device cpu --target-fps 8 --input-size 350 --max-res 640
```

Expected: exit 0; metadata reports 16 frames and a non-empty depth range. Repeat with `mps` on Apple Silicon and `cuda` on an NVIDIA host. If an explicit device fails, record a stable unavailable/unsupported error; `auto` may offer CPU only after informing the user.

- [ ] **Step 7: Commit worker and runner**

```bash
git add backend/depth_worker backend/app/depth_capture_runner.py backend/tests/test_depth_capture_runner.py backend/tests/fixtures/depth
git commit -m "feat: run isolated video depth inference"
```

### Task 4: Implement Versioned Depth Quality Gates

**Files:**
- Create: `backend/app/depth_quality.py`
- Create: `backend/tests/test_depth_quality.py`

**Interfaces:**
- Consumes: normalized depth tensor `[frames, height, width]`, source motion samples, expected video metadata.
- Produces: `assess_depth_quality()` returning six named `DepthQualityCheck` values and one status.

- [ ] **Step 1: Write one failing test for every gate**

```python
@pytest.mark.parametrize(("fixture", "criterion", "status"), [
    ("missing_frame.npz", "completeness", "failed"),
    ("constant_depth.npz", "dynamicRange", "failed"),
    ("flicker.npz", "temporalFlicker", "review_required"),
    ("inverted_segment.npz", "directionStability", "failed"),
    ("broken_edges.npz", "edgeContinuity", "review_required"),
    ("offset_timeline.npz", "timelineAlignment", "failed"),
])
def test_quality_gate_reports_specific_failure(fixture, criterion, status):
    result = assess_fixture(fixture)
    check = next(item for item in result.checks if item.criterion == criterion)
    assert check.status == status
```

- [ ] **Step 2: Run the quality tests and confirm failure**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_quality.py`

Expected: FAIL because the quality module is missing.

- [ ] **Step 3: Implement fixed threshold version 1**

```python
QUALITY_THRESHOLD_VERSION = 1
MIN_DEPTH_PERCENTILE_SPAN = 0.08
FLICKER_REVIEW_RATIO = 3.0
DIRECTION_REVERSAL_CORRELATION = -0.65
EDGE_BREAK_REVIEW_FRACTION = 0.20
TIMELINE_TOLERANCE_FRAMES = 1
```

Compute normalization from clip-wide P2/P98 percentiles, never per-frame min/max. `failed` takes precedence over `review_required`; every check records metric, threshold, message and sample timestamps. Generate `depth-quality.json` before committing the artifact directory.

- [ ] **Step 4: Run quality and runner regression tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_quality.py backend/tests/test_depth_capture_runner.py`

Expected: PASS.

- [ ] **Step 5: Commit quality gates**

```bash
git add backend/app/depth_quality.py backend/tests/test_depth_quality.py backend/app/depth_capture_runner.py
git commit -m "feat: gate depth control asset quality"
```

### Task 5: Add Recoverable Jobs and Depth Capture APIs

**Files:**
- Create: `backend/app/depth_capture_jobs.py`
- Create: `backend/tests/test_depth_capture_jobs.py`
- Create: `backend/tests/test_depth_capture_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/reference_video_storage.py`

**Interfaces:**
- Consumes: `run_depth_capture()`, project storage mutation and managed reference video path.
- Produces: `POST /api/projects/{id}/depth-captures`, `POST /api/projects/{id}/depth-captures/{capture_id}/confirm-review`, and project projections with artifact integrity validation.

- [ ] **Step 1: Write failing API behavior tests**

```python
def test_start_depth_capture_returns_202_and_queues_project(client, project_with_video):
    response = client.post(f"/api/projects/{project_with_video.id}/depth-captures", json={"devicePreference": "auto"})
    assert response.status_code == 202
    assert response.json()["depthCaptures"][-1]["status"] == "queued"

def test_start_requires_completed_in_scope_preconditions(client, project_id):
    response = client.post(f"/api/projects/{project_id}/depth-captures", json={"devicePreference": "auto"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_capture_preconditions_not_met"

def test_review_confirmation_only_accepts_review_required(client, passed_capture):
    response = client.post(f"/api/projects/p1/depth-captures/{passed_capture.id}/confirm-review")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "depth_review_not_required"
```

- [ ] **Step 2: Run API tests and confirm missing routes**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture_jobs.py backend/tests/test_depth_capture_api.py`

Expected: FAIL with 404 routes.

- [ ] **Step 3: Implement a shared single-local-compute queue**

Refactor the existing single-worker executor into `LocalComputeJobQueue` only if the same executor can preserve existing preprocessing behavior. Its key is `(job_kind, project_id)` and `max_workers=1`; both preprocessing and depth capture use this instance so an M1 / 8GB device never runs the two compute jobs concurrently.

```python
class LocalComputeJobQueue:
    def submit(self, job_kind: str, project_id: str, handler: Callable[[str], None]) -> bool:
        raise NotImplementedError
    def is_active(self, job_kind: str, project_id: str) -> bool:
        raise NotImplementedError
    def shutdown(self) -> None:
        raise NotImplementedError
```

- [ ] **Step 4: Implement route state transitions and restart reconciliation**

Start only when reference video and completed preprocessing still refer to the same video and the final reproducibility assessment is not out of scope. Persist every transition. On startup, convert `queued|running` captures to `failed` with `depth_capture_interrupted`; retain a completed capture only when all five artifacts validate.

- [ ] **Step 5: Make reference-video replacement invalidate active depth selection**

Keep historical `DepthCapture` records, set `activeDepthCaptureId` to `null`, and never delete a capture referenced by a completed `GenerationRun`. A future explicit cleanup operation may remove unreferenced artifacts; replacement itself must remain auditable.

- [ ] **Step 6: Run API, project and preprocessing regressions**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_capture_jobs.py backend/tests/test_depth_capture_api.py backend/tests/test_projects_api.py backend/tests/test_local_preprocessing_api.py`

Expected: PASS.

- [ ] **Step 7: Commit jobs and APIs**

```bash
git add backend/app/main.py backend/app/depth_capture_jobs.py backend/app/reference_video_storage.py backend/tests/test_depth_capture_jobs.py backend/tests/test_depth_capture_api.py
git commit -m "feat: add recoverable depth capture jobs"
```

### Task 6: Expose Authorized Local Preview Media

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_depth_media_api.py`
- Create: `frontend/src/depthCaptureApi.ts`
- Create: `frontend/src/depthCaptureApi.test.ts`
- Modify: `frontend/src/models.ts`

**Interfaces:**
- Consumes: active project and capture IDs.
- Produces: same-origin streamed preview URLs and typed client methods.

- [ ] **Step 1: Write failing range and ownership tests**

```python
def test_depth_preview_supports_range_requests(client, completed_capture):
    response = client.get(completed_capture.preview_url, headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert response.headers["accept-ranges"] == "bytes"

def test_capture_from_another_project_is_not_exposed(client, capture_from_p2):
    response = client.get(f"/api/projects/p1/depth-captures/{capture_from_p2.id}/preview")
    assert response.status_code == 404
```

- [ ] **Step 2: Implement fixed media routes**

```http
GET /api/projects/{project_id}/reference-video/content
GET /api/projects/{project_id}/depth-captures/{capture_id}/preview
```

Resolve only managed canonical paths, reject symlinks, emit `video/mp4`, `Accept-Ranges: bytes`, and `Cache-Control: private, no-store`.

- [ ] **Step 3: Add client types and API methods**

```ts
export type DepthCapture = {
  id: string;
  sourceReferenceVideoId: string;
  status: "queued" | "running" | "completed" | "failed";
  devicePreference: "auto" | "cuda" | "mps" | "cpu";
  executionDevice: "cuda" | "mps" | "cpu" | null;
  qualityAssessment: DepthQualityAssessment | null;
  reviewConfirmedAt: string | null;
};

export const depthPreviewUrl = (projectId: string, captureId: string) =>
  `/api/projects/${encodeURIComponent(projectId)}/depth-captures/${encodeURIComponent(captureId)}/preview`;
```

- [ ] **Step 4: Run backend and frontend API tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_depth_media_api.py`

Run: `npm --prefix frontend test -- depthCaptureApi.test.ts`

Expected: both PASS.

- [ ] **Step 5: Commit preview boundary**

```bash
git add backend/app/main.py backend/tests/test_depth_media_api.py frontend/src/depthCaptureApi.ts frontend/src/depthCaptureApi.test.ts frontend/src/models.ts
git commit -m "feat: expose local depth previews"
```

### Task 7: Build the Synchronized Depth Review Panel

**Files:**
- Create: `frontend/src/DepthCapturePanel.tsx`
- Create: `frontend/src/DepthCapturePanel.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `Project.depthCaptures`, active capture ID, media URLs and depth APIs.
- Produces: desktop start/retry/review actions and mobile read-only state.

- [ ] **Step 1: Write failing user-flow tests**

```tsx
it("keeps reference and depth previews on the same currentTime", async () => {
  render(<DepthCapturePanel project={completedProject} onProjectUpdated={vi.fn()} />);
  const reference = screen.getByLabelText("参考视频预览") as HTMLVideoElement;
  const depth = screen.getByLabelText("深度控制预览") as HTMLVideoElement;
  fireEvent.timeUpdate(Object.assign(reference, { currentTime: 1.25 }));
  expect(depth.currentTime).toBeCloseTo(1.25, 2);
});

it("requires confirmation for review_required before enabling workflow", async () => {
  render(<DepthCapturePanel project={reviewProject} onProjectUpdated={vi.fn()} />);
  expect(screen.getByRole("button", { name: "生成深度控制工作流" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "我已检查，继续实验性生成" }));
  expect(confirmReview).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run focused component tests and confirm failure**

Run: `npm --prefix frontend test -- DepthCapturePanel.test.tsx`

Expected: FAIL because the component is missing.

- [ ] **Step 3: Implement status, device and six named quality checks**

Render text for queued/running/completed/failed; announce state changes with `aria-live="polite"`; show final execution device and CPU slow-path warning. Do not collapse the six checks into one score.

- [ ] **Step 4: Implement synchronized playback without a timer loop**

Use media events (`play`, `pause`, `seeking`, `timeupdate`) from the reference video as source of truth. Correct the depth preview only when absolute drift exceeds 0.08 seconds. Respect `prefers-reduced-motion`; no decorative animation is added.

- [ ] **Step 5: Lock mutation actions below 1024px and during local compute**

On mobile render previews and results but no start, retry or confirmation buttons. In `App.tsx`, disable reference replacement while preprocessing or depth capture is queued/running.

- [ ] **Step 6: Run component, app, contrast and build checks**

Run: `npm --prefix frontend test -- DepthCapturePanel.test.tsx App.test.tsx`

Run: `node frontend/scripts/verify-color-contrast.mjs`

Run: `npm --prefix frontend run build`

Expected: all PASS.

- [ ] **Step 7: Commit the review panel**

```bash
git add frontend/src/DepthCapturePanel.tsx frontend/src/DepthCapturePanel.test.tsx frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/styles.css
git commit -m "feat: review synchronized depth capture"
```

### Task 8: Bind a Locked Wan2.2 Fun Control Workflow

**Files:**
- Create: `backend/app/workflow_templates/wan2.2_fun_workflow_v2v_control.json`
- Create: `backend/app/workflow_templates/wan2.2_fun_workflow_v2v_control.manifest.json`
- Create: `backend/app/wan_fun_control_workflow.py`
- Create: `backend/tests/test_wan_fun_control_workflow.py`

**Interfaces:**
- Consumes: current reproduction-plan snapshot, prompt snapshot, output settings, active depth control asset and optional first frame.
- Produces: immutable `WorkflowArtifact` with template ID `wan2.2-fun-control-depth-v1`.

- [ ] **Step 1: Import and record the official template exactly once**

Copy `comfyui/wan2_2_fun/v1/wan2.2_fun_workflow_v2v_control.json` from VideoX-Fun commit `968f0e2192ba4c7a12868bf36d73260d135424ca`; record source repository, commit, source path and SHA-256 in the adjacent manifest. Do not fetch the template at runtime.

- [ ] **Step 2: Write failing binding and freshness tests**

```python
def test_builder_binds_control_video_prompt_and_dimensions(valid_request):
    workflow = build_wan_fun_control_workflow(valid_request)
    assert workflow.templateId == "wan2.2-fun-control-depth-v1"
    assert workflow.inputs.controlVideo == "depth-control.mp4"
    assert workflow.inputs.width == 640
    assert workflow.inputs.height == 640

def test_failed_quality_is_rejected(valid_request):
    valid_request.depthCapture.qualityAssessment.status = "failed"
    with pytest.raises(WorkflowValidationError, match="深度质量检查未通过"):
        build_wan_fun_control_workflow(valid_request)
```

- [ ] **Step 3: Implement explicit template selectors**

Create one binding map keyed by semantic roles, not incidental node order:

```python
WAN_FUN_CONTROL_BINDINGS = {
    "positivePrompt": {"nodeTitle": "Prompts", "input": "positive_prompt"},
    "negativePrompt": {"nodeTitle": "Prompts", "input": "negative_prompt"},
    "controlVideo": {"nodeClass": "VHS_LoadVideo", "ordinal": 2, "input": "video"},
    "generator": {"nodeClass": "Wan22FunControlToVideo"},
}
```

At import time, normalize the UI workflow to the API prompt format and fail the fixture-contract test if a selector resolves zero or multiple nodes. Do not search arbitrary nodes at runtime.

- [ ] **Step 4: Validate model and node requirements from the locked manifest**

The manifest must list `Wan22FunControlToVideo`, `VHS_LoadVideo`, high-noise and low-noise Fun Control diffusion models, `umt5_xxl_fp8_e4m3fn_scaled.safetensors`, `wan_2.1_vae.safetensors`, and both Lightning LoRAs used by the imported template. Exact filenames must be copied from the locked template, not reconstructed from memory.

- [ ] **Step 5: Run builder tests and JSON schema checks**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_wan_fun_control_workflow.py`

Expected: PASS and no unresolved semantic binding.

- [ ] **Step 6: Commit the locked template and builder**

```bash
git add backend/app/workflow_templates backend/app/wan_fun_control_workflow.py backend/tests/test_wan_fun_control_workflow.py
git commit -m "feat: build wan fun depth workflows"
```

### Task 9: Connect Export, Environment Check and ComfyUI Queue

**Files:**
- Modify: `backend/app/reproduction_package.py`
- Modify: `backend/app/comfyui_adapter.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_wan_fun_control_package.py`
- Create: `backend/tests/integration/test_wan_fun_control_comfyui.py`
- Modify: `frontend/src/models.ts`
- Modify: `frontend/src/DepthCapturePanel.tsx`

**Interfaces:**
- Consumes: `WorkflowArtifact`, active depth artifacts and existing ComfyUI environment/Queue APIs from Ticket 10.
- Produces: downloadable package, dependency report, Queue state and linked generated result.

- [ ] **Step 1: Write failing package and environment tests**

```python
def test_depth_package_contains_control_not_reference_video(package_zip):
    assert set(package_zip.namelist()) >= {
        "workflow.json", "depth-control.mp4", "depth-quality.json", "manifest.json"
    }
    assert "reference-video.mp4" not in package_zip.namelist()

def test_environment_reports_each_missing_fun_dependency(fake_comfyui):
    report = inspect_wan_fun_control_environment(fake_comfyui)
    assert {item.kind for item in report.missing} == {"node", "model"}
```

- [ ] **Step 2: Add the depth assets to package and freshness snapshots**

Include depth capture ID, algorithm/model/quality versions and review confirmation in the workflow input snapshot. Changing the active depth capture, prompt or output dimensions marks the Workflow stale; a completed historical generation keeps its original snapshot.

- [ ] **Step 3: Extend environment checking and Queue mapping**

Resolve required node classes from `/object_info`, required model filenames from the appropriate ComfyUI model lists, upload/copy the control video only to the configured local ComfyUI input boundary, then submit the normalized prompt. Never install a missing dependency.

- [ ] **Step 4: Run fake-boundary tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_wan_fun_control_package.py backend/tests/test_comfyui_adapter.py`

Expected: PASS.

- [ ] **Step 5: Run the opt-in real ComfyUI integration test**

Run:

```bash
AI_VIDEO_COMFYUI_URL=http://127.0.0.1:8188 \
AI_VIDEO_RUN_COMFYUI_INTEGRATION=1 \
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/integration/test_wan_fun_control_comfyui.py
```

Expected: the locked workflow imports, enters Queue, reaches a terminal success state and produces a linked video. Record the tested ComfyUI commit and custom-node commits in the workflow manifest; do not claim compatibility if this command is skipped or fails.

- [ ] **Step 6: Commit the end-to-end local generation path**

```bash
git add backend/app/reproduction_package.py backend/app/comfyui_adapter.py backend/app/main.py backend/tests/test_wan_fun_control_package.py backend/tests/integration/test_wan_fun_control_comfyui.py frontend/src/models.ts frontend/src/DepthCapturePanel.tsx
git commit -m "feat: queue wan fun depth generation"
```

### Task 10: Verify M1 Baseline, Regression Suite and Documentation

**Files:**
- Create: `backend/tests/performance/test_depth_capture_m1.py`
- Modify: `README.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/14-generate-local-depth-control-and-wan-fun-control.md`

**Interfaces:**
- Consumes: completed Tasks 1～9 and fixed 2-, 5-, 10-second acceptance videos.
- Produces: reproducible evidence for memory stability, output contract and local end-to-end completion.

- [ ] **Step 1: Add an opt-in M1 acceptance harness**

```python
@pytest.mark.parametrize("duration", [2, 5, 10])
def test_m1_depth_capture_completes_within_memory_budget(duration, acceptance_video):
    result = run_measured_capture(acceptance_video(duration), device="auto")
    assert result.exit_code == 0
    assert result.peak_rss_bytes < 6_500_000_000
    assert result.quality.status in {"passed", "review_required"}
```

This is a stability budget, not a speed promise. Record elapsed time separately for product guidance.

- [ ] **Step 2: Document installation, data boundaries and recovery**

README must describe the separate worker environment, pinned upstream/checkpoint verification, `auto|cuda|mps|cpu`, artifact directory, quality statuses, no automatic cloud upload, Wan2.2 Fun Control dependencies and opt-in integration commands.

- [ ] **Step 3: Run the complete automated suite**

Run:

```bash
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: all commands exit 0.

- [ ] **Step 4: Run M1 and real ComfyUI acceptance where hardware exists**

Run the M1 harness with `AI_VIDEO_RUN_M1_ACCEPTANCE=1` and the real ComfyUI command from Task 9. Attach command output and tested commits under `## Comments` in issue 14. If either environment is unavailable, leave the corresponding acceptance item unchecked and do not mark Ticket 14 complete.

- [ ] **Step 5: Resolve the local-depth ticket only after evidence exists**

Change issue 14 status from `ready-for-agent` to the project’s resolved state only when the automated suite, M1 acceptance and real ComfyUI Queue all pass.

- [ ] **Step 6: Commit verification evidence and docs**

```bash
git add README.md backend/tests/performance/test_depth_capture_m1.py .scratch/ai-video-reverse-engineer/issues/14-generate-local-depth-control-and-wan-fun-control.md
git commit -m "test: verify local depth control workflow"
```
