// flowgate.v02.0026 — Designer Center session (0003-D §3.3.4 / §3.4): the single owner of the
// Center state — open tabs and their order, the active Flow, and per Flow the Graph read
// state, Viewport and Selection. Not a Pinia store (features/designer owns no store): the
// DesignerShell instance creates one session and it lives and dies with that Shell.
//
// Outside code sees the state read-only and changes it only through the actions below. The
// tab rules are the pure functions of flowTabs.ts; Graphs come from a FlowGraphSource
// (fixture by default). A Graph read that arrives after its tab closed, the project changed or
// a newer read of the same Flow started is dropped.
//
// flowgate.v02.0045 S2: a Graph carrying a saved Viewport restores it; a dragged movable Node
// and a user-committed Viewport are batched (layoutSaveQueue.ts) and stored through the
// source's saveLayout. A save the source answers with ignored Nodes, or a failed save, reads the
// Graph again so the screen shows what is actually stored (P0008 S2-2). One save per Flow is
// on the wire at a time (layoutSaveQueue.ts), so a slow earlier save cannot overwrite a later one.
// A Graph changed elsewhere (another tab, device or user — the source's watchChanges, P0008 §8.3)
// is read again in the background, keeping the shown Graph until the new one arrives.
// Every such re-read (ignored / failed save, change elsewhere) waits until this tab has no save of
// the Flow waiting or running. A read that comes back while a save of the Flow is waiting or
// running, or after a Node of the Flow was moved here since the read went out, may predate those
// moves: it does not replace a shown Graph, and the Flow is read again once its saves settle.
//
// flowgate.v02.0045 S3+S4: a shown Graph that is an editable draft is edited through the draft
// controller (edit/draftController.ts) — Palette drop, connect, remove, undo, validate, confirm.
// Draft saves count as saves of the Flow for the re-read rule above. A node created here keeps a
// `tmp_` key until its draft save answers the stored key; only then is its position saved — also
// when the tab was closed (or the project changed) while that save ran: the controller keeps the
// position shown at close and it is saved for the project the save belongs to. A tab of the Flow
// opened again meanwhile reads the Flow once the save and the position save settled.
//
// flowgate.v02.0045 T0019: a Flow opened right after it was created may carry a default node
// (`openFlow` option `seedNode`, the Shell gives a 요구사항 node). It is added to the Flow's draft
// once the first Graph read shows it empty and editable — never to a Flow that has nodes. An Edge
// the work already went through (graph/edgeProgress.ts) is never removed.
//
// flowgate.v02.0048 T#1 (D#1 §4.2 / §5.7): `activeSelection` gives the bottom Inspector the
// selected Node together with its Flow's Graph and the project; `refreshFlow` reads a Flow again
// quietly (as a change elsewhere does) after a change that sends no event.
//
// flowgate.v02.0045 TR0020 rev3: no 편집 / 편집 마치기 — which definition a Graph Flow shows is
// picked here. A stored draft (edits not run yet) is shown; with none, the latest revision (the
// run view) is shown editable as is (draftModel `editableRevision`) and its first edit saves a new
// draft from it. 실행 confirms the draft as the next revision and the Flow shows its run again.
// Revisions and runs are never changed by editing (the server keeps them apart from the draft).
//
// flowgate.v02.0053 T#9: the top band's 다음 단계 / 전체 실행 / 중지 run the Flow on the server
// (run/flowRunControl.ts). Each open Flow keeps its run state next to its Graph; the run is read with
// every Graph read (a run event reads the Canvas again, flowCanvasEvents), so the buttons, the run dot and
// the cards' colour dots show the server's run.
//
// flowgate.v02.0059 T#1 (0011-CH contract A): the Toolbar's [선택] mode (`selectMode`, one for the
// Shell) lets a Flow's Selection hold several Nodes — the Canvas reports them (`selectNodes`) and
// 전체 선택 (`selectAll`) takes every Node of the Flow and turns the mode on. Turning it off only drops
// such multi-Node Selections; nothing else (Graph, draft, saved layout) changes. A Selection keeps its
// Nodes across a `tmp_` key rename and loses the ones a re-read, an edit or its undo / redo took away.
//
// flowgate.v02.0059 T#2 (0011-CH contracts C / D): every action that moves cards — a drop of one card or of
// the dragged selection (`moveNodes`), 정렬 / 분배 of the selected cards (`arrangeNodes`), 자동 배치 at a
// spacing scale (`autoLayout`, the server's auto_layout rules) — puts the movable cards through one path
// (Graph, layout save queue) and is one entry of the Flow's undo / redo, next to its structure edits. An
// undone / redone move entry comes back through the same path; the draft is never saved for a move.
// 자동 배치 takes the server's layout input: when the shown Graph leaves part of it out (documents the read
// collapsed, a v0.1 root only its supersede chain tells — arrange/autoLayout.ts), the Flow is read whole
// first and the cards move once that answers; only the Flow's latest 자동 배치 moves them, and none does
// when the read fails. Nor does one whose answer comes after `cancelAutoLayout`, after the
// session was disposed, or after a read replaced the Graph it was asked for (a retry, a change elsewhere, the
// re-read of an ignored / failed save, a reset) — the cards stay where they are shown.
//
// flowgate.v02.0056 F2-1: a card's [승인] on the Canvas approves it through the same run control (with the
// result the shown Graph has for the card); the Canvas is read again after the answer.
//
// flowgate.v02.0061 T#14: the run buttons are 다음 단계 / 전체 실행 (one step / full command), 일시정지 / 재개 and 중지
// (run/flowRunControl.ts). While the Flow's run is running, paused or stopping its structure is locked here
// (run/flowRunLock.ts): no Palette drop, connection, removal, undo / redo or 검증 — only viewing, panning, zooming and
// moving cards. Whatever the run's status, a done / open / review node and the execution lines into it are never
// removed and get no new line (the server's T#3 judgement). The Canvas gets the same marks to draw (`runMarks`).
//
// flowgate.v02.0061 TR0031 rev1: the lock follows the Flow's confirmed run status (`runStatus`) — the run read (or a
// command's answer), else, while that read is pending or failed, the run status of the Flow's Canvas read. With neither
// (a Flow whose Canvas and run are both still unread, or whose run read failed with no Canvas shown) the status is not
// confirmed and the structure waits as if locked (`unknown`); a run last known locked stays locked. No run command goes
// until the status is confirmed. The Graph's load only adds the protected / running nodes: its loading or error never
// lifts the lock. The marks also carry the run read's node runs of the shown nodes (result integration, Hold).
import { computed, markRaw, reactive, readonly, shallowRef, type ComputedRef, type DeepReadonly } from 'vue'
import type { FlowRef } from '@/features/project'
import {
  createCanvasViewState,
  sameCanvasSize,
  sameViewport,
  setViewSelection,
  type CanvasSize,
  type CanvasViewState,
  type CanvasViewport,
} from './canvasView'
import {
  EMPTY_FLOW_TABS,
  activateFlowTab,
  closeFlowTab,
  findFlowTab,
  openFlowTab,
  type FlowTab,
  type FlowTabsState,
} from './flowTabs'
import { arrangeSelection, type ArrangeKind } from './arrange/arrangeNodes'
import { autoLayoutPlacements, loadLayoutBasis, needsLayoutBasis, type FlowLayoutBasis } from './arrange/autoLayout'
import type { ConnectionCheck } from './edit/connectionRules'
import { createDraftController, type DraftEditState } from './edit/draftController'
import { CLIENT_KEY_PREFIX, editableRevision, type CanvasNodeSpec } from './edit/draftModel'
import type { NodeMove, NodePlacement } from './edit/editHistory'
import { fixtureFlowGraphSource } from './graph/fixtureFlowGraphSource'
import { isEdgeProgressed } from './graph/edgeProgress'
import type { FlowGraph, FlowGraphNode, FlowGraphRun, FlowGraphSelection } from './graph/flowGraph'
import type { FlowGraphSource, FlowLayoutChange, FlowRunInfo } from './graph/flowGraphSource'
import { createLayoutSaveQueue } from './layoutSaveQueue'
import { createFlowRunControl, type FlowRunFailure, type FlowRunMode, type FlowRunState } from './run/flowRunControl'
import { NO_RUN_MARKS, flowRunMarks, isEdgeProtected, isRunLocked, type FlowEditLock, type FlowRunMarks } from './run/flowRunLock'

