// flowgate.v02.0045 TR0020 rev4 — where an Edge leaves its source card and enters its target card
// (rejection item 3, after the `edgeGeometry` rule of 04-designer-flow-rules.html): never a fixed
// side, but the side that faces the other card. The two card centres decide — when the cards lie
// further apart sideways than up / down, the Edge runs right or left (it leaves the facing side
// and enters the opposite one); otherwise it runs down or up. A card dragged around another one
// changes the sides as it moves. Pure over two rectangles; FlowEdgePath feeds it the live Vue Flow
// node positions, so nothing here knows the Graph or the screen.
import { Position } from '@vue-flow/core'

/** The card size FlowNodeCard fixes — used until Vue Flow has measured a card. */
export const NODE_CARD_WIDTH = 176
export const NODE_CARD_HEIGHT = 92

export interface NodeRect {
  x: number
  y: number
  width: number
  height: number
}

export interface EdgeAnchors {
  sourceX: number
  sourceY: number
  /** The side of the source card the Edge leaves from. */
  sourcePosition: Position
  targetX: number
  targetY: number
  /** The side of the target card the Edge (its arrow head) enters. */
  targetPosition: Position
}

const OPPOSITE: Record<Position, Position> = {
  [Position.Left]: Position.Right,
  [Position.Right]: Position.Left,
  [Position.Top]: Position.Bottom,
  [Position.Bottom]: Position.Top,
}

/** The middle of one side of a rectangle. */
export function sideMiddle(rect: NodeRect, side: Position): { x: number; y: number } {
  switch (side) {
    case Position.Left:
      return { x: rect.x, y: rect.y + rect.height / 2 }
    case Position.Right:
      return { x: rect.x + rect.width, y: rect.y + rect.height / 2 }
    case Position.Top:
      return { x: rect.x + rect.width / 2, y: rect.y }
    default:
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height }
  }
}

/** The side of `source` that faces `target`: sideways when the centres differ more in x than in y. */
export function facingSide(source: NodeRect, target: NodeRect): Position {
  const dx = target.x + target.width / 2 - (source.x + source.width / 2)
  const dy = target.y + target.height / 2 - (source.y + source.height / 2)
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? Position.Right : Position.Left
  return dy >= 0 ? Position.Bottom : Position.Top
}

/** The two anchor points (and sides) of the Edge from `source` to `target`. */
export function edgeAnchors(source: NodeRect, target: NodeRect): EdgeAnchors {
  const from = facingSide(source, target)
  const to = OPPOSITE[from]
  const start = sideMiddle(source, from)
  const end = sideMiddle(target, to)
  return { sourceX: start.x, sourceY: start.y, sourcePosition: from, targetX: end.x, targetY: end.y, targetPosition: to }
}

/** A Vue Flow node as a rectangle: its absolute position and measured size (the card size until measured). */
export function nodeRect(node: {
  computedPosition: { x: number; y: number }
  dimensions: { width: number; height: number }
}): NodeRect {
  return {
    x: node.computedPosition.x,
    y: node.computedPosition.y,
    width: node.dimensions.width || NODE_CARD_WIDTH,
    height: node.dimensions.height || NODE_CARD_HEIGHT,
  }
}
