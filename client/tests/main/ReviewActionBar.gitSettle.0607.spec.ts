/**
 * flowgate.default.0607 T0004 §3.6 / §5 Case I·J — after a Git-carrying approve loses its
 * response, the bar asks the SERVER before it lets anyone approve again.
 *
 * NR0003 §3/§6: the approve request timed out in the browser at 30s while the server was
 * still inside `git merge`; the catch re-read the document once, found `pending_review`,
 * showed "승인 실패" and re-armed the button. The second click reached a server that had
 * already merged and discarded the slot without pushing. These cases drive the real
 * component: the approve POST is sent once, then only reads follow until the server's own
 * `approval_in_flight` flag drops.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

const DOC_ID = 'test.p.0001.0003-AC'
const GROUP_ID = 'test.p.0001'
const POLL_MS = 3_000

/** What the server says right now. Tests move it as the "backend" progresses. */
let server: {
  doc: string
  git: Record<string, unknown>
}

function gitState(over: Record<string, unknown> = {}) {
  return {
    branch: 'test_p_0001', status: 'awaiting_choice', default_action: 'merge',
    choices: ['merge', 'merge_only'], aux_choices: [], action_axes: null,
    approval_in_flight: false, approval_pending: false, ...over,
  }
}

/** The axios shape of a request the browser gave up on — no response at all. */
const AXIOS_TIMEOUT = Object.assign(new Error('timeout of 330000ms exceeded'), {
  code: 'ECONNABORTED',
  isAxiosError: true,
})

function finalizeGets(): number {
  return getRequest.mock.calls.filter(([url]) => String(url).includes('/git/finalize')).length
}

function approvePosts(): unknown[][] {
  return postRequest.mock.calls.filter(([url]) => String(url).includes('review_transitions/approve'))
}

function mountAc() {
  return mount(ReviewActionBar, {
    props: {
      docId: DOC_ID,
      projectId: 'test-project',
      groupId: GROUP_ID,
      docRef: DOC_ID,
      docType: 'AC',
      reviewStatus: 'pending_review',
      mode: 'review' as const,
    },
    global: { plugins: [i18n] },
  })
}

function approveButton(wrapper: ReturnType<typeof mountAc>) {
  return wrapper.find('button.btn-success')
}

/** Mount, let the choice block load, then send an approve that times out while the
 *  server has just started its Git (the incident's request A). */
