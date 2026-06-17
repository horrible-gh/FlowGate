import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useDashboardStore, type DashboardSummary } from '@main/stores/dashboard'
import { useProjectStore } from '@main/stores/project'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  getRequest,
}))

function summary(projectId: string, generatedAt: string): DashboardSummary {
  return {
    ok: true,
    project_id: projectId,
    generated_at: generatedAt,
    recent_activities: { limit: 10, total: 0, has_more: false, items: [] },
    active_workflows: { limit: 10, total: 0, has_more: false, items: [] },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
  vi.useRealTimers()
})

describe('dashboard store', () => {
  it('keeps project responses isolated', async () => {
    const store = useDashboardStore()
    const projectStore = useProjectStore()
    projectStore.setCurrentProject('beta')
    getRequest
      .mockResolvedValueOnce({ data: summary('alpha', '2026-06-12T00:00:00Z') })
      .mockResolvedValueOnce({ data: summary('beta', '2026-06-12T00:01:00Z') })

    await store.fetchSummary('alpha')
    await store.fetchSummary('beta')

    expect(store.entryFor('alpha').data?.project_id).toBe('alpha')
    expect(store.currentEntry?.data?.project_id).toBe('beta')
  })

  it('does not apply an older generated_at response', async () => {
    const store = useDashboardStore()
    getRequest
      .mockResolvedValueOnce({ data: summary('alpha', '2026-06-12T00:02:00Z') })
      .mockResolvedValueOnce({ data: summary('alpha', '2026-06-12T00:01:00Z') })

    await store.fetchSummary('alpha')
    await store.fetchSummary('alpha')

    expect(store.entryFor('alpha').data?.generated_at).toBe('2026-06-12T00:02:00Z')
  })

  it('debounces repeated invalidations', async () => {
    vi.useFakeTimers()
    const store = useDashboardStore()
    getRequest.mockResolvedValue({ data: summary('alpha', '2026-06-12T00:00:00Z') })

    store.invalidate('alpha')
    store.invalidate('alpha')
    store.invalidate('alpha')
    expect(getRequest).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(250)
    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  it('runs a follow-up fetch when invalidated during a request', async () => {
    vi.useFakeTimers()
    const store = useDashboardStore()
    let resolveFirst!: (value: unknown) => void
    getRequest
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce({ data: summary('alpha', '2026-06-12T00:01:00Z') })

    const first = store.fetchSummary('alpha')
    store.invalidate('alpha')
    resolveFirst({ data: summary('alpha', '2026-06-12T00:00:00Z') })
    await first

    await vi.advanceTimersByTimeAsync(250)
    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('keeps existing data when a refresh fails', async () => {
    const store = useDashboardStore()
    getRequest
      .mockResolvedValueOnce({ data: summary('alpha', '2026-06-12T00:00:00Z') })
      .mockRejectedValueOnce(new Error('network'))

    await store.fetchSummary('alpha')
    await store.fetchSummary('alpha')

    expect(store.entryFor('alpha').data).not.toBeNull()
    expect(store.entryFor('alpha').error).toBe('network')
  })
})
