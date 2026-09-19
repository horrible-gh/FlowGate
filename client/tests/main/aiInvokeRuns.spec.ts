import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { deleteRequest, getRequest, postRequest } from '@shared/api'
import {
  FINISHED_CARD_TTL_MS,
  MAX_FINISHED_CARDS,
  PERSIST_QUOTA_FALLBACK_CARDS,
  cardSlotsFor,
  compareRunEntries,
  isExpired,
  isFinishedCard,
  openTargetDocId,
  type AiInvokeRunEntry,
  useAiInvokeRunsStore,
} from '@main/stores/aiInvokeRuns'
import {
  RETENTION_DEFAULT_MINUTES,
  RETENTION_DOMAIN_MINUTES,
  RETENTION_MIRROR_KEY,
  UI_SETTINGS_PATH,
  normalizeRetentionMinutes,
  parseRetentionMinutes,
  retentionMs,
} from '@shared/aiFinishedCardRetention'

const FINISHED_STORAGE_KEY = 'fg.ai_invoke.finished_cards'

// A finished card as it sits in sessionStorage, so restore can be exercised without
// having to run (and then dispose) a whole store first.
function seedFinishedSnapshot(groupId: string, finishedAtMs: number): void {
  sessionStorage.setItem(FINISHED_STORAGE_KEY, JSON.stringify({
    [groupId]: {
      runId: 'run-seed', groupId, docRef: 'r', phase: 'finished', mode: 'single',
      handoffPending: false, endReason: 'exited', outcome: 'complete',
      pendingQDocIds: [], reachedDocIds: [], finishedAtMs,
    },
  }))
}

