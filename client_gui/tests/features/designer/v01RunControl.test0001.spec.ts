/**
 * flowgate.v02test.0001 B0001 §2 / T0004 T#2 — 다음 단계 / 전체 실행 on a v0.1 Flow (center/run/v01RunControl.ts): the menu
 * commands take over what the header's ▶ 실행 / ↻ 재작업 did — the first unapproved work document runs once (edit, or
 * rework after a rejection); 전체 실행 goes on in document order after each run ended; nothing is approved or repeated
 * from here, and a run, a refusal or a rejection that stops it leaves the menu to run it again.
 */
import { describe, expect, it, vi } from 'vitest'
import { toReviewRunResult } from '@/features/document/api/review'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import {
  runV01Flow,
  v01Closed,
  v01RunDocs,
  v01Scope,
  type V01Activity,
  type V01RunEnd,
  type V01RunDeps,
  type V01RunScope,
} from '@/features/designer/center/run/v01RunControl'

const F = 'p.ui.0001'

function node(docId: string, typeCode: string, order: number, status: string | null, supersedes: string[] = []): FlowGraphNode {
  return {
    id: `n-${docId}`,
    kind: 'task',
    title: docId,
    x: 0,
    y: 0,
    docType: typeCode,
    document: { docId, typeCode, seq: order, title: docId, docReviewStatus: status, status: null, supersedes },
    slot: { itemId: order, itemSeq: order, order, type: typeCode, label: null, derivedStatus: null, protected: false, sourceWpCardId: null },
  }
}

const graphOf = (...nodes: FlowGraphNode[]): FlowGraph => ({ flowId: F, nodes, edges: [], ownership: 'v01' })

interface Fake extends V01RunDeps {
  starts: Array<[string, V01RunScope]>
  statuses: Record<string, string | null>
  /** What a started run does to its document (the run ended by then). */
  effect: (docId: string, scope: V01RunScope) => string | null
  active: V01Activity[]
  /** How each started run ended, by its order (run-1, run-2 ...): the real /ai-invoke/{run_id} record; default = ended well. */
  ends: Record<string, V01RunEnd>
}

function fake(statuses: Record<string, string | null>, effect: Fake['effect'] = () => 'pending_review'): Fake {
  const f: Fake = {
    starts: [],
    statuses,
    effect,
    active: [],
    ends: {},
    pollMs: 0,
    start: vi.fn(async (_flow: string, docId: string, scope: V01RunScope) => {
      f.starts.push([docId, scope])
      f.statuses[docId] = f.effect(docId, scope)
      return `run-${f.starts.length}`
    }),
    readRun: vi.fn(async (runId: string) => f.ends[runId] ?? { finished: true, succeeded: true }),
    readDocument: vi.fn(async (docId: string) => ({ reviewStatus: f.statuses[docId] ?? null, disposed: false })),
    // /ai-invoke/active answers { active: false } with no status once a run ended, failed or not.
    readActivity: vi.fn(async () => f.active.shift() ?? { active: false, status: null }),
  }
  return f
}

const run = (deps: Fake, graph: FlowGraph | null, mode: 'next' | 'all', alive = () => true) =>
  runV01Flow({ flowId: F, mode, graph: () => graph, deps, alive, sleep: async () => {} })

describe('v0.1 run — which documents, in which order', () => {
  it('takes the work documents (N · T · DS …) in sequence order, not the requirement or test ones', () => {
    const graph = graphOf(node('d3', 'DS', 3, null), node('r', 'R', 0, 'approved'), node('t1', 'T', 2, null), node('n1', 'N', 1, null), node('ts', 'TS', 4, null))
    expect(v01RunDocs(graph).map((doc) => doc.docId)).toEqual(['n1', 't1', 'd3'])
    expect(v01RunDocs(null)).toEqual([])
  })

  it('skips a document a later one replaced', () => {
    const graph = graphOf(node('t1', 'T', 1, 'rejected'), node('t2', 'T', 2, null, ['t1']))
    expect(v01RunDocs(graph).map((doc) => doc.docId)).toEqual(['t2'])
  })

  it('rework only after a rejection; approved / wf_done are closed', () => {
    expect(v01Scope('rejected')).toBe('rework')
    for (const status of ['pending_review', 'draft', null]) expect(v01Scope(status)).toBe('edit')
    expect([v01Closed('approved'), v01Closed('wf_done'), v01Closed('rejected'), v01Closed(null)]).toEqual([true, true, false, false])
  })
})

