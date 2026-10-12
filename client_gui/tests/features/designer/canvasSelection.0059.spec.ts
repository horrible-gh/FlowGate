/**
 * flowgate.v02.0059 T#1 — [선택] mode, multi-selection and group move (0011-CH contract A / B).
 * The Selection is a set of Nodes with the single `selectedNodeId` derived from it; in [선택] mode the
 * Canvas runs Vue Flow's own area selection and click toggle and hands every change to the Center
 * session; 전체 선택 takes every card; a dropped group is clamped to the Canvas origin as a whole and
 * saved through the usual moveNode → layout save path. Real Vue Flow under jsdom (no d3 gestures):
 * its store APIs and pane pointer events stand in for the mouse.
 * Rework (TR0014 r2): a selected Edge and a Node Selection of any size exclude each other, and the
 * Selection keeps only the cards a structure edit, its undo / redo or a `tmp_` key rename leaves.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { VueFlow, useVueFlow, type GraphEdge, type GraphNode } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { clampGroupToCanvas, createCanvasViewState, setViewSelection } from '@/features/designer/center/canvasView'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'

registerAppMessages(i18n)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))
const capture = ['setPointerCapture', 'releasePointerCapture'] as const
const originalCapture = capture.map((p) => Object.getOwnPropertyDescriptor(Element.prototype, p))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
  // jsdom has no pointer capture; Vue Flow's area selection calls it.
  for (const p of capture) Object.defineProperty(Element.prototype, p, { configurable: true, value: () => {} })
})

afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
  capture.forEach((p, i) => {
    if (originalCapture[i]) Object.defineProperty(Element.prototype, p, originalCapture[i]!)
    else delete (Element.prototype as unknown as Record<string, unknown>)[p]
  })
})

/** a, b: movable; c: not movable (stays where it is even when selected). */
function graphOf(): FlowGraph {
  return {
    flowId: 'f1',
    ownership: 'graph',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [
      { id: 'a', kind: 'requirement', title: 'A', state: 'done', x: 0, y: 0, positionSource: 'auto', movable: true },
      { id: 'b', kind: 'design', title: 'B', state: 'draft', x: 260, y: 0, positionSource: 'auto', movable: true },
      { id: 'c', kind: 'task', title: 'C', state: null, x: 100, y: 200, positionSource: 'auto', movable: false },
    ],
    edges: [{ id: 'seq:a>b', source: 'a', target: 'b', edgeClass: 'execution' }],
  }
}

let saves: FlowLayoutChange[]
let center: DesignerCenterSession
let wrapper: VueWrapper<any> | null = null
let graph: FlowGraph

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}
const wait = () => new Promise((resolve) => setTimeout(resolve, 5))

function session() {
  return createDesignerCenterSession({
    layoutSaveDelayMs: 0,
    source: {
      isSample: false,
      loadGraph: async () => graph,
      saveLayout: async (_p, _f, change) => {
        saves.push(change)
        return { ignoredNodeIds: [] }
      },
    },
  })
}

async function mountCanvas() {
  center = session()
  center.setProject('p')
  wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
  center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
  await settle()
  return wrapper
}

const flow = () => useVueFlow('designer-canvas:f1')
const selection = () => [...(center.activeView.value?.selectedNodeIds ?? [])]
const selectedCards = (w: VueWrapper<any>) =>
  w.findAll('.vue-flow__node').filter((n) => n.find('.flow-node-card.is-selected').exists()).map((n) => n.attributes('data-id'))