function finishOne(store: ReturnType<typeof useAiInvokeRunsStore>, groupId: string): void {
  store.trackStarted({ run_id: `run-${groupId}`, group_id: groupId, doc_ref: 'r' })
  store.trackFinished({ run_id: `run-${groupId}`, group_id: groupId, outcome: 'complete' })
}

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  deleteRequest: vi.fn(),
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
    localStorage.clear()
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

    // 0563 T0007: a genuine finish moves out of runsByGroup into run-keyed history.
    const run = store.finishedByRun['run-a']
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

    expect(store.finishedByRun['run-diagnostic'].registerErrors).toEqual([
      { status: 409, reason: 'dup_body', turn: 4 },
    ])
    expect(store.finishedByRun['run-diagnostic'].toolCallMisses).toBe(2)
    expect(store.finishedByRun['run-diagnostic'].turnLimitExhausted).toBe(true)
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

    expect(store.finishedByRun['run-a'].phase).toBe('finished')
    expect(store.runsByGroup[groupB].phase).toBe('running')
    expect(store.runsByGroup[groupB].runId).toBe('run-b')
    expect(store.runsByGroup[groupB].providerSwitches).toHaveLength(0)
    expect(store.activeCount).toBe(1)
  })

  it('removes a finished entry when dismissed', () => {
    const groupId = 'flowgate.default.1001'
    store.trackStarted({ run_id: 'run-a', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({ run_id: 'run-a', group_id: groupId, outcome: 'complete' })

    // 0563 T0007: dismiss() is run-keyed -- the finished card lives in finishedByRun.
    store.dismiss('run-a')

    expect(store.finishedByRun['run-a']).toBeUndefined()
  })

  it('marks only the polled group lost when its run returns 404', async () => {
    const groupA = 'flowgate.default.1001'
    const groupB = 'flowgate.default.1002'
    store.trackStarted({ run_id: 'run-a', group_id: groupA, doc_ref: 'a' })
    store.trackStarted({ run_id: 'run-b', group_id: groupB, doc_ref: 'b' })
    vi.mocked(getRequest).mockRejectedValueOnce({ response: { status: 404 } })

    await store.refresh(groupA)

    // 0563 T0007: markLost() moves the card into run-keyed finished history too.
    expect(store.finishedByRun['run-a'].phase).toBe('lost')
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
    // 0563 T0007: dismiss() is run-keyed now; a paused card was never in finishedByRun.
    store.dismiss(run.runId)
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

  it('keeps a restored pause card and rejects a resume launch 409', async () => {
    const groupId = 'flowgate.default.0384'
    store.trackFinished({
      run_id: 'run-old', group_id: groupId, doc_ref: 'r', end_reason: 'user_paused',
    })
    vi.mocked(getRequest).mockClear()
    const rejection = {
      response: {
        status: 409,
        data: {
          code: 'resume_advance_blocked',
          restored: true,
          resume_stage: 'advance',
        },
      },
    }
    vi.mocked(postRequest).mockRejectedValueOnce(rejection)

    await expect(store.resume(groupId)).rejects.toBe(rejection)

    expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    expect(vi.mocked(getRequest)).not.toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
  })

  // 0459 T0007 §2: group-keyed DELETE, separate from the run_id-keyed cancel() endpoint.
  describe('releasePaused', () => {
    it('does nothing for a non-paused card', async () => {
      const groupId = 'flowgate.default.0459.release-not-paused'
      store.trackStarted({ run_id: 'run-live', group_id: groupId, doc_ref: 'r' })

      await store.releasePaused(groupId)

      expect(vi.mocked(deleteRequest)).not.toHaveBeenCalled()
      expect(store.runsByGroup[groupId].phase).toBe('running')
    })

    it('calls the group-keyed DELETE endpoint and removes the card on released:true', async () => {
      const groupId = 'flowgate.default.0459.release-ok'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      vi.mocked(deleteRequest).mockResolvedValueOnce({
        data: { ok: true, group_id: groupId, released: true, already_released: false },
      } as any)

      await store.releasePaused(groupId)

      expect(vi.mocked(deleteRequest)).toHaveBeenCalledWith(
        `/api/v1/ai-invoke/paused/${encodeURIComponent(groupId)}`,
      )
      expect(store.runsByGroup[groupId]).toBeUndefined()
    })

    it('removes the card on the idempotent already_released:true response too', async () => {
      const groupId = 'flowgate.default.0459.release-idempotent'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      vi.mocked(deleteRequest).mockResolvedValueOnce({
        data: { ok: true, group_id: groupId, released: false, already_released: true },
      } as any)

      await store.releasePaused(groupId)

      expect(store.runsByGroup[groupId]).toBeUndefined()
    })

    it('preserves the card and does not delete on an unexpected 2xx with neither flag set', async () => {
      const groupId = 'flowgate.default.0459.release-weird-2xx'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      vi.mocked(deleteRequest).mockResolvedValueOnce({
        data: { ok: true, group_id: groupId },
      } as any)

      await store.releasePaused(groupId)

      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    })

    it('preserves the card and rethrows on 403 ownership rejection', async () => {
      const groupId = 'flowgate.default.0459.release-403'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      vi.mocked(getRequest).mockClear()
      const rejection = {
        response: { status: 403, data: { code: 'paused_chain_forbidden' } },
      }
      vi.mocked(deleteRequest).mockRejectedValueOnce(rejection)

      await expect(store.releasePaused(groupId)).rejects.toBe(rejection)

      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
      expect(vi.mocked(getRequest)).not.toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
    })

    it('reconciles via bootstrap and rethrows on 409 run_already_active', async () => {
      const groupId = 'flowgate.default.0459.release-409'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      const rejection = {
        response: { status: 409, data: { code: 'run_already_active', run_id: 'run-live' } },
      }
      vi.mocked(deleteRequest).mockRejectedValueOnce(rejection)
      vi.mocked(getRequest).mockResolvedValueOnce({
        data: {
          ok: true,
          runs: [{ run_id: 'run-live', group_id: groupId, status: 'running', doc_ref: 'r' }],
          paused: [],
        },
      } as any)

      await expect(store.releasePaused(groupId)).rejects.toBe(rejection)

      expect(vi.mocked(getRequest)).toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
      // Authoritative reconciliation replaced the paused card with the run another
      // session actually started -- never an optimistic delete.
      expect(store.runsByGroup[groupId]?.runId).toBe('run-live')
      expect(store.runsByGroup[groupId]?.phase).toBe('running')
    })

    it('reconciles via bootstrap and rethrows on 409 group_lease_active', async () => {
      // 0459 TR0008 rev1: the lease-conflict code is distinct from run_already_active
      // (server contract), but the store's reconciliation strategy is the same for
      // both -- never an optimistic delete, always an authoritative re-fetch.
      const groupId = 'flowgate.default.0459.release-409-lease'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      const rejection = {
        response: { status: 409, data: { code: 'group_lease_active', run_id: 'run-live' } },
      }
      vi.mocked(deleteRequest).mockRejectedValueOnce(rejection)
      vi.mocked(getRequest).mockResolvedValueOnce({
        data: {
          ok: true,
          runs: [],
          paused: [{ group_id: groupId, doc_ref: 'r', paused_at: '2026-08-25T10:00:00+09:00' }],
        },
      } as any)

      await expect(store.releasePaused(groupId)).rejects.toBe(rejection)

      expect(vi.mocked(getRequest)).toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    })

    it('preserves the card and rethrows on a network failure without bootstrapping', async () => {
      const groupId = 'flowgate.default.0459.release-network'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      vi.mocked(getRequest).mockClear()
      const failure = new Error('network down')
      vi.mocked(deleteRequest).mockRejectedValueOnce(failure)

      await expect(store.releasePaused(groupId)).rejects.toBe(failure)

      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
      expect(vi.mocked(getRequest)).not.toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
    })

    it('preserves the card and rethrows on a 5xx response without bootstrapping', async () => {
      // Distinct from the network-failure case above -- this is a genuine HTTP
      // response (status 500), not a rejected promise with no response object at all.
      const groupId = 'flowgate.default.0459.release-5xx'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      vi.mocked(getRequest).mockClear()
      const rejection = {
        response: { status: 500, data: { code: 'internal_error', message: 'db unavailable' } },
      }
      vi.mocked(deleteRequest).mockRejectedValueOnce(rejection)

      await expect(store.releasePaused(groupId)).rejects.toBe(rejection)

      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
      expect(vi.mocked(getRequest)).not.toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
    })

    it('is a distinct API surface from the run_id-keyed cancel()', async () => {
      const groupId = 'flowgate.default.0459.release-api-split'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })
      vi.mocked(deleteRequest).mockResolvedValueOnce({
        data: { ok: true, group_id: groupId, released: true, already_released: false },
      } as any)

      await store.releasePaused(groupId)

      expect(vi.mocked(postRequest)).not.toHaveBeenCalledWith(
        expect.stringContaining('/cancel'), expect.anything(),
      )
    })
  })

  // 0500 T0004 §7/§8: what every "목록에서 제거" control calls. The rev2 rejection was
  // "난 카드가 계속 뜨고있는데?" -- a local dismiss() cannot remove a card that a durable
  // ai_invoke_paused_chains row rebuilds on the next bootstrap (NR0003 §2/§8), and for
  // paused cards dismiss() does not even fire. removeCard() splits the two lifecycles.
  describe('removeCard', () => {
    const LEASE_DENIED_ROW = (groupId: string) => ({
      group_id: groupId,
      doc_ref: `${groupId}.0001-B`,
      paused_at: '2026-09-01T10:00:00+09:00',
      stop_kind: 'system',
      stop_code: 'group_lease_denied',
      stop_run_id: 'aiv_lease_denied',
      resume_available: false,
    })

    async function bootstrapLeaseDeniedCard(groupId: string): Promise<void> {
      vi.mocked(getRequest).mockResolvedValueOnce({
        data: { ok: true, runs: [], paused: [LEASE_DENIED_ROW(groupId)] },
      } as any)
      await store.bootstrap()
    }

    it('releases the durable row for a non-resumable system stop and drops the card', async () => {
      const groupId = 'flowgate.default.0500.remove-lease-denied'
      await bootstrapLeaseDeniedCard(groupId)
      expect(store.runsByGroup[groupId]).toMatchObject({
        phase: 'paused', stopKind: 'system', stopCode: 'group_lease_denied', resumeAvailable: false,
      })
      vi.mocked(deleteRequest).mockResolvedValueOnce({
        data: { ok: true, group_id: groupId, released: true, already_released: false },
      } as any)

      // 0563 T0007: removeCard() now takes the entry itself (a finished card's identity
      // is its runId, several can share a groupId), not a bare id.
      await store.removeCard(store.runsByGroup[groupId])

      expect(vi.mocked(deleteRequest)).toHaveBeenCalledWith(
        `/api/v1/ai-invoke/paused/${encodeURIComponent(groupId)}`,
      )
      expect(store.runsByGroup[groupId]).toBeUndefined()
    })

    // §16: the card may only go when the SERVER confirmed it. A refused remove that
    // still cleared the card locally would be the same ghost one bootstrap later.
    it('keeps the card and rethrows when the server refuses the release', async () => {
      const groupId = 'flowgate.default.0500.remove-refused'
      await bootstrapLeaseDeniedCard(groupId)
      const rejection = { response: { status: 409, data: { code: 'group_lease_active' } } }
      vi.mocked(deleteRequest).mockRejectedValueOnce(rejection)
      vi.mocked(getRequest).mockResolvedValueOnce({
        data: { ok: true, runs: [], paused: [LEASE_DENIED_ROW(groupId)] },
      } as any)

      await expect(store.removeCard(store.runsByGroup[groupId])).rejects.toBe(rejection)

      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    })

    // §9: a user pause is never removed by this shortcut -- it keeps the explicit
    // resume / cancel-release lifecycle, and dismiss() refuses every paused card.
    it('leaves a user pause alone and sends no request', async () => {
      const groupId = 'flowgate.default.0500.remove-user-pause'
      store.trackFinished({ run_id: 'run-old', group_id: groupId, end_reason: 'user_paused' })

      await store.removeCard(store.runsByGroup[groupId])

      expect(vi.mocked(deleteRequest)).not.toHaveBeenCalled()
      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    })

    // §10: stop_kind=system is NOT enough. A resumable system stop keeps the paused
    // lifecycle, so this must not reach the DELETE either.
    it('leaves a resumable system stop alone and sends no request', async () => {
      const groupId = 'flowgate.default.0500.remove-resumable-system'
      vi.mocked(getRequest).mockResolvedValueOnce({
        data: {
          ok: true,
          runs: [],
          paused: [{
            group_id: groupId,
            doc_ref: `${groupId}.0001-B`,
            paused_at: '2026-09-01T10:00:00+09:00',
            stop_kind: 'system',
            stop_code: 'no_output_exhausted',
            stop_run_id: 'aiv_resumable',
            resume_available: true,
          }],
        },
      } as any)
      await store.bootstrap()

      await store.removeCard(store.runsByGroup[groupId])

      expect(vi.mocked(deleteRequest)).not.toHaveBeenCalled()
      expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    })

    // §19.4: an ordinary finished card is still a purely local dismiss -- no request.
    it('dismisses an ordinary finished card locally without any request', async () => {
      const groupId = 'flowgate.default.0500.remove-finished'
      finishOne(store, groupId)
      const runId = `run-${groupId}`

      await store.removeCard(store.finishedByRun[runId])

      expect(vi.mocked(deleteRequest)).not.toHaveBeenCalled()
      expect(store.finishedByRun[runId]).toBeUndefined()
    })

    // The whole point of the rejection: after a confirmed remove the next active-all
    // must not bring the same card back.
    it('does not rehydrate the removed card on the next active-all', async () => {
      const groupId = 'flowgate.default.0500.remove-no-rehydrate'
      await bootstrapLeaseDeniedCard(groupId)
      vi.mocked(deleteRequest).mockResolvedValueOnce({
        data: { ok: true, group_id: groupId, released: true, already_released: false },
      } as any)

      await store.removeCard(store.runsByGroup[groupId])
      vi.mocked(getRequest).mockResolvedValueOnce({
        data: { ok: true, runs: [], paused: [] },
      } as any)
      await store.bootstrap()

      expect(store.runsByGroup[groupId]).toBeUndefined()
    })
  })

  // T0005 §2/§3 item 3 / §4 item 3: active-all's four resume-blocker fields must
  // survive the snake_case -> camelCase normalization losslessly (both the blocked
  // shape and the pre-existing true/null default for an ordinary paused row).
  it('preserves the four resume-blocker fields from active-all onto the paused card', async () => {
    const groupId = 'flowgate.default.0456.blocked'
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [],
        paused: [{
          group_id: groupId,
          doc_ref: `${groupId}.0001-B`,
          paused_at: '2026-08-24T12:00:00+09:00',
          resume_available: false,
          resume_block_code: 'provider_unavailable',
          resume_block_reason: 'The selected AI provider is not enabled for this project.',
          resume_provider_name: 'Old CLI',
        }],
      },
    } as any)

    await store.bootstrap()

    expect(store.runsByGroup[groupId]).toMatchObject({
      phase: 'paused',
      resumeAvailable: false,
      resumeBlockCode: 'provider_unavailable',
      resumeBlockReason: 'The selected AI provider is not enabled for this project.',
      resumeProviderName: 'Old CLI',
    })
  })

  it('defaults an ordinary paused row to resumable with null blockers', async () => {
    const groupId = 'flowgate.default.0456.plain'
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [],
        paused: [{
          group_id: groupId,
          doc_ref: `${groupId}.0001-B`,
          paused_at: '2026-08-24T12:00:00+09:00',
          resume_available: true,
          resume_block_code: null,
          resume_block_reason: null,
          resume_provider_name: null,
        }],
      },
    } as any)

    await store.bootstrap()

    expect(store.runsByGroup[groupId]).toMatchObject({
      phase: 'paused',
      resumeAvailable: true,
      resumeBlockCode: null,
      resumeBlockReason: null,
      resumeProviderName: null,
    })
  })

  // T0005 §3 item 3: a stale resumeAvailable hint (a settings change raced the open
  // card) must reach the caller as the server's own 422 -- never rewritten, never
  // swallowed -- and the paused card must survive the rejection.
  it('rejects with the original 422 provider_unavailable body and keeps the paused card', async () => {
    const groupId = 'flowgate.default.0456.stale'
    store.trackFinished({
      run_id: 'run-stale', group_id: groupId, doc_ref: 'r', end_reason: 'user_paused',
    })
    vi.mocked(getRequest).mockClear()
    const rejection = {
      response: {
        status: 422,
        data: {
          code: 'provider_unavailable',
          message: 'The selected AI provider is not enabled for this project.',
        },
      },
    }
    vi.mocked(postRequest).mockRejectedValueOnce(rejection)

    await expect(store.resume(groupId)).rejects.toBe(rejection)

    expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    expect(vi.mocked(getRequest)).not.toHaveBeenCalledWith('/api/v1/ai-invoke/active-all')
  })

  it('retains system-stop identity from active-all bootstrap', async () => {
    const groupId = 'flowgate.default.0385'
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [],
        paused: [{
          group_id: groupId,
          doc_ref: `${groupId}.0001-B`,
          paused_at: '2026-08-03T13:56:40+09:00',
          stop_kind: 'system',
          stop_code: 'no_output_exhausted',
          stop_run_id: 'aiv_old_chain',
          stop_last_message_excerpt: 'no output',
        }],
      },
    } as any)

    await store.bootstrap()

    expect(store.runsByGroup[groupId]).toMatchObject({
      phase: 'paused',
      stopKind: 'system',
      stopCode: 'no_output_exhausted',
      stopRunId: 'aiv_old_chain',
      stopLastMessageExcerpt: 'no output',
      pausedAt: '2026-08-03T13:56:40+09:00',
    })
  })

  // T0004 §19.3: a non-resumable system-stop card (the group_lease_denied
  // representative payload) must not come back after it has been explicitly
  // released -- an initial active-all bootstrap that only ever proves the card was
  // adopted once is not the same regression as proving it stays gone.
  it('does not rehydrate a released non-resumable system-stop card on the next active-all poll', async () => {
    const groupId = 'flowgate.default.0459.release-no-rehydrate'
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [],
        paused: [{
          group_id: groupId,
          doc_ref: `${groupId}.0001-B`,
          paused_at: '2026-08-25T10:00:00+09:00',
          stop_kind: 'system',
          stop_code: 'group_lease_denied',
          stop_run_id: 'aiv_lease_denied',
          resume_available: false,
        }],
      },
    } as any)

    await store.bootstrap()

    expect(store.runsByGroup[groupId]).toMatchObject({
      phase: 'paused',
      stopKind: 'system',
      stopCode: 'group_lease_denied',
      resumeAvailable: false,
    })

    vi.mocked(deleteRequest).mockResolvedValueOnce({
      data: { ok: true, group_id: groupId, released: true, already_released: false },
    } as any)

    await store.releasePaused(groupId)

    expect(store.runsByGroup[groupId]).toBeUndefined()

    vi.mocked(getRequest).mockResolvedValueOnce({
      data: { ok: true, runs: [], paused: [] },
    } as any)

    await store.bootstrap()

    expect(store.runsByGroup[groupId]).toBeUndefined()
  })

  // 0393 B0001 / T0005 §2-6: the server has shipped `stop_reason` on the finished payload
  // since 0359, but the card never read it — so a stopped run showed a bare code, and
  // B0001's three refused reviews showed nothing at all ("원인도 모르고").
  it('carries the server stop reason onto a finished card', () => {
    const groupId = 'flowgate.default.0393'
    store.trackStarted({
      run_id: 'aiv-0393', group_id: groupId, doc_ref: `${groupId}.0001-B`, mode: 'single',
    })
    store.trackFinished({
      run_id: 'aiv-0393', group_id: groupId, end_reason: 'exited', outcome: 'none',
      stop_code: 'group_lease_denied', resumable: false,
      stop_reason: "The group gate refused this run's own worker (GROUP_AI_RUN_OWNER_MISMATCH) on POST /flowgate/api/v1/inbox, so nothing it submitted was registered. A human must clear this: the run is not resumable.",
    })

    expect(store.finishedByRun['aiv-0393']).toMatchObject({
      phase: 'finished',
      stopCode: 'group_lease_denied',
      stopReason: "The group gate refused this run's own worker (GROUP_AI_RUN_OWNER_MISMATCH) on POST /flowgate/api/v1/inbox, so nothing it submitted was registered. A human must clear this: the run is not resumable.",
    })
  })

  it('keeps the stop reason across a paused bootstrap', async () => {
    const groupId = 'flowgate.default.0394'
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [],
        paused: [{
          group_id: groupId,
          doc_ref: `${groupId}.0001-B`,
          paused_at: '2026-08-07T17:10:00+09:00',
          stop_kind: 'system',
          stop_code: 'no_output_exhausted',
          stop_reason: '3 attempts on this hop ended without producing a document.',
        }],
      },
    } as any)

    await store.bootstrap()

    expect(store.runsByGroup[groupId]?.stopReason).toBe(
      '3 attempts on this hop ended without producing a document.',
    )
  })

  it('keeps refresh signals out of Q state and clears only after every item is answered', () => {
    const groupId = 'flowgate.default.2010'
    const qDocId = `${groupId}.0005-D`
    store.trackStarted({ run_id: 'run-q', group_id: groupId, doc_ref: 'r', mode: 'continuous' })

    store.trackQuestionRegistered(qDocId)
    store.trackQuestionRegistered(qDocId) // duplicate signal folds
    expect(store.runsByGroup[groupId].pendingQDocIds).toEqual([qDocId])
    expect(store.awaitingQCount).toBe(1)

    // AI-run completion refreshes Q&A panels but is not a semantic registration.
    window.dispatchEvent(new CustomEvent('fg:qa_refresh', { detail: { doc_id: qDocId } }))
    expect(store.runsByGroup[groupId].pendingQDocIds).toEqual([qDocId])

    // A Q for a group without a card is ignored (the document panel owns it).
    store.trackQuestionRegistered('flowgate.default.9999.0001-D')
    expect(store.runsByGroup['flowgate.default.9999']).toBeUndefined()

    store.trackQuestionAnswered(qDocId, true)
    expect(store.runsByGroup[groupId].pendingQDocIds).toEqual([qDocId])
    store.trackQuestionAnswered(qDocId, false)
    expect(store.runsByGroup[groupId].pendingQDocIds).toEqual([])
    expect(store.awaitingQCount).toBe(0)
  })

  it('clears stale pending Q state when polling returns an explicit empty array', async () => {
    const groupId = 'flowgate.default.2011'
    const docId = `${groupId}.0004-D`
    store.trackStarted({ run_id: 'run-poll', group_id: groupId, doc_ref: docId })
    store.trackQuestionRegistered(docId)
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        run_id: 'run-poll',
        group_id: groupId,
        status: 'running',
        pending_q_doc_ids: [],
      },
    } as any)

    await store.refresh(groupId)

    expect(store.runsByGroup[groupId].pendingQDocIds).toEqual([])
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

      // 0563 T0007: a user pause stays group-keyed current state; any other end reason
      // is a genuine completion and moves into the run-keyed finished history.
      const run = endReason === 'user_paused' ? store.runsByGroup[groupId] : store.finishedByRun['run-old']
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

    expect(store.finishedByRun['run-final'].phase).toBe('finished')
    expect(store.finishedByRun['run-final'].handoffPending).toBe(false)
    expect(isFinishedCard(store.finishedByRun['run-final'])).toBe(true)
  })

  // TR0012 rev2: bootstrap() and the user-pause triggered refreshPausedState() (fired from
  // trackFinished's `void refreshPausedState(groupId)` on end_reason: 'user_paused') both
  // read through fetchActiveAll()'s shared in-flight Promise. Neither call in this test is
  // awaited before the other starts, so this reproduces the two real, unrelated consumers
  // overlapping instead of exercising them sequentially like every other store.bootstrap()
  // call in this file does.
  it('joins bootstrap() and a user-pause refresh into one active-all HTTP call, then reads fresh next time', async () => {
    const groupId = 'flowgate.default.join.paused'
    store.trackStarted({ run_id: 'run-join', group_id: groupId, mode: 'continuous' })

    let resolveActiveAll!: (value: unknown) => void
    vi.mocked(getRequest).mockReturnValueOnce(new Promise((resolve) => { resolveActiveAll = resolve }))

    const bootstrapPromise = store.bootstrap()
    // Fires refreshPausedState(groupId) internally, before the mocked GET above resolves.
    store.trackFinished({ run_id: 'run-join', group_id: groupId, end_reason: 'user_paused' })

    expect(vi.mocked(getRequest)).toHaveBeenCalledTimes(1)

    resolveActiveAll({ data: { runs: [], paused: [] } })
    await bootstrapPromise
    // Let refreshPausedState's own `await fetchActiveAll()` continuation settle too.
    await Promise.resolve()
    await Promise.resolve()

    expect(vi.mocked(getRequest)).toHaveBeenCalledTimes(1)

    vi.mocked(getRequest).mockResolvedValueOnce({ data: { runs: [], paused: [] } } as any)
    await store.bootstrap()

    expect(vi.mocked(getRequest)).toHaveBeenCalledTimes(2)
  })

})

