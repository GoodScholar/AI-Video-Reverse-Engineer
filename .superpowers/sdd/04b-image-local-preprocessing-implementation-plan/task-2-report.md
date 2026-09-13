# Task 2 报告：图片纯算法与产物编码

## 范围与产物

- 新增 `backend/app/image_preprocessing.py`，只包含 Pillow 解码、标准化、JPEG 代理编码和静态图片适用性检查；不读取项目目录、不处理 HTTP，也不接触任务队列。
- 新增 `backend/tests/test_image_preprocessing.py`。测试在临时目录构造真实 PNG 输入，而非依赖任务简报中互相不一致的组合 fixture 文件名。
- 四个供 Task 3 消费的函数为 `inspect_image`、`normalize_image`、`write_analysis_proxy` 和 `assess_image_reproducibility`。返回事实对象不保存来源文件名或路径。

## TDD 证据

### RED

先新增 `backend/tests/test_image_preprocessing.py`，再运行：

```text
PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/test_image_preprocessing.py
```

结果为预期的目标模块缺失：`ModuleNotFoundError: No module named 'app.image_preprocessing'`。工作树本身没有 `.venv`，因此计划中的相对 `.venv/bin/pytest` 无法启动；使用仓库根目录已锁定 Pillow 11.3.0 的虚拟环境取得有效 RED。

### GREEN

完成最小实现后，定向测试通过：

```text
5 passed in 0.15s
```

覆盖的真实行为：

- EXIF 方向 6 后的实际尺寸与像素位置；
- 有效 sRGB ICC 输入、RGB 输出、实际透明像素的白底合成；
- PNG 的 EXIF、ICC 和文本来源元数据清除；
- JPEG 代理最长边缩至 2048px、RGB/EXIF 状态、真实字节数不超过 8,000,000；
- 小图不放大；
- 适用性检查重新打开两个产物，且评估对象不含原文件名或绝对路径。

## 回归验证

```text
PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/test_image_preprocessing.py
5 passed in 0.15s

PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/test_reference_image.py backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py
56 passed in 0.14s

PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests
638 passed, 8 skipped in 5.18s

git diff --check
通过
```

## Task 3 衔接与关注点

- `normalize_image` 只写调用方给定的目标文件；Task 3 负责将该目标设为阶段临时文件并原子提交。
- `write_analysis_proxy` 按 90 到 60 的质量序列编码，始终保留不超过 8,000,000 字节的候选；无法满足时返回稳定的 `proxy_generation_failed`。
- `assess_image_reproducibility(normalized_path, proxy_path)` 重新验证 PNG/JPEG 的可读性与技术约束，并明确将镜头、运动、主体和复杂交互标为 `not_assessed`，不从静态参考图片臆测时间事实。
- `manifest.json` 由 Task 3 生成，因而必须在那里继续断言序列化清单不含原文件名或绝对路径；本层事实对象本身不携带这两类数据。

## Fix round 1/5：CMYK ICC 转换

审查发现原实现先将原图转换为 RGB/RGBA，再把该模式与源 ICC 传给 `ImageCms.profileToProfile`。这会令有效 CMYK ICC 的颜色模式不匹配，触发 `PyCMSError` 后退化为普通 RGB 转换。

### RED

新增仓库固定 fixture `backend/tests/fixtures/images/generic-cmyk.icc`（52,280 字节，SHA-256 为 `0c8a584b288a306eac9e1d3f1e68bc1b64331c717ceb051420e6257f17b3509a`），并以其构造真实 CMYK JPEG。新增测试先确认普通转换为 `(226, 143, 69)`，再要求标准化 PNG 的色彩管理结果为 `(211, 149, 89)`。

修复前运行：

```text
PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/test_image_preprocessing.py
1 failed, 5 passed in 0.18s

E assert (226, 143, 69) == (211, 149, 89)
```

该失败证明代码错误地走了普通 `convert("RGB")` 路径，而不是测试配置或路径问题。

### GREEN

`_convert_to_srgb` 现会在源模式为 RGB、RGBA、CMYK、LAB 或 L 时，先将该源模式与嵌入 ICC 交给 `ImageCms.profileToProfile`，目标固定为 sRGB 的 RGB/RGBA；仅在无 ICC、模式不支持或 ICC 无效时才执行普通模式转换。这样透明度处理仍发生在色彩转换之后。

修复后完整命令与结果：

```text
PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/test_image_preprocessing.py
6 passed in 0.18s

PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/test_reference_image.py backend/tests/test_local_preprocessing.py backend/tests/test_local_preprocessing_storage.py
56 passed in 0.14s

git diff --check
通过
```
