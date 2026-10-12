/**
 * flowgate.v02.0059 T#1 — Toolbar / View menu fit and zoom reach the Canvas through the Center
 * session's view request: the Canvas runs them on its Vue Flow instance (zoom range, centre, pan
 * bound) and the result is the same Viewport the Toolbar value, the MiniMap and the saved layout
 * follow. jsdom replays no d3 gestures, so only the programmatic paths are exercised.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { CANVAS_MAX_ZOOM, CANVAS_MIN_ZOOM } from '@/features/designer/center/canvasView'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'

registerAppMessages(i18n)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
})

afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
})

function graphOf(empty: boolean): FlowGraph {
  return {
    flowId: 'f1',
    ownership: 'v01',
    viewport: null,
    nodes: empty
      ? []
      : [
          { id: 'doc:r', kind: 'requirement', title: 'R', state: 'done', x: 0, y: 0, positionSource: 'auto', movable: true },
          { id: 'slot_d', kind: 'design', title: 'D', state: 'draft', x: 260, y: 0, positionSource: 'auto', movable: true },
        ],
    edges: empty ? [] : [{ id: 'seq:doc:r>slot_d', source: 'doc:r', target: 'slot_d', edgeClass: 'execution' }],
  }
}

let saves: FlowLayoutChange[]
let center: DesignerCenterSession
let wrapper: VueWrapper<any> | null = null

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}
const wait = () => new Promise((resolve) => setTimeout(resolve, 5))

async function mountWith(empty = false) {
  center = createDesignerCenterSession({
    layoutSaveDelayMs: 0,
    source: {
      isSample: false,
      loadGraph: async () => graphOf(empty),
      saveLayout: async (_p, _f, change) => {
        saves.push(change)
        return { ignoredNodeIds: [] }
      },
    },
  })
  center.setProject('p')
  wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
  center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
  await settle()
  return wrapper
}

const zoomOf = () => center.activeView.value!.viewport!.zoom

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  saves = []
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('Canvas view requests (flowgate.v02.0059 T#1)', () => {
  it('zooms in and out about the Canvas inside the zoom range and stores the result', async () => {
    const w = await mountWith()
    center.recordViewport('f1', { x: 0, y: 0, zoom: 1 })
    await settle()
    expect(center.requestView('f1', { kind: 'zoom-in' })).toBe(true)
    await settle()
    await wait()
    expect(zoomOf()).toBeGreaterThan(1)
    expect(zoomOf()).toBeLessThanOrEqual(CANVAS_MAX_ZOOM)
    expect(w.get('[data-test="designer-canvas"]').attributes('data-zoom')).toBe(String(zoomOf()))
    expect(saves.at(-1)?.viewport?.zoom).toBe(zoomOf())

    center.requestView('f1', { kind: 'zoom-out' })
    await settle()
    await wait()
    expect(zoomOf()).toBeCloseTo(1, 5)
  })

  it('zooms to a preset (100%) and clamps a preset outside the range', async () => {
    await mountWith()
    center.requestView('f1', { kind: 'zoom-to', zoom: 0.5 })
    await settle()
    expect(zoomOf()).toBe(0.5)
    center.requestView('f1', { kind: 'zoom-to', zoom: 1 })
    await settle()
    expect(zoomOf()).toBe(1)
    center.requestView('f1', { kind: 'zoom-to', zoom: 9 })
    await settle()
    expect(zoomOf()).toBe(CANVAS_MAX_ZOOM)
    center.requestView('f1', { kind: 'zoom-to', zoom: 0.01 })
    await settle()
    expect(zoomOf()).toBe(CANVAS_MIN_ZOOM)
  })

  it('fits the Nodes without enlarging beyond 1x, and stores it', async () => {
    await mountWith()
    center.requestView('f1', { kind: 'zoom-to', zoom: 2 })
    await settle()
    saves = []
    // jsdom measures no Node: give them the size the browser would, or there is nothing to fit.
    const flow = useVueFlow('designer-canvas:f1')
    for (const id of ['doc:r', 'slot_d']) flow.findNode(id)!.dimensions = { width: 200, height: 80 }
    center.requestView('f1', { kind: 'fit' })
    await settle()
    await wait()
    const viewport = center.activeView.value!.viewport!
    expect(viewport.zoom).toBeLessThanOrEqual(1)
    expect(viewport.zoom).toBeGreaterThanOrEqual(CANVAS_MIN_ZOOM)
    expect([viewport.x, viewport.y].every(Number.isFinite)).toBe(true)
    expect(saves.at(-1)?.viewport).toEqual(viewport)
  })

  it('fits an empty Graph to the default view without failing', async () => {
    await mountWith(true)
    center.requestView('f1', { kind: 'zoom-to', zoom: 2 })
    await settle()
    center.requestView('f1', { kind: 'fit' })
    await settle()
    expect(center.activeView.value!.viewport).toEqual({ x: 0, y: 0, zoom: 1 })
  })

  it('refuses a request for a Flow without a ready Graph', async () => {
    await mountWith()
    expect(center.requestView('nope', { kind: 'fit' })).toBe(false)
    expect(center.viewRequest.value).toBeNull()
  })
})

describe('Toolbar zoom select (flowgate.v02.0059 T#1)', () => {
  it('offers the presets, adds the current zoom, and emits the chosen percent', async () => {
    const toolbar = mount(DesignerToolbar, { props: { runMode: null, density: 'normal', zoom: 130 }, global: { plugins: [i18n] } })
    const select = toolbar.get('[data-test="designer-tool-zoom-select"]')
    expect(select.findAll('option').map((o) => o.text())).toEqual(['25%', '50%', '75%', '100%', '130%', '150%', '200%'])
    expect((select.element as HTMLSelectElement).value).toBe('130')
    await select.setValue('50')
    expect(toolbar.emitted('update:zoom')).toEqual([[50]])
    expect(toolbar.get('[data-test="designer-tool-zoom"]').text()).toBe('130%')
  })

  it('is unavailable without a work target', () => {
    const toolbar = mount(DesignerToolbar, { props: { runMode: null, density: 'normal', zoom: 100, disabled: true }, global: { plugins: [i18n] } })
    expect(toolbar.get('[data-test="designer-tool-zoom-select"]').attributes('disabled')).toBeDefined()
  })
})
