/**
 * flowgate.v02.0059 T#2 (0011-CH contract D) — 정렬 / 분배 and 자동 배치, pure:
 * - the client port of server/modules/flow_gate/graph_workflow/domain/auto_layout.py: the same input gives the
 *   same coordinates. SERVER_CASES were made by running the server's own `layout()` / `layered_depths()` on
 *   these inputs (Python, the server file unchanged) and are compared value for value.
 * - the input a shown Graph gives (projection, Graph nodes, free documents, the sequence chain, cards that
 *   cannot move kept as saved) and the resulting card positions; the density's spacing scale.
 * - (0016-TR rework) the sequence chain by the server's roles — the root node and the slot nodes, a lone root or
 *   a lone slot included, the root found through its document's supersede chain when only that tells — and the
 *   documents the Canvas read left out (collapse) keeping their place (FlowLayoutBasis).
 * - 정렬 6종 / 균등 분배 2종 of the selected cards; the menu availability by the selection size.
 */
import { describe, expect, it } from 'vitest'
import { arrangeMinimum, arrangeSelection, ARRANGE_KINDS, type ArrangeKind } from '@/features/designer/center/arrange/arrangeNodes'
import {
  autoLayoutPlacements,
  layeredDepths,
  layout,
  layoutInputOf,
  loadLayoutBasis,
  needsLayoutBasis,
  sequenceRootOf,
  type AutoLayoutInput,
} from '@/features/designer/center/arrange/autoLayout'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import {
  ALL_MENU_TARGETS,
  DENSITY_LAYOUT_SCALE,
  DESIGNER_ARRANGE_GROUPS,
  DESIGNER_DENSITIES,
  isMenuCommandEnabled,
} from '@/features/designer/top/designerTop'

interface ServerCase {
  title: string
  projection: string
  input: Omit<AutoLayoutInput, 'saved' | 'graphEdges'> & { graphEdges?: [string, string][] }
  saved: Record<string, number[]>
  /** key -> [x, y, source] as the server's layout() answered. */
  placed: Record<string, (number | string)[]>
  /** The server's layered_depths() of the Graph nodes and edges. */
  depth: Record<string, number>
}

