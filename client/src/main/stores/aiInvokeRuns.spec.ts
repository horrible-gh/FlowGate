import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getRequest } from '@shared/api'
import { useAiInvokeRunsStore } from './aiInvokeRuns'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

describe('aiInvokeRuns store', () => {
  let store: ReturnType<typeof useAiInvokeRunsStore>

  beforeEach(() => {
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
    expect(run.finishedPayload?.last_message).toBe('done')
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
})