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
import { computed, markRaw, reactive, readonly, type ComputedRef, type DeepReadonly } from 'vue'
import type { FlowRef } from '@/features/project'
import { createCanvasViewState, sameViewport, type CanvasViewState, type CanvasViewport } from './canvasView'
import {
  EMPTY_FLOW_TABS,
  activateFlowTab,
  closeFlowTab,
  findFlowTab,
  openFlowTab,
  type FlowTab,
  type FlowTabsState,
} from './flowTabs'
import { fixtureFlowGraphSource } from './graph/fixtureFlowGraphSource'
import type { FlowGraph, FlowGraphNode } from './graph/flowGraph'
import type { FlowGraphSource, FlowLayoutChange } from './graph/flowGraphSource'
import { createLayoutSaveQueue } from './layoutSaveQueue'

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

  /** Sets the current project; a different project clears every tab, Graph and view. */
  setProject(projectId: string | null): void
  /** Project Explorer activation: open a new tab (and read its Graph) or activate the open one. */
  openFlow(request: CenterFlowOpenRequest): void
  /** Activates an open tab (tab click / arrow keys). Never re-reads the Graph. */
  activateFlow(flowId: string): void
  /** Closes a tab and drops its Graph, Viewport and Selection. */
  closeFlow(flowId: string): void
  /** Reads the Graph of an open Flow again. */
  retryGraph(flowId: string): void
  /** Single selection of one Node, or none. */
  selectNode(flowId: string, nodeId: string | null): void
  /** Records the Viewport the Canvas (or a later Minimap / Toolbar) reports. */
  recordViewport(flowId: string, viewport: CanvasViewport): void
  /** flowgate.v02.0045: a user pan / zoom ended — record it and save it (per user, per Flow). */
  commitViewport(flowId: string, viewport: CanvasViewport): void
  /** flowgate.v02.0045: a movable Node was dropped at (x, y) — move it and save its position. */
  moveNode(flowId: string, nodeId: string, x: number, y: number): void
  /** flowgate.v02.0045: the Shell is going away — send pending saves and stop change reports. */
  dispose(): void
}

export interface DesignerCenterSessionOptions {
  /** Graph source; the fixture source when omitted. Tests pass a fake one. */
  source?: FlowGraphSource
  /** Quiet time before a batched layout save is sent (layoutSaveQueue.ts default when omitted). */
  layoutSaveDelayMs?: number
}

export function createDesignerCenterSession(options: DesignerCenterSessionOptions = {}): DesignerCenterSession {
  const source = options.source ?? fixtureFlowGraphSource

  const state = reactive<DesignerCenterState>({
    projectId: null,
    tabs: EMPTY_FLOW_TABS.tabs,
    activeFlowId: null,
    graphs: {},
    views: {},
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
  let stopWatching: (() => void) | null = null

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
        if (projectId !== null && staleFlows.delete(flowId) && isOpen(projectId, flowId)) readGraph(flowId, true)
      },
    )
  }

  let layoutQueue = createQueue(null)

  function readWhenSaved(flowId: string) {
    if (layoutQueue.busy(flowId)) staleFlows.add(flowId)
    else readGraph(flowId, true)
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

    let request: Promise<FlowGraph>
    try {
      request = source.loadGraph(projectId, flowId)
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
        const outdated = layoutQueue.busy(flowId) || (moveCounts.get(flowId) ?? 0) !== movesAtStart
        if (outdated && state.graphs[flowId]?.status === 'ready') {
          readWhenSaved(flowId)
          return
        }
        state.graphs[flowId] = { status: 'ready', graph: markRaw(graph) }
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
        if (keepShown && state.graphs[flowId]?.status === 'ready') return
        state.graphs[flowId] = { status: 'error', message: error instanceof Error ? error.message : '' }
      },
    )
  }

  function setProject(projectId: string | null) {
    if (projectId === state.projectId) return
    layoutQueue.flushAll()
    layoutQueue = createQueue(projectId)
    pendingReads.clear()
    moveCounts.clear()
    state.projectId = projectId
    watchProject(projectId)
    applyTabs(EMPTY_FLOW_TABS)
    state.graphs = {}
    state.views = {}
  }

  function openFlow(request: CenterFlowOpenRequest) {
    if (state.projectId === null || request.projectId !== state.projectId) return
    const { state: next, opened } = openFlowTab(tabsState(), request.flowId, request.flowName)
    applyTabs(next)
    if (!opened) return
    state.views[request.flowId] = createCanvasViewState()
    readGraph(request.flowId)
  }

  function activateFlow(flowId: string) {
    applyTabs(activateFlowTab(tabsState(), flowId))
  }

  function closeFlow(flowId: string) {
    if (!findFlowTab(tabsState(), flowId)) return
    layoutQueue.flush(flowId)
    applyTabs(closeFlowTab(tabsState(), flowId))
    pendingReads.delete(flowId)
    moveCounts.delete(flowId)
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
    if (source.saveLayout) layoutQueue.queuePosition(flowId, nodeId, x, y)
  }

  function dispose() {
    layoutQueue.flushAll()
    stopWatching?.()
    stopWatching = null
    staleFlows.clear()
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
    setProject,
    openFlow,
    activateFlow,
    closeFlow,
    retryGraph,
    selectNode,
    recordViewport,
    commitViewport,
    moveNode,
    dispose,
  }
}

