# Cloud Video Generation Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不夸大深度能力、不静默上传素材或切换供应商的前提下，为 MiniMax H3 与豆包 Seedance 2.0 提供可审计的实验性参考式生成路径。

**Architecture:** 在 `VideoGenerationProvider` 接缝上建立能力驱动接口、持久化生成运行和一次性素材披露授权；MiniMax 与 Seedance 适配器分别转换官方异步 API，不共享供应商响应模型。项目只依赖稳定领域状态，外部任务标识用于重启恢复，任何重试提交都需要新的用户动作和幂等保护。

**Tech Stack:** Python 3.9+、FastAPI、Pydantic、HTTPX、系统安全凭据存储、React 18、TypeScript 5.7、Vitest、pytest、MiniMax Video Generation V2 API、火山方舟 Contents Generation API

**Spec:** `.scratch/ai-video-reverse-engineer/14-deep-motion-capture-and-generation-providers-design.md`

## Global Constraints

- 本计划被 Ticket 14 本地深度控制闭环阻塞；不得先于本地正式基准上线。
- MiniMax H3 与 Seedance 2.0 初始支持等级固定为 `experimental`，不是原生深度控制。
- 只有固定效果测试集通过后，单个适配器才可独立升级为 `beta`。
- 完整参考视频默认不绑定、不上传；深度控制素材、参考图和提示词也必须逐次披露并授权。
- 披露授权绑定供应商、项目、素材校验值和请求摘要；不得跨供应商或修改后的请求复用。
- 凭据只存在系统安全存储，不进入项目、日志、前端存储、导出包或测试快照。
- 供应商错误、限流或超时不得触发静默切换，也不得自动创建新的计费任务。
- 外部任务提交后立即持久化供应商任务 ID；应用重启后只查询，不重新提交。
- 价格属于易变外部事实；界面只展示供应商返回的明确预估或官方价格入口，不硬编码金额。
- MiniMax 官方契约基线为 `https://platform.minimax.io/docs/api-reference/video-generation-v2-create`、query、delete 和 file retrieval 文档的执行日快照。
- Seedance 官方契约基线为火山方舟 `POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks` 及对应 `GET /api/v3/contents/generations/tasks/{id}` 文档的执行日快照。
- 真实 API 验收会产生外部数据传输和费用，必须由执行者使用专用验收账户显式启用；普通自动化测试只使用本地 HTTP 替身。
- 当前工作区没有 `.git`；各 Task 的 Commit 步骤只有在用户另行决定初始化或迁入 Git 仓库后执行，未获授权时将其作为阶段检查点并跳过，禁止计划执行者自行运行 `git init`。

---

## File Map

### Backend

- `backend/app/video_generation.py`：供应商能力、规范化请求、素材绑定、生成运行和稳定错误。
- `backend/app/video_generation_provider.py`：Provider Protocol、注册表和能力匹配。
- `backend/app/video_generation_storage.py`：运行输入快照、状态和结果的原子持久化。
- `backend/app/video_generation_jobs.py`：查询调度、恢复和幂等提交协调。
- `backend/app/generation_disclosure.py`：按供应商、素材和请求摘要创建/验证一次性授权。
- `backend/app/providers/minimax_h3.py`：MiniMax H3 请求、查询、取消和结果下载。
- `backend/app/providers/seedance.py`：Seedance 请求、查询、取消能力和结果下载。
- `backend/app/provider_credentials.py`：在既有系统安全存储边界上命名、读取和删除视频供应商凭据。
- `backend/tests/contracts/`：从官方文档保存、脱敏并标注获取日期的请求/响应契约样本。
- `backend/tests/test_video_generation*.py`：领域、披露、持久化、任务和 API 测试。
- `backend/tests/providers/`：每个供应商独立契约与错误映射测试。
- `backend/tests/integration/`：显式启用的真实供应商验收。

### Frontend

