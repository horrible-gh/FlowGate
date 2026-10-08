// flowgate.v02.0026 — Graph source (adapter) boundary (0003-D §3.2.2): "project id + Flow id
// -> FlowGraph". Whatever transport a source uses (server JSON field names, ...) stays inside
// the source; only the FlowGate Graph Model leaves it. The Center session takes a source at
// creation and falls back to the fixture source (fixtureFlowGraphSource.ts); the Designer Shell
// passes the server source (serverFlowGraphSource.ts, flowgate.v02.0045). A server source goes
// through the features/project public surface (store loaders), never @shared/api or axios
// directly (structure.0024).
import type { FlowGraph, FlowGraphViewport } from './flowGraph'

/** One batched layout change of a Flow: moved Node positions and / or the Viewport. */
export interface FlowLayoutChange {
  readonly positions: ReadonlyArray<{ readonly nodeId: string; readonly x: number; readonly y: number }>
  /** null = Viewport unchanged. */
  readonly viewport: FlowGraphViewport | null
}

export interface FlowLayoutSaveResult {
  /** Node ids the source did not store (no longer current): the Graph should be read again. */
  readonly ignoredNodeIds: readonly string[]
}

/** What a source reports about Graphs changed elsewhere (another tab, device or user). */
export interface FlowGraphChangeHandlers {
  /** This Flow's Graph changed elsewhere: read it again. */
  changed(flowId: string): void
  /** Change reports may have been missed (reconnect): read every open Graph again. */
  resync(): void
}

export interface FlowGraphSource {
  /** True for sample data: the Canvas then shows the "sample data" badge. */
  readonly isSample: boolean
  /**
   * Reads the Graph of one Flow. Rejects with an Error whose `message` is safe to show to the
   * user when the Graph cannot be read.
   */
  loadGraph(projectId: string, flowId: string): Promise<FlowGraph>
  /** Stores a layout change. Absent = the source keeps no layout (moves stay on screen only). */
  saveLayout?(projectId: string, flowId: string, change: FlowLayoutChange): Promise<FlowLayoutSaveResult>
  /**
   * Starts reporting the project's Graph changes made elsewhere; returns the stop. Changes this
   * screen made itself are not reported. Absent = the source reports nothing.
   */
  watchChanges?(projectId: string, handlers: FlowGraphChangeHandlers): () => void
}
