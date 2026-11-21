/**
 * a card held in a drag stays under the pointer while the Graph is drawn again.
 *
 * Reported (0004-CH #32): place a card, grab it at once and move it without letting go — the card jumps back to
 * where it was placed. The placed card's draft save goes out 0.4 s after the drop; its answer renames the card
 * (`tmp_…` -> the server key) and the Canvas hands Vue Flow the whole Graph again, which put every card back on
 * its Graph position and dropped the held card under its old name, so the drop was ignored.
 *
 * On the real Center session and the real Vue Flow Canvas (only the Graph source is a fake whose draft save
 * answers when the test says so). jsdom replays no d3 drag: a drag is Vue Flow's own `nodeDragStart` /
 * `nodeDrag` / `nodeDragStop` with the held card where Vue Flow puts it, as moveDensity.0059 does.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow, type GraphNode } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'
import { DEFAULT_PALETTE_ITEMS, paletteItemDefinition, paletteNodeSpec, readPaletteDragData } from '@/features/designer/palette/paletteCatalog'

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

const FLOW = 'f1'
const def = (nodeType: string, docType: string | null, label: string) => ({ nodeType, docType, paletteItemId: null, label, assignee: null })

function draftGraph(): FlowGraph {
  return {
    flowId: FLOW,
    ownership: 'unassigned',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [
      { id: 'gn_a', kind: 'requirement', title: '요구', state: 'draft', x: 0, y: 0, movable: true, structureEditable: true, definition: def('document', 'R', '요구') },
      { id: 'gn_b', kind: 'design', title: '설계', state: 'draft', x: 260, y: 0, movable: true, structureEditable: true, definition: def('document', 'D', '설계') },
    ],
    edges: [],
    draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    viewMode: 'draft',
  }
}

let center: DesignerCenterSession
let wrapper: VueWrapper<any> | null = null
let drafts: FlowDraftContent[]
let layouts: FlowLayoutChange[]
/** Answers the draft save waiting now: every new card gets its server key `gn_t`. */
let answerSave: () => void

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}
const wait = () => new Promise((resolve) => setTimeout(resolve, 5))

async function mountCenter() {
  center = createDesignerCenterSession({
    draftSaveDelayMs: 0,
    layoutSaveDelayMs: 0,
    readDrop: (transfer) => {
      const definition = readPaletteDragData(transfer)
      return definition ? paletteNodeSpec(definition) : null
    },
    source: {
      isSample: false,
      loadGraph: async () => draftGraph(),
      saveLayout: async (_p, _f, change) => {
        layouts.push(change)
        return { ignoredNodeIds: [] }
      },
      saveDraft: (_p, _f, version, content) => new Promise((resolve) => {
        drafts.push(content)
        answerSave = () => resolve({ version: version + 1, keyMap: Object.fromEntries(content.nodes.filter((n) => n.isNew).map((n) => [n.key, 'gn_t'])) })
      }),
    },
  })
  center.setProject('p')
  wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
  center.openFlow({ projectId: 'p', flowId: FLOW, flowName: 'F1' })
  await settle()
}

/** Places a Implementation card from the Palette at (x, y); returns its `tmp_` id. */
async function placeCard(x: number, y: number): Promise<string> {
  const item = DEFAULT_PALETTE_ITEMS.find((i) => i.id === 'task')!
  const transfer = { getData: (type: string) => (type === 'application/x-flowgate-palette-item' ? JSON.stringify(paletteItemDefinition(item, '구현')) : '') }
  expect(center.dropOnCanvas(FLOW, transfer, x, y)).toBe(true)
  await settle()
  const id = shownGraph().nodes.at(-1)!.id
  expect(id.startsWith('tmp_')).toBe(true)
  return id
}

function shownGraph(): FlowGraph {
  const load = center.state.graphs[FLOW]
  if (load?.status !== 'ready') throw new Error('Graph not ready')
  return load.graph
}

const graphNode = (id: string) => shownGraph().nodes.find((node) => node.id === id)
const flow = () => useVueFlow(`designer-canvas:${FLOW}`)
const shownAt = (id: string) => {
  const node = flow().findNode(id)
  return node ? { x: node.position.x, y: node.position.y } : null
}