const shownNode = (id: string) => {
  const load = center.state.graphs.f1
  return load?.status === 'ready' ? load.graph.nodes.find((n) => n.id === id) : undefined
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  saves = []
  graph = graphOf()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('Selection state (contract A)', () => {
  it('keeps selectedNodeId as the Node of a one-Node Selection only', () => {
    const view = createCanvasViewState()
    expect(view).toEqual({ viewport: null, selectedNodeIds: [], selectedNodeId: null })
    expect(setViewSelection(view, ['a'])).toBe(true)
    expect(view).toMatchObject({ selectedNodeIds: ['a'], selectedNodeId: 'a' })
    expect(setViewSelection(view, ['a', 'b', 'a'])).toBe(true)
    expect(view).toMatchObject({ selectedNodeIds: ['a', 'b'], selectedNodeId: null })
    expect(setViewSelection(view, ['a', 'b'])).toBe(false)
    setViewSelection(view, [])
    expect(view).toMatchObject({ selectedNodeIds: [], selectedNodeId: null })
  })
})

describe('group clamp (contract B)', () => {
  it('moves a dropped group back over the origin as a whole, keeping its relative layout', () => {
    const drops = clampGroupToCanvas([
      { nodeId: 'a', x: -40, y: 30 },
      { nodeId: 'b', x: 220, y: -10 },
    ])
    expect(drops).toEqual([
      { nodeId: 'a', x: 0, y: 40 },
      { nodeId: 'b', x: 260, y: 0 },
    ])
    expect(drops[1].x - drops[0].x).toBe(260)
    expect(drops[0].y - drops[1].y).toBe(40)
  })

  it('clamps one Node as before and leaves a group inside the Canvas untouched', () => {
    expect(clampGroupToCanvas([{ nodeId: 'a', x: -15, y: -7 }])).toEqual([{ nodeId: 'a', x: 0, y: 0 }])
    expect(clampGroupToCanvas([{ nodeId: 'a', x: 15, y: -7 }])).toEqual([{ nodeId: 'a', x: 15, y: 0 }])
    const inside = [
      { nodeId: 'a', x: 5, y: 6 },
      { nodeId: 'b', x: 300, y: 7 },
    ]
    expect(clampGroupToCanvas(inside)).toEqual(inside)
    expect(clampGroupToCanvas([])).toEqual([])
  })
})

describe('Vue Flow mapping of the Selection', () => {
  it('out of [선택] mode maps as before and never leaves a Node selected for the library', () => {
    const nodes = toVueFlowNodes(graphOf(), ['a', 'b'])
    expect(nodes.map((n) => n.data!.selected)).toEqual([true, true, false])
    for (const node of nodes) expect(node).toMatchObject({ selectable: false, selected: false })
    expect(nodes.map((n) => n.draggable)).toEqual([true, true, false])
  })

  it('in [선택] mode Nodes are selectable and only selected movable Nodes can be dragged', () => {
    const nodes = toVueFlowNodes(graphOf(), ['a', 'c'], new Map(), { selectMode: true })
    expect(nodes.map((n) => [n.id, n.selectable, n.selected, n.draggable])).toEqual([
      ['a', true, true, true],
      ['b', true, false, false],
      ['c', true, true, false],
    ])
  })
})

describe('Center session — Selection actions', () => {
  it('selectNodes keeps only Nodes of the Graph; selectNode is a one-Node Selection', async () => {
    await mountCanvas()
    center.selectNodes('f1', ['b', 'gone', 'a'])
    expect(selection()).toEqual(['b', 'a'])
    expect(center.activeView.value!.selectedNodeId).toBeNull()
    expect(center.activeSelectedNode.value).toBeNull()
    center.selectNode('f1', 'a')
    expect(selection()).toEqual(['a'])
    expect(center.activeSelectedNode.value?.id).toBe('a')
    center.selectNodes('zzz', ['a'])
    expect(Object.keys(center.state.views)).toEqual(['f1'])
  })

  it('전체 선택 takes every card in [선택] mode; turning the mode off only drops the multi-Selection', async () => {
    await mountCanvas()
    expect(center.selectMode.value).toBe(false)
    expect(center.selectAll('f1')).toBe(true)
    expect(center.selectMode.value).toBe(true)
    expect(selection()).toEqual(['a', 'b', 'c'])
    expect(center.activeSelection.value).toBeNull()
    const before = JSON.stringify(center.state.graphs.f1)

    center.setSelectMode(false)
    expect(center.selectMode.value).toBe(false)
    expect(selection()).toEqual([])
    expect(JSON.stringify(center.state.graphs.f1)).toBe(before)
    await wait()
    expect(saves).toEqual([])

    // A one-Node Selection survives the switch.
    center.setSelectMode(true)
    center.selectNodes('f1', ['b'])
    center.setSelectMode(false)
    expect(selection()).toEqual(['b'])
    expect(center.selectAll('zzz')).toBe(false)
  })

  it('a re-read Graph keeps only the selected Nodes it still has', async () => {
    await mountCanvas()
    center.selectNodes('f1', ['a', 'b', 'c'])
    graph = { ...graphOf(), nodes: graphOf().nodes.filter((n) => n.id !== 'b') }
    center.retryGraph('f1')
    await settle()
    expect(selection()).toEqual(['a', 'c'])
  })
})

describe('FlowCanvas — [선택] mode with the real Vue Flow', () => {
  it('OFF keeps the earlier Canvas settings: drag pans, a card click selects one card', async () => {
    const w = await mountCanvas()
    const vf = w.findComponent(VueFlow)
    expect(vf.props()).toMatchObject({
      elementsSelectable: false,
      selectionKeyCode: null,
      multiSelectionKeyCode: null,
      panOnDrag: true,
      panOnScroll: false,
      zoomOnScroll: true,
    })
    expect(w.get('[data-test="designer-canvas"]').attributes('data-select-mode')).toBe('false')
    await w.get('.vue-flow__node[data-id="a"]').trigger('click')
    await w.get('.vue-flow__node[data-id="b"]').trigger('click')
    await settle()
    expect(selection()).toEqual(['b'])
    expect(selectedCards(w)).toEqual(['b'])
  })

  it('ON: a drag selects, the wheel pans and Vue Flow multi-selection is on', async () => {
    const w = await mountCanvas()
    center.setSelectMode(true)
    await settle()
    expect(w.findComponent(VueFlow).props()).toMatchObject({
      elementsSelectable: true,
      selectionKeyCode: true,
      multiSelectionKeyCode: true,
      selectNodesOnDrag: false,
      panOnDrag: false,
      panOnScroll: true,
    })
    expect(w.get('[data-test="designer-canvas"]').attributes('data-select-mode')).toBe('true')
    expect(flow().multiSelectionActive.value).toBe(true)
    expect(w.find('.vue-flow__pane.selection').exists()).toBe(true)
  })

  it('ON: a card click adds it to the Selection and a second click removes it', async () => {
    const w = await mountCanvas()
    center.setSelectMode(true)
    await settle()
    await w.get('.vue-flow__node[data-id="a"]').trigger('click')
    await settle()
    await w.get('.vue-flow__node[data-id="c"]').trigger('click')
    await settle()
    expect(selection()).toEqual(['a', 'c'])
    expect(selectedCards(w)).toEqual(['a', 'c'])
    expect(flow().findNode('a')!.selected).toBe(true)
    expect(center.activeSelectedNode.value).toBeNull()

    await w.get('.vue-flow__node[data-id="a"]').trigger('click')
    await settle()
    expect(selection()).toEqual(['c'])
    expect(center.activeSelectedNode.value?.id).toBe('c')
  })

  it('ON: dragging a rectangle on the empty Canvas selects the cards inside it', async () => {
    const w = await mountCanvas()
    center.setSelectMode(true)
    center.selectNodes('f1', ['c'])
    await settle()
    await flow().setViewport({ x: 0, y: 0, zoom: 1 })
    for (const node of flow().getNodes.value) node.dimensions = { width: 100, height: 50 }
    const pane = w.get('.vue-flow__pane').element
    const fire = (type: string, x: number, y: number) =>
      pane.dispatchEvent(Object.assign(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 }), { pointerId: 1 }))
    fire('pointerdown', -5, -5)
    fire('pointermove', 200, 30)
    fire('pointermove', 370, 60)
    fire('pointerup', 370, 60)
    await settle()
    // a (0,0) and b (260,0) are inside; c (100,200) is not, and the earlier Selection is replaced.
    expect(selection()).toEqual(['a', 'b'])
    expect(selectedCards(w)).toEqual(['a', 'b'])
  })

  it('ON: several reported changes in one tick build on each other', async () => {
    await mountCanvas()
    center.setSelectMode(true)
    await settle()
    flow().emits.nodesChange([{ type: 'select', id: 'a', selected: true }])
    flow().emits.nodesChange([{ type: 'select', id: 'b', selected: true }])
    await settle()
    expect(selection()).toEqual(['a', 'b'])
    flow().emits.nodesChange([{ type: 'position', id: 'a', dragging: false } as never])
    await settle()
    expect(selection()).toEqual(['a', 'b'])
  })

  it('dragging the selection moves its movable cards together, clamped as a group and saved', async () => {
    const w = await mountCanvas()
    center.selectAll('f1')
    await settle()
    // Only selected movable cards are dragged: c is selected but stays.
    expect(['a', 'b', 'c'].map((id) => flow().findNode(id)!.draggable)).toEqual([true, true, false])
    const dropped = ['a', 'b'].map((id) => {
      const node = flow().findNode(id)!
      return { ...node, position: { x: node.position.x - 40, y: node.position.y + 30 } } as GraphNode
    })
    flow().emits.nodeDragStop({ event: new MouseEvent('mouseup'), node: dropped[0], nodes: dropped, intersections: undefined } as never)
    await settle()
    expect(shownNode('a')).toMatchObject({ x: 0, y: 30 })
    expect(shownNode('b')).toMatchObject({ x: 260, y: 30 })
    expect(shownNode('c')).toMatchObject({ x: 100, y: 200 })
    await wait()
    expect(saves).toEqual([
      {
        positions: [
          { nodeId: 'a', x: 0, y: 30 },
          { nodeId: 'b', x: 260, y: 30 },
        ],
        viewport: null,
      },
    ])
    // The edge and the cards follow the stored positions.
    expect(flow().findNode('b')!.position).toEqual({ x: 260, y: 30 })
    expect(selectedCards(w)).toEqual(['a', 'b', 'c'])
  })

  it('a group dropped past the left edge and clamped back to where it was is drawn there again', async () => {
    const w = await mountCanvas()
    center.selectAll('f1')
    await settle()
    const before = JSON.stringify(center.state.graphs.f1)
    // Vue Flow's own drag: a and b are moved 40 to the left only (a past the Canvas origin).
    flow().updateNodePositions(
      ['a', 'b'].map((id) => {
        const node = flow().findNode(id)!
        const from = { ...node.position }
        return { id, from, position: { x: from.x - 40, y: from.y }, distance: { x: 0, y: 0 }, dimensions: node.dimensions }
      }) as never,
      true,
      true,
    )
    await settle()
    expect(flow().findNode('a')!.position).toEqual({ x: -40, y: 0 })
    expect(flow().findNode('b')!.position).toEqual({ x: 220, y: 0 })
    // The drop hands the dragged Vue Flow Nodes themselves.
    const dragged = ['a', 'b'].map((id) => flow().findNode(id)!)
    flow().emits.nodeDragStop({ event: new MouseEvent('mouseup'), node: dragged[0], nodes: dragged, intersections: undefined } as never)
    await settle()
    // The group clamp brings it back to (0,0) / (260,0): the Graph is unchanged and nothing is saved ...
    expect(JSON.stringify(center.state.graphs.f1)).toBe(before)
    await wait()
    expect(saves).toEqual([])
    // ... and the Canvas shows the cards (and draws their edge) at the Graph positions again.
    for (const id of ['a', 'b']) {
      const node = flow().findNode(id)!
      expect(node.position).toEqual({ x: shownNode(id)!.x, y: shownNode(id)!.y })
      expect({ x: node.computedPosition.x, y: node.computedPosition.y }).toEqual({ x: shownNode(id)!.x, y: shownNode(id)!.y })
    }
    expect(w.get('.vue-flow__node[data-id="a"]').attributes('style')).toContain('translate(0px,0px)')
    expect(w.get('.vue-flow__node[data-id="b"]').attributes('style')).toContain('translate(260px,0px)')
  })

  it('turning the mode off clears the multi-Selection on screen and in Vue Flow, nothing else', async () => {
    const w = await mountCanvas()
    center.selectAll('f1')
    await settle()
    center.setSelectMode(false)
    await settle()
    expect(selectedCards(w)).toEqual([])
    expect(['a', 'b', 'c'].map((id) => flow().findNode(id)!.selected)).toEqual([false, false, false])
    expect(['a', 'b', 'c'].map((id) => flow().findNode(id)!.draggable)).toEqual([true, true, false])
    expect(flow().multiSelectionActive.value).toBe(false)
    expect(w.findComponent(VueFlow).props('panOnDrag')).toBe(true)
  })

  it('a Canvas first shown while the mode is on toggles on click too', async () => {
    center = session()
    center.setProject('p')
    center.setSelectMode(true)
    wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
    center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    await settle()
    expect(flow().multiSelectionActive.value).toBe(true)
    await wrapper.get('.vue-flow__node[data-id="a"]').trigger('click')
    await settle()
    await wrapper.get('.vue-flow__node[data-id="b"]').trigger('click')
    await settle()
    expect(selection()).toEqual(['a', 'b'])
  })
})

