/**
 * flowgate.v02test.0001 B0001 §2 / T#2 — the menu's 다음 단계 · 전체 실행 on a v0.1 Flow (run/v01Run.ts):
 * the pick (first unapproved work document, rejected = rework), one start for 다음 단계, a followed series for 전체 실행
 * that never approves, never repeats by itself and stops on a refusal; no start while an AI run owns the group.
 */
import { describe, expect, it } from 'vitest'
import { runV01Flow, v01Pick, type V01Item, type V01RunDeps, type V01RunScope } from '@/features/designer/center/run/v01Run'

const ITEMS: V01Item[] = [
  { docId: 'g.0003-DS', order: 3, docType: 'DS' },
  { docId: 'g.0001-R', order: 1, docType: 'R' },
  { docId: 'g.0002-N', order: 2, docType: 'N' },
  { docId: 'g.0004-T', order: 4, docType: 'T' },
]

const statuses = (entries: Record<string, string | null>) => new Map<string, string | null>(Object.entries(entries))

describe('v01Pick — first unapproved work document in the sequence order', () => {
  it('skips non-work documents and approved / wf_done ones', () => {
    expect(v01Pick(ITEMS, statuses({ 'g.0002-N': 'approved', 'g.0003-DS': 'wf_done' }))).toEqual({ docId: 'g.0004-T', scope: 'edit', status: null })
  })
  it('a rejected document is reworked, any other status is run', () => {
    expect(v01Pick(ITEMS, statuses({ 'g.0002-N': 'rejected' }))).toEqual({ docId: 'g.0002-N', scope: 'rework', status: 'rejected' })
    expect(v01Pick(ITEMS, statuses({ 'g.0002-N': 'pending_review' }))?.scope).toBe('edit')
  })
  it('is null when every work document is approved', () => {
    expect(v01Pick(ITEMS, statuses({ 'g.0002-N': 'approved', 'g.0003-DS': 'approved', 'g.0004-T': 'approved' }))).toBeNull()
  })
})

interface FakeOptions {
  active?: boolean
  refuse?: unknown
  endReason?: string | null
  afterRun?: (docId: string, scope: V01RunScope, status: Record<string, string | null>) => void
  /** Called on every wait: the person acts (approves / rejects) while the run waits. */
  onWait?: (status: Record<string, string | null>) => void
}

/** A fake v0.1 group: a run leaves the documents the way `afterRun` says, a wait is a poll of the person's actions. */
function fake(initial: Record<string, string | null>, options: FakeOptions = {}) {
  const status = { ...initial }
  const starts: Array<[string, V01RunScope]> = []
  let running = false
  const deps: V01RunDeps = {
    items: () => ITEMS,
    readStatus: async (docId) => ({ status: status[docId] ?? null, disposed: false }),
    activity: async () => ({ active: options.active === true || running, runId: null }),
    start: async (docId, scope) => {
      if (options.refuse !== undefined) throw options.refuse
      starts.push([docId, scope])
      running = true
      options.afterRun?.(docId, scope, status)
      return `run-${starts.length}`
    },
    endReason: async () => (options.endReason === undefined ? 'completed' : options.endReason),
    current: () => true,
    sleep: async () => {
      running = false
      options.onWait?.(status)
    },
  }
  return { deps, starts, status }
}

describe('runV01Flow — 다음 단계', () => {
  it('starts exactly one run on the first unapproved work document and does not wait for it', async () => {
    const { deps, starts } = fake({ 'g.0002-N': 'approved' })
    const result = await runV01Flow('next', deps)
    expect(starts).toEqual([['g.0003-DS', 'edit']])
    expect(result).toMatchObject({ started: true, stop: 'done' })
  })
  it('reworks a rejected document', async () => {
    const { deps, starts } = fake({ 'g.0002-N': 'rejected' })
    await runV01Flow('next', deps)
    expect(starts).toEqual([['g.0002-N', 'rework']])
  })
  it('starts nothing while an AI run owns the group (no duplicate run)', async () => {
    const { deps, starts } = fake({}, { active: true })
    expect(await runV01Flow('next', deps)).toEqual({ started: false, stop: 'busy' })
    expect(starts).toEqual([])
  })
  it('a refused start stops with the refusal and starts nothing else', async () => {
    const refusal = { code: 'run_already_active', status: 409 }
    const { deps, starts } = fake({}, { refuse: refusal })
    expect(await runV01Flow('next', deps)).toEqual({ started: false, stop: 'refused', error: refusal })
    expect(starts).toEqual([])
  })
  it('does nothing when every work document is approved', async () => {
    const { deps, starts } = fake({ 'g.0002-N': 'approved', 'g.0003-DS': 'approved', 'g.0004-T': 'approved' })
    expect(await runV01Flow('next', deps)).toEqual({ started: false, stop: 'done' })
    expect(starts).toEqual([])
  })
})

