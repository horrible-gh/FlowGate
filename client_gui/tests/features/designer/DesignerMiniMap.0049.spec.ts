/**
 * flowgate.v02.0049 — the bottom MiniMap: alone (what it draws, how a press / drag turns into a
 * Viewport) and inside the Center column with the real Canvas (Vue Flow) and a real Center
 * session — the Graph, Viewport and Canvas size it draws are the session's, and its moves reach
 * the Canvas. jsdom lays nothing out: the Canvas size comes from a stubbed offsetWidth / Height
 * (what Vue Flow measures) and the MiniMap box from a stubbed getBoundingClientRect.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'
import DesignerMiniMap from '@/features/designer/minimap/DesignerMiniMap.vue'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'

registerAppMessages(i18n)

// --- Canvas environment for Vue Flow under jsdom (this file only) ---------------------------
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))
let canvasWidth = 800
let canvasHeight = 600
let paneStyle: HTMLStyleElement

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => canvasWidth })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => canvasHeight })
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

const BOX = { left: 10, top: 20, width: 200, height: 200 }

function stubBox(w: VueWrapper<any>) {
  const svg = w.get('[data-test="designer-minimap-svg"]').element as SVGSVGElement
  svg.getBoundingClientRect = () => ({ ...BOX, x: BOX.left, y: BOX.top, right: BOX.left + BOX.width, bottom: BOX.top + BOX.height, toJSON: () => ({}) }) as DOMRect
}

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}

const graphOf = (flowId: string, extra: Partial<FlowGraph> = {}): FlowGraph => ({
  flowId,
  nodes: [
    { id: `${flowId}:n1`, kind: 'requirement', title: '요구', x: 0, y: 0, movable: true },
    { id: `${flowId}:n2`, kind: 'task', title: '작업', x: 400, y: 100, movable: true },
  ],
  edges: [
    { id: `${flowId}:e1`, source: `${flowId}:n1`, target: `${flowId}:n2`, edgeClass: 'execution' },
    { id: `${flowId}:r1`, source: `${flowId}:n2`, target: `${flowId}:n1`, edgeClass: 'reference' },
  ],
  ...extra,
})

/** A v0.1 Flow as the server Canvas read gives it: v0.1 ownership, no draft, nothing movable. */
const legacyGraph = (flowId: string): FlowGraph => ({
  flowId,
  ownership: 'v01',
  draft: null,
  viewMode: null,
  nodes: [
    { id: `${flowId}:doc1`, kind: 'requirement', title: 'R0001', x: 0, y: 0, movable: false },
    { id: `${flowId}:doc2`, kind: 'task', title: 'T0002', x: 300, y: 0, movable: false },
    { id: `${flowId}:doc3`, kind: 'test', title: 'TR0003', x: 600, y: 0, movable: false },
  ],
  edges: [
    { id: `${flowId}:x1`, source: `${flowId}:doc1`, target: `${flowId}:doc2`, edgeClass: 'execution' },
    { id: `${flowId}:x2`, source: `${flowId}:doc2`, target: `${flowId}:doc3`, edgeClass: 'execution' },
  ],
})

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  canvasWidth = 800
  canvasHeight = 600
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const nodeIds = (w: VueWrapper<any>) => w.findAll('[data-test="designer-minimap-node"]').map((n) => n.attributes('data-node-id'))
const edgeIds = (w: VueWrapper<any>) => w.findAll('[data-test="designer-minimap-edge"]').map((n) => n.attributes('data-edge-id'))
const viewRect = (w: VueWrapper<any>) => {
  const rect = w.get('[data-test="designer-minimap-viewport"]')
  return ['x', 'y', 'width', 'height'].map((a) => Number(rect.attributes(a)))
}

