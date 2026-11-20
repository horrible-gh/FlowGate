# features

The ownership boundary for business functionality, state, API calls, and feature UI (T0005 §4).

`project/` was added by flowgate.v02.0008 T#1/T#2. It contains the Project model/types,
an API adapter over `@shared/api`, a Pinia store, current-project localStorage
persistence, and ProjectDialog/ProjectList/ProjectRow/ProjectCreateDialog.
The public boundary, `features/project/index.ts`, exports only `useProjectStore`,
types, and `ProjectDialog`. App does not directly reference the internal list,
row, or create-dialog components. App Shell Sidebar/Dashboard entry wiring through
`openDialog()` was left to T#3.

flowgate.v02.0023 added `fetchProjectGroupTree` for
`/api/v1/projects/{id}/groups/tree`, store caching (`fetchGroupTree` /
`getCachedGroupTree`), and read-only `currentProjectId`. Designer's Project Explorer
and File Explorer (0024) share this project context instead of a separate Designer
store. flowgate.v02.0024 T0005 added read-only Flow file-context APIs and loaders:
project Git status, Flow tree, changes relative to Base, single-file diff, and AI
activity (`loadProjectGitStatus`, `loadFlowFileTree`, `loadFlowChanges`,
`loadFlowFileDiff`, `loadFlowAiActivity`). flowgate.v02.0033 extended the same
loaders for Designer Git with additive pending-row/Base-dirty/Base-untracked
fields, AI run ID/status/action scope, and `loadBaseFileDiff` /
`loadMergeReviewFileDiff`.

`designer/` holds v0.2 Designer components. `designer/projectExplorer/` provides
Project > Group > Flow navigation: ProjectExplorer/ProjectTree/ProjectRow/GroupRow/
FlowRow, the ExplorerRowFrame, and pure display-tree logic in
`projectExplorerTree.ts`. `designer/fileExplorer/`, added in flowgate.v02.0024
T0005, provides DesignerFileExplorer, Toolbar/ScopeHeader/Filter/Tree/FolderRow/
FileRow. Both left-dock explorers share the thin `shared/ExplorerRowFrame` and
`TreeCaret` primitives and `shared/ExplorerIcon` for Project/Group/folder/Flow
icons. Designer has no API/store of its own and uses only the public store/types
from `features/project`. `designer/index.ts` exports `ProjectExplorer`,
`DesignerFileExplorer`, and `DesignerShell`.

flowgate.v02.0025 added `designer/shell/`. `DesignerShell`, the `/app/designer`
route, lays out Top + Body(Left + Center + Right), with Workspace + Bottom inside
Center. Components are DesignerTop/DesignerBody/DesignerLeftDock/ExplorerModeTabs/
DesignerCenterColumn/DesignerWorkspace/DesignerBottomDock/DesignerRightDock,
`ShellPlaceholder`, and layout definitions in `designerLayout.ts`.
The left dock assembles the existing 0023 Project Explorer and 0024 File Explorer
with Project/File tabs and collapse/expand. Top/Workspace/Bottom/Right initially
contain placeholders in their final containers. Each DesignerShell instance owns
layout state and the active Flow, with no Pinia/persistence or direct API calls.
Canvas, Flow Tabs, Inspector, Palette, Git, and resizing were follow-up work.

flowgate.v02.0025 T0006 made Designer a dedicated fullscreen workspace through
`meta.fullscreen`, removing App Topbar/Sidebar. The left-dock header became one
`ExplorerModeTabs` row (Project, File, collapse). Row styling alone marks the
active Flow, without a Badge. Normal states do not show explanatory Base-comparison
or current-Flow labels.

flowgate.v02.0026 added `designer/center/` (Flow Tabs and a read-only Canvas),
replacing the Workspace placeholder with `DesignerCenter`. A Center session in
`designerCenterSession.ts` solely owns open tabs/order, active Flow, and each
Flow's graph-load state/viewport/selection. DesignerShell creates one session
with the same lifetime; it is not Pinia. Project Explorer selection, File Explorer
target, and active tab all use the same active FlowRef. Graphs enter through
`graph/flowGraphSource.ts`; until the server Graph API existed, the default was
`graph/fixtureFlowGraphSource.ts`, marked by a sample-data badge on Canvas.
The engine is exactly pinned `@vue-flow/core` 1.48.2, imported only inside
`center/canvas/`. `canvas/vueFlowMapping.ts` maps the FlowGate Graph Model one way
to rendering. `FlowNodeCard`/`FlowEdgePath` provide FlowGate-specific visuals
without the library's default theme. Editing (move/connect/save), Inspector,
Minimap, and Toolbar were left to later work.

flowgate.v02.0030 added `designer/palette/`: `PalettePanel`, `PaletteCard`,
`PaletteAddDialog`, pure `paletteCatalog.ts`, and glyph table `paletteIcons.ts`.
Default Design/Work/Control items follow mockup 03 (flowgate.v02.0053 T#8 shortened
the Work Flow category to Work). `nodeKind` reuses keys from
`center/canvas/flowNodeKinds.ts`; `docType` reuses FlowGate document type codes.
Palette emits only `select` and `drag-start` intentions (DataTransfer type
`application/x-flowgate-palette-item`); it does not handle Canvas drop/node creation.
Parents can own/persist custom entries through `v-model:custom-items`, with no
pre-existing settings contract. Without binding, the component keeps them for
its session. Palette/Conversation/Git tabs and the Tool Panel Container were
left for later groups, so this stage did not wire Palette into Shell or export it
through the public boundary.

