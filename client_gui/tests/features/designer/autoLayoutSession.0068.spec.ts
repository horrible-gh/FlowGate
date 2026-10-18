/** Directional layout, one Undo/Redo, persistence, measured sizes and candidate rejection. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { facingSide } from '@/features/designer/center/canvas/edgeGeometry'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowLayoutChange, FlowLayoutSaveResult } from '@/features/designer/center/graph/flowGraphSource'

// A switch the refusal test turns on: the candidate's second card is put onto its first (V3 overlap).
const tamper = vi.hoisted(() => ({ on: false }))
vi.mock('@/features/designer/center/arrange/directionalLayout', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/designer/center/arrange/directionalLayout')>()
  return {
    ...actual,
    planDirectionalLayout: (...args: Parameters<typeof actual.planDirectionalLayout>) => {
      const plan = actual.planDirectionalLayout(...args)
      if (!tamper.on) return plan
      const [first, second, ...rest] = plan.candidate
      return { ...plan, candidate: [first, { ...second, x: first.x + 10, y: first.y + 10 }, ...rest] }
    },
  }
})

type Answer = 'ok' | 'offline' | 'forbidden' | 'stored-offline' | 'ignore-first' | 'hold'

const BRANCH: Array<[string, boolean]> = [['R', true], ['NA', false], ['NB', false], ['TA', false], ['TB', false], ['Z', false], ['P', false]]
const BRANCH_EDGES: Array<[string, string]> = [['R', 'NA'], ['R', 'NB'], ['NA', 'TA'], ['NB', 'TB'], ['TA', 'Z'], ['TB', 'Z']]
const LAID_H = { R: [0, 0], NA: [260, 0], NB: [260, 140], TA: [520, 0], TB: [520, 140], Z: [780, 0], P: [0, 340] }
const LAID_V = { R: [0, 0], NA: [0, 228], NB: [224, 228], TA: [0, 404], TB: [224, 404], Z: [0, 632], P: [508, 0] }

function node(id: string, root: boolean, [x, y]: [number, number]): FlowGraphNode {
  const docType = root ? 'R' : 'T'
  return {
    id, kind: root ? 'requirement' : 'task', title: id, x, y, movable: true,
    definition: { nodeType: 'document', docType, paletteItemId: null, label: id, assignee: null },
  }
}

/** `cards`: the branch -> merge Flow (L7) or that many cards without edges (n0, n1, …). */
async function open(cards: 'branch' | number = 'branch') {
  const ids = cards === 'branch' ? BRANCH.map(([id]) => id) : Array.from({ length: cards }, (_, i) => `n${i}`)
  const initial = new Map(ids.map((id, i) => [id, [1000 + 50 * i, 900 - 30 * i] as [number, number]]))
  const server = new Map(initial)
  const answers: Answer[] = []
  const held: Array<() => void> = []
  const requests: Array<{ projectId: string; flowId: string; change: FlowLayoutChange }> = []
  let loads = 0
  const store = (change: FlowLayoutChange, skip: string | null = null) => {
    for (const p of change.positions) if (p.nodeId !== skip) server.set(p.nodeId, [p.x, p.y])
  }
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: async (_projectId, flowId) => {
      loads += 1
      return {
        flowId,
        projection: 'graph',
        nodes: ids.map((id) => node(id, cards === 'branch' && id === 'R', server.get(id)!)),
        edges: (cards === 'branch' ? BRANCH_EDGES : []).map(([source, target], i) => ({ id: `e${i}`, source, target, edgeClass: 'execution' })),
        draft: null,
      } satisfies FlowGraph
    },
    saveLayout: (projectId, flowId, change) => {
      requests.push({ projectId, flowId, change })
      const answer = answers.shift() ?? 'ok'
      const ok = (ignored: string[] = []): Promise<FlowLayoutSaveResult> => Promise.resolve({ ignoredNodeIds: ignored })
      if (answer === 'offline') return Promise.reject(new Error('Network Error'))
      if (answer === 'forbidden') return Promise.reject(Object.assign(new Error('forbidden'), { cause: { response: { status: 403 } } }))
      if (answer === 'stored-offline') {
        store(change)
        return Promise.reject(new Error('Network Error'))
      }
      if (answer === 'ignore-first') {
        const first = change.positions[0].nodeId
        store(change, first)
        return ok([first])
      }
      if (answer === 'hold') {
        return new Promise((resolve) => held.push(() => {
          store(change)
          resolve({ ignoredNodeIds: [] })
        }))
      }
      store(change)
      return ok()
    },
  }
  const center = createDesignerCenterSession({ source, layoutSaveDelayMs: 50 })
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'Flow One' })
  await vi.runAllTimersAsync()
  const graph = () => {
    const load = center.state.graphs.f1
    if (load?.status !== 'ready') throw new Error('not ready')
    return load.graph
  }
  const positions = () => Object.fromEntries(graph().nodes.map((n) => [n.id, [n.x, n.y]]))
  const sides = (sizes: Record<string, { width: number; height: number }> = {}) => {
    const g = graph()
    const rect = (id: string) => ({ ...g.nodes.find((n) => n.id === id)!, width: 176, height: 92, ...sizes[id] })
    return g.edges.map((e) => `${facingSide(rect(e.source), rect(e.target))}`)
  }
  const release = async () => {
    held.shift()!()
    await vi.advanceTimersByTimeAsync(0)
  }
  return {
    center, server, initial, answers, requests, positions, sides, release, held,
    loads: () => loads,
    canUndo: () => center.state.edits.f1?.canUndo ?? false,
    initialPositions: () => Object.fromEntries([...initial].map(([id, at]) => [id, [...at]])),
    serverPositions: () => Object.fromEntries([...server].map(([id, at]) => [id, [...at]])),
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  tamper.on = false
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('0004-D §7 B in the session (flowgate.v02.0068 T#2)', () => {
  it.each([
    ['horizontal', LAID_H, 'right'],
    ['vertical', LAID_V, 'bottom'],
  ] as const)('%s: one 자동정렬 is one undo entry; redo and a read give the same positions and sides', async (dir, laid, side) => {
    const s = await open()
    expect(s.center.autoLayout('f1', dir)).toBe(true)
    expect(s.positions()).toEqual(laid)
    expect(s.sides().every((x) => x === side)).toBe(true)
    expect(s.center.undoEdit('f1')).toBe(true)
    expect(s.positions()).toEqual(s.initialPositions())
    expect(s.canUndo()).toBe(false)
    expect(s.center.redoEdit('f1')).toBe(true)
    expect(s.positions()).toEqual(laid)
    expect(s.sides().every((x) => x === side)).toBe(true)
    await vi.runAllTimersAsync()
    expect(s.serverPositions()).toEqual(laid)
    s.center.refreshFlow('f1')
    await vi.runAllTimersAsync()
    expect(s.positions()).toEqual(laid)
    expect(s.sides().every((x) => x === side)).toBe(true)
    // The same direction again: nothing to move, no entry.
    expect(s.center.autoLayout('f1', dir)).toBe(false)
  })

  it('a refused candidate moves no card, makes no undo entry, saves nothing and tells once', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const s = await open()
    tamper.on = true
    expect(s.center.autoLayout('f1', 'horizontal')).toBe(false)
    expect(s.positions()).toEqual(s.initialPositions())
    expect(s.canUndo()).toBe(false)
    expect(s.center.layoutNotice.value).toEqual({ seq: 1, kind: 'invalid', reason: 'overlap', flowId: 'f1', flowName: null })
    await vi.runAllTimersAsync()
    expect(s.requests).toEqual([])
    expect(warn).toHaveBeenCalledTimes(1)
  })

  it('uses the card sizes the Canvas measured (L1: a taller card)', async () => {
    const s = await open()
    s.center.recordNodeSizes('f1', { NA: { width: 176, height: 300 } })
    expect(s.center.activeNodeSizes.value).toEqual({ NA: { width: 176, height: 300 } })
    expect(s.center.autoLayout('f1', 'horizontal')).toBe(true)
    // NA's lane is 300 high: NB moves down to 348, and stage 1 moves right to 352 so R -> NB still faces right.
    expect(s.positions()).toMatchObject({ R: [0, 0], NA: [352, 0], NB: [352, 348], TA: [612, 0], TB: [612, 348], Z: [964, 0], P: [0, 548] })
    expect(s.sides({ NA: { width: 176, height: 300 } }).every((x) => x === 'right')).toBe(true)
  })
})

