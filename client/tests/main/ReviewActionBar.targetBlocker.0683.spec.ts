/**
 * flowgate.default.0683 T0004 §3 — an approval refused because another attempt holds the
 * merge target says WHICH attempt, as what it really is, and leads to it.
 *
 * The incident (flowgate.v02.0033.0007-AC): the server knew the blocker was branch merge
 * #206 (main → v0.2, resolved_pending_review) and sent it in `error.details`; the bar
 * dropped the details and printed "another finalize attempt owns this target branch's
 * workspace", so nobody could tell that reviewing/aborting #206 would unblock v0.2.
 */
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
