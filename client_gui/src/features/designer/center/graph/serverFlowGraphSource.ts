// flowgate.v02.0045 S1+S2 — server Graph source (D0004 §2.3 "서버 Graph 공급원"): implements the
// FlowGraphSource boundary over the Canvas read / layout save API of P0008 §3 / §4. It replaces
// the sample source in the Designer (isSample = false, no sample badge). The transport model
// comes from the features/project store loaders, so this file holds no API, store or axios of
// its own; it only translates FlowCanvas <-> FlowGate Graph Model.
//
// flowgate.v02.0045 S3+S4: the Graph definition fields (node definition, editable draft, view) and
// the draft operations (save, validate, confirm, start from the latest revision) of P0008 §5 / §6.
//
// flowgate.v02.0048 T#1: each Node also keeps its bound document, v0.1 slot and Graph node run.
//
// flowgate.v02.0053 T#9: the Flow's Graph run (P0008 §7 S5) — read the current one, start, stop, resume.
//
// flowgate.v02.0059 T#2: 자동 배치's input when the shown Graph is not the server's whole one — the same
// Canvas read with nothing left out (no collapse) and, given the v0.1 document read, a document's
// supersede link (the sequence root's chain).
//
// flowgate.v02.0056 F2-1: approve a card of it (O1 0005-TR §4-1); a run carries its pending propagation.
//
// flowgate.v02.0061 T#14: the run is the server's one run per Flow — the step / full command, Pause, Stop, Resume —
// and carries its command, idle reason, execution counts and its nodes' integration / Hold.
//
// flowgate.v02.0061 T#15: a card's Hold is read with the Graph — each Canvas read goes with the Graph read, whose nodes
// tell their cards' Hold (`holds`, also of cards the run has no node run of) — and written through the card settings
// call the Shell hands in (`cardHold`, features/document); a done card's Graph Time Machine goes through the run transport.
//
// flowgate.v02.0064 T#4: each node keeps its card title state; a card's title is saved through the store loader.
import type {
  FlowCanvas,
  FlowCanvasChangeHandlers,
  FlowCanvasLayoutInput,
  FlowCanvasLayoutResult,
  FlowCanvasTitleState,
  FlowGraphConfirmResult,
  FlowGraphDefinition,
  FlowGraphDraftInput,
  FlowGraphDraftSaveResult,
  FlowGraphIssue,
  FlowGraphValidation,
  FlowRun,
  FlowRunTimeMachine,
} from '@/features/project'
import type { FlowGraph, FlowGraphDraftInfo, FlowGraphRun } from './flowGraph'
import type {
  FlowDocumentLink,
  FlowDraftContent,
  FlowDraftIssue,
  FlowGraphSource,
  FlowLayoutChange,
  FlowLayoutSaveResult,
  FlowRunCommand,
  FlowRunInfo,
} from './flowGraphSource'

/** The project store loaders this source needs (`useProjectStore()` satisfies it). */
export interface FlowCanvasLoaders {
  loadFlowCanvas(flowId: string, view?: string, collapse?: readonly string[]): Promise<FlowCanvas>
  saveFlowCanvasLayout(flowId: string, input: FlowCanvasLayoutInput): Promise<FlowCanvasLayoutResult>
  /** Other tabs' / devices' Canvas changes (P0008 §8); absent = no change reports. */
  subscribeFlowCanvasChanges?(projectId: string, handlers: FlowCanvasChangeHandlers): () => void
  /** Graph definition saver (P0008 §5 / §6); absent = the Canvas cannot edit structure. */
  loadFlowGraph?(flowId: string): Promise<FlowGraphDefinition>
  saveFlowGraphDraft?(flowId: string, input: FlowGraphDraftInput): Promise<FlowGraphDraftSaveResult>
  validateFlowGraphDraft?(flowId: string, draftVersion: number): Promise<FlowGraphValidation>
  confirmFlowGraphRevision?(flowId: string, draftVersion: number, baseRevisionNo: number | null): Promise<FlowGraphConfirmResult>
  /** flowgate.v02.0053 T#9: the Graph run (P0008 §7 S5); absent = the Designer runs nothing. */
  loadFlowRun?(flowId: string): Promise<FlowRun | null>
  startFlowRun?(flowId: string, mode?: FlowRunCommand): Promise<FlowRun>
  stopFlowRun?(runId: string): Promise<FlowRun>
  resumeFlowRun?(runId: string): Promise<FlowRun>
  /** flowgate.v02.0061 T#14: Pause; absent = the Designer cannot pause. */
  pauseFlowRun?(runId: string): Promise<FlowRun>
  /** flowgate.v02.0056 F2-1: a card's [승인] (O1 0005-TR §4-1); absent = the Canvas approves nothing. */
  approveFlowRunNode?(runId: string, nodeKey: string, resultDocId: string | null): Promise<{ run: FlowRun }>
  /** flowgate.v02.0061 T#15: a done card's Graph Time Machine (T#13); absent = none. */
  timeMachineFlowRunNode?(runId: string, nodeKey: string): Promise<{ run: FlowRun; timeMachine: FlowRunTimeMachine }>
  /** flowgate.v02.0064 T#4: a card's title save (P0006 §4.2.2); absent = no inline title edit. */
  saveFlowNodeTitle?(flowId: string, nodeKey: string, title: string): Promise<FlowCanvasTitleState>
}

