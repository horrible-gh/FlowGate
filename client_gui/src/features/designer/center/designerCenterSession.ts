// flowgate.v02.0026 — Designer Center session (0003-D §3.3.4 / §3.4): the single owner of the
// Center state — open tabs and their order, the active Flow, and per Flow the Graph read
// state, Viewport and Selection. Not a Pinia store (features/designer owns no store): the
// DesignerShell instance creates one session and it lives and dies with that Shell.
//
// Outside code sees the state read-only and changes it only through the actions below. The
// tab rules are the pure functions of flowTabs.ts; Graphs come from a FlowGraphSource
// (fixture by default). A Graph read that arrives after its tab closed, the project changed or
// a newer read of the same Flow started is dropped.
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
import type { FlowGraphSource } from './graph/flowGraphSource'

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
}

export interface DesignerCenterSessionOptions {
  /** Graph source; the fixture source when omitted. Tests pass a fake one. */
  source?: FlowGraphSource
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

  function applyTabs(next: FlowTabsState) {
    state.tabs = next.tabs
    state.activeFlowId = next.activeFlowId
  }

  function readGraph(flowId: string) {
    const projectId = state.projectId
    if (projectId === null) return
    readSeq += 1
    const token = readSeq
    pendingReads.set(flowId, token)
    state.graphs[flowId] = { status: 'loading' }
    const isCurrent = () => pendingReads.get(flowId) === token && state.projectId === projectId

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
        state.graphs[flowId] = { status: 'ready', graph: markRaw(graph) }
        // A retried Graph keeps the selection only when the selected Node still exists.
        const view = state.views[flowId]
        if (view?.selectedNodeId && !graph.nodes.some((node) => node.id === view.selectedNodeId)) {
          view.selectedNodeId = null
        }
      },
      (error: unknown) => {
        if (!isCurrent()) return
        pendingReads.delete(flowId)
        state.graphs[flowId] = { status: 'error', message: error instanceof Error ? error.message : '' }
      },
    )
  }

  function setProject(projectId: string | null) {
    if (projectId === state.projectId) return
    pendingReads.clear()
    state.projectId = projectId
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
    applyTabs(closeFlowTab(tabsState(), flowId))
    pendingReads.delete(flowId)
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
  }
}
