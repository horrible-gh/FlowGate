import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getRequest, postRequest } from '@shared/api'
import {
  FINISHED_CARD_TTL_MS,
  MAX_FINISHED_CARDS,
  isFinishedCard,
  openTargetDocId,
  type AiInvokeRunEntry,
  useAiInvokeRunsStore,
} from './aiInvokeRuns'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const openTargetEntry = (
  overrides: Partial<Pick<AiInvokeRunEntry, 'pendingQDocIds' | 'reachedDocIds' | 'docRef'>> = {},
): AiInvokeRunEntry => ({
  pendingQDocIds: [],
  reachedDocIds: [],
  docRef: 'flowgate.default.0302.0001-R',
  ...overrides,
} as AiInvokeRunEntry)

describe('openTargetDocId', () => {
  it('prefers the first pending Q document', () => {
    const entry = openTargetEntry({
      pendingQDocIds: ['flowgate.default.0302.0005-Q'],
      reachedDocIds: ['flowgate.default.0302.0004-TR'],
    })

    expect(openTargetDocId(entry)).toBe('flowgate.default.0302.0005-Q')
  })

  it('selects the last reached document when there is no pending Q', () => {
    const entry = openTargetEntry({
      reachedDocIds: [
        'flowgate.default.0302.0004-TR',
        'flowgate.default.0302.0006-TR',
      ],
    })

    expect(openTargetDocId(entry)).toBe('flowgate.default.0302.0006-TR')
  })

  it('falls back to the source document when no reached document exists', () => {
    expect(openTargetDocId(openTargetEntry())).toBe('flowgate.default.0302.0001-R')
  })
})