/** flowgate.v02.0061 T#15: a card's Hold write through the card settings (features/document settingsNodeHoldWrite). */
export interface FlowCardHoldLoaders {
  write(flowId: string, nodeKey: string, on: boolean): Promise<boolean>
}

/** flowgate.v02.0061 T#15: the card Holds a Graph read tells (draft and latest revision nodes, by node key). */
export function toCardHolds(definition: FlowGraphDefinition): Record<string, boolean> {
  const holds: Record<string, boolean> = {}
  for (const node of [...(definition.latestRevision?.nodes ?? []), ...(definition.draft?.nodes ?? [])]) {
    if (typeof node.hold === 'boolean') holds[node.nodeKey] = node.hold
  }
  return holds
}

const toRunInfo = (run: FlowRun): FlowRunInfo => ({
  runId: run.runId,
  status: run.status,
  openNodeKeys: run.openNodeKeys,
  // flowgate.v02.0056 F2-1: the run band's "멈춤 · 다음 카드 N개 대기".
  pendingPropagationCount: run.pendingPropagationCount ?? 0,
  pendingNodeKeys: run.pendingNodeKeys ?? [],
  // flowgate.v02.0061 T#14: the band's status line and the Canvas lock / running lines.
  mode: run.mode ?? null,
  idleReason: run.idleReason ?? null,
  runningExecutionCount: run.runningExecutionCount ?? 0,
  requestedExecutionCount: run.requestedExecutionCount ?? 0,
  executionLimit: run.executionLimit ?? null,
  ...(run.nodeRuns ? { nodeRuns: run.nodeRuns } : {}),
  // flowgate.v02.0061 T#15: the run's history as the Time Machine confirmation compares it.
  revisionNo: run.revisionNo ?? null,
  lastEventSeq: run.lastEventSeq ?? 0,
})

/**
 * flowgate.v02.0045 TR0020 rev5: conversations (CH, and the Q / A of the same server collapse group,
 * P0008 §2.5) are not part of the work flow; every Canvas read asks the server to leave them out,
 * so a new conversation never shows up as a memo card.
 */
export const CANVAS_COLLAPSE: readonly string[] = ['conversation']

/** P0008 §3 `graph` + `view` -> the draft the Canvas may edit. */
function draftInfo(canvas: FlowCanvas): FlowGraphDraftInfo | null {
  const state = canvas.ownership.state
  const stored = canvas.graph?.draft ?? null
  const inDraftView = canvas.view?.mode === 'draft'
  const editable = state === 'unassigned' || (state === 'graph' && inDraftView)
  if (!stored && !editable) return null
  return {
    version: stored?.draftVersion ?? 0,
    editable,
    confirmable: stored ? stored.confirmable : state === 'unassigned',
    blockedReason: stored?.blockedReason ?? null,
    baseRevisionNo: stored?.baseRevisionNo ?? null,
  }
}

