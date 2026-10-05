// flowgate.v02.0026 — Flow Tabs rules (0003-D §3.1.2) as pure functions over an immutable
// tab state. No Vue, no screen: the Center session holds the state and applies these.
import type { FlowRef } from '@/features/project'

export interface FlowTab {
  readonly flowId: string
  readonly name: string
  /**
   * The FlowRef handed to the left Dock while this tab is active. Created once when the tab
   * opens and replaced only on a rename, so the File Explorer (which reloads whenever its
   * `flow` object changes) does not reload for unrelated tab changes (0003-D §4.1.3).
   */
  readonly flowRef: FlowRef
}

export interface FlowTabsState {
  /** Screen order. */
  readonly tabs: readonly FlowTab[]
  readonly activeFlowId: string | null
}

export const EMPTY_FLOW_TABS: FlowTabsState = Object.freeze({ tabs: Object.freeze([]), activeFlowId: null })

function createTab(flowId: string, name: string): FlowTab {
  // Same FlowRef content as the 0025 Shell: no branch.
  return { flowId, name, flowRef: { groupId: flowId, name } }
}

export function findFlowTab(state: FlowTabsState, flowId: string): FlowTab | null {
  return state.tabs.find((tab) => tab.flowId === flowId) ?? null
}

/**
 * Opens a Flow: a new Flow is appended and activated (`opened: true`); an open Flow is only
 * activated, keeping the order — its name (and FlowRef) change only when the name differs.
 */
export function openFlowTab(
  state: FlowTabsState,
  flowId: string,
  name: string,
): { state: FlowTabsState; opened: boolean } {
  const existing = findFlowTab(state, flowId)
  if (!existing) {
    return { state: { tabs: [...state.tabs, createTab(flowId, name)], activeFlowId: flowId }, opened: true }
  }
  const tabs =
    existing.name === name ? state.tabs : state.tabs.map((tab) => (tab === existing ? createTab(flowId, name) : tab))
  if (tabs === state.tabs && state.activeFlowId === flowId) return { state, opened: false }
  return { state: { tabs, activeFlowId: flowId }, opened: false }
}

/** Activates an open tab; unknown ids leave the state unchanged. */
export function activateFlowTab(state: FlowTabsState, flowId: string): FlowTabsState {
  if (state.activeFlowId === flowId || !findFlowTab(state, flowId)) return state
  return { tabs: state.tabs, activeFlowId: flowId }
}

/**
 * Closes a tab. Closing an inactive tab keeps the active one; closing the active tab
 * activates the tab right of it, else the one left of it, else nothing.
 */
export function closeFlowTab(state: FlowTabsState, flowId: string): FlowTabsState {
  const index = state.tabs.findIndex((tab) => tab.flowId === flowId)
  if (index === -1) return state
  const tabs = state.tabs.filter((_, i) => i !== index)
  if (state.activeFlowId !== flowId) return { tabs, activeFlowId: state.activeFlowId }
  const fallback = tabs[index] ?? tabs[index - 1] ?? null
  return { tabs, activeFlowId: fallback ? fallback.flowId : null }
}

/** The previous (-1) / next (+1) tab of `flowId`, wrapping around at both ends. */
export function adjacentFlowTabId(state: FlowTabsState, flowId: string, direction: 1 | -1): string | null {
  const count = state.tabs.length
  const index = state.tabs.findIndex((tab) => tab.flowId === flowId)
  if (index === -1) return null
  return state.tabs[(index + direction + count) % count].flowId
}
