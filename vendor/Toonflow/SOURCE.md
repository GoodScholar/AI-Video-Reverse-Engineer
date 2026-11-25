# Toonflow source reuse

- Repository: https://github.com/HBAI-Ltd/Toonflow-app
- Fixed commit: `d86cce6b689916ab73f75bc4c1f42a2a7424516c`
- Reused source: `packages/nodeScaffold/src/connection.ts`
- Adapted into: `frontend/src/creativeCanvasConnection.ts`
- License: MIT (`LICENSE` in this directory)

The original Vue Flow type and node-context validation are intentionally not copied. The React/XYFlow canvas only reuses the generic `getDataTypes` and `isTypeCompatible` compatibility algorithm.

## Initial node layout adaptation (superseded acceptance baseline)

- Read source: `packages/nodes/imageGenerationNode/src/index.vue`, `packages/nodes/imageNode/src/index.vue`, and `packages/nodes/textNode/src/index.vue` at the fixed commit above.
- Retained behavior: media is rendered directly in a bounded `object-fit: contain` preview; text and prompt editing stay inside the node; generation has preview, prompt, fixed size selector, action footer, image-result/error state, and a nearby service-settings action.
- Adapted implementation: `frontend/src/CreativeCanvasNodes.tsx` and `frontend/src/creativeCanvas.css` use React/XYFlow nodes and project asset IDs. Vue templates, Element Plus widgets, provider model groups, worktree file APIs, cancellation model, and their node runtime are not portable and are not copied.

The initial adaptation omitted substantial behavior and is **not** accepted as full alignment. Framework differences require adapters; they do not justify removing the reference's primary interactions. The ongoing correction reads `useNodeReferences.ts`, `useNodeGeneration.ts`, `nodeSkeleton.vue`, `components/referenceItem.vue`, and `components/promptInput.vue` at the same fixed commit. Exact implemented behavior and remaining differences are tracked in `.scratch/ai-creative-canvas/parity-acceptance.md` and `progress.md`.

## Infinite Canvas media-action reference

- Repository: https://github.com/basketikun/infinite-canvas
- Fixed commit: `dab19adc0847e32e39b7fc8ff90cb392561fb826`
- Read source: `web/src/components/canvas/canvas-node.tsx` and `web/src/components/canvas/canvas-node-hover-toolbar.tsx`.
- License: MIT. The project has a much larger Ant Design/i18n/store-dependent node implementation, so it is not directly imported. Its direct media rendering, non-draggable media treatment, and node-near actions informed the bounded React/XYFlow preview and settings/view actions above.

## 2026-09-30 visual shell adaptation

`CreativeCanvasNodes.tsx` / `creativeCanvas.css` now adapt Toonflow nodeSkeleton title/card/floating slots and imageGenerationNode prompt footer; the canvas shell and light palette reuse Infinite Canvas canvas-theme/top-bar/toolbar source at the pinned revisions above. React/XYFlow and current project asset/run APIs replace framework-specific bindings. See `.scratch/ai-creative-canvas/progress.md` for exact source files, tests, browser evidence, and unclosed parity gaps. MIT notices remain in the source header and vendor licenses. This is not a full-product or pixel-perfect parity claim.

## 2026-09-30 unified Toonflow baseline and interaction reuse

The user selected Toonflow as the unified visual baseline. The earlier mixed visual shell above is historical, not the current acceptance standard. The following fixed-revision sources are now adapted into the React/XYFlow host:

- `canvasControls.vue`, `nodeSearch.vue`, `canvasShortcuts.ts`: page controls and node/group search.
- `selectionToolbar.vue`, `selectionNodes.ts`, `arrangeCanvas.ts`: nested group creation/merge/ungroup, relative coordinate conversion, drag reparenting and Dagre connected-component packing. Dagre 3.1.1 is used with its MIT package license.
- `components/promptInput.vue`: `CreativeCanvasPrompt.tsx` directly retains the x-sender editor, reference-tag normalization, cursor/composition safeguards and scoped cleanup, replacing Vue watchers with a React adapter. The exact `x-sender@1.4.6` dependency is MIT, Copyright 2025-present jianfv (license distributed in the package). Browser insertion synchronization is still under verification.
- `textNode/src/index.vue`: plain-text Blob download lifecycle and full-screen text editing; native modal dialog replaces Element Plus. Model generation remains outstanding.

This reuse is partial and does not close the complete feature/visual acceptance matrix.