/** Server Canvas -> FlowGate Graph Model. Node ids are the server node identities. */
export function toFlowGraph(canvas: FlowCanvas): FlowGraph {
  return {
    flowId: canvas.flowId,
    nodes: canvas.nodes.map((node) => ({
      id: node.nodeKey,
      kind: node.kind,
      title: node.title,
      state: node.state,
      docType: node.docType,
      x: node.position.x,
      y: node.position.y,
      positionSource: node.position.source,
      movable: node.editable.move,
      structureEditable: node.editable.structure,
      definition: node.graphNode
        ? {
            nodeType: node.graphNode.nodeType,
            docType: node.docType,
            paletteItemId: node.graphNode.paletteItemId,
            label: node.graphNode.label,
            assignee: node.graphNode.assignee,
            rootBound: node.graphNode.rootBound,
          }
        : null,
      document: node.document,
      slot: node.slot,
      nodeRun: node.nodeRun ?? null,
      ...(node.cardDocuments ? { cardDocuments: node.cardDocuments } : {}),
      // flowgate.v02.0064 T#4: the card title state, as sent (absent from an older server).
      ...(node.titleState !== undefined ? { titleState: node.titleState } : {}),
    })),
    edges: canvas.edges.map((edge) => ({
      id: edge.edgeId,
      source: edge.source,
      target: edge.target,
      edgeClass: edge.edgeClass,
      editable: edge.editable,
    })),
    ownership: canvas.ownership.state,
    viewport: canvas.viewport,
    draft: draftInfo(canvas),
    viewMode: canvas.view?.mode ?? null,
    latestRevisionNo: canvas.graph?.latestRevisionNo ?? null,
    viewRevisionNo: canvas.view?.revisionNo ?? null,
    projection: canvas.projection || null,
    sequenceRootDocId: canvas.ownership.sequenceRootDocId ?? null,
    hiddenNodeCount: Object.values(canvas.collapsed?.hiddenCounts ?? {}).reduce((sum, count) => sum + (Number(count) || 0), 0),
    // flowgate.v02.0061 TR0031 rev1: the run block's status (the run lock while the run read is pending or failed).
    // T#15: with the revision it runs and its event sequence (the Time Machine confirmation's basis).
    // flowgate.v02.0083 T#4 rev1: and the band's facts it sent (toRun keeps only those).
    ...(canvas.run !== undefined ? { run: canvas.run ? toGraphRun(canvas.run) : null } : {}),
  }
}

function toGraphRun(run: NonNullable<FlowCanvas['run']>): FlowGraphRun {
  const { runId, status, revisionNo, lastEventSeq, ...band } = run
  return { runId, status, revisionNo, ...(lastEventSeq !== undefined ? { lastEventSeq } : {}), ...band }
}

export function toLayoutInput(change: FlowLayoutChange): FlowCanvasLayoutInput {
  return {
    positions: change.positions.map((p) => ({ nodeKey: p.nodeId, x: p.x, y: p.y })),
    viewport: change.viewport ? { x: change.viewport.x, y: change.viewport.y, zoom: change.viewport.zoom } : null,
  }
}

export function toDraftInput(version: number, content: FlowDraftContent): FlowGraphDraftInput {
  return {
    draftVersion: version,
    nodes: content.nodes.map((n) => ({
      nodeKey: n.isNew ? null : n.key,
      clientKey: n.isNew ? n.key : null,
      nodeType: n.nodeType,
      docType: n.docType,
      paletteItemId: n.paletteItemId,
      label: n.label,
      assignee: n.assignee ? { ...n.assignee } : null,
    })),
    edges: content.edges.map((e) => ({
      edgeId: e.isNew ? null : e.key,
      clientKey: e.isNew ? e.key : null,
      source: e.source,
      target: e.target,
    })),
  }
}

const toIssue = (issue: FlowGraphIssue): FlowDraftIssue => ({
  code: issue.code, structure: issue.structure, nodeIds: issue.nodeKeys, edgeIds: issue.edgeIds, message: issue.message,
})

function asDraftError(error: unknown): unknown {
  const issues = (error as { issues?: FlowGraphIssue[] } | null)?.issues
  if (error && typeof error === 'object' && Array.isArray(issues)) {
    Object.defineProperty(error, 'issues', { value: issues.map(toIssue), configurable: true })
  }
  return error
}

/**
 * `readDocument`: the v0.1 document read (`documentRead` of features/document fits); absent = no supersede
 * link is read, so 자동 배치 cannot tell a v0.1 root that replaced an older root document.
 * `cardHold` (flowgate.v02.0061 T#15): a card's Hold write; absent = the Canvas holds nothing.
 */
