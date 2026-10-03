// Public surface of features/project (WP0005 T#1 §6/T#2 §8). App consumes only this: the
// store factory, the domain types, and the top-level ProjectDialog component. App mounts
// ProjectDialog once and opens it via `useProjectStore().openDialog()` (D0004 §4.1) — it
// never reaches ProjectList/ProjectRow/ProjectCreateDialog or the API/persistence helpers
// directly, so those stay internal to this feature. The group tree types are exported for
// features that read the project tree through the store (Designer, flowgate.v02.0023).
export { useProjectStore } from './store'
// Group tree types (flowgate.v02.0023) and Flow file context read models (flowgate.v02.0024
// T0005) — type-only, so the runtime surface stays the store factory + ProjectDialog.
export type {
  CreateProjectInput,
  FetchGroupTreeOptions,
  FlowAiActivity,
  FlowChangeEntry,
  FlowChangeSet,
  FlowDiffSide,
  FlowFileDiff,
  FlowFileNode,
  FlowFileTree,
  FlowRef,
  GroupTreeNode,
  GroupTreeNodeKind,
  Project,
  ProjectGitSlot,
  ProjectGitStatus,
  ProjectModule,
} from './types'
export { default as ProjectDialog } from './ProjectDialog.vue'
