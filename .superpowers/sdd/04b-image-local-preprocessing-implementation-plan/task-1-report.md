# Task 1 实施报告：本地预处理领域模型媒体感知化

## 已实施

- 新增 `MediaType`、视频/图片阶段联合及 `stage_order_for(media_type)`；保留 `STAGE_ORDER` 作为视频兼容别名。
- `new_local_preprocessing` 改为必须显式传入 `media_type`，并按对应阶段表初始化。
- 新增以 `mediaType` 为判别字段的 `ImageProxySummary`、`VideoProxySummary` 与 `ProxySummary`；`AnalysisProxySummary` 保留为视频摘要兼容别名。
- 存储层按媒体类型映射阶段产物：图片使用 `normalized.png`、`analysis-proxy.jpg`、`manifest.json`。
- 存储恢复和清理均通过当前任务的 `stage_order_for(preprocessing.mediaType)` 工作；manifest 同时验证 `mediaType`、算法版本及 `sourceReferenceMediaId`。
- 所有受构造器无默认 `media_type` 影响的现有视频调用点已显式传入 `"video"`；清理调用点传入任务的媒体类型。

## RED

任务简报指定命令：

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py
```

该工作树没有 `.venv/bin/pytest`，输出为：`zsh:1: no such file or directory: .venv/bin/pytest`。

使用仓库共享环境执行等效命令：

```sh
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py
```

结果：`6 failed, 31 passed`。

关键失败原因：图片任务仍被初始化为五个视频阶段；图片摘要被旧视频摘要字段强制校验；图片的 `normalized.png` / `analysis-proxy.jpg` / 新 manifest 不被旧视频阶段文件映射识别。这证明 RED 是目标行为缺失，而非过时的构造器签名失败。

## GREEN 与验证

```sh
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py
```

结果：`37 passed in 0.11s`。

额外执行：

```sh
git diff --check
PYTHONPATH=backend ../../.venv/bin/python -m compileall -q backend/app
```

两项均以退出码 0 完成。

## 后续关注点

全量后端套件运行结果为 `611 passed, 21 failed, 8 skipped`。失败均属于 Task 3/04a 的既定后续契约衔接：现有视频 runner 仍写旧 manifest 字段 `sourceReferenceVideoId`，以及 04a 旧持久化迁移尚未给历史 `proxySummary` 写入 `mediaType` 判别字段。Task 1 的存储校验按新契约拒绝这些旧产物是预期行为；未在本任务提前实现 runner/main 的图片运行时分发或重复 04a 迁移职责。

## 审查修复（round 1）

### 根因与修复

- `ProxySummary` 是以 `mediaType` 判别的联合；旧完成视频项目的 `proxySummary` 没有该字段，Pydantic 会在选择 `VideoProxySummary` 之前报 `union_tag_not_found`。
- 视频 runner 的 decode/最终 manifest 仍写 `sourceReferenceVideoId`，与本任务新存储校验的 `mediaType` 和 `sourceReferenceMediaId` 契约不一致。
- 深度捕捉 API 的已完成预处理夹具仍写旧 manifest，因此新校验正确拒绝其前置条件，造成后续深度测试连锁失败。

修复内容：

- 项目读取迁移仅在 `localPreprocessing.mediaType == "video"` 且摘要是对象时，以 `setdefault` 补入 `proxySummary.mediaType = "video"`。该操作不猜测图片类型、不会覆盖当前字段，并且可重复执行。
- 视频 runner 的 decode 与最终 manifest 均改为写入 `mediaType: "video"` 和 `sourceReferenceMediaId`。
- 所有受此持久化契约影响的测试工件改写为新字段。

### RED

```sh
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests/test_project_migrations.py backend/tests/test_projects_api.py backend/tests/test_local_preprocessing_runner.py backend/tests/test_local_preprocessing_api.py backend/tests/test_local_preprocessing_storage.py backend/tests/test_local_preprocessing.py
```

完整输出：

```text
..F............................F.....................F................ss [ 52%]
sssss..F.........................................................        [100%]
FAILED backend/tests/test_project_migrations.py::test_migrates_legacy_video_proxy_summary_to_discriminated_union_idempotently
  KeyError: 'mediaType'
FAILED backend/tests/test_projects_api.py::test_five_legacy_project_json_fixtures_migrate_and_next_write_uses_only_new_fields[completed]
  pydantic_core._pydantic_core.ValidationError: Unable to extract tag using discriminator 'mediaType'
FAILED backend/tests/test_local_preprocessing_runner.py::test_full_run_writes_only_proxy_contract_and_not_source_metadata
  KeyError: 'mediaType'
FAILED backend/tests/test_local_preprocessing_api.py::test_opening_project_returns_a_valid_completed_preprocessing_unchanged
  已完成项目的 manifest 缺少新身份字段，被 storage 判为 assessment_failed。
4 failed, 126 passed, 7 skipped in 1.13s
```

这四个失败分别证明旧摘要迁移缺失、旧项目读取失败、真实 runner 未写新字段、视频完成结果不能通过存储校验。

首次修复后的全量验证仍有同类夹具回归：

```sh
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests
```

完整输出：

```text
18 failed, 615 passed, 8 skipped, 1 warning in 9.27s
```

18 个失败都来自 `backend/tests/test_depth_capture_api.py` 的已完成视频预处理夹具仍写旧 manifest；该夹具迁移后不再失败。

### GREEN

审查指定的覆盖集：

```sh
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests/test_project_migrations.py backend/tests/test_projects_api.py backend/tests/test_local_preprocessing_runner.py backend/tests/test_local_preprocessing_api.py backend/tests/test_local_preprocessing_storage.py backend/tests/test_local_preprocessing.py
```

完整输出：

```text
......................................................................ss [ 52%]
sssss............................................................        [100%]
130 passed, 7 skipped in 1.02s
```

深度捕捉回归集：

```sh
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests/test_depth_capture_api.py
```

完整输出：

```text
......................................                                   [100%]
38 passed in 0.53s
```

全量后端验证：

```sh
PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests
```

完整输出：

```text
........................................................................ [ 11%]
........................................................................ [ 22%]
........................................................................ [ 33%]
..........................................................s............. [ 44%]
........................................................................ [ 56%]
........................................................................ [ 67%]
..............sssssss................................................... [ 78%]
........................................................................ [ 89%]
.................................................................        [100%]
633 passed, 8 skipped in 4.59s
```

静态验证：

```sh
git diff --check
PYTHONPATH=backend ../../.venv/bin/python -m compileall -q backend/app
```

两项均以退出码 0 完成。