describe('aiInvokeRuns store — bounded handoff adoption', () => {
  let store: ReturnType<typeof useAiInvokeRunsStore>

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-01T12:00:00Z'))
    sessionStorage.clear()
    localStorage.clear()
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
    // 0563 T0007: finalizeHandoff() lands the settled hop in run-keyed finished history.
    expect(store.finishedByRun['run-old'].handoffPending).toBe(false)
    expect(store.finishedByRun['run-old'].phase).toBe('finished')
    expect(isFinishedCard(store.finishedByRun['run-old'])).toBe(true)

    const activeAllCalls = vi.mocked(getRequest).mock.calls
      .filter(([path]) => path === '/api/v1/ai-invoke/active-all')
    expect(activeAllCalls.length).toBeGreaterThanOrEqual(6)
  })

  it('keeps a newer run handoff lifecycle when an older run finishes late', async () => {
    const groupId = 'flowgate.default.handoff.late-old-finish'
    store.trackStarted({
      run_id: 'run-B', group_id: groupId, mode: 'continuous',
      continuation_pending: true, chain_id: 'chain-B',
    })
    store.trackFinished({
      run_id: 'run-B', group_id: groupId, mode: 'continuous',
      end_reason: 'exited', stop_code: 'hop_handoff', outcome: 'complete',
      chain_id: 'chain-B',
    })
    expect(store.runsByGroup[groupId]).toMatchObject({
      runId: 'run-B', phase: 'running', handoffPending: true,
    })

    // A terminal event for an older run may create A's history, but must not clear B's
    // group-scoped adoption timer, poll counters or settled payload.
    store.trackFinished({
      run_id: 'run-A', group_id: groupId, mode: 'single',
      end_reason: 'exited', outcome: 'complete',
    })
    expect(store.finishedByRun['run-A']).toMatchObject({ runId: 'run-A', phase: 'finished' })
    expect(store.runsByGroup[groupId]).toMatchObject({
      runId: 'run-B', phase: 'running', handoffPending: true,
    })

    // The same late A is even more dangerous when it is itself a handoff boundary:
    // it must not overwrite B's settled payload or create A timers inside B's slot.
    store.trackFinished({
      run_id: 'run-A', group_id: groupId, mode: 'continuous',
      end_reason: 'exited', stop_code: 'hop_handoff', outcome: 'none',
      chain_id: 'chain-A',
    })
    expect(store.runsByGroup[groupId]).toMatchObject({
      runId: 'run-B', phase: 'running', handoffPending: true, chainId: 'chain-B',
    })

    await vi.advanceTimersByTimeAsync(3_000)
    await store.refreshAllRunning()
    await store.refreshAllRunning()

    expect(store.runsByGroup[groupId]).toBeUndefined()
    expect(store.finishedByRun['run-B']).toMatchObject({
      runId: 'run-B', phase: 'finished', handoffPending: false,
      outcome: 'complete', chainId: 'chain-B',
    })
  })
})