describe('runV01Flow — 전체 실행', () => {
  it('goes document by document in order, waiting for the approval it never gives itself', async () => {
    const order = ['g.0002-N', 'g.0003-DS', 'g.0004-T']
    let waits = 0
    const { deps, starts } = fake({}, {
      afterRun: (docId, _scope, status) => {
        status[docId] = 'pending_review'
      },
      // The person approves the document the run waits for.
      onWait: (status) => {
        waits += 1
        const waiting = order.find((id) => status[id] === 'pending_review')
        if (waiting && waits % 2 === 0) status[waiting] = 'approved'
      },
    })
    const result = await runV01Flow('all', deps)
    expect(starts.map(([doc]) => doc)).toEqual(order)
    expect(starts.every(([, scope]) => scope === 'edit')).toBe(true)
    expect(result).toEqual({ started: true, stop: 'done' })
    expect(waits).toBeGreaterThan(0)
  })
  it('a rejection while waiting turns the next start into a rework of that document', async () => {
    let rejected = false
    const { deps, starts } = fake({ 'g.0003-DS': 'approved', 'g.0004-T': 'approved' }, {
      afterRun: (docId, scope, status) => {
        status[docId] = scope === 'rework' ? 'approved' : 'pending_review'
      },
      onWait: (status) => {
        if (!rejected && status['g.0002-N'] === 'pending_review') {
          rejected = true
          status['g.0002-N'] = 'rejected'
        }
      },
    })
    await runV01Flow('all', deps)
    expect(starts).toEqual([['g.0002-N', 'edit'], ['g.0002-N', 'rework']])
  })
  it('a run that did not complete stops the series — not repeated by itself', async () => {
    const { deps, starts } = fake({}, { endReason: 'failed' })
    expect(await runV01Flow('all', deps)).toEqual({ started: true, stop: 'not_completed' })
    expect(starts).toHaveLength(1)
  })
  it('a document that did not move after its run stops the series (no automatic repeat)', async () => {
    const { deps, starts } = fake({})
    expect(await runV01Flow('all', deps)).toEqual({ started: true, stop: 'no_progress' })
    expect(starts).toHaveLength(1)
  })
  it('stops when the Flow is no longer the current one', async () => {
    let calls = 0
    const { deps } = fake({})
    const result = await runV01Flow('all', { ...deps, current: () => ++calls < 2 })
    expect(result.stop).toBe('closed')
  })
  it.each(['next', 'all'] as const)('%s: a Flow closed while the statuses are being read starts nothing (0006-TR rev1)', async (mode) => {
    let open = true
    const { deps, starts } = fake({})
    const result = await runV01Flow(mode, {
      ...deps,
      current: () => open,
      // The tab is closed (or the Project switched) while the read is still pending.
      readStatus: async (docId) => {
        const read = await deps.readStatus(docId)
        open = false
        return read
      },
    })
    expect(starts).toEqual([])
    expect(result).toEqual({ started: false, stop: 'closed' })
  })
  it('a document awaiting approval from the start is waited for, not run again', async () => {
    const { deps, starts } = fake({ 'g.0002-N': 'pending_review', 'g.0003-DS': 'approved', 'g.0004-T': 'approved' }, {
      onWait: (status) => {
        status['g.0002-N'] = 'approved'
      },
    })
    const result = await runV01Flow('all', deps)
    expect(starts).toEqual([])
    expect(result).toEqual({ started: false, stop: 'done' })
  })
})


describe('runV01Flow — TS (test scenario) is written and rewritten by the menu (0006-TR rev3)', () => {
  const WITH_TS: V01Item[] = [
    { docId: 'g.0004-T', order: 4, docType: 'T' },
    { docId: 'g.0005-TS', order: 5, docType: 'TS' },
    { docId: 'g.0006-TSR', order: 6, docType: 'TSR' },
  ]
  const run = (mode: 'next' | 'all', initial: Record<string, string | null>, afterRun?: (docId: string, status: Record<string, string | null>) => void) => {
    const status = { ...initial }
    const starts: Array<[string, V01RunScope]> = []
    let running = false
    const deps: V01RunDeps = {
      items: () => WITH_TS,
      readStatus: async (docId) => ({ status: status[docId] ?? null, disposed: false }),
      activity: async () => ({ active: running, runId: null }),
      start: async (docId, scope) => {
        starts.push([docId, scope])
        running = true
        afterRun?.(docId, status)
        return `run-${starts.length}`
      },
      endReason: async () => 'completed',
      current: () => true,
      sleep: async () => {
        running = false
      },
    }
    return { promise: runV01Flow(mode, deps), starts }
  }

  it('the pick reaches a draft TS after an approved T, and a rejected TS is reworked', () => {
    expect(v01Pick(WITH_TS, statuses({ 'g.0004-T': 'approved', 'g.0005-TS': 'draft' }))).toEqual({ docId: 'g.0005-TS', scope: 'edit', status: 'draft' })
    expect(v01Pick(WITH_TS, statuses({ 'g.0004-T': 'approved', 'g.0005-TS': 'rejected' }))).toEqual({ docId: 'g.0005-TS', scope: 'rework', status: 'rejected' })
  })
  it('다음 단계 starts the TS write (draft) and the TS rewrite (rejected); never the TSR', async () => {
    const draft = run('next', { 'g.0004-T': 'approved', 'g.0005-TS': 'draft' })
    expect(await draft.promise).toMatchObject({ started: true, stop: 'done' })
    expect(draft.starts).toEqual([['g.0005-TS', 'edit']])
    const rejected = run('next', { 'g.0004-T': 'approved', 'g.0005-TS': 'rejected' })
    await rejected.promise
    expect(rejected.starts).toEqual([['g.0005-TS', 'rework']])
  })
  it('전체 실행 starts the TS (draft and rejected) after the approved T and then ends at the TSR', async () => {
    for (const [state, scope] of [['draft', 'edit'], ['rejected', 'rework']] as const) {
      const { promise, starts } = run('all', { 'g.0004-T': 'approved', 'g.0005-TS': state }, (docId, status) => {
        status[docId] = 'approved'
      })
      expect(await promise).toMatchObject({ started: true, stop: 'done' })
      expect(starts).toEqual([['g.0005-TS', scope]])
    }
  })
})