describe('aiInvokeRuns store', () => {
  let store: ReturnType<typeof useAiInvokeRunsStore>

  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    store = useAiInvokeRunsStore()
    vi.clearAllMocks()
  })

  afterEach(() => {
    store.$dispose()
  })

  it('applies started, provider switched, and finished lifecycle events', () => {
    const groupId = 'flowgate.default.1001'

    store.applySse({
      kind: 'started',
      payload: {
        run_id: 'run-a',
        group_id: groupId,
        doc_ref: 'flowgate.default.1001.0001-R',
        started_at: '2026-07-13T00:00:00Z',
        provider_id: 'provider-a',
        provider_name: 'Provider A',
      },
    })
    store.applySse({
      kind: 'switched',
      payload: {
        run_id: 'run-a',
        group_id: groupId,
        from_provider_id: 'provider-a',
        from_provider_name: 'Provider A',
        to_provider_id: 'provider-b',
        to_provider_name: 'Provider B',
        attempt_no: 2,
        reason: 'fast_fail',
      },
    })
    store.applySse({
      kind: 'finished',
      payload: {
        run_id: 'run-a',
        group_id: groupId,
        outcome: 'complete',
        docs_reached: 1,
        reached_doc_ids: ['flowgate.default.1001.0002-TR'],
        last_message_received: true,
        last_message: 'done',
        duration_ms: 12_000,
      },
    })

    const run = store.runsByGroup[groupId]
    expect(run.phase).toBe('finished')
    expect(run.provider?.name).toBe('Provider B')
    expect(run.providerSwitches).toHaveLength(1)
    expect(run.providerSwitches[0].reason).toBe('fast_fail')
    expect(run.reachedDocIds).toEqual(['flowgate.default.1001.0002-TR'])
    expect(run.chainDocsReached).toBe(1) // legacy payload falls back to run progress
    expect(run.finishedPayload?.last_message).toBe('done')
  })

  it('keeps chain progress when a continuous hop starts with a new run id', () => {
    const groupId = 'flowgate.default.0357'
    store.trackStarted({
      run_id: 'run-hop-1', group_id: groupId, mode: 'continuous',
      docs_target: 5, chain_id: 'run-hop-1',
      chain_docs_target: 5, chain_docs_reached: 0,
    })
    store.trackStarted({
      run_id: 'run-hop-2', group_id: groupId, mode: 'continuous',
      docs_target: 4, chain_id: 'run-hop-1',
      chain_docs_target: 5, chain_docs_reached: 1,
    })

    const run = store.runsByGroup[groupId]
    expect(run.runId).toBe('run-hop-2')
    expect(run.docsTarget).toBe(4)
    expect(run.docsReachedSoFar).toBe(0)
    expect(run.chainId).toBe('run-hop-1')
    expect(run.chainDocsTarget).toBe(5)
    expect(run.chainDocsReached).toBe(1)
  })

  it('normalizes registration diagnostics from a finished payload', () => {
    const groupId = 'flowgate.default.1003'
    store.trackFinished({
      run_id: 'run-diagnostic',
      group_id: groupId,
      outcome: 'none',
      docs_reached: 0,
      register_errors: [{ status: 409, reason: 'dup_body', turn: 4 }],
      tool_call_misses: 2,
      turn_limit_exhausted: true,
      oracle_mismatch: false,
    })

    expect(store.runsByGroup[groupId].registerErrors).toEqual([
      { status: 409, reason: 'dup_body', turn: 4 },
    ])
    expect(store.runsByGroup[groupId].toolCallMisses).toBe(2)
    expect(store.runsByGroup[groupId].turnLimitExhausted).toBe(true)
  })

  it('keeps simultaneous groups isolated', () => {
    const groupA = 'flowgate.default.1001'
    const groupB = 'flowgate.default.1002'
    store.trackStarted({ run_id: 'run-a', group_id: groupA, doc_ref: 'a' })
    store.trackStarted({ run_id: 'run-b', group_id: groupB, doc_ref: 'b' })

    store.trackProviderSwitched({
      run_id: 'run-a',
      group_id: groupA,
      to_provider_id: 'provider-c',
      to_provider_name: 'Provider C',
      attempt_no: 2,
      reason: 'api_error',
    })
    store.trackFinished({
      run_id: 'run-a',
      group_id: groupA,
      outcome: 'partial',
      docs_reached: 1,
    })

    expect(store.runsByGroup[groupA].phase).toBe('finished')
    expect(store.runsByGroup[groupB].phase).toBe('running')
    expect(store.runsByGroup[groupB].runId).toBe('run-b')
    expect(store.runsByGroup[groupB].providerSwitches).toHaveLength(0)
    expect(store.activeCount).toBe(1)
  })

  it('removes a finished entry when dismissed', () => {
    const groupId = 'flowgate.default.1001'
    store.trackStarted({ run_id: 'run-a', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({ run_id: 'run-a', group_id: groupId, outcome: 'complete' })

    store.dismiss(groupId)

    expect(store.runsByGroup[groupId]).toBeUndefined()
  })

  it('marks only the polled group lost when its run returns 404', async () => {
    const groupA = 'flowgate.default.1001'
    const groupB = 'flowgate.default.1002'
    store.trackStarted({ run_id: 'run-a', group_id: groupA, doc_ref: 'a' })
    store.trackStarted({ run_id: 'run-b', group_id: groupB, doc_ref: 'b' })
    vi.mocked(getRequest).mockRejectedValueOnce({ response: { status: 404 } })

    await store.refresh(groupA)

    expect(store.runsByGroup[groupA].phase).toBe('lost')
    expect(store.runsByGroup[groupB].phase).toBe('running')
  })

  // ── 미니플레이어 (group 0252) ────────────────────────────────────────────────

  it('turns a user_paused finish into a paused card instead of a finished one', () => {
    const groupId = 'flowgate.default.2001'
    store.trackStarted({ run_id: 'run-p', group_id: groupId, doc_ref: 'r', mode: 'continuous' })
    store.trackFinished({
      run_id: 'run-p', group_id: groupId, outcome: 'partial',
      docs_reached: 4, docs_target: 6, end_reason: 'user_paused',
    })

    const run = store.runsByGroup[groupId]
    expect(run.phase).toBe('paused')
    expect(run.endReason).toBe('user_paused')
    expect(run.finishedAtMs).toBeNull()
    // Paused cards are not dismissible — resume (or another path) owns their removal.
    store.dismiss(groupId)
    expect(store.runsByGroup[groupId]).toBeDefined()
  })

  it('bootstraps running and paused cards from active-all and drops stale paused ones', async () => {
    const staleGroup = 'flowgate.default.2002'
    store.trackStarted({ run_id: 'run-x', group_id: staleGroup, doc_ref: 'r', mode: 'continuous' })
    store.trackFinished({ run_id: 'run-x', group_id: staleGroup, end_reason: 'user_paused' })
    expect(store.runsByGroup[staleGroup].phase).toBe('paused')

    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [{
          run_id: 'run-live', group_id: 'flowgate.default.2003', status: 'running',
          mode: 'continuous', doc_ref: 'flowgate.default.2003.0001-R',
          docs_target: 6, docs_reached_so_far: 2,
        }],
        paused: [{
          group_id: 'flowgate.default.2004', doc_ref: 'flowgate.default.2004.0001-R',
          mode: 'continuous', paused_by: 'u1', paused_at: '2026-07-17T00:00:00+09:00',
          docs_target: 6, docs_reached: 3,
          pending_q_doc_ids: ['flowgate.default.2004.0005-Q'],
        }],
      },
    } as any)

    await store.bootstrap()

    expect(store.runsByGroup['flowgate.default.2003'].phase).toBe('running')
    const paused = store.runsByGroup['flowgate.default.2004']
    expect(paused.phase).toBe('paused')
    expect(paused.docsReachedSoFar).toBe(3)
    expect(paused.pendingQDocIds).toEqual(['flowgate.default.2004.0005-Q'])
    // The stale paused card the server no longer reports is gone (P0008 실패 2 재조회).
    expect(store.runsByGroup[staleGroup]).toBeUndefined()
  })

  it('requests a boundary pause and reflects pause_requested', async () => {
    const groupId = 'flowgate.default.2005'
    store.trackStarted({ run_id: 'run-c', group_id: groupId, doc_ref: 'r', mode: 'continuous' })
    vi.mocked(postRequest).mockResolvedValueOnce({
      data: { ok: true, run_id: 'run-c', status: 'pause_requested', effective_at: 'step_boundary' },
    } as any)

    await store.pause(groupId)

    expect(vi.mocked(postRequest)).toHaveBeenCalledWith('/api/v1/ai-invoke/run-c/pause', {})
    expect(store.runsByGroup[groupId].phase).toBe('pause_requested')
  })

  it('never requests pause for a single-mode run', async () => {
    const groupId = 'flowgate.default.2006'
    store.trackStarted({ run_id: 'run-s', group_id: groupId, doc_ref: 'r', mode: 'single' })

    await store.pause(groupId)

    expect(vi.mocked(postRequest)).not.toHaveBeenCalled()
    expect(store.runsByGroup[groupId].phase).toBe('running')
  })

  it('replaces the paused card with the new run on resume', async () => {
    const groupId = 'flowgate.default.2007'
    store.trackFinished({
      run_id: 'run-old', group_id: groupId, doc_ref: 'r', end_reason: 'user_paused',
    })
    vi.mocked(postRequest).mockResolvedValueOnce({
      data: { ok: true, run_id: 'run-new', status: 'running', mode: 'continuous', docs_target: 2 },
    } as any)

    await store.resume(groupId)

    const run = store.runsByGroup[groupId]
    expect(run.runId).toBe('run-new')
    expect(run.phase).toBe('running')
    expect(run.docsTarget).toBe(2)
  })

  it('adopts the already-active run on resume 409 run_already_active', async () => {
    const groupId = 'flowgate.default.2008'
    store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
    vi.mocked(postRequest).mockRejectedValueOnce({
      response: { status: 409, data: { code: 'run_already_active', run_id: 'run-live' } },
    })
    vi.mocked(getRequest).mockResolvedValue({ data: { status: 'running', run_id: 'run-live' } } as any)

    await store.resume(groupId)

    expect(store.runsByGroup[groupId].runId).toBe('run-live')
    expect(store.runsByGroup[groupId].phase).toBe('running')
  })

  it('drops the card and re-bootstraps on resume 409 resume_conflict', async () => {
    const groupId = 'flowgate.default.2009'
    store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
    vi.mocked(postRequest).mockRejectedValueOnce({
      response: { status: 409, data: { code: 'resume_conflict' } },
    })
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, runs: [], paused: [] } } as any)

    await store.resume(groupId)

    expect(store.runsByGroup[groupId]).toBeUndefined()
    expect(vi.mocked(getRequest)).toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
  })

  it('correlates registered and answered questions to the group card', () => {
    const groupId = 'flowgate.default.2010'
    const qDocId = `${groupId}.0005-Q`
    store.trackStarted({ run_id: 'run-q', group_id: groupId, doc_ref: 'r', mode: 'continuous' })

    store.trackQuestionRegistered(qDocId)
    store.trackQuestionRegistered(qDocId) // duplicate signal folds
    expect(store.runsByGroup[groupId].pendingQDocIds).toEqual([qDocId])
    expect(store.awaitingQCount).toBe(1)

    // A Q for a group without a card is ignored (the document panel owns it).
    store.trackQuestionRegistered('flowgate.default.9999.0001-Q')
    expect(store.runsByGroup['flowgate.default.9999']).toBeUndefined()

    store.trackQuestionAnswered(qDocId)
    expect(store.runsByGroup[groupId].pendingQDocIds).toEqual([])
    expect(store.awaitingQCount).toBe(0)
  })
  it.each(['complete', 'none'] as const)(
    'keeps an exited hop_handoff live regardless of %s outcome',
    (outcome) => {
      const groupId = `flowgate.default.handoff.${outcome}`
      store.trackStarted({
        run_id: 'run-old', group_id: groupId, mode: 'continuous',
        docs_target: 1, chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 6,
      })

      store.trackFinished({
        run_id: 'run-old', group_id: groupId, mode: 'continuous',
        end_reason: 'exited', stop_code: 'hop_handoff', outcome,
        docs_reached: 1, docs_target: 1,
        chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 7,
      })

      const run = store.runsByGroup[groupId]
      expect(run.phase).toBe('running')
      expect(run.handoffPending).toBe(true)
      expect(run.chainDocsReached).toBe(7)
      expect(isFinishedCard(run)).toBe(false)
    },
  )

  it('keeps a pending handoff in active-all polling and adopts a new run id', async () => {
    const groupId = 'flowgate.default.handoff.adopt'
    store.trackStarted({
      run_id: 'run-old', group_id: groupId, mode: 'continuous',
      chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 6,
    })
    store.trackFinished({
      run_id: 'run-old', group_id: groupId, end_reason: 'exited',
      stop_code: 'hop_handoff', outcome: 'complete',
      chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 7,
    })
    vi.mocked(getRequest).mockResolvedValueOnce({ data: { runs: [], paused: [] } } as any)

    await store.refreshAllRunning()

    expect(vi.mocked(getRequest)).toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
    expect(store.runsByGroup[groupId].handoffPending).toBe(true)

    store.trackStarted({
      run_id: 'run-new', group_id: groupId, mode: 'continuous',
      chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 7,
    })
    expect(store.runsByGroup[groupId].runId).toBe('run-new')
    expect(store.runsByGroup[groupId].phase).toBe('running')
    expect(store.runsByGroup[groupId].handoffPending).toBe(false)
    expect(store.runsByGroup[groupId].chainDocsReached).toBe(7)
  })

  it('uses chain progress to infer a handoff when the run target is the 1/1 boundary', () => {
    const groupId = 'flowgate.default.handoff.chain-counter'
    store.trackStarted({
      run_id: 'run-old', group_id: groupId, mode: 'continuous',
      docs_target: 1, chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 6,
    })

    store.trackFinished({
      run_id: 'run-old', group_id: groupId, end_reason: 'exited', outcome: 'complete',
      docs_reached: 1, docs_target: 1,
      chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 7,
    })

    expect(store.runsByGroup[groupId].handoffPending).toBe(true)
  })

  it.each(['cancelled', 'timeout', 'user_paused'])(
    'does not treat non-exited %s as a handoff even with hop_handoff stop_code',
    (endReason) => {
      const groupId = `flowgate.default.handoff.${endReason}`
      store.trackStarted({ run_id: 'run-old', group_id: groupId, mode: 'continuous' })
      store.trackFinished({
        run_id: 'run-old', group_id: groupId, end_reason: endReason,
        stop_code: 'hop_handoff', outcome: 'none',
      })

      const run = store.runsByGroup[groupId]
      expect(run.handoffPending).toBe(false)
      expect(run.phase).toBe(endReason === 'user_paused' ? 'paused' : 'finished')
    },
  )

  it('ignores a delayed started frame from the settled old hop', () => {
    const groupId = 'flowgate.default.handoff.late-start'
    store.trackStarted({ run_id: 'run-old', group_id: groupId, mode: 'continuous' })
    store.trackFinished({
      run_id: 'run-old', group_id: groupId, end_reason: 'exited',
      stop_code: 'hop_handoff', outcome: 'complete',
    })

    store.trackStarted({ run_id: 'run-old', group_id: groupId, mode: 'continuous' })

    expect(store.runsByGroup[groupId].handoffPending).toBe(true)
    expect(store.runsByGroup[groupId].finishedPayload).toBeNull()
  })

  it('keeps a real final hop terminal when no handoff signal or remaining progress exists', () => {
    const groupId = 'flowgate.default.handoff.final'
    store.trackStarted({
      run_id: 'run-final', group_id: groupId, mode: 'continuous',
      docs_target: 1, chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 9,
    })
    store.trackFinished({
      run_id: 'run-final', group_id: groupId, end_reason: 'exited', outcome: 'complete',
      docs_reached: 1, docs_target: 1,
      chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 10,
    })

    expect(store.runsByGroup[groupId].phase).toBe('finished')
    expect(store.runsByGroup[groupId].handoffPending).toBe(false)
    expect(isFinishedCard(store.runsByGroup[groupId])).toBe(true)
  })

})