export type { ArrangeKind, CanvasNodeSpec, FlowEditLock, FlowRunFailure, FlowRunMarks, FlowRunMode, FlowRunState, NodePlacement }

/** flowgate.v02.0061 TR0031 rev1: a Flow's run status as the screen acts on it. */
export interface FlowRunStatus {
  /** The run (null: none). Not `known`: the run last known, if any. */
  readonly run: FlowRunInfo | null
  /** Confirmed by the run read / a command's answer, or (that read pending or failed) by the Flow's Canvas read. */
  readonly known: boolean
}

const KNOWN_NO_RUN: FlowRunStatus = Object.freeze({ run: null, known: true })

/**
 * flowgate.v02.0059 T#1: a view operation the Toolbar / View menu asks of a Flow's Canvas. Fit and
 * the zoom steps need what only the Canvas knows (measured Nodes, its area, the pan bound), so the
 * Canvas performs them and reports the result as its Viewport; a new `seq` makes a repeated
 * request count again.
 */
export type CanvasViewOperation =
  | { kind: 'fit' }
  | { kind: 'zoom-in' }
  | { kind: 'zoom-out' }
  | { kind: 'zoom-to'; zoom: number }

export type CanvasViewRequest = CanvasViewOperation & { readonly flowId: string; readonly seq: number }

/** Graph read state of one open Flow. */
export type FlowGraphLoad =
  | { status: 'loading' }
  | { status: 'ready'; graph: FlowGraph }
  | { status: 'error'; message: string }

/** What the Project Explorer's `activate-flow` carries (ProjectExplorerFlowEvent fits). */
export interface CenterFlowOpenRequest {
  projectId: string
  flowId: string
  flowName: string
}

export interface DesignerCenterState {
  projectId: string | null
  tabs: FlowTabsState['tabs']
  activeFlowId: string | null
  /** Keyed by Flow id, one entry per open tab. */
  graphs: Record<string, FlowGraphLoad>
  /** Keyed by Flow id, one entry per open tab. */
  views: Record<string, CanvasViewState>
  /** flowgate.v02.0045 S3: draft editing state per open Flow with an editable / existing draft. */
  edits: Record<string, DraftEditState>
  /** flowgate.v02.0053 T#9: the server run per open Flow whose run was read or acted on. */
  runs: Record<string, FlowRunState>
}

export interface DesignerCenterSession {
  /** Read-only view of the whole Center state. */
  readonly state: DeepReadonly<DesignerCenterState>
  /** True when Graphs come from sample data (the Canvas shows a badge). */
  readonly isSampleSource: boolean
  readonly activeTab: ComputedRef<DeepReadonly<FlowTab> | null>
  /** Same object for as long as the same tab stays active (0003-D §4.1.3). */
  readonly activeFlowRef: ComputedRef<FlowRef | null>
  readonly activeGraphLoad: ComputedRef<FlowGraphLoad | null>
  readonly activeView: ComputedRef<DeepReadonly<CanvasViewState> | null>
  /** flowgate.v02.0048 T#1: the selected Node with its Flow's Graph and the project, or null. */
  readonly activeSelection: ComputedRef<FlowGraphSelection | null>
  /**
   * flowgate.v02.0036 — the selected Node of the active Flow's ready Graph, or null (no tab, Graph
   * not ready, nothing selected). The bottom Inspector reads it; it is derived, never stored.
   */
  readonly activeSelectedNode: ComputedRef<FlowGraphNode | null>
  /**
   * flowgate.v02.0049 — the Canvas area size the shown Canvas last reported, or null (none shown
   * yet). One Canvas area serves every tab; the MiniMap turns it and the Viewport into the shown area.
   */
  readonly canvasSize: ComputedRef<CanvasSize | null>