describe('DesignerMiniMap — drawing (flowgate.v02.0049)', () => {
  function mountMap(props: Record<string, unknown>) {
    wrapper = mount(DesignerMiniMap, {
      attachTo: document.body,
      props: { flowId: 'f', graph: graphOf('f'), viewport: { x: 0, y: 0, zoom: 1 }, canvasSize: { width: 800, height: 600 }, ...props },
      global: { plugins: [i18n] },
    })
    return wrapper
  }

  it('draws the Graph Nodes, the execution Edges the Canvas draws and the shown area', () => {
    const w = mountMap({ viewport: { x: -100, y: 50, zoom: 2 } })
    expect(nodeIds(w)).toEqual(['f:n1', 'f:n2'])
    expect(edgeIds(w)).toEqual(['f:e1'])
    expect(viewRect(w)).toEqual([50, -25, 400, 300])
    expect(w.get('[data-test="designer-minimap"]').attributes('data-empty')).toBe('false')
    // No buttons and no text of its own.
    expect(w.findAll('button')).toHaveLength(0)
    expect(w.text()).toBe('')
  })

  it('draws nothing without a Graph or with an empty Graph, and no shown area without a Canvas size', async () => {
    const w = mountMap({ graph: null })
    expect(w.get('[data-test="designer-minimap"]').attributes('data-empty')).toBe('true')
    expect(w.find('svg').exists()).toBe(false)
    await w.setProps({ graph: { flowId: 'f', nodes: [], edges: [] } })
    expect(w.find('svg').exists()).toBe(false)
    await w.setProps({ graph: graphOf('f'), canvasSize: null })
    expect(nodeIds(w)).toEqual(['f:n1', 'f:n2'])
    expect(w.find('[data-test="designer-minimap-viewport"]').exists()).toBe(false)
  })

  it('moves the Viewport in all directions and keeps its map boundary fixed', async () => {
    const w = mountMap({ viewport: { x: 0, y: 0, zoom: 2 } })
    stubBox(w)
    const svg = w.get('[data-test="designer-minimap-svg"]')
    const [bx, by, bw, bh] = svg.attributes('viewBox')!.split(' ').map(Number)
    const scale = Math.min(BOX.width / bw, BOX.height / bh)
    const at = (x: number, y: number) => ({
      clientX: BOX.left + (x - bx) * scale,
      clientY: BOX.top + (y - by) * scale,
    })

    await svg.trigger('pointerdown', { button: 0, pointerId: 1, ...at(200, 150) })
    await svg.trigger('pointermove', { pointerId: 1, ...at(300, 250) })
    const moved = w.emitted('viewport-change')!.at(-1)![0] as { x: number; y: number; zoom: number }
    expect(moved).toEqual({ x: -200, y: -200, zoom: 2 })
    await w.setProps({ viewport: moved })
    expect(svg.attributes('viewBox')).toBe(`${bx} ${by} ${bw} ${bh}`)
    await svg.trigger('pointerup', { pointerId: 1 })
    expect(w.emitted('viewport-commit')).toEqual([[moved]])

    await svg.trigger('pointerdown', { button: 0, pointerId: 2, ...at(999, 999) })
    const corner = w.emitted('viewport-change')!.at(-1)![0] as { x: number; y: number; zoom: number }
    expect(corner.x).toBeCloseTo(-(bw - 400) * 2)
    expect(corner.y).toBeCloseTo(-(bh - 300) * 2)
    await w.setProps({ viewport: corner })
    expect(svg.attributes('viewBox')).toBe(`${bx} ${by} ${bw} ${bh}`)
    await svg.trigger('pointercancel', { pointerId: 2 })

    await svg.trigger('pointerdown', { button: 0, pointerId: 3, ...at(0, 0) })
    const origin = w.emitted('viewport-change')!.at(-1)![0] as { x: number; y: number; zoom: number }
    expect(origin).toEqual({ x: 0, y: 0, zoom: 2 })
    await svg.trigger('pointerup', { pointerId: 3 })
  })

  it('drops a drag when the Flow changes and ignores presses with nothing shown', async () => {
    const w = mountMap({})
    stubBox(w)
    await w.get('[data-test="designer-minimap-svg"]').trigger('pointerdown', { button: 0, pointerId: 1, clientX: 15, clientY: 25 })
    const changes = w.emitted('viewport-change')?.length ?? 0
    await w.setProps({ flowId: 'g', graph: graphOf('g') })
    stubBox(w)
    await w.get('[data-test="designer-minimap-svg"]').trigger('pointermove', { pointerId: 1, clientX: 100, clientY: 100 })
    await w.get('[data-test="designer-minimap-svg"]').trigger('pointerup', { pointerId: 1 })
    expect(w.emitted('viewport-change')?.length ?? 0).toBe(changes)
    expect(w.emitted('viewport-commit')).toBeUndefined()

    await w.setProps({ canvasSize: null })
    await w.get('[data-test="designer-minimap-svg"]').trigger('pointerdown', { button: 0, pointerId: 3, clientX: 50, clientY: 50 })
    expect(w.emitted('viewport-change')?.length ?? 0).toBe(changes)
  })
})

