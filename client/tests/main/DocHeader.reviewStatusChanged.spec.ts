import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// Regression (R0001 / NR0003): after a workflow decision the action bar stayed
// stuck on [워크플로 결정] for viewers other than the decider. Those viewers learn
// of the decision only via the SSE `fg:doc_review_status_changed` event, handled
// by `_onReviewStatusChanged`. That handler mutated doc state in place but — unlike
// onWorkflowConfirmed — never emitted `doc-updated`, so MainPanel's headerRevision
// never bumped and its derived workflow view never re-resolved. The fix emits
// `doc-updated` on every matching review-status change so the button flips at once.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function detailResponse() {
  return {
    data: {
      doc_id: 'test.none.0002.0001-R',
      title: 'test',
      status: 'open',
      type_code: 'R',
      doc_review_status: 'draft',
      is_editable: true,
      project_id: 'test',
      group_id: 'test.none.0002',
      workflow_steps: ['M', 'DS', 'D', 'M'],
    },
  }
}

const TAB = { id: 'test.none.0002.0001-R', title: 'test', path: '', type: 'md', typeCode: 'R' }

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
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader fg:doc_review_status_changed', () => {
  it('emits doc-updated so the derived workflow view re-resolves', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    const before = (wrapper.emitted('doc-updated') ?? []).length

    window.dispatchEvent(new CustomEvent('fg:doc_review_status_changed', {
      detail: { doc_id: 'test.none.0002.0001-R', next_status: 'wf_in_progress' },
    }))
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    const after = (wrapper.emitted('doc-updated') ?? []).length
    expect(after).toBeGreaterThan(before)
    expect((wrapper.emitted('doc-updated') as any[]).at(-1)).toEqual([{ docId: 'test.none.0002.0001-R' }])

    wrapper.unmount()
  })

  it('ignores events for a different document', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    const before = (wrapper.emitted('doc-updated') ?? []).length

    window.dispatchEvent(new CustomEvent('fg:doc_review_status_changed', {
      detail: { doc_id: 'some.other.doc-R', next_status: 'wf_in_progress' },
    }))
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('draft')
    expect((wrapper.emitted('doc-updated') ?? []).length).toBe(before)

    wrapper.unmount()
  })

  it('stops listening after unmount', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    wrapper.unmount()
    const before = (wrapper.emitted('doc-updated') ?? []).length

    window.dispatchEvent(new CustomEvent('fg:doc_review_status_changed', {
      detail: { doc_id: 'test.none.0002.0001-R', next_status: 'wf_in_progress' },
    }))
    await flushPromises()

    expect((wrapper.emitted('doc-updated') ?? []).length).toBe(before)
  })
})
