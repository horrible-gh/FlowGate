/**
 * flowgate.v02.0068 T#2 (0005-L §2.5a, T §7 DEFERRED) — the pierce check's curve against the line the Canvas draws.
 * FlowEdgePath: edgeAnchors over the two cards, the end moved ARROW_LENGTH (9) out of the target side, then Vue Flow's
 * getBezierPath (curvature 0.25). arrange/edgeCurve.ts writes the same formula out (Vue Flow stays inside
 * center/canvas/); here both are built for sample card pairs and compared: the four Bézier points parsed from the
 * SVG path and PIERCE_SAMPLES + 1 = 33 points along the curve, within 1e-9 px.
 */
import { describe, expect, it } from 'vitest'
import { getBezierPath, Position } from '@vue-flow/core'
import { edgeAnchors, type NodeRect } from '@/features/designer/center/canvas/edgeGeometry'
import {
  ARROW_LENGTH,
  BEZIER_CURVATURE,
  PIERCE_INSET,
  PIERCE_SAMPLES,
  curvePierces,
  curvePoint,
  edgeCurve,
  type EdgeCurve,
} from '@/features/designer/center/arrange/edgeCurve'

const TOLERANCE = 1e-9

const rect = (x: number, y: number, width = 176, height = 92): NodeRect => ({ x, y, width, height })

/** The curve FlowEdgePath draws, read back from getBezierPath's SVG path. */
function canvasCurve(source: NodeRect, target: NodeRect): EdgeCurve {
  const a = edgeAnchors(source, target)
  const into: Record<Position, [number, number]> = {
    [Position.Left]: [1, 0], [Position.Right]: [-1, 0], [Position.Top]: [0, 1], [Position.Bottom]: [0, -1],
  }
  const [dx, dy] = into[a.targetPosition]
  const [path] = getBezierPath({
    sourceX: a.sourceX,
    sourceY: a.sourceY,
    sourcePosition: a.sourcePosition,
    targetX: a.targetX - dx * ARROW_LENGTH,
    targetY: a.targetY - dy * ARROW_LENGTH,
    targetPosition: a.targetPosition,
  })
  const n = path.match(/-?\d+(\.\d+)?(e-?\d+)?/g)!.map(Number)
  expect(n).toHaveLength(8)
  return { p0: { x: n[0], y: n[1] }, c1: { x: n[2], y: n[3] }, c2: { x: n[4], y: n[5] }, p3: { x: n[6], y: n[7] } }
}

const SAMPLES: Array<[string, NodeRect, NodeRect]> = [
  ['right, same row', rect(0, 0), rect(260, 0)],
  ['right, a far branch below', rect(0, 0), rect(564, 560)],
  ['left (target behind the source)', rect(600, 100), rect(0, 0)],
  ['down', rect(0, 0), rect(0, 228)],
  ['down to a far branch right', rect(0, 0), rect(896, 900)],
  ['up', rect(100, 500), rect(80, 0)],
  ['a tie (|dx| = |dy|) goes sideways', rect(0, 0), rect(300, 300)],
  ['backwards in the target direction (negative control distance)', rect(0, 0), rect(150, 20)],
  ['other card sizes', rect(0, 0, 120, 60), rect(240, 248, 176, 92)],
]

describe('the pierce check curve is the Canvas line (flowgate.v02.0068 T#2)', () => {
  it('uses the Canvas constants', () => {
    expect([ARROW_LENGTH, BEZIER_CURVATURE, PIERCE_SAMPLES, PIERCE_INSET]).toEqual([9, 0.25, 32, 1])
  })

  it.each(SAMPLES)('%s: the same Bézier points and the same 33 sample points as getBezierPath', (_title, source, target) => {
    const ours = edgeCurve(source, target)
    const canvas = canvasCurve(source, target)
    for (const key of ['p0', 'c1', 'c2', 'p3'] as const) {
      expect(Math.abs(ours[key].x - canvas[key].x)).toBeLessThanOrEqual(TOLERANCE)
      expect(Math.abs(ours[key].y - canvas[key].y)).toBeLessThanOrEqual(TOLERANCE)
    }
    for (let i = 0; i <= PIERCE_SAMPLES; i += 1) {
      const a = curvePoint(ours, i / PIERCE_SAMPLES)
      const b = curvePoint(canvas, i / PIERCE_SAMPLES)
      expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeLessThanOrEqual(TOLERANCE)
    }
  })

  it('a pierce enters the card shrunk by 1 px; touching or running along a side is none', () => {
    // R -> B straight through A (L5 before its repair).
    expect(curvePierces(edgeCurve(rect(0, 0), rect(520, 0)), rect(260, 0))).toBe(true)
    // The same line just below A's bottom side, and along it inside the 1 px inset.
    expect(curvePierces(edgeCurve(rect(0, 0), rect(520, 0)), rect(260, 47))).toBe(false)
    expect(curvePierces(edgeCurve(rect(0, 0), rect(520, 0)), rect(260, 45.5))).toBe(false)
    expect(curvePierces(edgeCurve(rect(0, 0), rect(520, 0)), rect(260, 44))).toBe(true)
    // The L2 case: B -> C bends through the tall card A before the gap repair, not after it.
    const a = rect(240, 0, 300, 200)
    expect(curvePierces(edgeCurve(rect(240, 248), rect(624, 0, 250, 150)), a)).toBe(true)
    expect(curvePierces(edgeCurve(rect(240, 248), rect(708, 0, 250, 150)), a)).toBe(false)
  })
})
