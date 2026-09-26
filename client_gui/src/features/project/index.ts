// Public surface of features/project (WP0005 T#1 §6). App consumes only this: the store
// factory and the domain types. No component, no API adapter, no persistence helper is
// exported here — T#2 adds ProjectDialog/ProjectCreateDialog behind this same boundary.
export { useProjectStore } from './store'
export type { CreateProjectInput, Project, ProjectModule } from './types'
