/**
 * flowgate.v02.0068 T#2 — 가로 / 세로 자동정렬, pure (arrange/directionalLayout.ts):
 * - 0005-L §5.4 L1 ~ L15, horizontal and vertical, at the coordinates the L rules give (card size 176 x 92 unless
 *   given, movable and shown unless said);
 * - 0004-D §7 B: branch -> merge, isolated cards apart, a fixed start, a cycle, a collapsed document, a lone sequence
 *   card, the same result after a `tmp_` key became a stored key, wide branches and far merges, branches of other
 *   lengths, several starts and groups, real card sizes, obstacles, a forced candidate refused, and the same sides
 *   from the same positions (the Canvas's facingSide / edgeAnchors, the MiniMap's lines).
 * The cards start somewhere else on purpose: their current position is no input.
 */
import { describe, expect, it } from 'vitest'
import { edgeAnchors, facingSide, type NodeRect } from '@/features/designer/center/canvas/edgeGeometry'
import { checkDirectionalLayout, planDirectionalLayout } from '@/features/designer/center/arrange/directionalLayout'
import type { LayoutDirection } from '@/features/designer/center/arrange/layoutInput'
import type { FlowLayoutBasis } from '@/features/designer/center/arrange/autoLayout'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import { miniMapEdges } from '@/features/designer/minimap/minimapGeometry'

type Size = { width: number; height: number }

let scatter = 0
/** A card (a Graph node) somewhere away from where it will go. `root`: a 요구사항 (R) document card. */
function card(id: string, label: string, opts: { seq?: number; root?: boolean; movable?: boolean; at?: [number, number] } = {}): FlowGraphNode {
  scatter += 1
  const [x, y] = opts.at ?? [3000 + 37 * scatter, 2000 - 53 * scatter]
  const docType = opts.root ? 'R' : 'T'
  return {
    id,
    kind: opts.root ? 'requirement' : 'task',
    title: label,
    x,
    y,
    movable: opts.movable ?? true,
    definition: { nodeType: 'document', docType, paletteItemId: null, label, assignee: null },
    document: opts.seq === undefined ? null : { docId: `p.f.000${opts.seq}-${docType}`, typeCode: docType, seq: opts.seq, title: label, docReviewStatus: null, status: 'open', supersedes: [] },
  }
}

/** A document (no Graph definition). */
function doc(id: string, title: string, seq: number, opts: { movable?: boolean; at?: [number, number] } = {}): FlowGraphNode {
  scatter += 1
  const [x, y] = opts.at ?? [4000 + 41 * scatter, 2500 - 29 * scatter]
  return {
    id, kind: 'design', title, docType: 'D', x, y, movable: opts.movable ?? true,
    document: { docId: `p.f.00${10 + seq}-D`, typeCode: 'D', seq, title, docReviewStatus: null, status: 'open', supersedes: [] },
  }
}

function graph(nodes: FlowGraphNode[], edges: Array<[string, string]>, extra: Partial<FlowGraph> = {}): FlowGraph {
  return {
    flowId: 'f',
    projection: 'graph',
    nodes,
    edges: edges.map(([source, target], i) => ({ id: `e${i}`, source, target, edgeClass: 'execution' })),
    ...extra,
  }
}

function lay(g: FlowGraph, dir: LayoutDirection, sizes: Record<string, Size> = {}, basis?: FlowLayoutBasis) {
  const plan = planDirectionalLayout(g, basis, dir, (id) => sizes[id])
  const at = Object.fromEntries(plan.candidate.map((p) => [p.nodeId, [p.x, p.y]]))
  return { plan, at, refusal: checkDirectionalLayout(plan) }
}

/** The shown Graph with every placed card where the layout put it. */
const applied = (g: FlowGraph, at: Record<string, number[]>): FlowGraph => ({
  ...g,
  nodes: g.nodes.map((node) => (at[node.id] ? { ...node, x: at[node.id][0], y: at[node.id][1] } : node)),
})

const rectOf = (g: FlowGraph, id: string, sizes: Record<string, Size> = {}): NodeRect => {
  const node = g.nodes.find((n) => n.id === id)!
  return { x: node.x, y: node.y, width: sizes[id]?.width ?? 176, height: sizes[id]?.height ?? 92 }
}