// 0563 T#2: the default retention is now -1 (until manually deleted), so these cases can no
// longer rely on "nobody has opened the account screen" to mean a finite TTL. Each one sets
// the mirror to 30 explicitly -- the exact sweep/cap mechanics these regressions pin are
// unchanged, they are just no longer implied by silence. The per-user cases (including -1's
// own no-TTL/no-cap behaviour) live in the block after it.
describe('aiInvokeRuns store — finished-card TTL sweep', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sessionStorage.clear()
    localStorage.clear()
    localStorage.setItem(RETENTION_MIRROR_KEY, '30')
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

    vi.advanceTimersByTime(retentionMs(30) - 2_000)
    expect(store.finishedByRun['run-f']).toBeDefined()

    vi.advanceTimersByTime(3_000)
    expect(store.finishedByRun['run-f']).toBeUndefined()
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

    vi.advanceTimersByTime(retentionMs(30) - 2_000)
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
    expect(store.finishedByRun['run-f']).toBeDefined()

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

    // 0563 T0007: the finished backlog is run-keyed now, so membership is checked by
    // run_id (`run-${i}`), not the group id used to build each card.
    const remaining = Object.keys(store.finishedByRun).sort()
    expect(remaining).toHaveLength(MAX_FINISHED_CARDS)
    expect(remaining).not.toContain('run-0')
    expect(remaining).toContain(`run-${MAX_FINISHED_CARDS + 2}`)

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

    expect(store.finishedByRun['run-f']).toBeUndefined()
    expect(store.runsByGroup['g.bulk.pau']?.phase).toBe('paused')
    expect(store.runsByGroup['g.bulk.run']?.phase).toBe('running')
    expect(store.finishedCount).toBe(0)

    store.$dispose()
  })

  // /ai-invoke/active-all never returns finished runs, so without this a reload wiped
  // the cards the TTL exists to keep (0290 NR0003 §3.5).
  it('restores finished cards across a reload and drops the expired ones', () => {
    // The mirror is what a reload judges by (0452 L0003 §2-4); this block pins the 30-minute
    // TTL sweep explicitly (0563 T#2: the default is -1 now). Without a mirror this case
    // would be the fail-open branch instead, which the next block covers on its own.
    localStorage.setItem(RETENTION_MIRROR_KEY, '30')
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'run-f', group_id: 'g.persist.1', doc_ref: 'r' })
    store.trackFinished({ run_id: 'run-f', group_id: 'g.persist.1', outcome: 'complete' })
    vi.advanceTimersByTime(2_000)  // let the 1s clock flush the write
    store.$dispose()

    setActivePinia(createPinia())
    const reloaded = useAiInvokeRunsStore()
    // 0563 T0007: persisted finished snapshots restore into finishedByRun, keyed by runId.
    expect(reloaded.finishedByRun['run-f']?.phase).toBe('finished')
    reloaded.$dispose()

    vi.advanceTimersByTime(retentionMs(30))
    setActivePinia(createPinia())
    const stale = useAiInvokeRunsStore()
    expect(stale.finishedByRun['run-f']).toBeUndefined()
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

// ── 0452: the retention is a per-user setting ─────────────────────────────────

