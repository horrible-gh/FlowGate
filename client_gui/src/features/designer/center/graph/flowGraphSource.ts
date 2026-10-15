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

/**
 * flowgate.v02.0061 T#14: one node of a run as the band and the Canvas lock read it — its state, its attempt's
 * result integration and Hold, and its attempt's running / requested AI executions. T#15: its current attempt
 * (`attemptId`, null when none; absent when not known), a basis of the Time Machine confirmation.
 */
export interface FlowRunNodeInfo {
  readonly nodeKey: string
  readonly status: string
  readonly integrationStatus?: string | null
  readonly hold?: boolean
  /** flowgate.v02.0063: the card's document holds a question still unanswered. */
  readonly question?: boolean
  readonly runningExecutionCount?: number
  readonly requestedExecutionCount?: number
  readonly attemptId?: string | null
}

/** flowgate.v02.0061 T#14: the server's step / full command (다음 단계 / 전체 실행). */
export type FlowRunCommand = 'step' | 'full'

/**
 * flowgate.v02.0053 T#9: a Flow's Graph run as the top band shows it (P0008 S5-4). `status` is the server's
 * open key — flowgate.v02.0061 T#14: `running` | `paused` | `stopping` | `idle` (one run per Flow).
 */
export interface FlowRunInfo {
  readonly runId: string
  readonly status: string
  readonly openNodeKeys: readonly string[]
  /** flowgate.v02.0061 T#14: the run's last command (`step` | `full`), or null / absent when not known. */
  readonly mode?: string | null
  /** flowgate.v02.0061 T#14: why an idle run is idle (open key), or null / absent. */
  readonly idleReason?: string | null
  /** flowgate.v02.0061 T#14: running / requested AI executions of the run and the Flow's limit. Absent = not known. */
  readonly runningExecutionCount?: number
  readonly requestedExecutionCount?: number
  readonly executionLimit?: number | null
  /** flowgate.v02.0061 T#14: the run's nodes when the answer carried them, else absent. */
  readonly nodeRuns?: readonly FlowRunNodeInfo[]
  /**
   * flowgate.v02.0061 T#15: the revision the run runs and its event sequence (grows with every run event). Absent = not
   * known. With the node runs' attempts they tell the Time Machine confirmation that the run's history changed.
   */
  readonly revisionNo?: number | null
  readonly lastEventSeq?: number
  /**
   * flowgate.v02.0056 F2-1 (O1 0005-TR §4-2): cards approved while the run was stopped whose successors wait
   * ("멈춤 · 다음 카드 N개 대기"), and their node keys. Absent = none (a source that does not know them).
   */
  readonly pendingPropagationCount?: number
  readonly pendingNodeKeys?: readonly string[]
}

/**
 * flowgate.v02.0059 T#2: a document's supersede link as the v0.1 document read gives it — what the server's
 * live_end follows to find the document a v0.1 sequence root shows.
 */
export interface FlowDocumentLink {
  readonly docId: string
  /** v0.1 group id = Flow id. */
  readonly groupId: string | null
  readonly status: string | null
  readonly supersededBy: string | null
}

/** A refused run operation rejects with an Error carrying the server's `code` (e.g. `run_policy_undecided`). */
export interface FlowRunError extends Error {
  readonly code?: string | null
  /** flowgate.v02.0056 F2-1: the HTTP status and the server's params (a refused approval names its document). */
  readonly status?: number | null
  readonly params?: Readonly<Record<string, unknown>>
  /** flowgate.v02.0061 T#15: a refused Graph Time Machine's assessment (`graph_time_machine_blocked`), else absent. */
  readonly timeMachine?: FlowTimeMachineRefusal
}

/**
 * flowgate.v02.0061 T#15: what a Graph Time Machine of a done node did (T#13): the server's impact (the node and every
 * node below it) and what changed. The run stays idle; the next step / full command opens the pending nodes again.
 */