/** The side every execution edge leaves from, judged by the unchanged facingSide at the given sizes. */
const sidesOf = (g: FlowGraph, sizes: Record<string, Size> = {}) =>
  g.edges.map((e) => `${facingSide(rectOf(g, e.source, sizes), rectOf(g, e.target, sizes))}`)

const R = (id = 'R') => card(id, 'R', { root: true })

const shortMerge = () => graph([R(), card('A', 'A'), card('B', 'B')], [['R', 'A'], ['A', 'B'], ['R', 'B']])

const branchMerge = () =>
  graph(
    [card('P', 'P'), R(), card('NA', 'NA'), card('NB', 'NB'), card('TA', 'TA'), card('TB', 'TB'), card('Z', 'Z')],
    [['R', 'NA'], ['R', 'NB'], ['NA', 'TA'], ['NB', 'TB'], ['TA', 'Z'], ['TB', 'Z']],
  )

const withObstacle = (o: FlowGraphNode) => graph([R(), card('A', 'A'), card('B', 'B'), o], [['R', 'A'], ['A', 'B']])

describe('0005-L §5.4 L1 ~ L15 (flowgate.v02.0068 T#2)', () => {
  it('L1: the start card at the top left, a taller / wider card next — V4 passes', () => {
    const g = graph([R(), card('A', 'A')], [['R', 'A']])
    const h = lay(g, 'horizontal', { A: { width: 176, height: 300 } })
    expect(h.at).toEqual({ R: [0, 0], A: [260, 0] })
    expect(h.refusal).toBeNull()
    const v = lay(g, 'vertical', { A: { width: 400, height: 92 } })
    expect(v.at).toEqual({ R: [0, 0], A: [0, 176] })
    expect(v.refusal).toBeNull()
  })

  it('L2: other sizes — the start side, the centre gap and one gap repair for the big card of the source stage', () => {
    const sizes = { R: { width: 120, height: 60 }, A: { width: 300, height: 200 }, C: { width: 250, height: 150 } }
    const g = graph([R(), card('A', 'A'), card('B', 'B'), card('C', 'C')], [['R', 'A'], ['R', 'B'], ['A', 'C'], ['B', 'C']])
    const h = lay(g, 'horizontal', sizes)
    expect(h.at).toEqual({ R: [0, 0], A: [240, 0], B: [240, 248], C: [708, 0] })
    expect([h.refusal, h.plan.remainingPierces]).toEqual([null, 0])
    const v = lay(g, 'vertical', sizes)
    expect(v.at).toEqual({ R: [0, 0], A: [0, 364], B: [348, 364], C: [0, 734] })
    expect([v.refusal, v.plan.remainingPierces]).toEqual([null, 0])
  })

  it('L3: the same result whether the cards still have tmp_ keys or their stored keys (the barycentre of the predecessors)', () => {
    const make = (a: string, b: string) =>
      graph(
        [card('R1', 'R', { root: true, seq: 1 }), card('R2', 'R', { root: true, seq: 2 }), card(a, 'T'), card(b, 'T')],
        [['R1', a], ['R1', b], ['R2', b]],
      )
    for (const [a, b] of [['tmp_1', 'tmp_2'], ['node_z', 'node_a']]) {
      const h = lay(make(a, b), 'horizontal')
      expect(h.at).toEqual({ R1: [0, 0], R2: [0, 140], [a]: [260, 0], [b]: [260, 140] })
      const v = lay(make(a, b), 'vertical')
      expect(v.at).toEqual({ R1: [0, 0], R2: [224, 0], [a]: [0, 228], [b]: [224, 228] })
      expect([h.refusal, v.refusal]).toEqual([null, null])
    }
  })

  it('L4: Documents — the draft card layers, then the isolated draft cards, then the free documents', () => {
    const g = graph(
      [doc('D1', 'D1', 1), doc('D2', 'D2', 2), R(), card('A', 'A'), card('P', 'P')],
      [['R', 'A']],
      { projection: 'documents' },
    )
    expect(lay(g, 'horizontal').at).toEqual({ R: [0, 0], A: [260, 0], P: [0, 200], D1: [0, 400], D2: [260, 400] })
    expect(lay(g, 'vertical').at).toEqual({ R: [0, 0], A: [0, 176], P: [284, 0], D1: [568, 0], D2: [568, 176] })
  })



  it('L5: a short branch merging — no line through a card, every line on the direction sides', () => {
    const h = lay(shortMerge(), 'horizontal')
    expect(h.at).toEqual({ R: [0, 0], A: [260, 140], B: [520, 0] })
    const v = lay(shortMerge(), 'vertical')
    expect(v.at).toEqual({ R: [0, 0], A: [224, 228], B: [0, 456] })
    for (const run of [h, v]) expect([run.refusal, run.plan.remainingPierces]).toEqual([null, 0])
  })

  it('L6: an edge skipping two stages', () => {
    const g = graph([R(), card('A', 'A'), card('B', 'B'), card('C', 'C')], [['R', 'A'], ['A', 'B'], ['B', 'C'], ['R', 'C']])
    const h = lay(g, 'horizontal')
    expect(h.at).toEqual({ R: [0, 0], A: [260, 140], B: [520, 140], C: [780, 0] })
    const v = lay(g, 'vertical')
    expect(v.at).toEqual({ R: [0, 0], A: [224, 228], B: [224, 404], C: [0, 632] })
    for (const run of [h, v]) expect([run.refusal, run.plan.remainingPierces]).toEqual([null, 0])
  })



  it('L7: branch -> merge, the isolated card apart (below / to the right)', () => {
    expect(lay(branchMerge(), 'horizontal').at).toEqual({
      R: [0, 0], NA: [260, 0], NB: [260, 140], TA: [520, 0], TB: [520, 140], Z: [780, 0], P: [0, 340],
    })
    expect(lay(branchMerge(), 'vertical').at).toEqual({
      R: [0, 0], NA: [0, 228], NB: [224, 228], TA: [0, 404], TB: [224, 404], Z: [0, 632], P: [508, 0],
    })
  })

  const fiveBranches = () =>
    graph(
      [R(), ...[0, 1, 2, 3, 4].map((i) => card(`B${i}`, `B${i}`)), card('Z', 'Z')],
      [...[0, 1, 2, 3, 4].map((i): [string, string] => ['R', `B${i}`]), ...[0, 1, 2, 3, 4].map((i): [string, string] => [`B${i}`, 'Z'])],
    )

  it('L8: five branches and their merge — the farthest branch faces the direction too', () => {
    const h = lay(fiveBranches(), 'horizontal')
    expect(h.at).toEqual({ R: [0, 0], B0: [564, 0], B1: [564, 140], B2: [564, 280], B3: [564, 420], B4: [564, 560], Z: [1128, 0] })
    const v = lay(fiveBranches(), 'vertical')
    expect(v.at).toEqual({ R: [0, 0], B0: [0, 900], B1: [224, 900], B2: [448, 900], B3: [672, 900], B4: [896, 900], Z: [0, 1800] })
    expect(sidesOf(applied(fiveBranches(), h.at)).every((side) => side === 'right')).toBe(true)
    expect(sidesOf(applied(fiveBranches(), v.at)).every((side) => side === 'bottom')).toBe(true)
    expect([h.refusal, v.refusal]).toEqual([null, null])
  })

  it('L9: a fixed start card is the reference point', () => {
    const g = graph([card('R', 'R', { root: true, movable: false, at: [300, 500] }), card('A', 'A'), card('B', 'B')], [['R', 'A'], ['A', 'B']])
    expect(lay(g, 'horizontal').at).toEqual({ A: [560, 500], B: [820, 500] })
    expect(lay(g, 'vertical').at).toEqual({ A: [300, 676], B: [300, 852] })
  })



  it('L10: a fixed card is passed by moving the whole stage along the flow axis', () => {
    const g = withObstacle(card('O', 'O', { movable: false, at: [250, -10] }))
    const h = lay(g, 'horizontal')
    expect(h.at).toEqual({ R: [0, 0], A: [450, 0], B: [710, 0] })
    const v = lay(g, 'vertical')
    expect(v.at).toEqual({ R: [0, 0], A: [0, 176], B: [0, 352] })
    expect([h.refusal, v.refusal]).toEqual([null, null])
  })

  it('L11: a complete DAG keeps the crossings it cannot avoid — the candidate is still taken (V5 passes)', () => {
    const g = graph(
      [R(), card('A', 'A'), card('B', 'B'), card('C', 'C')],
      [['R', 'A'], ['R', 'B'], ['R', 'C'], ['A', 'B'], ['A', 'C'], ['B', 'C']],
    )
    const h = lay(g, 'horizontal')
    expect(h.at).toEqual({ R: [0, 0], A: [260, 140], B: [520, 0], C: [780, 0] })
    const v = lay(g, 'vertical')
    expect(v.at).toEqual({ R: [0, 0], A: [224, 228], B: [0, 456], C: [0, 632] })
    for (const run of [h, v]) expect([run.refusal, run.plan.remainingPierces]).toEqual([null, 2])
  })

  it('L12: laying L5 out twice gives the same coordinates (also from the laid-out positions)', () => {
    for (const dir of ['horizontal', 'vertical'] as const) {
      const first = lay(shortMerge(), dir).at
      expect(lay(shortMerge(), dir).at).toEqual(first)
      expect(lay(applied(shortMerge(), first), dir).at).toEqual(first)
    }
  })

  it('L13: a candidate that skipped the pierce repair is refused (V5 pierce)', () => {
    const h = lay(shortMerge(), 'horizontal')
    expect(checkDirectionalLayout(h.plan, [{ nodeId: 'R', x: 0, y: 0 }, { nodeId: 'A', x: 260, y: 0 }, { nodeId: 'B', x: 520, y: 0 }])).toBe('pierce')
    const v = lay(shortMerge(), 'vertical')
    expect(checkDirectionalLayout(v.plan, [{ nodeId: 'R', x: 0, y: 0 }, { nodeId: 'A', x: 0, y: 176 }, { nodeId: 'B', x: 0, y: 352 }])).toBe('pierce')
  })

  /** R -> A -> B shown; O a collapsed (left-out) movable document of the whole Flow at (250, -10). */
  function collapsedObstacle() {
    const shown = graph([R(), card('A', 'A'), card('B', 'B')], [['R', 'A'], ['A', 'B']], { hiddenNodeCount: 1 })
    const whole = { ...shown, hiddenNodeCount: 0, nodes: [...shown.nodes, doc('O', 'O', 1, { at: [250, -10] })] }
    return { shown, basis: { whole, sequenceRoot: null } as FlowLayoutBasis }
  }

  it('L14: a collapsed movable card is where it really is: an obstacle, never moved', () => {
    const { shown, basis } = collapsedObstacle()
    const h = lay(shown, 'horizontal', {}, basis)
    expect(h.at).toEqual({ R: [0, 0], A: [450, 0], B: [710, 0] })
    const v = lay(shown, 'vertical', {}, basis)
    expect(v.at).toEqual({ R: [0, 0], A: [0, 176], B: [0, 352] })
    expect([h.refusal, v.refusal]).toEqual([null, null])
  })

  it('L15: a candidate over the collapsed card\'s real place is refused (V3 overlap)', () => {
    const { shown, basis } = collapsedObstacle()
    for (const [dir, a] of [['horizontal', [260, 0]], ['vertical', [300, 30]]] as const) {
      const run = lay(shown, dir, {}, basis)
      const forced = run.plan.candidate.map((p) => (p.nodeId === 'A' ? { nodeId: 'A', x: a[0], y: a[1] } : p))
      expect(checkDirectionalLayout(run.plan, forced)).toBe('overlap')
    }
  })
})

