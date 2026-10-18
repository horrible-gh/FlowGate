// flowgate.v02.0068 T#2 (0005-L §2.4 ~ §2.6) — coordinates by direction. A card sits on the start side of its lane
// and of its stage (horizontal: top / left, vertical: left / top); each stage starts far enough after the one
// before it for the card sizes (G_FLOW) and for every forward edge into it to face the direction:
// flow distance of the centres ≥ |lane distance of the centres| + DIR_MARGIN, so the unchanged facingSide gives
// Right → Left (horizontal) or Bottom → Top (vertical). The axis table, not a rotation, makes the two directions.
// A group is a band (horizontal: stacked downward, vertical: side by side to the right); a card that does not move
// (fixed, or a collapsed card where it really is) is an obstacle a stage passes by moving along the flow axis as a
// whole, so no lane distance changes. The pierce repair then moves a card one lane on, or widens a stage gap, while
// that takes pierced cards away. The unconnected areas come after every band. Pure.
import { NODE_CARD_HEIGHT, NODE_CARD_WIDTH, type NodeRect } from '../canvas/edgeGeometry'
import { curvePierces, edgeCurve } from './edgeCurve'
import type { LayoutDirection, LayoutInput, LayoutNode } from './layoutInput'
import { lanesOf, stageNodes, type LayoutComponent } from './layoutLanes'

export const G_FLOW = 84
export const G_LANE = 48
export const BAND_GAP = 108
export const DIR_MARGIN = 4
export const OBSTACLE_GAP = 24
export const GAP_STEP = G_FLOW
export const REPAIR_MAX_STEPS = 64

// --- axes (0005-L §1.1) -------------------------------------------------------------------------------------------

export const fstart = (dir: LayoutDirection, r: NodeRect) => (dir === 'horizontal' ? r.x : r.y)
export const fend = (dir: LayoutDirection, r: NodeRect) => (dir === 'horizontal' ? r.x + r.width : r.y + r.height)
export const cstart = (dir: LayoutDirection, r: NodeRect) => (dir === 'horizontal' ? r.y : r.x)
export const cend = (dir: LayoutDirection, r: NodeRect) => (dir === 'horizontal' ? r.y + r.height : r.x + r.width)

function rectAt(dir: LayoutDirection, flow: number, cross: number, v: LayoutNode): NodeRect {
  const [x, y] = dir === 'horizontal' ? [flow, cross] : [cross, flow]
  return { x: Math.round(x), y: Math.round(y), width: v.width, height: v.height }
}

/** Overlap of two rectangles; touching is not overlap. */
export const overlaps = (a: NodeRect, b: NodeRect) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

const expand = (r: NodeRect, by: number): NodeRect => ({ x: r.x - by, y: r.y - by, width: r.width + 2 * by, height: r.height + 2 * by })

/** The card that really moves: shown and movable. */
export const placeable = (v: LayoutNode) => v.shown && v.movable

// --- 0005-L §2.4 local geometry -----------------------------------------------------------------------------------

interface LocalGeometry {
  readonly cs0: ReadonlyMap<string, number>
  readonly stageOff: readonly number[]
}

function localGeometry(input: LayoutInput, comp: LayoutComponent, lane: ReadonlyMap<string, number>, extra: ReadonlyMap<number, number>): LocalGeometry {
  const node = (id: string) => input.byId.get(id)!
  const s = (id: string) => comp.stage.get(id)!
  const lanes = Math.max(0, ...lane.values()) + 1
  const laneSize = Array.from({ length: lanes }, () => 0)
  for (const v of comp.nodes) laneSize[lane.get(v.id)!] = Math.max(laneSize[lane.get(v.id)!], v.crossSize)
  const laneOff = [0]
  for (let k = 0; k < lanes; k += 1) laneOff.push(laneOff[k] + (laneSize[k] > 0 ? laneSize[k] + G_LANE : 0))
  const cs0 = new Map(comp.nodes.map((v) => [v.id, laneOff[lane.get(v.id)!]] as const))
  const cc = (id: string) => cs0.get(id)! + node(id).crossSize / 2
  const defaultFlow = input.direction === 'horizontal' ? NODE_CARD_WIDTH : NODE_CARD_HEIGHT
  const thickness = (st: number) => {
    const sizes = stageNodes(comp, st).map((v) => v.flowSize)
    return sizes.length ? Math.max(...sizes) : defaultFlow
  }
  const stageOff = [0]
  for (let st = 1; st <= comp.maxStage; st += 1) {
    const needSize = stageOff[st - 1] + thickness(st - 1) + G_FLOW
    let needDir = -Infinity
    for (const [u, v] of comp.forward) {
      if (s(v) !== st) continue
      needDir = Math.max(needDir, stageOff[s(u)] + node(u).flowSize / 2 - node(v).flowSize / 2 + Math.abs(cc(v) - cc(u)) + DIR_MARGIN)
    }
    stageOff.push(Math.ceil(Math.max(needSize, needDir)) + (extra.get(st) ?? 0))
  }
  return { cs0, stageOff }
}