describe('finished-card retention — the shared contract', () => {
  it('accepts exactly the nine values and repairs everything else', () => {
    for (const minutes of RETENTION_DOMAIN_MINUTES) {
      expect(normalizeRetentionMinutes(minutes)).toBe(minutes)
    }
    // -1 is a MEMBER, not a lower bound. A range clamp passes every other case here and
    // silently turns "never expires" into 30 minutes.
    expect(normalizeRetentionMinutes(-1)).toBe(-1)
    for (const bad of [null, undefined, true, false, '30', 'abc', 45, -2, 0.5, NaN, 100_000]) {
      expect(normalizeRetentionMinutes(bad)).toBe(RETENTION_DEFAULT_MINUTES)
    }
  })

  it('reads text values without letting an empty string become "immediately"', () => {
    expect(parseRetentionMinutes('-1')).toBe(-1)
    expect(parseRetentionMinutes('0')).toBe(0)
    expect(parseRetentionMinutes(' 1440 ')).toBe(1440)
    // Number('') and Number(' ') are both 0, and 0 empties the list, so the text has to
    // be judged before it is converted.
    for (const bad of ['', '   ', 'null', '3o', null]) {
      expect(parseRetentionMinutes(bad)).toBe(RETENTION_DEFAULT_MINUTES)
    }
  })

  it('converts minutes to a TTL, with -1 leaving arithmetic entirely', () => {
    expect(retentionMs(-1)).toBe(Number.POSITIVE_INFINITY)
    expect(retentionMs(0)).toBe(0)
    expect(retentionMs(30)).toBe(1_800_000)
    expect(retentionMs(1440)).toBe(86_400_000)
    // The default constant is derived from the same place, not typed twice.
    expect(FINISHED_CARD_TTL_MS).toBe(retentionMs(RETENTION_DEFAULT_MINUTES))
  })

  it('expires at the boundary and intercepts both sentinels before the subtraction', () => {
    expect(isExpired(0, 1_800_000 - 1, 1_800_000)).toBe(false)
    expect(isExpired(0, 1_800_000, 1_800_000)).toBe(true)   // age >= ttl
    expect(isExpired(0, 1_800_001, 1_800_000)).toBe(true)
    expect(isExpired(0, Number.MAX_SAFE_INTEGER, Number.POSITIVE_INFINITY)).toBe(false)
    // The sweep's 1s tick can sit behind a card's own finishedAtMs, so the age is
    // negative — and `-5 >= 0` is false. Without the branch, "disappears immediately"
    // would leave a card up for as much as a second.
    expect(isExpired(1_000, 995, 0)).toBe(true)
    expect(isExpired(1_000, 995, 1_800_000)).toBe(false)
  })

  it('removes the count cap entirely at -1, the manual-delete-only choice', () => {
    expect(cardSlotsFor(-1)).toBe(Number.POSITIVE_INFINITY)
    for (const minutes of RETENTION_DOMAIN_MINUTES.filter((m) => m !== -1)) {
      expect(cardSlotsFor(minutes)).toBe(MAX_FINISHED_CARDS)
    }
  })
})

