// Public surface of features/designer: the Designer screen (DesignerShell, the /designer
// route component — flowgate.v02.0025) and the Explorer parts it assembles in its left Dock
// (DesignerLeftDock puts the Project Explorer next to the File Explorer — R0001 §5). Shell
// containers, rows, primitives, models and the data adapters stay internal.
//
// flowgate.v02.0023 — Project Explorer: `<ProjectExplorer :active-flow-id @select-group
// @select-flow @activate-flow />`. Opening a Flow tab / Canvas stays with the Designer.
export { default as ProjectExplorer } from './projectExplorer/ProjectExplorer.vue'
export type {
  ProjectExplorerFlowEvent,
  ProjectExplorerGroupEvent,
} from './projectExplorer/projectExplorerTree'
// flowgate.v02.0024 T0005 — File Explorer of the active Flow.
export { default as DesignerFileExplorer } from './fileExplorer/DesignerFileExplorer.vue'
export type {
  ChangeStatus,
  DesignerFileIntent,
  ExplorerFilter,
} from './fileExplorer/fileExplorerModel'
// flowgate.v02.0025 — Designer Shell (Top + Body(Left + Center(Workspace + Bottom) + Right)).
// flowgate.v02.0026 — the Designer Center (center/: Flow Tabs + read-only Canvas) is assembled
// inside the Shell only and has no consumer outside it, so it adds no export here.
export { default as DesignerShell } from './shell/DesignerShell.vue'
// flowgate.v02.0030 — the Palette part (palette/PalettePanel.vue) is mounted by the right Tool
// Panel Container inside features/designer only, so it adds no export here either.
// flowgate.v02.0036 — the bottom Inspector Frame (inspector/DesignerInspector.vue) is mounted by
// the Shell's Bottom Dock only, so it adds no export here either.