  /** Sets the current project; a different project clears every tab, Graph and view. */
  setProject(projectId: string | null): void
  /**
   * Project Explorer activation: open a new tab (and read its Graph) or activate the open one.
   * flowgate.v02.0045 T0019: `seedNode` — a just created Flow's default node (see above).
   */
  openFlow(request: CenterFlowOpenRequest, options?: { seedNode?: CanvasNodeSpec }): void
  /** Activates an open tab (tab click / arrow keys). Never re-reads the Graph. */
  activateFlow(flowId: string): void
  /** Closes a tab and drops its Graph, Viewport and Selection. */
  closeFlow(flowId: string): void
  /** Reads the Graph of an open Flow again. */
  retryGraph(flowId: string): void
  /**
   * flowgate.v02.0048 T#1: reads an open Flow again in the background (the shown Graph stays until
   * the new one arrives), once this tab's saves of the Flow have settled.
   */
  refreshFlow(flowId: string): void
  /** Single selection of one Node, or none. */
  selectNode(flowId: string, nodeId: string | null): void
  /** flowgate.v02.0059 T#1: the Selection becomes these Nodes of the Flow (the Canvas in 선택 mode). */
  selectNodes(flowId: string, nodeIds: readonly string[]): void
  /** flowgate.v02.0059 T#1: 전체 선택 — every Node of the Flow's ready Graph, in 선택 mode. Whether it ran. */
  selectAll(flowId: string): boolean
  /** flowgate.v02.0059 T#1: the Toolbar's [선택] mode is on (area / click multi-selection). */
  readonly selectMode: ComputedRef<boolean>
  /** Turns [선택] mode on / off; off drops every Selection of more than one Node (and nothing else). */
  setSelectMode(on: boolean): void
  /** Records the Viewport the Canvas (or the Minimap / a later Toolbar) reports. */
  recordViewport(flowId: string, viewport: CanvasViewport): void
  /** flowgate.v02.0049: the shown Canvas reports its area size (when shown and on every resize). */
  recordCanvasSize(size: CanvasSize): void
  /** flowgate.v02.0045: a user pan / zoom ended — record it and save it (per user, per Flow). */
  commitViewport(flowId: string, viewport: CanvasViewport): void
  /** flowgate.v02.0059 T#1: the latest view operation asked of a Canvas (fit / zoom), or null. */
  readonly viewRequest: ComputedRef<CanvasViewRequest | null>
  /** Asks the open Flow's shown Canvas to fit / zoom; false when the Flow has no ready Graph. */
  requestView(flowId: string, operation: CanvasViewOperation): boolean
  /** flowgate.v02.0045: a movable Node was dropped at (x, y) — move it and save its position. */
  moveNode(flowId: string, nodeId: string, x: number, y: number): void
  /**
   * flowgate.v02.0059 T#2: movable Nodes moved by one action (a drop of the dragged selection) — moved and
   * saved like moveNode, one undo entry for all of them. Whether any Node moved.
   */
  moveNodes(flowId: string, placements: readonly NodePlacement[]): boolean
  /** flowgate.v02.0059 T#2: 정렬 / 분배 of the Flow's selected Nodes (one undo entry). Whether any Node moved. */
  arrangeNodes(flowId: string, kind: ArrangeKind): boolean
  /**
   * flowgate.v02.0059 T#2: 자동 배치 — the server's auto placement rules (auto_layout.py) with their spacing
   * times `scale` (1 = the server's), for every movable Node (one undo entry). Whether any Node moved —
   * at once, or once the Flow read whole answers when the shown Graph leaves part of the input out.
   */
  autoLayout(flowId: string, scale?: number): boolean | Promise<boolean>
  /** flowgate.v02.0059 T#2: every 자동 배치 still reading the whole Flow moves nothing when it answers. */
  cancelAutoLayout(): void
  /** flowgate.v02.0045: the Shell is going away — send pending saves and stop change reports. */
  dispose(): void

  // flowgate.v02.0045 S3+S4 — Graph draft editing (edit/draftController.ts).
  readonly activeEdit: ComputedRef<DeepReadonly<DraftEditState> | null>
  /** Something was dropped on the Canvas at Graph coordinates (x, y): a Palette card adds a node. */
  dropOnCanvas(flowId: string, transfer: Pick<DataTransfer, 'getData'>, x: number, y: number): boolean
  /** Whether the draft would take an edge source -> target (pre-check, the server decides). */
  checkConnection(flowId: string, source: string, target: string): ConnectionCheck
  connectNodes(flowId: string, source: string, target: string): boolean
  removeNode(flowId: string, nodeId: string): boolean
  removeEdge(flowId: string, edgeId: string): boolean
  /** Takes back the Flow's last structure edit or card move (flowgate.v02.0059 T#2: one history). */
  undoEdit(flowId: string): boolean
  /** flowgate.v02.0059 T#2: takes back the last undo of the Flow's structure edits / card moves. */
  redoEdit(flowId: string): boolean
  validateDraft(flowId: string): Promise<void>
  confirmDraft(flowId: string): Promise<void>
  /**
   * flowgate.v02.0045 TR0020 rev2: 실행 is about to start on the Flow — its edited draft is
   * confirmed first (no separate 확정 on screen). Whether the run may start — at once when there
   * is nothing to confirm, else once the confirmation answered.
   */
  prepareRun(flowId: string): boolean | Promise<boolean>
  /** flowgate.v02.0045 TR0020 rev2: 검증 can run on the active Flow now (the Toolbar's button). */
  readonly activeCanValidate: ComputedRef<boolean>
  /** Read the Flow again, dropping local draft edits a refused save left. */
  reloadDraft(flowId: string): void

