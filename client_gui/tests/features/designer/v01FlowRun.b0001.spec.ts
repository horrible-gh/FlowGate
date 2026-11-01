// flowgate.v02 B0001 T#2 — 다음 단계 / 전체 실행 on a v0.1 Flow (shell/v01FlowRun.ts): which document runs, with which
// scope, that a full run follows the run to its end and waits for approval, and that it stops (never repeats) on a
// refusal, a failed run, a rejection or a stop.
import { describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import { createV01FlowRun, v01PickTarget, v01RunOrder } from '@/features/designer/shell/v01FlowRun'

const node = (id: string, typeCode: string, order: number, status: string | null): FlowGraphNode =>
  ({
    id,
    slot: { itemId: order, itemSeq: order, order, type: typeCode, label: null, derivedStatus: null, protected: false, sourceWpCardId: null },
    document: { docId: `g.0001.${id}`, typeCode, seq: order, title: id, docReviewStatus: status, status: null, supersedes: [] },
  }) as unknown as FlowGraphNode

const graph = (nodes: FlowGraphNode[], ownership = 'v01'): FlowGraph => ({ flowId: 'g', nodes, edges: [], ownership }) as unknown as FlowGraph

describe('v01RunOrder / v01PickTarget', () => {
  const g = graph([node('c', 'DS', 3, null), node('r', 'R', 0, 'approved'), node('a', 'N', 1, 'approved'), node('b', 'T', 2, 'rejected')])
  const status = (n: FlowGraphNode) => n.document!.docReviewStatus

  it('lists the work documents in slot order and skips requirement cards', () => {
    expect(v01RunOrder(g).map((n) => n.id)).toEqual(['a', 'b', 'c'])
  })

  it('a Graph-owned Flow has no v0.1 order', () => {
    expect(v01RunOrder(graph([node('a', 'N', 1, null)], 'graph'))).toEqual([])
  })

  it('picks the first unapproved document: rejected -> rework, else edit; approved / wf_done are skipped', () => {
    expect(v01PickTarget(v01RunOrder(g), status)).toEqual({ docId: 'g.0001.b', scope: 'rework' })
    expect(v01PickTarget(v01RunOrder(graph([node('a', 'N', 1, 'wf_done'), node('b', 'T', 2, 'pending_review')])), status)).toEqual({ docId: 'g.0001.b', scope: 'edit' })
    expect(v01PickTarget(v01RunOrder(graph([node('a', 'N', 1, 'approved')])), status)).toBeNull()
  })
})

type Activity = { active: boolean; runId: string | null; status: string | null }

function harness(statuses: Record<string, string>, activity: Activity[] = []) {
  const calls: string[] = []
  const queue = [...activity]
  const deps = {
    readStatus: vi.fn(async (docId: string): Promise<string | null> => statuses[docId] ?? null),
    readActivity: vi.fn(async (): Promise<Activity> => queue.shift() ?? { active: false, runId: null, status: 'finished' }),
    start: vi.fn(async (_flow: string, docId: string, scope: string) => {
      calls.push(`${docId}:${scope}`)
      return { runId: `run-${calls.length}` }
    }),
    sleep: vi.fn(async () => {}),
    pollMs: 1,
    maxWaitMs: 5,
  }
  return { deps, calls, runner: createV01FlowRun(deps) }
}

describe('createV01FlowRun — 다음 단계', () => {
  it('starts exactly one run on the first unapproved document and does not wait for it', async () => {
    const h = harness({ 'g.0001.a': 'approved', 'g.0001.b': 'rejected' })
    const result = await h.runner.run('g', graph([node('a', 'N', 1, null), node('b', 'T', 2, null)]), 'next')
    expect(result).toEqual({ ok: true })
    expect(h.calls).toEqual(['g.0001.b:rework'])
  })

  it('says none when every document is approved', async () => {
    const h = harness({ 'g.0001.a': 'approved' })
    expect(await h.runner.run('g', graph([node('a', 'N', 1, null)]), 'next')).toEqual({ ok: false, reason: 'none' })
    expect(h.calls).toEqual([])
  })

  it('starts nothing while the group has an AI run, and a refused start is reported', async () => {
    const busy = harness({}, [{ active: true, runId: 'x', status: 'running' }])
    expect(await busy.runner.run('g', graph([node('a', 'N', 1, null)]), 'next')).toEqual({ ok: false, reason: 'ai_running' })
    expect(busy.deps.start).not.toHaveBeenCalled()

    const refused = harness({})
    const boom = new Error('409')
    refused.deps.start.mockRejectedValueOnce(boom)
    expect(await refused.runner.run('g', graph([node('a', 'N', 1, null)]), 'next')).toEqual({ ok: false, reason: 'refused', error: boom })
  })

  it('a second command of the same Flow is refused while one is answered (no duplicate run)', async () => {
    const h = harness({})
    let release: () => void = () => {}
    h.deps.start.mockImplementationOnce(() => new Promise((resolve) => { release = () => resolve({ runId: 'r' }) }))
    const g = graph([node('a', 'N', 1, null)])
    const first = h.runner.run('g', g, 'next')
    await vi.waitFor(() => expect(h.runner.busy.g).toBe(true))
    expect(await h.runner.run('g', g, 'all')).toEqual({ ok: false, reason: 'busy' })
    release()
    expect(await first).toEqual({ ok: true })
    expect(h.runner.busy.g).toBeUndefined()
  })
})

describe('createV01FlowRun — 전체 실행', () => {
  it('runs the documents in order, following each run to its end and waiting for the approval before the next', async () => {
    const h = harness({}, [
      { active: false, runId: null, status: null }, // before a
      { active: true, runId: 'run-1', status: 'running' },
      { active: false, runId: 'run-1', status: 'finished' },
      { active: false, runId: null, status: null }, // before b
      { active: false, runId: 'run-2', status: 'finished' },
    ])
    // a is approved only after a few reads: the person approves, nothing here does.
    let reads = 0
    h.deps.readStatus.mockImplementation(async (docId: string) => {
      if (docId === 'g.0001.a') return ++reads > 3 && h.calls.length >= 1 ? 'approved' : 'pending_review'
      return h.calls.length >= 2 ? 'approved' : 'draft'
    })
    const result = await h.runner.run('g', graph([node('a', 'N', 1, null), node('b', 'T', 2, null)]), 'all')
    expect(result).toEqual({ ok: true })
    expect(h.calls).toEqual(['g.0001.a:edit', 'g.0001.b:edit'])
    expect(h.deps.sleep).toHaveBeenCalled()
  })

  it('stops on a failed run, a rejection, a wait that never ends, and a cancel — never repeating by itself', async () => {
    const g = graph([node('a', 'N', 1, null)])
    const failed = harness({ 'g.0001.a': 'draft' }, [{ active: false, runId: null, status: null }, { active: false, runId: 'run-1', status: 'failed' }])
    expect(await failed.runner.run('g', g, 'all')).toEqual({ ok: false, reason: 'failed' })
    expect(failed.calls).toHaveLength(1)

    const rejected = harness({ 'g.0001.a': 'draft' })
    rejected.deps.readStatus.mockImplementation(async () => (rejected.calls.length ? 'rejected' : 'draft'))
    expect(await rejected.runner.run('g', g, 'all')).toEqual({ ok: false, reason: 'rejected' })
    expect(rejected.calls).toHaveLength(1)

    const waiting = harness({})
    waiting.deps.readStatus.mockImplementation(async () => 'pending_review')
    expect(await waiting.runner.run('g', g, 'all')).toEqual({ ok: false, reason: 'timeout' })
    expect(waiting.calls).toHaveLength(1)

    const cancelled = harness({})
    cancelled.deps.readStatus.mockImplementation(async () => {
      cancelled.runner.cancel('g')
      return 'pending_review'
    })
    expect(await cancelled.runner.run('g', g, 'all')).toEqual({ ok: false, reason: 'stopped' })
  })
})