// Output of server auto_layout.layout(projection, chain=…, free_docs=…, graph_nodes=…, graph_edges=…, saved=…).
const SERVER_CASES: ServerCase[] = [
  {
    title: 'graph: layers by longest path, a diamond, free documents under the lowest',
    projection: 'graph',
    input: { graphNodes: ['gn_e', 'gn_a', 'gn_c', 'gn_b', 'gn_d'], graphEdges: [['gn_a', 'gn_b'], ['gn_a', 'gn_c'], ['gn_b', 'gn_d'], ['gn_c', 'gn_d'], ['gn_d', 'gn_e'], ['gn_a', 'gn_e'], ['gn_x', 'gn_a']], freeDocs: ['doc:f1.0003-D', 'doc:f1.0001-R', 'doc:f1.0002-NR'] },
    saved: {},
    placed: {'gn_a': [0, 0, 'auto'], 'gn_b': [260, 0, 'auto'], 'gn_c': [260, 140, 'auto'], 'gn_d': [520, 0, 'auto'], 'gn_e': [780, 0, 'auto'], 'doc:f1.0003-D': [0, 340, 'auto'], 'doc:f1.0001-R': [260, 340, 'auto'], 'doc:f1.0002-NR': [520, 340, 'auto']},
    depth: {'gn_a': 0, 'gn_b': 1, 'gn_c': 1, 'gn_e': 3, 'gn_d': 2},
  },
  {
    title: 'graph: a cycle and a node no layered node reaches',
    projection: 'graph',
    input: { graphNodes: ['gn_s', 'gn_p', 'gn_q', 'gn_r', 'gn_u', 'gn_v'], graphEdges: [['gn_s', 'gn_p'], ['gn_p', 'gn_q'], ['gn_q', 'gn_r'], ['gn_r', 'gn_p'], ['gn_u', 'gn_v'], ['gn_v', 'gn_u']] },
    saved: {},
    placed: {'gn_s': [0, 0, 'auto'], 'gn_u': [0, 140, 'auto'], 'gn_p': [260, 0, 'auto'], 'gn_v': [260, 140, 'auto'], 'gn_q': [520, 0, 'auto'], 'gn_r': [780, 0, 'auto']},
    depth: {'gn_s': 0, 'gn_p': 1, 'gn_q': 2, 'gn_r': 3, 'gn_u': 0, 'gn_v': 1},
  },
  {
    title: 'graph: saved cards win and push auto cards down by the collision step',
    projection: 'graph',
    input: { graphNodes: ['gn_a', 'gn_b', 'gn_c', 'gn_d'], graphEdges: [['gn_a', 'gn_b'], ['gn_a', 'gn_c']], freeDocs: ['doc:x'] },
    saved: {'gn_d': [260, 60], 'doc:x': [0, 0]},
    placed: {'doc:x': [0, 0, 'saved'], 'gn_d': [260, 60, 'saved'], 'gn_a': [0, 120, 'auto'], 'gn_b': [260, 240, 'auto'], 'gn_c': [260, 380, 'auto']},
    depth: {'gn_a': 0, 'gn_d': 0, 'gn_b': 1, 'gn_c': 1},
  },
  {
    title: 'documents: one row of documents, draft nodes in layers from y=320',
    projection: 'documents',
    input: { freeDocs: ['doc:b', 'doc:a', 'doc:c'], graphNodes: ['tmp_2', 'gn_1', 'tmp_1'], graphEdges: [['gn_1', 'tmp_1'], ['tmp_1', 'tmp_2']] },
    saved: {},
    placed: {'doc:b': [0, 0, 'auto'], 'doc:a': [260, 0, 'auto'], 'doc:c': [520, 0, 'auto'], 'gn_1': [0, 320, 'auto'], 'tmp_1': [260, 320, 'auto'], 'tmp_2': [520, 320, 'auto']},
    depth: {'gn_1': 0, 'tmp_1': 1, 'tmp_2': 2},
  },
  {
    title: 'sequence: the chain next to each other, documents on y=180 from x=260',
    projection: 'sequence',
    input: { chain: ['doc:root', 'slot_1', 'slot_2', 'slot_3'], freeDocs: ['doc:m', 'doc:n'] },
    saved: {'slot_2': [700, 300]},
    placed: {'slot_2': [700, 300, 'saved'], 'doc:root': [0, 0, 'auto'], 'slot_1': [260, 0, 'auto'], 'slot_3': [960, 300, 'auto'], 'doc:m': [260, 180, 'auto'], 'doc:n': [520, 180, 'auto']},
    depth: {},
  },
  {
    title: 'any other projection: one row by key',
    projection: 'someday',
    input: { chain: ['c'], freeDocs: ['b', 'a'], graphNodes: ['d'] },
    saved: {},
    placed: {'a': [0, 0, 'auto'], 'b': [260, 0, 'auto'], 'c': [520, 0, 'auto'], 'd': [780, 0, 'auto']},
    depth: {'d': 0},
  },
  {
    title: 'collision tries stop after 20',
    projection: 'graph',
    input: { graphNodes: ['gn_z'], others: ['s00', 's01', 's02', 's03', 's04', 's05', 's06', 's07', 's08', 's09', 's10', 's11', 's12', 's13', 's14', 's15', 's16', 's17', 's18', 's19', 's20', 's21', 's22', 's23', 's24'] },
    saved: {'s00': [0, 0], 's01': [0, 120], 's02': [0, 240], 's03': [0, 360], 's04': [0, 480], 's05': [0, 600], 's06': [0, 720], 's07': [0, 840], 's08': [0, 960], 's09': [0, 1080], 's10': [0, 1200], 's11': [0, 1320], 's12': [0, 1440], 's13': [0, 1560], 's14': [0, 1680], 's15': [0, 1800], 's16': [0, 1920], 's17': [0, 2040], 's18': [0, 2160], 's19': [0, 2280], 's20': [0, 2400], 's21': [0, 2520], 's22': [0, 2640], 's23': [0, 2760], 's24': [0, 2880]},
    placed: {'s00': [0, 0, 'saved'], 's01': [0, 120, 'saved'], 's02': [0, 240, 'saved'], 's03': [0, 360, 'saved'], 's04': [0, 480, 'saved'], 's05': [0, 600, 'saved'], 's06': [0, 720, 'saved'], 's07': [0, 840, 'saved'], 's08': [0, 960, 'saved'], 's09': [0, 1080, 'saved'], 's10': [0, 1200, 'saved'], 's11': [0, 1320, 'saved'], 's12': [0, 1440, 'saved'], 's13': [0, 1560, 'saved'], 's14': [0, 1680, 'saved'], 's15': [0, 1800, 'saved'], 's16': [0, 1920, 'saved'], 's17': [0, 2040, 'saved'], 's18': [0, 2160, 'saved'], 's19': [0, 2280, 'saved'], 's20': [0, 2400, 'saved'], 's21': [0, 2520, 'saved'], 's22': [0, 2640, 'saved'], 's23': [0, 2760, 'saved'], 's24': [0, 2880, 'saved'], 'gn_z': [0, 2400, 'auto']},
    depth: {'gn_z': 0},
  },
]

