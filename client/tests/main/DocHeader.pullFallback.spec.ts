import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// Gap C — active-document pull fallback (group 0040 / R0001 / NR0003 §4, §6 item 1).
//
// The 워크플로 결정 regression recurs because keeping an open R tab's head/decision
// state fresh AFTER the mount fetch was push-only: an SSE event, or the one-shot
// backfill right after a local decision. In the "일정 시간이 지난 후" window both can
// silently fail — a zombie SSE stream drops the decision event, or the backfill 401s on
// a stale session — and with no pull path the tab froze (toast shown, button/strip never
// updated). DocHeader now also PULLS the active document on foreground-regain (window
// focus / tab visible / network back), with one retry to ride out a transient 401.

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

// Mutable head state the mocked /detail endpoint returns: null = pre-decision (stale),
// then the head doc id once the decision has materialised server-side.
let headDocId: string | null = null
// When >0, the next N detail GETs reject (simulate a 401/transient failure window).
let failNextDetail = 0

function detailResponse() {
  return {
    data: {
      doc_id: 'test.none.0040.0001-R',
      title: 'test',
      status: 'closed',
      type_code: 'R',
      doc_review_status: 'wf_in_progress',
      is_editable: true,
      project_id: 'test',
      group_id: 'test.none.0040',
      workflow_steps: ['N', 'NR', 'T', 'TR'],
      workflow_head_type: 'NR',
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

const TAB = { id: 'test.none.0040.0001-R', title: 'test', path: '', type: 'md', typeCode: 'R' }

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  headDocId = null
  failNextDetail = 0
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      if (failNextDetail > 0) {
        failNextDetail -= 1
        return Promise.reject(new Error('401'))
      }
      return Promise.resolve(detailResponse())
    }
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader Gap C — pull fallback on foreground-regain', () => {
  it('pulls the active document on window focus (SSE missed the decision)', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)
    expect((wrapper.vm as any).headDocId).toBe(null)

    // Decision materialised server-side while the SSE stream was a zombie — no event
    // reached this tab. Regaining focus must pull the fresh state.
    headDocId = 'test.none.0040.0002-NR'
    window.dispatchEvent(new Event('focus'))
    await flushPromises()

    expect(detailCallCount()).toBe(2)
    expect((wrapper.vm as any).headDocId).toBe('test.none.0040.0002-NR')
    wrapper.unmount()
  })

  it('pulls on visibilitychange when the tab becomes visible', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)

    headDocId = 'test.none.0040.0002-NR'
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()

    expect(detailCallCount()).toBe(2)
    expect((wrapper.vm as any).headDocId).toBe('test.none.0040.0002-NR')
    wrapper.unmount()
  })

  it('retries once when the first pull fails (transient 401 in the rotation window)', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)

    // First pull attempt 401s (stale session), the Axios layer refreshes, the retry lands.
    headDocId = 'test.none.0040.0002-NR'
    failNextDetail = 1
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    // The retry waits a short real-timer beat (PULL_RETRY_DELAY_MS) to let the in-flight
    // token rotation settle before the second attempt — wait past it, then flush.
    await new Promise((resolve) => setTimeout(resolve, 600))
    await flushPromises()
    // attempt 1 (reject) + attempt 2 (success) on top of the mount fetch.
    expect(detailCallCount()).toBe(3)
    expect((wrapper.vm as any).headDocId).toBe('test.none.0040.0002-NR')
    wrapper.unmount()
  })

  it('cooldown collapses the focus+online wake burst into a single pull', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)

    headDocId = 'test.none.0040.0002-NR'
    window.dispatchEvent(new Event('focus'))
    window.dispatchEvent(new Event('online'))
    await flushPromises()

    // Both fired within the cooldown/in-flight window → only one extra fetch.
    expect(detailCallCount()).toBe(2)
    wrapper.unmount()
  })

  it('stops pulling after unmount', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(detailCallCount()).toBe(1)
    wrapper.unmount()

    window.dispatchEvent(new Event('focus'))
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(detailCallCount()).toBe(1)
  })
})