- `frontend/src/videoGenerationApi.ts`：供应商、校验、披露、提交、查询和取消请求。
- `frontend/src/GenerationProviderPanel.tsx`：能力比较、素材披露、授权和运行状态。
- `frontend/src/GenerationProviderPanel.test.tsx`：禁止夸大、禁止静默提交和恢复状态测试。
- `frontend/src/models.ts`：能力、披露和生成运行类型。
- `frontend/src/App.tsx`：接入复刻工作台和项目轮询。
- `frontend/src/styles.css`：支持等级、披露清单、错误与移动端只读样式。

### Evidence

- `docs/provider-contracts/minimax-h3-2026-09-12.md`：官方字段、端点与执行日。
- `docs/provider-contracts/seedance-2-2026-09-12.md`：官方字段、端点与执行日。
- `docs/provider-evaluations/minimax-h3.md`：固定测试集结果。
- `docs/provider-evaluations/seedance-2.md`：固定测试集结果。

---

### Task 1: Define Provider Capabilities and Matching

**Files:**
- Create: `backend/app/video_generation.py`
- Create: `backend/app/video_generation_provider.py`
- Create: `backend/tests/test_video_generation.py`
- Create: `backend/tests/test_video_generation_provider.py`

**Interfaces:**
- Consumes: reproduction-plan snapshot, prompt snapshot, output settings and local asset descriptors.
- Produces: `ProviderCapabilities`, `VideoGenerationRequest`, `GenerationRun`, `VideoGenerationProvider` and `match_provider_capabilities()`.

- [ ] **Step 1: Write failing capability-match tests**

```python
def test_native_depth_strategy_rejects_reference_only_provider():
    result = match_provider_capabilities(
        strategy="native_depth_control",
        capabilities=reference_video_capabilities("minimax-h3"),
    )
    assert result.compatible is False
    assert result.code == "native_depth_control_unavailable"

def test_no_match_silently_changes_strategy():
    result = match_provider_capabilities("reference_video_motion", image_only_capabilities("veo"))
    assert result.compatible is False
    assert result.suggestedStrategy == "image_prompt_fallback"
    assert result.requiresUserChoice is True
```

- [ ] **Step 2: Run tests and confirm missing modules**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_generation.py backend/tests/test_video_generation_provider.py`

Expected: FAIL because the provider contract does not exist.

- [ ] **Step 3: Implement exact capability and run types**

```python
class ProviderCapabilities(BaseModel):
    providerId: str
    adapterVersion: int
    nativeDepthControl: bool
    arbitraryReferenceVideo: bool
    videoEdit: bool
    referenceImages: bool
    firstLastFrames: bool
    maxDurationSeconds: Optional[int]
    acceptedVideoFormats: list[str]
    cancellation: Literal["supported", "unsupported", "unknown"]
    dataBoundary: Literal["local", "cloud"]
    supportLevel: Literal["verified", "beta", "experimental", "fallback"]

class VideoGenerationProvider(Protocol):
    def describe_capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError
    def validate(self, request: VideoGenerationRequest) -> ProviderValidationResult:
        raise NotImplementedError
    def submit(self, request: VideoGenerationRequest, credentials: str) -> ExternalJob:
        raise NotImplementedError
    def get_status(self, external_job_id: str, credentials: str) -> ExternalStatus:
        raise NotImplementedError
    def cancel(self, external_job_id: str, credentials: str) -> CancelResult:
        raise NotImplementedError
    def fetch_result(self, external_job_id: str, credentials: str, destination: Path) -> GeneratedVideoResult:
        raise NotImplementedError
```

Implement the registry with explicit IDs `local-comfyui`, `minimax-h3`, and `seedance-2`; no dynamic plugin loader is introduced.

- [ ] **Step 4: Run contract tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_generation.py backend/tests/test_video_generation_provider.py`

Expected: PASS.

- [ ] **Step 5: Commit provider contracts**

```bash
git add backend/app/video_generation.py backend/app/video_generation_provider.py backend/tests/test_video_generation.py backend/tests/test_video_generation_provider.py
git commit -m "feat: define video generation provider capabilities"
```