describe('aiInvokeRuns store — bounded handoff adoption', () => {
  let store: ReturnType<typeof useAiInvokeRunsStore>

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-01T12:00:00Z'))
    sessionStorage.clear()
    setActivePinia(createPinia())
    store = useAiInvokeRunsStore()
    vi.clearAllMocks()
    vi.mocked(getRequest).mockResolvedValue({ data: { runs: [], paused: [] } } as any)
  })

  afterEach(() => {
    store.$dispose()
    vi.useRealTimers()
  })

  it('waits through scheduled retries and two later successful polling misses before finishing', async () => {
    const groupId = 'flowgate.default.handoff.bounded'
    store.trackStarted({ run_id: 'run-old', group_id: groupId, mode: 'continuous' })
    store.trackFinished({
      run_id: 'run-old', group_id: groupId, end_reason: 'exited',
      stop_code: 'hop_handoff', outcome: 'none',
    })

    await vi.advanceTimersByTimeAsync(3_000)
    expect(store.runsByGroup[groupId].handoffPending).toBe(true)
    expect(store.runsByGroup[groupId].phase).toBe('running')

    await store.refreshAllRunning()
    expect(store.runsByGroup[groupId].handoffPending).toBe(true)

    await store.refreshAllRunning()
    expect(store.runsByGroup[groupId].handoffPending).toBe(false)
    expect(store.runsByGroup[groupId].phase).toBe('finished')
    expect(isFinishedCard(store.runsByGroup[groupId])).toBe(true)

    const activeAllCalls = vi.mocked(getRequest).mock.calls
      .filter(([path]) => path === '/api/v1/ai-invoke/active-all')
    expect(activeAllCalls.length).toBeGreaterThanOrEqual(6)
  })
})