const asObject = (placed: ReturnType<typeof layout>) =>
  Object.fromEntries([...placed].map(([key, p]) => [key, [p.x, p.y, p.source]]))

describe('auto_layout port (server input -> server coordinates)', () => {
  it.each(SERVER_CASES.map((c) => [c.title, c] as const))('%s', (_title, c) => {
    const saved = new Map(Object.entries(c.saved).map(([key, [x, y]]) => [key, { x, y }]))
    const placed = layout(c.projection, { ...c.input, saved })
    expect(asObject(placed)).toEqual(c.placed)
    if (c.input.graphNodes?.length) {
      expect(Object.fromEntries(layeredDepths(c.input.graphNodes, c.input.graphEdges ?? []))).toEqual(c.depth)
    }
  })

  it('the spacing scale multiplies every step and offset, never the card footprint', () => {
    const diamond = SERVER_CASES[0]
    expect(asObject(layout('graph', diamond.input, 1.5))).toEqual({
      gn_a: [0, 0, 'auto'], gn_b: [390, 0, 'auto'], gn_c: [390, 210, 'auto'], gn_d: [780, 0, 'auto'], gn_e: [1170, 0, 'auto'],
      'doc:f1.0003-D': [0, 510, 'auto'], 'doc:f1.0001-R': [390, 510, 'auto'], 'doc:f1.0002-NR': [780, 510, 'auto'],
    })
    expect(asObject(layout('documents', { freeDocs: ['a', 'b'], graphNodes: ['g'] }, 1.25))).toEqual({
      a: [0, 0, 'auto'], b: [325, 0, 'auto'], g: [0, 400, 'auto'],
    })
    expect(asObject(layout('sequence', { chain: ['r', 's'], freeDocs: ['d'] }, 1.5))).toEqual({
      r: [0, 0, 'auto'], s: [390, 0, 'auto'], d: [390, 270, 'auto'],
    })
    // The collision step grows with it (a saved card right where the first auto card goes).
    const pushed = layout('graph', { graphNodes: ['g'], others: ['s'], saved: new Map([['s', { x: 0, y: 0 }]]) }, 1.5)
    expect(pushed.get('g')).toEqual({ x: 0, y: 180, source: 'auto' })
  })
})

const node = (id: string, x: number, y: number, extra: Partial<FlowGraphNode> = {}): FlowGraphNode => ({
  id, kind: 'design', title: id, x, y, movable: true, ...extra,
})
const graphNode = (id: string, x: number, y: number, extra: Partial<FlowGraphNode> = {}) =>
  node(id, x, y, { definition: { nodeType: 'document', docType: 'D', paletteItemId: null, label: id, assignee: null }, ...extra })
const exec = (source: string, target: string) => ({ id: `e:${source}>${target}`, source, target, edgeClass: 'execution' })
/** A v0.1 slot node as the server draws it (`slot` set). */
const slotNode = (id: string, x: number, y: number, extra: Partial<FlowGraphNode> = {}) =>
  node(id, x, y, {
    slot: { itemId: 1, itemSeq: 1, order: 1, type: 'T', label: id, derivedStatus: 'pending', protected: false, sourceWpCardId: null },
    ...extra,
  })
/** A document node as the server draws it (`document` set). */
const docNode = (id: string, x: number, y: number, supersedes: string[] = [], extra: Partial<FlowGraphNode> = {}) =>
  node(id, x, y, {
    document: { docId: id.replace(/^doc:/, ''), typeCode: null, seq: null, title: id, docReviewStatus: null, status: 'open', supersedes },
    ...extra,
  })

