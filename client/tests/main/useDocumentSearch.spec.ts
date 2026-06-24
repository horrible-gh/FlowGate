import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useDocumentSearch } from '@main/composables/useDocumentSearch'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({ getRequest }))

// useDocumentSearch calls useI18n().t for the error fallback; stub it so the
// composable can run outside a mounted component.
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (k: string) => k }),
}))

function okResponse(items: any[], total = items.length) {
  return { data: { ok: true, query: 'x', total, offset: 0, limit: 50, items } }
}

beforeEach(() => {
  getRequest.mockReset()
})

describe('useDocumentSearch', () => {
  it('fetches results and exposes them', async () => {
    getRequest.mockResolvedValueOnce(okResponse([{ doc_id: 'R0001', title: 'Root' }], 1))
    const s = useDocumentSearch()
    s.query.value = 'root'

    const ok = await s.search()

    expect(ok).toBe(true)
    expect(s.results.value).toHaveLength(1)
    expect(s.results.value[0].doc_id).toBe('R0001')
    expect(s.total.value).toBe(1)
    expect(s.searched.value).toBe(true)
    expect(s.loading.value).toBe(false)
  })

  it('passes q and only the provided facets as params', async () => {
    getRequest.mockResolvedValueOnce(okResponse([]))
    const s = useDocumentSearch()
    s.query.value = '  Root  '

    await s.search({ type: 'R' }, 25, 10)

    expect(getRequest).toHaveBeenCalledWith('/api/v1/search/documents', {
      q: 'Root',
      limit: 25,
      offset: 10,
      type: 'R',
    })
  })

  it('routes to the content endpoint in content mode', async () => {
    getRequest.mockResolvedValueOnce(okResponse([{ doc_id: 'R0001', snippet: '…hit…' }], 1))
    const s = useDocumentSearch()
    s.query.value = 'marker'

    await s.search({}, 50, 0, 'content')

    expect(getRequest).toHaveBeenCalledWith('/api/v1/search/documents/content', {
      q: 'marker',
      limit: 50,
      offset: 0,
    })
    expect(s.results.value[0].snippet).toBe('…hit…')
  })

  it('skips the request and clears state for a blank query', async () => {
    const s = useDocumentSearch()
    s.query.value = '   '

    const ok = await s.search()

    expect(ok).toBe(false)
    expect(getRequest).not.toHaveBeenCalled()
    expect(s.results.value).toEqual([])
    expect(s.searched.value).toBe(false)
  })

  it('surfaces the backend error message', async () => {
    getRequest.mockRejectedValueOnce({ response: { data: { error_message: 'boom' } } })
    const s = useDocumentSearch()
    s.query.value = 'root'

    const ok = await s.search()

    expect(ok).toBe(false)
    expect(s.error.value).toBe('boom')
    expect(s.results.value).toEqual([])
    expect(s.searched.value).toBe(true)
    expect(s.loading.value).toBe(false)
  })

  it('reset clears query and results', async () => {
    getRequest.mockResolvedValueOnce(okResponse([{ doc_id: 'R0001' }], 1))
    const s = useDocumentSearch()
    s.query.value = 'root'
    await s.search()

    s.reset()

    expect(s.query.value).toBe('')
    expect(s.results.value).toEqual([])
    expect(s.total.value).toBe(0)
    expect(s.searched.value).toBe(false)
  })
})
