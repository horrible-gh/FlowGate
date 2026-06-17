import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// Regression: an open R tab derives its action-bar head state from a one-shot fetch
// on mount. When a sibling next-step doc (e.g. DS) is created out-of-band — by an AI
// worker via the inbox API — the SSE layer dispatches `fg:open_docs_refresh`, and the
// tab must silently refetch so workflow_head_doc_id becomes the new DS doc. Without
// this, the action bar stays stale and offers "proceed to next step" (open the create
// dialog) instead of navigating to the doc that already exists.

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

// Mutable head state the mocked /detail endpoint returns: null = pre-DS (stale),
// then the DS doc id once the sibling has been created.
let headDocId: string | null = null
let isEditable = true

function detailResponse() {
  return {
    data: {
      doc_id: 'test.none.0002.0001-R',
      title: 'test',
      status: 'closed',
      type_code: 'R',
      doc_review_status: 'wf_in_progress',
      is_editable: isEditable,
      project_id: 'test',
      group_id: 'test.none.0002',
      workflow_steps: ['M', 'DS', 'D', 'M'],
      workflow_head_type: 'DS',
      workflow_head_status: headDocId ? 'in_progress' : 'pending',
      workflow_head_doc_id: headDocId,
      next_step_exists: true,
    },
  }
}

function detailCallCount(): number {
  return getRequest.mock.calls.filter(
    (c: any[]) => typeof c[0] === 'string' && c[0].includes('/documents/detail'),
  ).length
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
  headDocId = null
  isEditable = true
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader fg:open_docs_refresh', () => {
  it('uses server editability for a closed document before and after final approval', async () => {
    const editableWrapper = mountHeader()
    await flushPromises()
    expect((editableWrapper.vm as any).canEditDocument).toBe(true)
    editableWrapper.unmount()

    isEditable = false
    const lockedWrapper = mountHeader()
    await flushPromises()
    expect((lockedWrapper.vm as any).canEditDocument).toBe(false)
    lockedWrapper.unmount()
  })

  it('refetches head state when a sibling doc is created out-of-band', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // Stale pre-DS state: head step is DS but no doc realised yet.
    expect((wrapper.vm as any).headDocId).toBe(null)
    expect(detailCallCount()).toBe(1)

    // Sibling DS doc now exists (created via the inbox API); SSE fires.
    headDocId = 'test.none.0002.0003-DS'
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test' } }))
    await flushPromises()

    expect(detailCallCount()).toBe(2)
    expect((wrapper.vm as any).headDocId).toBe('test.none.0002.0003-DS')

    wrapper.unmount()
  })

  it('ignores refresh events scoped to a different project', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)

    headDocId = 'test.none.0002.0003-DS'
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'other-project' } }))
    await flushPromises()

    // No refetch: event was for a different project.
    expect(detailCallCount()).toBe(1)
    expect((wrapper.vm as any).headDocId).toBe(null)

    wrapper.unmount()
  })

  it('stops listening after unmount', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)
    wrapper.unmount()

    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test' } }))
    await flushPromises()
    expect(detailCallCount()).toBe(1)
  })
})