describe('자동 배치 of a shown Graph', () => {
  it('a graph projection: Graph nodes by their execution edges, the rest as documents — the server coordinates', () => {
    const c = SERVER_CASES[0]
    const graph: FlowGraph = {
      flowId: 'f1',
      projection: 'graph',
      // Server order: Graph nodes, then documents in document order; positions as the user left them.
      nodes: [
        ...c.input.graphNodes!.map((id, i) => graphNode(id, 1000 + i * 7, 900 - i * 3)),
        ...c.input.freeDocs!.map((id, i) => node(id, 40 * i, 777)),
      ],
      edges: [
        ...c.input.graphEdges!.filter(([s]) => s !== 'gn_x').map(([s, t]) => exec(s, t)),
        { id: 'ref:1', source: 'doc:f1.0001-R', target: 'gn_a', edgeClass: 'reference' },
      ],
    }
    const { projection, input } = layoutInputOf(graph)
    expect(projection).toBe('graph')
    expect(input.graphNodes).toEqual(c.input.graphNodes)
    expect(input.freeDocs).toEqual(c.input.freeDocs)
    expect(input.chain).toEqual([])
    expect(autoLayoutPlacements(graph).map((p) => [p.nodeId, p.x, p.y])).toEqual(
      graph.nodes.map((n) => [n.id, c.placed[n.id][0], c.placed[n.id][1]]),
    )
  })

  it('a sequence projection: the root and the slots as the server draws them, a card that cannot move kept as saved', () => {
    const c = SERVER_CASES[4]
    const graph: FlowGraph = {
      flowId: 'f1',
      projection: 'sequence',
      sequenceRootDocId: 'root',
      nodes: [
        docNode('doc:root', 5, 5), slotNode('slot_1', 9, 9), slotNode('slot_2', 700, 300, { movable: false }), slotNode('slot_3', 1, 1),
        docNode('doc:m', 3, 3), docNode('doc:n', 4, 4),
      ],
      edges: [exec('doc:root', 'slot_1'), exec('slot_1', 'slot_2'), exec('slot_2', 'slot_3')],
    }
    const { input } = layoutInputOf(graph)
    expect(input.chain).toEqual(c.input.chain)
    expect(input.freeDocs).toEqual(c.input.freeDocs)
    expect([...(input.saved ?? new Map())]).toEqual([['slot_2', { x: 700, y: 300 }]])
    const placements = autoLayoutPlacements(graph)
    expect(Object.fromEntries(placements.map((p) => [p.nodeId, [p.x, p.y, 'auto']]))).toEqual(
      Object.fromEntries(Object.entries(c.placed).filter(([key]) => key !== 'slot_2')),
    )
    expect(placements.some((p) => p.nodeId === 'slot_2')).toBe(false)
  })

  it('a documents projection (unassigned draft): documents on one row, draft nodes in layers below', () => {
    const c = SERVER_CASES[3]
    const graph: FlowGraph = {
      flowId: 'f1',
      projection: 'documents',
      nodes: [
        node('doc:b', 0, 50), node('doc:a', 0, 60), node('doc:c', 0, 70),
        graphNode('tmp_2', 1, 1), graphNode('gn_1', 2, 2), graphNode('tmp_1', 3, 3),
      ],
      edges: [exec('gn_1', 'tmp_1'), exec('tmp_1', 'tmp_2')],
    }
    expect(Object.fromEntries(autoLayoutPlacements(graph).map((p) => [p.nodeId, [p.x, p.y, 'auto']]))).toEqual(c.placed)
  })

  it('every position is inside the Canvas origin and a wider density spreads the cards further', () => {
    const graph: FlowGraph = {
      flowId: 'f1',
      projection: 'graph',
      nodes: [graphNode('gn_a', 500, 500), graphNode('gn_b', 10, 10), graphNode('gn_c', 30, 0)],
      edges: [exec('gn_a', 'gn_b'), exec('gn_a', 'gn_c')],
    }
    const at = (scale: number) => Object.fromEntries(autoLayoutPlacements(graph, scale).map((p) => [p.nodeId, [p.x, p.y]]))
    expect(at(DENSITY_LAYOUT_SCALE.normal!)).toEqual({ gn_a: [0, 0], gn_b: [260, 0], gn_c: [260, 140] })
    expect(at(DENSITY_LAYOUT_SCALE.wide!)).toEqual({ gn_a: [0, 0], gn_b: [325, 0], gn_c: [325, 175] })
    expect(at(DENSITY_LAYOUT_SCALE['very-wide']!)).toEqual({ gn_a: [0, 0], gn_b: [390, 0], gn_c: [390, 210] })
    for (const scale of [1, 1.25, 1.5]) {
      expect(autoLayoutPlacements(graph, scale).every((p) => p.x >= 0 && p.y >= 0)).toBe(true)
    }
  })
})

