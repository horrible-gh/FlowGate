// Public surface of features/designer (flowgate.v02.0024 T0005). Only the File Explorer part
// exists so far; the Designer left dock that will assemble it (with the Project Explorer)
// is a later group's work. Rows, primitives, the model and the data adapter stay internal.
export { default as DesignerFileExplorer } from './fileExplorer/DesignerFileExplorer.vue'
export type {
  ChangeStatus,
  DesignerFileIntent,
  ExplorerFilter,
} from './fileExplorer/fileExplorerModel'