describe('Selection with the structure edits of an editable draft (TR0014 r2)', () => {
  const def = (docType: string, label: string) => ({ nodeType: 'document', docType, paletteItemId: null, label, assignee: null })
  const draftNode = (id: string, x: number) => ({
    id, kind: 'design', docType: 'L', title: id, state: 'draft', x, y: 320, movable: true, structureEditable: true, definition: def('L', id),
  })
  /** An editable draft: gn_a → gn_b by the removable draft edge ge_ab. */
  function draftGraph(): FlowGraph {
    return {
      flowId: 'f1',
      ownership: 'unassigned',
      viewport: { x: 0, y: 0, zoom: 1 },
      nodes: [draftNode('gn_a', 0), draftNode('gn_b', 260)],
      edges: [{ id: 'ge_ab', source: 'gn_a', target: 'gn_b', edgeClass: 'execution', editable: true }],
      draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
      viewMode: 'draft',
    }
  }
  /** The same draft with gn_a alone. */
  const singleGraph = (): FlowGraph => ({ ...draftGraph(), nodes: [draftNode('gn_a', 0)], edges: [] })

  let drafts: FlowDraftContent[]

  beforeEach(() => {
    graph = draftGraph()
  })

  // `draftSaveDelayMs`: long by default, so no draft save answers inside a test unless it waits for it.
  async function mountDraft(draftSaveDelayMs = 10_000) {
    drafts = []
    center = createDesignerCenterSession({
      layoutSaveDelayMs: 0,
      draftSaveDelayMs,
      readDrop: () => ({ nodeType: 'document', docType: 'T', paletteItemId: 'task', label: '구현', kind: 'task' }),
      source: {
        isSample: false,
        loadGraph: async () => graph,
        saveLayout: async (_p, _f, change) => {
          saves.push(change)
          return { ignoredNodeIds: [] }
        },
        saveDraft: async (_p, _f, version, content) => {
          drafts.push(content)
          const keyMap: Record<string, string> = {}
          content.nodes.filter((n) => n.isNew).forEach((n, i) => (keyMap[n.key] = `gn_new${i}`))
          return { version: version + 1, keyMap }
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

  const shownGraph = () => {
    const load = center.state.graphs.f1
    if (load?.status !== 'ready') throw new Error('not ready')
    return load.graph
  }
  const shownIds = () => shownGraph().nodes.map((n) => n.id)
  const canvasAttr = (w: VueWrapper<any>, name: string) => w.get('[data-test="designer-canvas"]').attributes(name)
  const clickEdge = (id: string) => flow().emits.edgeClick({ event: new MouseEvent('click'), edge: flow().findEdge(id) as GraphEdge })
  const press = (w: VueWrapper<any>, key: string) =>
    w.get('[data-test="designer-canvas"]').element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  /** The Selection, its single Node, the Inspector's target (activeSelection) and the cards drawn selected. */
  const selected = (w: VueWrapper<any>) => ({
    ids: selection(),
    single: center.activeView.value!.selectedNodeId,
    inspector: center.activeSelection.value?.node.id ?? null,
    cards: selectedCards(w),
  })

  it('an Edge click ends a Selection of several cards; Delete then removes the Edge only', async () => {
    const w = await mountDraft()
    center.selectAll('f1')
    await settle()
    expect(selection()).toEqual(['gn_a', 'gn_b'])
    expect(center.activeView.value!.selectedNodeId).toBeNull()

    clickEdge('ge_ab')
    await settle()
    expect(canvasAttr(w, 'data-selected-edge-id')).toBe('ge_ab')
    expect(flow().findEdge('ge_ab')?.data).toMatchObject({ selected: true })
    expect(selected(w)).toEqual({ ids: [], single: null, inspector: null, cards: [] })
    expect(['gn_a', 'gn_b'].map((id) => flow().findNode(id)!.selected)).toEqual([false, false])

    press(w, 'Delete')
    await settle()
    expect(shownGraph().edges.map((e) => e.id)).toEqual([])
    expect(shownIds()).toEqual(['gn_a', 'gn_b'])
    expect(canvasAttr(w, 'data-selected-edge-id')).toBe('')
  })

  it('전체 선택 while an Edge is selected ends the Edge selection; Delete then removes no Edge', async () => {
    const w = await mountDraft()
    clickEdge('ge_ab')
    await settle()
    expect(canvasAttr(w, 'data-selected-edge-id')).toBe('ge_ab')

    center.selectAll('f1')
    await settle()
    expect(canvasAttr(w, 'data-selected-edge-id')).toBe('')
    expect(flow().findEdge('ge_ab')?.data?.selected).not.toBe(true)
    expect(selected(w)).toEqual({ ids: ['gn_a', 'gn_b'], single: null, inspector: null, cards: ['gn_a', 'gn_b'] })

    // Only the two cards are selected for Delete, and a Selection of several is not removed.
    press(w, 'Delete')
    press(w, 'Backspace')
    await settle()
    expect(shownGraph().edges.map((e) => e.id)).toEqual(['ge_ab'])
    expect(shownIds()).toEqual(['gn_a', 'gn_b'])
    expect(selected(w).cards).toEqual(['gn_a', 'gn_b'])
  })

  it('an undone add takes the new card out of the Selection: the card left is the single selection again', async () => {
    graph = singleGraph()
    const w = await mountDraft()
    expect(center.dropOnCanvas('f1', { getData: () => '' }, 520, 320)).toBe(true)
    await settle()
    const added = shownIds().find((id) => id.startsWith('tmp_'))!
    expect(added).toBeDefined()
    center.selectAll('f1')
    await settle()
    expect(selected(w)).toEqual({ ids: ['gn_a', added], single: null, inspector: null, cards: ['gn_a', added] })

    expect(center.undoEdit('f1')).toBe(true)
    await settle()
    expect(shownIds()).toEqual(['gn_a'])
    expect(selected(w)).toEqual({ ids: ['gn_a'], single: 'gn_a', inspector: 'gn_a', cards: ['gn_a'] })
  })

  it('a redone removal takes the removed card out of the Selection too', async () => {
    const w = await mountDraft()
    expect(center.removeNode('f1', 'gn_b')).toBe(true)
    expect(center.undoEdit('f1')).toBe(true)
    await settle()
    expect([...shownIds()].sort()).toEqual(['gn_a', 'gn_b'])
    center.selectAll('f1')
    await settle()
    expect([...selection()].sort()).toEqual(['gn_a', 'gn_b'])
    expect(center.activeSelection.value).toBeNull()

    expect(center.redoEdit('f1')).toBe(true)
    await settle()
    expect(shownIds()).toEqual(['gn_a'])
    expect(selected(w)).toEqual({ ids: ['gn_a'], single: 'gn_a', inspector: 'gn_a', cards: ['gn_a'] })
  })

  it('a save answering the new card its key renames it in the Selection instead of dropping it', async () => {
    graph = singleGraph()
    const w = await mountDraft(0)
    expect(center.dropOnCanvas('f1', { getData: () => '' }, 520, 320)).toBe(true)
    const added = shownIds().find((id) => id.startsWith('tmp_'))!
    center.selectAll('f1')
    await wait()
    await settle()
    expect(drafts.map((d) => d.nodes.map((n) => n.key))).toEqual([['gn_a', added]])
    expect(shownIds()).toEqual(['gn_a', 'gn_new0'])
    expect(selected(w)).toEqual({ ids: ['gn_a', 'gn_new0'], single: null, inspector: null, cards: ['gn_a', 'gn_new0'] })
  })
})
