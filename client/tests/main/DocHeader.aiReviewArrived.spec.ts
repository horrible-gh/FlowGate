import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// 0543 T0004 — AI review arrival must reconcile the CURRENTLY OPEN document's
// review/history immediately (R1/R8), must not force a same-project sibling
// document's tab to refetch (R2/R3), must survive out-of-order responses and tab
// switches (R5/R6), and must never clobber the user's in-progress local edits
// (R7). This exercises the doc-scoped `fg:open_docs_refresh` (T0004 §3/§5) end to
// end through DocHeader's existing generation-guarded silent refetch.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const DOC_A = 'test.none.0543.0001-R'
const DOC_B = 'test.none.0543.0002-DS'

let aiReviewForA: any = null
let aiReviewHistoryForA: any[] = []

function detailResponse(docId: string) {
  return {
    data: {
      doc_id: docId,
      title: 'test',
      status: 'open',
      type_code: 'R',
      doc_review_status: 'pending_review',
      is_editable: true,
      project_id: 'test',
      group_id: 'test.none.0543',
      ai_review: docId === DOC_A ? aiReviewForA : null,
      ai_review_history: docId === DOC_A ? aiReviewHistoryForA : [],
    },
  }
}

function detailCallCount(): number {
  return getRequest.mock.calls.filter(
    (c: any[]) => typeof c[0] === 'string' && c[0].includes('/documents/detail'),
  ).length
}

const TAB = { id: DOC_A, title: 'test', path: '', type: 'md', typeCode: 'R' }

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  aiReviewForA = null
  aiReviewHistoryForA = []
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      const id = new URL(url, 'http://x').searchParams.get('doc_id') ?? DOC_A
      return Promise.resolve(detailResponse(id))
    }
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader ai_review_arrived reconcile (0543 T0004)', () => {
  it('R1: refetches and shows the new verdict when the arrival names the open document — no F5', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).aiReview).toBeNull()

    aiReviewForA = { id: 1, verdict: 'pass', finding_count: 0, findings: [], comment: null }
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test', doc_id: DOC_A } }))
    await flushPromises()

    expect(detailCallCount()).toBe(2)
    expect((wrapper.vm as any).aiReview?.verdict).toBe('pass')
    wrapper.unmount()
  })

  it('R2/R3: does not refetch when the arrival names a different document in the same project', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)

    aiReviewForA = { id: 1, verdict: 'pass', finding_count: 0, findings: [], comment: null }
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test', doc_id: DOC_B } }))
    await flushPromises()

    // No fetch triggered for A, and its (unrelated) screen state is untouched.
    expect(detailCallCount()).toBe(1)
    expect((wrapper.vm as any).aiReview).toBeNull()
    wrapper.unmount()
  })

  it('a null-scoped refresh (no specific doc named) still refetches every open tab as before', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)

    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test', doc_id: null } }))
    await flushPromises()

    expect(detailCallCount()).toBe(2)
    wrapper.unmount()
  })

  it('R5: a stale first response arriving after a newer one does not overwrite the latest review', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    const first = deferred<any>()
    const second = deferred<any>()
    let detailCalls = 0
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) {
        detailCalls += 1
        return detailCalls === 1 ? first.promise : second.promise
      }
      return Promise.resolve({ data: {} })
    })

    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test', doc_id: DOC_A } }))
    await flushPromises()
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test', doc_id: DOC_A } }))
    await flushPromises()

    // The newer request (review id 2) resolves first; the older one (review id 1)
    // arrives late and must be discarded by the generation guard.
    second.resolve({ data: { ...detailResponse(DOC_A).data, ai_review: { id: 2, verdict: 'issues', finding_count: 1, findings: [], comment: null } } })
    await flushPromises()
    first.resolve({ data: { ...detailResponse(DOC_A).data, ai_review: { id: 1, verdict: 'pass', finding_count: 0, findings: [], comment: null } } })
    await flushPromises()

    expect((wrapper.vm as any).aiReview?.id).toBe(2)
    wrapper.unmount()
  })

  it('R6: a response for a document that was open before a tab switch does not apply to the new tab', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    const pending = deferred<any>()
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) return pending.promise
      return Promise.resolve({ data: {} })
    })
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test', doc_id: DOC_A } }))
    await flushPromises()

    // Tab switches to a different document while the refetch is still in flight.
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) return Promise.resolve(detailResponse(DOC_B))
      return Promise.resolve({ data: {} })
    })
    await wrapper.setProps({ tab: { id: DOC_B, title: 'other', path: '', type: 'md', typeCode: 'DS' } })
    await flushPromises()

    // The stale A response now resolves; it must not stomp the now-open B doc.
    pending.resolve({ data: { ...detailResponse(DOC_A).data, ai_review: { id: 99, verdict: 'pass', finding_count: 0, findings: [], comment: null } } })
    await flushPromises()

    // ai_review from the stale A response must not appear on the now-open B doc.
    expect((wrapper.vm as any).aiReview?.id).not.toBe(99)
    wrapper.unmount()
  })

  it('R7: an in-progress title edit survives a silent review/history refresh', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    await wrapper.find('.doc-title-pencil').trigger('click')
    await flushPromises()
    const input = wrapper.find('.doc-title-input')
    expect(input.exists()).toBe(true)
    await input.setValue('작성 중인 제목')

    aiReviewForA = { id: 1, verdict: 'pass', finding_count: 0, findings: [], comment: null }
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test', doc_id: DOC_A } }))
    await flushPromises()

    // The silent refetch replaced ai_review, but the unsaved title draft is untouched.
    expect((wrapper.vm as any).aiReview?.verdict).toBe('pass')
    expect(wrapper.find('.doc-title-input').element as HTMLInputElement).toMatchObject({ value: '작성 중인 제목' })
    wrapper.unmount()
  })

  it('R8: the displayed review comes from the authoritative GET, not from the event payload', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    aiReviewForA = { id: 7, verdict: 'pass', finding_count: 0, findings: [], comment: 'server truth' }
    // The event payload carries a DIFFERENT (bogus) verdict/comment; the client must
    // not assemble the display from it.
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', {
      detail: { project: 'test', doc_id: DOC_A, verdict: 'issues', comment: 'from SSE payload, should be ignored' },
    }))
    await flushPromises()

    expect((wrapper.vm as any).aiReview?.id).toBe(7)
    expect((wrapper.vm as any).aiReview?.comment).toBe('server truth')
    wrapper.unmount()
  })
})
