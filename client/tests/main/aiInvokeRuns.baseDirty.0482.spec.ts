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
})