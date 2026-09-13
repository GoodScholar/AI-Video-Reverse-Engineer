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
