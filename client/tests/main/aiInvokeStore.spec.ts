import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('group-scoped AI invoke store', () => {
  it('keeps concurrent runs isolated by group and settles the matching SSE run', () => {
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'r1', group_id: 'p.none.0001', doc_ref: 'd1' })
    store.trackStarted({ run_id: 'r2', group_id: 'p.none.0002', doc_ref: 'd2' })

    window.dispatchEvent(new CustomEvent('fg:ai_invoke', { detail: {
      kind: 'finished',
      payload: {
        run_id: 'r1', group_id: 'p.none.0001', outcome: 'complete',
        docs_reached: 1, docs_target: 1, duration_ms: 1200,
      },
    } }))

    expect(store.runsByGroup['p.none.0001'].phase).toBe('finished')
    expect(store.runsByGroup['p.none.0002'].phase).toBe('running')
    expect(store.activeCount).toBe(1)
  })

  it('discovers an active group run after navigation or reload', async () => {
    getRequest.mockResolvedValueOnce({ data: {
      ok: true,
      active: true,
      run_id: 'r3',
      group_id: 'p.none.0003',
      doc_ref: 'd3',
      mode: 'single',
      status: 'running',
      docs_target: 1,
      elapsed_ms: 500,
    } })
    const store = useAiInvokeRunsStore()

    await store.discover('p.none.0003')

    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/active', { group_id: 'p.none.0003' })
    expect(store.runsByGroup['p.none.0003'].runId).toBe('r3')
  })

  it('keeps provider metadata when the started SSE is the first observation', () => {
    const store = useAiInvokeRunsStore()

    window.dispatchEvent(new CustomEvent('fg:ai_invoke', { detail: {
      kind: 'started',
      payload: {
        run_id: 'r4', group_id: 'p.none.0004', doc_ref: 'd4',
        provider_id: 'aip_1', provider_name: 'Codex', attempt_no: 1,
      },
    } }))

    expect(store.runsByGroup['p.none.0004'].provider).toEqual({ id: 'aip_1', name: 'Codex' })
  })

  it('refreshes immediately when cancel races a natural finish', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r5', status: 'finished' } })
    getRequest.mockResolvedValueOnce({ data: {
      ok: true, run_id: 'r5', group_id: 'p.none.0005', status: 'finished',
      outcome: 'complete', docs_reached: 1, docs_target: 1, duration_ms: 800,
    } })
    const store = useAiInvokeRunsStore()
    store.trackStarted({ run_id: 'r5', group_id: 'p.none.0005', doc_ref: 'd5' })

    await store.cancel('p.none.0005')

    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/r5')
    expect(store.runsByGroup['p.none.0005'].phase).toBe('finished')
  })
})
