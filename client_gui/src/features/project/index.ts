// Public surface of features/project (WP0005 T#1 §6/T#2 §8). App consumes only this: the
// store factory, the domain types, and the top-level ProjectDialog component. App mounts
// ProjectDialog once and opens it via `useProjectStore().openDialog()` (D0004 §4.1) — it
// never reaches ProjectList/ProjectRow/ProjectCreateDialog or the API/persistence helpers
// directly, so those stay internal to this feature.
export { useProjectStore } from './store'
export type { CreateProjectInput, Project, ProjectModule } from './types'
// Flow file context read models (flowgate.v02.0024 T0005) — type-only, so the runtime
// surface stays the store factory + ProjectDialog.
export type {
  FlowAiActivity,
  FlowChangeEntry,
  FlowChangeSet,
  FlowDiffSide,
  FlowFileDiff,
  FlowFileNode,
  FlowFileTree,
  FlowRef,
  ProjectGitSlot,
  ProjectGitStatus,
} from './types'
export { default as ProjectDialog } from './ProjectDialog.vue'