describe('aiInvokeRuns store — per-user retention', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sessionStorage.clear()
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getRequest).mockResolvedValue({ data: {} } as any)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts from the mirror and falls back to manual-delete-only when there is none', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '1440')
    const mirrored = useAiInvokeRunsStore()
    expect(mirrored.retentionMinutes).toBe(1440)
    mirrored.$dispose()

    setActivePinia(createPinia())
    localStorage.setItem(RETENTION_MIRROR_KEY, 'nonsense')
    const broken = useAiInvokeRunsStore()
    expect(broken.retentionMinutes).toBe(RETENTION_DEFAULT_MINUTES)
    broken.$dispose()
  })

  it('never expires a card by time at -1, and never caps the pile by count either', () => {
    // 0563 T#2: the old 200-slot "unbounded" cap is gone. "-1" is a real choice to let
    // results pile up, and no count silently starts deleting the oldest ones again.
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    const store = useAiInvokeRunsStore()
    finishOne(store, 'g.never.1')

    vi.advanceTimersByTime(2 * 24 * 60 * 60_000)
    expect(store.finishedByRun['run-g.never.1']).toBeDefined()
    expect(store.finishedCount).toBe(1)

    const total = 220   // well past the old 200-card cap
    for (let i = 0; i < total; i += 1) {
      finishOne(store, `g.never.cap.${String(i).padStart(3, '0')}`)
      vi.advanceTimersByTime(1_000)
    }

    // 0563 T0007: the backlog is run-keyed (finishOne's `run-${groupId}`), not group-keyed.
    const remaining = Object.keys(store.finishedByRun)
    expect(remaining).toHaveLength(total + 1)
    // Nothing fell out -- not the first card, not the oldest of the batch.
    expect(remaining).toContain('run-g.never.1')
    expect(remaining).toContain('run-g.never.cap.000')
    expect(remaining).toContain(`run-g.never.cap.${String(total - 1).padStart(3, '0')}`)
    store.$dispose()
  })

  it('makes no finished or lost card at 0, and still leaves paused and handoff alone', async () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '0')
    const store = useAiInvokeRunsStore()

    finishOne(store, 'g.zero.done')
    expect(store.runsByGroup['g.zero.done']).toBeUndefined()
    // 0563 T0007: at retention 0 no card is created anywhere, not just absent from the
    // old group-keyed slot -- the run-keyed history must be equally empty.
    expect(store.finishedByRun['run-g.zero.done']).toBeUndefined()
    expect(store.finishedCount).toBe(0)

    store.trackStarted({ run_id: 'run-lost', group_id: 'g.zero.lost', doc_ref: 'r' })
    store.markLost('g.zero.lost', 'run-lost')
    expect(store.runsByGroup['g.zero.lost']).toBeUndefined()
    expect(store.finishedByRun['run-lost']).toBeUndefined()

    // A user pause and a hop boundary are judged FIRST and are not completions, so
    // "disappears immediately" must not reach either of them (L0003 §4-1).
    store.trackStarted({ run_id: 'run-p', group_id: 'g.zero.paused', doc_ref: 'r', mode: 'continuous' })
    store.trackFinished({ run_id: 'run-p', group_id: 'g.zero.paused', end_reason: 'user_paused' })
    expect(store.runsByGroup['g.zero.paused']?.phase).toBe('paused')

    store.trackStarted({
      run_id: 'run-h', group_id: 'g.zero.handoff', mode: 'continuous',
      chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 6,
    })
    store.trackFinished({
      run_id: 'run-h', group_id: 'g.zero.handoff', end_reason: 'exited',
      stop_code: 'hop_handoff', outcome: 'complete',
      chain_id: 'chain-1', chain_docs_target: 10, chain_docs_reached: 7,
    })
    expect(store.runsByGroup['g.zero.handoff']?.handoffPending).toBe(true)
    expect(store.runsByGroup['g.zero.handoff']?.phase).toBe('running')

    // The retention=0 early return for old A must not clear newer B's group-scoped
    // adoption timer/poll state either.
    store.trackFinished({
      run_id: 'run-old-A', group_id: 'g.zero.handoff',
      end_reason: 'exited', outcome: 'complete',
    })
    expect(store.runsByGroup['g.zero.handoff']).toMatchObject({
      runId: 'run-h', phase: 'running', handoffPending: true,
    })

    // Not one tick: a card must never appear and then be swept a second later. B still
    // reaches normal handoff finalization; retention=0 then removes it without a card.
    await vi.advanceTimersByTimeAsync(5_000)
    await store.refreshAllRunning()
    await store.refreshAllRunning()
    expect(store.finishedCount).toBe(0)
    expect(store.runsByGroup['g.zero.handoff']).toBeUndefined()
    store.$dispose()
  })

  it.each([30, 60, 120, 180, 360, 720, 1440])(
    'removes a card exactly at the %i-minute boundary',
    (minutes) => {
      localStorage.setItem(RETENTION_MIRROR_KEY, String(minutes))
      setActivePinia(createPinia())
      const store = useAiInvokeRunsStore()
      finishOne(store, 'g.boundary')

      vi.advanceTimersByTime(minutes * 60_000 - 1_000)
      expect(store.finishedByRun['run-g.boundary']).toBeDefined()

      vi.advanceTimersByTime(1_000)
      expect(store.finishedByRun['run-g.boundary']).toBeUndefined()
      store.$dispose()
    },
  )

  it('restores by the mirror, and fails open when the mirror is missing', () => {
    const finishedAtMs = Date.now() - 45 * 60_000

    localStorage.setItem(RETENTION_MIRROR_KEY, '30')
    seedFinishedSnapshot('g.restore.30', finishedAtMs)
    const short = useAiInvokeRunsStore()
    // 0563 T0007: restore rekeys every snapshot entry by its own runId (seedFinishedSnapshot
    // always stamps 'run-seed'), regardless of the legacy group-keyed outer object.
    expect(short.finishedByRun['run-seed']).toBeUndefined()
    short.$dispose()

    setActivePinia(createPinia())
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    seedFinishedSnapshot('g.restore.never', finishedAtMs)
    const never = useAiInvokeRunsStore()
    expect(never.finishedByRun['run-seed']).toBeDefined()
    never.$dispose()

    // No mirror: restore everything. Assuming 30 here would permanently delete the cards
    // of somebody who chose "never expires" and has not been told the setting yet — and
    // there is no way back from that. The first sweep after the value lands is the cost
    // of the other direction.
    setActivePinia(createPinia())
    localStorage.removeItem(RETENTION_MIRROR_KEY)
    seedFinishedSnapshot('g.restore.absent', finishedAtMs)
    const failOpen = useAiInvokeRunsStore()
    expect(failOpen.finishedByRun['run-seed']).toBeDefined()
    failOpen.$dispose()
  })

  it('adopts the server value, mirrors it, and sweeps at once', async () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '30')
    const store = useAiInvokeRunsStore()
    finishOne(store, 'g.server.1')
    expect(store.finishedCount).toBe(1)

    vi.mocked(getRequest).mockResolvedValueOnce({
      data: { ok: true, settings: { ai_finished_card_retention_minutes: 0 }, is_default: false },
    } as any)
    await store.refreshRetentionSetting()

    expect(vi.mocked(getRequest)).toHaveBeenCalledWith(UI_SETTINGS_PATH)
    expect(store.retentionMinutes).toBe(0)
    expect(localStorage.getItem(RETENTION_MIRROR_KEY)).toBe('0')
    // Swept on adoption, not on the next 1s tick: the list the user just emptied is empty.
    expect(store.finishedCount).toBe(0)
    store.$dispose()
  })

  it('repairs a server value that is not in the domain', async () => {
    const store = useAiInvokeRunsStore()
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: { settings: { ai_finished_card_retention_minutes: 45 } },
    } as any)
    await store.refreshRetentionSetting()
    expect(store.retentionMinutes).toBe(RETENTION_DEFAULT_MINUTES)
    store.$dispose()
  })

  it('keeps a usable value when the lookup fails, and lands on manual-delete-only when there is none', async () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '720')
    const mirrored = useAiInvokeRunsStore()
    vi.mocked(getRequest).mockRejectedValueOnce(new Error('offline'))
    await mirrored.refreshRetentionSetting()
    expect(mirrored.retentionMinutes).toBe(720)
    expect(localStorage.getItem(RETENTION_MIRROR_KEY)).toBe('720')
    mirrored.$dispose()

    setActivePinia(createPinia())
    localStorage.removeItem(RETENTION_MIRROR_KEY)
    const bare = useAiInvokeRunsStore()
    vi.mocked(getRequest).mockRejectedValueOnce(new Error('offline'))
    await bare.refreshRetentionSetting()
    expect(bare.retentionMinutes).toBe(RETENTION_DEFAULT_MINUTES)
    bare.$dispose()
  })

  it('coalesces overlapping lookups into a single request', async () => {
    const store = useAiInvokeRunsStore()
    vi.mocked(getRequest).mockResolvedValue({
      data: { settings: { ai_finished_card_retention_minutes: 60 } },
    } as any)

    await Promise.all([store.refreshRetentionSetting(), store.refreshRetentionSetting()])

    const calls = vi.mocked(getRequest).mock.calls.filter(([path]) => path === UI_SETTINGS_PATH)
    expect(calls).toHaveLength(1)
    expect(store.retentionMinutes).toBe(60)
    store.$dispose()
  })

  it('adopts another tab\'s save from the storage event and sweeps immediately', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '30')
    const store = useAiInvokeRunsStore()
    finishOne(store, 'g.storage.1')
    expect(store.finishedCount).toBe(1)

    window.dispatchEvent(new StorageEvent('storage', {
      key: RETENTION_MIRROR_KEY, oldValue: '30', newValue: '0',
    }))
    expect(store.retentionMinutes).toBe(0)
    expect(store.finishedCount).toBe(0)

    // Widening the setting does not resurrect what is already gone: retention decides how
    // long results will last from now on, it does not undo removals (L0003 §2-5).
    window.dispatchEvent(new StorageEvent('storage', {
      key: RETENTION_MIRROR_KEY, oldValue: '0', newValue: '-1',
    }))
    expect(store.retentionMinutes).toBe(-1)
    expect(store.finishedCount).toBe(0)

    // Any other key in localStorage is none of this listener's business.
    window.dispatchEvent(new StorageEvent('storage', {
      key: 'fg_refresh_token', oldValue: null, newValue: '0',
    }))
    expect(store.retentionMinutes).toBe(-1)
    store.$dispose()
  })

  it('drops a lookup response that lost the race to a storage event', async () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '30')
    const store = useAiInvokeRunsStore()

    let settle: (value: unknown) => void = () => {}
    vi.mocked(getRequest).mockImplementationOnce(
      () => new Promise((resolve) => { settle = resolve }) as any,
    )
    const pending = store.refreshRetentionSetting()

    window.dispatchEvent(new StorageEvent('storage', {
      key: RETENTION_MIRROR_KEY, oldValue: '30', newValue: '-1',
    }))
    expect(store.retentionMinutes).toBe(-1)

    settle({ data: { settings: { ai_finished_card_retention_minutes: 30 } } })
    await pending

    // The slow answer describes a state that is already two saves old.
    expect(store.retentionMinutes).toBe(-1)
    store.$dispose()
  })

  it('retries a quota-refused write with only the newest cards, then keeps them in memory', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    const store = useAiInvokeRunsStore()
    const total = PERSIST_QUOTA_FALLBACK_CARDS + 3

    // setSystemTime rather than advanceTimersByTime: the cards need distinct
    // finishedAtMs values, but the 1s clock must not fire in between or the refusal
    // would land on a snapshot of one card instead of the full one.
    const base = Date.now()
    for (let i = 0; i < total; i += 1) {
      vi.setSystemTime(new Date(base + i * 1_000))
      finishOne(store, `g.quota.${String(i).padStart(2, '0')}`)
    }
    expect(sessionStorage.getItem(FINISHED_STORAGE_KEY)).toBeNull()

    const realSetItem = Storage.prototype.setItem
    let refusalsLeft = 1
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(
      function (this: Storage, key: string, value: string) {
        if (key === FINISHED_STORAGE_KEY && refusalsLeft > 0) {
          refusalsLeft -= 1
          throw new DOMException('quota exceeded', 'QuotaExceededError')
        }
        return realSetItem.call(this, key, value)
      },
    )
    try {
      vi.advanceTimersByTime(1_000)   // one flush, refused once, retried once
    } finally {
      spy.mockRestore()
    }
    expect(refusalsLeft).toBe(0)

    const stored = JSON.parse(sessionStorage.getItem(FINISHED_STORAGE_KEY) as string)
    expect(Object.keys(stored)).toHaveLength(PERSIST_QUOTA_FALLBACK_CARDS)
    // 0563 T0007: the persisted snapshot is keyed by runId (finishOne's `run-${groupId}`).
    expect(stored[`run-g.quota.${String(total - 1).padStart(2, '0')}`]).toBeDefined()
    expect(stored['run-g.quota.00']).toBeUndefined()
    // The write was given up on; the registry was not.
    expect(store.finishedCount).toBe(total)
    store.$dispose()
  })

  it('caps the inline banner at 60s regardless of the retention, and closes it at 0', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    const never = useAiInvokeRunsStore()
    expect(never.inlineResultWindowMs).toBe(60_000)
    never.$dispose()

    setActivePinia(createPinia())
    localStorage.setItem(RETENTION_MIRROR_KEY, '1440')
    const long = useAiInvokeRunsStore()
    expect(long.inlineResultWindowMs).toBe(60_000)
    long.$dispose()

    setActivePinia(createPinia())
    localStorage.setItem(RETENTION_MIRROR_KEY, '0')
    const immediate = useAiInvokeRunsStore()
    expect(immediate.inlineResultWindowMs).toBe(0)
    immediate.$dispose()
  })
})


