/**
 * flowgate.v02.0026 — Designer Center session (0003-D §3.3.4 / §3.4 / §7.1). A fake Graph
 * source hands out one pending promise per read so each test decides when (and in which
 * order) reads finish.
 */
import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { isReadonly } from 'vue'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'

interface PendingRead {
  projectId: string
  flowId: string
  resolve: (graph: FlowGraph) => void
  reject: (error: Error) => void
}

function fakeSource(isSample = false) {
  const reads: PendingRead[] = []
  const source: FlowGraphSource = {
    isSample,
    loadGraph: vi.fn(
      (projectId: string, flowId: string) =>
        new Promise<FlowGraph>((resolve, reject) => reads.push({ projectId, flowId, resolve, reject })),
    ),
  }
  return { source, reads }
}

function graph(flowId: string, nodeIds: string[] = ['n1', 'n2']): FlowGraph {
  return {
    flowId,
    nodes: nodeIds.map((id, i) => ({ id: `${flowId}:${id}`, kind: 'task', title: id, x: i * 100, y: i * 50 })),
    edges: [],
  }
}

const open = (projectId: string, flowId: string, flowName = flowId.toUpperCase()) => ({ projectId, flowId, flowName })

function setup() {
  const fake = fakeSource()
  const center = createDesignerCenterSession({ source: fake.source })
  center.setProject('p')
  return { ...fake, center }
}

afterEach(() => vi.restoreAllMocks())

describe('opening Flows', () => {
  it('ignores an activation for another Project, or while no Project is set', () => {
    const fake = fakeSource()
    const center = createDesignerCenterSession({ source: fake.source })
    center.openFlow(open('p', 'a'))
    expect(center.state.tabs).toEqual([])
    center.setProject('p')
    center.openFlow(open('q', 'a'))
    expect(center.state.tabs).toEqual([])
    expect(fake.source.loadGraph).not.toHaveBeenCalled()
  })

  it('reads the Graph once for a new tab only; re-activation and tab switches never read again', () => {
    const { center, source } = setup()
    center.openFlow(open('p', 'a'))
    center.openFlow(open('p', 'b'))
    expect(source.loadGraph).toHaveBeenCalledTimes(2)
    expect(source.loadGraph).toHaveBeenNthCalledWith(1, 'p', 'a')
    expect(source.loadGraph).toHaveBeenNthCalledWith(2, 'p', 'b')

    center.openFlow(open('p', 'a'))
    center.activateFlow('b')
    center.activateFlow('a')
    expect(source.loadGraph).toHaveBeenCalledTimes(2)
    expect(center.state.tabs.map((t) => t.flowId)).toEqual(['a', 'b'])
    expect(center.state.activeFlowId).toBe('a')
  })

  it('goes loading -> ready, and error -> retry -> ready', async () => {
    const { center, reads, source } = setup()
    center.openFlow(open('p', 'a'))
    expect(center.activeGraphLoad.value).toEqual({ status: 'loading' })

    reads[0].reject(new Error('서버 오류'))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'error', message: '서버 오류' })

    center.retryGraph('a')
    expect(source.loadGraph).toHaveBeenCalledTimes(2)
    expect(center.activeGraphLoad.value).toEqual({ status: 'loading' })
    reads[1].resolve(graph('a'))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'ready', graph: graph('a') })
  })

  it('a source that throws synchronously ends in the error state', async () => {
    const center = createDesignerCenterSession({
      source: {
        isSample: false,
        loadGraph: () => {
          throw new Error('boom')
        },
      },
    })
    center.setProject('p')
    center.openFlow(open('p', 'a'))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'error', message: 'boom' })
  })

  it('exposes whether the source is sample data', () => {
    expect(createDesignerCenterSession({ source: fakeSource(true).source }).isSampleSource).toBe(true)
    expect(createDesignerCenterSession({ source: fakeSource(false).source }).isSampleSource).toBe(false)
    // The default source is the fixture.
    expect(createDesignerCenterSession().isSampleSource).toBe(true)
  })
})

