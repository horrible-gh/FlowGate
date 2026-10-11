// flowgate.v02.0026 — Graph source (adapter) boundary (0003-D §3.2.2): "project id + Flow id
// -> FlowGraph". Whatever transport a source uses (server JSON field names, ...) stays inside
// the source; only the FlowGate Graph Model leaves it. The Center session takes a source at
// creation and falls back to the fixture source (fixtureFlowGraphSource.ts); the Designer Shell
// passes the server source (serverFlowGraphSource.ts, flowgate.v02.0045). A server source goes
// through the features/project public surface (store loaders), never @shared/api or axios
// directly (structure.0024).
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNodeDefinition, FlowGraphViewport } from './flowGraph'

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

/**
 * flowgate.v02.0045 S3: a whole Graph draft as the Center edits it. A node / edge created here and
 * not yet saved has `isNew` and a `tmp_` key; the save answers the key it got (`keyMap`).
 */
export interface FlowDraftContent {
  readonly nodes: ReadonlyArray<{
    readonly key: string
    readonly isNew: boolean
    readonly nodeType: string
    readonly docType: string | null
    readonly paletteItemId: string | null
    readonly label: string
    readonly assignee: FlowGraphNodeDefinition['assignee']
  }>
  readonly edges: ReadonlyArray<{ readonly key: string; readonly isNew: boolean; readonly source: string; readonly target: string }>
}

export interface FlowDraftSaveResult {
  /** The draft version now stored. */
  readonly version: number
  /** `tmp_` key -> stored key, nodes and edges alike. */
  readonly keyMap: Readonly<Record<string, string>>
  /** flowgate.v02.0053 T#5: stored node key -> the card's document lines the save made or kept. */
  readonly cardDocuments?: Readonly<Record<string, readonly FlowGraphCardDocument[]>>
}

/** One structure / root binding problem (P0008 S3-5); ids are node / edge ids of the Graph. */
export interface FlowDraftIssue {
  readonly code: string
  readonly structure: string | null
  readonly nodeIds: readonly string[]
  readonly edgeIds: readonly string[]
  readonly message: string
}

export interface FlowDraftCheck {
  readonly confirmable: boolean
  readonly issues: readonly FlowDraftIssue[]
}

/**
 * A refused draft operation rejects with an Error carrying the server's `code` (open key, e.g.
 * `draft_version_conflict`, `graph_validation_failed`, `flow_owned_by_v01`) and, for a refused
 * confirmation, its `issues`.
 */
export interface FlowDraftError extends Error {
  readonly code?: string | null
  readonly issues?: readonly FlowDraftIssue[]
}

export interface FlowGraphSource {
  /** True for sample data: the Canvas then shows the "sample data" badge. */
  readonly isSample: boolean
  /**
   * Reads the Graph of one Flow. Rejects with an Error whose `message` is safe to show to the
   * user when the Graph cannot be read. `view` ('draft' | 'revision') picks the definition a
   * Graph Flow shows; omitted, the source's default.
   */
  loadGraph(projectId: string, flowId: string, view?: string): Promise<FlowGraph>
  /** Stores a layout change. Absent = the source keeps no layout (moves stay on screen only). */
  saveLayout?(projectId: string, flowId: string, change: FlowLayoutChange): Promise<FlowLayoutSaveResult>
  /**
   * Starts reporting the project's Graph changes made elsewhere; returns the stop. Changes this
   * screen made itself are not reported. Absent = the source reports nothing.
   */
  watchChanges?(projectId: string, handlers: FlowGraphChangeHandlers): () => void
  /** flowgate.v02.0045 S3: replaces the Flow's draft (`version` = the one replaced, 0 = none). */
  saveDraft?(projectId: string, flowId: string, version: number, content: FlowDraftContent): Promise<FlowDraftSaveResult>
  /** Checks the saved draft without changing anything. */
  validateDraft?(projectId: string, flowId: string, version: number): Promise<FlowDraftCheck>
  /** flowgate.v02.0045 S4: confirms the saved draft as the next revision; resolves its number. */
  confirmDraft?(projectId: string, flowId: string, version: number, baseRevisionNo: number | null): Promise<number>
  /** The latest revision's definition, to start a new draft from it (null = no revision). */
  loadRevision?(projectId: string, flowId: string): Promise<{ revisionNo: number; content: FlowDraftContent } | null>
}
