// Public surface of features/project (WP0005 T#1 §6/T#2 §8). App consumes only this: the
// store factory, the domain types, and the top-level ProjectDialog component. App mounts
// ProjectDialog once and opens it via `useProjectStore().openDialog()` (D0004 §4.1) — it
// never reaches ProjectList/ProjectRow/ProjectCreateDialog or the API/persistence helpers
// directly, so those stay internal to this feature.
export { useProjectStore } from './store'
export type { CreateProjectInput, Project, ProjectModule } from './types'
export { default as ProjectDialog } from './ProjectDialog.vue'
