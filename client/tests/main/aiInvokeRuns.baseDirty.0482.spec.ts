import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(), postRequest: vi.fn(), deleteRequest: vi.fn(),
}))

describe('0482 group-less base-dirty run registry', () => {
  let store: ReturnType<typeof useAiInvokeRunsStore>

  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    setActivePinia(createPinia())
    store = useAiInvokeRunsStore()
  })

  afterEach(() => store.$dispose())

  it('accepts group_id:null under a project key and keeps provider metadata', () => {
    store.trackStarted({
      run_id: 'base-run', group_id: null, project_id: 'flowgate',
      action_scope: 'resolve_base_dirty', provider_id: 'p1', provider_name: 'Provider 1',
      doc_ref: '', docs_target: 0,
    })

    const run = store.runsByGroup['project:flowgate']
    expect(run).toBeDefined()
    expect(run.runId).toBe('base-run')
    expect(run.groupId).toBe('project:flowgate')
    expect(run.provider?.id).toBe('p1')
    expect(store.isGroupRunning('project:flowgate')).toBe(true)
  })

  it('finishes the same project-scoped card without creating a literal null group', () => {
    store.trackStarted({ run_id: 'base-run', group_id: null, project_id: 'flowgate', action_scope: 'resolve_base_dirty' })
    store.applySse({ kind: 'finished', payload: {
      run_id: 'base-run', group_id: null, project_id: 'flowgate',
      action_scope: 'resolve_base_dirty', outcome: 'complete',
    } })

    expect(store.runsByGroup['project:flowgate'].phase).toBe('finished')
    expect(store.runsByGroup.null).toBeUndefined()
    expect(store.isGroupRunning('project:flowgate')).toBe(false)
  })

  // flowgate.default.0481 T0010 #1 — the SSE events are not the HTTP response.
  //
  // The route nulls `group_id` in its own reply, which is why the two tests above pass. The
  // engine's `ai_invoke_started` / `ai_invoke_finished` events are built from the run record,
  // and that record carries the synthetic `<project>.none.0000` group the lease is keyed by.
  // While `payloadGroupKey` read `group_id` first, every live event filed the card under a
  // group nobody watches: no live card, and GitStatusPanel's `phase === 'finished'` watcher
  // (which re-enables [AI에게 맡기기] and refreshes the status) never fired.
  it('files the SSE events under the project key even though they carry the synthetic group', () => {
    store.applySse({ kind: 'started', payload: {
      run_id: 'base-run', group_id: 'flowgate.none.0000', project_id: 'flowgate',
      action_scope: 'resolve_base_dirty', doc_ref: 'flowgate', docs_target: 0,
    } })

    expect(store.runsByGroup['project:flowgate']?.runId).toBe('base-run')
    expect(store.runsByGroup['flowgate.none.0000']).toBeUndefined()

    store.applySse({ kind: 'finished', payload: {
      run_id: 'base-run', group_id: 'flowgate.none.0000', project_id: 'flowgate',
      action_scope: 'resolve_base_dirty', outcome: 'complete',
    } })

    expect(store.runsByGroup['project:flowgate'].phase).toBe('finished')
    expect(store.runsByGroup['flowgate.none.0000']).toBeUndefined()
  })

  it('still files an ordinary group run under its own group', () => {
    store.applySse({ kind: 'started', payload: {
      run_id: 'edit-run', group_id: 'flowgate.default.0481', project_id: 'flowgate',
      action_scope: 'edit', doc_ref: 'flowgate.default.0481.0010-T', docs_target: 1,
    } })

    expect(store.runsByGroup['flowgate.default.0481']?.runId).toBe('edit-run')
    expect(store.runsByGroup['project:flowgate']).toBeUndefined()
  })
})