### Task 2: Capture Official API Contracts Before Adapter Code

**Files:**
- Create: `docs/provider-contracts/minimax-h3-2026-09-12.md`
- Create: `docs/provider-contracts/seedance-2-2026-09-12.md`
- Create: `backend/tests/contracts/minimax_h3_create.json`
- Create: `backend/tests/contracts/minimax_h3_status.json`
- Create: `backend/tests/contracts/seedance_create.json`
- Create: `backend/tests/contracts/seedance_status.json`

**Interfaces:**
- Consumes: official API documentation available on the execution date.
- Produces: reviewed, redacted fixtures that later adapter tests treat as the external contract.

- [ ] **Step 1: Record each exact endpoint and field table from official docs**

For MiniMax record create/query/cancel/result endpoints, H3 model IDs, supported `content` item types, duration/resolution limits, status values and result expiry. For Seedance record create/query/cancel behavior, the current model ID, `content` item schema for text/image/video/audio, asset limits, status values and output URL expiry.

- [ ] **Step 2: Save one minimal request and every terminal response shape**

Fixtures must include queued/running/succeeded/failed responses and redact keys, user URLs and task IDs with stable values such as `task-contract-1`. Add `sourceUrl`, `retrievedAt` and `modelId` beside every JSON block in the Markdown evidence.

- [ ] **Step 3: Add a fixture sanity test**

```python
@pytest.mark.parametrize("name", CONTRACT_FIXTURES)
def test_provider_contract_fixture_is_redacted_and_attributed(name):
    text = (CONTRACT_DIR / name).read_text()
    assert "sourceUrl" in text
    assert "retrievedAt" in text
    assert "Bearer " not in text
    assert "sk-" not in text
```

- [ ] **Step 4: Review the contract delta against the approved design**

If either official API lacks arbitrary reference-video input on the execution date, keep that provider registered but set `arbitraryReferenceVideo=false`; do not implement a fabricated depth-video binding. The provider remains visible only for capabilities its contract actually exposes.

- [ ] **Step 5: Commit the external contract evidence**

```bash
git add docs/provider-contracts backend/tests/contracts backend/tests/test_provider_contract_fixtures.py
git commit -m "test: capture cloud video provider contracts"
```

### Task 3: Reuse the System Credential Boundary

**Files:**
- Create: `backend/app/provider_credentials.py`
- Create: `backend/tests/test_provider_credentials.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: Ticket 04 `CredentialStore.get(name)`, `set(name, value)` and `delete(name)` backed by system secure storage.
- Produces: namespaced credential operations for `video-generation:minimax-h3` and `video-generation:seedance-2`.

- [ ] **Step 1: Write failing isolation and redaction tests**

```python
def test_video_credentials_are_namespaced(fake_store):
    save_provider_credential(fake_store, "minimax-h3", "secret")
    assert fake_store.values == {"video-generation:minimax-h3": "secret"}

def test_project_json_never_contains_provider_secret(client, fake_store):
    save_provider_credential(fake_store, "seedance-2", "secret-value")
    assert "secret-value" not in client.get("/api/projects").text
```

- [ ] **Step 2: Implement a strict provider allowlist**

```python
VIDEO_PROVIDER_CREDENTIAL_NAMES = {
    "minimax-h3": "video-generation:minimax-h3",
    "seedance-2": "video-generation:seedance-2",
}
```

Unknown IDs return `provider_not_supported`. GET capability/configuration responses expose only `configured: boolean`.

- [ ] **Step 3: Add save/delete/test routes using the existing credential API pattern**

No route returns a secret. A connection test may call a read-only provider endpoint; failure maps to authentication/unavailable errors without logging request headers.

- [ ] **Step 4: Run credential and project persistence tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_provider_credentials.py backend/tests/test_projects_api.py`

Expected: PASS.

- [ ] **Step 5: Commit credential integration**

