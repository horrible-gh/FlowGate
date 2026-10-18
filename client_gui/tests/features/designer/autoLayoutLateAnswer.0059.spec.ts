/**
 * flowgate.v02.0059 T#2 (0016-TR rev2) — a 자동 배치 whose extra read (the whole Flow, or a root document of a
 * v0.1 Flow) answers late moves nothing when, meanwhile, it was cancelled (the Shell's 자율형), the session was
 * disposed, or a read replaced the Graph it was asked for (a retry, a change elsewhere, the re-read of an
 * ignored / failed save, a reset). No card moves, no layout save goes out and no undo entry is made; the next
 * 자동 배치 lays the Graph now shown out from its own input.
 * The real read path as autoLayoutServerRead.0059: the project store's Canvas loader / saver -> the server Graph
 * source -> the Center session; only `@shared/api` is mocked (lateLayoutServer.ts), and the server source's
 * change reports are a handle the test calls.
 */
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { documentRead } from '@/features/document'
import { useProjectStore } from '@/features/project'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import {
  assignedFlow,
  createLateServer,
  documentsFlow,
  replacedRootFlow,
  type LateServer,
  type ServerFlow,
} from './lateLayoutServer'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  putRequest,
  patchRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

type ChangeHandlers = Parameters<NonNullable<FlowGraphSource['watchChanges']>>[1]

let server: LateServer
let changes: ChangeHandlers | null = null
const sessions: Session[] = []

beforeEach(() => {
  server = createLateServer()
  changes = null
  getRequest.mockReset()
  putRequest.mockReset()
  getRequest.mockImplementation(server.get)
  putRequest.mockImplementation(server.put)
})

// A session left running would send its delayed saves to the next test's server.
afterEach(() => {
  while (sessions.length) sessions.pop()!.dispose()
})

/** The layout save delay (10 ms) and its answer. */
async function settleSaves() {
  await new Promise((resolve) => setTimeout(resolve, 40))
  await flushPromises()
}

const savesOf = (flowId: string) => server.saves.filter((save) => save.flowId === flowId)

/** The last stored position of every card of the Flow. */
function stored(flowId: string) {
  const out: Record<string, [number, number]> = {}
  for (const save of savesOf(flowId)) for (const p of save.positions) out[p.node_key] = [p.x, p.y]
  return out
}

/** Where the server's collapsed read puts each card it shows. */
function serverShown(flow: ServerFlow) {
  const nodes = (flow.collapsed as { nodes: Array<{ node_key: string; position: { x: number; y: number } }> }).nodes
  return Object.fromEntries(nodes.map((node) => [node.node_key, [node.position.x, node.position.y]]))
}

async function openSession(flowId: string, flow: ServerFlow) {
  server.flows[flowId] = flow
  setActivePinia(createPinia())
  const store = useProjectStore()
  const serverSource = createServerFlowGraphSource(
    { loadFlowCanvas: store.loadFlowCanvas, saveFlowCanvasLayout: store.saveFlowCanvasLayout },
    documentRead,
  )
  const source: FlowGraphSource = {
    ...serverSource,
    watchChanges: (_projectId, handlers) => {
      changes = handlers
      return () => {
        changes = null
      }
    },
  }
  const center = createDesignerCenterSession({ source, layoutSaveDelayMs: 10 })
  sessions.push(center)
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId, flowName: flowId })
  await flushPromises()
  const shown = () => {
    const load = center.state.graphs[flowId]
    if (load?.status !== 'ready') throw new Error('not ready')
    return load.graph as FlowGraph
  }
  const positions = () => Object.fromEntries(shown().nodes.map((node) => [node.id, [node.x, node.y]]))
  /** Every shown card somewhere else (one undo entry), as the reviewer's R (2000, 1500), D (2300, 1540). */
  const scatter = () =>
    center.moveNodes(flowId, shown().nodes.map((node, i) => ({ nodeId: node.id, x: 2000 + 300 * i, y: 1500 + 40 * i })))
  return { center, shown, positions, scatter }
}

