// flowgate.v02.0059 T#2 (0011-CH contract D) — 자동 배치: the server's auto placement
// (server/modules/flow_gate/graph_workflow/domain/auto_layout.py, L0009 §1.3 / §2.7) ported as it is —
// the same constants, layering by depth, collision rule and projections — so the same input gives the
// same coordinates. No new rule: the one addition is `scale` (1 = the server's spacing), which multiplies
// every spacing — column step, stack step, row offsets and collision step; the collision box is the card's
// footprint and stays. flowgate.v02.0059 0017-T: the Designer's 자동 배치 uses 1 — the density is a drag
// step (top/designerTop.ts DENSITY_MOVE_STEP), not this scale.
//
// For a shown Graph (`autoLayoutPlacements`) the input is what the server's projection holds: Graph /
// draft nodes (those with a definition) laid out in layers along the execution edges, the other cards
// as free documents (in Graph order, the server's document order), and for the `sequence` projection the
// chain — the root node and every slot node, as legacy_projector puts them (a lone root or a lone slot is
// a chain too, edges or not). Every movable card is placed again; a card that is not movable keeps its
// place and counts as a saved position (the server's "a saved coordinate always wins").
// The shown Graph may lack part of that input: the documents the Canvas read left out (collapse — the
// server lays the whole projection out first, then hides them) and, for a sequence root that replaced an
// older root document, which node the root is. Then the input comes from a `FlowLayoutBasis` — the Flow
// read whole (no collapse) and the root document's supersede chain — and the left-out documents keep their
// place in the order and in the collision check while only the shown cards move.
// Pure apart from `loadLayoutBasis`, which only calls the reads it is given: no Vue, no Vue Flow.
import type { NodePlacement } from '../edit/editHistory'
import type { FlowGraph } from '../graph/flowGraph'
import type { FlowDocumentLink } from '../graph/flowGraphSource'

export const AUTO_X_STEP = 260
export const AUTO_STACK_DY = 140
export const FREE_DOC_ROW_Y_SEQUENCE = 180
export const DRAFT_ROW_Y_DOCUMENTS = 320
export const FREE_DOC_ROW_GAP_GRAPH = 200
export const COLLISION_BOX_W = 200
export const COLLISION_BOX_H = 100
export const COLLISION_DY = 120
export const COLLISION_MAX_TRIES = 20

export interface PlacedPosition {
  readonly x: number
  readonly y: number
  /** 'saved': kept as given; 'auto': placed here. */
  readonly source: 'saved' | 'auto'
}

/** auto_layout.layout's keyword arguments. */
export interface AutoLayoutInput {
  readonly chain?: readonly string[]
  readonly freeDocs?: readonly string[]
  readonly others?: readonly string[]
  readonly graphNodes?: readonly string[]
  readonly graphEdges?: readonly (readonly [string, string])[]
  readonly saved?: ReadonlyMap<string, { readonly x: number; readonly y: number }>
}

/** Python's sorted() order of keys. */
const byKey = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)
const sortedKeys = (keys: Iterable<string>) => [...keys].sort(byKey)

function createPlacer(keys: readonly string[], saved: AutoLayoutInput['saved'], scale: number) {
  const placed = new Map<string, PlacedPosition>()
  for (const key of keys) {
    const position = saved?.get(key)
    if (position) placed.set(key, { x: position.x, y: position.y, source: 'saved' })
  }
  const collides = (x: number, y: number) =>
    [...placed.values()].some((p) => Math.abs(p.x - x) < COLLISION_BOX_W && Math.abs(p.y - y) < COLLISION_BOX_H)
  function auto(key: string, x: number, y: number) {
    if (placed.has(key)) return
    let tries = 0
    while (tries < COLLISION_MAX_TRIES && collides(x, y)) {
      y += COLLISION_DY * scale
      tries += 1
    }
    placed.set(key, { x, y, source: 'auto' })
  }
  return { placed, auto }
}

/**
 * L0009 §2.7 `layered` depth: 0 for start nodes, else 1 + max depth of predecessors. The acyclic part is
 * layered by longest path; a node in a cycle keeps the depth of its first visit from a layered
 * predecessor (breadth-first, key order); one no layered node reaches takes depth 0.
 */
