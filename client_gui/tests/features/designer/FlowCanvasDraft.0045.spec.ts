/**
 * flowgate.v02.0045 T0013 (S3+S4) — Designer Center screen editing a Graph draft with the real Vue
 * Flow: the draft bar (label, validate / confirm, issues), a Palette drop that becomes a draft
 * node, a connection between draft nodes (Vue Flow's connect event, as its handle drag would
 * emit it), Delete on the selected draft node, Ctrl+Z, and a v0.1 Flow that offers no editing.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent } from '@/features/designer/center/graph/flowGraphSource'
import { DEFAULT_PALETTE_ITEMS, paletteItemDefinition, paletteNodeSpec, readPaletteDragData, writePaletteDragData } from '@/features/designer/palette/paletteCatalog'

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

const def = (nodeType: string, docType: string | null, label: string) => ({ nodeType, docType, paletteItemId: null, label, assignee: null })

function draftGraph(): FlowGraph {
  return {
    flowId: 'f1',
    ownership: 'unassigned',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [
      { id: 'doc:r', kind: 'requirement', title: 'R 문서', state: 'done', x: 0, y: 0, movable: true },
      { id: 'gn_a', kind: 'requirement', title: '요구', state: 'draft', x: 0, y: 320, movable: true, structureEditable: true, definition: def('document', 'R', '요구') },
      { id: 'gn_b', kind: 'design', title: '설계', state: 'draft', x: 260, y: 320, movable: true, structureEditable: true, definition: def('document', 'D', '설계') },
    ],
    edges: [],
    draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    viewMode: 'draft',
  }
}

let center: DesignerCenterSession
let wrapper: VueWrapper<any> | null = null
let saved: FlowDraftContent[]

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}

async function mountWith(graph: FlowGraph) {
  center = createDesignerCenterSession({
    draftSaveDelayMs: 0,
    layoutSaveDelayMs: 0,
    readDrop: (transfer) => {
      const definition = readPaletteDragData(transfer)
      return definition ? paletteNodeSpec(definition) : null
    },
    source: {
      isSample: false,
      loadGraph: async () => graph,
      saveLayout: async () => ({ ignoredNodeIds: [] }),
      saveDraft: async (_p, _f, version, content) => {
        saved.push(content)
        return { version: version + 1, keyMap: {} }
      },
      validateDraft: async () => ({
        confirmable: false,
        issues: [{ code: 'rule_undecided', structure: 'join', nodeIds: ['gn_b'], edgeIds: [], message: '합류 규칙 미정' }],
      }),
      confirmDraft: async () => 1,
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
  saved = []
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('FlowCanvas editing a Graph draft (flowgate.v02.0045 S3)', () => {
  it('shows the draft bar and marks draft nodes connectable', async () => {
    const w = await mountWith(draftGraph())
    expect(w.get('[data-test="designer-draft-label"]').text()).toBe('초안 v2')
    expect(w.get('[data-test="designer-canvas"]').attributes('data-editable')).toBe('true')
    const cards = w.findAll('[data-test="designer-canvas-node"]')
    expect(cards.map((c) => [c.attributes('data-node-id'), c.attributes('data-connectable')])).toEqual([
      ['doc:r', 'false'], ['gn_a', 'true'], ['gn_b', 'true'],
    ])
  })

  it('a Palette card dropped on the Canvas becomes a draft node and is saved', async () => {
    const w = await mountWith(draftGraph())
    const store = new Map<string, string>()
    const transfer = { setData: (k: string, v: string) => store.set(k, v), getData: (k: string) => store.get(k) ?? '', effectAllowed: 'none', dropEffect: 'none' }
    const item = DEFAULT_PALETTE_ITEMS.find((i) => i.id === 'task')!
    writePaletteDragData(transfer as unknown as DataTransfer, paletteItemDefinition(item, '구현'))
    const canvas = w.get('[data-test="designer-canvas"]')
    const over = new Event('dragover', { cancelable: true }) as DragEvent
    Object.defineProperty(over, 'dataTransfer', { value: transfer })
    canvas.element.dispatchEvent(over)
    expect(over.defaultPrevented).toBe(true)
    const drop = new Event('drop', { cancelable: true }) as DragEvent
    Object.defineProperties(drop, { dataTransfer: { value: transfer }, clientX: { value: 300 }, clientY: { value: 200 } })
    canvas.element.dispatchEvent(drop)
    await settle()
    await wait()
    await settle()
    expect(saved).toHaveLength(1)
    expect(saved[0].nodes.map((n) => [n.docType, n.label, n.isNew])).toEqual([['R', '요구', false], ['D', '설계', false], ['T', '구현', true]])
  })

  it('connect, Delete and Ctrl+Z go through the draft commands', async () => {
    const w = await mountWith(draftGraph())
    const flow = useVueFlow('designer-canvas:f1')
    flow.emits.connect({ source: 'gn_a', target: 'gn_b', sourceHandle: null, targetHandle: null })
    flow.emits.connect({ source: 'doc:r', target: 'gn_b', sourceHandle: null, targetHandle: null })   // refused: not a draft node
    await settle()
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.edges.map((e) => [e.source, e.target])).toEqual([['gn_a', 'gn_b']])
    center.selectNode('f1', 'gn_b')
    await settle()
    const canvas = w.get('[data-test="designer-canvas"]')
    await canvas.trigger('keydown', { key: 'Delete' })
    expect(center.state.graphs.f1?.status === 'ready' && (center.state.graphs.f1 as { graph: FlowGraph }).graph.nodes.map((n) => n.id)).toEqual(['doc:r', 'gn_a'])
    await canvas.trigger('keydown', { key: 'z', ctrlKey: true })
    expect((center.state.graphs.f1 as { graph: FlowGraph }).graph.nodes.map((n) => n.id).sort()).toEqual(['doc:r', 'gn_a', 'gn_b'])
  })

  it('validation issues are listed and highlight their node', async () => {
    const w = await mountWith(draftGraph())
    await w.get('[data-test="designer-draft-validate"]').trigger('click')
    await settle()
    expect(w.get('[data-test="designer-draft-check"]').attributes('data-confirmable')).toBe('false')
    expect(w.findAll('[data-test="designer-draft-issue"]').map((i) => i.text())).toEqual(['합류 규칙 미정'])
    const issueCard = w.findAll('[data-test="designer-canvas-node"]').find((c) => c.attributes('data-node-id') === 'gn_b')!
    expect(issueCard.attributes('data-issue')).toBe('true')
  })

  it('a v0.1 Flow offers no draft bar and no drop', async () => {
    const w = await mountWith({ ...draftGraph(), ownership: 'v01', draft: null, viewMode: null })
    expect(w.find('[data-test="designer-draft-toolbar"]').exists()).toBe(false)
    expect(w.get('[data-test="designer-canvas"]').attributes('data-editable')).toBe('false')
    const over = new Event('dragover', { cancelable: true })
    Object.defineProperty(over, 'dataTransfer', { value: { dropEffect: 'none' } })
    w.get('[data-test="designer-canvas"]').element.dispatchEvent(over)
    expect(over.defaultPrevented).toBe(false)
  })
})
