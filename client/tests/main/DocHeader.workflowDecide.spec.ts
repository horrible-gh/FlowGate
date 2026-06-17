import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// Regression (group 0021 / NR0003 item 4): after a manual workflow decision the header
// optimistically flips to wf_in_progress, then refetches detail to backfill head info.
// The refetch used to run NON-silently — nulling doc.value first — so a transient
// detail-GET failure left the doc null and the header looked undecided again (the
// R0001 "decided but screen doesn't refresh" report). It must now refetch silently,
// preserving the optimistic state, and surface a soft warning on failure.

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
let decided = false

function detailResponse() {
  // Mirror the server: once a decision is stored, detail reflects the decided state.
  return {
    data: {
      doc_id: 'test.none.0002.0001-R',
      title: 'test',
      status: 'closed',
      type_code: 'R',
      doc_review_status: decided ? 'wf_in_progress' : 'approved',
      is_editable: true,
      project_id: 'test',
      group_id: 'test.none.0002',
      workflow_steps: decided ? ['N', 'NR'] : null,
    },
  }
}

const TAB = { id: 'test.none.0002.0001-R', title: 'test', path: '', type: 'md', typeCode: 'R' }
const PAYLOAD = { docClass: 'R', sequence: [{ type: 'N' }, { type: 'NR' }] }

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  detailShouldFail = false
  decided = false
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      return detailShouldFail
        ? Promise.reject(new Error('network'))
        : Promise.resolve(detailResponse())
    }
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
  postRequest.mockResolvedValue({ data: { ok: true } })
})

describe('DocHeader manual workflow decision', () => {
  it('keeps the optimistic decided state when the post-decision refetch fails', async () => {
    const wrapper = shallowMount(DocHeader, {
      props: { tab: TAB as any },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('approved')

    // The backfill detail GET will fail this time.
    detailShouldFail = true
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    // Optimistic state survives the failed refetch (not nulled back to undecided).
    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    expect((wrapper.vm as any).workflowSteps).toEqual(['N', 'NR'])
    // Soft warning surfaced to the user.
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.main_panel.workflow_refresh_failed'),
      'warning',
    )
    wrapper.unmount()
  })

  it('in-flight guard: a second confirm during the async window does not re-post decide (NR0003 item 2)', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // Hold the decide POST open so the first call is still in flight when the second fires.
    let releaseDecide: () => void = () => {}
    postRequest.mockImplementation((url: string) => {
      if (url.includes('/workflow/decide')) {
        return new Promise((resolve) => { releaseDecide = () => resolve({ data: { ok: true } }) })
      }
      return Promise.resolve({ data: { ok: true } })
    })

    const first = (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()
    expect((wrapper.vm as any).deciding).toBe(true)

    // Second confirm while the first is still pending — must be ignored.
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    const decideCalls = postRequest.mock.calls.filter((c: any[]) => String(c[0]).includes('/workflow/decide'))
    expect(decideCalls.length).toBe(1)

    releaseDecide()
    await first
    await flushPromises()
    // Guard releases after the decision settles.
    expect((wrapper.vm as any).deciding).toBe(false)
    wrapper.unmount()
  })

  it('emits workflow-decided with the head from the POST 201, before/independent of the backfill (NR0003 §6.1/§6.2)', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // Server 201 carries the confirmed head — the client must USE it, not discard it.
    postRequest.mockImplementation((url: string) => {
      if (url.includes('/workflow/decide')) {
        return Promise.resolve({ data: { status: 'decided', head: { id: 1, type: 'N', label: '조사지시' } } })
      }
      return Promise.resolve({ data: { ok: true } })
    })
    // The backfill detail GET fails — it must NOT affect the transition payload.
    detailShouldFail = true

    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    const events = wrapper.emitted('workflow-decided')
    expect(events).toBeTruthy()
    expect(events!.length).toBe(1)
    expect(events![0][0]).toEqual({
      docId: TAB.id,
      reviewStatus: 'wf_in_progress',
      steps: ['N', 'NR'],
      headType: 'N',
      headLabel: '조사지시',
    })
    wrapper.unmount()
  })

  it('falls back to the dialog sequence head on already_decided (409 body has no head)', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    postRequest.mockImplementation((url: string) => {
      if (url.includes('/workflow/decide')) {
        return Promise.reject({ response: { data: { error: 'already_decided' } } })
      }
      return Promise.resolve({ data: { ok: true } })
    })

    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    const events = wrapper.emitted('workflow-decided')
    expect(events).toBeTruthy()
    // No server head in a 409 → head derived from sequence[0] (= BE get_effective_head).
    expect(events![0][0]).toMatchObject({ reviewStatus: 'wf_in_progress', headType: 'N', headLabel: null })
    wrapper.unmount()
  })

  it('backfills from the server when the refetch succeeds', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // Server now stores the decision; the backfill refetch returns the decided state.
    decided = true
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    // No refresh-failure warning on the happy path.
    expect(showToast).not.toHaveBeenCalledWith(
      i18n.global.t('main.main_panel.workflow_refresh_failed'),
      'warning',
    )
    wrapper.unmount()
  })
})
