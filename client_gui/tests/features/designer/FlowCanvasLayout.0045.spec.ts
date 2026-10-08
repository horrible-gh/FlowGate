/**
 * flowgate.v02.0045 T0011 (S1+S2) — Designer Center screen with a server-like Graph source and
 * the real Vue Flow: ownership badge instead of the sample badge, a dropped movable Node becomes
 * a saved move, a user pan / zoom end becomes a saved Viewport, a programmatic Viewport change
 * saves nothing. jsdom replays no d3 gestures, so the library events are emitted through the
 * Vue Flow store (as the drag / zoom handlers would).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow, type GraphNode } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'

registerAppMessages(i18n)

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

function graph(ownership: string): FlowGraph {
  return {
    flowId: 'f1',
    ownership,
    viewport: null,
    nodes: [
      { id: 'doc:r', kind: 'requirement', title: 'R', state: 'done', x: 0, y: 0, positionSource: 'auto', movable: true },
      { id: 'slot_d', kind: 'design', title: 'D', state: 'draft', x: 260, y: 0, positionSource: 'auto', movable: true },
    ],
    edges: [{ id: 'seq:doc:r>slot_d', source: 'doc:r', target: 'slot_d', edgeClass: 'execution' }],
  }
}

let saves: FlowLayoutChange[]
let center: DesignerCenterSession
let wrapper: VueWrapper<any> | null = null

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}

async function mountWith(ownership = 'v01') {
  center = createDesignerCenterSession({
    layoutSaveDelayMs: 0,
    source: {
      isSample: false,
      loadGraph: async () => graph(ownership),
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

const wait = () => new Promise((resolve) => setTimeout(resolve, 5))

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  saves = []
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('FlowCanvas with a server Graph (flowgate.v02.0045)', () => {
  it('shows the ownership badge instead of the sample badge', async () => {
    const w = await mountWith('v01')
    expect(w.find('[data-test="designer-canvas-sample"]').exists()).toBe(false)
    const badge = w.get('[data-test="designer-canvas-ownership"]')
    expect(badge.text()).toBe('담당: v0.1(읽기)')
    expect(badge.attributes('data-ownership')).toBe('v01')
    wrapper!.unmount()
    const conflict = await mountWith('conflict')
    expect(conflict.get('[data-test="designer-canvas-ownership"]').text()).toBe('담당 충돌 — 운영자 정리 필요')
    wrapper!.unmount()
    const unknown = await mountWith('someday')
    expect(unknown.find('[data-test="designer-canvas-ownership"]').exists()).toBe(false)
  })

  it('turns a Node drop into a moved, saved position', async () => {
    await mountWith()
    const flow = useVueFlow('designer-canvas:f1')
    const node = { ...flow.findNode('slot_d')!, position: { x: 400, y: 120 } } as GraphNode
    flow.emits.nodeDragStop({ event: new MouseEvent('mouseup'), node, nodes: [node], intersections: undefined } as never)
    await settle()
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.nodes.find((n) => n.id === 'slot_d')).toMatchObject({ x: 400, y: 120 })
    await wait()
    expect(saves).toEqual([{ positions: [{ nodeId: 'slot_d', x: 400, y: 120 }], viewport: null }])
  })

  it('saves the Viewport only when a user pan / zoom ends', async () => {
    await mountWith()
    const flow = useVueFlow('designer-canvas:f1')
    await flow.setViewport({ x: 40, y: -20, zoom: 0.5 })
    await settle()
    await wait()
    expect(center.activeView.value!.viewport).toEqual({ x: 40, y: -20, zoom: 0.5 })
    expect(saves).toEqual([])
    flow.emits.viewportChangeEnd({ x: 12, y: 34, zoom: 0.8 })
    await settle()
    await wait()
    expect(saves).toEqual([{ positions: [], viewport: { x: 12, y: 34, zoom: 0.8 } }])
  })
})