// --- 0005-L §2.5 band placement and obstacle avoidance --------------------------------------------------------------

export interface BandPlacement {
  /** New rectangles of the band's cards that move. */
  readonly placed: ReadonlyMap<string, NodeRect>
  readonly lane: ReadonlyMap<string, number>
  readonly flowOrigin: number
}

export interface BandContext {
  readonly input: LayoutInput
  readonly comp: LayoutComponent
  readonly compare: (a: string, b: string) => number
  readonly obstacles: readonly NodeRect[]
  readonly flow0: number
  readonly crossCursor: number
  readonly isMain: boolean
}

function placeBand(ctx: BandContext, floors: ReadonlyMap<string, number>, extra: ReadonlyMap<number, number>): BandPlacement {
  const { input, comp, obstacles } = ctx
  const dir = input.direction
  const lane = lanesOf(comp, ctx.compare, floors)
  const { cs0, stageOff } = localGeometry(input, comp, lane, extra)
  const s = (id: string) => comp.stage.get(id)!
  const main = comp.nodes.find((v) => s(v.id) === 0 && lane.get(v.id) === 0)!
  let flowOrigin = ctx.flow0
  let crossOrigin = ctx.crossCursor
  if (ctx.isMain && !main.movable) {
    // A fixed start card is the band's reference point (the movable contract comes first).
    flowOrigin = fstart(dir, main.rect0)
    crossOrigin = cstart(dir, main.rect0) - cs0.get(main.id)!
  }
  const shift = Array.from({ length: comp.maxStage + 1 }, () => 0)
  const rectOf = (v: LayoutNode) => rectAt(dir, flowOrigin + stageOff[s(v.id)] + shift[s(v.id)], crossOrigin + cs0.get(v.id)!, v)
  for (let st = 0; st <= comp.maxStage; st += 1) {
    const moving = stageNodes(comp, st).filter(placeable)
    for (let tries = 0; tries <= obstacles.length; tries += 1) {
      const hits = obstacles.filter((o) => moving.some((v) => overlaps(rectOf(v), expand(o, OBSTACLE_GAP))))
      if (hits.length === 0) break
      const delta = Math.max(...hits.map((o) => fend(dir, o))) + OBSTACLE_GAP - (flowOrigin + stageOff[st] + shift[st])
      for (let j = st; j <= comp.maxStage; j += 1) shift[j] += delta
    }
  }
  const placed = new Map(comp.nodes.filter(placeable).map((v) => [v.id, rectOf(v)] as const))
  return { placed, lane, flowOrigin }
}

// --- 0005-L §2.5a pierce judgement and repair -----------------------------------------------------------------------

/** One pierce: the forward edge source -> target passes through `card`. */
export interface Pierce {
  readonly source: string
  readonly target: string
  readonly card: string
}

export const pierceKey = (p: Pierce) => `${p.source}\u0000${p.target}\u0000${p.card}`

/** P(c): every forward edge between shown cards that passes through another shown card of the group. */
export function piercesOf(input: LayoutInput, comp: LayoutComponent, rectOf: (v: LayoutNode) => NodeRect): Pierce[] {
  const shown = comp.nodes.filter((v) => v.shown)
  const found: Pierce[] = []
  for (const [source, target] of comp.forward) {
    const u = input.byId.get(source)!
    const v = input.byId.get(target)!
    if (!u.shown || !v.shown) continue
    const curve = edgeCurve(rectOf(u), rectOf(v))
    for (const w of shown) {
      if (w.id !== source && w.id !== target && curvePierces(curve, rectOf(w))) found.push({ source, target, card: w.id })
    }
  }
  return found
}

export interface RepairedBand extends BandPlacement {
  /** Pierces still there after the repair. */
  readonly pierces: readonly Pierce[]
  /** Pierces the repair tried (and the ones left at its end): V5 lets only these stay. */
  readonly tried: ReadonlySet<string>
}

