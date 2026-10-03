// Public surface of features/designer. The Designer screen itself is not assembled yet; this
// exposes the parts later groups mount inside it (DesignerLeftDock assembles the Project
// Explorer next to the File Explorer — R0001 §5).
//
// flowgate.v02.0023 — Project Explorer: `<ProjectExplorer :active-flow-id @select-group
// @select-flow @activate-flow />`. Opening a Flow tab / Canvas stays with the Designer.
export { default as ProjectExplorer } from './projectExplorer/ProjectExplorer.vue'
export type {
  ProjectExplorerFlowEvent,
  ProjectExplorerGroupEvent,
} from './projectExplorer/projectExplorerTree'