describe('DesignerMiniMap — with the real Canvas and Center session (flowgate.v02.0049)', () => {
  let center: DesignerCenterSession
  let graphs: Record<string, FlowGraph>
  let saves: Array<{ flowId: string; change: FlowLayoutChange }>

  function setup() {
    graphs = { a: graphOf('a'), b: graphOf('b'), legacy: legacyGraph('legacy') }
    saves = []
    const source: FlowGraphSource = {
      isSample: false,
      loadGraph: (_projectId, flowId) => Promise.resolve(graphs[flowId]),
      saveLayout: (_projectId, flowId, change) => {
        saves.push({ flowId, change })
        return Promise.resolve({ ignoredNodeIds: [] })
      },
    }
    center = createDesignerCenterSession({ source, layoutSaveDelayMs: 0 })
    center.setProject('p')
    wrapper = mount(DesignerCenterColumn, {
      attachTo: document.body,
      props: { center, bottomHeight: 240 },
      global: { plugins: [i18n] },
    })
    return wrapper
  }

  async function open(flowId: string) {
    center.openFlow({ projectId: 'p', flowId, flowName: flowId })
    await settle()
  }

  const canvasNodeIds = (w: VueWrapper<any>) => w.findAll('[data-test="designer-canvas-node"]').map((n) => n.attributes('data-node-id'))

  it('draws the active Flow’s Canvas Nodes / Edges and the Canvas size Vue Flow measured', async () => {
    const w = setup()
    expect(w.get('[data-test="designer-minimap"]').attributes('data-empty')).toBe('true')
    await open('a')
    expect(nodeIds(w)).toEqual(canvasNodeIds(w))
    expect(edgeIds(w)).toEqual(['a:e1'])
    expect(center.canvasSize.value).toEqual({ width: 800, height: 600 })
  })

  it('follows Canvas Pan / Zoom, and its own moves pan the Canvas and are stored', async () => {
    const w = setup()
    await open('a')
    await useVueFlow('designer-canvas:a').setViewport({ x: -100, y: 60, zoom: 2 })
    await settle()
    // The shown area is the session's Viewport (as the Canvas reported it) over the Canvas size.
    const before = center.activeView.value!.viewport!
    expect(before.zoom).toBe(2)
    expect(viewRect(w)).toEqual([-before.x / 2, -before.y / 2, 400, 300])

    stubBox(w)
    const svg = w.get('[data-test="designer-minimap-svg"]')
    const [bx, by, bw, bh] = svg.attributes('viewBox')!.split(' ').map(Number)
    const scale = Math.min(BOX.width / bw, BOX.height / bh)
    const at = (x: number, y: number) => ({
      clientX: BOX.left + (BOX.width - bw * scale) / 2 + (x - bx) * scale,
      clientY: BOX.top + (BOX.height - bh * scale) / 2 + (y - by) * scale,
    })
    const [vx, vy] = viewRect(w)
    await svg.trigger('pointerdown', { button: 0, pointerId: 1, ...at(vx + 200, vy + 150) })
    await svg.trigger('pointermove', { pointerId: 1, ...at(vx + 250, vy + 200) })
    await svg.trigger('pointerup', { pointerId: 1 })
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 5))
    const view = center.activeView.value!.viewport!
    expect(view.zoom).toBe(2)
    // Dragged 50 World units right and down at zoom 2: the Canvas moved 100px the other way.
    expect(view.x).toBeCloseTo(before.x - 100)
    expect(view.y).toBeCloseTo(before.y - 100)
    expect(w.get('[data-test="designer-canvas"]').attributes('data-zoom')).toBe('2')
    expect(w.get('.vue-flow__transformationpane').attributes('style')).toContain(`translate(${view.x}px,${view.y}px) scale(2)`)
    expect(saves.some((s) => s.flowId === 'a' && s.change.viewport?.zoom === 2)).toBe(true)
  })

  it('shrinks a temporary Canvas exploration and keeps space occupied by a moved Node', async () => {
    const w = setup()
    await open('a')
    center.recordViewport('a', { x: 0, y: 0, zoom: 1 })
    await settle()
    const initial = w.get('[data-test="designer-minimap-svg"]').attributes('viewBox')!
    center.recordViewport('a', { x: -2400, y: -1800, zoom: 1 })
    await settle()
    const explored = w.get('[data-test="designer-minimap-svg"]').attributes('viewBox')!
    expect(Number(explored.split(' ')[2])).toBeGreaterThan(Number(initial.split(' ')[2]))
    center.recordViewport('a', { x: 0, y: 0, zoom: 1 })
    await settle()
    expect(w.get('[data-test="designer-minimap-svg"]').attributes('viewBox')).toBe(initial)

    center.moveNode('a', 'a:n2', 2800, 1900)
    await settle()
    const occupied = w.get('[data-test="designer-minimap-svg"]').attributes('viewBox')!
    expect(Number(occupied.split(' ')[2])).toBeGreaterThan(Number(initial.split(' ')[2]))
    expect(w.get('[data-test="designer-minimap-node"][data-node-id="a:n2"]').attributes('x')).toBe('2800')
  })

  it('follows a Node move and a changed Edge set of the shown Graph', async () => {
    const w = setup()
    await open('a')
    center.moveNode('a', 'a:n2', 520, 260)
    await settle()
    const moved = w.get('[data-test="designer-minimap-node"][data-node-id="a:n2"]')
    expect([moved.attributes('x'), moved.attributes('y')]).toEqual(['520', '260'])

    graphs.a = { ...graphOf('a'), edges: [] }
    center.retryGraph('a')
    await settle()
    expect(edgeIds(w)).toEqual([])
  })

  it('switches with the Flow tab, keeps nothing of a closed Flow and draws a v0.1 Flow the same way', async () => {
    const w = setup()
    await open('a')
    await open('legacy')
    expect(w.get('[data-test="designer-minimap"]').attributes('data-flow-id')).toBe('legacy')
    expect(nodeIds(w)).toEqual(['legacy:doc1', 'legacy:doc2', 'legacy:doc3'])
    expect(edgeIds(w)).toEqual(['legacy:x1', 'legacy:x2'])
    expect(w.find('[data-test="designer-minimap-viewport"]').exists()).toBe(true)

    center.activateFlow('a')
    await settle()
    expect(nodeIds(w)).toEqual(['a:n1', 'a:n2'])
    center.closeFlow('a')
    center.closeFlow('legacy')
    await settle()
    expect(w.get('[data-test="designer-minimap"]').attributes('data-empty')).toBe('true')
    expect(w.findAll('[data-test="designer-minimap-node"]')).toHaveLength(0)
  })

  it('moves the v0.1 Flow’s Canvas from the MiniMap too', async () => {
    const w = setup()
    await open('legacy')
    stubBox(w)
    const before = center.activeView.value!.viewport ?? { x: 0, y: 0, zoom: 1 }
    const svg = w.get('[data-test="designer-minimap-svg"]')
    await svg.trigger('pointerdown', { button: 0, pointerId: 1, clientX: BOX.left + BOX.width - 1, clientY: BOX.top + BOX.height - 1 })
    await svg.trigger('pointerup', { pointerId: 1 })
    await settle()
    const after = center.activeView.value!.viewport!
    expect(after.zoom).toBe(before.zoom)
    expect(after.x === before.x && after.y === before.y).toBe(false)
  })

  it('takes a new Canvas size after a resize (Canvas or Bottom Dock)', async () => {
    const w = setup()
    await open('a')
    await useVueFlow('designer-canvas:a').setViewport({ x: 0, y: 0, zoom: 1 })
    await settle()
    expect(viewRect(w)).toEqual([0, 0, 800, 600])
    canvasWidth = 640
    canvasHeight = 360
    // Vue Flow measures its area again on a resize (its observer or the window event).
    window.dispatchEvent(new Event('resize'))
    await settle()
    expect(center.canvasSize.value).toEqual({ width: 640, height: 360 })
    expect(viewRect(w)).toEqual([0, 0, 640, 360])
  })
})