// Server output: auto_layout.layout('sequence', chain=[the one card], free_docs=[…]) and, for the v0.1 Flows,
// legacy_projector.project + canvas_service._place (run in Python, no saved layout): a lone root or a lone
// slot is the chain, at (0, 0); the documents start at (260, 180).
describe('the sequence chain by the server roles, and the documents the read left out (0016-TR rework)', () => {
  const LONE = { chain: { 'doc:f1.0001-R': [0, 0, 'auto'], 'doc:f1.0002-D': [260, 180, 'auto'] } }

  it('a v0.1 root with no slot (no execution edge) is the chain: (0, 0), not the document row', () => {
    expect(asObject(layout('sequence', { chain: ['doc:f1.0001-R'], freeDocs: ['doc:f1.0002-D'] }))).toEqual(LONE.chain)
    const graph: FlowGraph = {
      flowId: 'f1',
      projection: 'sequence',
      sequenceRootDocId: 'f1.0001-R',
      nodes: [docNode('doc:f1.0001-R', 500, 500), docNode('doc:f1.0002-D', 10, 10)],
      edges: [],
    }
    expect(layoutInputOf(graph).input).toMatchObject({ chain: ['doc:f1.0001-R'], freeDocs: ['doc:f1.0002-D'] })
    expect(autoLayoutPlacements(graph).map((p) => [p.nodeId, p.x, p.y])).toEqual([['doc:f1.0001-R', 0, 0], ['doc:f1.0002-D', 260, 180]])
  })

  it('a lone slot (the root cancelled, not drawn) is the chain: (0, 0)', () => {
    const graph: FlowGraph = {
      flowId: 'f1',
      projection: 'sequence',
      sequenceRootDocId: 'f1.0001-R',
      nodes: [slotNode('slot_21', 400, 40), docNode('doc:f1.0002-D', 10, 10)],
      edges: [],
    }
    expect(layoutInputOf(graph).input).toMatchObject({ chain: ['slot_21'], freeDocs: ['doc:f1.0002-D'] })
    expect(autoLayoutPlacements(graph).map((p) => [p.nodeId, p.x, p.y])).toEqual([['slot_21', 0, 0], ['doc:f1.0002-D', 260, 180]])
  })

  it('tells the root as legacy_projector draws it, or that only the supersede chain can (undefined)', () => {
    const seq = (nodes: FlowGraphNode[], extra: Partial<FlowGraph> = {}): FlowGraph =>
      ({ flowId: 'f1', projection: 'sequence', sequenceRootDocId: 'f1.0001-R', nodes, edges: [], ...extra })
    expect(sequenceRootOf(seq([docNode('doc:f1.0005-R', 0, 0, ['f1.0002-R']), slotNode('s', 0, 0)]))).toBe('doc:f1.0005-R')
    expect(sequenceRootOf(seq([slotNode('s', 0, 0), docNode('doc:f1.0003-D', 0, 0)]))).toBeNull()
    expect(sequenceRootOf(seq([docNode('doc:f1.0001-R', 0, 0), docNode('doc:f1.0003-D', 0, 0)]))).toBe('doc:f1.0001-R')
    expect(sequenceRootOf(seq([docNode('doc:f1.0003-D', 0, 0)]))).toBeNull()
    expect(sequenceRootOf(seq([docNode('doc:f1.0005-R', 0, 0, ['f1.0002-R'])]))).toBeUndefined()
    expect(sequenceRootOf(seq([]))).toBeNull()
    expect(needsLayoutBasis(seq([docNode('doc:f1.0005-R', 0, 0, ['f1.0002-R'])]))).toBe(true)
    expect(needsLayoutBasis(seq([docNode('doc:f1.0001-R', 0, 0)]))).toBe(false)
    expect(needsLayoutBasis(seq([docNode('doc:f1.0001-R', 0, 0)], { hiddenNodeCount: 1 }))).toBe(true)
    expect(needsLayoutBasis({ flowId: 'f1', projection: 'documents', nodes: [docNode('doc:a', 0, 0, ['x'])], edges: [] })).toBe(false)
  })

  it('follows the root document supersede chain as the server live_end does', async () => {
    const whole: FlowGraph = {
      flowId: 'f1',
      projection: 'sequence',
      sequenceRootDocId: 'f1.0001-R',
      nodes: [docNode('doc:f1.0005-R', 0, 0, ['f1.0002-R']), docNode('doc:f1.0003-D', 0, 0)],
      edges: [],
    }
    const docs = (links: Record<string, [string | null, string | null, string | null]>) =>
      async (docId: string) => {
        const link = links[docId]
        if (!link) throw new Error(`no ${docId}`)
        return { docId, groupId: link[0], status: link[1], supersededBy: link[2] }
      }
    const chain = { 'f1.0001-R': ['f1', 'open', 'f1.0002-R'], 'f1.0002-R': ['f1', 'open', 'f1.0005-R'], 'f1.0005-R': ['f1', 'open', null] } as const
    const basis = await loadLayoutBasis(async () => whole, docs({ ...chain }))
    expect(basis.sequenceRoot).toBe('doc:f1.0005-R')
    expect(layoutInputOf(whole, basis).input).toMatchObject({ chain: ['doc:f1.0005-R'], freeDocs: ['doc:f1.0003-D'] })
    // A cancelled link, another Flow or a cycle ends the chain with no root: the first node is a document.
    for (const broken of [
      { ...chain, 'f1.0002-R': ['f1', 'cancelled', 'f1.0005-R'] },
      { ...chain, 'f1.0002-R': ['f2', 'open', 'f1.0005-R'] },
      { ...chain, 'f1.0002-R': ['f1', 'open', 'f1.0001-R'] },
    ] as Record<string, [string | null, string | null, string | null]>[]) {
      expect((await loadLayoutBasis(async () => whole, docs(broken))).sequenceRoot).toBeNull()
    }
    // A read that fails is not guessed over; with no document read the root cannot be told.
    await expect(loadLayoutBasis(async () => whole, docs({ 'f1.0001-R': ['f1', 'open', 'f1.0002-R'] }))).rejects.toThrow()
    await expect(loadLayoutBasis(async () => whole)).rejects.toThrow()
  })

  it('a document the shown Graph left out keeps its place in the order and in the collision check', () => {
    // Server: documents R, CH, D -> R (0, 0), CH (260, 0), D (520, 0); collapse then hides CH.
    expect(asObject(layout('documents', { freeDocs: ['doc:R', 'doc:CH', 'doc:D'] }))).toEqual({
      'doc:R': [0, 0, 'auto'], 'doc:CH': [260, 0, 'auto'], 'doc:D': [520, 0, 'auto'],
    })
    const shown: FlowGraph = {
      flowId: 'f1', projection: 'documents', hiddenNodeCount: 1, nodes: [docNode('doc:R', 70, 70), docNode('doc:D', 90, 90)], edges: [],
    }
    const whole: FlowGraph = {
      flowId: 'f1', projection: 'documents', nodes: [docNode('doc:R', 0, 0), docNode('doc:CH', 260, 0), docNode('doc:D', 520, 0)], edges: [],
    }
    // Without the whole Flow, D takes the hidden CH's place.
    expect(autoLayoutPlacements(shown).map((p) => [p.nodeId, p.x, p.y])).toEqual([['doc:R', 0, 0], ['doc:D', 260, 0]])
    const basis = { whole, sequenceRoot: null }
    expect(layoutInputOf(shown, basis).input.freeDocs).toEqual(['doc:R', 'doc:CH', 'doc:D'])
    expect(autoLayoutPlacements(shown, 1, basis).map((p) => [p.nodeId, p.x, p.y])).toEqual([['doc:R', 0, 0], ['doc:D', 520, 0]])
    // A left-out card that cannot move stays where it is and pushes the card that would land on it.
    const fixedCh = { ...whole, nodes: [whole.nodes[0], { ...whole.nodes[1], x: 520, y: 0, movable: false }, whole.nodes[2]] }
    expect(autoLayoutPlacements(shown, 1, { whole: fixedCh, sequenceRoot: null }).map((p) => [p.nodeId, p.x, p.y]))
      .toEqual([['doc:R', 0, 0], ['doc:D', 520, 120]])
  })
})