/**
 * Grabs the card Vue Flow shows as `id` and keeps holding it: each move puts it where the pointer is and reports the
 * drag (Vue Flow's drag follows the card it grabbed, under the id it grabbed it with); `drop` lets go.
 */
function hold(id: string) {
  const node = () => flow().findNode(id) as GraphNode
  const params = (event: MouseEvent) => ({ event, node: node(), nodes: [node()], intersections: undefined }) as never
  flow().emits.nodeDragStart(params(new MouseEvent('mousedown')))
  return {
    async moveTo(x: number, y: number) {
      node().position = { x, y }
      flow().emits.nodeDrag(params(new MouseEvent('mousemove')))
      await settle()
    },
    async drop() {
      flow().emits.nodeDragStop(params(new MouseEvent('mouseup')))
      await settle()
    },
  }
}

/** The last stored position of a node (the layout saves, in order). */
function storedAt(nodeId: string) {
  const positions = layouts.flatMap((change) => change.positions).filter((p) => p.nodeId === nodeId)
  const last = positions.at(-1)
  return last ? { x: last.x, y: last.y } : null
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  drafts = []
  layouts = []
  answerSave = () => {}
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('a card held in a drag while the Graph is drawn again (flowgate.v02gui.0020)', () => {
  it('a card placed and grabbed at once stays under the pointer when its save answers, and is saved where it is dropped', async () => {
    await mountCenter()
    const tmp = await placeCard(300, 200)
    const held = hold(tmp)
    await held.moveTo(420, 260)
    await wait()
    await settle()
    expect(drafts).toHaveLength(1) // the placed card's save went out while it is held

    answerSave()
    await settle()
    // The Graph knows the card by its server key now; the card is still where the pointer holds it.
    expect(graphNode('gn_t')).toBeTruthy()
    expect(graphNode(tmp)).toBeUndefined()
    expect(shownAt(tmp)).toEqual({ x: 420, y: 260 })

    await held.moveTo(520, 300)
    expect(shownAt(tmp)).toEqual({ x: 520, y: 300 })
    await held.drop()

    expect(graphNode('gn_t')).toMatchObject({ x: 520, y: 300 })
    expect(shownAt('gn_t')).toEqual({ x: 520, y: 300 })
    expect(shownAt(tmp)).toBeNull()
    await wait()
    await settle()
    expect(storedAt('gn_t')).toEqual({ x: 520, y: 300 })
    expect(layouts.flatMap((change) => change.positions).some((p) => p.nodeId.startsWith('tmp_'))).toBe(false)
  })

  it('let go without moving after the save answered, the card stays where it was let go', async () => {
    await mountCenter()
    const tmp = await placeCard(300, 200)
    const held = hold(tmp)
    await held.moveTo(440, 120)
    await wait()
    answerSave()
    await settle()
    await held.drop()

    expect(graphNode('gn_t')).toMatchObject({ x: 440, y: 120 })
    expect(shownAt('gn_t')).toEqual({ x: 440, y: 120 })
    await wait()
    await settle()
    expect(storedAt('gn_t')).toEqual({ x: 440, y: 120 })
  })

  it('a card already on the Canvas held while another card\'s save answers stays under the pointer', async () => {
    await mountCenter()
    await placeCard(300, 200)
    const held = hold('gn_b')
    await held.moveTo(500, 420)
    await wait()
    answerSave()
    await settle()
    expect(graphNode('gn_t')).toBeTruthy()
    expect(shownAt('gn_b')).toEqual({ x: 500, y: 420 })

    await held.drop() // no move after the Graph was drawn again
    expect(graphNode('gn_b')).toMatchObject({ x: 500, y: 420 })
    expect(shownAt('gn_b')).toEqual({ x: 500, y: 420 })
    await wait()
    await settle()
    expect(storedAt('gn_b')).toEqual({ x: 500, y: 420 })
  })

  it('a card that is not held is drawn where the Graph has it, as before', async () => {
    await mountCenter()
    const tmp = await placeCard(300, 200)
    await wait()
    answerSave()
    await settle()
    expect(shownAt(tmp)).toBeNull()
    expect(shownAt('gn_t')).toEqual({ x: 300, y: 200 })
    expect(shownAt('gn_b')).toEqual({ x: 260, y: 0 })
  })
})
