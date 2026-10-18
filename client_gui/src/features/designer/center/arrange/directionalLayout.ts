// flowgate.v02.0068 T#2 (0005-L §2.7 / §2.8) — 가로 / 세로 자동정렬 of a shown Graph: the input (layoutInput.ts), the
// groups' order, stages and lanes (layoutLanes.ts), the bands with obstacle avoidance and pierce repair and the
// unconnected areas (layoutBands.ts) give a candidate — the new position of every card that moves (shown and
// movable). The candidate is checked whole before anything moves; one failed check refuses all of it:
// V1 the cards that move, once each — V2 valid positions — V3 no overlap with another card (a card that does not
// move counts where it really is, a collapsed card too) — V4 every target edge, judged by the unchanged facingSide
// at the real sizes, faces the direction (Right → Left / Bottom → Top) — V5 no pierce the repair did not try.
// Same Graph, same direction, same sizes: the same result, whatever the moving cards' positions and node ids.
// The former 자동 배치 (autoLayout.ts layout / autoLayoutPlacements, the server's rules) stays for the server
// match tests. Pure: no Vue, no Vue Flow.
import { facingSide, type NodeRect } from '../canvas/edgeGeometry'
import type { NodePlacement } from '../edit/editHistory'
import type { FlowGraph } from '../graph/flowGraph'
import type { FlowLayoutBasis } from './autoLayout'
import { fend, fstart, layOutBands, overlaps, pierceKey, piercesOf, placeable, type RepairedBand } from './layoutBands'
import { buildLayoutInput, type LayoutDirection, type LayoutInput, type LayoutNode, type NodeSizeOf } from './layoutInput'
import { orderLayout, type LayoutComponent } from './layoutLanes'

export const COORD_LIMIT = 10_000_000

/** Why a candidate was refused (0005-L §2.7). */
export type LayoutRefusal = 'missing' | 'duplicate' | 'invalid_coordinate' | 'overlap' | 'direction' | 'pierce'

export interface DirectionalLayoutPlan {
  readonly input: LayoutInput
  readonly graph: FlowGraph
  readonly components: readonly LayoutComponent[]
  /** The groups laid out as bands (a group with no card that moves is only an obstacle). */
  readonly bands: ReadonlyMap<LayoutComponent, RepairedBand>
  /** The new position of every card that moves, in the shown Graph's order. */
  readonly candidate: readonly NodePlacement[]
  /** Pierces the repair could not take away (an unavoidable one is kept: 0005-L §2.5a). */
  readonly remainingPierces: number
}

/** 0005-L §2.8 up to the candidate. `sizeOf`: the Canvas's measured card sizes at the command. */
export function planDirectionalLayout(
  graph: FlowGraph,
  basis: FlowLayoutBasis | undefined,
  direction: LayoutDirection,
  sizeOf?: NodeSizeOf,
): DirectionalLayoutPlan {
  const input = buildLayoutInput(graph, basis, direction, sizeOf)
  const { components, areas, compare } = orderLayout(input)
  const { placed, bands } = layOutBands(input, components, areas, compare)
  const candidate = graph.nodes.flatMap((node) => {
    const rect = placed.get(node.id)
    return rect ? [{ nodeId: node.id, x: rect.x, y: rect.y }] : []
  })
  const remainingPierces = [...bands.values()].reduce((sum, band) => sum + band.pierces.length, 0)
  return { input, graph, components, bands, candidate, remainingPierces }
}

/** 0005-L §2.7 V1 ~ V5 on a candidate (the plan's own by default); null when it may be applied. */
export function checkDirectionalLayout(plan: DirectionalLayoutPlan, candidate: readonly NodePlacement[] = plan.candidate): LayoutRefusal | null {
  const { input } = plan
  const dir = input.direction
  // V1: the cards that move, each once, no other.
  const moving = new Set(input.nodes.filter(placeable).map((v) => v.id))
  const seen = new Set<string>()
  for (const p of candidate) {
    if (seen.has(p.nodeId)) return 'duplicate'
    seen.add(p.nodeId)
    if (!moving.has(p.nodeId)) return 'missing'
  }
  if (seen.size !== moving.size) return 'missing'
  // V2: finite and inside COORD_LIMIT.
  if (candidate.some((p) => !Number.isFinite(p.x) || !Number.isFinite(p.y) || Math.abs(p.x) > COORD_LIMIT || Math.abs(p.y) > COORD_LIMIT)) {
    return 'invalid_coordinate'
  }
  const at = new Map(candidate.map((p) => [p.nodeId, p] as const))
  const rectOf = (v: LayoutNode): NodeRect => {
    const p = at.get(v.id)
    return p && placeable(v) ? { x: p.x, y: p.y, width: v.width, height: v.height } : v.rect0
  }
  // V3: no overlap of a moved card with any other card (sweep along the flow axis).
  const boxes = input.nodes
    .map((v) => ({ rect: rectOf(v), moved: placeable(v) }))
    .sort((a, b) => fstart(dir, a.rect) - fstart(dir, b.rect))
  let active: typeof boxes = []
  for (const box of boxes) {
    active = active.filter((other) => fend(dir, other.rect) > fstart(dir, box.rect))
    if (active.some((other) => (other.moved || box.moved) && overlaps(other.rect, box.rect))) return 'overlap'
    active.push(box)
  }
  // V4: every target edge faces the direction.
  const groupOf = new Map<string, LayoutComponent>()
  for (const comp of plan.bands.keys()) for (const v of comp.nodes) groupOf.set(v.id, comp)
  const expected = dir === 'horizontal' ? 'right' : 'bottom'
  for (const edge of plan.graph.edges) {
    if (edge.edgeClass !== 'execution') continue
    const u = input.byId.get(edge.source)
    const v = input.byId.get(edge.target)
    const comp = groupOf.get(edge.source)
    if (!u || !v || !comp || groupOf.get(edge.target) !== comp || !placeable(u) || !placeable(v)) continue
    if (comp.stage.get(v.id)! <= comp.stage.get(u.id)!) continue
    if (`${facingSide(rectOf(u), rectOf(v))}` !== expected) return 'direction'
  }
  // V5: a pierce left on the candidate is one the repair tried.
  for (const [comp, band] of plan.bands) {
    if (piercesOf(input, comp, rectOf).some((p) => !band.tried.has(pierceKey(p)))) return 'pierce'
  }
  return null
}