flowgate.v02.0036 added the bottom-center Inspector frame in
`designer/inspector/`: `DesignerInspector`, `InspectorHeader`, and pure tab rules
in `inspectorTabs.ts`. Bottom Dock changed to Inspector 7 : Minimap 3, retaining
the Minimap placeholder and increasing default height from 200 to 300px. The
reference is flowgate.v02.0035 v7. Without node selection it shows one guidance
line; otherwise it shows a 40px header (kind icon/kind/title/status), 32px tabs
(Documents/Settings/AI/Q&A/Attachments), and content. Selection comes from Center's
derived `activeSelectedNode`. The tab persists across node changes, falling back
to Documents when a node has `has-settings=false`. Later features inject
`header-meta` / `header-actions` and `tab-documents` / `tab-qna` /
`tab-attachments` / `tab-settings` slots with the selected node. Unfilled tabs
show one placeholder line. Actual node-specific functions/actions/APIs/state
machines and Settings availability were left for subsequent groups; this frame
was not yet exported publicly.

flowgate.v02.0049 replaced the Minimap placeholder with
`designer/minimap/DesignerMiniMap.vue` and pure `minimapGeometry.ts`. It renders
the active Flow's ready graph (new or v0.1 legacy), Center's viewport, and Canvas
size reported from Vue Flow measurements (`canvas-resize` → `recordCanvasSize`).
It has no independent graph/viewport state. Edges use the same executable-edge
`toVueFlowEdges` output as Canvas. Clicking/dragging sends a viewport at the same
zoom through session `recordViewport`, applied by Canvas; release calls
`commitViewport` to persist it like a user pan. There are no buttons or labels.

flowgate.v02.0037 replaced Right Dock's placeholder with the Tool Panel.
`DesignerRightDock` owns a one-row Palette/Conversation/Git Tabs header and body,
mounting 0030 `PalettePanel`, 0031/0032 `ConversationPanel`
(`@/features/conversation`), and 0033 `DesignerGitPanel` (`@/features/git`).
Layout's `activeRightTool` (default `palette`) owns selection. Each component
mounts on first display, then hides during switching/collapse to preserve its
session (Git gets `active=false` while hidden). Conversation/Git receive Shell's
current Project and active FlowRef. The Inspector remains in Center's Bottom Dock
as installed by 0036. Palette select/drag, Conversation `open-change`, Git
`open-diff` / `open-confirm-settings` / `windows-change` / `badge-change`, right
collapse UI, and resizing await their receiving features.

`conversation/`, added in flowgate.v02.0031, is an independent right-tool-area
component based on mockup 03. `ConversationPanel` (`:project-id`, active Flow's
`:group-id`, `@open-change`) is its public boundary. It contains a chat list,
editable title, new-chat action, one chronological timeline (user/AI messages,
command requests, work changes, state events), participants, and input controls
(chat settings, AI source mode read/edit/one-time edit, copy mention, invoke/select
AI, send/stop). `conversationSession.ts` solely owns panel-lifetime state,
without Pinia. Behind `conversationSource.ts`, `api.ts` uses the existing v0.1
ConversationView/MainPanel contracts (CH list/title PATCH, conversation
turns/turn/read, ai-invoke, me/chat-settings, token/issue) and the 0032 production
Conversation contract. 0032 connected the default HTTP source: New Chat creates a
standalone CH independent of workflow head via `POST /api/v1/conversations`, not
next-empty. `GET /api/v1/chat-activity/{doc_id}` supplies command requests and
run-level work changes at server anchors; decisions use
`POST /api/v1/chat-commands/{request_id}/decision`. Without a live subscription
layer (`subscribeActivities` unimplemented), activity refreshes at every 2.5s AI
poll, immediately after invocation ends, and after decisions. Clicking a file on
a work-change card opens its server-recorded run-start/run-end diff below the
card. Tab switching, Tool Panel Container, Palette, Git, and Designer assembly
are outside this component.

`git/`, added in flowgate.v02.0033, is the independent right-tool Git component
(mockup 06 v7). Its public `DesignerGitPanel` takes `:project-id`, active `:flow`,
`:active`, `:read-only`, optional `:source`; emits `badge-change`, `open-diff`,
`open-confirm-settings`, `windows-change`; and exposes `refresh`, `restoreWindow`,
`closeWindow`. It contains the Git Header, exception cards (merge conflict,
AI resolution, merge approval pending, apply held, uncommitted changes outside
the flow, remote push failure), and two modeless dialogs (conflict resolution,
remote failure details). `gitSession.ts` owns panel-lifetime state without Pinia.
Reads behind `gitSource.ts` use project loaders; `api.ts` actions use existing Git
contracts: conflicts/abort/review/approve/reject, finalize, push,
base-commit/revert/remove, and ai-invoke start/cancel/providers. Before server
extensions for held-group planned apply mode (S1) and new-file totals/full scope
(S2), behavior is provisional stage 1; responses containing those fields enable
stage 2. Tool Panel Container, Designer taskbar, Center diff tabs, and Inspector
confirmation settings remain intention-event boundaries outside this component.

`settings/`, added by flowgate.v02.0020, is the Environment Settings feature
(mockups 11–14: workspace list/detail, System > Users/Security).
`features/settings/index.ts` exports only routed screens.

Remaining feature migration belongs to follow-up groups.