describe('aiInvokeRuns store — finished-card TTL sweep', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sessionStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getRequest).mockResolvedValue({ data: {} } as any)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('auto-removes finished cards after the TTL but never paused ones', () => {
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-f', group_id: 'g.finished.1', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-f', group_id: 'g.finished.1', outcome: 'complete' })
    store.trackStarted({ run_id: 'run-p', group_id: 'g.paused.1', doc_ref: 'r', mode: 'continuous' })
    store.trackFinished({ run_id: 'run-p', group_id: 'g.paused.1', end_reason: 'user_paused' })

    vi.advanceTimersByTime(FINISHED_CARD_TTL_MS - 2_000)
    expect(store.runsByGroup['g.finished.1']).toBeDefined()

    vi.advanceTimersByTime(3_000)
    expect(store.runsByGroup['g.finished.1']).toBeUndefined()
    expect(store.runsByGroup['g.paused.1']?.phase).toBe('paused')

    store.$dispose()
  })

  // 0294 B0001: the header chip counts finished cards while they live, so the derived
  // counts must track the TTL exactly — and must not fold a failure into the clean tone.
  it('counts finished cards for their TTL and flags the non-clean ones', () => {
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-ok', group_id: 'g.count.ok', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-ok', group_id: 'g.count.ok', outcome: 'complete' })
    store.trackStarted({ run_id: 'run-bad', group_id: 'g.count.bad', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-bad', group_id: 'g.count.bad', outcome: 'partial' })
    store.trackStarted({ run_id: 'run-lost', group_id: 'g.count.lost', doc_ref: 'r' })
    store.markLost('g.count.lost', 'run-lost')
    // A user-paused stop is not an end-of-run signal: it stays a paused card indefinitely.
    store.trackStarted({ run_id: 'run-pz', group_id: 'g.count.pz', doc_ref: 'r', mode: 'continuous' })
    store.trackFinished({ run_id: 'run-pz', group_id: 'g.count.pz', end_reason: 'user_paused' })

    expect(store.finishedCount).toBe(3)
    expect(store.finishedAlertCount).toBe(2)
    expect(store.pausedCount).toBe(1)
    expect(store.activeCount).toBe(0)

    vi.advanceTimersByTime(FINISHED_CARD_TTL_MS - 2_000)
    expect(store.finishedCount).toBe(3)

    vi.advanceTimersByTime(3_000)
    expect(store.finishedCount).toBe(0)
    expect(store.finishedAlertCount).toBe(0)
    expect(store.pausedCount).toBe(1)

    store.$dispose()
  })
  // 0290 R0001: the old 10s TTL meant a result was gone before it could be read. The
  // exact value is a product decision, but "long enough to walk away from" is the point.
  it('keeps a finished card well past the old 10s window', () => {
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-f', group_id: 'g.finished.2', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-f', group_id: 'g.finished.2', outcome: 'complete' })

    vi.advanceTimersByTime(10 * 60_000)
    expect(store.runsByGroup['g.finished.2']).toBeDefined()

    store.$dispose()
  })

  it('caps the finished backlog by dropping the oldest cards first', () => {
    const store = useAiInvokeRunsStore()
    for (let i = 0; i < MAX_FINISHED_CARDS + 3; i += 1) {
      const groupId = `g.cap.${String(i).padStart(2, '0')}`
      store.trackStarted({ run_id: `run-${i}`, group_id: groupId, doc_ref: 'r' })
      store.trackFinished({ run_id: `run-${i}`, group_id: groupId, outcome: 'complete' })
      vi.advanceTimersByTime(1_000)  // distinct finishedAtMs so "oldest" is unambiguous
    }

    const remaining = Object.keys(store.runsByGroup).sort()
    expect(remaining).toHaveLength(MAX_FINISHED_CARDS)
    expect(remaining).not.toContain('g.cap.00')
    expect(remaining).toContain(`g.cap.${String(MAX_FINISHED_CARDS + 2).padStart(2, '0')}`)

    store.$dispose()
  })

  it('clears every finished card at once but leaves running and paused ones', () => {
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-f', group_id: 'g.bulk.fin', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-f', group_id: 'g.bulk.fin', outcome: 'complete' })
    store.trackStarted({ run_id: 'run-p', group_id: 'g.bulk.pau', doc_ref: 'r', mode: 'continuous' })
    store.trackFinished({ run_id: 'run-p', group_id: 'g.bulk.pau', end_reason: 'user_paused' })
    store.trackStarted({ run_id: 'run-r', group_id: 'g.bulk.run', doc_ref: 'r' })
    expect(store.finishedCount).toBe(1)

    store.dismissAllFinished()

    expect(store.runsByGroup['g.bulk.fin']).toBeUndefined()
    expect(store.runsByGroup['g.bulk.pau']?.phase).toBe('paused')
    expect(store.runsByGroup['g.bulk.run']?.phase).toBe('running')
    expect(store.finishedCount).toBe(0)

    store.$dispose()
  })

  // /ai-invoke/active-all never returns finished runs, so without this a reload wiped
  // the cards the TTL exists to keep (0290 NR0003 §3.5).
  it('restores finished cards across a reload and drops the expired ones', () => {
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-f', group_id: 'g.persist.1', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-f', group_id: 'g.persist.1', outcome: 'complete' })
    vi.advanceTimersByTime(2_000)  // let the 1s clock flush the write
    store.$dispose()

    setActivePinia(createPinia())
    const reloaded = useAiInvokeRunsStore()
    expect(reloaded.runsByGroup['g.persist.1']?.phase).toBe('finished')
    reloaded.$dispose()

    vi.advanceTimersByTime(FINISHED_CARD_TTL_MS)
    setActivePinia(createPinia())
    const stale = useAiInvokeRunsStore()
    expect(stale.runsByGroup['g.persist.1']).toBeUndefined()
    stale.$dispose()
  })

  it('does not persist paused cards', () => {
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-p', group_id: 'g.persist.2', doc_ref: 'r', mode: 'continuous' })
    store.trackFinished({ run_id: 'run-p', group_id: 'g.persist.2', end_reason: 'user_paused' })
    vi.advanceTimersByTime(2_000)
    store.$dispose()

    setActivePinia(createPinia())
    const reloaded = useAiInvokeRunsStore()
    // Paused chains come back from the server (active-all), not from this cache.
    expect(reloaded.runsByGroup['g.persist.2']).toBeUndefined()
    reloaded.$dispose()
  })
})