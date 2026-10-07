/**
 * flowgate.default.0683 T0004 §3 — an approval refused because another attempt holds the
 * merge target says WHICH attempt, as what it really is, and leads to it.
 *
 * The incident (flowgate.v02.0033.0007-AC): the server knew the blocker was branch merge
 * #206 (main → v0.2, resolved_pending_review) and sent it in `error.details`; the bar
 * dropped the details and printed "another finalize attempt owns this target branch's
 * workspace", so nobody could tell that reviewing/aborting #206 would unblock v0.2.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import { isOpenableBranchMergeBlocker, mergeTargetBlockerOf } from '@shared/mergeTargetBlocker'

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

const DOC_ID = 'flowgate.v02.0033.0007-AC'
const GROUP_ID = 'flowgate.v02.0033'

const BRANCH_MERGE_DETAILS = {
  target_branch: 'v0.2',
  blocking_group_id: null,
  merge_id: 206,
  started_at: '2026-10-06T22:04:06+09:00',
  blocking_owner_type: 'branch_merge',
  source_branch: 'main',
  project_id: 'flowgate',
  touched_at: '2026-10-06T22:15:39+09:00',
  attempt_state: 'conflict',
  review_state: 'resolved_pending_review',
  blocking_state: 'resolved_pending_review',
  route: '/api/v1/projects/flowgate/git/merge/206',
}

function busyError(details: Record<string, unknown>, message = 'branch merge #206 (main → v0.2) owns this target branch\'s workspace') {
  const error = { code: 'merge_target_busy', message, details }
  return Object.assign(new Error('Request failed with status code 409'), {
    isAxiosError: true,
    response: {
      status: 409,
      data: { ok: false, error, git: { ok: false, error }, approval: { approved: false, stage: 'precheck' } },
    },
  })
}

const hostStub = {
  name: 'GitBranchMergeConflictHost',
  props: ['projectId', 'mergeId'],
  emits: ['close', 'changed'],
  template: '<div data-test="host-stub" />',
}

function mountAc() {
  return mount(ReviewActionBar, {
    props: {
      docId: DOC_ID,
      projectId: 'flowgate',
      groupId: GROUP_ID,
      docRef: DOC_ID,
      docType: 'AC',
      reviewStatus: 'pending_review',
      mode: 'review' as const,
    },
    global: { plugins: [i18n], stubs: { GitBranchMergeConflictHost: hostStub } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockImplementation(async (url: string) => {
    if (url.includes('/documents/detail')) return { data: { doc_id: DOC_ID, doc_review_status: 'pending_review' } }
    if (url.includes('/git/finalize')) {
      return { data: { ok: true, state: { branch: 'flowgate_v02_0033', status: 'awaiting_choice', default_action: 'merge',
        choices: ['merge', 'merge_only'], aux_choices: [], action_axes: null, approval_in_flight: false } } }
    }
    return { data: {} }
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ReviewActionBar — merge_target_busy names its blocker (0683 T0004 §3)', () => {
  it('9. a branch merge blocker is shown as a branch merge with id, source → target, state and start', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockRejectedValueOnce(busyError(BRANCH_MERGE_DETAILS))
    await (wrapper.vm as any).doApprove()
    await flushPromises()

    const text = wrapper.get('[data-testid="ab-target-blocker-text"]').text()
    expect(text).toContain('#206')
    expect(text).toContain('main → v0.2')
    expect(text).toContain(i18n.global.t('main.git_branch_manager.attempt_state.resolved_pending_review'))
    expect(text).toContain('2026-10-06 22:04')
    expect(text).not.toContain('another finalize attempt')
    const toast = String(showToast.mock.calls.at(-1)?.[0])
    expect(toast).toContain('#206')
    expect(toast).not.toContain('another finalize attempt')
    // a precheck refusal never re-reads the document as if a Git step might have run
    expect(getRequest.mock.calls.some(([url]) => String(url).includes('/documents/detail'))).toBe(false)
    wrapper.unmount()
  })

  it('10. the blocking branch merge opens right here, on its own project route', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockRejectedValueOnce(busyError(BRANCH_MERGE_DETAILS))
    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    await wrapper.get('[data-testid="ab-target-blocker-open"]').trigger('click')
    const host = wrapper.findComponent({ name: 'GitBranchMergeConflictHost' })
    expect(host.exists()).toBe(true)
    expect(host.props('mergeId')).toBe(206)
    expect(host.props('projectId')).toBe('flowgate')

    // approving/aborting it there changes what holds the target: the stale line goes away
    host.vm.$emit('changed')
    host.vm.$emit('close')
    await flushPromises()
    expect(wrapper.find('[data-testid="ab-target-blocker"]').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    wrapper.unmount()
  })

  it('a group finalize blocker is named as the group finalize and offers no branch-merge link', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockRejectedValueOnce(busyError({
      target_branch: 'v0.2', blocking_group_id: 'flowgate.v02.0031', merge_id: 199,
      started_at: '2026-10-06T21:00:00+09:00', blocking_owner_type: 'group',
      source_branch: 'flowgate_v02_0031', blocking_state: 'conflict', project_id: 'flowgate',
      route: '/api/v1/groups/flowgate.v02.0031/git/merge/199',
    }, 'the finalize attempt #199 of group flowgate.v02.0031 owns this target branch\'s workspace'))
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    const text = wrapper.get('[data-testid="ab-target-blocker-text"]').text()
    expect(text).toContain('flowgate.v02.0031')
    expect(text).toContain('#199')
    expect(wrapper.find('[data-testid="ab-target-blocker-open"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('opening a mismatch keeps its reason but offers no review button', async () => {
    const wrapper = mountAc()
    await flushPromises()
    const error = { code: 'merge_target_owner_mismatch', message: 'owner mismatch',
      details: { target_branch: 'v0.2', merge_id: 7, reason: 'owner_marker_mismatch' } }
    postRequest.mockRejectedValueOnce(Object.assign(new Error('Request failed with status code 409'), {
      isAxiosError: true, response: { status: 409, data: { ok: false, error } },
    }))
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    expect(wrapper.get('[data-testid="ab-target-blocker-summary"]').text()).toBe(
      i18n.global.t('main.review_action_bar.target_owner_mismatch_summary', { target: 'v0.2' }),
    )
    expect(wrapper.get('[data-testid="ab-target-blocker-text"]').text()).toContain('owner_marker_mismatch')
    expect(wrapper.find('[data-testid="ab-target-blocker-open"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('the next approve starts without the old blocker line', async () => {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockRejectedValueOnce(busyError(BRANCH_MERGE_DETAILS))
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    expect(wrapper.find('[data-testid="ab-target-blocker"]').exists()).toBe(true)
    postRequest.mockResolvedValueOnce({
      data: { ok: true, document: { doc_review_status: 'approved' }, approval: { approved: true } },
    })
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    expect(wrapper.find('[data-testid="ab-target-blocker"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

// flowgate.default.0685 T0006 §1-2 — the blocker is told in its own alert, never between
// [승인] and [반려]; it leads with a short sentence and a button named by what it opens.
describe('ReviewActionBar — blocker alert outside the action buttons (0685 T0006)', () => {
  async function blocked() {
    const wrapper = mountAc()
    await flushPromises()
    postRequest.mockRejectedValueOnce(busyError(BRANCH_MERGE_DETAILS))
    await (wrapper.vm as any).doApprove()
    await flushPromises()
    return wrapper
  }

  it('does not sit between [승인] and [반려]: the action row keeps both buttons side by side', async () => {
    const wrapper = await blocked()
    const alert = wrapper.get('[data-testid="ab-target-blocker"]')
    expect(alert.attributes('role')).toBe('alert')
    // outside .sfb-inner, so neither the action row nor its narrow-screen overflow-x
    // container ever holds it
    expect(wrapper.find('.sfb-inner [data-testid="ab-target-blocker"]').exists()).toBe(false)
    expect(alert.element.parentElement?.classList.contains('sticky-footer-bar')).toBe(true)

    const actions = wrapper.get('.sfb-actions')
    const approve = actions.get('button.btn-success')
    const reject = actions.get('button.btn-danger')
    let between = approve.element.nextElementSibling
    const crossed: Element[] = []
    while (between && between !== reject.element) {
      crossed.push(between)
      between = between.nextElementSibling
    }
    expect(between).toBe(reject.element)
    expect(crossed.some((el) => el.querySelector('[data-testid^="ab-target-blocker"]'))).toBe(false)
    wrapper.unmount()
  })

  it('leads with a short sentence, then source → target · merge id · state, and keeps the long line under details', async () => {
    const wrapper = await blocked()
    const state = i18n.global.t('main.git_branch_manager.attempt_state.resolved_pending_review')
    expect(wrapper.get('[data-testid="ab-target-blocker-summary"]').text()).toBe(
      i18n.global.t('main.review_action_bar.target_busy_summary', { target: 'v0.2' }),
    )
    const meta = wrapper.get('[data-testid="ab-target-blocker-meta"]').text()
    expect(meta).toContain('main → v0.2')
    expect(meta).toContain('#206')
    expect(meta).toContain(state)
    expect(meta).not.toContain('2026-10-06')
    // start time and the full owner line are preserved, one toggle away
    const details = wrapper.get('details.ab-target-blocker-details')
    expect(details.find('[data-testid="ab-target-blocker-text"]').text()).toContain('2026-10-06 22:04')
    expect(wrapper.get('[data-testid="ab-target-blocker-hint"]').text()).toBe(
      i18n.global.t('main.review_action_bar.target_busy_hint_branch_merge'),
    )
    wrapper.unmount()
  })

  it('names the review button by the merge it opens and never as continuing the approval', async () => {
    const wrapper = await blocked()
    const button = wrapper.get('[data-testid="ab-target-blocker-open"]')
    expect(button.text()).toBe('병합 #206 검토')
    expect(button.text()).not.toContain('막고 있는')
    expect(button.text()).not.toContain('계속')
    expect(button.attributes('title')).toContain('main → v0.2')
    expect(i18n.global.t('main.review_action_bar.target_busy_open_review', { merge_id: 206 }, { locale: 'en' })).toBe('Review merge #206')
    expect(i18n.global.t('main.review_action_bar.target_busy_open_review', { merge_id: 206 }, { locale: 'ja' })).toContain('#206')

    // opening it only opens the review: no approval request is sent
    postRequest.mockClear()
    await button.trigger('click')
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()

    // once the merge moved, the stale alert goes and the user is told to approve again
    wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).vm.$emit('changed')
    await flushPromises()
    expect(wrapper.find('[data-testid="ab-target-blocker"]').exists()).toBe(false)
    expect(showToast).toHaveBeenLastCalledWith(i18n.global.t('main.review_action_bar.target_blocker_cleared'), 'info')
    wrapper.unmount()
  })

  it('floats above the 60px bar and wraps long text instead of widening the action row', () => {
    const source = readFileSync(resolve(__dirname, '../../src/main/components/ReviewActionBar.vue'), 'utf8')
    const rule = source.match(/\n\.ab-target-blocker \{([^}]*)\}/)?.[1] ?? ''
    expect(rule).toContain('position: absolute')
    expect(rule).toContain('bottom: calc(100% + 8px)')
    expect(rule).toContain('overflow-wrap: anywhere')
    expect(rule).toContain('overflow-x: hidden')
    expect(rule).not.toContain('nowrap')
  })
})

describe('mergeTargetBlockerOf', () => {
  it('reads the axios error, the body and the ride-along git block alike', () => {
    const err = busyError(BRANCH_MERGE_DETAILS)
    const fromError = mergeTargetBlockerOf(err)
    expect(fromError).toMatchObject({
      code: 'merge_target_busy', mergeId: 206, ownerType: 'branch_merge', sourceBranch: 'main',
      targetBranch: 'v0.2', state: 'resolved_pending_review', projectId: 'flowgate',
    })
    expect(mergeTargetBlockerOf(err.response.data)).toEqual(fromError)
    expect(mergeTargetBlockerOf(err.response.data.git)).toEqual(fromError)
    expect(isOpenableBranchMergeBlocker(fromError)).toBe(true)
  })

  it('ignores every other failure and never opens a mismatch', () => {
    expect(mergeTargetBlockerOf({ response: { status: 409, data: { error: { code: 'git_busy' } } } })).toBeNull()
    expect(mergeTargetBlockerOf(new Error('boom'))).toBeNull()
    const mismatch = mergeTargetBlockerOf({ error: {
      code: 'merge_target_owner_mismatch', details: { merge_id: 7, reason: 'owner_marker_mismatch' },
    } })
    expect(mismatch?.code).toBe('merge_target_owner_mismatch')
    expect(isOpenableBranchMergeBlocker(mismatch)).toBe(false)
  })
})