export function layeredDepths(keys: readonly string[], edges: Iterable<readonly [string, string]>): Map<string, number> {
  const keySet = new Set(keys)
  const succ = new Map<string, string[]>(keys.map((k) => [k, []]))
  const indeg = new Map<string, number>(keys.map((k) => [k, 0]))
  for (const [s, t] of edges) {
    if (keySet.has(s) && keySet.has(t)) {
      succ.get(s)!.push(t)
      indeg.set(t, indeg.get(t)! + 1)
    }
  }
  const depth = new Map<string, number>()
  const remaining = new Map(indeg)
  const ready = sortedKeys(keys.filter((k) => indeg.get(k) === 0))
  for (const k of ready) depth.set(k, 0)
  const order: string[] = []
  while (ready.length) {
    const cur = ready.shift()!
    order.push(cur)
    for (const nxt of succ.get(cur)!) {
      depth.set(nxt, Math.max(depth.get(nxt) ?? 0, depth.get(cur)! + 1))
      remaining.set(nxt, remaining.get(nxt)! - 1)
      if (remaining.get(nxt) === 0) ready.push(nxt)
    }
    ready.sort(byKey)
  }
  const done = new Set(order)
  const visit = (queue: string[], from: number) => {
    for (let i = from; i < queue.length; i += 1) {
      const cur = queue[i]
      for (const nxt of sortedKeys(succ.get(cur)!)) {
        if (done.has(nxt)) continue
        done.add(nxt)
        depth.set(nxt, depth.get(cur)! + 1)
        queue.push(nxt)
      }
    }
  }
  visit(sortedKeys(done), 0)
  for (const k of sortedKeys(keys)) {
    if (done.has(k)) continue
    depth.set(k, 0)
    done.add(k)
    visit([k], 0)
  }
  return depth
}

function layered(
  placer: ReturnType<typeof createPlacer>,
  keys: readonly string[],
  edges: readonly (readonly [string, string])[],
  originX: number,
  originY: number,
  scale: number,
) {
  const depth = layeredDepths(keys, edges)
  const byDepth = new Map<number, string[]>()
  for (const k of keys) {
    const d = depth.get(k)!
    if (!byDepth.has(d)) byDepth.set(d, [])
    byDepth.get(d)!.push(k)
  }
  for (const d of [...byDepth.keys()].sort((a, b) => a - b)) {
    sortedKeys(byDepth.get(d)!).forEach((key, j) => {
      placer.auto(key, originX + AUTO_X_STEP * scale * d, originY + AUTO_STACK_DY * scale * j)
    })
  }
}

/**
 * auto_layout.layout: the position of every given key (`saved` ones kept), by projection —
 * `documents`: free documents on one row at y=0, draft nodes in layers from (0, 320); `sequence`: the
 * chain each next to the previous node, free documents on y=180 from x=260; `graph`: Graph nodes in
 * layers from (0, 0), free documents on a row 200 below the lowest; anything else: one row by key.
 */
export function layout(projection: string, input: AutoLayoutInput = {}, scale = 1): Map<string, PlacedPosition> {
  const { chain = [], freeDocs = [], others = [], graphNodes = [], graphEdges = [] } = input
  const every = [...chain, ...freeDocs, ...graphNodes, ...others]
  const placer = createPlacer(every, input.saved, scale)
  const step = AUTO_X_STEP * scale
  if (projection === 'documents') {
    freeDocs.forEach((key, i) => placer.auto(key, step * i, 0))
    layered(placer, graphNodes, graphEdges, 0, DRAFT_ROW_Y_DOCUMENTS * scale, scale)
  } else if (projection === 'sequence') {
    let prev: string | null = null
    for (const key of chain) {
      if (prev === null) placer.auto(key, 0, 0)
      else placer.auto(key, placer.placed.get(prev)!.x + step, placer.placed.get(prev)!.y)
      prev = key
    }
    freeDocs.forEach((key, i) => placer.auto(key, step * (i + 1), FREE_DOC_ROW_Y_SEQUENCE * scale))
  } else if (projection === 'graph') {
    layered(placer, graphNodes, graphEdges, 0, 0, scale)
    const bottom = graphNodes.length ? Math.max(...graphNodes.map((k) => placer.placed.get(k)!.y)) : 0
    freeDocs.forEach((key, i) => placer.auto(key, step * i, bottom + FREE_DOC_ROW_GAP_GRAPH * scale))
  } else {
    sortedKeys(every).forEach((key, i) => placer.auto(key, step * i, 0))
  }
  return placer.placed
}

const CANCELLED_STATUS = 'cancelled'

/**
 * The `sequence` projection's root node. legacy_projector draws it first — before the slots and the free
 * documents — as the live end of the sequence root document's supersede chain. With a slot drawn, a first
 * node that is not a slot is the root; with none, the first node is the root when it shows the root
 * document itself. null: no root drawn. undefined: only the supersede chain can tell (the first node
 * replaced an older document, the root document is not drawn).
 */
export function sequenceRootOf(graph: FlowGraph): string | null | undefined {
  const first = graph.nodes[0]
  if (!first || first.slot) return null
  if (graph.nodes.some((node) => node.slot)) return first.id
  const rootDocId = graph.sequenceRootDocId ?? null
  if (!first.document || rootDocId === null) return null
  if (first.document.docId === rootDocId) return first.id
  return first.document.supersedes.length > 0 ? undefined : null
}

