# 前置工作台独立审查

审查范围：spec.md 指定的新节点引擎、前端工作台/API、App 集成、旧面板 preparationOnly 与最终生成边界；不修改实现代码，不操作 Git。

## 已复现问题与修复复核

1. **P1：上传素材覆盖未保存编辑。** `PreproductionWorkspace.tsx` 的 upload 允许 dirty 状态上传，随后 accept 整体替换 Workspace。独立 Vitest 探针修改画面提示词为“尚未保存的新提示词”，切素材页上传，再回镜头页，内容恢复为服务端“雨夜街头”。
2. **P1：保存回包覆盖保存期间的新编辑。** 同文件 save 调用期间编辑器仍可修改，回包直接 accept。探针先提交“提交版本”，等待期间改为“提交之后继续编辑”，回包后丢失后者并恢复“提交版本”。轮询晚到回包具有相同风险，已通知实现者检查最新编辑版本。
3. **P2：已有分镜缺少导入入口。** `importAction("shots")` 已实现但没有 UI 调用，前端搜索仅 API/test 出现 import-shots；用户无法完成 spec 第3项已有分镜导入镜头表。
4. **P2：低帧率视频尾帧提取失败。** `preproduction_nodes.py::_extract_frame` 固定 `-sseof -1`。真实 FFmpeg 构造 `color=c=red:s=64x32:r=0.5:d=4` 的4秒 MP4 后执行 last_frame，得到 `NodeExecutionError: 节点未生成有效产物。`，最后一秒没有时间戳落入的帧导致空输出。

## 已执行验证

- `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_preproduction_nodes.py -q`：15 passed；上述低帧率探针额外揭示测试覆盖缺口。
- 前端 CharacterMotionPanel / ReproductionPanel / preproductionApi：30 passed。
- `test_preproduction_boundary.py`：2 passed，最终生成 POST 返回410；准备 GET 可用；不可信来源被拒。
- 临时 `PreproductionWorkspace.reviewprobe.test.tsx`：2 failed，分别复现第1、2项；已交给 UI 实现者用于修复回归。
- 静态确认 App 传入 preparationOnly，工具 slot 已接入既有面板。

## 后端修复期间提醒

已将当前中间版本 checks 缺 project_id 参数、_referenced_assets 对 plain ID split 越界两处错误直接通知后端实现者；不将未完成编辑的中间状态当作最终结论。原已知后端问题待实现者修复完成后复核。

## 19:00 修复复核

- 上传、保存回包与旧轮询回包的3项独立探针全部通过；UI采用 dirty 上传禁用、保存期间禁用交互/保留更新草稿、revision单调保护。已有分镜导入按钮已补。临时探针删除，等效回归并入正式测试。
- 尾帧增加末秒无帧时逐帧覆写同一PNG的有界回退；新增0.5fps像素验证通过。
- 独立复跑后端 API+nodes+boundary 合计24 passed；前端工作台正式7项+独立探针3项通过（后续正式测试已扩充，未以此前结果冒充新计数）。
- 后端 checks 参数与 ZIP 素材集合修复已核实。排队下游期间重跑上游的独立API探针返回409，避免依赖被运行中失效。
- 尚提醒后端补完整 prompt context：当前文本拼接遗漏 mustPreserve/aspect/duration/negativePrompt，需求中的必须保留项不应丢失。

## 最终复核（19:03）

**本次审查发现的问题均已修复，当前无未解决的阻断项。**

- 工作台运行期间锁定编辑但保留导航；未运行的前序节点可预先选为输入；未就绪依赖不可运行；CAS冲突有显式“放弃修改并重新读取”入口。独立复跑 Workspace 11项与 API 5项，共16 passed。
- 导入分镜→编辑标题保存→再次导入的独立API探针返回200，镜头始终1个，未重复。
- prompt已包含完整带标签的需求字段、必须保留项、正负镜头提示及节点文字。对应后端回归通过。
- 审查额外复现了损坏MP4容器头有效但帧数据全部损坏仍可上传的问题；现已补有超时的真实解码验证。用同一篡改mdat探针复查得到HTTP422“媒体无法解码有效内容”。
- 最终后端API定向回归7 passed；此前节点16项及边界2项已通过，本轮没有再变更这两个模块，不重复计数或重跑。

审查仅使用临时目录和测试夹具，未改主代理浏览器项目或现有用户媒体；临时前端探针已移除，等效测试由实现者并入正式测试文件。未进行Git操作。