describe('document review loop state normalization (0417 T0013)', () => {
  it('preserves monotonic rounds across start, poll/SSE-shaped updates, finish, and duplicate history', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0417'
    store.trackStarted({
      run_id: 'loop-1', group_id: groupId, status: 'running',
      document_review_loop: {
        round_no: 1, current_stage: 'review',
        history: [{ round_no: 1, stage: 'review', result: 'issues' }],
      },
    })
    store.trackStarted({
      run_id: 'loop-1', group_id: groupId, status: 'running',
      document_review_loop: {
        round_no: 2, current_stage: 'rework',
        history: [
          { round_no: 1, stage: 'review', result: 'issues' },
          { round_no: 2, stage: 'rework', result: 'complete' },
        ],
      },
    })
    store.trackStarted({
      run_id: 'loop-1', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'review' },
    })
    expect(store.runsByGroup[groupId].documentReviewLoop).toMatchObject({
      roundNo: 2, currentStage: 'rework',
    })
    expect(store.runsByGroup[groupId].documentReviewLoop?.history).toHaveLength(2)

    store.trackFinished({
      run_id: 'loop-1', group_id: groupId, status: 'finished', outcome: 'complete',
      document_review_loop: {
        round_no: 2, current_stage: 'stopped', stop_reason: 'review_passed',
        stop_detail: 'passed on round 2',
        history: [{ round_no: 2, stage: 'rework', result: 'complete' }],
      },
    })
    // 0563 T0007: this trackFinished call is a genuine completion, so the card moved
    // into finishedByRun -- keyed by the run's own id ('loop-1').
    expect(store.finishedByRun['loop-1'].documentReviewLoop).toMatchObject({
      roundNo: 2, currentStage: 'stopped', stopReason: 'review_passed',
      stopDetail: 'passed on round 2',
    })
    // A LATE, shorter copy of the table (an older response arriving after a newer one)
    // never shrinks what is already on screen.
    expect(store.finishedByRun['loop-1'].documentReviewLoop?.history).toHaveLength(2)

    // The server's table is the authority: a fuller one replaces the card's rows outright.
    store.trackFinished({
      run_id: 'loop-1', group_id: groupId, status: 'finished', outcome: 'complete',
      document_review_loop: {
        round_no: 2, current_stage: 'stopped', stop_reason: 'review_passed',
        history: [
          { round_no: 1, stage: 'review', result: 'issues', finding_count: 3, at: '2026-08-29T12:04:00+09:00' },
          { round_no: 1, stage: 'rework', result: 'complete', at: '2026-08-29T12:19:00+09:00' },
          { round_no: 2, stage: 'review', result: 'passed', finding_count: 0, at: '2026-08-29T12:26:00+09:00' },
        ],
      },
    })
    expect(store.finishedByRun['loop-1'].documentReviewLoop?.history).toHaveLength(3)
    expect(store.finishedByRun['loop-1'].documentReviewLoop?.history[0]).toMatchObject({
      round_no: 1, stage: 'review', result: 'issues', finding_count: 3,
    })
    store.$dispose()
  })

  // 0417 T0013 item 7 (bootstrap/reconnect restores the same card) and the TR0018 rejection:
  // the round table must survive a refresh, so it may not be derived from transitions this
  // browser watched. A brand-new store that observed NOTHING still shows every round.
  it('restores the whole round table on bootstrap without having observed any transition', async () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0417.reconnect'
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [{
          run_id: 'loop-reconnect', group_id: groupId, status: 'running', mode: 'single',
          doc_ref: 'flowgate.default.0417.0011-T',
          document_review_loop: {
            round_no: 3, current_stage: 'review', stop_reason: null, stop_detail: null,
            history: [
              { round_no: 1, stage: 'review', result: 'issues', finding_count: 3, at: '2026-08-29T12:04:00+09:00' },
              { round_no: 1, stage: 'rework', result: 'complete', at: '2026-08-29T12:19:00+09:00' },
              { round_no: 2, stage: 'review', result: 'issues', finding_count: 1, at: '2026-08-29T12:26:00+09:00' },
              { round_no: 2, stage: 'rework', result: 'complete', at: '2026-08-29T12:38:00+09:00' },
            ],
          },
        }],
        paused: [],
      },
    } as any)

    await store.bootstrap()

    const loop = store.runsByGroup[groupId].documentReviewLoop
    expect(loop).toMatchObject({ roundNo: 3, currentStage: 'review' })
    expect(loop?.history).toHaveLength(4)
    expect(loop?.history.map(row => `${row.round_no}:${row.stage}:${row.result}`)).toEqual([
      '1:review:issues', '1:rework:complete', '2:review:issues', '2:rework:complete',
    ])
    store.$dispose()
  })

  // The other half of the same rule: nothing is invented locally. A run whose payloads carry
  // no history array leaves the table empty however many stage transitions go by, instead of
  // manufacturing rows that a reconnecting browser could never reproduce.
  it('never derives rows from observed stage transitions', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0417.no-derivation'
    const base = { run_id: 'loop-observed', group_id: groupId, status: 'running' }
    store.trackStarted({ ...base, document_review_loop: { round_no: 1, current_stage: 'review' } })
    store.trackStarted({ ...base, document_review_loop: { round_no: 2, current_stage: 'rework' } })
    store.trackFinished({
      ...base, status: 'finished', outcome: 'complete',
      document_review_loop: { round_no: 2, current_stage: 'stopped', stop_reason: 'review_passed' },
    })
    // 0563 T0007: a genuine completion moves the card into finishedByRun, keyed by
    // the run's own id ('loop-observed').
    expect(store.finishedByRun['loop-observed'].documentReviewLoop).toMatchObject({
      roundNo: 2, currentStage: 'stopped', stopReason: 'review_passed',
    })
    expect(store.finishedByRun['loop-observed'].documentReviewLoop?.history).toEqual([])
    store.$dispose()
  })
})


describe('document review loop attempts_used exposure (0569 T0006)', () => {
  it('normalizes attempts_used and failure_restart_max_attempts from snake_case', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0569.normalize'
    store.trackStarted({
      run_id: 'loop-attempts-1', group_id: groupId, status: 'running',
      document_review_loop: {
        round_no: 1, current_stage: 'review',
        attempts_used: 1, failure_restart_max_attempts: 1,
      },
    })
    expect(store.runsByGroup[groupId].documentReviewLoop).toMatchObject({
      attemptsUsed: 1, failureRestartMaxAttempts: 1,
    })
    store.$dispose()
  })

  it('keeps the stage-local retry count at 1 across a same-stage retry poll', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0569.retry'
    store.trackStarted({
      run_id: 'loop-attempts-2', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'review', attempts_used: 0, failure_restart_max_attempts: 1 },
    })
    store.trackStarted({
      run_id: 'loop-attempts-2', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'review', attempts_used: 1, failure_restart_max_attempts: 1 },
    })
    expect(store.runsByGroup[groupId].documentReviewLoop).toMatchObject({
      roundNo: 1, currentStage: 'review', attemptsUsed: 1, failureRestartMaxAttempts: 1,
    })
    store.$dispose()
  })

  // The critical regression T0006 4.2 calls out: a successful retry resets attempts_used to
  // 0 on the SAME transition that moves the stage on, and that reset must be accepted -- not
  // clamped to the previous max the way roundNo is.
  it('accepts the 1 -> 0 reset when the stage transitions after a successful retry', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0569.reset'
    store.trackStarted({
      run_id: 'loop-attempts-3', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'review', attempts_used: 1, failure_restart_max_attempts: 1 },
    })
    store.trackStarted({
      run_id: 'loop-attempts-3', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'rework', attempts_used: 0, failure_restart_max_attempts: 1 },
    })
    expect(store.runsByGroup[groupId].documentReviewLoop).toMatchObject({
      currentStage: 'rework', attemptsUsed: 0,
    })
    store.$dispose()
  })

  // Older/partial payloads (a server that has not deployed the new fields yet, or a
  // trimmed SSE event) must not be read as "no retries" -- the field is simply absent,
  // not zero, so the previous observed value has to survive.
  it('preserves attemptsUsed/failureRestartMaxAttempts when a later payload omits the fields', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0569.partial'
    store.trackStarted({
      run_id: 'loop-attempts-4', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'review', attempts_used: 1, failure_restart_max_attempts: 1 },
    })
    store.trackStarted({
      run_id: 'loop-attempts-4', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'review' },
    })
    expect(store.runsByGroup[groupId].documentReviewLoop).toMatchObject({
      attemptsUsed: 1, failureRestartMaxAttempts: 1,
    })
    store.$dispose()
  })

  // A stale/regressed payload (round_no going backwards, arriving after a newer one) must
  // not be allowed to overwrite the currently-displayed retry count either -- the same
  // staleness guard that already protects currentStage/roundNo.
  it('ignores attempts_used from a stale payload the same way currentStage already is', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0569.stale'
    store.trackStarted({
      run_id: 'loop-attempts-5', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 2, current_stage: 'review', attempts_used: 1, failure_restart_max_attempts: 1 },
    })
    store.trackStarted({
      run_id: 'loop-attempts-5', group_id: groupId, status: 'running',
      document_review_loop: { round_no: 1, current_stage: 'review', attempts_used: 0, failure_restart_max_attempts: 1 },
    })
    expect(store.runsByGroup[groupId].documentReviewLoop).toMatchObject({
      roundNo: 2, currentStage: 'review', attemptsUsed: 1,
    })
    store.$dispose()
  })
})