async function approveThatTimesOut(inFlightAfterTimeout = true) {
  const wrapper = mountAc()
  await flushPromises()
  postRequest.mockImplementationOnce(async () => {
    server.git = gitState({ approval_in_flight: inFlightAfterTimeout, status: 'merging' })
    throw AXIOS_TIMEOUT
  })
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const done = (wrapper.vm as any).doApprove() as Promise<void>
  await flushPromises()
  return { wrapper, done }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  server = { doc: 'pending_review', git: gitState() }
  getRequest.mockImplementation(async (url: string) => {
    if (url.includes('/documents/detail')) {
      return { data: { doc_id: DOC_ID, doc_review_status: server.doc } }
    }
    if (url.includes('/git/finalize')) return { data: { ok: true, state: { ...server.git } } }
    return { data: {} }
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ReviewActionBar after a lost Git approve response (0607 T0004 §3.6)', () => {
  it('sends the Git choice with the approve (precondition of every case below)', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockResolvedValueOnce({
      data: { ok: true, document: { doc_review_status: 'approved' }, approval: { approved: true } },
    })
    await (wrapper.vm as any).doApprove()
    expect(approvePosts()).toHaveLength(1)
    expect((approvePosts()[0][1] as any).git_action).toBe('merge')
  })

  it('Case I — while the server still runs Git, the button stays locked and nothing is re-sent', async () => {
    const { wrapper, done } = await approveThatTimesOut()

    expect(approvePosts()).toHaveLength(1)
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.review_action_bar.git_settle_in_progress'), 'info',
    )
    // no failure verdict while the server is still working
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'danger')
    expect(approveButton(wrapper).attributes('disabled')).toBeDefined()
    expect(approveButton(wrapper).attributes('title')).toBe(
      i18n.global.t('main.review_action_bar.git_settle_in_progress'),
    )

    // A second click finds the bar locked: no confirm, no second approve.
    ;(wrapper.vm as any).onApproveClick()
    expect((wrapper.vm as any).showApproveConfirm).toBe(false)
    await (wrapper.vm as any).doApprove()
    expect(approvePosts()).toHaveLength(1)

    // It keeps asking the server — the poll is alive, not a one-shot re-read.
    const before = finalizeGets()
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    expect(finalizeGets()).toBe(before + 1)
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    expect(finalizeGets()).toBe(before + 2)
    expect(approveButton(wrapper).attributes('disabled')).toBeDefined()
    expect(approvePosts()).toHaveLength(1)

    // Let it finish so the loop does not outlive the test.
    server.doc = 'approved'
    server.git = gitState({ status: 'merged', choices: [] })
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done
    wrapper.unmount()
  })

  it('rev1 — keeps the button locked and never re-POSTs while finalize GET intermittently fails', async () => {
    const { wrapper, done } = await approveThatTimesOut()
    expect(approvePosts()).toHaveLength(1)

    // The server has NOT actually finished yet (doc is still pending_review) while
    // the client's own finalize GET itself fails a couple of times (network blip).
    // A failed GET must never be read as "approval_in_flight: false" — if it were,
    // the bar would read the still-pending document right now and wrongly report
    // a failure instead of waiting for the real answer.
    let flakyGets = 2
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('/git/finalize')) {
        if (flakyGets > 0) { flakyGets -= 1; throw new Error('network blip') }
        return { data: { ok: true, state: { ...server.git } } }
      }
      if (url.includes('/documents/detail')) {
        return { data: { doc_id: DOC_ID, doc_review_status: server.doc } }
      }
      return { data: {} }
    })

    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    expect(approveButton(wrapper).attributes('disabled')).toBeDefined()
    expect(approvePosts()).toHaveLength(1)
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'danger')
    expect(wrapper.emitted('approve')).toBeFalsy()

    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    expect(approveButton(wrapper).attributes('disabled')).toBeDefined()
    expect(approvePosts()).toHaveLength(1)
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'danger')
    expect(wrapper.emitted('approve')).toBeFalsy()

    // The server actually finishes now; the next poll finally gets a real,
    // explicit `false` and converges normally.
    server.doc = 'approved'
    server.git = gitState({ status: 'merged', choices: [], approval_in_flight: false })
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done

    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    expect(approvePosts()).toHaveLength(1)
    wrapper.unmount()
  })

  it('rev1 — keeps the button locked when the finalize response omits approval_in_flight entirely', async () => {
    const { wrapper, done } = await approveThatTimesOut()
    expect(approvePosts()).toHaveLength(1)

    // A successful response whose approval_in_flight is missing/null (stale
    // gateway cache, older server) must not be read as "no approval running".
    const { approval_in_flight: _omit, ...rest } = server.git as Record<string, unknown>
    server.git = { ...rest, approval_in_flight: null }

    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    expect(approveButton(wrapper).attributes('disabled')).toBeDefined()
    expect(approvePosts()).toHaveLength(1)
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'danger')

    // Only once the server explicitly confirms `false` does it converge.
    server.doc = 'approved'
    server.git = { ...rest, approval_in_flight: false, status: 'merged', choices: [] }
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done

    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    expect(approvePosts()).toHaveLength(1)
    wrapper.unmount()
  })

  it('Case J — when the server finishes the merge, the bar converges to approved without a second approve', async () => {
    const { wrapper, done } = await approveThatTimesOut()
    expect(wrapper.emitted('approve')).toBeFalsy()

    // The backend completes: approval committed, then the approval lock released.
    server.doc = 'approved'
    server.git = gitState({ status: 'merged', choices: [], approval_in_flight: false })
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done

    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    expect(approvePosts()).toHaveLength(1)
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'danger')
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'warning')
    expect(approveButton(wrapper).attributes('disabled')).toBeDefined() // approved: stays done
  })

  it('reports a failure only after the server has stopped, then allows a retry', async () => {
    const { wrapper, done } = await approveThatTimesOut()

    server.git = gitState({ status: 'waiting', approval_in_flight: false })
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done

    expect(wrapper.emitted('approve')).toBeFalsy()
    expect(showToast).toHaveBeenCalledWith(expect.any(String), 'danger')
    expect(approveButton(wrapper).attributes('disabled')).toBeUndefined()
    expect(approvePosts()).toHaveLength(1)
  })

  it('settles at once, without the in-progress notice, when the server had already stopped', async () => {
    const { wrapper, done } = await approveThatTimesOut(false)
    await done

    expect(showToast).not.toHaveBeenCalledWith(
      i18n.global.t('main.review_action_bar.git_settle_in_progress'), 'info',
    )
    expect(showToast).toHaveBeenCalledWith(expect.any(String), 'danger')
    expect(approveButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('a conflict is reported as deferred to the conflict screen, not as a failure', async () => {
    const { wrapper, done } = await approveThatTimesOut()
    server.git = gitState({ status: 'conflict', choices: [], approval_in_flight: false })
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.review_action_bar.git_settle_deferred'), 'warning',
    )
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'danger')
    expect(wrapper.emitted('approve')).toBeFalsy()
  })

  it('terminal Git with an unrecorded approval is offered as an approval-only retry', async () => {
    const { wrapper, done } = await approveThatTimesOut()
    server.git = gitState({
      status: 'merged', choices: [], approval_in_flight: false, approval_pending: true,
    })
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.review_action_bar.git_settle_retry'), 'warning',
    )
    expect(wrapper.emitted('approve')).toBeFalsy()
    expect(approveButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('stops asking about the old document once the bar moves to another one', async () => {
    const { wrapper, done } = await approveThatTimesOut()
    await wrapper.setProps({ docId: 'test.p.0001.0004-AC', docRef: 'test.p.0001.0004-AC' })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    await done
    const settled = finalizeGets()

    await vi.advanceTimersByTimeAsync(POLL_MS * 3)
    expect(finalizeGets()).toBe(settled)
    expect(showToast).not.toHaveBeenCalledWith(expect.anything(), 'danger')
    expect(approvePosts()).toHaveLength(1)
  })

  it('a plain approve (no Git choice) keeps the one-shot document re-read', async () => {
    server.git = gitState({ status: 'none', choices: [] })
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockRejectedValueOnce(AXIOS_TIMEOUT)
    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect((approvePosts()[0][1] as any).git_action).toBeUndefined()
    expect(showToast).not.toHaveBeenCalledWith(
      i18n.global.t('main.review_action_bar.git_settle_in_progress'), 'info',
    )
    expect(showToast).toHaveBeenCalledWith(expect.any(String), 'danger')
  })
})