export interface FlowTimeMachineResult {
  readonly operationId: string | null
  readonly operationStatus: string | null
  readonly nodeKey: string
  readonly impactNodeKeys: readonly string[]
  readonly resetNodeKeys: readonly string[]
  readonly reopenedDocumentIds: readonly string[]
  readonly revertedCommitCount: number
  readonly invalidatedAttemptCount: number
  readonly revokedTokenCount: number
  readonly idleReason: string | null
}

/** flowgate.v02.0061 T#15: why a Graph Time Machine was refused — the server's reasons (its sentences) and impact. */
export interface FlowTimeMachineRefusal {
  readonly reasons: ReadonlyArray<{ readonly code: string; readonly message: string }>
  readonly impactNodeKeys: readonly string[]
}

export interface FlowGraphSource {
  /** True for sample data: the Canvas then shows the "sample data" badge. */
  readonly isSample: boolean
  /**
   * Reads the Graph of one Flow. Rejects with an Error whose `message` is safe to show to the
   * user when the Graph cannot be read. `view` ('draft' | 'revision') picks the definition a
   * Graph Flow shows; omitted, the source's default. flowgate.v02.0061 T#15: with the card Holds (`holds`) when the
   * source keeps Holds.
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
  /** flowgate.v02.0053 T#9: the Flow's run, or null. Absent = the source runs nothing. */
  loadRun?(projectId: string, flowId: string): Promise<FlowRunInfo | null>
  /**
   * flowgate.v02.0061 T#14: one step / full command — a new run of the Flow's latest revision, or its idle run going on
   * (`full` when omitted).
   */
  startRun?(projectId: string, flowId: string, mode?: FlowRunCommand): Promise<FlowRunInfo>
  /** flowgate.v02.0061 T#14: Stop (T#7) — the run is stopping, then idle (stopped) once its AI processes ended. */
  stopRun?(projectId: string, runId: string): Promise<FlowRunInfo>
  /** Resumes a paused run with its own command. */
  resumeRun?(projectId: string, runId: string): Promise<FlowRunInfo>
  /** flowgate.v02.0061 T#14: Pause (running -> paused). Absent = the source cannot pause. */
  pauseRun?(projectId: string, runId: string): Promise<FlowRunInfo>
  /**
   * flowgate.v02.0059 T#2: the Graph as loadGraph reads it but with nothing left out (no collapse) — the
   * input of 자동 배치 when the shown Graph left Nodes out. Absent = loadGraph leaves nothing out.
   */
  loadLayoutGraph?(projectId: string, flowId: string, view?: string): Promise<FlowGraph>
  /** flowgate.v02.0059 T#2: one document's supersede link (a v0.1 sequence root's chain). Absent = none read. */
  readDocumentLink?(projectId: string, docId: string): Promise<FlowDocumentLink>
  /**
   * flowgate.v02.0056 F2-1: approves a card waiting for its approval (O1 0005-TR §4-1), `resultDocId` the result
   * the Canvas showed. Answers the run after it; a refusal rejects with a FlowRunError (the server's code).
   * Absent = the source approves nothing.
   */
  approveNode?(projectId: string, runId: string, nodeKey: string, resultDocId: string | null): Promise<FlowRunInfo>
  /**
   * flowgate.v02.0061 T#15: sets (true) / releases (false) a card's Hold; resolves whether it is held now. Only the
   * setting changes: nothing runs before the next command. Rejects with a FlowRunError (the server's code).
   */
  writeCardHold?(projectId: string, flowId: string, nodeKey: string, on: boolean): Promise<boolean>
  /**
   * flowgate.v02.0061 T#15: the Graph Time Machine of a done node of the run (T#13) — the node and every node below it.
   * Answers what it did and the run after it; a refusal rejects with a FlowRunError (`timeMachine` when blocked).
   * Absent = the source has no Time Machine.
   */
  timeMachine?(projectId: string, runId: string, nodeKey: string): Promise<{ result: FlowTimeMachineResult; run: FlowRunInfo }>
}