// 0563 T0007 §11: the required regressions for the run-keyed finished-history split.
// TC10 (existing lifecycle regressions keep passing) is the rest of this file and the
// rest of the suite staying green, not a test of its own.
describe('aiInvokeRuns store — run-keyed finished history (0563 T0007)', () => {
  let store: ReturnType<typeof useAiInvokeRunsStore>

  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    setActivePinia(createPinia())
    store = useAiInvokeRunsStore()
    vi.clearAllMocks()
  })

  afterEach(() => {
    store.$dispose()
  })

  // TC1 — a new run starting in the same group must not evict the previous finished card.
  it('keeps a finished card when a new run starts in the same group', () => {
    const groupId = 'flowgate.default.0563.tc1'
    store.trackStarted({ run_id: 'run-A', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({ run_id: 'run-A', group_id: groupId, outcome: 'complete' })
    store.trackStarted({ run_id: 'run-B', group_id: groupId, doc_ref: 'b' })

    expect(store.finishedByRun['run-A']).toMatchObject({ phase: 'finished' })
    expect(store.runsByGroup[groupId]).toMatchObject({ runId: 'run-B', phase: 'running' })
  })

  // TC2 — two finished runs from the same group must coexist.
  it('lets two finished runs from the same group coexist', () => {
    const groupId = 'flowgate.default.0563.tc2'
    store.trackStarted({ run_id: 'run-A', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({ run_id: 'run-A', group_id: groupId, outcome: 'complete' })
    store.trackStarted({ run_id: 'run-B', group_id: groupId, doc_ref: 'b' })
    store.trackFinished({ run_id: 'run-B', group_id: groupId, outcome: 'complete' })

    expect(store.finishedByRun['run-A']).toBeDefined()
    expect(store.finishedByRun['run-B']).toBeDefined()
    expect(store.finishedCount).toBe(2)
  })

  // TC3 — removing one finished run by its own id must not touch a sibling in the same
  // group, and the durable DELETE must address exactly that run's own id.
  it('removes one finished run by its own id without touching a sibling in the same group', async () => {
    const groupId = 'flowgate.default.0563.tc3'
    store.trackStarted({ run_id: 'run-A', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({
      run_id: 'run-A', group_id: groupId, outcome: 'complete', persisted: true,
    })
    store.trackStarted({ run_id: 'run-B', group_id: groupId, doc_ref: 'b' })
    store.trackFinished({ run_id: 'run-B', group_id: groupId, outcome: 'complete' })

    vi.mocked(deleteRequest).mockResolvedValueOnce({
      data: { ok: true, dismissed: true, already_dismissed: false },
    } as any)

    await store.removeCard(store.finishedByRun['run-A'])

    expect(vi.mocked(deleteRequest)).toHaveBeenCalledWith('/api/v1/ai-invoke/runs/run-A/card')
    expect(store.finishedByRun['run-A']).toBeUndefined()
    expect(store.finishedByRun['run-B']).toBeDefined()
  })

  // TC4 — several finished runs from the same group must all survive a reload.
  it('restores several finished runs from the same group across a reload', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, String(RETENTION_DEFAULT_MINUTES))
    const groupId = 'flowgate.default.0563.tc4'
    store.trackStarted({ run_id: 'run-A', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({ run_id: 'run-A', group_id: groupId, outcome: 'complete' })
    store.trackStarted({ run_id: 'run-B', group_id: groupId, doc_ref: 'b' })
    store.trackFinished({ run_id: 'run-B', group_id: groupId, outcome: 'complete' })
    store.$dispose()

    setActivePinia(createPinia())
    const reloaded = useAiInvokeRunsStore()
    expect(reloaded.finishedByRun['run-A']).toBeDefined()
    expect(reloaded.finishedByRun['run-B']).toBeDefined()
    reloaded.$dispose()
  })

  // TC6 — an active run and a finished run from the same group must both be visible in
  // the merged projection, sorted active-first per the existing priority.
  it('keeps an active run and a finished run from the same group both visible, active first', () => {
    const groupId = 'flowgate.default.0563.tc6'
    store.trackStarted({ run_id: 'run-A', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({ run_id: 'run-A', group_id: groupId, outcome: 'complete' })
    store.trackStarted({ run_id: 'run-B', group_id: groupId, doc_ref: 'b' })

    expect(store.activeCount).toBe(1)
    expect(store.finishedCount).toBe(1)
    const ordered = store.allEntries.slice().sort(compareRunEntries)
    expect(ordered.map((e) => e.runId)).toEqual(['run-B', 'run-A'])
  })

  // TC7 — a duplicate finished event for the same run must update, not duplicate, the card.
  it('does not duplicate a finished card on a repeated finished event for the same run', () => {
    const groupId = 'flowgate.default.0563.tc7'
    store.trackStarted({ run_id: 'run-A', group_id: groupId, doc_ref: 'a' })
    store.trackFinished({ run_id: 'run-A', group_id: groupId, outcome: 'complete' })
    store.trackFinished({ run_id: 'run-A', group_id: groupId, outcome: 'complete' })

    expect(store.finishedCount).toBe(1)
    expect(Object.keys(store.finishedByRun)).toEqual(['run-A'])
  })

  // TC8 — a locally restored finished card and the same durable card active-all returns
  // on bootstrap must reconcile into one, not two.
  it('reconciles a locally restored finished card with the same durable card from bootstrap', async () => {
    // Seed sessionStorage and construct a FRESH store -- loadPersistedFinished() only
    // runs once, at store construction, so the beforeEach store above never sees this.
    store.$dispose()
    localStorage.setItem(RETENTION_MIRROR_KEY, String(RETENTION_DEFAULT_MINUTES))
    const runId = 'aiv_tc8'
    const groupId = 'flowgate.default.0563.tc8'
    const finishedAtIso = new Date().toISOString()
    sessionStorage.setItem(FINISHED_STORAGE_KEY, JSON.stringify({
      [runId]: {
        runId, groupId, docRef: 'r', phase: 'finished', mode: 'single',
        handoffPending: false, endReason: 'exited', outcome: 'complete',
        pendingQDocIds: [], reachedDocIds: [], finishedAtMs: Date.parse(finishedAtIso),
        persisted: true,
      },
    }))
    setActivePinia(createPinia())
    const restored = useAiInvokeRunsStore()
    expect(Object.keys(restored.finishedByRun)).toHaveLength(1)

    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [{
          run_id: runId, group_id: groupId, doc_ref: 'r', status: 'finished',
          mode: 'single', outcome: 'complete', end_reason: 'exited',
          finished_at: finishedAtIso, persisted: true,
        }],
        paused: [],
      },
    } as any)
    await restored.bootstrap()

    expect(Object.keys(restored.finishedByRun)).toHaveLength(1)
    expect(restored.finishedByRun[runId].persisted).toBe(true)
    restored.$dispose()
  })

  // TC9 — a new active run in the group must survive the FIRST late finished event AND
  // bootstrap response for the run it replaced, while that old run is still recorded.
  it('records an old run first seen finished after a new run became active without replacing the new run', async () => {
    const groupId = 'flowgate.default.0563.tc9'
    store.trackStarted({ run_id: 'run-A', group_id: groupId, doc_ref: 'a' })
    store.trackStarted({ run_id: 'run-B', group_id: groupId, doc_ref: 'b' })

    expect(store.finishedByRun['run-A']).toBeUndefined()

    // The first finished payload for the OLD run must create its history card without
    // touching the newer active run that already owns this group.
    store.trackFinished({ run_id: 'run-A', group_id: groupId, doc_ref: 'a', outcome: 'complete' })
    expect(store.finishedByRun['run-A']).toMatchObject({
      runId: 'run-A', groupId, docRef: 'a', phase: 'finished', outcome: 'complete',
    })
    expect(store.runsByGroup[groupId]).toMatchObject({ runId: 'run-B', phase: 'running' })

    // A stale bootstrap response that only knows about the old run must not clear the
    // new active run either.
    vi.mocked(getRequest).mockResolvedValueOnce({
      data: {
        ok: true,
        runs: [{
          run_id: 'run-A', group_id: groupId, doc_ref: 'a',
          status: 'finished', outcome: 'complete',
        }],
        paused: [],
      },
    } as any)
    await store.bootstrap()
    expect(store.runsByGroup[groupId]).toMatchObject({ runId: 'run-B', phase: 'running' })
    expect(Object.keys(store.finishedByRun)).toEqual(['run-A'])
  })
})
