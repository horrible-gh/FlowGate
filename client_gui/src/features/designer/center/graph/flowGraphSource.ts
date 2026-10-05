// flowgate.v02.0026 — Graph source (adapter) boundary (0003-D §3.2.2): "project id + Flow id
// -> FlowGraph". Whatever transport a source uses (server JSON field names, ...) stays inside
// the source; only the FlowGate Graph Model leaves it. The Center session takes a source at
// creation and falls back to the fixture source (fixtureFlowGraphSource.ts) — a server source
// replaces that one default later. A server source goes through the features/project public
// surface (store loaders), never @shared/api or axios directly (structure.0024).
import type { FlowGraph } from './flowGraph'

export interface FlowGraphSource {
  /** True for sample data: the Canvas then shows the "sample data" badge. */
  readonly isSample: boolean
  /**
   * Reads the Graph of one Flow. Rejects with an Error whose `message` is safe to show to the
   * user when the Graph cannot be read.
   */
  loadGraph(projectId: string, flowId: string): Promise<FlowGraph>
}
