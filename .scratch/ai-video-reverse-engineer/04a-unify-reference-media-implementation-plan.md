# Unified Reference Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将视频专用项目模型升级为图片/视频联合参考素材，并交付可事务性替换的图片上传与统一前端入口。

**Architecture:** 新建媒体共享模型和图片探测模块，保留现有 FFprobe 视频探测逻辑。项目读取在模型校验前迁移旧 JSON；统一上传接口按真实内容分发到图片或视频探测器，并以一次项目写入提交新素材。

**Tech Stack:** Python 3.9、FastAPI 0.115.12、Pydantic、Pillow 11.3.0、React 18、TypeScript 5.7、Vitest。

**Spec:** `.scratch/ai-video-reverse-engineer/04-image-reference-and-multi-provider-analysis-design.md`

## Global Constraints

- 单项目只允许一个 `referenceMedia`，类型为 `image | video`。
- 图片只支持 JPG/JPEG、PNG、WebP；最大 30,000,000 字节；宽高 256～5760；比例 2:5～5:2；拒绝动画 WebP。
- 视频限制保持 MP4/MOV、2～10 秒、最大 200,000,000 字节、最低 480P、最高 UHD 4K。
- 旧 `referenceVideo` 和 `sourceReferenceVideoId` 必须幂等迁移，用户无需手工操作。
- 替换失败必须保留旧素材与全部旧结果；运行中的任务禁止替换。
- 仅修改本计划涉及的媒体模型、上传、项目迁移、参考素材界面及相应测试。
- 实现与测试使用 `gpt-5.6-terra/high`；阶段审查使用 `gpt-6 Astra/medium`。

---

### Task 1: 建立 ReferenceMedia 联合模型与旧数据迁移

**Files:**
- Create: `backend/app/reference_media.py`
- Create: `backend/app/project_migrations.py`
- Modify: `backend/app/reference_video.py`
- Modify: `backend/app/local_preprocessing.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_project_migrations.py`
- Modify tests: `backend/tests/test_projects_api.py`, `backend/tests/test_video_probe.py`

**Interfaces:**
- Produces: `ReferenceImage`, `ReferenceVideo`, `ReferenceMedia`, `migrate_project_payload(payload: object) -> object`。
- Produces: `LocalPreprocessing.sourceReferenceMediaId: str` 与 `mediaType: Literal['image', 'video']`。
- Consumers: Task 2 的存储路径和上传提交；计划 04b 的媒体分支预处理。

- [ ] **Step 1: 写旧项目迁移失败测试**

```python
def test_legacy_video_project_is_migrated_before_validation():
    legacy = {
        'id': 'project-1', 'name': '旧项目',
        'createdAt': '2026-09-10T10:00:00+00:00',
        'updatedAt': '2026-09-10T10:00:00+00:00',
        'referenceVideo': {
            'id': 'video-1', 'originalName': 'clip.mp4', 'format': 'mp4',
            'sizeBytes': 10, 'durationSeconds': 5, 'width': 1280,
            'height': 720, 'frameRate': 24,
        },
        'localPreprocessing': None,
    }
    migrated = migrate_project_payload(legacy)
    assert migrated['referenceMedia']['type'] == 'video'
    assert 'referenceVideo' not in migrated
```

- [ ] **Step 2: 运行测试并确认因迁移函数缺失而失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_project_migrations.py`

Expected: FAIL，导入 `migrate_project_payload` 失败。

- [ ] **Step 3: 实现最小联合模型和幂等迁移**

```python
class ReferenceImage(BaseModel):
    type: Literal['image'] = 'image'
    id: str
    originalName: str
    format: Literal['jpeg', 'png', 'webp']
    sizeBytes: int = Field(gt=0, le=30_000_000)
    width: int = Field(ge=256, le=5760)
    height: int = Field(ge=256, le=5760)
    hasTransparency: bool

class ReferenceVideo(BaseModel):
    type: Literal['video'] = 'video'
    # 保留现有视频字段和校验器。

