/**
 * flowgate.v02.0049 — MiniMap geometry (pure): Nodes / Edges as drawn, the Canvas's shown area,
 * the drawn bounds and the MiniMap point -> Canvas Viewport mapping.
 */
import { describe, expect, it } from 'vitest'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import {
  MINIMAP_NODE_HEIGHT,
  MINIMAP_NODE_WIDTH,
  MINIMAP_PADDING_RATIO,
  boxToWorld,
  containsPoint,
  miniMapBounds,
  miniMapEdges,
  miniMapFit,
  miniMapNodes,
  unionRect,
  viewportAt,
  viewportRect,
} from '@/features/designer/minimap/minimapGeometry'

const graph: FlowGraph = {
  flowId: 'f',
  nodes: [
    { id: 'a', kind: 'requirement', title: 'A', x: 0, y: 0 },
    { id: 'b', kind: 'design', title: 'B', docType: 'P', x: 300, y: 100 },
    { id: 'c', kind: 'task', title: 'C', x: -200, y: 300 },
  ],
  edges: [
    { id: 'ab', source: 'a', target: 'b' },
    { id: 'bc', source: 'b', target: 'c' },
    { id: 'ref', source: 'a', target: 'c', edgeClass: 'reference' },
  ],
}

describe('minimapGeometry (flowgate.v02.0049)', () => {
  it('draws every Node at its World position with the Canvas card size', () => {
    expect(miniMapNodes(graph)).toEqual([
      { id: 'a', kind: 'requirement', docType: null, x: 0, y: 0, width: MINIMAP_NODE_WIDTH, height: MINIMAP_NODE_HEIGHT },
      { id: 'b', kind: 'design', docType: 'P', x: 300, y: 100, width: MINIMAP_NODE_WIDTH, height: MINIMAP_NODE_HEIGHT },
      { id: 'c', kind: 'task', docType: null, x: -200, y: 300, width: MINIMAP_NODE_WIDTH, height: MINIMAP_NODE_HEIGHT },
    ])
    expect([MINIMAP_NODE_WIDTH, MINIMAP_NODE_HEIGHT]).toEqual([176, 92])
  })

  it('draws only the given (Canvas-drawn) Edges, between the facing sides of their Nodes', () => {
    const lines = miniMapEdges(graph, new Set(['ab', 'bc']))
    expect(lines.map((line) => line.id)).toEqual(['ab', 'bc'])
    // a -> b goes right: a's right side to b's left side.
    expect(lines[0]).toEqual({ id: 'ab', x1: 176, y1: 46, x2: 300, y2: 146 })
    // b -> c goes left: b's left side to c's right side.
    expect(lines[1]).toEqual({ id: 'bc', x1: 300, y1: 146, x2: -24, y2: 346 })
    // An id whose Node is missing is skipped.
    expect(miniMapEdges({ ...graph, nodes: graph.nodes.slice(0, 1) }, new Set(['ab']))).toEqual([])
  })

  it('turns the Viewport and the Canvas size into the shown World area', () => {
    expect(viewportRect({ x: -100, y: 50, zoom: 2 }, { width: 800, height: 600 })).toEqual({ x: 50, y: -25, width: 400, height: 300 })
    expect(viewportRect({ x: 0, y: 0, zoom: 1 }, null)).toBeNull()
    expect(viewportRect({ x: 0, y: 0, zoom: 1 }, { width: 0, height: 600 })).toBeNull()
    expect(viewportRect({ x: 0, y: 0, zoom: 0 }, { width: 800, height: 600 })).toBeNull()
  })

  it('bounds all Nodes and the shown area with padding, and nothing without Nodes', () => {
    const nodes = miniMapNodes(graph)
    const content = unionRect(nodes)!
    expect(content).toEqual({ x: -200, y: 0, width: 676, height: 392 })
    const pad = 676 * MINIMAP_PADDING_RATIO
    expect(miniMapBounds(nodes, null)).toEqual({ x: -200 - pad, y: -pad, width: 676 + pad * 2, height: 392 + pad * 2 })
    const far = { x: 2000, y: 2000, width: 100, height: 100 }
    const withView = miniMapBounds(nodes, far)!
    expect(containsPoint(withView, { x: 2100, y: 2100 })).toBe(true)
    expect(miniMapBounds([], far)).toBeNull()
  })

  it('maps a MiniMap point to World coordinates like SVG xMidYMid meet', () => {
    const bounds = { x: -100, y: 0, width: 400, height: 200 }
    // A 200 x 200 box: scale 0.5, the 100px-high drawing is centered vertically (offset 50).
    const fit = miniMapFit(bounds, { width: 200, height: 200 })!
    expect(fit).toEqual({ scale: 0.5, offsetX: 0, offsetY: 50 })
    expect(boxToWorld({ x: 0, y: 50 }, bounds, fit)).toEqual({ x: -100, y: 0 })
    expect(boxToWorld({ x: 200, y: 150 }, bounds, fit)).toEqual({ x: 300, y: 200 })
    expect(miniMapFit(bounds, { width: 0, height: 200 })).toBeNull()
  })

  it('gives the Viewport whose shown area starts at a World point, keeping the zoom', () => {
    const viewport = viewportAt(50, -25, 2)
    expect(viewport).toEqual({ x: -100, y: 50, zoom: 2 })
    expect(viewportRect(viewport, { width: 800, height: 600 })).toMatchObject({ x: 50, y: -25 })
  })
})
