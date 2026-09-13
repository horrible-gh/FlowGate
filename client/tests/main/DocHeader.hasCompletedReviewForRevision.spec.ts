import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// flowgate.default.0544 TR0014 rev2 (rejection): the AiInvokeDialog rerun button used to infer
// "a review is already recorded for this revision" from doc_review_status — which stays
// 'pending_review' from first submission until a HUMAN decides, regardless of how many AI
// reviews already ran underneath it. A document sitting in pending_review with an already
// -completed review for its CURRENT revision kept showing the plain [검수 시작] button and
// bounced off the server's review_already_completed 409 exactly like the bug this TR claims to
// have fixed. hasCompletedReviewForRevision instead mirrors admission.py's own check
// (db_document_reviews.get_latest_for_revision(doc_ref, revision_no)): a completed review row
// exists for THIS document's current revision_no — independent of doc_review_status.

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

const DOC = 'test.none.0544.0014-TR'
const TAB = { id: DOC, title: 'test', path: '', type: 'md', typeCode: 'TR' }

function detailResponse(overrides: Record<string, unknown>) {
  return {
    data: {
      doc_id: DOC,
      title: 'test',
      status: 'open',
      type_code: 'TR',
      doc_review_status: 'pending_review',
      revision_no: 1,
      is_editable: true,
      project_id: 'test',
      group_id: 'test.none.0544',
      ai_review: null,
      ai_review_history: [],
      ...overrides,
    },
  }
}

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader hasCompletedReviewForRevision (0544 TR0014 rev2)', () => {
  it('true when a completed review exists for the CURRENT revision, even while doc_review_status stays pending_review', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) {
        return Promise.resolve(detailResponse({
          revision_no: 1,
          ai_review: { id: 933, revision_no: 1, verdict: 'pass', finding_count: 0, findings: [], comment: null },
        }))
      }
      return Promise.resolve({ data: {} })
    })
    const wrapper = mountHeader()
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('pending_review')
    expect((wrapper.vm as any).hasCompletedReviewForRevision).toBe(true)
    wrapper.unmount()
  })

  it('false when the only recorded review belongs to an OLDER revision (a revise happened after it)', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) {
        return Promise.resolve(detailResponse({
          revision_no: 2,
          ai_review: { id: 933, revision_no: 1, verdict: 'issues', finding_count: 1, findings: [], comment: null },
        }))
      }
      return Promise.resolve({ data: {} })
    })
    const wrapper = mountHeader()
    await flushPromises()

    expect((wrapper.vm as any).hasCompletedReviewForRevision).toBe(false)
    wrapper.unmount()
  })

  it('false when no review has run yet for this document', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/documents/detail')) return Promise.resolve(detailResponse({ ai_review: null }))
      return Promise.resolve({ data: {} })
    })
    const wrapper = mountHeader()
    await flushPromises()

    expect((wrapper.vm as any).hasCompletedReviewForRevision).toBe(false)
    wrapper.unmount()
  })
})
