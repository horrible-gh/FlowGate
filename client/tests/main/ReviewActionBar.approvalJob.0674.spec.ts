/**
 * flowgate.default.0674 T0004 §2-3 (C1) / §2-5 — a final approval answered
 * `approval.stage = "queued" | "recovery_required"` is neither approved nor failed: its
 * Git post-step is a job the Runner finishes later. The bar used to read neither stage,
 * so the button came straight back and the approval looked like it had not happened.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'

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

const DOC_ID = 'test.p.0674.0003-AC'
const GROUP_ID = 'test.p.0674'
const JOB_ID = 'opj_0674queued'

let server: { doc: string; git: Record<string, unknown> }

function gitState(over: Record<string, unknown> = {}) {
  return {
    branch: 'test_p_0674', status: 'awaiting_choice', default_action: 'merge',
    choices: ['merge', 'merge_only'], aux_choices: [], action_axes: null,
    approval_in_flight: false, approval_pending: false, approval_job: null, ...over,
  }
}

function liveJob(over: Record<string, unknown> = {}) {
  return {
    job_id: JOB_ID, status: 'freeze_wait', stage: 'freeze_wait', doc_id: DOC_ID, outcome: null,
    blocker: { domain: 'G', holder: 'job:selfcheck', holder_kind: 'selfcheck', since: 'now' },
    ...over,
  }
}

function queuedResponse(stage: 'queued' | 'recovery_required', job = liveJob()) {
  return {
    data: {
      ok: true,
      response_status: 'queued',
      git: { ok: true, terminal: false, queued: true },
      approval: {
        approved: false, document_status: 'pending_review', root_status: 'wf_in_progress',
        deferred: true, stage, job_id: JOB_ID, blocker: job.blocker,
      },
      job,
    },
  }
}

function approvePosts(): unknown[][] {
  return postRequest.mock.calls.filter(([url]) => String(url).includes('review_transitions/approve'))
}

function mountAc() {
  return mount(ReviewActionBar, {
    props: {
      docId: DOC_ID, projectId: 'test-project', groupId: GROUP_ID, docRef: DOC_ID,
      docType: 'AC', reviewStatus: 'pending_review', mode: 'review' as const,
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
  server = { doc: 'pending_review', git: gitState() }
  getRequest.mockImplementation(async (url: string) => {
    if (url.includes('/documents/detail')) return { data: { doc_id: DOC_ID, doc_review_status: server.doc } }
    if (url.includes('/git/finalize')) return { data: { ok: true, state: { ...server.git } } }
    return { data: {} }
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ReviewActionBar — queued / recovery_required final approval (0674 C1)', () => {
  it('queued: says the Git post-step is waiting, names the job and blocker, and locks the button', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockImplementationOnce(async () => {
      server.git = gitState({ approval_in_flight: true, approval_job: liveJob() })
      return queuedResponse('queued')
    })
    await (wrapper.vm as any).doApprove()
    await flushPromises()

    const notice = wrapper.find('[data-testid="ab-approval-job"]')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toContain(JOB_ID)
    expect(notice.text()).toContain(i18n.global.t('main.review_action_bar.approval_job_stage.freeze_wait'))
    expect(notice.text()).toContain('G')
    expect(notice.text()).toContain('selfcheck')
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining(JOB_ID), 'info')
    expect(wrapper.emitted('approve')).toBeUndefined()

    // not re-runnable while the job is not terminal
    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeDefined()
    await (wrapper.vm as any).doApprove()
    expect(approvePosts()).toHaveLength(1)
    wrapper.unmount()
  })

  it('recovery_required: warns that recovery is needed and keeps the button locked', async () => {
    const wrapper = mountAc()
    await flushPromises()
    const job = liveJob({ status: 'recovery_required', stage: 'recovery_required', blocker: null })
    postRequest.mockImplementationOnce(async () => {
      server.git = gitState({ approval_in_flight: true, approval_job: job })
      return queuedResponse('recovery_required', job)
    })
    await (wrapper.vm as any).doApprove()
    await flushPromises()

    const text = i18n.global.t('main.review_action_bar.approval_job_recovery', { job: JOB_ID })
    expect(wrapper.find('[data-testid="ab-approval-job"]').text()).toContain(text)
    expect(wrapper.find('[data-testid="ab-approval-job"]').classes()).toContain('ab-approval-job--recovery')
    expect(showToast).toHaveBeenCalledWith(text, 'warning')
    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('follows the job: a blocker change is re-read, and terminal + approved unlocks into the approved state', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockImplementationOnce(async () => {
      server.git = gitState({ approval_in_flight: true, approval_job: liveJob() })
      return queuedResponse('queued')
    })
    await (wrapper.vm as any).doApprove()
    await flushPromises()

    // the Runner moved it: blocked on R (the SSE re-broadcast window event)
    server.git = gitState({
      approval_in_flight: true,
      approval_job: liveJob({ status: 'blocked', stage: 'blocked', blocker: { domain: 'R', holder_kind: 'publish' } }),
    })
    window.dispatchEvent(new CustomEvent('fg:git_approval_job_changed', { detail: { group_id: GROUP_ID } }))
    await flushPromises()
    const notice = wrapper.find('[data-testid="ab-approval-job"]').text()
    expect(notice).toContain(i18n.global.t('main.review_action_bar.approval_job_stage.blocked'))
    expect(notice).toContain('R')

    // terminal: git_finalize_done, the job is gone and the AC is approved
    server.git = gitState({ approval_in_flight: false, status: 'merged', approval_job: null })
    server.doc = 'approved'
    window.dispatchEvent(new CustomEvent('fg:git_finalize_done', { detail: { group_id: GROUP_ID } }))
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('[data-testid="ab-approval-job"]').exists()).toBe(false)
    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    expect(showToast).toHaveBeenCalledWith(i18n.global.t('main.review_action_bar.approval_job_done'), 'success')
    wrapper.unmount()
  })

  it('a job that ends without approving (failed) unlocks the button again', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockImplementationOnce(async () => {
      server.git = gitState({ approval_in_flight: true, approval_job: liveJob() })
      return queuedResponse('queued')
    })
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeDefined()

    server.git = gitState({ approval_in_flight: false, approval_job: null })
    window.dispatchEvent(new CustomEvent('fg:git_finalize_done', { detail: { group_id: GROUP_ID } }))
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('[data-testid="ab-approval-job"]').exists()).toBe(false)
    expect(wrapper.emitted('approve')).toBeUndefined()
    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('polls slowly while no event arrives', async () => {
    const wrapper = mountAc()
    await flushPromises()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    postRequest.mockImplementationOnce(async () => {
      server.git = gitState({ approval_in_flight: true, approval_job: liveJob() })
      return queuedResponse('queued')
    })
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    const reads = () => getRequest.mock.calls.filter(([url]) => String(url).includes('/git/finalize')).length
    const before = reads()

    server.git = gitState({ approval_in_flight: false, approval_job: null })
    await vi.advanceTimersByTimeAsync(5_000)
    await flushPromises()
    expect(reads()).toBeGreaterThan(before)
    expect(wrapper.find('[data-testid="ab-approval-job"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('a job already waiting when the document is opened locks the button from the start', async () => {
    server.git = gitState({ approval_in_flight: true, approval_job: liveJob() })
    const wrapper = mountAc()
    await flushPromises()
    expect(wrapper.find('[data-testid="ab-approval-job"]').text()).toContain(JOB_ID)
    expect(wrapper.find('button.btn-success').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it("another document's job in the same group does not lock this bar", async () => {
    server.git = gitState({ approval_in_flight: true, approval_job: liveJob({ doc_id: 'test.p.0674.0009-AC' }) })
    const wrapper = mountAc()
    await flushPromises()
    expect(wrapper.find('[data-testid="ab-approval-job"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('an executed approval keeps the old approved path', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockResolvedValueOnce({
      data: {
        ok: true, git: { ok: true, terminal: true, result: { status: 'pushed' } },
        approval: { approved: true, stage: 'complete' }, document: { doc_review_status: 'approved' },
        job: { job_id: JOB_ID, status: 'succeeded', stage: 'terminal', outcome: 'executed' },
      },
    })
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    expect(wrapper.find('[data-testid="ab-approval-job"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('useFlowGateSse — git_approval_job_changed (0674 §2-5)', () => {
  it('re-broadcasts the job event and a git status refresh for that group', async () => {
    type Listener = (event: Event) => void
    class FakeEventSource {
      static latest: FakeEventSource | null = null
      listeners = new Map<string, Listener[]>()
      onopen: Listener | null = null
      onerror: Listener | null = null
      constructor(_url: string) { FakeEventSource.latest = this }
      addEventListener(name: string, listener: Listener) {
        this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener])
      }
      emit(name: string, data: unknown) {
        const event = new MessageEvent(name, { data: JSON.stringify(data) })
        for (const listener of this.listeners.get(name) ?? []) listener(event)
      }
      close() {}
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    ;(window as any).__accessToken__ = 'tok1'
    const jobEvents = vi.fn()
    const refreshes = vi.fn()
    window.addEventListener('fg:git_approval_job_changed', jobEvents)
    window.addEventListener('fg:git_status_refresh', refreshes)
    const Harness = defineComponent({
      setup() {
        useFlowGateSse(vi.fn())
        return () => h('div')
      },
    })
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    await flushPromises()

    FakeEventSource.latest?.emit('git_approval_job_changed', {
      project: 'test-project',
      payload: { group_id: GROUP_ID, doc_id: DOC_ID, status: 'blocked', stage: 'blocked', job: { job_id: JOB_ID } },
    })

    expect(jobEvents).toHaveBeenCalledTimes(1)
    const detail = (jobEvents.mock.calls[0][0] as CustomEvent).detail
    expect(detail).toMatchObject({ project: 'test-project', group_id: GROUP_ID, status: 'blocked' })
    expect(detail.job.job_id).toBe(JOB_ID)
    expect(refreshes).toHaveBeenCalledTimes(1)
    expect((refreshes.mock.calls[0][0] as CustomEvent).detail).toEqual({
      project: 'test-project', group_id: GROUP_ID, status: null,
    })
    window.removeEventListener('fg:git_approval_job_changed', jobEvents)
    window.removeEventListener('fg:git_status_refresh', refreshes)
    wrapper.unmount()
    vi.unstubAllGlobals()
    delete (window as any).__accessToken__
  })
})