export function repairBand(ctx: BandContext): RepairedBand {
  const { input, comp, compare } = ctx
  const s = (id: string) => comp.stage.get(id)!
  const rectsWith = (placed: ReadonlyMap<string, NodeRect>) => (v: LayoutNode) => placed.get(v.id) ?? v.rect0
  let floors = new Map<string, number>()
  let extra = new Map<number, number>()
  let band = placeBand(ctx, floors, extra)
  let pierces = piercesOf(input, comp, rectsWith(band.placed))
  const tried = new Set<string>()
  for (let steps = 0; steps < REPAIR_MAX_STEPS; ) {
    const lane = band.lane
    const cand = pierces
      .filter((p) => !tried.has(pierceKey(p)))
      .sort((a, b) => s(a.card) - s(b.card) || lane.get(a.card)! - lane.get(b.card)! || compare(a.card, b.card) || compare(a.source, b.source) || compare(a.target, b.target))
    if (cand.length === 0) break
    const p = cand[0]
    steps += 1
    let nextFloors = floors
    let nextExtra = extra
    if (s(p.source) < s(p.card) && s(p.card) < s(p.target)) {
      // An edge skipping stages passes a card between: that card one lane down / right.
      if (!placeable(input.byId.get(p.card)!)) {
        tried.add(pierceKey(p))
        continue
      }
      nextFloors = new Map(floors).set(p.card, lane.get(p.card)! + 1)
    } else if (s(p.card) === s(p.source)) {
      nextExtra = new Map(extra).set(s(p.source) + 1, (extra.get(s(p.source) + 1) ?? 0) + GAP_STEP)
    } else if (s(p.card) === s(p.target)) {
      nextExtra = new Map(extra).set(s(p.target), (extra.get(s(p.target)) ?? 0) + GAP_STEP)
    } else {
      tried.add(pierceKey(p))
      continue
    }
    const trial = placeBand(ctx, nextFloors, nextExtra)
    const trialPierces = piercesOf(input, comp, rectsWith(trial.placed))
    if (trialPierces.length < pierces.length) {
      floors = nextFloors
      extra = nextExtra
      band = trial
      pierces = trialPierces
    } else tried.add(pierceKey(p))
  }
  for (const p of pierces) tried.add(pierceKey(p))
  return { ...band, pierces, tried }
}

// --- bands and unconnected areas (0005-L §2.5 loop, §2.6) -----------------------------------------------------------

export interface LaidOut {
  /** The new rectangle of every card that moves. */
  readonly placed: ReadonlyMap<string, NodeRect>
  /** The repaired bands, by group. */
  readonly bands: ReadonlyMap<LayoutComponent, RepairedBand>
}

export function layOutBands(
  input: LayoutInput,
  components: readonly LayoutComponent[],
  areas: readonly (readonly LayoutNode[])[],
  compare: (a: string, b: string) => number,
): LaidOut {
  const dir = input.direction
  const obstacles = input.nodes.filter((v) => !placeable(v)).map((v) => v.rect0)
  const placed = new Map<string, NodeRect>()
  const bands = new Map<LayoutComponent, RepairedBand>()
  let flow0 = 0
  let crossCursor = 0
  let first = true
  for (const comp of components) {
    if (!comp.nodes.some(placeable)) continue
    const band = repairBand({ input, comp, compare, obstacles, flow0, crossCursor, isMain: first })
    bands.set(comp, band)
    for (const [id, rect] of band.placed) placed.set(id, rect)
    if (first) flow0 = band.flowOrigin
    crossCursor = Math.max(...[...band.placed.values()].map((r) => cend(dir, r))) + BAND_GAP
    first = false
  }
  for (const area of areas) {
    if (area.length === 0) continue
    const pitch = Math.max(...area.map((v) => v.flowSize)) + G_FLOW
    const areaCross = crossCursor
    let slot = 0
    const inArea: NodeRect[] = []
    for (const v of area) {
      if (!v.movable) continue
      let rect = rectAt(dir, flow0 + slot * pitch, areaCross, v)
      while (placeable(v) && obstacles.some((o) => overlaps(rect, expand(o, OBSTACLE_GAP)))) {
        slot += 1
        rect = rectAt(dir, flow0 + slot * pitch, areaCross, v)
      }
      if (placeable(v)) {
        placed.set(v.id, rect)
        inArea.push(rect)
      }
      slot += 1
    }
    if (inArea.length > 0) crossCursor = Math.max(...inArea.map((r) => cend(dir, r))) + BAND_GAP
  }
  return { placed, bands }
}