describe('late Graph results are dropped', () => {
  it('after the tab was closed (and a re-opened tab starts fresh)', async () => {
    const { center, reads } = setup()
    center.openFlow(open('p', 'a'))
    center.closeFlow('a')
    reads[0].resolve(graph('a'))
    await flushPromises()
    expect(center.state.graphs).toEqual({})

    center.openFlow(open('p', 'a'))
    expect(center.activeGraphLoad.value).toEqual({ status: 'loading' })
    reads[0].resolve(graph('a', ['stale']))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'loading' })
    reads[1].resolve(graph('a'))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'ready', graph: graph('a') })
  })

  it('after the Project changed', async () => {
    const { center, reads } = setup()
    center.openFlow(open('p', 'a'))
    center.setProject('q')
    center.openFlow(open('q', 'a'))
    reads[0].resolve(graph('a', ['from-p']))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'loading' })
    reads[1].resolve(graph('a', ['from-q']))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'ready', graph: graph('a', ['from-q']) })
  })

  it('when a newer read of the same Flow started', async () => {
    const { center, reads } = setup()
    center.openFlow(open('p', 'a'))
    reads[0].reject(new Error('first'))
    await flushPromises()
    center.retryGraph('a')
    center.retryGraph('a')
    reads[2].resolve(graph('a', ['newest']))
    await flushPromises()
    reads[1].resolve(graph('a', ['older']))
    await flushPromises()
    expect(center.activeGraphLoad.value).toEqual({ status: 'ready', graph: graph('a', ['newest']) })
  })
})

describe('Project change', () => {
  it('clears tabs, active Flow, Graphs, Viewports and Selections; the same Project is a no-op', async () => {
    const { center, reads } = setup()
    center.openFlow(open('p', 'a'))
    center.openFlow(open('p', 'b'))
    reads[0].resolve(graph('a'))
    await flushPromises()
    center.selectNode('a', 'a:n1')
    center.recordViewport('a', { x: 1, y: 2, zoom: 0.5 })

    center.setProject('p')
    expect(center.state.tabs).toHaveLength(2)

    center.setProject('q')
    expect(center.state.projectId).toBe('q')
    expect(center.state.tabs).toEqual([])
    expect(center.state.activeFlowId).toBeNull()
    expect(center.state.graphs).toEqual({})
    expect(center.state.views).toEqual({})
    expect(center.activeFlowRef.value).toBeNull()
    expect(center.activeGraphLoad.value).toBeNull()
    expect(center.activeView.value).toBeNull()

    center.setProject(null)
    expect(center.state.projectId).toBeNull()
  })
})

