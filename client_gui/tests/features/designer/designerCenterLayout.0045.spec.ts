/**
 * flowgate.v02.0045 T0011 (S1+S2) — server Graph source, Graph model mapping and the Center
 * session's layout save (P0008 §3 / §4): saved Viewport restore, movable Node moves, batched
 * debounced save, re-read on ignored keys or a failed save, one save per Flow on the wire, the
 * background re-read of a Graph changed in another tab / device (S2-4, §8.3), and re-reads that
 * never bring back positions older than this tab's later moves (S2-2). No DOM.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowCanvas, FlowCanvasLayoutInput, FlowCanvasLayoutResult } from '@/features/project'
import { toVueFlowEdges, toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphChangeHandlers, FlowGraphSource, FlowLayoutChange } from '@/features/designer/center/graph/flowGraphSource'
import { CANVAS_COLLAPSE, createServerFlowGraphSource, toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import { createLayoutSaveQueue } from '@/features/designer/center/layoutSaveQueue'

function canvas(overrides: Partial<FlowCanvas> = {}): FlowCanvas {
  const node = (key: string, x: number, move = true) => ({
    nodeKey: key, identitySource: 'document', origin: 'document', kind: 'requirement', docType: 'R', title: key,
    state: 'done', position: { x, y: 0, source: 'auto' }, editable: { move, structure: false }, document: null, slot: null,
  })
  return {
    flowId: 'f1',
    projectId: 'p',
    ownership: { state: 'v01', sequenceRootDocId: 'f1.0001-R', graphRevisionNo: null, assignedAt: null },
    projection: 'sequence',
    nodes: [node('doc:a', 0), node('slot_b', 260), node('doc:c', 520, false)],
    edges: [
      { edgeId: 'seq:doc:a>slot_b', edgeClass: 'execution', origin: 'sequence', source: 'doc:a', target: 'slot_b', relation: null, editable: false },
      { edgeId: 'ref:target:a>c', edgeClass: 'reference', origin: 'document_relation', source: 'doc:a', target: 'doc:c', relation: 'target', editable: false },
    ],
    externalRelations: [],
    viewport: null,
    identity: { sequenceTag: null, rematched: false, retiredNodeKeys: [] },
    collapsed: { applied: [], ignoredKeys: [], hiddenCounts: {} },
    ...overrides,
  }
}

describe('server Graph source (S1)', () => {
  it('maps the Canvas read onto the FlowGate Graph Model with meaning and layout fields', () => {
    const graph = toFlowGraph(canvas({ viewport: { x: -40, y: 12, zoom: 0.85 } }))
    expect(graph.flowId).toBe('f1')
    expect(graph.ownership).toBe('v01')
    expect(graph.viewport).toEqual({ x: -40, y: 12, zoom: 0.85 })
    expect(graph.nodes.map((n) => [n.id, n.x, n.positionSource, n.movable])).toEqual([
      ['doc:a', 0, 'auto', true], ['slot_b', 260, 'auto', true], ['doc:c', 520, 'auto', false],
    ])
    expect(graph.edges.map((e) => [e.id, e.edgeClass])).toEqual([['seq:doc:a>slot_b', 'execution'], ['ref:target:a>c', 'reference']])
  })

  it('is not sample data, reads through the store loader and saves layout in the transport shape', async () => {
    const saved: Array<[string, FlowCanvasLayoutInput]> = []
    const reads: unknown[][] = []
    const source = createServerFlowGraphSource({
      loadFlowCanvas: async (...args) => {
        reads.push(args)
        return canvas({ flowId: args[0] })
      },
      saveFlowCanvasLayout: async (flowId, input): Promise<FlowCanvasLayoutResult> => {
        saved.push([flowId, input])
        return { savedPositions: 1, viewportSaved: true, ignored: [{ nodeKey: 'slot_old', reason: 'retired' }] }
      },
    })
    expect(source.isSample).toBe(false)
    expect((await source.loadGraph('p', 'f9')).flowId).toBe('f9')
    await source.loadGraph('p', 'f9', 'draft')
    // TR0020 rev5: every Canvas read leaves conversations out (no memo card for a new conversation).
    expect(CANVAS_COLLAPSE).toEqual(['conversation'])
    expect(reads).toEqual([['f9', undefined, ['conversation']], ['f9', 'draft', ['conversation']]])
    const result = await source.saveLayout!('p', 'f9', { positions: [{ nodeId: 'doc:a', x: 1, y: 2 }], viewport: { x: 0, y: 0, zoom: 1 } })
    expect(saved).toEqual([['f9', { positions: [{ nodeKey: 'doc:a', x: 1, y: 2 }], viewport: { x: 0, y: 0, zoom: 1 } }]])
    expect(result).toEqual({ ignoredNodeIds: ['slot_old'] })
  })

  it('Vue Flow input: only movable Nodes are draggable and Edges carry their class', () => {
    const graph = toFlowGraph(canvas())
    expect(toVueFlowNodes(graph, null).map((n) => n.draggable)).toEqual([true, true, false])
    // flowgate.v02.0045 T0019: both ends are done, so the execution Edge is progressed. TR0020 rev4:
    // the reference relation is kept in the Graph but not drawn (no dashed fan-out from a document).
    expect(toVueFlowEdges(graph).map((e) => [e.id, e.data])).toEqual([
      ['seq:doc:a>slot_b', { edgeClass: 'execution', progressed: true, deletable: false }],
    ])
    const sample: FlowGraph = { flowId: 's', nodes: [{ id: 'n', kind: 'task', title: 't', x: 0, y: 0 }], edges: [] }
    expect(toVueFlowNodes(sample, null)[0].draggable).toBe(false)
  })
})

describe('layout save queue', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('batches per Flow, keeps the last position per Node and sends once after the quiet time', () => {
    const sent: Array<[string, FlowLayoutChange]> = []
    const queue = createLayoutSaveQueue((flowId, change) => sent.push([flowId, change]), 100)
    queue.queuePosition('f1', 'a', 1, 1)
    vi.advanceTimersByTime(60)
    queue.queuePosition('f1', 'a', 5, 6)
    queue.queuePosition('f1', 'b', 7, 8)
    queue.queueViewport('f1', { x: 1, y: 2, zoom: 0.5 })
    queue.queuePosition('f2', 'z', 0, 0)
    vi.advanceTimersByTime(99)
    expect(sent).toEqual([])
    vi.advanceTimersByTime(1)
    expect(sent).toEqual([
      ['f1', { positions: [{ nodeId: 'a', x: 5, y: 6 }, { nodeId: 'b', x: 7, y: 8 }], viewport: { x: 1, y: 2, zoom: 0.5 } }],
      ['f2', { positions: [{ nodeId: 'z', x: 0, y: 0 }], viewport: null }],
    ])
    queue.flushAll()
    expect(sent).toHaveLength(2)
  })
})

describe('Center session layout (S2)', () => {
  let loads: number
  let saves: Array<[string, FlowLayoutChange]>
  let saveResult: () => Promise<{ ignoredNodeIds: readonly string[] }>

  function source(): FlowGraphSource {
    return {
      isSample: false,
      loadGraph: async (_p, flowId) => {
        loads += 1
        return toFlowGraph(canvas({ flowId, viewport: { x: 10, y: 20, zoom: 0.5 } }))
      },
      saveLayout: async (_p, flowId, change) => {
        saves.push([flowId, change])
        return saveResult()
      },
    }
  }

  async function ready() {
    const center = createDesignerCenterSession({ source: source(), layoutSaveDelayMs: 50 })
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    await vi.runAllTimersAsync()
    return center
  }

  const graphOf = (center: ReturnType<typeof createDesignerCenterSession>) => {
    const load = center.state.graphs.f1
    if (load?.status !== 'ready') throw new Error('graph not ready')
    return load.graph
  }

  beforeEach(() => {
    vi.useFakeTimers()
    loads = 0
    saves = []
    saveResult = async () => ({ ignoredNodeIds: [] })
  })
  afterEach(() => vi.useRealTimers())

  it('restores the saved Viewport of the read and reports no sample source', async () => {
    const center = await ready()
    expect(center.isSampleSource).toBe(false)
    expect(center.activeView.value!.viewport).toEqual({ x: 10, y: 20, zoom: 0.5 })
  })

  it('moves a movable Node at once and saves moves + committed Viewport in one batch', async () => {
    const center = await ready()
    center.moveNode('f1', 'slot_b', 300, 40)
    center.moveNode('f1', 'doc:a', 5, 5)
    center.commitViewport('f1', { x: -1, y: -2, zoom: 0.7 })
    expect(graphOf(center).nodes.find((n) => n.id === 'slot_b')).toMatchObject({ x: 300, y: 40, positionSource: 'saved' })
    expect(center.activeView.value!.viewport).toEqual({ x: -1, y: -2, zoom: 0.7 })
    expect(saves).toEqual([])
    await vi.advanceTimersByTimeAsync(50)
    expect(saves).toEqual([['f1', {
      positions: [{ nodeId: 'slot_b', x: 300, y: 40 }, { nodeId: 'doc:a', x: 5, y: 5 }],
      viewport: { x: -1, y: -2, zoom: 0.7 },
    }]])
    expect(loads).toBe(1)
  })

  it('ignores a Node that is not movable or unknown, and a move to the same place', async () => {
    const center = await ready()
    const before = graphOf(center)
    center.moveNode('f1', 'doc:c', 999, 999)
    center.moveNode('f1', 'nope', 1, 1)
    center.moveNode('f1', 'doc:a', 0, 0)
    await vi.advanceTimersByTimeAsync(100)
    expect(graphOf(center)).toBe(before)
    expect(saves).toEqual([])
  })

  it('reads the Graph again when the save ignored keys or failed', async () => {
    const center = await ready()
    saveResult = async () => ({ ignoredNodeIds: ['slot_b'] })
    center.moveNode('f1', 'slot_b', 1, 1)
    await vi.runAllTimersAsync()
    expect(loads).toBe(2)
    saveResult = async () => { throw new Error('network') }
    center.moveNode('f1', 'doc:a', 2, 2)
    await vi.runAllTimersAsync()
    expect(loads).toBe(3)
  })

  it('sends what is pending when the tab closes, and keeps moves on screen only for a source without saveLayout', async () => {
    const center = await ready()
    center.moveNode('f1', 'doc:a', 9, 9)
    center.closeFlow('f1')
    expect(saves).toEqual([['f1', { positions: [{ nodeId: 'doc:a', x: 9, y: 9 }], viewport: null }]])

    const plain = createDesignerCenterSession({
      source: { isSample: false, loadGraph: async (_p, flowId) => toFlowGraph(canvas({ flowId })) },
    })
    plain.setProject('p')
    plain.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    await vi.runAllTimersAsync()
    plain.moveNode('f1', 'doc:a', 3, 3)
    plain.commitViewport('f1', { x: 1, y: 1, zoom: 1 })
    expect(plain.activeView.value!.viewport).toEqual({ x: 1, y: 1, zoom: 1 })
    const load = plain.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.nodes[0].x).toBe(3)
  })
})

describe('layout save queue — one save per Flow on the wire (S2-1 last write)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('holds a newer batch until the running save settles, then sends the newest values', async () => {
    const sent: Array<[string, FlowLayoutChange]> = []
    const settle: Array<{ ok: () => void; fail: () => void }> = []
    const idle: string[] = []
    const queue = createLayoutSaveQueue((flowId, change) => {
      sent.push([flowId, change])
      return new Promise<void>((ok, fail) => settle.push({ ok, fail: () => fail(new Error('network')) }))
    }, 100, (flowId) => idle.push(flowId))
    queue.queuePosition('f1', 'a', 1, 1)
    vi.advanceTimersByTime(100)
    queue.queuePosition('f1', 'a', 2, 2)
    queue.queueViewport('f1', { x: 3, y: 3, zoom: 1 })
    vi.advanceTimersByTime(100)
    queue.queuePosition('f1', 'a', 4, 4)
    queue.flush('f1')
    queue.queuePosition('f2', 'z', 0, 0)
    queue.flush('f2')
    // The second f1 batch waits for the first; another Flow is not held back.
    expect(sent.map(([flowId]) => flowId)).toEqual(['f1', 'f2'])
    expect(queue.busy('f1')).toBe(true)
    settle[0].fail()
    await vi.advanceTimersByTimeAsync(0)
    expect(sent[2]).toEqual(['f1', { positions: [{ nodeId: 'a', x: 4, y: 4 }], viewport: { x: 3, y: 3, zoom: 1 } }])
    expect(idle).toEqual([])
    settle[1].ok()
    settle[2].ok()
    await vi.advanceTimersByTimeAsync(0)
    expect(idle.sort()).toEqual(['f1', 'f2'])
    expect([queue.busy('f1'), queue.busy('f2'), sent.length]).toEqual([false, false, 3])
  })
})

describe('Center session — Graph changed in another tab / device (P0008 S2-4, §8.3)', () => {
  let loads: number
  let saves: FlowLayoutChange[]
  let release: Array<() => void>
  let handlers: FlowGraphChangeHandlers | null
  let watched: string[]
  let stops: number

  function source(): FlowGraphSource {
    return {
      isSample: false,
      loadGraph: async (_p, flowId) => {
        loads += 1
        return toFlowGraph(canvas({ flowId }))
      },
      saveLayout: (_p, _flowId, change) => {
        saves.push(change)
        return new Promise((ok) => release.push(() => ok({ ignoredNodeIds: [] })))
      },
      watchChanges: (projectId, h) => {
        watched.push(projectId)
        handlers = h
        return () => {
          stops += 1
        }
      },
    }
  }

  async function ready() {
    const center = createDesignerCenterSession({ source: source(), layoutSaveDelayMs: 50 })
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    center.openFlow({ projectId: 'p', flowId: 'f2', flowName: 'F2' })
    await vi.runAllTimersAsync()
    return center
  }

  beforeEach(() => {
    vi.useFakeTimers()
    loads = 0
    saves = []
    release = []
    handlers = null
    watched = []
    stops = 0
  })
  afterEach(() => vi.useRealTimers())

  it('reads a changed open Flow again while still showing it, and every open Flow after a resync', async () => {
    const center = await ready()
    expect(watched).toEqual(['p'])
    const shown = center.state.graphs.f1
    handlers!.changed('f1')
    expect(center.state.graphs.f1).toBe(shown)
    await vi.runAllTimersAsync()
    expect(loads).toBe(3)
    expect(center.state.graphs.f1?.status).toBe('ready')
    expect(center.state.graphs.f1).not.toBe(shown)
    handlers!.changed('not-open')
    await vi.runAllTimersAsync()
    expect(loads).toBe(3)
    handlers!.resync()
    await vi.runAllTimersAsync()
    expect(loads).toBe(5)
  })

  it('waits with the re-read until this tab\'s waiting / running save of the Flow settles', async () => {
    const center = await ready()
    center.moveNode('f1', 'doc:a', 7, 7)
    handlers!.changed('f1')
    await vi.advanceTimersByTimeAsync(50)
    expect(saves).toHaveLength(1)
    handlers!.changed('f1')
    await vi.advanceTimersByTimeAsync(0)
    expect(loads).toBe(2)
    release[0]()
    await vi.runAllTimersAsync()
    expect(loads).toBe(3)
    expect(center.state.graphs.f1?.status).toBe('ready')
  })

  it('watches only the current project and stops watching on dispose, sending what is pending', async () => {
    const center = await ready()
    center.moveNode('f1', 'doc:a', 1, 2)
    center.setProject('q')
    expect([watched, stops, saves.length]).toEqual([['p', 'q'], 1, 1])
    center.dispose()
    expect(stops).toBe(2)
  })
})

describe('Center session — re-read after an ignored / failed save vs. later moves (P0008 S2-2)', () => {
  // The server keeps doc:a's x; a read takes the value of the moment it goes out.
  let serverX: number
  let reads: Array<() => void>
  let saves: Array<{ ok: (ignored?: string[]) => void; fail: () => void }>

  function source(): FlowGraphSource {
    return {
      isSample: false,
      loadGraph: (_p, flowId) => {
        const x = serverX
        const graph = toFlowGraph(canvas({ flowId }))
        return new Promise((ok) => reads.push(() => ok({ ...graph, nodes: graph.nodes.map((n) => (n.id === 'doc:a' ? { ...n, x } : n)) })))
      },
      saveLayout: (_p, _flowId, change) =>
        new Promise((ok, fail) => saves.push({
          ok: (ignored = []) => {
            const moved = change.positions.find((p) => p.nodeId === 'doc:a')
            if (moved && !ignored.includes('doc:a')) serverX = moved.x
            ok({ ignoredNodeIds: ignored })
          },
          fail: () => fail(new Error('network')),
        })),
    }
  }

  async function ready() {
    const center = createDesignerCenterSession({ source: source(), layoutSaveDelayMs: 50 })
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    reads.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    return center
  }

  const xOf = (center: ReturnType<typeof createDesignerCenterSession>) => {
    const load = center.state.graphs.f1
    if (load?.status !== 'ready') throw new Error('graph not ready')
    return load.graph.nodes.find((n) => n.id === 'doc:a')!.x
  }

  beforeEach(() => {
    vi.useFakeTimers()
    serverX = 0
    reads = []
    saves = []
  })
  afterEach(() => vi.useRealTimers())

  it('reads only after the later save of the Flow settles, not right after the ignored answer', async () => {
    const center = await ready()
    center.moveNode('f1', 'doc:a', 10, 10)
    await vi.advanceTimersByTimeAsync(50)
    center.moveNode('f1', 'doc:a', 20, 20)
    await vi.advanceTimersByTimeAsync(50)
    saves[0].ok(['slot_b'])
    await vi.advanceTimersByTimeAsync(0)
    expect([reads.length, saves.length, xOf(center)]).toEqual([0, 2, 20])
    saves[1].ok()
    await vi.advanceTimersByTimeAsync(0)
    expect(reads).toHaveLength(1)
    reads.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect([xOf(center), serverX]).toEqual([20, 20])
  })

  it('a re-read answered while a later save is running keeps the moved Node, then reads again', async () => {
    const center = await ready()
    center.moveNode('f1', 'doc:a', 10, 10)
    await vi.advanceTimersByTimeAsync(50)
    saves[0].fail()
    await vi.advanceTimersByTimeAsync(0)
    expect(reads).toHaveLength(1)
    center.moveNode('f1', 'doc:a', 30, 30)
    await vi.advanceTimersByTimeAsync(50)
    expect(saves).toHaveLength(2)
    reads.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect([xOf(center), reads.length]).toEqual([30, 0])
    saves[1].ok()
    await vi.advanceTimersByTimeAsync(0)
    expect(reads).toHaveLength(1)
    reads.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect([xOf(center), serverX]).toEqual([30, 30])
  })

  it('a re-read older than a move whose save already settled keeps the move and reads again at once', async () => {
    const center = await ready()
    center.moveNode('f1', 'doc:a', 10, 10)
    await vi.advanceTimersByTimeAsync(50)
    saves[0].ok(['slot_b'])
    await vi.advanceTimersByTimeAsync(0)
    expect(reads).toHaveLength(1)
    center.moveNode('f1', 'doc:a', 40, 40)
    await vi.advanceTimersByTimeAsync(50)
    saves[1].ok()
    await vi.advanceTimersByTimeAsync(0)
    expect([reads.length, serverX]).toEqual([1, 40])
    reads.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect([xOf(center), reads.length]).toEqual([40, 1])
    reads.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect([xOf(center), reads.length]).toEqual([40, 0])
  })
})