  // flowgate.v02.0053 T#9 — the Flow's server run (run/flowRunControl.ts).
  /** The source can run Flows (the sample source cannot). */
  readonly canRun: boolean
  /** The active Flow's run state, or null (none read yet). */
  readonly activeRun: ComputedRef<DeepReadonly<FlowRunState> | null>
  /** 다음 단계 (`next`) / 전체 실행 (`all`): confirms the draft (idle only), then one step / full command. */
  runFlow(flowId: string, mode: FlowRunMode): Promise<boolean>
  /** 중지 (T#7 Stop) of the Flow's running or paused run. Whether the server took it. */
  stopRun(flowId: string): Promise<boolean>
  /** flowgate.v02.0061 T#14: the source can pause a run (일시정지). */
  readonly canPause: boolean
  /** flowgate.v02.0061 T#14 일시정지 of the Flow's running run. */
  pauseRun(flowId: string): Promise<boolean>
  /** flowgate.v02.0061 T#14 재개 of the Flow's paused run (its own command). */
  resumeRun(flowId: string): Promise<boolean>
  /**
   * flowgate.v02.0061 T#14: what the Flow's run means for its Canvas — the edit lock (running / paused / stopping),
   * the protected nodes (done / open / review) and the nodes the work is going through now (running lines).
   */
  runMarks(flowId: string): FlowRunMarks
  /** flowgate.v02.0061 T#14: the active Flow's run marks (TR0031 rev1: the lock also without a ready Graph). */
  readonly activeRunMarks: ComputedRef<FlowRunMarks>
  /** flowgate.v02.0061 TR0031 rev1: the Flow's confirmed run status (`known` false: not confirmed yet). */
  runStatus(flowId: string): FlowRunStatus
  /** flowgate.v02.0061 TR0031 rev1: the active Flow's run status (no active Flow: no run). */
  readonly activeRunStatus: ComputedRef<FlowRunStatus>
  /** flowgate.v02.0056 F2-1: the source can approve a card (the Canvas offers [승인]). */
  readonly canApprove: boolean
  /**
   * flowgate.v02.0056 F2-1 — a Canvas card's [승인]: approves the card (O1 0005-TR §4-1) with the result its
   * Canvas read shows. Whether it was approved; a refusal is the Flow's run `error` (kind `approval`).
   */
  approveNode(flowId: string, nodeId: string): Promise<boolean>
}

export interface DesignerCenterSessionOptions {
  /** Graph source; the fixture source when omitted. Tests pass a fake one. */
  source?: FlowGraphSource
  /** Quiet time before a batched layout save is sent (layoutSaveQueue.ts default when omitted). */
  layoutSaveDelayMs?: number
  /** Quiet time before a draft save is sent (edit/draftEditor.ts default when omitted). */
  draftSaveDelayMs?: number
  /** Which node a Canvas drop means (the Shell reads Palette drags); absent = drops are ignored. */
  readDrop?: (transfer: Pick<DataTransfer, 'getData'>) => CanvasNodeSpec | null
}