```bash
git add backend/app/provider_credentials.py backend/tests/test_provider_credentials.py backend/app/main.py
git commit -m "feat: store video provider credentials securely"
```

### Task 4: Implement Request Validation and One-Time Disclosure Authorization

**Files:**
- Create: `backend/app/generation_disclosure.py`
- Create: `backend/tests/test_generation_disclosure.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: normalized request, selected provider capabilities and canonical asset descriptors.
- Produces: validation response, disclosure summary and short-lived `disclosureAcceptanceId`.

- [ ] **Step 1: Write failing authorization-binding tests**

```python
def test_acceptance_is_bound_to_provider_and_asset_hashes(service):
    acceptance = service.accept(disclosure(provider="minimax-h3", sha256="aaa"))
    assert service.verify(acceptance.id, request(provider="seedance-2", sha256="aaa")) is False
    assert service.verify(acceptance.id, request(provider="minimax-h3", sha256="bbb")) is False

def test_reference_video_is_absent_by_default(validated_request):
    assert "referenceVideo" not in validated_request.disclosure.assets
```

- [ ] **Step 2: Implement deterministic request and asset digests**

```python
def disclosure_digest(provider_id: str, request: dict, assets: list[AssetDescriptor]) -> str:
    payload = {
        "providerId": provider_id,
        "request": request,
        "assets": [item.model_dump() for item in sorted(assets, key=lambda item: item.role)],
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()
```

Store only the digest, accepted timestamp and expiry in project state. Acceptance expires after 15 minutes or immediately when provider, prompt, settings or assets change.

- [ ] **Step 3: Add validate and accept endpoints**

```http
POST /api/projects/{project_id}/generation-runs/validate
POST /api/projects/{project_id}/generation-disclosures/{disclosure_id}/accept
```

Validation performs no upload and no billable create call. Acceptance returns only an ID and expiry.

- [ ] **Step 4: Run disclosure and API tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_generation_disclosure.py backend/tests/test_video_generation_api.py`

Expected: PASS.

- [ ] **Step 5: Commit disclosure authorization**

```bash
git add backend/app/generation_disclosure.py backend/tests/test_generation_disclosure.py backend/app/main.py
git commit -m "feat: require cloud generation disclosure"
```

### Task 5: Persist Idempotent Generation Runs and Resume Queries

**Files:**
- Create: `backend/app/video_generation_storage.py`
- Create: `backend/app/video_generation_jobs.py`
- Create: `backend/tests/test_video_generation_storage.py`
- Create: `backend/tests/test_video_generation_jobs.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: authorized request, provider registry and credential store.
- Produces: durable `GenerationRun`, submit/query/cancel APIs and downloaded local result.

- [ ] **Step 1: Write failing duplicate-submit and restart tests**

```python
def test_same_idempotency_key_submits_once(service, fake_provider):
    first = service.submit(request(idempotencyKey="request-1"))
    second = service.submit(request(idempotencyKey="request-1"))
    assert first.id == second.id
    assert fake_provider.submit_count == 1

def test_restart_queries_external_job_instead_of_resubmitting(service_factory, saved_running_run):
    service = service_factory(saved_running_run)
    service.reconcile()
    assert service.provider.get_status_count == 1
    assert service.provider.submit_count == 0
```

- [ ] **Step 2: Implement generation run storage and canonical result paths**

Store metadata in the project and output video under:

```text
<data_dir>/project-files/<project-id>/generation-runs/<run-id>/result.mp4
<data_dir>/project-files/<project-id>/generation-runs/<run-id>/manifest.json
```

Validate safe IDs, no symlinks, checksum downloaded bytes, and atomically promote `.part` files.

- [ ] **Step 3: Implement submit ordering that closes the billing gap**

Persist a `submitting` run with idempotency key before calling the provider. Immediately persist `externalJobReference` after a successful response. If the process dies in the uncertain interval, mark the run `submission_unknown` and require user reconciliation; never auto-submit again.

- [ ] **Step 4: Implement bounded polling and recovery**

Use one timer loop with provider-specific minimum interval, exponential backoff capped at 30 seconds and persisted last-query time. On success, download immediately to local managed storage because provider URLs may expire.

- [ ] **Step 5: Run storage, job and API tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_generation_storage.py backend/tests/test_video_generation_jobs.py backend/tests/test_video_generation_api.py`

Expected: PASS.

- [ ] **Step 6: Commit durable generation runs**

```bash
git add backend/app/video_generation_storage.py backend/app/video_generation_jobs.py backend/app/main.py backend/tests/test_video_generation_storage.py backend/tests/test_video_generation_jobs.py backend/tests/test_video_generation_api.py
git commit -m "feat: persist cloud video generation runs"
```

### Task 6: Implement the MiniMax H3 Adapter

**Files:**
- Create: `backend/app/providers/__init__.py`
- Create: `backend/app/providers/minimax_h3.py`
- Create: `backend/tests/providers/test_minimax_h3.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.lock`

**Interfaces:**
- Consumes: normalized reference-video request, MiniMax credential and Task 2 contract fixtures.
- Produces: `MiniMaxH3Provider` with stable external status and errors.

- [ ] **Step 1: Add the locked HTTP client dependency**

Add `httpx==0.28.1` to both dependency files and install through the existing lock-file workflow.

- [ ] **Step 2: Write request, status and error contract tests**

```python
def test_submit_uses_h3_content_array(http_mock, h3_request):
    provider = MiniMaxH3Provider(client=http_mock)
    job = provider.submit(h3_request, "secret")
    sent = http_mock.last_request.json()
    assert sent["model"] in {"MiniMax-H3", "MiniMax-H3-Max"}
    assert [item["type"] for item in sent["content"]] == ["text", "video"]
    assert job.id == "task-contract-1"

@pytest.mark.parametrize(("remote", "local"), [
    ("Preparing", "queued"), ("Queueing", "queued"),
    ("Processing", "running"), ("Success", "completed"), ("Fail", "failed"),
])
def test_status_mapping(remote, local, provider):
    assert provider.map_status(remote) == local
```

- [ ] **Step 3: Implement only capabilities verified in Task 2**

Set `nativeDepthControl=false`, `supportLevel=experimental`, and `arbitraryReferenceVideo` to the official contract result. Build `content` in prompt-first order and bind a video only when the documented H3 schema accepts it. Reject unsupported duration/resolution before calling HTTP.

- [ ] **Step 4: Normalize provider errors without leaking bodies**

Map 401/403 to `provider_authentication_failed`, documented quota/balance codes to `provider_balance_insufficient`, 429 to `provider_rate_limited`, unsupported input to `provider_input_rejected`, and 5xx/timeouts to `provider_unavailable`. Store provider request IDs, not authorization headers or full response bodies.

- [ ] **Step 5: Run adapter contract tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/providers/test_minimax_h3.py`

Expected: PASS with no real network calls.

- [ ] **Step 6: Commit the MiniMax adapter**

```bash
git add backend/app/providers backend/tests/providers/test_minimax_h3.py backend/pyproject.toml backend/requirements.lock
git commit -m "feat: add experimental minimax h3 adapter"
```

### Task 7: Implement the Seedance 2.0 Adapter

**Files:**
- Create: `backend/app/providers/seedance.py`
- Create: `backend/tests/providers/test_seedance.py`

**Interfaces:**
- Consumes: normalized reference-video request, Ark credential and Task 2 Seedance contract fixtures.
- Produces: `SeedanceProvider` using the official contents-generation task API.

- [ ] **Step 1: Write request, status and role-order contract tests**

```python
def test_submit_preserves_documented_asset_order(http_mock, seedance_request):
    provider = SeedanceProvider(client=http_mock)
    provider.submit(seedance_request, "secret")
    content = http_mock.last_request.json()["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "video_url"

def test_provider_does_not_bind_reference_video_without_authorized_role(provider, request):
    request.assetBindings.referenceMotionVideo = None
    payload = provider.build_payload(request)
    assert all(item["type"] != "video_url" for item in payload["content"])
```

- [ ] **Step 2: Implement the documented Ark task endpoints**

Use `POST /api/v3/contents/generations/tasks` and `GET /api/v3/contents/generations/tasks/{id}` against `https://ark.cn-beijing.volces.com`. Copy the execution-date model ID and exact content roles from Task 2 evidence. Do not use a third-party OpenAI-compatible aggregator.

- [ ] **Step 3: Declare cancellation honestly**

If Task 2 official contract exposes cancellation, implement and test it; otherwise set `cancellation="unsupported"`, return a stable `provider_cancellation_unsupported` result, and leave the remote task running while stopping local polling only after explicit user confirmation.

- [ ] **Step 4: Map errors and result download**

Use the same stable domain codes as MiniMax but keep Seedance response parsing private to this adapter. Download only HTTPS result URLs returned by the official API, enforce maximum response bytes from capabilities, verify MP4 with ffprobe, then atomically store it.

- [ ] **Step 5: Run adapter tests**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/providers/test_seedance.py`

Expected: PASS with no real network calls.

- [ ] **Step 6: Commit the Seedance adapter**

```bash
git add backend/app/providers/seedance.py backend/tests/providers/test_seedance.py
git commit -m "feat: add experimental seedance adapter"
```

### Task 8: Build Provider Selection, Disclosure and Run Status UI

**Files:**
- Create: `frontend/src/videoGenerationApi.ts`
- Create: `frontend/src/videoGenerationApi.test.ts`
- Create: `frontend/src/GenerationProviderPanel.tsx`
- Create: `frontend/src/GenerationProviderPanel.test.tsx`
- Modify: `frontend/src/models.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: provider capabilities, validation/disclosure responses and generation runs.
- Produces: explicit service selection, per-asset authorization and recoverable run tracking.

- [ ] **Step 1: Write failing capability-language and authorization tests**

```tsx
it("labels reference-video providers as experimental instead of native depth", () => {
  render(<GenerationProviderPanel project={project} providers={[minimax]} />);
  expect(screen.getByText("实验性参考式生成")).toBeInTheDocument();
  expect(screen.queryByText("原生深度控制")).not.toBeInTheDocument();
});

it("cannot submit before accepting the exact asset disclosure", async () => {
  render(<GenerationProviderPanel project={project} providers={[seedance]} />);
  expect(screen.getByRole("button", { name: "提交到 Seedance 2.0" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /发送深度控制素材/ }));
  await user.click(screen.getByRole("button", { name: "确认本次发送" }));
  expect(screen.getByRole("button", { name: "提交到 Seedance 2.0" })).toBeEnabled();
});
```

- [ ] **Step 2: Implement typed API methods and stale-response protection**

Follow existing request-generation guards: changing project/provider/request invalidates in-flight validation and disclosure responses. Poll only queued/running runs; stop on project change, terminal state or unmount.

- [ ] **Step 3: Render a compact capability comparison and exact asset list**

Show support level, accepted control type, maximum duration, credential configuration and data boundary. The disclosure lists filename role, duration, size and SHA-256 abbreviation. Full reference video is absent unless the user explicitly adds it through a separate control.

- [ ] **Step 4: Make submit and retry deliberately billable actions**

The submit button includes provider name. A failed or `submission_unknown` run never auto-retries; show “查询现有任务” when an external ID exists and “重新提交并可能再次计费” only as a separate new action.

- [ ] **Step 5: Enforce mobile read-only and accessibility**

Below 1024px hide credential editing, disclosure acceptance, submit, cancel and retry. Use `aria-live` for run transitions, visible focus and text+icon+color for support/status.

- [ ] **Step 6: Run frontend tests, contrast and build**

Run:

```bash
npm --prefix frontend test -- videoGenerationApi.test.ts GenerationProviderPanel.test.tsx App.test.tsx
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: all PASS.

- [ ] **Step 7: Commit the cloud generation UI**

```bash
git add frontend/src/videoGenerationApi.ts frontend/src/videoGenerationApi.test.ts frontend/src/GenerationProviderPanel.tsx frontend/src/GenerationProviderPanel.test.tsx frontend/src/models.ts frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: add explicit cloud generation controls"
```

### Task 9: Run Paid Provider Acceptance and Promote Independently

**Files:**
- Create: `backend/tests/integration/test_minimax_h3_live.py`
- Create: `backend/tests/integration/test_seedance_live.py`
- Create: `docs/provider-evaluations/minimax-h3.md`
- Create: `docs/provider-evaluations/seedance-2.md`
- Modify: `backend/app/providers/minimax_h3.py`
- Modify: `backend/app/providers/seedance.py`
- Modify: `.scratch/ai-video-reverse-engineer/issues/15-validate-minimax-and-seedance-generation-providers.md`

**Interfaces:**
- Consumes: fixed 2-, 5-, 10-second reference set, dedicated provider accounts and completed adapters.
- Produces: provider-specific evidence and an independent `experimental` or `beta` decision.

- [ ] **Step 1: Define the fixed evaluation record schema**

```text
sampleId
providerId
adapterVersion
modelId
inputAssetHashes
runIds
motionTiming: pass | partial | fail
subjectTrajectory: pass | partial | fail
depthOrdering: pass | partial | fail
cameraRhythm: pass | partial | fail
appearanceDegradation: none | minor | major
repeatability: stable | mixed | unstable
reviewerNotes
```

Use three runs per sample. Do not average these fields into one similarity percentage.

- [ ] **Step 2: Add opt-in live tests with hard spending guards**

Require all of `AI_VIDEO_RUN_PAID_PROVIDER_TESTS=1`, provider credential, `AI_VIDEO_MAX_PAID_RUNS`, and `AI_VIDEO_ACCEPT_PROVIDER_COSTS=yes`. Tests abort before submission when any guard is missing and never create more tasks than the configured maximum.

- [ ] **Step 3: Run MiniMax acceptance separately**

```bash
AI_VIDEO_RUN_PAID_PROVIDER_TESTS=1 \
AI_VIDEO_ACCEPT_PROVIDER_COSTS=yes \
AI_VIDEO_MAX_PAID_RUNS=15 \
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/integration/test_minimax_h3_live.py
```

Save run IDs, model ID, adapter version, input hashes and structured review in `docs/provider-evaluations/minimax-h3.md`.

- [ ] **Step 4: Run Seedance acceptance separately**

```bash
AI_VIDEO_RUN_PAID_PROVIDER_TESTS=1 \
AI_VIDEO_ACCEPT_PROVIDER_COSTS=yes \
AI_VIDEO_MAX_PAID_RUNS=15 \
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/integration/test_seedance_live.py
```

Save equivalent evidence in `docs/provider-evaluations/seedance-2.md`.

- [ ] **Step 5: Apply the promotion rule independently**

A provider becomes `beta` only when every mandatory 2-, 5-, 10-second sample has at least two of three runs rated `pass` for motion timing and subject trajectory, no run has reversed depth ordering, and no mandatory sample is `unstable`. Otherwise it stays `experimental`; record the failed dimensions without removing the adapter.

- [ ] **Step 6: Run the full regression suite**

Run:

```bash
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: all commands exit 0.

- [ ] **Step 7: Resolve Ticket 15 only with both operational and evaluation evidence**

Append contract dates, live command outputs, billed run counts and each independent support decision under issue 15 comments. Mark the issue resolved only when both adapters can submit, resume, download and preserve privacy/idempotency guarantees; either may remain `experimental` after functional completion.

- [ ] **Step 8: Commit evidence and final support levels**

```bash
git add backend/tests/integration docs/provider-evaluations backend/app/providers .scratch/ai-video-reverse-engineer/issues/15-validate-minimax-and-seedance-generation-providers.md
git commit -m "test: validate cloud video generation providers"
```
