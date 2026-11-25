# Infinite Canvas source reference and adaptation

- Repository: https://github.com/basketikun/infinite-canvas
- Fixed commit: `dab19adc0847e32e39b7fc8ff90cb392561fb826`
- License: MIT; copyright 2026 basketikun, retained in LICENSE.
- Source examined: `web/src/components/canvas/canvas-node-hover-toolbar.tsx`, `canvas-image-toolbar-tools.tsx`, `canvas-node.tsx`.
- The current correction is adapting the node-near action definitions to project Asset IDs and React/XYFlow. The canvas has an explicit design exception: the old site design system does not constrain its layout, node appearance or floating tools. Ant Design/i18n/custom canvas store are not imported wholesale.
- Exact implemented/copy ranges are recorded in the task progress after code review. Unimplemented image processing, batch outputs and other actions remain in `.scratch/ai-creative-canvas/parity-acceptance.md`; retaining this notice does not mean the full toolbar was ported.

## 2026-09-30 visual shell adaptation

`CreativeCanvasNodes.tsx` / `creativeCanvas.css` now adapt Toonflow nodeSkeleton title/card/floating slots and imageGenerationNode prompt footer; the canvas shell and light palette reuse Infinite Canvas canvas-theme/top-bar/toolbar source at the pinned revisions above. React/XYFlow and current project asset/run APIs replace framework-specific bindings. See `.scratch/ai-creative-canvas/progress.md` for exact source files, tests, browser evidence, and unclosed parity gaps. MIT notices remain in the source header and vendor licenses. This is not a full-product or pixel-perfect parity claim.
