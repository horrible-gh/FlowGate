// flowgate.v02.0059 T#2 (0011-CH contract D) — 정렬 / 분배 of the selected cards: where each movable
// selected card goes. Every card has the one size FlowNodeCard fixes, so lining up their left / centre /
// right edges is lining up their x (top / middle / bottom: y), and equal gaps are equal steps of x (y).
// - 정렬 (2 or more selected): left / top = the smallest x / y of the selection, right / bottom = the
//   largest, centre / middle = halfway between the two.
// - 균등 분배 (3 or more selected): the first and last card along the axis stay, the others are spread
//   between them at equal steps, in their order along that axis.
// Every selected card counts for the reference; a card that is not movable stays where it is. The result
// is kept inside the Canvas origin as a group (canvasView clampGroupToCanvas). Pure: no Vue, no Vue Flow.
import { clampGroupToCanvas } from '../canvasView'
import type { NodePlacement } from '../edit/editHistory'
import type { FlowGraphNode } from '../graph/flowGraph'

export type ArrangeKind =
  | 'align-left'
  | 'align-center'
  | 'align-right'
  | 'align-top'
  | 'align-middle'
  | 'align-bottom'
  | 'distribute-horizontal'
  | 'distribute-vertical'

export const ARRANGE_KINDS: readonly ArrangeKind[] = [
  'align-left', 'align-center', 'align-right', 'align-top', 'align-middle', 'align-bottom',
  'distribute-horizontal', 'distribute-vertical',
]

/** How many selected cards each kind needs: 정렬 2, 분배 3. */
export function arrangeMinimum(kind: ArrangeKind): number {
  return kind.startsWith('distribute-') ? 3 : 2
}

const AXIS: Readonly<Record<ArrangeKind, 'x' | 'y'>> = {
  'align-left': 'x', 'align-center': 'x', 'align-right': 'x', 'distribute-horizontal': 'x',
  'align-top': 'y', 'align-middle': 'y', 'align-bottom': 'y', 'distribute-vertical': 'y',
}

const byKey = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** The new positions of the movable selected cards (none when too few are selected). */
export function arrangeSelection(
  nodes: readonly FlowGraphNode[],
  selectedIds: readonly string[],
  kind: ArrangeKind,
): NodePlacement[] {
  const chosen = new Set(selectedIds)
  const picked = nodes.filter((node) => chosen.has(node.id))
  if (picked.length < arrangeMinimum(kind)) return []
  const axis = AXIS[kind]
  const values = picked.map((node) => node[axis])
  const low = Math.min(...values)
  const high = Math.max(...values)
  const at = (node: FlowGraphNode, value: number): NodePlacement =>
    axis === 'x' ? { nodeId: node.id, x: value, y: node.y } : { nodeId: node.id, x: node.x, y: value }
  let placed: NodePlacement[]
  if (kind.startsWith('distribute-')) {
    const ordered = [...picked].sort((a, b) => a[axis] - b[axis] || byKey(a.id, b.id))
    const gap = (high - low) / (ordered.length - 1)
    placed = ordered.map((node, index) => at(node, index === ordered.length - 1 ? high : Math.round(low + gap * index)))
  } else {
    const line = kind === 'align-left' || kind === 'align-top' ? low : kind === 'align-right' || kind === 'align-bottom' ? high : Math.round((low + high) / 2)
    placed = picked.map((node) => at(node, line))
  }
  const movable = new Set(picked.filter((node) => node.movable === true).map((node) => node.id))
  return clampGroupToCanvas(placed.filter((placement) => movable.has(placement.nodeId)))
}