describe('다음 단계 on a v0.1 Flow', () => {
  const graph = graphOf(node('n1', 'N', 1, 'approved'), node('t1', 'T', 2, null), node('d1', 'DS', 3, null))

  it('runs the first unapproved document once (edit), and nothing else', async () => {
    const f = fake({ n1: 'approved', t1: 'draft', d1: null })
    expect(await run(f, graph, 'next')).toEqual({ kind: 'started' })
    expect(f.starts).toEqual([['t1', 'edit']])
  })

  it('runs a rejected document as rework', async () => {
    const f = fake({ n1: 'approved', t1: 'rejected', d1: null })
    expect(await run(f, graph, 'next')).toEqual({ kind: 'started' })
    expect(f.starts).toEqual([['t1', 'rework']])
  })

  it('judges by the document read now, not by an older Graph read', async () => {
    const f = fake({ n1: 'approved', t1: 'approved', d1: 'rejected' })
    await run(f, graph, 'next')
    expect(f.starts).toEqual([['d1', 'rework']])
  })

  it('starts nothing while an AI run owns the group (no duplicate run)', async () => {
    const f = fake({ n1: 'approved', t1: null, d1: null })
    f.active = [{ active: true, status: 'running' }]
    expect(await run(f, graph, 'next')).toEqual({ kind: 'busy' })
    expect(f.starts).toEqual([])
  })

  it('has nothing to run when every work document is approved', async () => {
    const f = fake({ n1: 'approved', t1: 'wf_done', d1: 'approved' })
    expect(await run(f, graph, 'next')).toEqual({ kind: 'none' })
    expect(f.starts).toEqual([])
  })

  it('a refused start is told as failed (nothing repeats)', async () => {
    const f = fake({ n1: 'approved', t1: null, d1: null })
    const refusal = new Error('409')
    f.start = vi.fn(async () => {
      throw refusal
    })
    expect(await run(f, graph, 'next')).toEqual({ kind: 'failed', started: 0, error: refusal })
    expect(f.start).toHaveBeenCalledTimes(1)
  })

  it('a disposed group is refused before anything starts', async () => {
    const f = fake({ n1: 'approved', t1: null, d1: null })
    f.readDocument = vi.fn(async () => ({ reviewStatus: null, disposed: true }))
    const outcome = await run(f, graph, 'next')
    expect(outcome.kind).toBe('failed')
    expect(f.starts).toEqual([])
  })
})

