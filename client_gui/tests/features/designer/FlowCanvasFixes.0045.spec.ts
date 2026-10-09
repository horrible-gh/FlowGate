/**
 * flowgate.v02.0045 T0019 — Designer Canvas fixes from the mid-way check:
 * 1. no empty-Graph text, 2. a new Flow starts with one 요구사항 node (never added twice),
 * 3. wide connection areas on draft cards, 4. 설계 cards purple and named like the Palette,
 * 5. no ownership badge, 6. progressed / not yet progressed Edges drawn apart (actual node states),
 * 7. Edge selection apart from removal — a progressed Edge is never removed.
 * TR0020 rev4 (rejection): 2. only execution Edges are drawn — no dashed reference fan-out from a
 * 요구사항 card; 3. a connection starts from any side of a card and an Edge attaches to the sides
 * that face each other (edgeGeometry.ts), never a fixed right side.
 * Pure rules over the model, the session over a fake source, and the screen with the real Vue Flow
 * (jsdom draws no Vue Flow edges, so Edge clicks go through Vue Flow's own emits and the Edge
 * look is checked on FlowEdgePath itself).
 */
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { Position, useVueFlow, type GraphEdge, type GraphNode } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { NODE_CARD_HEIGHT, NODE_CARD_WIDTH, edgeAnchors, facingSide, nodeRect } from '@/features/designer/center/canvas/edgeGeometry'
import FlowEdgePath from '@/features/designer/center/canvas/FlowEdgePath.vue'
import { DESIGN_DOC_TYPE_ICONS, DESIGN_DOC_TYPE_LABELS, flowNodeKindDisplay, flowNodeKindLabelKey } from '@/features/designer/center/canvas/flowNodeKinds'
import { toVueFlowEdges, toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { createDesignerCenterSession, type CanvasNodeSpec, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { isEdgeProgressed, progressedEdgeIds } from '@/features/designer/center/graph/edgeProgress'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'
import { DEFAULT_PALETTE_ITEMS } from '@/features/designer/palette/paletteCatalog'
import { paletteIconPath } from '@/features/designer/palette/paletteIcons'
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

const def = (nodeType: string, docType: string | null, label: string) => ({ nodeType, docType, paletteItemId: null, label, assignee: null })

const node = (id: string, state: string | null, extra: Partial<FlowGraphNode> = {}): FlowGraphNode => ({
  id, kind: 'task', title: id, state, x: 0, y: 0, movable: true, ...extra,
})

const REQUIREMENT: CanvasNodeSpec = { nodeType: 'document', docType: 'R', paletteItemId: null, label: '요구사항', kind: 'requirement' }

/**
 * A draft next to two documents: the execution edge doc:r (done) -> doc:t (in progress) is
 * progressed; the reference relation between the same two documents is not (no execution
 * meaning), and neither is the draft edge.
 */
function mixedGraph(): FlowGraph {
  return {
    flowId: 'f1',
    ownership: 'unassigned',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [
      { id: 'doc:r', kind: 'requirement', title: 'R 문서', state: 'done', x: 0, y: 0, movable: true },
      { id: 'doc:t', kind: 'task', title: 'T 문서', state: 'in_progress', x: 260, y: 0, movable: true },
      { id: 'gn_a', kind: 'design', docType: 'L', title: '로직 노드', state: 'draft', x: 0, y: 320, movable: true, structureEditable: true, definition: def('document', 'L', '로직 노드') },
      { id: 'gn_b', kind: 'design', docType: 'DB', title: 'DB 노드', state: 'draft', x: 260, y: 320, movable: true, structureEditable: true, definition: def('document', 'DB', 'DB 노드') },
    ],
    edges: [
      { id: 'seq:doc:r>doc:t', source: 'doc:r', target: 'doc:t', edgeClass: 'execution' },
      { id: 'ref:triggered_by:r>t', source: 'doc:r', target: 'doc:t', edgeClass: 'reference' },
      { id: 'ge_ab', source: 'gn_a', target: 'gn_b', edgeClass: 'execution', editable: true },
    ],
    draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    viewMode: 'draft',
  }
}

describe('Edge progress from the actual node states (item 6)', () => {
  it('is progressed only when the source is done and the target has been reached', () => {
    const exec = (id: string, source: string, target: string) => ({ id, source, target, edgeClass: 'execution' })
    const graph: FlowGraph = {
      flowId: 'f',
      nodes: [node('a', 'done'), node('b', 'in_progress'), node('c', 'draft'), node('d', 'review'), node('e', null), node('f', 'blocked'), node('g', 'done')],
      edges: [
        exec('a>b', 'a', 'b'),
        exec('a>c', 'a', 'c'),
        exec('b>d', 'b', 'd'),
        exec('a>e', 'a', 'e'),
        exec('a>f', 'a', 'f'),
        exec('a>g', 'a', 'g'),
        exec('a>x', 'a', 'missing'),
      ],
    }
    expect([...progressedEdgeIds(graph)].sort()).toEqual(['a>b', 'a>f', 'a>g'])
    expect(isEdgeProgressed(graph, 'a>b')).toBe(true)
    expect(isEdgeProgressed(graph, 'a>c')).toBe(false)
    expect(isEdgeProgressed(graph, 'nope')).toBe(false)
  })

  it('a line out of the 요구사항 is progressed once its target is reached, while the 요구사항 is still running (rev5)', () => {
    // The workflow root R stays wf_in_progress (Canvas in_progress) until the whole workflow ends.
    const req = (id: string, state: string | null) => node(id, state, { kind: 'requirement' })
    const exec = (id: string, source: string, target: string) => ({ id, source, target, edgeClass: 'execution' })
    const graph: FlowGraph = {
      flowId: 'f',
      nodes: [req('r', 'in_progress'), req('r2', 'review'), node('b', 'in_progress'), node('c', 'draft'), node('d', 'done'), node('e', null), node('t', 'in_progress')],
      edges: [
        exec('r>b', 'r', 'b'),
        exec('r>c', 'r', 'c'),
        exec('r2>d', 'r2', 'd'),
        exec('r>e', 'r', 'e'),
        exec('t>d', 't', 'd'),
        { id: 'ref:triggered_by:r>d', source: 'r', target: 'd', edgeClass: 'reference' },
      ],
    }
    expect([...progressedEdgeIds(graph)].sort()).toEqual(['r2>d', 'r>b'])
    // A line not yet reached, a reference relation and a non-요구사항 source that is not done keep
    // the pending line.
    expect(isEdgeProgressed(graph, 'r>c')).toBe(false)
    expect(isEdgeProgressed(graph, 'r>e')).toBe(false)
    expect(isEdgeProgressed(graph, 't>d')).toBe(false)
    expect(isEdgeProgressed(graph, 'ref:triggered_by:r>d')).toBe(false)
    expect(toVueFlowEdges(graph).find((e) => e.id === 'r>b')?.data).toEqual({ edgeClass: 'execution', progressed: true, deletable: false })
  })

  it('never marks a reference relation (or an unclassified edge) progressed, and draws no reference relation at all (rev4)', () => {
    const graph: FlowGraph = {
      flowId: 'f',
      nodes: [node('r', 'done'), node('t', 'done'), node('u', 'in_progress'), node('v', 'review'), node('w', 'blocked')],
      edges: [
        { id: 'ref:triggered_by:r>t', source: 'r', target: 't', edgeClass: 'reference' },
        { id: 'ref:target:r>t', source: 'r', target: 't', edgeClass: 'reference' },
        { id: 'ref:triggered_by:r>u', source: 'r', target: 'u', edgeClass: 'reference' },
        { id: 'ref:target:t>v', source: 't', target: 'v', edgeClass: 'reference' },
        { id: 'ref:target:t>w', source: 't', target: 'w', edgeClass: 'reference' },
        { id: 'plain:r>t', source: 'r', target: 't' },
        { id: 'null:r>t', source: 'r', target: 't', edgeClass: null },
        { id: 'seq:r>t', source: 'r', target: 't', edgeClass: 'execution' },
      ],
    }
    expect([...progressedEdgeIds(graph)]).toEqual(['seq:r>t'])
    expect(isEdgeProgressed(graph, 'ref:triggered_by:r>t')).toBe(false)
    expect(isEdgeProgressed(graph, 'ref:target:r>t')).toBe(false)
    // TR0020 rev4 (rejection item 2): the five document relations are not drawn — only the lines
    // in the direction the work goes; an unclassified edge is still drawn (never progressed).
    expect(toVueFlowEdges(graph).map((e) => [e.id, e.data])).toEqual([
      ['plain:r>t', { edgeClass: null, progressed: false, deletable: false }],
      ['null:r>t', { edgeClass: null, progressed: false, deletable: false }],
      ['seq:r>t', { edgeClass: 'execution', progressed: true, deletable: false }],
    ])
  })

  it('marks Vue Flow edges progressed, selected and — apart from that — deletable; a problem names itself', () => {
    const edges = toVueFlowEdges(mixedGraph(), new Map([['ge_ab', '합류 규칙 미정']]), 'ge_ab')
    expect(edges.map((e) => [e.id, e.data])).toEqual([
      ['seq:doc:r>doc:t', { edgeClass: 'execution', progressed: true, deletable: false }],
      ['ge_ab', { edgeClass: 'execution', progressed: false, deletable: true, editable: true, issue: true, issueText: '합류 규칙 미정', selected: true }],
    ])
    expect(toVueFlowNodes(mixedGraph(), null, new Map([['gn_b', '분기 규칙 미정']])).map((n) => [n.data.issue ?? false, n.data.issueText ?? null])).toEqual([
      [false, null], [false, null], [false, null], [true, '분기 규칙 미정'],
    ])
    // An editable draft edge the work already went through is selectable but never deletable.
    const done = { ...mixedGraph(), nodes: mixedGraph().nodes.map((n) => (n.id.startsWith('gn_') ? { ...n, state: 'done' } : n)) }
    expect(toVueFlowEdges(done, new Map(), 'ge_ab').find((e) => e.id === 'ge_ab')?.data).toEqual({
      edgeClass: 'execution', progressed: true, deletable: false, editable: true, selected: true,
    })
  })
})

describe('설계 cards (item 4)', () => {
  it('every 설계 Palette card is purple, the same colour as a 설계 card on the Canvas', () => {
    const accent = flowNodeKindDisplay('design').accent
    expect(accent).toBe('#9b72ff')
    const design = DEFAULT_PALETTE_ITEMS.filter((item) => item.category === 'design')
    expect(design.map((item) => [item.id, item.accent])).toEqual([
      ['basic_design', accent], ['protocol', accent], ['logic', accent], ['db_design', accent],
    ])
    // Other categories keep their own colours.
    expect(DEFAULT_PALETTE_ITEMS.find((item) => item.id === 'task')?.accent).toBe('#f59e0b')
  })

  it('names a 설계 card after its Palette card, by document type', () => {
    for (const item of DEFAULT_PALETTE_ITEMS.filter((i) => i.category === 'design')) {
      expect(DESIGN_DOC_TYPE_LABELS[item.docType!]).toBe(item.labelKey)
      expect(flowNodeKindLabelKey('design', item.docType)).toBe(`gui.designer.palette.items.${item.labelKey}`)
    }
    expect(flowNodeKindLabelKey('design', 'db')).toBe('gui.designer.palette.items.db_design')
    expect(flowNodeKindLabelKey('design', 'DS')).toBe('gui.designer.center.node_kinds.design')
    expect(flowNodeKindLabelKey('design', null)).toBe('gui.designer.center.node_kinds.design')
    expect(flowNodeKindLabelKey('task', 'T')).toBe('gui.designer.center.node_kinds.task')
    expect(flowNodeKindLabelKey('custom.7', null)).toBe('gui.designer.center.node_kinds.unknown')
    expect(toVueFlowNodes(mixedGraph(), null).map((n) => n.data.docType ?? null)).toEqual([null, null, 'L', 'DB'])
  })

  // TR0020 rev2 (rejection item 2): the four 설계 cards are told apart by their glyph.
  it('draws each 설계 document type with its own glyph — the one its Palette card shows', () => {
    const design = DEFAULT_PALETTE_ITEMS.filter((i) => i.category === 'design')
    const icons = design.map((item) => flowNodeKindDisplay('design', item.docType).icon)
    expect(new Set(icons).size).toBe(4)
    for (const item of design) {
      expect(flowNodeKindDisplay('design', item.docType).icon, item.id).toBe(paletteIconPath(item.icon))
      expect(DESIGN_DOC_TYPE_ICONS[item.docType!]).toBe(paletteIconPath(item.icon))
      expect(flowNodeKindDisplay('design', item.docType).accent).toBe('#9b72ff')
    }
    expect(flowNodeKindDisplay('design', 'db').icon).toBe(DESIGN_DOC_TYPE_ICONS.DB)
    // DS / none keep the generic 설계 glyph; other kinds ignore the document type.
    const generic = flowNodeKindDisplay('design').icon
    expect(flowNodeKindDisplay('design', 'DS').icon).toBe(generic)
    expect(flowNodeKindDisplay('task', 'L').icon).toBe(flowNodeKindDisplay('task').icon)
    expect(flowNodeKindDisplay('design', 'constructor').icon).toBe(generic)
  })
})

describe('FlowEdgePath look (items 6 and 7)', () => {
  const props = { id: 'e1', sourceX: 0, sourceY: 0, targetX: 200, targetY: 40 }
  const mountEdge = (extra: Record<string, unknown>) =>
    mount({ components: { FlowEdgePath }, template: '<svg><FlowEdgePath v-bind="p" @remove="removed++" /></svg>', data: () => ({ p: { ...props, ...extra }, removed: 0 }) }, { global: { plugins: [i18n] } })

  beforeEach(() => {
    i18n.global.locale.value = 'ko'
  })

  it('draws progressed and pending edges apart, and every edge has a hit line', () => {
    const progressed = mountEdge({ progressed: true }).get('[data-test="designer-canvas-edge"]')
    expect(progressed.attributes('data-progress')).toBe('progressed')
    expect(progressed.classes()).toContain('flow-edge-progressed')
    const pending = mountEdge({}).get('[data-test="designer-canvas-edge"]')
    expect(pending.attributes('data-progress')).toBe('pending')
    expect(pending.classes()).toContain('flow-edge-pending')
    expect(pending.find('[data-test="designer-canvas-edge-hit"]').exists()).toBe(true)
  })

  it('a selected deletable edge shows the remove button; a selected progressed edge shows it cannot be removed', async () => {
    const plain = mountEdge({ deletable: true })
    expect(plain.find('[data-test="designer-canvas-edge-remove"]').exists()).toBe(false)

    const w = mountEdge({ selected: true, deletable: true, editable: true })
    const edge = w.get('[data-test="designer-canvas-edge"]')
    expect([edge.attributes('data-selected'), edge.attributes('data-deletable')]).toEqual(['true', 'true'])
    const button = w.get('[data-test="designer-canvas-edge-remove"]')
    expect(button.attributes('aria-label')).toBe('연결선 삭제')
    await button.trigger('click')
    expect((w.vm as unknown as { removed: number }).removed).toBe(1)

    const locked = mountEdge({ selected: true, progressed: true, editable: true, deletable: false })
    expect(locked.get('[data-test="designer-canvas-edge"]').attributes('data-deletable')).toBe('false')
    expect(locked.find('[data-test="designer-canvas-edge-remove"]').exists()).toBe(false)
    expect(locked.get('[data-test="designer-canvas-edge-locked"] title').text()).toBe('이미 진행된 연결선은 삭제할 수 없습니다')
  })

  it('is one kind of line: no dashed reference look any more (rev4)', () => {
    const src = readFileSync(join(resolve(__dirname, '../../../src/features/designer/center/canvas'), 'FlowEdgePath.vue'), 'utf8')
    expect(src).not.toMatch(/stroke-dasharray|flow-edge-reference/)
    const edge = mountEdge({ edgeClass: 'reference' }).get('[data-test="designer-canvas-edge"]')
    expect(edge.classes()).not.toContain('flow-edge-reference')
  })

  it('names a reported problem on hover', () => {
    const w = mountEdge({ issue: true, issueText: '합류 규칙 미정' })
    expect(w.get('[data-test="designer-canvas-edge"]').classes()).toContain('flow-edge-issue')
    expect(w.get('[data-test="designer-canvas-edge"] > title').text()).toBe('합류 규칙 미정')
    expect(mountEdge({ issueText: 'x' }).find('[data-test="designer-canvas-edge"] > title').exists()).toBe(false)
  })
})

// TR0020 rev4 (rejection item 3): an Edge leaves a card on the side that faces the other card.
describe('Edge sides from the card positions (04-designer-flow-rules edgeGeometry)', () => {
  const rect = (x: number, y: number) => ({ x, y, width: NODE_CARD_WIDTH, height: NODE_CARD_HEIGHT })

  it('runs sideways when the cards lie apart more in x than in y, else up / down', () => {
    expect(facingSide(rect(0, 0), rect(300, 40))).toBe(Position.Right)
    expect(facingSide(rect(300, 40), rect(0, 0))).toBe(Position.Left)
    expect(facingSide(rect(0, 0), rect(40, 300))).toBe(Position.Bottom)
    expect(facingSide(rect(40, 300), rect(0, 0))).toBe(Position.Top)
    // A tie goes sideways; the same spot too.
    expect(facingSide(rect(0, 0), rect(100, 100))).toBe(Position.Right)
    expect(facingSide(rect(0, 0), rect(0, 0))).toBe(Position.Right)
  })

  it('anchors at the middle of the facing side and enters the opposite side of the target', () => {
    expect(edgeAnchors(rect(0, 0), rect(300, 40))).toEqual({
      sourceX: 176, sourceY: 46, sourcePosition: Position.Right, targetX: 300, targetY: 86, targetPosition: Position.Left,
    })
    expect(edgeAnchors(rect(0, 0), rect(40, 300))).toEqual({
      sourceX: 88, sourceY: 92, sourcePosition: Position.Bottom, targetX: 128, targetY: 300, targetPosition: Position.Top,
    })
    expect(edgeAnchors(rect(40, 300), rect(0, 0))).toEqual({
      sourceX: 128, sourceY: 300, sourcePosition: Position.Top, targetX: 88, targetY: 92, targetPosition: Position.Bottom,
    })
    expect(edgeAnchors(rect(300, 40), rect(0, 0))).toMatchObject({ sourcePosition: Position.Left, sourceX: 300, targetPosition: Position.Right, targetX: 176 })
  })

  it('takes a Vue Flow node as its absolute rectangle, the card size until it is measured', () => {
    expect(nodeRect({ computedPosition: { x: 10, y: 20 }, dimensions: { width: 0, height: 0 } })).toEqual({ x: 10, y: 20, width: 176, height: 92 })
    expect(nodeRect({ computedPosition: { x: 10, y: 20 }, dimensions: { width: 200, height: 100 } })).toEqual({ x: 10, y: 20, width: 200, height: 100 })
  })

  it('FlowEdgePath draws between the facing sides of its two nodes and follows a moved node', async () => {
    const node = (id: string, x: number, y: number) =>
      ({ id, computedPosition: { x, y, z: 0 }, dimensions: { width: 0, height: 0 } }) as unknown as GraphNode
    const w = mount(
      { components: { FlowEdgePath }, template: '<svg><FlowEdgePath id="e" :source-x="0" :source-y="0" :target-x="1" :target-y="1" :source-node="a" :target-node="b" /></svg>', data: () => ({ a: node('a', 0, 0), b: node('b', 40, 300) }) },
      { global: { plugins: [i18n] } },
    )
    const edge = () => w.get('[data-test="designer-canvas-edge"]')
    expect([edge().attributes('data-source-side'), edge().attributes('data-target-side')]).toEqual(['bottom', 'top'])
    expect(edge().get('[data-test="designer-canvas-edge-path"]').attributes('d')).toMatch(/^M88,92 C/)
    // The arrow tip sits on the target's top middle and points down into it.
    expect(edge().get('[data-test="designer-canvas-edge-arrow"]').attributes('d')).toMatch(/^M128,300 L123,291 L133,291 Z$/)
    ;(w.vm as unknown as { b: GraphNode }).b.computedPosition = { x: 400, y: 20, z: 0 }
    await flushPromises()
    expect([edge().attributes('data-source-side'), edge().attributes('data-target-side')]).toEqual(['right', 'left'])
    expect(edge().get('[data-test="designer-canvas-edge-path"]').attributes('d')).toMatch(/^M176,46 C/)
    expect(edge().get('[data-test="designer-canvas-edge-arrow"]').attributes('d')).toMatch(/^M400,66 L391,/)
  })
})

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
    source: {
      isSample: false,
      loadGraph: async () => graph,
      saveLayout: async () => ({ ignoredNodeIds: [] }),
      saveDraft: async (_p, _f, version, content) => {
        saved.push(content)
        return { version: version + 1, keyMap: {} }
      },
      validateDraft: async () => ({ confirmable: true, issues: [] }),
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

function clickEdge(id: string) {
  const flow = useVueFlow('designer-canvas:f1')
  flow.emits.edgeClick({ event: new MouseEvent('click'), edge: flow.findEdge(id) as GraphEdge })
}

function press(w: VueWrapper<any>, key: string) {
  w.get('[data-test="designer-canvas"]').element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
}

describe('FlowCanvas (flowgate.v02.0045 T0019)', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    saved = []
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('names 설계 cards like the Palette and shows no ownership badge or empty-Graph text (items 1, 4, 5)', async () => {
    const w = await mountWith(mixedGraph())
    const kinds = w.findAll('[data-test="designer-canvas-node-kind"]').map((k) => k.text())
    expect(kinds).toEqual(['요구사항', '작업', '로직', 'DB설계'])
    expect(w.find('[data-test="designer-canvas-ownership"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-canvas-empty-graph"]').exists()).toBe(false)
    expect(w.get('[data-test="designer-canvas"]').text()).not.toMatch(/담당|표시할 노드가 없습니다/)
  })

  it('gives draft cards a start strip on every side and a whole-card drop area, with no handle dots (item 3, TR0020 rev2 / rev4)', async () => {
    const w = await mountWith(mixedGraph())
    const draftCard = w.get('[data-test="designer-canvas-node"][data-node-id="gn_a"]')
    const handles = draftCard.findAll('.flow-node-handle')
    expect(handles.map((h) => h.classes().includes('is-connectable'))).toEqual([true, true, true, true, true])
    expect(handles.map((h) => h.classes().find((c) => /^vue-flow__handle-(left|right|top|bottom)$/.test(c)))).toEqual([
      'vue-flow__handle-left', 'vue-flow__handle-top', 'vue-flow__handle-right', 'vue-flow__handle-bottom', 'vue-flow__handle-left',
    ])
    // Target = the whole card (drop area); source = a strip along each of the four sides.
    expect(handles.map((h) => [h.classes().includes('flow-node-drop'), h.classes().includes('flow-node-link')])).toEqual([
      [true, false], [false, true], [false, true], [false, true], [false, true],
    ])
    expect(handles.slice(1).map((h) => h.attributes('data-handleid'))).toEqual(['top', 'right', 'bottom', 'left'])
    expect(handles.slice(1).map((h) => h.classes().find((c) => /^flow-node-link-/.test(c)))).toEqual([
      'flow-node-link-top', 'flow-node-link-right', 'flow-node-link-bottom', 'flow-node-link-left',
    ])
    expect(draftCard.findAll('.flow-node-handle *')).toHaveLength(0)
    const docCard = w.get('[data-test="designer-canvas-node"][data-node-id="doc:r"]')
    expect(docCard.findAll('.flow-node-handle.is-connectable')).toHaveLength(0)
    expect(docCard.findAll('.flow-node-drop, .flow-node-link')).toHaveLength(0)
    expect(useVueFlow('designer-canvas:f1').connectionRadius.value).toBe(36)
  })

  it('a connection started from any side strip becomes the same draft edge (rev4)', async () => {
    await mountWith(mixedGraph())
    const flow = useVueFlow('designer-canvas:f1')
    flow.emits.connect({ source: 'gn_a', target: 'gn_b', sourceHandle: 'bottom', targetHandle: null })
    await settle()
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.edges.filter((e) => e.editable).map((e) => [e.source, e.target])).toEqual([['gn_a', 'gn_b']])
    flow.emits.connect({ source: 'gn_b', target: 'gn_a', sourceHandle: 'top', targetHandle: null })
    await settle()
    const again = center.state.graphs.f1
    expect(again?.status === 'ready' && again.graph.edges.filter((e) => e.editable).map((e) => [e.source, e.target])).toEqual([['gn_a', 'gn_b'], ['gn_b', 'gn_a']])
  })

  it('while a connection is dragged the Canvas turns every draft card into a drop area', async () => {
    const w = await mountWith(mixedGraph())
    const canvas = w.get('[data-test="designer-canvas"]')
    expect(canvas.classes()).not.toContain('is-connecting')
    const flow = useVueFlow('designer-canvas:f1')
    flow.emits.connectStart({ event: new MouseEvent('mousedown'), nodeId: 'gn_a', handleId: null, handleType: 'source' })
    await settle()
    expect(canvas.classes()).toContain('is-connecting')
    flow.emits.connectEnd(new MouseEvent('mouseup'))
    await settle()
    expect(canvas.classes()).not.toContain('is-connecting')
  })

  it('draws the connection areas without dots: no handle pseudo element, a whole-card drop area only while connecting', () => {
    const read = (rel: string) => readFileSync(join(resolve(__dirname, '../../../src/features/designer/center/canvas'), rel), 'utf8')
    const card = read('FlowNodeCard.vue')
    expect(card).not.toMatch(/flow-node-handle[^{]*::?(after|before)/)
    expect(card).not.toMatch(/border-radius:\s*50%/)
    expect(card).toMatch(/\.flow-node-drop\)\s*\{[^}]*width:\s*100%[^}]*pointer-events:\s*none/)
    // rev4: a strip along each side — the top / bottom ones span the width, the left / right ones the height.
    expect(card).toMatch(/\.flow-node-link-top\),\s*\n\s*\.flow-node-card :deep\(\.flow-node-link-bottom\)\s*\{[^}]*width:\s*100%/)
    expect(card).toMatch(/\.flow-node-link-left\),\s*\n\s*\.flow-node-card :deep\(\.flow-node-link-right\)\s*\{[^}]*height:\s*100%/)
    const canvas = read('FlowCanvas.vue')
    expect(canvas).toMatch(/\.flow-canvas\.is-connecting :deep\(\.flow-node-drop\)\s*\{[^}]*pointer-events:\s*all/)
  })

  it('selects an edge apart from nodes and removes a deletable one with Delete (item 7)', async () => {
    const w = await mountWith(mixedGraph())
    const canvas = () => w.get('[data-test="designer-canvas"]')
    center.selectNode('f1', 'gn_a')
    await settle()
    clickEdge('ge_ab')
    await settle()
    expect(canvas().attributes('data-selected-edge-id')).toBe('ge_ab')
    expect(canvas().attributes('data-selected-node-id')).toBe('')
    expect(useVueFlow('designer-canvas:f1').findEdge('ge_ab')?.data).toMatchObject({ selected: true, deletable: true })
    // Redrawing the Edges (here: the selection) keeps every drawn Edge of the Graph in Vue Flow
    // (the reference relation is never drawn — rev4).
    expect(useVueFlow('designer-canvas:f1').edges.value.map((e) => e.id)).toEqual(['seq:doc:r>doc:t', 'ge_ab'])
    press(w, 'Delete')
    await settle()
    await wait()
    await settle()
    expect(canvas().attributes('data-selected-edge-id')).toBe('')
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.edges.map((e) => e.id)).toEqual(['seq:doc:r>doc:t', 'ref:triggered_by:r>t'])
    expect(saved.at(-1)?.edges).toEqual([])
  })

  it('a progressed edge can be selected but never removed (item 7)', async () => {
    const w = await mountWith(mixedGraph())
    const canvas = () => w.get('[data-test="designer-canvas"]')
    clickEdge('seq:doc:r>doc:t')
    await settle()
    expect(canvas().attributes('data-selected-edge-id')).toBe('seq:doc:r>doc:t')
    expect(useVueFlow('designer-canvas:f1').findEdge('seq:doc:r>doc:t')?.data).toMatchObject({ selected: true, progressed: true, deletable: false })
    // The reference relation between the same two documents is not drawn at all (rev4).
    expect(useVueFlow('designer-canvas:f1').findEdge('ref:triggered_by:r>t')).toBeUndefined()
    expect(wrapper!.findAll('[data-test="designer-canvas-edge"]').map((e) => e.attributes('data-edge-class'))).not.toContain('reference')
    press(w, 'Delete')
    press(w, 'Backspace')
    await settle()
    await wait()
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.edges.map((e) => e.id)).toEqual(['seq:doc:r>doc:t', 'ref:triggered_by:r>t', 'ge_ab'])
    expect(saved).toEqual([])
    // Selection still ends on a pane click / a node click / Escape.
    expect(canvas().attributes('data-selected-edge-id')).toBe('seq:doc:r>doc:t')
    press(w, 'Escape')
    await settle()
    expect(canvas().attributes('data-selected-edge-id')).toBe('')
    clickEdge('ge_ab')
    await settle()
    center.selectNode('f1', 'gn_b')
    await settle()
    expect(canvas().attributes('data-selected-edge-id')).toBe('')
  })

  it('keeps every Edge drawn when the Edges are handed to Vue Flow again (a new connection)', async () => {
    await mountWith(mixedGraph())
    const flow = useVueFlow('designer-canvas:f1')
    expect(flow.edges.value.map((e) => e.id)).toEqual(['seq:doc:r>doc:t', 'ge_ab'])
    flow.emits.connect({ source: 'gn_b', target: 'gn_a', sourceHandle: null, targetHandle: null })
    await settle()
    expect(flow.edges.value).toHaveLength(3)
    expect(flow.edges.value.slice(0, 2).map((e) => e.id)).toEqual(['seq:doc:r>doc:t', 'ge_ab'])
  })

  it('a progressed draft edge stays even when the session is asked to remove it (item 7)', async () => {
    const graph = mixedGraph()
    const done: FlowGraph = { ...graph, nodes: graph.nodes.map((n) => (n.id.startsWith('gn_') ? { ...n, state: 'done' } : n)) }
    await mountWith(done)
    expect(center.removeEdge('f1', 'ge_ab')).toBe(false)
    const flow = useVueFlow('designer-canvas:f1')
    flow.emits.edgeDoubleClick({ event: new MouseEvent('dblclick'), edge: flow.findEdge('ge_ab') as GraphEdge })
    await settle()
    await wait()
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.edges.map((e) => e.id)).toEqual(['seq:doc:r>doc:t', 'ref:triggered_by:r>t', 'ge_ab'])
    expect(saved).toEqual([])
  })
})