/**
 * The server's supersede.live_end from the root document: the first live document following
 * `superseded_by` (a cancelled link, a document of another Flow or a cycle ends it with none). A read that
 * fails is thrown: the input is not guessed.
 */
async function liveEnd(flowId: string, docId: string, read: (docId: string) => Promise<FlowDocumentLink>): Promise<string | null> {
  const seen = new Set<string>()
  for (let id: string | null = docId; id !== null && !seen.has(id); ) {
    seen.add(id)
    const doc = await read(id)
    const cancelled = doc.status === CANCELLED_STATUS
    if (doc.groupId === flowId && !cancelled && !doc.supersededBy) return doc.docId
    if (cancelled || doc.groupId !== flowId) return null
    id = doc.supersededBy
  }
  return null
}

/** What the shown Graph cannot tell by itself (see the file comment). */
export interface FlowLayoutBasis {
  /** The Flow as the server projects it with nothing left out (no collapse), in the server's order. */
  readonly whole: FlowGraph
  /** The `sequence` projection's root node in it, or null (none drawn). */
  readonly sequenceRoot: string | null
}

/** Whether the shown Graph leaves out part of the server's layout input (left-out documents, an unknown root). */
export function needsLayoutBasis(graph: FlowGraph): boolean {
  return (graph.hiddenNodeCount ?? 0) > 0 || (graph.projection === 'sequence' && sequenceRootOf(graph) === undefined)
}

/** Reads the FlowLayoutBasis: the whole Flow and, when it takes it, the root document's supersede chain. */
export async function loadLayoutBasis(
  readWhole: () => Promise<FlowGraph>,
  readDocument?: (docId: string) => Promise<FlowDocumentLink>,
): Promise<FlowLayoutBasis> {
  const whole = await readWhole()
  let root = whole.projection === 'sequence' ? sequenceRootOf(whole) : null
  if (root === undefined) {
    if (!readDocument) throw new Error('the sequence root takes the supersede chain of the root document')
    const first = whole.nodes[0]
    const end = await liveEnd(whole.flowId, whole.sequenceRootDocId!, readDocument)
    root = end !== null && end === first.document?.docId ? first.id : null
  }
  return { whole, sequenceRoot: root }
}

/**
 * What the server's projection of a shown Graph gives auto_layout.layout (cards that cannot move as saved).
 * `basis`: the whole Flow (see FlowLayoutBasis); without it the shown Graph is the whole one.
 */
export function layoutInputOf(graph: FlowGraph, basis?: FlowLayoutBasis): { projection: string; input: AutoLayoutInput } {
  const whole = basis?.whole ?? graph
  const projection = graph.projection ?? whole.projection ?? ''
  const execution = graph.edges.filter((edge) => edge.edgeClass === 'execution')
  const graphNodes = graph.nodes.filter((node) => !!node.definition).map((node) => node.id)
  const root = basis ? basis.sequenceRoot : sequenceRootOf(graph) ?? null
  const slots = whole.nodes.filter((node) => node.slot).map((node) => node.id)
  const chain = projection === 'sequence' ? (root === null ? slots : [root, ...slots]) : []
  const grouped = new Set([...chain, ...graphNodes])
  const shown = new Set(graph.nodes.map((node) => node.id))
  const inWhole = new Set(whole.nodes.map((node) => node.id))
  const leftOut = whole.nodes.filter((node) => !shown.has(node.id))
  return {
    projection,
    input: {
      chain,
      // The server's document order; a document the shown Graph left out keeps its place.
      freeDocs: [...whole.nodes, ...graph.nodes.filter((node) => !inWhole.has(node.id))]
        .filter((node) => !node.definition && !grouped.has(node.id))
        .map((node) => node.id),
      graphNodes,
      graphEdges: execution.map((edge) => [edge.source, edge.target] as const),
      saved: new Map(
        [...graph.nodes, ...leftOut].filter((node) => node.movable !== true).map((node) => [node.id, { x: node.x, y: node.y }] as const),
      ),
    },
  }
}

/** 자동 배치 of a shown Graph at a spacing scale: the new position of every movable card it shows. */
export function autoLayoutPlacements(graph: FlowGraph, scale = 1, basis?: FlowLayoutBasis): NodePlacement[] {
  const { projection, input } = layoutInputOf(graph, basis)
  const placed = layout(projection, input, scale)
  return graph.nodes.flatMap((node) => {
    const at = placed.get(node.id)
    return node.movable === true && at?.source === 'auto' ? [{ nodeId: node.id, x: at.x, y: at.y }] : []
  })
}