describe('전체 실행 on a v0.1 Flow', () => {
  const graph = graphOf(node('t1', 'T', 1, null), node('t2', 'T', 2, null), node('t3', 'T', 3, null))

  it('goes through the documents in order, each after the previous run ended and was approved', async () => {
    const f = fake({ t1: null, t2: null, t3: null }, () => 'pending_review')
    // A person approves whatever was run, while the command waits for it.
    let reads = 0
    const readDocument = f.readDocument
    f.readDocument = vi.fn(async (docId: string) => {
      if (++reads % 3 === 0 && f.statuses[docId] === 'pending_review') f.statuses[docId] = 'approved'
      return readDocument(docId)
    })
    const outcome = await run(f, graph, 'all')
    expect(outcome).toEqual({ kind: 'completed', started: 3 })
    expect(f.starts.map(([docId]) => docId)).toEqual(['t1', 't2', 't3'])
  })

  it('never approves anything itself: a document waiting for approval is waited for until the Flow is closed', async () => {
    const f = fake({ t1: null, t2: null, t3: null }, () => 'pending_review')
    let alive = true
    let sleeps = 0
    const outcome = await runV01Flow({
      flowId: F,
      mode: 'all',
      graph: () => graph,
      deps: f,
      alive: () => alive,
      sleep: async () => {
        if (++sleeps > 3) alive = false
      },
    })
    expect(outcome).toEqual({ kind: 'stopped', started: 1, reason: 'closed' })
    expect(f.starts).toEqual([['t1', 'edit']])
  })

  it('waits for a run to end before reading the document (the start answer is not the end)', async () => {
    const f = fake({ t1: null, t2: 'approved', t3: 'approved' }, () => 'approved')
    f.active = [{ active: false, status: null }, { active: true, status: 'running' }, { active: true, status: 'running' }]
    expect(await run(f, graph, 'all')).toEqual({ kind: 'completed', started: 1 })
    expect(f.readActivity).toHaveBeenCalledTimes(5)
  })

  it('stops at a rejection and does not run it again by itself', async () => {
    const f = fake({ t1: null, t2: null, t3: null }, () => 'rejected')
    expect(await run(f, graph, 'all')).toEqual({ kind: 'stopped', started: 1, reason: 'rejected' })
    expect(f.starts).toEqual([['t1', 'edit']])
  })

  it('stops when the run failed (its own record says so; /active only says it is no longer active), or left the document as it was', async () => {
    const failed = fake({ t1: null, t2: null, t3: null }, () => null)
    failed.ends['run-1'] = { finished: true, succeeded: false }
    expect(await run(failed, graph, 'all')).toEqual({ kind: 'stopped', started: 1, reason: 'failed' })
    const unchanged = fake({ t1: 'draft', t2: null, t3: null }, () => 'draft')
    expect(await run(unchanged, graph, 'all')).toEqual({ kind: 'stopped', started: 1, reason: 'failed' })
    expect(unchanged.starts).toHaveLength(1)
  })

  it('a failed run on a document that stays pending_review stops at once instead of waiting for an approval (the lock is released)', async () => {
    const f = fake({ t1: 'pending_review', t2: null, t3: null }, () => 'pending_review')
    f.ends['run-1'] = { finished: true, succeeded: false }
    const outcome = await run(f, graph, 'all')
    expect(outcome).toEqual({ kind: 'stopped', started: 1, reason: 'failed' })
    expect(f.starts).toEqual([['t1', 'edit']])
    // Nothing repeated by itself: the menu command is the only way to run it again.
    expect(f.start).toHaveBeenCalledTimes(1)
  })

  it('a run that ended well on a document waiting for approval still waits for the person (not a failure)', async () => {
    const f = fake({ t1: 'pending_review', t2: null, t3: null }, () => 'pending_review')
    let alive = true
    let sleeps = 0
    const outcome = await runV01Flow({
      flowId: F,
      mode: 'all',
      graph: () => graph,
      deps: f,
      alive: () => alive,
      sleep: async () => {
        if (++sleeps > 3) alive = false
      },
    })
    expect(outcome).toEqual({ kind: 'stopped', started: 1, reason: 'closed' })
  })

  it('a one-off error reading the run record is read again: the failure is found, the command stops and the lock is released', async () => {
    const f = fake({ t1: 'pending_review', t2: null, t3: null }, () => 'pending_review')
    let reads = 0
    f.readRun = vi.fn(async () => {
      if (++reads === 1) throw new Error('network')
      return { finished: true, succeeded: false }
    })
    expect(await run(f, graph, 'all')).toEqual({ kind: 'stopped', started: 1, reason: 'failed' })
    expect(f.readRun).toHaveBeenCalledTimes(2)
    expect(f.start).toHaveBeenCalledTimes(1)
  })

  it('a one-off error reading the record of a run that ended well still waits for the approval afterwards', async () => {
    const f = fake({ t1: null, t2: null, t3: null }, () => 'pending_review')
    let reads = 0
    f.readRun = vi.fn(async () => {
      if (++reads === 1) throw new Error('network')
      return { finished: true, succeeded: true }
    })
    let alive = true
    let sleeps = 0
    const outcome = await runV01Flow({ flowId: F, mode: 'all', graph: () => graph, deps: f, alive: () => alive, sleep: async () => { if (++sleeps > 4) alive = false } })
    expect(outcome).toEqual({ kind: 'stopped', started: 1, reason: 'closed' })
    expect(f.readRun).toHaveBeenCalledTimes(2)
  })

  it('a run record that stays unreadable stops the command as failed (no endless wait; the menu runs it again)', async () => {
    const f = fake({ t1: 'pending_review', t2: null, t3: null }, () => 'pending_review')
    f.readRun = vi.fn(async () => {
      throw new Error('404 run_not_found')
    })
    expect(await run(f, graph, 'all')).toEqual({ kind: 'stopped', started: 1, reason: 'failed' })
    expect(f.readRun).toHaveBeenCalledTimes(3)
    expect(f.start).toHaveBeenCalledTimes(1)
  })

  it('stops with the refusal when a start is refused midway', async () => {
    const f = fake({ t1: null, t2: null, t3: null }, () => 'approved')
    const refusal = new Error('423')
    let count = 0
    const start = f.start
    f.start = vi.fn(async (flow: string, docId: string, scope: V01RunScope) => {
      if (++count === 2) throw refusal
      return start(flow, docId, scope)
    })
    expect(await run(f, graph, 'all')).toEqual({ kind: 'failed', started: 1, error: refusal })
  })

  it('a command whose Flow was closed starts nothing', async () => {
    const f = fake({ t1: null, t2: null, t3: null })
    expect(await run(f, graph, 'all', () => false)).toEqual({ kind: 'stopped', started: 0, reason: 'closed' })
    expect(f.starts).toEqual([])
  })
})

describe("a started run's own record (GET /ai-invoke/{run_id}) is what tells failure", () => {
  it('reads succeeded only from a finished run; a live run says nothing yet', () => {
    expect(toReviewRunResult('r1', { status: 'finished', succeeded: false, outcome: 'failed', end_reason: 'error' })).toMatchObject({ finished: true, succeeded: false, endReason: 'error' })
    expect(toReviewRunResult('r1', { status: 'finished', succeeded: true, outcome: 'complete' })).toMatchObject({ finished: true, succeeded: true })
    expect(toReviewRunResult('r1', { status: 'running', succeeded: false })).toMatchObject({ finished: false, succeeded: null })
    expect(toReviewRunResult('r1', null)).toMatchObject({ finished: false, succeeded: null })
  })
})