export function createDesignerCenterSession(options: DesignerCenterSessionOptions = {}): DesignerCenterSession {
  const source = options.source ?? fixtureFlowGraphSource

  const state = reactive<DesignerCenterState>({
    projectId: null,
    tabs: EMPTY_FLOW_TABS.tabs,
    activeFlowId: null,
    graphs: {},
    views: {},
    edits: {},
    runs: {},
  })

  // Latest read token per Flow id; a result whose token is no longer the latest is dropped.
  // Cleared on close and on project change, so their late results are dropped too.
  const pendingReads = new Map<string, number>()
  let readSeq = 0

  const tabsState = (): FlowTabsState => ({ tabs: state.tabs, activeFlowId: state.activeFlowId })

  // Flows to read again once this tab's saves of the Flow have settled.
  const staleFlows = new Set<string>()
  // Node moves per Flow; a read compares it to tell whether a move happened meanwhile.
  const moveCounts = new Map<string, number>()
  // flowgate.v02.0059 T#2: the latest 자동 배치 asked per Flow; an earlier one still reading the whole Flow
  // moves nothing.
  const layoutRuns = new Map<string, number>()
  let layoutRunSeq = 0
  // flowgate.v02.0059 T#2: a new mark per Flow each time a read puts a Graph on screen (or its loading /
  // error); a 자동 배치 whose input was read for an earlier one moves nothing.
  const shownReads = new Map<string, number>()
  let shownReadSeq = 0
  function setShownLoad(flowId: string, load: FlowGraphLoad) {
    shownReadSeq += 1
    shownReads.set(flowId, shownReadSeq)
    state.graphs[flowId] = load
  }
  // flowgate.v02.0045 S3: the Graph definition a Graph Flow shows ('draft' | 'revision'), if chosen
  // (TR0020 rev3: chosen by readGraph itself, never by a button).
  const graphViews = new Map<string, string>()
  // flowgate.v02.0045 T0019: default nodes of just created Flows, until their Graph is first shown.
  const seeds = new Map<string, CanvasNodeSpec>()
  let stopWatching: (() => void) | null = null
  let disposed = false

  const isOpen = (projectId: string, flowId: string) =>
    state.projectId === projectId && findFlowTab(tabsState(), flowId) !== null

  // Bound to the project it was created for, so a save still waiting when the project changes
  // goes out (and re-reads) for that project, not the new one.
  function sendLayout(projectId: string | null, flowId: string, change: FlowLayoutChange): Promise<unknown> | undefined {
    if (projectId === null || !source.saveLayout) return undefined
    // Not at once: a newer batch of the Flow may be waiting or about to go out. The queue's idle
    // report (createQueue) reads once nothing of the Flow is waiting or running.
    const reread = () => {
      if (isOpen(projectId, flowId)) staleFlows.add(flowId)
    }
    let request: Promise<{ ignoredNodeIds: readonly string[] }>
    try {
      request = source.saveLayout(projectId, flowId, change)
    } catch (error) {
      request = Promise.reject(error)
    }
    return request.then((result) => {
      if (result.ignoredNodeIds.length > 0) reread()
    }, reread)
  }

  function createQueue(projectId: string | null) {
    return createLayoutSaveQueue(
      (flowId, change) => sendLayout(projectId, flowId, change),
      options.layoutSaveDelayMs,
      (flowId) => {
        if (projectId !== null && !drafts.busy(flowId) && staleFlows.delete(flowId) && isOpen(projectId, flowId)) {
          readGraph(flowId, true)
        }
      },
    )
  }

  let layoutQueue = createQueue(null)

  const savesBusy = (flowId: string) => layoutQueue.busy(flowId) || drafts.busy(flowId)

  function readWhenSaved(flowId: string) {
    if (savesBusy(flowId)) staleFlows.add(flowId)
    else readGraph(flowId, true)
  }

  function readyGraph(flowId: string): FlowGraph | null {
    const load = state.graphs[flowId]
    return load?.status === 'ready' ? load.graph : null
  }

  const drafts = createDraftController({
    source,
    projectId: () => state.projectId,
    graph: readyGraph,
    setGraph: (flowId, graph) => {
      if (!state.graphs[flowId]) return
      state.graphs[flowId] = { status: 'ready', graph: markRaw(graph) }
      // An edit, its undo or its redo may take selected Nodes away (flowgate.v02.0059 T#1).
      keepShownSelection(flowId, graph)
    },
    renameSelection: (flowId, keyMap) => {
      const view = state.views[flowId]
      if (view) setViewSelection(view, view.selectedNodeIds.map((id) => keyMap[id] ?? id))
    },
    queuePosition: (flowId, nodeId, x, y) => {
      if (source.saveLayout) layoutQueue.queuePosition(flowId, nodeId, x, y)
    },
    placeNodes: (flowId, placements) => placeNodes(flowId, placements).length > 0,
    keepPositions: (projectId, flowId, positions) => {
      if (!source.saveLayout) return
      if (projectId !== state.projectId) {
        sendLayout(projectId, flowId, { positions: [...positions], viewport: null })
        return
      }
      for (const p of positions) layoutQueue.queuePosition(flowId, p.nodeId, p.x, p.y)
      layoutQueue.flush(flowId)
      // The Flow opened again meanwhile may show the node without this position.
      if (!disposed && isOpen(projectId, flowId)) readWhenSaved(flowId)
    },
    reread: (flowId) => {
      if (state.projectId !== null && isOpen(state.projectId, flowId)) readWhenSaved(flowId)
    },
    settled: (flowId) => {
      if (state.projectId !== null && !savesBusy(flowId) && staleFlows.delete(flowId) && isOpen(state.projectId, flowId)) {
        readGraph(flowId, true)
      }
    },
    setView: (flowId, view) => {
      if (view === null) graphViews.delete(flowId)
      else graphViews.set(flowId, view)
    },
    readDrop: options.readDrop,
    delayMs: options.draftSaveDelayMs,
  }, state.edits)

  const runControl = createFlowRunControl({
    source,
    projectId: () => state.projectId,
    isOpen,
    prepare: (flowId) => prepareRun(flowId),
    refresh: (flowId) => refreshFlow(flowId),
  }, state.runs)

  /**
   * flowgate.v02.0061 TR0031 rev1: the run the Canvas read shows, with what the run last read adds when it is the same
   * run (its band and node runs; a newer status is the Canvas's). A Canvas showing no run means no locked run.
   */
  function canvasRunInfo(shown: FlowGraphRun | null, last: FlowRunInfo | null): FlowRunInfo | null {
    if (shown === null) return last && !isRunLocked(last) ? last : null
    if (last?.runId === shown.runId) return last.status === shown.status ? last : { ...last, status: shown.status }
    return { runId: shown.runId, status: shown.status, openNodeKeys: [] }
  }

  // flowgate.v02.0061 TR0031 rev1: the run status the lock and the run commands follow (see the head of this file).
  function runStatus(flowId: string): FlowRunStatus {
    const run = state.runs[flowId]
    const shown = readyGraph(flowId)?.run
    if (!runControl.available) return shown ? { run: canvasRunInfo(shown, null), known: true } : KNOWN_NO_RUN
    if (run?.readState === 'read') return { run: run.run, known: true }
    if (shown !== undefined) return { run: canvasRunInfo(shown, run?.run ?? null), known: true }
    return { run: run?.run ?? null, known: false }
  }

  // flowgate.v02.0061 T#14: the run's marks of a Flow's shown Graph (run/flowRunLock.ts); TR0031 rev1: the lock stays
  // whatever the Graph's load (no ready Graph: the lock alone).
  function runMarks(flowId: string): FlowRunMarks {
    const { run, known } = runStatus(flowId)
    return flowRunMarks(readyGraph(flowId), run, known)
  }

  /** flowgate.v02.0061 TR0031 rev1: a run command goes only on a confirmed status (the phase's own buttons). */
  function whenKnown(flowId: string, go: (run: FlowRunInfo | null) => boolean): boolean {
    const status = runStatus(flowId)
    return status.known && go(status.run)
  }

  const editLock = (flowId: string): FlowEditLock => runMarks(flowId).lock

  function checkConnection(flowId: string, source: string, target: string): ConnectionCheck {
    const marks = runMarks(flowId)
    if (marks.lock !== null) return { ok: false, reason: 'locked' }
    if (marks.protectedNodeIds.has(target)) return { ok: false, reason: 'protected' }
    return drafts.check(flowId, source, target)
  }

  function changedElsewhere(projectId: string, flowId: string) {
    if (isOpen(projectId, flowId)) readWhenSaved(flowId)
  }

  function watchProject(projectId: string | null) {
    stopWatching?.()
    stopWatching = null
    staleFlows.clear()
    if (projectId === null || !source.watchChanges) return
    stopWatching = source.watchChanges(projectId, {
      changed: (flowId) => changedElsewhere(projectId, flowId),
      resync: () => {
        for (const tab of state.tabs) changedElsewhere(projectId, tab.flowId)
      },
    })
  }

  function applyTabs(next: FlowTabsState) {
    state.tabs = next.tabs
    state.activeFlowId = next.activeFlowId
  }

  // `background`: a refresh — a ready Graph stays on screen until the new one arrives, and a
  // failed refresh keeps it (the next change report or retry reads again).
  /** flowgate.v02.0059 T#1: the Flow's Selection keeps only Nodes of the Graph now shown. */
  function keepShownSelection(flowId: string, graph: FlowGraph) {
    const view = state.views[flowId]
    if (!view || view.selectedNodeIds.length === 0) return
    const shown = new Set(graph.nodes.map((node) => node.id))
    setViewSelection(view, view.selectedNodeIds.filter((id) => shown.has(id)))
  }

  function readGraph(flowId: string, background = false) {
    const projectId = state.projectId
    if (projectId === null) return
    readSeq += 1
    const token = readSeq
    pendingReads.set(flowId, token)
    const keepShown = background && state.graphs[flowId]?.status === 'ready'
    if (!keepShown) setShownLoad(flowId, { status: 'loading' })
    const isCurrent = () => pendingReads.get(flowId) === token && state.projectId === projectId
    const movesAtStart = moveCounts.get(flowId) ?? 0
    const view = graphViews.get(flowId)

    let request: Promise<FlowGraph>
    try {
      request = view ? source.loadGraph(projectId, flowId, view) : source.loadGraph(projectId, flowId)
    } catch (error) {
      request = Promise.reject(error)
    }
    request.then(
      (graph) => {
        if (!isCurrent()) return
        pendingReads.delete(flowId)
        // flowgate.v02.0045: the read may predate a move made here or a save still waiting or
        // running — keep the shown Graph (with its local moves) and read again once the saves
        // settle. A Graph still loading (a reopened tab) shows this one meanwhile.
        const outdated = savesBusy(flowId) || (moveCounts.get(flowId) ?? 0) !== movesAtStart
        if (outdated && state.graphs[flowId]?.status === 'ready') {
          readWhenSaved(flowId)
          return
        }
        // TR0020 rev3: a Graph Flow with a stored draft shows the draft — read it instead.
        if (graph.ownership === 'graph' && graph.viewMode === 'revision' && graph.draft && !graph.draft.editable) {
          graphViews.set(flowId, 'draft')
          readGraph(flowId, true)
          return
        }
        const shown = source.saveDraft ? editableRevision(graph) ?? graph : graph
        setShownLoad(flowId, { status: 'ready', graph: markRaw(shown) })
        drafts.graphLoaded(flowId, shown)
        // flowgate.v02.0053 T#9: the run goes with the Graph it moves (a run event reads both again).
        runControl.read(flowId)
        const seed = seeds.get(flowId)
        if (seed) {
          seeds.delete(flowId)
          // Only on a Graph with nothing on it and no saved draft: never a second node.
          if (graph.draft?.editable === true && graph.draft.version === 0 && graph.nodes.length === 0) drafts.seed(flowId, seed)
        }
        // A retried Graph keeps the selection only when the selected Node still exists.
        const view = state.views[flowId]
        // flowgate.v02.0045: the user's saved Viewport, unless this tab already has one.
        if (view && view.viewport === null && graph.viewport) {
          view.viewport = { x: graph.viewport.x, y: graph.viewport.y, zoom: graph.viewport.zoom }
        }
        keepShownSelection(flowId, graph)
        if (outdated) readWhenSaved(flowId)
      },
      (error: unknown) => {
        if (!isCurrent()) return
        pendingReads.delete(flowId)
        // TR0020 rev3: the draft is gone meanwhile (run / removed elsewhere) — the server's default.
        if (view === 'draft') {
          graphViews.delete(flowId)
          readGraph(flowId, background)
          return
        }
        if (keepShown && state.graphs[flowId]?.status === 'ready') return
        setShownLoad(flowId, { status: 'error', message: error instanceof Error ? error.message : '' })
      },
    )
  }

  function setProject(projectId: string | null) {
    if (projectId === state.projectId) return
    layoutQueue.flushAll()
    drafts.closeAll()
    layoutQueue = createQueue(projectId)
    pendingReads.clear()
    moveCounts.clear()
    layoutRuns.clear()
    shownReads.clear()
    graphViews.clear()
    seeds.clear()
    runControl.clear()
    state.projectId = projectId
    watchProject(projectId)
    applyTabs(EMPTY_FLOW_TABS)
    state.graphs = {}
    state.views = {}
  }

  function openFlow(request: CenterFlowOpenRequest, options: { seedNode?: CanvasNodeSpec } = {}) {
    if (state.projectId === null || request.projectId !== state.projectId) return
    const { state: next, opened } = openFlowTab(tabsState(), request.flowId, request.flowName)
    applyTabs(next)
    if (!opened) return
    if (options.seedNode) seeds.set(request.flowId, options.seedNode)
    state.views[request.flowId] = createCanvasViewState()
    readGraph(request.flowId)
  }

  function activateFlow(flowId: string) {
    applyTabs(activateFlowTab(tabsState(), flowId))
  }

  function closeFlow(flowId: string) {
    if (!findFlowTab(tabsState(), flowId)) return
    layoutQueue.flush(flowId)
    drafts.close(flowId)
    applyTabs(closeFlowTab(tabsState(), flowId))
    pendingReads.delete(flowId)
    moveCounts.delete(flowId)
    layoutRuns.delete(flowId)
    shownReads.delete(flowId)
    graphViews.delete(flowId)
    seeds.delete(flowId)
    runControl.forget(flowId)
    delete state.graphs[flowId]
    delete state.views[flowId]
  }

  function retryGraph(flowId: string) {
    if (!findFlowTab(tabsState(), flowId)) return
    readGraph(flowId)
  }

  function refreshFlow(flowId: string) {
    if (state.projectId !== null) changedElsewhere(state.projectId, flowId)
  }

  function selectNode(flowId: string, nodeId: string | null) {
    const view = state.views[flowId]
    if (view) setViewSelection(view, nodeId === null ? [] : [nodeId])
  }

  const selectMode = shallowRef(false)

  function selectNodes(flowId: string, nodeIds: readonly string[]) {
    const view = state.views[flowId]
    if (!view) return
    const graph = readyGraph(flowId)
    const shown = graph ? new Set(graph.nodes.map((node) => node.id)) : null
    setViewSelection(view, shown ? nodeIds.filter((id) => shown.has(id)) : nodeIds)
  }

  function selectAll(flowId: string): boolean {
    const graph = readyGraph(flowId)
    const view = state.views[flowId]
    if (!graph || !view) return false
    selectMode.value = true
    setViewSelection(view, graph.nodes.map((node) => node.id))
    return true
  }

  function setSelectMode(on: boolean) {
    if (selectMode.value === on) return
    selectMode.value = on
    if (on) return
    for (const view of Object.values(state.views)) {
      if (view.selectedNodeIds.length > 1) setViewSelection(view, [])
    }
  }

  function recordViewport(flowId: string, viewport: CanvasViewport) {
    const view = state.views[flowId]
    if (!view || sameViewport(view.viewport, viewport)) return
    view.viewport = { x: viewport.x, y: viewport.y, zoom: viewport.zoom }
  }

  const canvasSize = shallowRef<CanvasSize | null>(null)

  function recordCanvasSize(size: CanvasSize) {
    if (!(size.width > 0) || !(size.height > 0) || sameCanvasSize(canvasSize.value, size)) return
    canvasSize.value = { width: size.width, height: size.height }
  }

  function commitViewport(flowId: string, viewport: CanvasViewport) {
    if (!state.views[flowId]) return
    recordViewport(flowId, viewport)
    if (source.saveLayout) layoutQueue.queueViewport(flowId, viewport)
  }

  const viewRequest = shallowRef<CanvasViewRequest | null>(null)
  let viewRequestSeq = 0

  function requestView(flowId: string, operation: CanvasViewOperation): boolean {
    if (state.graphs[flowId]?.status !== 'ready') return false
    viewRequestSeq += 1
    viewRequest.value = { ...operation, flowId, seq: viewRequestSeq }
    return true
  }

  /**
   * Puts movable Nodes of a ready Graph at new positions and saves them (a Node not movable, unknown or
   * already there stays). flowgate.v02.0059 T#2: the one path of every move — a drop, 정렬 / 분배,
   * 자동 배치, and an undone / redone move. The draft keeps the same positions. The moves made.
   */
  function placeNodes(flowId: string, placements: readonly NodePlacement[]): NodeMove[] {
    const load = state.graphs[flowId]
    if (load?.status !== 'ready') return []
    const at = new Map(placements.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).map((p) => [p.nodeId, p] as const))
    const moves: NodeMove[] = []
    const nodes = load.graph.nodes.map((node) => {
      const to = at.get(node.id)
      if (!to || node.movable !== true || (node.x === to.x && node.y === to.y)) return node
      moves.push({ nodeId: node.id, from: { x: node.x, y: node.y }, to: { x: to.x, y: to.y } })
      return { ...node, x: to.x, y: to.y, positionSource: 'saved' }
    })
    if (moves.length === 0) return moves
    state.graphs[flowId] = { status: 'ready', graph: markRaw({ ...load.graph, nodes }) }
    moveCounts.set(flowId, (moveCounts.get(flowId) ?? 0) + 1)
    for (const { nodeId, to } of moves) {
      // A node created here is stored with its position once its draft save answers its key.
      if (source.saveLayout && !nodeId.startsWith(CLIENT_KEY_PREFIX)) layoutQueue.queuePosition(flowId, nodeId, to.x, to.y)
    }
    drafts.placed(flowId, moves.map(({ nodeId, to }) => ({ nodeId, ...to })))
    return moves
  }

  /** flowgate.v02.0059 T#2: one action's moves — placed, and one entry of the Flow's undo / redo. */
  function moveNodes(flowId: string, placements: readonly NodePlacement[]): boolean {
    const moves = placeNodes(flowId, placements)
    if (moves.length > 0) drafts.recordMove(flowId, moves)
    return moves.length > 0
  }

  function arrangeNodes(flowId: string, kind: ArrangeKind): boolean {
    const graph = readyGraph(flowId)
    const view = state.views[flowId]
    return !!graph && !!view && moveNodes(flowId, arrangeSelection(graph.nodes, view.selectedNodeIds, kind))
  }

  function layOut(flowId: string, scale: number, basis?: FlowLayoutBasis): boolean {
    const graph = readyGraph(flowId)
    return !!graph && moveNodes(flowId, autoLayoutPlacements(graph, scale, basis))
  }

  function autoLayout(flowId: string, scale = 1): boolean | Promise<boolean> {
    const graph = readyGraph(flowId)
    const projectId = state.projectId
    if (disposed || !graph || projectId === null) return false
    layoutRunSeq += 1
    const run = layoutRunSeq
    layoutRuns.set(flowId, run)
    if (!needsLayoutBasis(graph) || !source.loadLayoutGraph) return layOut(flowId, scale)
    const view = graphViews.get(flowId)
    const shownAt = shownReads.get(flowId)
    const readDocument = source.readDocumentLink
      ? (docId: string) => source.readDocumentLink!(projectId, docId)
      : undefined
    // The answer belongs to the Graph shown when it was asked: not cancelled, the session alive, the tab open,
    // and no read put another Graph (or its loading) on screen meanwhile.
    const current = () =>
      !disposed && layoutRuns.get(flowId) === run && shownReads.get(flowId) === shownAt && isOpen(projectId, flowId)
    return loadLayoutBasis(() => source.loadLayoutGraph!(projectId, flowId, view), readDocument).then(
      (basis) => current() && layOut(flowId, scale, basis),
      () => false,
    )
  }

  function cancelAutoLayout() {
    layoutRuns.clear()
  }

  function dispose() {
    disposed = true
    layoutRuns.clear()
    drafts.closeAll()
    layoutQueue.flushAll()
    stopWatching?.()
    stopWatching = null
    staleFlows.clear()
  }

  // flowgate.v02.0045 TR0020 rev2: no 확정 step on screen. 실행 takes what was drawn: an editable,
  // confirmable draft with something saved (or being saved) is confirmed as the next revision (the
  // server's run unit) first, and the run starts only if that succeeds (refused: its issues show).
  // Nothing to confirm is a go at once (not through a promise), so the run state changes in the
  // same tick.
  function prepareRun(flowId: string): boolean | Promise<boolean> {
    const draft = readyGraph(flowId)?.draft
    if (!draft?.editable || !draft.confirmable || !drafts.drawn(flowId)) return true
    if (state.edits[flowId]?.action) return false
    return drafts.confirm(flowId).then(() => state.edits[flowId]?.notice?.kind === 'confirmed')
  }

  const exposed = readonly(state)

  const activeSelectedNode = computed(() => {
    const id = exposed.activeFlowId
    if (id === null) return null
    const load = exposed.graphs[id] as FlowGraphLoad | undefined
    const nodeId = exposed.views[id]?.selectedNodeId ?? null
    if (load?.status !== 'ready' || nodeId === null) return null
    return load.graph.nodes.find((node) => node.id === nodeId) ?? null
  })

  const activeTab = computed(() => {
    const id = exposed.activeFlowId
    return id === null ? null : exposed.tabs.find((tab) => tab.flowId === id) ?? null
  })

  return {
    state: exposed,
    isSampleSource: source.isSample,
    activeTab,
    activeFlowRef: computed(() => (activeTab.value?.flowRef as FlowRef | undefined) ?? null),
    activeGraphLoad: computed(() => {
      const id = state.activeFlowId
      return id === null ? null : ((exposed.graphs[id] as FlowGraphLoad | undefined) ?? null)
    }),
    activeView: computed(() => {
      const id = exposed.activeFlowId
      return id === null ? null : exposed.views[id] ?? null
    }),
    activeSelectedNode,
    activeSelection: computed(() => {
      const node = activeSelectedNode.value
      const id = exposed.activeFlowId
      const load = id === null ? undefined : (exposed.graphs[id] as FlowGraphLoad | undefined)
      if (!node || id === null || exposed.projectId === null || load?.status !== 'ready') return null
      // flowgate.v02.0061 T#14: the Inspector's card settings follow the run lock as the Canvas does.
      return { projectId: exposed.projectId, graph: load.graph, node, editLock: editLock(id) }
    }),
    canvasSize: computed(() => canvasSize.value),
    setProject,
    openFlow,
    activateFlow,
    closeFlow,
    retryGraph,
    refreshFlow,
    selectNode,
    selectNodes,
    selectAll,
    selectMode: computed(() => selectMode.value),
    setSelectMode,
    recordViewport,
    recordCanvasSize,
    commitViewport,
    viewRequest: computed(() => viewRequest.value),
    requestView,
    moveNode: (flowId, nodeId, x, y) => {
      moveNodes(flowId, [{ nodeId, x, y }])
    },
    moveNodes,
    arrangeNodes,
    autoLayout,
    cancelAutoLayout,
    dispose,
    activeEdit: computed(() => {
      const id = exposed.activeFlowId
      return id === null ? null : exposed.edits[id] ?? null
    }),
    dropOnCanvas: (flowId, transfer, x, y) => editLock(flowId) === null && drafts.drop(flowId, transfer, x, y),
    checkConnection,
    connectNodes: (flowId: string, source: string, target: string) =>
      checkConnection(flowId, source, target).ok && drafts.connect(flowId, source, target),
    removeNode: (flowId: string, nodeId: string) => {
      const marks = runMarks(flowId)
      return marks.lock === null && !marks.protectedNodeIds.has(nodeId) && drafts.removeNode(flowId, nodeId)
    },
    removeEdge: (flowId: string, edgeId: string) => {
      const graph = readyGraph(flowId)
      const marks = runMarks(flowId)
      const edge = graph?.edges.find((candidate) => candidate.id === edgeId)
      if (marks.lock !== null || (edge && isEdgeProtected(edge, marks.protectedNodeIds))) return false
      return !(graph && isEdgeProgressed(graph, edgeId)) && drafts.removeEdge(flowId, edgeId)
    },
    // flowgate.v02.0061 T#14: the history holds structure edits, so it waits while the run is locked.
    undoEdit: (flowId: string) => editLock(flowId) === null && drafts.undo(flowId),
    redoEdit: (flowId: string) => editLock(flowId) === null && drafts.redo(flowId),
    validateDraft: drafts.validate,
    confirmDraft: drafts.confirm,
    prepareRun,
    activeCanValidate: computed(() => {
      const id = exposed.activeFlowId
      const draft = id === null ? null : readyGraph(id)?.draft
      const edit = id === null ? undefined : exposed.edits[id]
      const status = edit?.status ?? 'idle'
      if (id === null || editLock(id) !== null) return false
      return !!draft?.editable && !edit?.action && status !== 'conflict' && (draft.version > 0 || status !== 'idle')
    }),
    reloadDraft: drafts.reload,
    canRun: runControl.available,
    activeRun: computed(() => {
      const id = exposed.activeFlowId
      return id === null ? null : exposed.runs[id] ?? null
    }),
    // TR0031 rev1: 다음 단계 / 전체 실행 only on a confirmed idle (or no) run; 일시정지 / 재개 / 중지 on a confirmed run.
    runFlow: (flowId, mode) =>
      whenKnown(flowId, (run) => !isRunLocked(run)) ? runControl.run(flowId, mode) : Promise.resolve(false),
    stopRun: (flowId) => (whenKnown(flowId, (run) => run !== null) ? runControl.stop(flowId) : Promise.resolve(false)),
    canPause: runControl.canPause,
    pauseRun: (flowId) => (whenKnown(flowId, (run) => run !== null) ? runControl.pause(flowId) : Promise.resolve(false)),
    resumeRun: (flowId) => (whenKnown(flowId, (run) => run !== null) ? runControl.resume(flowId) : Promise.resolve(false)),
    runMarks,
    activeRunMarks: computed(() => {
      const id = exposed.activeFlowId
      return id === null ? NO_RUN_MARKS : runMarks(id)
    }),
    runStatus,
    activeRunStatus: computed(() => {
      const id = exposed.activeFlowId
      return id === null ? KNOWN_NO_RUN : runStatus(id)
    }),
    canApprove: runControl.canApprove,
    approveNode: (flowId, nodeId) => {
      const node = readyGraph(flowId)?.nodes.find((candidate) => candidate.id === nodeId)
      if (!node?.nodeRun) return Promise.resolve(false)
      return runControl.approve(flowId, nodeId, node.nodeRun.resultDocId ?? null)
    },
  }
}