// flowgate.v02.0068 T#2: the last column is where 가로 자동정렬 puts the shown cards (0005-L §2.6: after the chain,
// the documents in one row below it; a hidden document keeps its slot and its real place).
const HELD_READS = [
  ['the whole Flow read (a hidden conversation)', 'p.ui.0021', () => documentsFlow('p.ui.0021'), 'whole',
    { 'doc:p.ui.0021.0001-R': [0, 0], 'doc:p.ui.0021.0003-D': [520, 0] }],
  ['the root document read (a replaced v0.1 root)', 'p.ui.0022', () => replacedRootFlow('p.ui.0022'), 'document',
    { 'doc:p.ui.0022.0005-R': [0, 0], 'doc:p.ui.0022.0003-D': [0, 200], 'doc:p.ui.0022.0004-D': [260, 200] }],
] as const

describe('a late 자동 배치 answer moves nothing once it no longer belongs to the shown Graph (flowgate.v02.0059 T#2)', () => {
  it.each(HELD_READS)('cancelAutoLayout (자율형) while waiting on %s', async (_title, flowId, flow, held, laidOut) => {
    const { center, positions, scatter } = await openSession(flowId, flow())
    expect(scatter()).toBe(true)
    await settleSaves()
    const scattered = positions()
    const savesBefore = savesOf(flowId).length
    server.hold[held] = true
    const pending = center.autoLayout(flowId, 'vertical')
    await flushPromises()
    expect(server.heldCount()).toBe(1)
    center.cancelAutoLayout()
    server.release()
    expect(await pending).toBe(false)
    expect(positions()).toEqual(scattered)
    await settleSaves()
    expect(savesOf(flowId)).toHaveLength(savesBefore)
    // The history holds the scatter only.
    expect(center.undoEdit(flowId)).toBe(true)
    expect(positions()).toEqual(serverShown(server.flows[flowId]))
    expect(center.undoEdit(flowId)).toBe(false)
    // A 자동정렬 asked afterwards still lays the cards out.
    expect(scatter()).toBe(true)
    expect(await center.autoLayout(flowId, 'horizontal')).toBe(true)
    expect(positions()).toEqual(laidOut)
  })

  it.each(HELD_READS)('dispose while waiting on %s: the waiting user save still goes out, the answer changes nothing', async (_title, flowId, flow, held) => {
    const { center, positions, scatter } = await openSession(flowId, flow())
    server.hold[held] = true
    expect(scatter()).toBe(true)
    const scattered = positions()
    const pending = center.autoLayout(flowId, 'vertical')
    await flushPromises()
    expect(server.heldCount()).toBe(1)
    center.dispose()
    await flushPromises()
    // The user's move was saved (sent at once by dispose, or by its own delay before).
    expect(stored(flowId)).toEqual(scattered)
    const savesAtDispose = savesOf(flowId).length
    server.release()
    expect(await pending).toBe(false)
    expect(positions()).toEqual(scattered)
    await settleSaves()
    expect(savesOf(flowId)).toHaveLength(savesAtDispose)
    expect(stored(flowId)).toEqual(scattered)
    expect(center.undoEdit(flowId)).toBe(false)
    // Nothing more after dispose.
    expect(center.autoLayout(flowId, 'horizontal')).toBe(false)
  })

  // The reviewer's case: R, CH (hidden), D 0003; the read meanwhile shows D 0004 in place of D 0003. The old
  // whole Flow (with D 0003) laid over the new Graph would put D 0004 after it, at (780, 0) — the server has
  // it at (520, 0).
  const REREADS = [
    ['retryGraph', async (center: Session, flowId: string) => center.retryGraph(flowId)],
    ['a change elsewhere', async (_center: Session, flowId: string) => changes!.changed(flowId)],
    ['an ignored layout save', async (center: Session, flowId: string) => {
      server.saveAnswer = 'ignored'
      center.moveNode(flowId, `doc:${flowId}.0001-R`, 2100, 1600)
    }],
    ['a failed layout save', async (center: Session, flowId: string) => {
      server.saveAnswer = 'fail'
      center.moveNode(flowId, `doc:${flowId}.0001-R`, 2100, 1600)
    }],
    ['a reset (reloadDraft)', async (center: Session, flowId: string) => center.reloadDraft(flowId)],
  ] as const

  it.each(REREADS)('a read after %s replaced the Graph: the old whole Flow is not laid over the new one', async (_title, reread) => {
    const flowId = 'p.ui.0023'
    const { center, positions, scatter } = await openSession(flowId, documentsFlow(flowId))
    expect(scatter()).toBe(true)
    await settleSaves()
    server.hold.whole = true
    const pending = center.autoLayout(flowId, 'horizontal')
    await flushPromises()
    // The held read is the Flow as it was asked (R, CH, D 0003); the server has D 0004 since.
    expect(server.heldCount()).toBe(1)
    server.flows[flowId] = documentsFlow(flowId, { replaced: true })
    const reads = server.collapsedReads()
    await reread(center, flowId)
    await settleSaves()
    expect(server.collapsedReads()).toBe(reads + 1)
    server.saveAnswer = 'ok'
    const replaced = { [`doc:${flowId}.0001-R`]: [0, 0], [`doc:${flowId}.0004-D`]: [520, 0] }
    expect(positions()).toEqual(replaced)
    const savesBefore = savesOf(flowId).length

    server.release()
    expect(await pending).toBe(false)
    expect(positions()).toEqual(replaced)
    await settleSaves()
    expect(savesOf(flowId)).toHaveLength(savesBefore)
    // The re-read reset the history; the late answer added nothing to it.
    expect(center.undoEdit(flowId)).toBe(false)

    // The next 자동정렬 reads the new Flow whole: D 0004 back at (520, 0).
    expect(scatter()).toBe(true)
    expect(await center.autoLayout(flowId, 'horizontal')).toBe(true)
    expect(positions()).toEqual(replaced)
  })

  it('a re-read that turned the Flow into a sequence: the old documents read lends it no order and no roles', async () => {
    const flowId = 'p.ui.0024'
    const { center, positions, scatter } = await openSession(flowId, documentsFlow(flowId))
    server.hold.whole = true
    const pending = center.autoLayout(flowId, 'horizontal')
    await flushPromises()
    expect(server.heldCount()).toBe(1)
    server.flows[flowId] = assignedFlow(flowId)
    center.retryGraph(flowId)
    await flushPromises()
    const assigned = serverShown(server.flows[flowId])
    expect(assigned).toEqual({ [`doc:${flowId}.0001-R`]: [0, 0], slot_31: [260, 0], [`doc:${flowId}.0003-D`]: [520, 180] })
    expect(positions()).toEqual(assigned)
    server.release()
    expect(await pending).toBe(false)
    expect(positions()).toEqual(assigned)
    await settleSaves()
    expect(savesOf(flowId)).toHaveLength(0)
    expect(center.undoEdit(flowId)).toBe(false)
    expect(scatter()).toBe(true)
    expect(await center.autoLayout(flowId, 'horizontal')).toBe(true)
    // flowgate.v02.0068 T#2: the chain R -> slot, then the shown document below it (its slot 0 is the hidden CH's).
    expect(positions()).toEqual({ [`doc:${flowId}.0001-R`]: [0, 0], slot_31: [260, 0], [`doc:${flowId}.0003-D`]: [520, 200] })
  })

  it('with nothing in between, the late answer still lays the cards out and saves them', async () => {
    const flowId = 'p.ui.0025'
    const { center, positions, scatter } = await openSession(flowId, documentsFlow(flowId))
    expect(scatter()).toBe(true)
    await settleSaves()
    server.hold.whole = true
    const pending = center.autoLayout(flowId, 'horizontal')
    await flushPromises()
    expect(server.heldCount()).toBe(1)
    server.release()
    expect(await pending).toBe(true)
    expect(positions()).toEqual(serverShown(server.flows[flowId]))
    await settleSaves()
    expect(stored(flowId)).toEqual(serverShown(server.flows[flowId]))
    expect(center.undoEdit(flowId)).toBe(true)
  })
})

type Session = ReturnType<typeof createDesignerCenterSession>
