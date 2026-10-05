/**
 * flowgate.v02.0026 — Flow Tabs rules (0003-D §3.1.2 / §7.1), pure functions.
 */
import { describe, expect, it } from 'vitest'
import {
  EMPTY_FLOW_TABS,
  activateFlowTab,
  adjacentFlowTabId,
  closeFlowTab,
  openFlowTab,
  type FlowTabsState,
} from '@/features/designer/center/flowTabs'

function openAll(...flows: Array<[string, string]>): FlowTabsState {
  return flows.reduce((state, [id, name]) => openFlowTab(state, id, name).state, EMPTY_FLOW_TABS)
}

const ids = (state: FlowTabsState) => state.tabs.map((tab) => tab.flowId)

describe('openFlowTab', () => {
  it('appends a new Flow at the end and activates it', () => {
    const first = openFlowTab(EMPTY_FLOW_TABS, 'a', 'A')
    expect(first.opened).toBe(true)
    expect(ids(first.state)).toEqual(['a'])
    expect(first.state.activeFlowId).toBe('a')
    expect(first.state.tabs[0].flowRef).toEqual({ groupId: 'a', name: 'A' })

    const second = openFlowTab(first.state, 'b', 'B')
    expect(second.opened).toBe(true)
    expect(ids(second.state)).toEqual(['a', 'b'])
    expect(second.state.activeFlowId).toBe('b')
  })

  it('re-activating an open Flow keeps the tab count and order and moves only the active tab', () => {
    const state = openAll(['a', 'A'], ['b', 'B'], ['c', 'C'])
    const result = openFlowTab(state, 'a', 'A')
    expect(result.opened).toBe(false)
    expect(ids(result.state)).toEqual(['a', 'b', 'c'])
    expect(result.state.activeFlowId).toBe('a')
    // Same tab objects (and FlowRefs) — nothing was re-created.
    expect(result.state.tabs[0]).toBe(state.tabs[0])
    expect(result.state.tabs[0].flowRef).toBe(state.tabs[0].flowRef)
  })

  it('re-activating the already active Flow under the same name changes nothing', () => {
    const state = openAll(['a', 'A'])
    expect(openFlowTab(state, 'a', 'A')).toEqual({ state, opened: false })
    expect(openFlowTab(state, 'a', 'A').state).toBe(state)
  })

  it('a renamed Flow updates only that tab name and FlowRef', () => {
    const state = openAll(['a', 'A'], ['b', 'B'])
    const result = openFlowTab(state, 'a', 'A2')
    expect(result.opened).toBe(false)
    expect(result.state.tabs.map((tab) => tab.name)).toEqual(['A2', 'B'])
    expect(result.state.tabs[0].flowRef).toEqual({ groupId: 'a', name: 'A2' })
    expect(result.state.tabs[0].flowRef).not.toBe(state.tabs[0].flowRef)
    expect(result.state.tabs[1]).toBe(state.tabs[1])
    expect(result.state.activeFlowId).toBe('a')
  })
})

describe('activateFlowTab', () => {
  it('activates an open tab and ignores unknown ids', () => {
    const state = openAll(['a', 'A'], ['b', 'B'])
    expect(activateFlowTab(state, 'a').activeFlowId).toBe('a')
    expect(ids(activateFlowTab(state, 'a'))).toEqual(['a', 'b'])
    expect(activateFlowTab(state, 'zzz')).toBe(state)
    expect(activateFlowTab(state, 'b')).toBe(state)
  })
})

describe('closeFlowTab', () => {
  it('closing an inactive tab keeps the active tab', () => {
    const state = activateFlowTab(openAll(['a', 'A'], ['b', 'B'], ['c', 'C']), 'b')
    const next = closeFlowTab(state, 'c')
    expect(ids(next)).toEqual(['a', 'b'])
    expect(next.activeFlowId).toBe('b')
    expect(closeFlowTab(state, 'a').activeFlowId).toBe('b')
  })

  it('closing the active tab activates the tab on its right', () => {
    const state = activateFlowTab(openAll(['a', 'A'], ['b', 'B'], ['c', 'C']), 'b')
    const next = closeFlowTab(state, 'b')
    expect(ids(next)).toEqual(['a', 'c'])
    expect(next.activeFlowId).toBe('c')
  })

  it('closing the active last tab activates the tab on its left', () => {
    const state = openAll(['a', 'A'], ['b', 'B'], ['c', 'C'])
    const next = closeFlowTab(state, 'c')
    expect(ids(next)).toEqual(['a', 'b'])
    expect(next.activeFlowId).toBe('b')
  })

  it('closing the only tab leaves no active tab', () => {
    const next = closeFlowTab(openAll(['a', 'A']), 'a')
    expect(next.tabs).toEqual([])
    expect(next.activeFlowId).toBeNull()
  })

  it('ignores unknown ids', () => {
    const state = openAll(['a', 'A'])
    expect(closeFlowTab(state, 'zzz')).toBe(state)
  })
})

describe('adjacentFlowTabId', () => {
  it('moves to the previous / next tab and wraps around at both ends', () => {
    const state = openAll(['a', 'A'], ['b', 'B'], ['c', 'C'])
    expect(adjacentFlowTabId(state, 'a', 1)).toBe('b')
    expect(adjacentFlowTabId(state, 'c', 1)).toBe('a')
    expect(adjacentFlowTabId(state, 'a', -1)).toBe('c')
    expect(adjacentFlowTabId(state, 'b', -1)).toBe('a')
    expect(adjacentFlowTabId(state, 'zzz', 1)).toBeNull()
  })
})

describe('EMPTY_FLOW_TABS', () => {
  it('is the cleared state: no tabs, no active Flow', () => {
    expect(EMPTY_FLOW_TABS).toEqual({ tabs: [], activeFlowId: null })
  })
})
