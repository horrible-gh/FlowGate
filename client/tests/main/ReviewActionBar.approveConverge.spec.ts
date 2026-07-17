/**
 * Group 0257 B0001 / NR0003 §3: approving twice must not surface an error.
 *
 * The scenario every test here reproduces is the one NR0003 named as the root cause: the
 * server has already approved the document, but this tab's `reviewStatus` prop is stale and
 * still says `pending_review`, so the bar stays in review mode with the button armed.
 * These fail against the unmodified code (see TR0005 "검증" for the recorded red run).
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const GIT_STATE = {
  data: { ok: true, state: { branch: null, status: 'none', default_action: null, choices: [] } },
}

/** The server's real refusal when a second approve lands on an approved document. */
const ALREADY_APPROVED_ERROR = {
  response: {
    data: {
      detail: "Invalid review transition: doc_review_status='approved' + action='approve'",
    },
  },
}

/** Answers the git-state probe and the document refetch; review status is per-test. */
function mockGets(reviewStatus: string | null) {
  getRequest.mockImplementation(async (url: string) => {
    if (url.includes('/documents/detail')) {
      return { data: { doc_id: 'test.p.0001.0003-D', doc_review_status: reviewStatus } }
    }
    return GIT_STATE
  })
}

/** A tab whose reviewStatus prop is stale: the server approved, the prop never refreshed. */
function mountStaleBar() {
  return mount(ReviewActionBar, {
    props: {
      docId: 'test.p.0001.0003-D',
      projectId: 'test-project',
      groupId: 'test.p.0001',
      docRef: 'test.p.0001.0003-D',
      docType: 'D',
      reviewStatus: 'pending_review',
      mode: 'review' as const,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  mockGets('approved')
  postRequest.mockResolvedValue({ data: { document: { doc_review_status: 'approved' } } })
})

describe('ReviewActionBar approve convergence (0257 NR0003 §3)', () => {
  // ── NR0003 필수 회귀 (e): approve POST는 한 번만 ──────────────────────────────

  it('sends only one approve POST when the bar is clicked again after a success', async () => {
    const wrapper = mountStaleBar()

    // The parent never feeds a fresh status back, so the bar still looks approvable.
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    await (wrapper.vm as any).doApprove()
    await flushPromises()

    const approveCalls = postRequest.mock.calls.filter(([url]) =>
      String(url).includes('review_transitions/approve'),
    )
    expect(approveCalls).toHaveLength(1)
    expect(showToast).not.toHaveBeenCalledWith(expect.stringContaining('failed'), 'danger')
  })

  it('does not reopen the confirm dialog for a document it already approved', async () => {
    const wrapper = mountStaleBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()
    ;(wrapper.vm as any).onApproveClick()

    expect((wrapper.vm as any).showApproveConfirm).toBe(false)
  })

  it('disables the approve button once the document is known to be approved', async () => {
    const wrapper = mountStaleBar()
    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeUndefined()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeDefined()
  })

  it('keeps approve available for a document that is genuinely awaiting review', () => {
    const wrapper = mountStaleBar()
    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeUndefined()
  })

  // ── NR0003 필수 회귀 (f): 이미 approved인 서버 상태로 수렴 ──────────────────────

  it('converges to approved instead of reporting a failure when the server says already approved', async () => {
    postRequest.mockRejectedValueOnce(ALREADY_APPROVED_ERROR)
    mockGets('approved')
    const wrapper = mountStaleBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    expect(showToast).not.toHaveBeenCalled()
  })

  it('converges by re-reading the document, not by matching the error text', async () => {
    // Same state, different server wording. Convergence must not depend on the message.
    postRequest.mockRejectedValueOnce({
      response: { data: { detail: 'review transition rejected (문서가 이미 승인되었습니다)' } },
    })
    mockGets('approved')
    const wrapper = mountStaleBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/documents/detail?doc_id=test.p.0001.0003-D'),
    )
    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    expect(showToast).not.toHaveBeenCalled()
  })

  it('still surfaces a real approve failure that left the document unapproved', async () => {
    postRequest.mockRejectedValueOnce({ response: { data: { detail: 'database is locked' } } })
    mockGets('pending_review')
    const wrapper = mountStaleBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(wrapper.emitted('approve')).toBeFalsy()
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('database is locked'), 'danger')
  })

  it('still surfaces the failure when the document cannot be re-read', async () => {
    postRequest.mockRejectedValueOnce({ response: { data: { detail: 'boom' } } })
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('/documents/detail')) throw new Error('network down')
      return GIT_STATE
    })
    const wrapper = mountStaleBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(wrapper.emitted('approve')).toBeFalsy()
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('boom'), 'danger')
  })
})