export function createServerFlowGraphSource(
  loaders: FlowCanvasLoaders,
  readDocument?: (docId: string) => Promise<FlowDocumentLink>,
  cardHold?: FlowCardHoldLoaders,
): FlowGraphSource {
  const graphLoaders = loaders.saveFlowGraphDraft && loaders.validateFlowGraphDraft && loaders.confirmFlowGraphRevision
  const runLoaders = loaders.loadFlowRun && loaders.startFlowRun && loaders.stopFlowRun && loaders.resumeFlowRun
  // flowgate.v02.0061 T#15: the card Holds of a Canvas read, read beside it; a failed Graph read leaves them unknown.
  const readHolds = (flowId: string): Promise<Record<string, boolean> | null> =>
    cardHold && loaders.loadFlowGraph ? loaders.loadFlowGraph(flowId).then(toCardHolds, () => null) : Promise.resolve(null)
  return {
    isSample: false,
    // The Flow id is the global v0.1 group id; the project id is not part of the address.
    loadGraph: async (_projectId: string, flowId: string, view?: string) => {
      const [canvas, holds] = await Promise.all([loaders.loadFlowCanvas(flowId, view, CANVAS_COLLAPSE), readHolds(flowId)])
      const graph = toFlowGraph(canvas)
      return holds ? { ...graph, holds } : graph
    },
    saveLayout: async (_projectId: string, flowId: string, change: FlowLayoutChange): Promise<FlowLayoutSaveResult> => {
      const result = await loaders.saveFlowCanvasLayout(flowId, toLayoutInput(change))
      return { ignoredNodeIds: result.ignored.map((item) => item.nodeKey) }
    },
    watchChanges: loaders.subscribeFlowCanvasChanges
      ? (projectId, handlers) => loaders.subscribeFlowCanvasChanges!(projectId, handlers)
      : undefined,
    saveDraft: graphLoaders
      ? async (_projectId, flowId, version, content) => {
          const result = await loaders.saveFlowGraphDraft!(flowId, toDraftInput(version, content))
          return {
            version: result.draftVersion,
            keyMap: result.keyMap,
            ...(result.cardDocuments ? { cardDocuments: result.cardDocuments } : {}),
          }
        }
      : undefined,
    validateDraft: graphLoaders
      ? async (_projectId, flowId, version) => {
          const result = await loaders.validateFlowGraphDraft!(flowId, version)
          return { confirmable: result.confirmable, issues: result.issues.map(toIssue) }
        }
      : undefined,
    confirmDraft: graphLoaders
      ? async (_projectId, flowId, version, baseRevisionNo) => {
          try {
            return (await loaders.confirmFlowGraphRevision!(flowId, version, baseRevisionNo)).revisionNo
          } catch (error) {
            throw asDraftError(error)
          }
        }
      : undefined,
    loadRevision: loaders.loadFlowGraph
      ? async (_projectId, flowId) => {
          const latest = (await loaders.loadFlowGraph!(flowId)).latestRevision
          if (!latest) return null
          return {
            revisionNo: latest.revisionNo,
            content: {
              nodes: latest.nodes.map((n) => ({
                key: n.nodeKey, isNew: false, nodeType: n.nodeType, docType: n.docType,
                paletteItemId: n.paletteItemId, label: n.label, assignee: n.assignee,
              })),
              edges: latest.edges.map((e) => ({ key: e.edgeId, isNew: false, source: e.source, target: e.target })),
            },
          }
        }
      : undefined,
    loadRun: runLoaders
      ? async (_projectId, flowId) => {
          const run = await loaders.loadFlowRun!(flowId)
          return run ? toRunInfo(run) : null
        }
      : undefined,
    startRun: runLoaders ? async (_projectId, flowId, mode) => toRunInfo(await loaders.startFlowRun!(flowId, mode)) : undefined,
    stopRun: runLoaders ? async (_projectId, runId) => toRunInfo(await loaders.stopFlowRun!(runId)) : undefined,
    resumeRun: runLoaders ? async (_projectId, runId) => toRunInfo(await loaders.resumeFlowRun!(runId)) : undefined,
    pauseRun: runLoaders && loaders.pauseFlowRun ? async (_projectId, runId) => toRunInfo(await loaders.pauseFlowRun!(runId)) : undefined,
    loadLayoutGraph: async (_projectId: string, flowId: string, view?: string) =>
      toFlowGraph(await loaders.loadFlowCanvas(flowId, view)),
    readDocumentLink: readDocument ? (_projectId, docId) => readDocument(docId) : undefined,
    approveNode:
      runLoaders && loaders.approveFlowRunNode
        ? async (_projectId, runId, nodeKey, resultDocId) => toRunInfo((await loaders.approveFlowRunNode!(runId, nodeKey, resultDocId)).run)
        : undefined,
    writeCardHold: cardHold ? (_projectId, flowId, nodeKey, on) => cardHold.write(flowId, nodeKey, on) : undefined,
    timeMachine:
      runLoaders && loaders.timeMachineFlowRunNode
        ? async (_projectId, runId, nodeKey) => {
            const answer = await loaders.timeMachineFlowRunNode!(runId, nodeKey)
            return { result: { ...answer.timeMachine }, run: toRunInfo(answer.run) }
          }
        : undefined,
    saveNodeTitle: loaders.saveFlowNodeTitle
      ? async (_projectId, flowId, nodeKey, title) => ({ ...(await loaders.saveFlowNodeTitle!(flowId, nodeKey, title)) })
      : undefined,
  }
}