describe('배치 밀도 (layout density) scale', () => {
  it('is 1.5 / 1.25 / 1 for 매우 넓게 / 넓게 / 보통 and none for 자율형, in one constant', () => {
    expect(Object.keys(DENSITY_LAYOUT_SCALE)).toEqual([...DESIGNER_DENSITIES])
    expect(DENSITY_LAYOUT_SCALE).toEqual({ 'very-wide': 1.5, wide: 1.25, normal: 1, free: null })
  })
})

describe('정렬 / 균등 분배 of the selected cards', () => {
  const cards = [node('a', 40, 300), node('b', 300, 100), node('c', 130, 520), node('d', 900, 900)]
  const run = (kind: ArrangeKind, nodes = cards, selected = ['a', 'b', 'c']) =>
    Object.fromEntries(arrangeSelection(nodes, selected, kind).map((p) => [p.nodeId, [p.x, p.y]]))

  it('aligns the left / centre / right edges and the top / middle / bottom edges (d is not selected)', () => {
    expect(run('align-left')).toEqual({ a: [40, 300], b: [40, 100], c: [40, 520] })
    expect(run('align-center')).toEqual({ a: [170, 300], b: [170, 100], c: [170, 520] })
    expect(run('align-right')).toEqual({ a: [300, 300], b: [300, 100], c: [300, 520] })
    expect(run('align-top')).toEqual({ a: [40, 100], b: [300, 100], c: [130, 100] })
    expect(run('align-middle')).toEqual({ a: [40, 310], b: [300, 310], c: [130, 310] })
    expect(run('align-bottom')).toEqual({ a: [40, 520], b: [300, 520], c: [130, 520] })
  })

  it('distributes at equal steps between the first and the last card along the axis', () => {
    const row = [node('a', 0, 0), node('b', 100, 40), node('c', 600, 10), node('e', 250, 80)]
    const all = ['a', 'b', 'c', 'e']
    expect(run('distribute-horizontal', row, all)).toEqual({ a: [0, 0], b: [200, 40], e: [400, 80], c: [600, 10] })
    expect(run('distribute-vertical', row, all)).toEqual({ a: [0, 0], c: [600, 27], b: [100, 53], e: [250, 80] })
  })

  it('needs 2 selected cards to align and 3 to distribute', () => {
    expect(ARRANGE_KINDS.map(arrangeMinimum)).toEqual([2, 2, 2, 2, 2, 2, 3, 3])
    expect(run('align-left', cards, ['a'])).toEqual({})
    expect(run('distribute-horizontal', cards, ['a', 'b'])).toEqual({})
    expect(run('align-left', cards, ['a', 'b'])).toEqual({ a: [40, 300], b: [40, 100] })
  })

  it('a card that cannot move stays but still counts; the result never leaves the Canvas origin', () => {
    const fixed = [node('a', 40, 300, { movable: false }), node('b', 300, 100), node('c', 130, 520)]
    expect(run('align-left', fixed)).toEqual({ b: [40, 100], c: [40, 520] })
    const outside = [node('a', -20, 0, { movable: false }), node('b', 300, 100)]
    // b would go to x=-20: the moved cards are brought back inside as a whole.
    expect(run('align-left', outside, ['a', 'b'])).toEqual({ b: [0, 100] })
  })

  it('the Edit menu / Toolbar commands follow the selection size (2 for 정렬, 3 for 균등 분배)', () => {
    const commands = DESIGNER_ARRANGE_GROUPS.flat()
    expect(commands.map((c) => c.slice('edit.'.length))).toEqual([...ARRANGE_KINDS])
    const enabled = (count: number) => commands.filter((c) => isMenuCommandEnabled(c, null, { ...ALL_MENU_TARGETS, selectedCount: count }))
    expect(enabled(0)).toEqual([])
    expect(enabled(1)).toEqual([])
    expect(enabled(2)).toEqual(commands.slice(0, 6))
    expect(enabled(3)).toEqual(commands)
    expect(isMenuCommandEnabled('edit.align-left', null, { project: true, group: false, flow: false, selectedCount: 5 })).toBe(false)
    // 자동 배치 needs the open Flow only.
    expect(isMenuCommandEnabled('edit.auto-layout', null, { ...ALL_MENU_TARGETS, selectedCount: 0 })).toBe(true)
    expect(isMenuCommandEnabled('edit.auto-layout', null, { project: true, group: false, flow: false })).toBe(false)
  })
})
