// flowgate.v02.0068 T#2 (0005-L §2.5a) — the curve the Canvas draws for an Edge, as a pure function, and whether
// it pierces a card. FlowEdgePath takes the two cards' facing sides (edgeGeometry edgeAnchors), stops the line
// ARROW_LENGTH before the target side and draws Vue Flow's getBezierPath (curvature BEZIER_CURVATURE) between the
// two points. Vue Flow may be imported inside center/canvas/ only (structure.0026), so the same formula is written
// out here; edgeCurve.0068 pins it against getBezierPath's path on sample cases. edgeGeometry stays as it is.
import { edgeAnchors, type NodeRect } from '../canvas/edgeGeometry'

export const ARROW_LENGTH = 9
export const BEZIER_CURVATURE = 0.25
export const PIERCE_SAMPLES = 32
export const PIERCE_INSET = 1

export interface Point {
  readonly x: number
  readonly y: number
}

/** A cubic Bézier: start, two control points, end. */
export interface EdgeCurve {
  readonly p0: Point
  readonly c1: Point
  readonly c2: Point
  readonly p3: Point
}

type Side = 'left' | 'right' | 'top' | 'bottom'

/** The outward unit vector of a side. */
const OUTWARD: Record<Side, [number, number]> = { left: [-1, 0], right: [1, 0], top: [0, -1], bottom: [0, 1] }

/** getBezierPath's control point: `p` moved out of `side` by half the distance toward `q`, or 0.25·25·√(−d) behind it. */
function control(side: Side, p: Point, q: Point): Point {
  const [ox, oy] = OUTWARD[side]
  const distance = ox !== 0 ? (q.x - p.x) * ox : (q.y - p.y) * oy
  const offset = distance >= 0 ? distance / 2 : BEZIER_CURVATURE * 25 * Math.sqrt(-distance)
  return { x: p.x + ox * offset, y: p.y + oy * offset }
}

/** The Edge source -> target as the Canvas draws it (0005-L §2.5a edge_curve). */
export function edgeCurve(source: NodeRect, target: NodeRect): EdgeCurve {
  const a = edgeAnchors(source, target)
  const from = `${a.sourcePosition}` as Side
  const to = `${a.targetPosition}` as Side
  const [tx, ty] = OUTWARD[to]
  const p0 = { x: a.sourceX, y: a.sourceY }
  const p3 = { x: a.targetX + tx * ARROW_LENGTH, y: a.targetY + ty * ARROW_LENGTH }
  return { p0, c1: control(from, p0, p3), c2: control(to, p3, p0), p3 }
}

/** B(t). */
export function curvePoint(curve: EdgeCurve, t: number): Point {
  const u = 1 - t
  const a = u * u * u
  const b = 3 * u * u * t
  const c = 3 * u * t * t
  const d = t * t * t
  return {
    x: a * curve.p0.x + b * curve.c1.x + c * curve.c2.x + d * curve.p3.x,
    y: a * curve.p0.y + b * curve.c1.y + c * curve.c2.y + d * curve.p3.y,
  }
}

/** Whether the segment p–q meets the open box (x0, x1) × (y0, y1). */
function segmentEntersBox(p: Point, q: Point, x0: number, x1: number, y0: number, y1: number): boolean {
  if (x1 <= x0 || y1 <= y0) return false
  // Clip the segment to the closed box (Liang–Barsky), then look at the middle of what is left: on a convex box a
  // piece that runs only along a side (or touches a corner) never reaches the open inside.
  let t0 = 0
  let t1 = 1
  const dx = q.x - p.x
  const dy = q.y - p.y
  for (const [step, room] of [[-dx, p.x - x0], [dx, x1 - p.x], [-dy, p.y - y0], [dy, y1 - p.y]] as const) {
    if (step === 0) {
      if (room < 0) return false
      continue
    }
    const t = room / step
    if (step < 0) t0 = Math.max(t0, t)
    else t1 = Math.min(t1, t)
    if (t0 > t1) return false
  }
  const mid = (t0 + t1) / 2
  const x = p.x + dx * mid
  const y = p.y + dy * mid
  return x > x0 && x < x1 && y > y0 && y < y1
}

/** 0005-L §2.5a pierce: a piece of the curve (PIERCE_SAMPLES equal steps) enters the card shrunk by PIERCE_INSET. */
export function curvePierces(curve: EdgeCurve, rect: NodeRect): boolean {
  const x0 = rect.x + PIERCE_INSET
  const x1 = rect.x + rect.width - PIERCE_INSET
  const y0 = rect.y + PIERCE_INSET
  const y1 = rect.y + rect.height - PIERCE_INSET
  let prev = curve.p0
  for (let i = 1; i <= PIERCE_SAMPLES; i += 1) {
    const next = curvePoint(curve, i / PIERCE_SAMPLES)
    if (segmentEntersBox(prev, next, x0, x1, y0, y1)) return true
    prev = next
  }
  return false
}