ReferenceMedia = Annotated[Union[ReferenceImage, ReferenceVideo], Field(discriminator='type')]
```

迁移函数复制输入字典；仅在新字段缺失时移动旧字段，并把旧预处理来源 ID 改名。对已经迁移的数据返回等价结构，不覆盖已有新字段。

- [ ] **Step 4: 将项目读取接到迁移函数并更新前后端字段测试**

在 `_read_projects` 中对每个原始字典执行 `migrate_project_payload` 后再调用 `Project.model_validate`。补充空项目、完成预处理、失败预处理和重复迁移测试。

- [ ] **Step 5: 运行领域与项目 API 测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_project_migrations.py backend/tests/test_projects_api.py backend/tests/test_video_probe.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`refactor: unify project reference media model`

### Task 2: 增加图片探测并统一上传事务

**Files:**
- Create: `backend/app/reference_image.py`
- Create: `backend/app/reference_media_storage.py`
- Modify: `backend/app/main.py`
- Delete after callers migrate: `backend/app/reference_video_storage.py`
- Test: `backend/tests/test_reference_image.py`
- Test: `backend/tests/test_reference_media_upload_api.py`
- Modify tests: `backend/tests/test_reference_video_upload_api.py`, `backend/tests/test_local_preprocessing_api.py`
- Modify: `backend/pyproject.toml`, `backend/requirements.lock`

**Interfaces:**
- Consumes: `ReferenceImage | ReferenceVideo` from Task 1。
- Produces: `probe_reference_image(path, original_name, size_bytes) -> ReferenceImageFacts`。
- Produces: `managed_reference_media_path(data_dir, project_id, media) -> Path`。
- Produces: `PUT /api/projects/{project_id}/reference-media`。

- [ ] **Step 1: 锁定 Pillow 并写图片边界测试**

在两个依赖文件加入 `Pillow==11.3.0`；该版本保持项目现有 Python `>=3.9` 下限。测试必须覆盖 JPEG、PNG、WebP、实际透明像素、动画 WebP、伪扩展名、30,000,001 字节、255/5761 像素和比例越界。

```python
def test_animated_webp_is_rejected(tmp_path):
    path = make_animated_webp(tmp_path)
    with pytest.raises(ReferenceMediaError) as captured:
        probe_reference_image(path, 'animated.webp', path.stat().st_size)
    assert captured.value.code == 'animated_image_unsupported'
```

- [ ] **Step 2: 运行新测试并确认失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_reference_image.py backend/tests/test_reference_media_upload_api.py`

Expected: FAIL，图片探测器和新路由尚不存在。

- [ ] **Step 3: 实现真实内容探测和安全关闭文件**

使用 `PIL.Image.open`、`verify()` 和重新打开后的完整 `load()`；从 `image.format` 判断格式，从 `n_frames` 拒绝动画，从应用 EXIF 方向后的尺寸进行边界判断。`hasTransparency` 必须检查 Alpha/透明索引是否存在实际透明像素。

- [ ] **Step 4: 将暂存、路径和提交函数泛化到 ReferenceMedia**

```python
def managed_reference_media_path(data_dir: Path, project_id: str, media: ReferenceMedia) -> Path:
    validate_storage_id(project_id)
    validate_storage_id(media.id)
    return checked_child(data_dir, 'project-files', project_id, 'reference-media', f'{media.id}.{media.format}')
```

复用现有分块暂存和 `os.replace` 事务语义。新项目只写入新目录；路径解析器先检查 `reference-media/<id>.<format>`，不存在时仅对迁移得到的视频检查旧 `reference-videos/<id>.<format>`。替换旧视频时使用同一解析器定位并清理旧文件，不能因为 JSON 字段已迁移而丢失文件位置。

- [ ] **Step 5: 实现统一上传路由与路径中间件**

客户端快速校验只负责反馈，服务端始终执行真实内容校验。新路由接受一个 `file`；根据签名字节和探测结果分发图片/视频。锁定、回滚、旧产物失效规则复用现有提交临界区。

- [ ] **Step 6: 运行上传、回滚和既有视频回归测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_reference_image.py backend/tests/test_reference_media_upload_api.py backend/tests/test_reference_video_upload_api.py backend/tests/test_local_preprocessing_api.py`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: accept image and video reference media`

