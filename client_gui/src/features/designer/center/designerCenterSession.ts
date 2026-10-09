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
// flowgate.v02.0045 TR0020 rev3: no 편집 / 편집 마치기 — which definition a Graph Flow shows is
// picked here. A stored draft (edits not run yet) is shown; with none, the latest revision (the
// run view) is shown editable as is (draftModel `editableRevision`) and its first edit saves a new
// draft from it. 실행 confirms the draft as the next revision and the Flow shows its run again.
// Revisions and runs are never changed by editing (the server keeps them apart from the draft).
import { computed, markRaw, reactive, readonly, shallowRef, type ComputedRef, type DeepReadonly } from 'vue'
import type { FlowRef } from '@/features/project'
import {
  createCanvasViewState,
  sameCanvasSize,
  sameViewport,
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
import type { ConnectionCheck } from './edit/connectionRules'
import { createDraftController, type DraftEditState } from './edit/draftController'
import { CLIENT_KEY_PREFIX, editableRevision, type CanvasNodeSpec } from './edit/draftModel'
import { fixtureFlowGraphSource } from './graph/fixtureFlowGraphSource'
import { isEdgeProgressed } from './graph/edgeProgress'
import type { FlowGraph, FlowGraphNode } from './graph/flowGraph'
import type { FlowGraphSource, FlowLayoutChange } from './graph/flowGraphSource'
import { createLayoutSaveQueue } from './layoutSaveQueue'

export type { CanvasNodeSpec }

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
  /** Single selection of one Node, or none. */
  selectNode(flowId: string, nodeId: string | null): void
  /** Records the Viewport the Canvas (or the Minimap / a later Toolbar) reports. */
  recordViewport(flowId: string, viewport: CanvasViewport): void
  /** flowgate.v02.0049: the shown Canvas reports its area size (when shown and on every resize). */
  recordCanvasSize(size: CanvasSize): void
  /** flowgate.v02.0045: a user pan / zoom ended — record it and save it (per user, per Flow). */
  commitViewport(flowId: string, viewport: CanvasViewport): void
  /** flowgate.v02.0045: a movable Node was dropped at (x, y) — move it and save its position. */
  moveNode(flowId: string, nodeId: string, x: number, y: number): void
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
  undoEdit(flowId: string): boolean
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
      if (state.graphs[flowId]) state.graphs[flowId] = { status: 'ready', graph: markRaw(graph) }
    },
    renameSelection: (flowId, keyMap) => {
      const view = state.views[flowId]
      if (view?.selectedNodeId && keyMap[view.selectedNodeId]) view.selectedNodeId = keyMap[view.selectedNodeId]
    },
    queuePosition: (flowId, nodeId, x, y) => {
      if (source.saveLayout) layoutQueue.queuePosition(flowId, nodeId, x, y)
    },
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
  function readGraph(flowId: string, background = false) {
    const projectId = state.projectId
    if (projectId === null) return
    readSeq += 1
    const token = readSeq
    pendingReads.set(flowId, token)
    const keepShown = background && state.graphs[flowId]?.status === 'ready'
    if (!keepShown) state.graphs[flowId] = { status: 'loading' }
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
        state.graphs[flowId] = { status: 'ready', graph: markRaw(shown) }
        drafts.graphLoaded(flowId, shown)
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
        if (view?.selectedNodeId && !graph.nodes.some((node) => node.id === view.selectedNodeId)) {
          view.selectedNodeId = null
        }
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
        state.graphs[flowId] = { status: 'error', message: error instanceof Error ? error.message : '' }
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
    graphViews.clear()
    seeds.clear()
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
    graphViews.delete(flowId)
    seeds.delete(flowId)
    delete state.graphs[flowId]
    delete state.views[flowId]
  }

  function retryGraph(flowId: string) {
    if (!findFlowTab(tabsState(), flowId)) return
    readGraph(flowId)
  }

  function selectNode(flowId: string, nodeId: string | null) {
    const view = state.views[flowId]
    if (view && view.selectedNodeId !== nodeId) view.selectedNodeId = nodeId
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

  function moveNode(flowId: string, nodeId: string, x: number, y: number) {
    const load = state.graphs[flowId]
    if (load?.status !== 'ready' || !Number.isFinite(x) || !Number.isFinite(y)) return
    const node = load.graph.nodes.find((candidate) => candidate.id === nodeId)
    if (!node || node.movable !== true || (node.x === x && node.y === y)) return
    const nodes = load.graph.nodes.map((candidate) =>
      candidate.id === nodeId ? { ...candidate, x, y, positionSource: 'saved' } : candidate,
    )
    state.graphs[flowId] = { status: 'ready', graph: markRaw({ ...load.graph, nodes }) }
    moveCounts.set(flowId, (moveCounts.get(flowId) ?? 0) + 1)
    // A node created here is stored with its position once its draft save answers its key.
    if (source.saveLayout && !nodeId.startsWith(CLIENT_KEY_PREFIX)) layoutQueue.queuePosition(flowId, nodeId, x, y)
  }

  function dispose() {
    disposed = true
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
    activeSelectedNode: computed(() => {
      const id = exposed.activeFlowId
      if (id === null) return null
      const load = exposed.graphs[id] as FlowGraphLoad | undefined
      const nodeId = exposed.views[id]?.selectedNodeId ?? null
      if (load?.status !== 'ready' || nodeId === null) return null
      return load.graph.nodes.find((node) => node.id === nodeId) ?? null
    }),
    canvasSize: computed(() => canvasSize.value),
    setProject,
    openFlow,
    activateFlow,
    closeFlow,
    retryGraph,
    selectNode,
    recordViewport,
    recordCanvasSize,
    commitViewport,
    moveNode,
    dispose,
    activeEdit: computed(() => {
      const id = exposed.activeFlowId
      return id === null ? null : exposed.edits[id] ?? null
    }),
    dropOnCanvas: drafts.drop,
    checkConnection: drafts.check,
    connectNodes: drafts.connect,
    removeNode: drafts.removeNode,
    removeEdge: (flowId: string, edgeId: string) => {
      const graph = readyGraph(flowId)
      return !(graph && isEdgeProgressed(graph, edgeId)) && drafts.removeEdge(flowId, edgeId)
    },
    undoEdit: drafts.undo,
    validateDraft: drafts.validate,
    confirmDraft: drafts.confirm,
    prepareRun,
    activeCanValidate: computed(() => {
      const id = exposed.activeFlowId
      const draft = id === null ? null : readyGraph(id)?.draft
      const edit = id === null ? undefined : exposed.edits[id]
      const status = edit?.status ?? 'idle'
      return !!draft?.editable && !edit?.action && status !== 'conflict' && (draft.version > 0 || status !== 'idle')
    }),
    reloadDraft: drafts.reload,
  }
}

