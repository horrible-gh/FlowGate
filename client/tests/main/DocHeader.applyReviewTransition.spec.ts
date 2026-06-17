import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// Regression (group 0040 / NR0003 §2 / §6 item 2 — "gap D"): approve / reject /
// mark-revised used to refresh the header through the NON-silent fetchDoc, which blanks
// doc.value BEFORE the GET. In the "상시 열린 유휴" window that single GET can be slow or
// 401, leaving the header stuck blank with only the synchronous success toast showing
// (the 6/13 recurrence). Unlike the workflow-decision path, these actions had no
// optimistic flip and no retry. applyReviewTransition() now gives them the same treatment
// onWorkflowConfirmed already has: optimistic flip to the server-confirmed status + a
// SILENT retrying backfill, so a transient GET failure can never blank an already-
// transitioned header.

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

let detailShouldFail = false
let failuresBeforeSuccess = 0
let serverReviewStatus = 'in_review'

function detailResponse() {
  return {
    data: {
      doc_id: 'test.none.0040.0005-TR',
      title: 'test',
      status: 'in_review',
      type_code: 'TR',
      doc_review_status: serverReviewStatus,
      is_editable: true,
      project_id: 'test',
      group_id: 'test.none.0040',
      workflow_steps: null,
    },
  }
}

const TAB = { id: 'test.none.0040.0005-TR', title: 'test', path: '', type: 'md', typeCode: 'TR' }

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  detailShouldFail = false
  failuresBeforeSuccess = 0
  serverReviewStatus = 'in_review'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      if (detailShouldFail) return Promise.reject(new Error('network'))
      if (failuresBeforeSuccess > 0) {
        failuresBeforeSuccess -= 1
        return Promise.reject(new Error('401'))
      }
      return Promise.resolve(detailResponse())
    }
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
  postRequest.mockResolvedValue({ data: { ok: true } })
})

describe('DocHeader applyReviewTransition (gap D)', () => {
  it('optimistically flips to the server-confirmed status and bumps the header (emit doc-updated)', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('in_review')
    const emitsBefore = (wrapper.emitted('doc-updated') ?? []).length

    serverReviewStatus = 'approved'
    await (wrapper.vm as any).applyReviewTransition('approved')
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('approved')
    // The optimistic flip must emit so MainPanel re-resolves the derived action bar/strip.
    expect((wrapper.emitted('doc-updated') ?? []).length).toBeGreaterThan(emitsBefore)
    wrapper.unmount()
  })

  it('keeps the transitioned status when the backfill GET fails (no blank-to-undecided)', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // The backfill (and its one retry) will fail this time.
    detailShouldFail = true
    await (wrapper.vm as any).applyReviewTransition('rejected')
    await flushPromises()

    // The old non-silent fetchDoc would have nulled doc.value → status null. The optimistic
    // flip must survive a failed silent backfill.
    expect((wrapper.vm as any).docReviewStatus).toBe('rejected')
    expect((wrapper.vm as any).docLoaded).toBe(true)
    wrapper.unmount()
  })

  it('retries the silent backfill once when the first GET 401s, then backfills from the server', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // First post-action GET 401s (token rotation window); the retry succeeds.
    failuresBeforeSuccess = 1
    serverReviewStatus = 'approved'
    await (wrapper.vm as any).applyReviewTransition('approved')
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('approved')
    wrapper.unmount()
  })

  it('with no status (dialog close) does a silent backfill and never blanks on failure', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('in_review')

    detailShouldFail = true
    await (wrapper.vm as any).applyReviewTransition()
    await flushPromises()

    // No optimistic change requested, and the failed silent backfill must not blank.
    expect((wrapper.vm as any).docReviewStatus).toBe('in_review')
    expect((wrapper.vm as any).docLoaded).toBe(true)
    wrapper.unmount()
  })

  it('lets the server status win after a successful backfill', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // Optimistic guess differs from what the server ultimately reports; the backfill wins.
    serverReviewStatus = 'wf_in_progress'
    await (wrapper.vm as any).applyReviewTransition('approved')
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    wrapper.unmount()
  })
})