describe('per-tab Viewport and Selection', () => {
  it('keeps each tab’s Viewport / Selection across switches and resets them when a tab is re-opened', async () => {
    const { center, reads } = setup()
    center.openFlow(open('p', 'a'))
    center.openFlow(open('p', 'b'))
    reads.forEach((r) => r.resolve(graph(r.flowId)))
    await flushPromises()

    center.selectNode('b', 'b:n2')
    center.recordViewport('b', { x: 10, y: 20, zoom: 1.5 })
    center.activateFlow('a')
    expect(center.activeView.value).toEqual({ viewport: null, selectedNodeId: null })
    center.selectNode('a', 'a:n1')
    center.recordViewport('a', { x: -5, y: 0, zoom: 0.5 })

    center.activateFlow('b')
    expect(center.activeView.value).toEqual({ viewport: { x: 10, y: 20, zoom: 1.5 }, selectedNodeId: 'b:n2' })
    center.activateFlow('a')
    expect(center.activeView.value).toEqual({ viewport: { x: -5, y: 0, zoom: 0.5 }, selectedNodeId: 'a:n1' })

    center.closeFlow('a')
    center.openFlow(open('p', 'a'))
    expect(center.activeView.value).toEqual({ viewport: null, selectedNodeId: null })
  })

  it('selection is single; null clears it; actions on unknown tabs do nothing', () => {
    const { center } = setup()
    center.openFlow(open('p', 'a'))
    center.selectNode('a', 'a:n1')
    center.selectNode('a', 'a:n2')
    expect(center.activeView.value!.selectedNodeId).toBe('a:n2')
    center.selectNode('a', null)
    expect(center.activeView.value!.selectedNodeId).toBeNull()

    center.selectNode('zzz', 'x')
    center.recordViewport('zzz', { x: 0, y: 0, zoom: 1 })
    center.retryGraph('zzz')
    center.closeFlow('zzz')
    expect(Object.keys(center.state.views)).toEqual(['a'])
  })

  it('a retried Graph keeps the Selection only when that Node still exists, and keeps the Viewport', async () => {
    const { center, reads } = setup()
    center.openFlow(open('p', 'a'))
    reads[0].resolve(graph('a', ['n1', 'n2']))
    await flushPromises()
    center.selectNode('a', 'a:n2')
    center.recordViewport('a', { x: 3, y: 4, zoom: 0.75 })

    center.retryGraph('a')
    reads[1].resolve(graph('a', ['n1', 'n2', 'n3']))
    await flushPromises()
    expect(center.activeView.value).toEqual({ viewport: { x: 3, y: 4, zoom: 0.75 }, selectedNodeId: 'a:n2' })

    center.retryGraph('a')
    reads[2].resolve(graph('a', ['n1']))
    await flushPromises()
    expect(center.activeView.value).toEqual({ viewport: { x: 3, y: 4, zoom: 0.75 }, selectedNodeId: null })
  })
})

describe('active FlowRef', () => {
  it('is the same object while the same tab stays active, even after other tabs close', () => {
    const { center } = setup()
    center.openFlow(open('p', 'a', 'Alpha'))
    center.openFlow(open('p', 'b', 'Beta'))
    center.openFlow(open('p', 'c', 'Gamma'))
    center.activateFlow('b')
    const ref = center.activeFlowRef.value
    expect(ref).toEqual({ groupId: 'b', name: 'Beta' })

    center.closeFlow('c')
    expect(center.activeFlowRef.value).toBe(ref)
    center.closeFlow('a')
    expect(center.activeFlowRef.value).toBe(ref)
    center.openFlow(open('p', 'b', 'Beta'))
    expect(center.activeFlowRef.value).toBe(ref)

    // A rename replaces it.
    center.openFlow(open('p', 'b', 'Beta 2'))
    expect(center.activeFlowRef.value).toEqual({ groupId: 'b', name: 'Beta 2' })
    expect(center.activeFlowRef.value).not.toBe(ref)
  })

  it('follows the close fallback and is null after the last tab closes', () => {
    const { center } = setup()
    center.openFlow(open('p', 'a', 'Alpha'))
    center.openFlow(open('p', 'b', 'Beta'))
    center.closeFlow('b')
    expect(center.activeFlowRef.value).toEqual({ groupId: 'a', name: 'Alpha' })
    center.closeFlow('a')
    expect(center.activeFlowRef.value).toBeNull()
    expect(center.activeTab.value).toBeNull()
  })
})

describe('read-only state', () => {
  it('the exposed state cannot be changed from outside', () => {
    const { center } = setup()
    center.openFlow(open('p', 'a'))
    expect(isReadonly(center.state)).toBe(true)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    ;(center.state as { activeFlowId: string | null }).activeFlowId = null
    ;(center.state.views.a as { selectedNodeId: string | null }).selectedNodeId = 'x'
    expect(center.state.activeFlowId).toBe('a')
    expect(center.state.views.a.selectedNodeId).toBeNull()
    expect(warn).toHaveBeenCalled()
  })
})