### Task 3: 将前端参考视频面板升级为参考素材面板

**Files:**
- Create: `frontend/src/referenceMediaApi.ts`
- Create: `frontend/src/ReferenceMediaPanel.tsx`
- Create: `frontend/src/referenceMediaApi.test.ts`
- Create: `frontend/src/ReferenceMediaPanel.test.tsx`
- Modify: `frontend/src/models.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/styles.css`
- Delete after imports migrate: `frontend/src/referenceVideoApi.ts`, `frontend/src/ReferenceVideoPanel.tsx`, corresponding tests

**Interfaces:**
- Consumes: `Project.referenceMedia` 和统一上传路由。
- Produces: `uploadReferenceMedia(projectId: string, file: File): Promise<Project>`。
- Produces: `<ReferenceMediaPanel project ... />`，保持既有上传锁和焦点恢复属性。

- [ ] **Step 1: 写联合类型、API 路径和图片展示失败测试**

```ts
const image: ReferenceImage = {
  type: 'image', id: 'image-1', originalName: 'hero.png', format: 'png',
  sizeBytes: 1024, width: 1200, height: 1600, hasTransparency: true,
};
expect(screen.getByText('参考图片')).toBeInTheDocument();
expect(screen.getByText('1200×1600')).toBeInTheDocument();
expect(screen.getByText(/透明区域将以白色背景处理/)).toBeInTheDocument();
```

- [ ] **Step 2: 运行测试并确认因新模块缺失而失败**

Run: `npm --prefix frontend test -- referenceMediaApi.test.ts ReferenceMediaPanel.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 实现 TypeScript 可辨识联合与统一 API**

```ts
export type ReferenceImage = {
  type: 'image'; id: string; originalName: string; format: 'jpeg' | 'png' | 'webp';
  sizeBytes: number; width: number; height: number; hasTransparency: boolean;
};
export type ReferenceMedia = ReferenceImage | ReferenceVideo;
```

上传函数请求 `/api/projects/${encodeURIComponent(projectId)}/reference-media`，网络错误文案改为“无法上传并校验参考素材”。

- [ ] **Step 4: 实现单入口选择、类型化元数据和替换确认**

文件选择器接受 `.jpg,.jpeg,.png,.webp,.mp4,.mov`。客户端只按扩展名和大小给即时提示；服务端结果决定最终类型。跨类型替换确认必须列明本地预处理及后续结果会失效。

- [ ] **Step 5: 接入 App 并更新样式命名**

把组件、回调和 CSS 从 `reference-video-*` 精确迁移到 `reference-media-*`，保持 1024px 只读边界、44px 操作目标、可见焦点和无浏览器原生确认框。

- [ ] **Step 6: 运行组件与应用回归测试**

Run: `npm --prefix frontend test -- referenceMediaApi.test.ts ReferenceMediaPanel.test.tsx App.test.tsx`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: add unified reference media panel`

### Task 4: 更新领域文档并完成增量验收

**Files:**
- Modify: `PRODUCT.md`
- Modify: `CONTEXT.md`
- Create: `docs/adr/0006-reference-media-and-media-specific-preprocessing.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–3 已通过测试的外部行为。
- Produces: 统一术语、输入边界、迁移决定和运行说明。

- [ ] **Step 1: 更新产品与领域术语**

新增“参考素材”和“参考图片”，保留“参考视频”作为子类型；明确单项目二选一以及图片动作/运镜不是可观察事实。

- [ ] **Step 2: 记录 ADR**

ADR 必须说明为何不建立平行图片项目字段、如何迁移旧 JSON、为何不把图片转成静态视频。

- [ ] **Step 3: 更新 README 的依赖、格式、目录和隐私说明**

加入 Pillow、图片限制和 `reference-media/` 路径；保留视频 FFmpeg 前置条件。

- [ ] **Step 4: 运行完整验收**

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 全部通过；前端构建不再引用 `referenceVideo` 或 `/reference-video`。

- [ ] **Step 5: 验证检查点**

建议提交信息：`docs: define unified reference media boundary`