describe('A new Flow starts with one 요구사항 node (item 2)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const empty = (extra: Partial<FlowGraph> = {}): FlowGraph => ({
    flowId: 'f1',
    ownership: 'unassigned',
    nodes: [],
    edges: [],
    draft: { version: 0, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    viewMode: null,
    ...extra,
  })

  function setup(graph: FlowGraph) {
    const drafts: Array<{ version: number; content: FlowDraftContent }> = []
    const layouts: unknown[] = []
    const source: FlowGraphSource = {
      isSample: false,
      loadGraph: vi.fn(async () => graph),
      saveLayout: vi.fn(async (_p, _f, change) => {
        layouts.push(change)
        return { ignoredNodeIds: [] }
      }),
      saveDraft: vi.fn(async (_p, _f, version, content) => {
        drafts.push({ version, content })
        const keyMap: Record<string, string> = {}
        content.nodes.filter((n) => n.isNew).forEach((n, i) => (keyMap[n.key] = `gn_seed${i}`))
        return { version: version + 1, keyMap }
      }),
      validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
      confirmDraft: vi.fn(async () => 1),
    }
    const session = createDesignerCenterSession({ source, layoutSaveDelayMs: 10, draftSaveDelayMs: 10 })
    session.setProject('p')
    return { session, source, drafts, layouts }
  }

  const shownNodes = (session: DesignerCenterSession) => {
    const load = session.state.graphs.f1
    return load?.status === 'ready' ? load.graph.nodes : []
  }

  it('adds and saves the default node to a just created, empty Flow — not offered to undo', async () => {
    const { session, drafts, layouts } = setup(empty())
    session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' }, { seedNode: REQUIREMENT })
    await vi.runAllTimersAsync()
    expect(drafts).toHaveLength(1)
    expect(drafts[0].version).toBe(0)
    expect(drafts[0].content.nodes.map((n) => [n.nodeType, n.docType, n.label, n.isNew])).toEqual([['document', 'R', '요구사항', true]])
    expect(drafts[0].content.edges).toEqual([])
    expect(shownNodes(session).map((n) => [n.id, n.kind, n.title, n.structureEditable])).toEqual([['gn_seed0', 'requirement', '요구사항', true]])
    expect(layouts).toEqual([{ positions: [{ nodeId: 'gn_seed0', x: 0, y: 0 }], viewport: null }])
    expect(session.activeEdit.value?.canUndo).toBe(false)
  })

  it('never adds a second node: not to a Flow with nodes, a saved draft, a read-only Flow or a Flow just opened', async () => {
    const cases: FlowGraph[] = [
      empty({ nodes: [{ id: 'doc:r', kind: 'requirement', title: 'R', state: 'done', x: 0, y: 0, movable: true }] }),
      empty({ draft: { version: 3, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }),
      empty({ ownership: 'v01', draft: null }),
    ]
    for (const graph of cases) {
      const { session, drafts } = setup(graph)
      session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' }, { seedNode: REQUIREMENT })
      await vi.runAllTimersAsync()
      expect(drafts).toEqual([])
      expect(shownNodes(session)).toEqual(graph.nodes)
    }
    const plain = setup(empty())
    plain.session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    await vi.runAllTimersAsync()
    expect(plain.drafts).toEqual([])
    expect(shownNodes(plain.session)).toEqual([])
  })

  it('adds it once: reading the Flow again or reopening an open tab does not add another', async () => {
    const { session, drafts } = setup(empty())
    session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' }, { seedNode: REQUIREMENT })
    await vi.runAllTimersAsync()
    session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' }, { seedNode: REQUIREMENT })
    session.retryGraph('f1')
    await vi.runAllTimersAsync()
    expect(drafts).toHaveLength(1)
  })
})

// TR0020 rev2 (rejection item 3): 검증 is a Toolbar tool right before the run buttons.
describe('Toolbar 검증 (TR0020 rev2)', () => {
  const mountToolbar = (props: Record<string, unknown>) =>
    mount(DesignerToolbar, { props: { runMode: null, density: 'normal', zoom: 100, ...props }, global: { plugins: [i18n] } })

  beforeEach(() => {
    i18n.global.locale.value = 'ko'
  })

  it('sits right before 다음 단계 / 전체 실행 and runs run.validate only when there is something to check', async () => {
    const w = mountToolbar({ canValidate: true })
    const group = w.get('[data-command="run.validate"]').element.parentElement!
    expect([...group.querySelectorAll('button')].map((b) => b.textContent?.trim())).toEqual(['검증', '다음 단계', '전체 실행'])
    await w.get('[data-command="run.validate"]').trigger('click')
    expect(w.emitted('command')).toEqual([['run.validate']])
    for (const props of [{ canValidate: false }, { canValidate: true, disabled: true }, { canValidate: true, runMode: 'all' }]) {
      expect(mountToolbar(props).get('[data-command="run.validate"]').attributes('disabled'), JSON.stringify(props)).toBeDefined()
    }
  })
})
