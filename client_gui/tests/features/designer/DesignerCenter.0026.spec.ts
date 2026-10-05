/**
 * flowgate.v02.0026 — Designer Center screen (0003-D §7.1): a real Center session with a fake
 * Graph source, mounted with the real Vue Flow. jsdom measures nothing, so Vue Flow cannot
 * place Edges or replay d3 gestures here — those are checked in the browser (0003-D §7.2).
 * This file alone gives the Canvas a size and a ResizeObserver stand-in (no global setup).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import FlowEdgePath from '@/features/designer/center/canvas/FlowEdgePath.vue'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'

registerAppMessages(i18n)

// --- Canvas environment for Vue Flow under jsdom (this file only) ---------------------------
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))
let paneStyle: HTMLStyleElement

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
  // The one rule Vue Flow probes to tell its base stylesheet is loaded (jsdom applies no CSS imports).
  paneStyle = document.createElement('style')
  paneStyle.textContent = '.vue-flow__pane { z-index: 1; }'
  document.head.appendChild(paneStyle)
})

afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
  paneStyle.remove()
})
// -------------------------------------------------------------------------------------------

interface PendingRead {
  flowId: string
  resolve: (graph: FlowGraph) => void
  reject: (error: Error) => void
}

function graphOf(flowId: string, nodes: Array<[string, string, string?]> = [['n1', 'requirement', 'done'], ['n2', 'task']]): FlowGraph {
  return {
    flowId,
    nodes: nodes.map(([id, kind, state], i) => ({ id: `${flowId}:${id}`, kind, title: `${flowId} ${id}`, state, x: i * 260, y: i * 80 })),
    edges: nodes.length > 1 ? [{ id: `${flowId}:e1`, source: `${flowId}:${nodes[0][0]}`, target: `${flowId}:${nodes[1][0]}` }] : [],
  }
}

let reads: PendingRead[] = []
let center: DesignerCenterSession
let wrapper: VueWrapper<any> | null = null

function createCenter(isSample = true) {
  const source: FlowGraphSource = {
    isSample,
    loadGraph: (_projectId, flowId) => new Promise<FlowGraph>((resolve, reject) => reads.push({ flowId, resolve, reject })),
  }
  center = createDesignerCenterSession({ source })
  center.setProject('p')
  return center
}

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}

function mountCenter() {
  wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
  return wrapper
}

async function openReady(flowId: string, name: string, graph: FlowGraph = graphOf(flowId)) {
  center.openFlow({ projectId: 'p', flowId, flowName: name })
  await settle()
  reads.filter((r) => r.flowId === flowId).at(-1)!.resolve(graph)
  await settle()
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const tabs = (w: VueWrapper<any>) => w.findAll('[data-test="designer-flow-tab"]')
const tabButton = (w: VueWrapper<any>, flowId: string) => w.get(`[data-test="designer-flow-tab"][data-flow-id="${flowId}"] [role="tab"]`)
const nodeCard = (w: VueWrapper<any>, nodeId: string) => w.get(`[data-test="designer-canvas-node"][data-node-id="${nodeId}"]`)

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  reads = []
  createCenter()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('DesignerCenter — empty / loading / error', () => {
  it('without tabs shows the "no open Flow" empty state and no Canvas', () => {
    const w = mountCenter()
    expect(get(w, 'designer-center').exists()).toBe(true)
    expect(tabs(w)).toHaveLength(0)
    const empty = get(w, 'designer-center-no-flow')
    expect(empty.text()).toContain('열린 플로우 없음')
    expect(empty.text()).toContain('프로젝트 탐색기에서 플로우를 더블클릭하면 여기에 열립니다')
    expect(w.find('[data-test="designer-canvas"]').exists()).toBe(false)
  })

  it('shows loading, then the error with its message, and Retry reads again into the Canvas', async () => {
    const w = mountCenter()
    center.openFlow({ projectId: 'p', flowId: 'a', flowName: 'Alpha' })
    await settle()
    expect(get(w, 'designer-canvas-loading').text()).toContain('그래프를 불러오는 중')
    expect(w.find('[data-test="designer-center-no-flow"]').exists()).toBe(false)

    reads[0].reject(new Error('연결이 끊어졌습니다'))
    await settle()
    const error = get(w, 'designer-canvas-error')
    expect(error.text()).toContain('그래프를 불러오지 못했습니다')
    expect(error.text()).toContain('연결이 끊어졌습니다')
    expect(w.find('[data-test="designer-canvas"]').exists()).toBe(false)

    await get(w, 'designer-canvas-retry').trigger('click')
    await settle()
    expect(reads).toHaveLength(2)
    expect(w.find('[data-test="designer-canvas-loading"]').exists()).toBe(true)
    reads[1].resolve(graphOf('a'))
    await settle()
    expect(get(w, 'designer-canvas').attributes('data-flow-id')).toBe('a')
  })
})

describe('DesignerCenter — Flow Tabs', () => {
  it('shows each tab with aria-selected, activates on click and closes with its own button', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha')
    await openReady('b', 'Beta')
    await openReady('c', 'Gamma')

    expect(get(w, 'designer-flow-tabs').attributes('role')).toBe('tablist')
    expect(get(w, 'designer-flow-tabs').attributes('aria-label')).toBe('열린 플로우')
    expect(tabs(w).map((t) => t.attributes('data-flow-id'))).toEqual(['a', 'b', 'c'])
    expect(tabs(w).map((t) => t.get('[role="tab"]').text())).toEqual(['Alpha', 'Beta', 'Gamma'])
    expect(tabButton(w, 'c').attributes('aria-selected')).toBe('true')
    expect(tabButton(w, 'a').attributes('aria-selected')).toBe('false')
    expect(tabButton(w, 'c').attributes('tabindex')).toBe('0')
    expect(tabButton(w, 'a').attributes('tabindex')).toBe('-1')
    // The close button sits next to the tab button, never inside it.
    expect(tabButton(w, 'a').find('button').exists()).toBe(false)

    await tabButton(w, 'a').trigger('click')
    expect(center.state.activeFlowId).toBe('a')
    expect(tabButton(w, 'a').attributes('aria-selected')).toBe('true')

    const close = w.get('[data-test="designer-flow-tab"][data-flow-id="a"] [data-test="designer-flow-tab-close"]')
    expect(close.attributes('aria-label')).toBe('Alpha 닫기')
    await close.trigger('click')
    expect(tabs(w).map((t) => t.attributes('data-flow-id'))).toEqual(['b', 'c'])
    expect(center.state.activeFlowId).toBe('b')
  })

  it('left / right arrows move to the previous / next tab with wrap-around, and focus follows', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha')
    await openReady('b', 'Beta')
    await openReady('c', 'Gamma')

    await tabButton(w, 'c').trigger('keydown', { key: 'ArrowRight' })
    await nextTick()
    expect(center.state.activeFlowId).toBe('a')
    expect(document.activeElement).toBe(tabButton(w, 'a').element)

    await tabButton(w, 'a').trigger('keydown', { key: 'ArrowLeft' })
    await nextTick()
    expect(center.state.activeFlowId).toBe('c')
    expect(document.activeElement).toBe(tabButton(w, 'c').element)

    await tabButton(w, 'c').trigger('keydown', { key: 'ArrowLeft' })
    expect(center.state.activeFlowId).toBe('b')
  })
})

describe('DesignerCenter — Canvas', () => {
  it('renders one FlowGate Node card per Graph Node with kind, state and title', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha', graphOf('a', [['n1', 'requirement', 'done'], ['n2', 'task'], ['n3', 'mystery', 'weird']]))

    const canvas = get(w, 'designer-canvas')
    expect(canvas.attributes('aria-label')).toBe('Alpha 캔버스')
    const cards = w.findAll('[data-test="designer-canvas-node"]')
    expect(cards.map((c) => c.attributes('data-node-id'))).toEqual(['a:n1', 'a:n2', 'a:n3'])
    expect(cards.map((c) => c.attributes('data-kind'))).toEqual(['requirement', 'task', 'unknown'])
    expect(cards.map((c) => c.get('[data-test="designer-canvas-node-kind"]').text())).toEqual(['요구사항', '작업', '노드'])
    expect(cards.map((c) => c.get('[data-test="designer-canvas-node-title"]').text())).toEqual(['a n1', 'a n2', 'a n3'])
    expect(cards[0].get('[data-test="designer-canvas-node-state"]').text()).toBe('완료')
    // No state, and an unknown state key, both show no state.
    expect(cards[1].find('[data-test="designer-canvas-node-state"]').exists()).toBe(false)
    expect(cards[2].find('[data-test="designer-canvas-node-state"]').exists()).toBe(false)
    // FlowGate's own look: the library's default theme is not loaded.
    expect(w.find('.vue-flow__node-default').exists()).toBe(false)
  })

  it('a Node click selects that one Node; another click moves it; a pane click clears it', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha')

    await nodeCard(w, 'a:n1').trigger('click')
    expect(center.activeView.value!.selectedNodeId).toBe('a:n1')
    expect(nodeCard(w, 'a:n1').classes()).toContain('is-selected')
    expect(get(w, 'designer-canvas').attributes('data-selected-node-id')).toBe('a:n1')

    await nodeCard(w, 'a:n2').trigger('click')
    expect(center.activeView.value!.selectedNodeId).toBe('a:n2')
    expect(nodeCard(w, 'a:n1').classes()).not.toContain('is-selected')
    expect(nodeCard(w, 'a:n2').classes()).toContain('is-selected')

    // Clicking the selected Node again keeps it (no toggle).
    await nodeCard(w, 'a:n2').trigger('click')
    expect(center.activeView.value!.selectedNodeId).toBe('a:n2')

    await w.get('.vue-flow__pane').trigger('click')
    expect(center.activeView.value!.selectedNodeId).toBeNull()
    expect(w.findAll('.is-selected')).toHaveLength(0)
    expect(get(w, 'designer-canvas').attributes('data-selected-node-id')).toBe('')
  })

  it('reports Vue Flow Viewport changes to the session', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha')

    await useVueFlow('designer-canvas:a').setViewport({ x: 40, y: -20, zoom: 0.5 })
    await settle()
    expect(center.activeView.value!.viewport).toEqual({ x: 40, y: -20, zoom: 0.5 })
    expect(get(w, 'designer-canvas').attributes('data-zoom')).toBe('0.5')
  })

  it('applies a Viewport set on the session from elsewhere (later Minimap / Toolbar)', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha')

    center.recordViewport('a', { x: 12, y: 34, zoom: 1.5 })
    await settle()
    expect(get(w, 'designer-canvas').attributes('data-zoom')).toBe('1.5')
    expect(w.get('.vue-flow__transformationpane').attributes('style')).toContain('translate(12px,34px) scale(1.5)')
  })

  it('switching tabs swaps the Canvas Flow and Nodes and restores the other tab’s Selection and Viewport', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha')
    await openReady('b', 'Beta', graphOf('b', [['x1', 'test'], ['x2', 'approval'], ['x3', 'memo']]))

    expect(get(w, 'designer-canvas').attributes('data-flow-id')).toBe('b')
    expect(w.findAll('[data-test="designer-canvas-node"]').map((c) => c.attributes('data-node-id'))).toEqual(['b:x1', 'b:x2', 'b:x3'])
    await nodeCard(w, 'b:x3').trigger('click')
    await useVueFlow('designer-canvas:b').setViewport({ x: 5, y: 6, zoom: 0.75 })
    await settle()

    await tabButton(w, 'a').trigger('click')
    await settle()
    expect(get(w, 'designer-canvas').attributes('data-flow-id')).toBe('a')
    expect(w.findAll('[data-test="designer-canvas-node"]').map((c) => c.attributes('data-node-id'))).toEqual(['a:n1', 'a:n2'])
    expect(w.findAll('.is-selected')).toHaveLength(0)
    await nodeCard(w, 'a:n1').trigger('click')

    await tabButton(w, 'b').trigger('click')
    await settle()
    expect(get(w, 'designer-canvas').attributes('data-flow-id')).toBe('b')
    expect(nodeCard(w, 'b:x3').classes()).toContain('is-selected')
    expect(get(w, 'designer-canvas').attributes('data-zoom')).toBe('0.75')
    expect(w.get('.vue-flow__transformationpane').attributes('style')).toContain('translate(5px,6px) scale(0.75)')
    expect(center.state.views.a.selectedNodeId).toBe('a:n1')
  })

  it('shows the sample badge only for a sample source', async () => {
    let w = mountCenter()
    await openReady('a', 'Alpha')
    expect(get(w, 'designer-canvas-sample').text()).toBe('샘플 데이터')
    w.unmount()

    createCenter(false)
    w = mountCenter()
    await openReady('a', 'Alpha')
    expect(w.find('[data-test="designer-canvas"]').exists()).toBe(true)
    expect(w.find('[data-test="designer-canvas-sample"]').exists()).toBe(false)
  })

  it('a Graph without Nodes keeps the Canvas and shows the empty-Graph notice', async () => {
    const w = mountCenter()
    await openReady('a', 'Alpha', { flowId: 'a', nodes: [], edges: [] })
    expect(get(w, 'designer-canvas').attributes('data-zoom')).toBe('1')
    expect(get(w, 'designer-canvas-empty-graph').text()).toBe('표시할 노드가 없습니다')
    expect(w.findAll('[data-test="designer-canvas-node"]')).toHaveLength(0)
  })
})

describe('FlowEdgePath', () => {
  it('draws a curve and an arrow head that ends at the target anchor', () => {
    const w = mount(
      { components: { FlowEdgePath }, template: '<svg><FlowEdgePath id="e1" :source-x="0" :source-y="0" :target-x="200" :target-y="100" /></svg>' },
      { global: { plugins: [i18n] } },
    )
    const edge = w.get('[data-test="designer-canvas-edge"]')
    expect(edge.attributes('data-edge-id')).toBe('e1')
    const path = edge.get('[data-test="designer-canvas-edge-path"]').attributes('d')
    expect(path).toMatch(/^M0,0 C/)
    const arrow = edge.get('[data-test="designer-canvas-edge-arrow"]').attributes('d')
    // Tip on the target anchor; the base sits left of it (target handle on the left side).
    expect(arrow).toMatch(/^M200,100 L191,/)
    expect(arrow.endsWith('Z')).toBe(true)
  })
})
