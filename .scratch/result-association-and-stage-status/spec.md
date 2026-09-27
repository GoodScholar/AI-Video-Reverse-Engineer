# 镜头结果关联与阶段缺项定位

Status: resolved

## Problem Statement

创作者能在前置工作台绑定素材、运行镜头步骤、上传多个结果候选并将采用的结果放入本地时间线，但这些信息分散在不同页面。方案修改后，候选只显示“方案已变化”，无法回看它关联时的方案内容；创作者也难以从候选直接找到其关联素材、步骤和剪辑使用位置。流程导航展示阶段入口，却不能清楚说明当前已保存方案还缺什么。外部视频生成不由本应用执行，界面尤其不能把镜头方案的关联误称为已验证的生成过程。

## Solution

为每个新关联的镜头结果候选保存一份由服务端生成、之后不随编辑改写的方案摘要。候选详情展示关联时的镜头需求、提示词、素材和步骤，标明它与当前方案是否有差异，并列出应用内可定位的剪辑引用。用户可以添加外部制作备注，但备注不作为模型运行证据。旧候选若缺少摘要，清楚显示历史关联内容不可还原，仍可保留现有人工检查和采用行为。

在现有视频复刻流程中，以已保存的工作台与时间线状态展示各阶段的已知进展、缺项数量和可点击问题位置。无法由应用验证的外部制作阶段明确标成待外部完成；可选的控制素材不计作必做缺项。未保存草稿存在时提示进度依据的是已保存版本。

## User Stories

1. As a 视频创作者, I want to open a result candidate and see its associated saved shot plan, so that later edits do not erase the context in which I attached it.
2. As a 视频创作者, I want to see the associated prompt, planned duration, project assets and shot steps together, so that I can check which preparation material was available for that candidate.
3. As a 视频创作者, I want the interface to distinguish a plan association from a verified model run, so that I do not mistake an uploaded video for proof that a particular model consumed those inputs.
4. As a 视频创作者, I want to add an optional external production note, so that I can remember where I made a candidate without claiming that the application verified it.
5. As a 视频创作者, I want each candidate's original association to remain visible after I change the shot plan, so that I can compare past and current plans.
6. As a 视频创作者, I want the existing “plan changed” warning and manual review state to keep working, so that a historical association never silently counts as current approval.
7. As a 视频创作者, I want an older candidate with no saved association summary to say that its original plan is unknown, so that migrated projects do not show invented history.
8. As a 视频创作者, I want to follow a candidate's project asset and shot-step references to their current locations, so that I can inspect them quickly.
9. As a 视频创作者, I want to see which current timeline clips and saved render histories use a candidate asset, so that changing my adopted candidate does not hide earlier usage.
10. As a 视频创作者, I want missing or removed historical files to be marked unavailable while their recorded metadata remains visible, so that I can understand an old association without a broken link being presented as valid media.
11. As a 视频创作者, I want the workflow to show how many shots and preparation steps still need attention, so that I know where to continue.
12. As a 视频创作者, I want to open a stage issue and land on its shot or step, so that I do not have to search the whole workbench.
13. As a 视频创作者, I want warnings, blocking errors and optional work distinguished, so that I can decide what must be fixed before handoff.
14. As a 视频创作者, I want result-return progress to reflect candidates that exist, have enough duration and still need review, so that a mere upload is not mistaken for an accepted result.
15. As a 视频创作者, I want the external production stage described as an external step, so that the application does not claim knowledge of a model run it did not observe.
16. As a 视频创作者, I want progress derived from the saved version when I have an unsaved draft, so that pending edits are not shown as completed work.
17. As a 视频创作者, I want the reference-video, depth-video and white-model-video routes to report only verifiable input readiness, so that a missing or optional input is not falsely marked complete.
18. As a 视频创作者, I want existing candidate selection, cleanup, export and timeline editing behavior to keep working, so that source visibility does not disrupt the production flow.

## Implementation Decisions

- The first release covers result association and stage status. Candidate A/B comparison, reusable shot-step templates, structured shot-language fields and a read-only dependency graph remain separate later work.
- The application records only its own verifiable associations: saved brief and shot-plan content, project asset identifiers, shot-step definitions and outputs, candidate identifiers, and current or historical timeline use. External production information is a candidate-specific user-authored note, never a verified generation run.
- A candidate gets a server-owned, bounded association summary when an uploaded video is attached to a shot or an existing video is first selected as a candidate. The summary remains unchanged when the shot plan changes, the candidate is reviewed, or another candidate is adopted. Candidate removal still follows existing cleanup behavior.
- The association summary identifies the saved plan version and captures enough human-readable content to understand the original prompt, duration, referenced assets and step inputs/parameters. It does not duplicate media bytes or secrets. Existing plan signatures continue to determine whether manual review is fresh; they are not used to reconstruct an old plan.
- Legacy candidates without an association summary remain usable. Their original plan is labeled unavailable, and no current plan is presented as their historical source.
- Current asset and timeline references are resolved from server-managed identifiers. A historical summary may show an asset that is now unavailable, but links are offered only for files the application can still serve.
- The candidate view uses existing preproduction and asset-reference read boundaries, extending their structured responses as needed. Timeline use is read from the existing timeline data rather than stored as a second copy in the candidate.
- Stage status is derived from saved project, preproduction checks, candidate review states and timeline state. It is not a separately editable workflow flag or a synthetic percentage.
- A stage shows completion only for an observable condition. Control material remains optional unless the selected preparation route requires it. Export readiness uses the existing distinction between warnings and blocking errors. External generation itself is never marked verified by the application.
- Stage issues carry stable identifiers for navigation rather than being classified by matching translated message text. Unsaved drafts display an explicit saved-version notice.
- The existing product boundaries remain: shot steps are an ordered list and final AI video generation happens outside the application.

## Testing Decisions

- Test behavior through existing project and preproduction HTTP APIs and the visible workbench, using realistic saved data and version changes. Avoid tests that merely mirror helper implementations.
- Verify that attaching a candidate captures the saved association once, later plan edits and manual review do not rewrite it, and old candidates do not receive fabricated content.
- Verify that missing historical files do not produce usable download links and that current and saved historical timeline uses are reported from asset identifiers.
- Verify that stage counts and navigation match server checks, the selected input route, candidate review state and unsaved-draft rules. A warning must not be treated as a blocking error; optional work must not become a required step.
- Reuse the repository's preproduction API, candidate-history, timeline-import and workbench component test patterns. Run affected backend tests, frontend tests and the frontend build after implementation.

## Out of Scope

- Replacing the ordered shot-step editor with an infinite canvas or making the dependency view editable.
- Executing final AI generation, verifying external model input or importing an external provider's run log.
- Candidate A/B playback comparison, reusable step templates, new generation providers, script-to-drama production and a new professional editing workflow.
- Copying source code or UI assets from the researched projects.

## Further Notes

- Existing product decisions are [ADR 0007](../../docs/adr/0007-preproduction-with-shot-steps.md) and [ADR 0008](../../docs/adr/0008-local-timeline-and-audio.md). Relevant primary-source research is in [open-source-infinite-canvas.md](../../docs/research/open-source-infinite-canvas.md) and [huobao-drama.md](../../docs/research/huobao-drama.md).
- The glossary term is “镜头结果关联”. It deliberately does not imply proof of external model execution.
