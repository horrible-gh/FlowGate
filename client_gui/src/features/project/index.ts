// Public surface of features/project (WP0005 T#1 §6/T#2 §8). App consumes only this: the
// store factory, the domain types, and the top-level ProjectDialog component. App mounts
// ProjectDialog once and opens it via `useProjectStore().openDialog()` (D0004 §4.1) — it
// never reaches ProjectList/ProjectRow/ProjectCreateDialog or the API/persistence helpers
// directly, so those stay internal to this feature. The group tree types are exported for
// features that read the project tree through the store (Designer, flowgate.v02.0023).
export { useProjectStore } from './store'
// Group tree types (flowgate.v02.0023) and Flow file context read models (flowgate.v02.0024
// T0005) — type-only, so the runtime surface stays the store factory + ProjectDialog.
// flowgate.v02.0033 adds the Base / review file diff and Git status row types the Designer
// Git part reads through the same store loaders.
// flowgate.v02.0043 adds the recent Project / Group / Flow entries and the Group / Flow create
// models the Designer File menu uses through the store.
// flowgate.v02.0045 adds the Flow Canvas read / layout save models the Designer Center reads
// through the store loaders (loadFlowCanvas / saveFlowCanvasLayout), and the handler shape of
// the store's Canvas change subscription (subscribeFlowCanvasChanges).
export type { FlowCanvasChangeHandlers } from './flowCanvasEvents'
export type {
  BaseFileDiff,
  CreateFlowInput,
  CreateGroupInput,
  CreateProjectInput,
  CreatedFlow,
  CreatedGroup,
  FetchGroupTreeOptions,
  FlowAiActivity,
  FlowChangeEntry,
  FlowCanvas,
  FlowCanvasDocument,
  FlowCanvasEdge,
  FlowCanvasExternalRelation,
  FlowCanvasLayoutInput,
  FlowCanvasLayoutResult,
  FlowCanvasNode,
  FlowCanvasOwnership,
  FlowCanvasPosition,
  FlowCanvasSlot,
  FlowCanvasViewport,
  FlowChangeSet,
  FlowDiffSide,
  FlowFileDiff,
  FlowFileNode,
  FlowFileTree,
  FlowRef,
  GroupTreeNode,
  GroupTreeNodeKind,
  MergeReviewFileDiff,
  Project,
  ProjectGitBaseDirty,
  ProjectGitBaseUntracked,
  ProjectGitPendingPlan,
  ProjectGitPendingRow,
  ProjectGitSlot,
  ProjectGitStatus,
  ProjectModule,
  RecentFlow,
  RecentGroup,
  RecentProject,
} from './types'
export { default as ProjectDialog } from './ProjectDialog.vue'