describe('0004-D §7 B — 자동정렬 cases (flowgate.v02.0068 T#2)', () => {
  it('a cycle is laid out by the fallback rule; its way back is not judged, the Graph is not changed', () => {
    const g = graph([R(), card('A', 'A'), card('B', 'B')], [['R', 'A'], ['A', 'B'], ['B', 'A']])
    const edgesBefore = JSON.stringify(g.edges)
    const h = lay(g, 'horizontal')
    expect(h.at).toEqual({ R: [0, 0], A: [260, 0], B: [520, 0] })
    expect(lay(g, 'vertical').at).toEqual({ R: [0, 0], A: [0, 176], B: [0, 352] })
    expect(h.refusal).toBeNull()
    expect(JSON.stringify(g.edges)).toBe(edgesBefore)
  })

  it('a lone sequence card is a chain at the top left; the documents go below / to the right', () => {
    const slot: FlowGraphNode = {
      id: 'slot_1', kind: 'task', title: 'T#1', x: 900, y: 900, movable: true,
      slot: { itemId: 1, itemSeq: 1, order: 1, type: 'T', label: 'T#1', derivedStatus: null, protected: false, sourceWpCardId: null },
    }
    const g = graph([slot, doc('D', 'D', 2)], [], { projection: 'sequence' })
    expect(lay(g, 'horizontal').at).toEqual({ slot_1: [0, 0], D: [0, 200] })
    expect(lay(g, 'vertical').at).toEqual({ slot_1: [0, 0], D: [284, 0] })
  })

  it('several groups: the group with the root start first, the next one as its own band', () => {
    const g = graph(
      [card('X', 'A'), card('Y', 'B'), R('R1'), card('S', 'S')],
      [['X', 'Y'], ['R1', 'S']],
    )
    expect(lay(g, 'horizontal').at).toEqual({ R1: [0, 0], S: [260, 0], X: [0, 200], Y: [260, 200] })
    expect(lay(g, 'vertical').at).toEqual({ R1: [0, 0], S: [0, 176], X: [284, 0], Y: [284, 176] })
  })

  it('every target edge faces the direction — branch -> merge, short branches, obstacles, real sizes', () => {
    const cases: Array<[FlowGraph, Record<string, Size>]> = [
      [branchMerge(), {}],
      [shortMerge(), {}],
      [withObstacle(card('O', 'O', { movable: false, at: [250, -10] })), {}],
      [graph([R(), card('A', 'A'), card('B', 'B'), card('C', 'C')], [['R', 'A'], ['R', 'B'], ['A', 'C'], ['B', 'C']]),
        { R: { width: 120, height: 60 }, A: { width: 300, height: 200 }, C: { width: 250, height: 150 } }],
    ]
    for (const [g, sizes] of cases) {
      for (const [dir, side] of [['horizontal', 'right'], ['vertical', 'bottom']] as const) {
        const run = lay(g, dir, sizes)
        expect(run.refusal).toBeNull()
        const after = applied(g, run.at)
        const judged = g.edges.filter((e) => run.at[e.source] && run.at[e.target]).map((e) => `${facingSide(rectOf(after, e.source, sizes), rectOf(after, e.target, sizes))}`)
        expect(judged.every((s) => s === side)).toBe(true)
      }
    }
  })

  it('a forced candidate with a line on the wrong sides is refused (V4 direction)', () => {
    const run = lay(graph([R(), card('A', 'A')], [['R', 'A']]), 'vertical')
    expect(checkDirectionalLayout(run.plan, [{ nodeId: 'R', x: 0, y: 0 }, { nodeId: 'A', x: 400, y: 100 }])).toBe('direction')
  })

  it('V1 / V2: a missing, duplicated, unknown or invalid position is refused', () => {
    const run = lay(shortMerge(), 'horizontal')
    const [r, a, b] = run.plan.candidate
    expect(checkDirectionalLayout(run.plan, [r, a])).toBe('missing')
    expect(checkDirectionalLayout(run.plan, [r, a, b, a])).toBe('duplicate')
    expect(checkDirectionalLayout(run.plan, [r, a, { nodeId: 'nope', x: 0, y: 0 }])).toBe('missing')
    expect(checkDirectionalLayout(run.plan, [r, a, { ...b, x: Number.NaN }])).toBe('invalid_coordinate')
    expect(checkDirectionalLayout(run.plan, [r, a, { ...b, x: 10_000_001 }])).toBe('invalid_coordinate')
  })

  it('no movable shown card: no candidate', () => {
    const g = graph([card('R', 'R', { root: true, movable: false, at: [0, 0] })], [])
    expect(lay(g, 'horizontal').plan.candidate).toEqual([])
  })

  it('same positions, same sides: the Canvas (edgeAnchors) and the MiniMap draw each line from the same points', () => {
    for (const dir of ['horizontal', 'vertical'] as const) {
      const after = applied(branchMerge(), lay(branchMerge(), dir).at)
      const lines = miniMapEdges(after, new Set(after.edges.map((e) => e.id)))
      const canvas = after.edges.map((e) => {
        const a = edgeAnchors(rectOf(after, e.source), rectOf(after, e.target))
        return { id: e.id, x1: a.sourceX, y1: a.sourceY, x2: a.targetX, y2: a.targetY }
      })
      expect(lines).toEqual(canvas)
      // Vertical: from the bottom middle of the source to the top middle of the target.
      if (dir === 'vertical') expect(lines[0]).toEqual({ id: 'e0', x1: 88, y1: 92, x2: 88, y2: 228 })
    }
  })
})
