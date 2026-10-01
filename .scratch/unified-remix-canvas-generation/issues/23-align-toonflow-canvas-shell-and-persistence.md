# 23: 对齐 Toonflow 画布主体并贯通持久化

**What to build:** 以 Toonflow 固定提交 `d86cce6b689916ab73f75bc4c1f42a2a7424516c` 为唯一主体 UI 基准，交付独立全幅画布、顶部区域、添加入口、基础节点、就近浮动工具、画布控制和按需编辑器，并用一个项目素材节点贯通创建、保存、刷新恢复、版本冲突和草稿保护。

**Blocked by:** 01: 冻结五张验收矩阵与三条金标接缝; 03: 统一项目素材结果安装与版本失效传播

**Status:** claimed

## Scope

- 画布从独立入口打开，不依赖制作镜头；只把导航返回、项目身份和共享状态接入现有应用。
- 直接参考并在许可允许时复制 Toonflow 的主体页面、节点、浮动工具和编辑器结构，不受旧站点侧栏/卡片外观约束。
- 建立画布创建、命名、切换、保存、刷新恢复、草稿和并发冲突的完整纵向路径。
- 一个项目素材节点以稳定素材身份显示、预览并保存位置/尺寸。

## Source reuse prerequisite

- 在编码前完整阅读 Toonflow 固定提交对应页面、组件、状态、持久化和交互源码，记录源文件、MIT 许可、复制范围和必要适配。
- 同时核对当前画布实现和现有草稿/版本冲突机制，优先兼容数据而非另建空白画布。

## Acceptance criteria

- [ ] 独立全幅画布按固定参考具备顶栏、添加入口、节点、浮动工具、画布控制和按需编辑器主体结构。
- [ ] 用户可创建、命名、切换和保存画布，刷新后恢复相同画布与节点视图。
- [ ] 项目素材节点引用稳定素材身份，可预览且不要求归属 Shot。
- [ ] 多窗口并发保存被版本校验阻止，用户可恢复、导出或放弃草稿，不静默覆盖。
- [ ] 旧画布可兼容读取或幂等迁移，失败不覆盖原数据。
- [ ] UI 明确当前只是已实现能力，不把后续节点或真实生成标为已支持。

## Verification evidence

- 同视口、同节点数据的参考操作对照和截图辅助证据。
- UI/领域测试覆盖创建、保存、刷新、冲突、草稿和素材节点预览。
- E1 记录每个复制/改造组件的固定提交、许可证和适配点。

## Out of scope

- 生成节点、批量执行和 Agent 操作。
- 为保持旧站点视觉删减 Toonflow 主体交互。

## Evidence and current implementation audit (2026-10-02)

### Fixed-source and licence audit (E1 candidate; not yet a registered formal verifier)

- Retrieved and checked out `https://github.com/HBAI-Ltd/Toonflow-app.git` at the required immutable commit `d86cce6b689916ab73f75bc4c1f42a2a7424516c`; the upstream `LICENSE` is MIT, Copyright (c) 2026 HBAI-Ltd. The repository's `vendor/Toonflow/` is an attribution directory, not a source checkout; its current Git `HEAD` is the host repository and must not be treated as the fixed upstream source.
- Fully inspected the fixed source's `apps/web/src/pages/workspace/panels/canvas/{canvasHost.vue,index.vue,components/canvasControls.vue,components/canvasMenu.vue,components/nodeSearch.vue,components/selectionToolbar.vue}` and `packages/nodeScaffold/{nodeSkeleton.vue,components/promptInput.vue}`, plus the text/image/image-generation node implementations.
- Adaptation mapping: `frontend/src/CreativeCanvas.tsx`, `CreativeCanvasNodes.tsx`, `creativeCanvas.css` and `creativeCanvasPrompt.tsx` retain the full-screen canvas shell, compact top region, add/search entrances, node-near actions, view controls and on-demand editors. `backend/app/creative_canvas.py` and `creative_canvas_api.py` supply project-scoped canvas identity, atomic document/catalog persistence, default-canvas compatibility, per-canvas run isolation, soft deletion, and revision CAS. Vue Flow/Element Plus/workspace-file APIs were not copied because this application uses React 18, XYFlow and FastAPI; the adaptation keeps project-asset IDs and the existing application navigation.
- The only direct reusable pure algorithm remains the separately attributed `packages/nodeScaffold/src/connection.ts` adaptation in `frontend/src/creativeCanvasConnection.ts`; this ticket did not claim a direct copy of Vue UI code.

### Reused work audit and deterministic checks (E2 candidate)

- The CreativeCanvas backend/frontend files and prior canvas records were already untracked before this ticket and also contain later-ticket work (groups, text/media generation, batches and editing). They were preserved without reset, clean, deletion or whole-file rewrite. They cannot be put into a Ticket 23-only commit without falsely including Tickets 24–28.
- `backend/.venv/bin/python -m pytest tests/test_creative_canvas.py -q` from `backend/`: 76 passed (one third-party Starlette deprecation warning). Coverage includes default-canvas compatibility and isolation, soft-delete/restore asset references, stale revision rejection, and stable media IDs.
- `npm test -- --run src/CreativeCanvas.test.tsx src/creativeCanvasApi.test.ts` from `frontend/`: 69 passed. Coverage includes catalog switch, preserved local drafts, save retry with the returned revision, and the canvas-scoped API contract.
- `npm run build` from `frontend/`: passed; Vite reported only the existing bundle-size warning.

### Temporary-project browser operation (E4 candidate; screenshots are auxiliary)

- To avoid modifying an existing project, a temporary backend data directory was used. At a 1280×900 viewport, created a temporary project, opened the independent canvas, added a text node and the stable project asset `grounds.jpg`, saved, refreshed, and verified the same nodes were restored. Evidence: `../../ai-creative-canvas/evidence/ticket23-nonempty-saved-1280x900.png` and `../../ai-creative-canvas/evidence/ticket23-refresh-restored-1280x900.png`.
- In a second browser session, saved a concurrent text-node edit. The first session then attempted to save its own edit; the server rejected the stale revision and the UI displayed that the local draft was retained, offered explicit discard/reload, and after explicit confirmation restored the other window's saved version. Evidence: `../../ai-creative-canvas/evidence/ticket23-conflict-draft-1280x900.png` and `../../ai-creative-canvas/evidence/ticket23-conflict-recovered-1280x900.png`.
- Created and switched to a named second canvas; it opened as an isolated document. Evidence: `../../ai-creative-canvas/evidence/ticket23-second-canvas-1280x900.png`. The shell baseline is recorded in `../../ai-creative-canvas/evidence/ticket23-toonflow-shell-1280x900.png`.

### Formal acceptance status

Ticket-level code and temporary-project operation are present, but this ticket is **not resolved**: the frozen acceptance registry deliberately has no production E1/E2/E4 truth-source verifier. Therefore C01–C03 remain formally blocked in `matrix-contract.json`; screenshots, test output and this record are not promoted to “通过”. Ticket 24 remains blocked by the formal Ticket 23 dependency until an independently reviewable E1/E2/E4 verifier and acceptance entry exist. No real generation, batch execution, Agent action or whole-product alignment is claimed here.
