import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getRequest, postRequest } from '@shared/api'
import {
  FINISHED_CARD_TTL_MS,
  MAX_FINISHED_CARDS,
  MAX_FINISHED_CARDS_UNBOUNDED,
  PERSIST_QUOTA_FALLBACK_CARDS,
  cardSlotsFor,
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

  it('keeps a restored pause card and rejects a resume launch 409', async () => {
    const groupId = 'flowgate.default.0384'
    store.trackFinished({
      run_id: 'run-old', group_id: groupId, doc_ref: 'r', end_reason: 'user_paused',
    })
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

    expect(store.runsByGroup[groupId]).toMatchObject({
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
    expect(store.runsByGroup[groupId].handoffPending).toBe(false)
    expect(store.runsByGroup[groupId].phase).toBe('finished')
    expect(isFinishedCard(store.runsByGroup[groupId])).toBe(true)

    const activeAllCalls = vi.mocked(getRequest).mock.calls
      .filter(([path]) => path === '/api/v1/ai-invoke/active-all')
    expect(activeAllCalls.length).toBeGreaterThanOrEqual(6)
  })
})

// Every case in this block is the DEFAULT retention — somebody who has never opened the
// account screen. 0452 kept that number at 30 minutes precisely so these regressions keep
// meaning what they meant; the per-user cases live in the block after it.
describe('aiInvokeRuns store — finished-card TTL sweep', () => {
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
    // The mirror is what a reload judges by (0452 L0003 §2-4), and 30 is what it holds for
    // a user who never changed the setting. Without it this case would be the fail-open
    // branch instead, which the next block covers on its own.
    localStorage.setItem(RETENTION_MIRROR_KEY, String(RETENTION_DEFAULT_MINUTES))
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

  it('widens the slot count only for the unbounded choice', () => {
    expect(cardSlotsFor(-1)).toBe(MAX_FINISHED_CARDS_UNBOUNDED)
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

  it('starts from the mirror and falls back to 30 when there is none', () => {
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

  it('never expires a card by time at -1, and caps the pile at 200 instead of 20', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    const store = useAiInvokeRunsStore()
    finishOne(store, 'g.never.1')

    vi.advanceTimersByTime(2 * 24 * 60 * 60_000)
    expect(store.runsByGroup['g.never.1']).toBeDefined()
    expect(store.finishedCount).toBe(1)

    for (let i = 0; i < MAX_FINISHED_CARDS_UNBOUNDED + 2; i += 1) {
      finishOne(store, `g.never.cap.${String(i).padStart(3, '0')}`)
      vi.advanceTimersByTime(1_000)   // distinct finishedAtMs so "oldest" is unambiguous
    }

    const remaining = Object.keys(store.runsByGroup)
    expect(remaining).toHaveLength(MAX_FINISHED_CARDS_UNBOUNDED)
    // The first card and the two oldest of the batch are what fell out, newest first out
    // is never the rule.
    expect(remaining).not.toContain('g.never.1')
    expect(remaining).not.toContain('g.never.cap.000')
    expect(remaining).toContain(
      `g.never.cap.${String(MAX_FINISHED_CARDS_UNBOUNDED + 1).padStart(3, '0')}`,
    )
    store.$dispose()
  })

  it('makes no finished or lost card at 0, and still leaves paused and handoff alone', () => {
    localStorage.setItem(RETENTION_MIRROR_KEY, '0')
    const store = useAiInvokeRunsStore()

    finishOne(store, 'g.zero.done')
    expect(store.runsByGroup['g.zero.done']).toBeUndefined()
    expect(store.finishedCount).toBe(0)

    store.trackStarted({ run_id: 'run-lost', group_id: 'g.zero.lost', doc_ref: 'r' })
    store.markLost('g.zero.lost', 'run-lost')
    expect(store.runsByGroup['g.zero.lost']).toBeUndefined()

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

    // Not one tick: a card must never appear and then be swept a second later.
    vi.advanceTimersByTime(5_000)
    expect(store.finishedCount).toBe(0)
    expect(store.runsByGroup['g.zero.paused']?.phase).toBe('paused')
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
      expect(store.runsByGroup['g.boundary']).toBeDefined()

      vi.advanceTimersByTime(1_000)
      expect(store.runsByGroup['g.boundary']).toBeUndefined()
      store.$dispose()
    },
  )

  it('restores by the mirror, and fails open when the mirror is missing', () => {
    const finishedAtMs = Date.now() - 45 * 60_000

    localStorage.setItem(RETENTION_MIRROR_KEY, '30')
    seedFinishedSnapshot('g.restore.30', finishedAtMs)
    const short = useAiInvokeRunsStore()
    expect(short.runsByGroup['g.restore.30']).toBeUndefined()
    short.$dispose()

    setActivePinia(createPinia())
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    seedFinishedSnapshot('g.restore.never', finishedAtMs)
    const never = useAiInvokeRunsStore()
    expect(never.runsByGroup['g.restore.never']).toBeDefined()
    never.$dispose()

    // No mirror: restore everything. Assuming 30 here would permanently delete the cards
    // of somebody who chose "never expires" and has not been told the setting yet — and
    // there is no way back from that. The first sweep after the value lands is the cost
    // of the other direction.
    setActivePinia(createPinia())
    localStorage.removeItem(RETENTION_MIRROR_KEY)
    seedFinishedSnapshot('g.restore.absent', finishedAtMs)
    const failOpen = useAiInvokeRunsStore()
    expect(failOpen.runsByGroup['g.restore.absent']).toBeDefined()
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

  it('keeps a usable value when the lookup fails, and lands on 30 when there is none', async () => {
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
    expect(stored[`g.quota.${String(total - 1).padStart(2, '0')}`]).toBeDefined()
    expect(stored['g.quota.00']).toBeUndefined()
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
